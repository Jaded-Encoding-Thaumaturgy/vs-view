from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from concurrent.futures import Future
from time import perf_counter_ns
from typing import Any, NamedTuple
from unittest.mock import MagicMock

import pytest
import vapoursynth as vs
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment

from tests.conftest import FakeAudioSink
from tests.harness import HarnessPluginController
from vsview.app.outputs import AudioBuffer, FrameBuffer
from vsview.app.outputs.audio import AudioOutput
from vsview.app.outputs.video import VideoMetadata, VideoOutput
from vsview.app.packing import Packer
from vsview.app.plugins.manager import PluginManager
from vsview.app.views.timeline import TimelineControlBar
from vsview.app.workspace.playback import PlaybackManager, PlaybackState
from vsview.types import Frame
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("unique-core")]


# PlaybackState Tests
def test_playback_state_reset(
    qapp: QApplication,
    qt_event_loop: QtEventLoop,
    vpy_env: ManagedEnvironment,
    create_test_video_output: Callable[[int], VideoOutput],
    create_test_audio_output: Callable[[int], AudioOutput],
) -> None:
    state = PlaybackState(qapp)

    # Mutate all fields from initial defaults
    state.last_fps_update_ns = 1_000_000
    state.fps_history.extend([100, 200, 300])
    state.frame_interval_ns = 41_666_666
    state.next_frame_time_ns = 99_999_999
    state.audio_frame_interval_ns = 21_333_333
    state.next_audio_frame_time_ns = 88_888_888

    state.video_timer.start(100)
    state.audio_timer.start(100)
    assert state.video_timer.isActive()
    assert state.audio_timer.isActive()

    voutput = create_test_video_output(100)
    video_buffer = FrameBuffer(voutput, vpy_env)
    state.buffer = video_buffer

    aoutput = create_test_audio_output(1000)
    audio_buffer = AudioBuffer(aoutput, vpy_env)
    state.audio_buffer = audio_buffer

    state.reset()

    assert state.last_fps_update_ns == 0
    assert len(state.fps_history) == 0
    assert state.frame_interval_ns == 0
    assert state.next_frame_time_ns == 0
    assert state.audio_frame_interval_ns == 0
    assert state.next_audio_frame_time_ns == 0

    assert not state.video_timer.isActive()
    assert not state.audio_timer.isActive()

    assert state.buffer is None
    assert state.audio_buffer is None

    # Observable cleanup execution via public contract
    state.wait_for_cleanup()


def test_playback_state_reset_audio(
    qapp: QApplication,
    qt_event_loop: QtEventLoop,
    vpy_env: ManagedEnvironment,
    create_test_audio_output: Callable[[int], AudioOutput],
) -> None:
    state = PlaybackState(qapp)

    state.next_audio_frame_time_ns = 77_777_777
    state.audio_frame_interval_ns = 21_333_333
    state.audio_timer.start(100)
    assert state.audio_timer.isActive()

    aoutput = create_test_audio_output(1000)
    audio_buffer = AudioBuffer(aoutput, vpy_env)
    state.audio_buffer = audio_buffer

    state.reset_audio()

    assert state.next_audio_frame_time_ns == 0
    assert state.audio_frame_interval_ns == 0
    assert not state.audio_timer.isActive()

    assert state.audio_buffer is None
    state.wait_for_cleanup()


def test_playback_state_wait_for_cleanup(
    qapp: QApplication,
    qt_event_loop: QtEventLoop,
    vpy_env: ManagedEnvironment,
    create_test_video_output: Callable[[int], VideoOutput],
    mocker: MockerFixture,
) -> None:
    state = PlaybackState(qapp)

    # 1. No pending futures - completes immediately
    state.wait_for_cleanup()
    assert state.buffer is None
    assert state.audio_buffer is None

    # 2. Reset real buffer and wait for cleanup without stalling
    voutput = create_test_video_output(10)
    state.buffer = FrameBuffer(voutput, vpy_env)
    state.reset()
    assert state.buffer is None

    stall_called = False

    def stall_cb() -> None:
        nonlocal stall_called
        stall_called = True

    state.wait_for_cleanup(timeout=0.5, stall_cb=stall_cb)
    assert stall_called is False

    # 3. Slow/undone future triggers stall_cb on timeout
    undone_fut = Future[None]()
    stall_triggered = False

    def stall_handler() -> None:
        nonlocal stall_triggered
        stall_triggered = True
        undone_fut.set_result(None)

    slow_buffer = FrameBuffer(voutput, vpy_env)
    mocker.patch.object(FrameBuffer, "invalidate", return_value=undone_fut)
    state.buffer = slow_buffer
    state.reset()

    state.wait_for_cleanup(timeout=0.01, stall_cb=stall_handler)
    assert stall_triggered is True


# PlaybackManager Fixtures & Helpers
class PlaybackManagerSetup(NamedTuple):
    playback_manager: PlaybackManager
    env: ManagedEnvironment
    plugin_controller: HarnessPluginController
    mock_outputs: MagicMock
    mock_tabs: MagicMock
    tbar: TimelineControlBar


@pytest.fixture
def playback_manager_setup(
    qt_event_loop: QtEventLoop,
    vpy_env: ManagedEnvironment,
    plugin_controller: HarnessPluginController,
    qtbot: QtBot,
) -> Generator[PlaybackManagerSetup, None, None]:

    mock_outputs = MagicMock()
    mock_outputs.current_voutput = None
    mock_outputs.current_aoutput = None
    mock_outputs.aoutputs = []

    mock_tabs = MagicMock()
    mock_tabs.tabs.currentIndex.return_value = 0

    tbar = TimelineControlBar()
    qtbot.addWidget(tbar)

    manager = PlaybackManager(
        loop=qt_event_loop,
        get_env=lambda: vpy_env,
        controller=plugin_controller,  # pyright: ignore[reportArgumentType]
        outputs_manager=mock_outputs,
        tab_manager=mock_tabs,
        tbar=tbar,
    )

    try:
        yield PlaybackManagerSetup(manager, vpy_env, plugin_controller, mock_outputs, mock_tabs, tbar)
    finally:
        manager.stop()


# PlaybackManager Tests
def test_playback_manager_seek_bounds(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_video_output: Callable[[int], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, _, _, mock_outputs, _, _ = playback_manager_setup
    voutput = create_test_video_output(100)
    mock_outputs.current_voutput = voutput

    try:
        # Initial frame at 0
        assert manager.state.current_frame == 0

        # Seek backward from frame 0 -> clamps at 0
        manager.seek_frame(-10)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)
        assert manager.state.current_frame == 0

        # Seek forward past frame 99 -> clamps at 99
        manager.seek_frame(200)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)
        assert manager.state.current_frame == 99

        # Seek relative step backward from 99
        # Default seek_step is 1, so seek_n_frames(-5) seeks by -5 frames
        manager.seek_n_frames(-5)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)
        assert manager.state.current_frame == 94

        # Seek relative step forward
        manager.seek_n_frames(5)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)
        assert manager.state.current_frame == 99

        # Seek to frame 50 then step forward and backward
        manager.seek_to_frame_coalesced(Frame(50))
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)
        assert manager.state.current_frame == 50

        manager.seek_n_frames(5)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)
        assert manager.state.current_frame == 55

        manager.seek_n_frames(-5)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)
        assert manager.state.current_frame == 50

        # Stepping backward past frame 0 clamps at 0
        manager.seek_to_frame_coalesced(Frame(3))
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)
        manager.seek_n_frames(-10)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)
        assert manager.state.current_frame == 0
    finally:
        voutput.clear()
        mock_outputs.current_voutput = None


@pytest.mark.parametrize(
    ("start", "points", "forward", "expected"),
    [
        pytest.param(15, [40, 10, 20, 20], True, 20, id="next"),
        pytest.param(15, [40, 10, 20, 20], False, 10, id="previous"),
        pytest.param(20, [10, 20, 40], True, 40, id="next-skips-the-current-frame"),
        pytest.param(20, [10, 20, 40], False, 10, id="previous-skips-the-current-frame"),
        pytest.param(40, [10, 40, 100, 150], True, 40, id="next-skips-points-past-the-clip"),
        pytest.param(10, [-5, 10, 20], False, 10, id="previous-skips-negative-points"),
        pytest.param(15, [], True, 15, id="no-points"),
    ],
)
def test_playback_manager_seek_to_neighbor(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_video_output: Callable[[int], VideoOutput],
    qtbot: QtBot,
    start: int,
    points: list[int],
    forward: bool,
    expected: int,
) -> None:
    manager, _, _, mock_outputs, _, _ = playback_manager_setup
    voutput = create_test_video_output(100)
    mock_outputs.current_voutput = voutput

    try:
        manager.seek_to_frame_coalesced(Frame(start))
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)

        manager.seek_to_neighbor(points, forward=forward)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)

        assert manager.state.current_frame == expected
    finally:
        voutput.clear()
        mock_outputs.current_voutput = None


def test_playback_manager_volume_and_mute(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_audio_output: Callable[[int], AudioOutput],
    mocker: MockerFixture,
) -> None:
    manager, env, _, mock_outputs, _, tbar = playback_manager_setup

    mock_aoutput = MagicMock()
    mock_outputs.current_aoutput = mock_aoutput

    # Triggered via TimelineControlBar volumeChanged signal
    tbar.playback_container.volumeChanged.emit(0.85)
    assert mock_aoutput.volume == 0.85

    tbar.playback_container.volumeChanged.emit(0.4)
    assert mock_aoutput.volume == 0.4

    # Guard check when current_aoutput is None
    mock_outputs.current_aoutput = None
    tbar.playback_container.volumeChanged.emit(0.5)

    # Mute changed to True: stops audio sink and invalidates audio buffer
    mock_outputs.current_aoutput = mock_aoutput
    mock_outputs.aoutputs = [mock_aoutput]
    aoutput = create_test_audio_output(10000)
    real_audio_buffer = AudioBuffer(aoutput, env)
    spy_invalidate = mocker.spy(AudioBuffer, "invalidate")
    manager.state.audio_buffer = real_audio_buffer
    manager.state.audio_timer.start(100)

    tbar.playback_container.muteChanged.emit(True)

    mock_aoutput.sink.reset.assert_called_once()
    assert not manager.state.audio_timer.isActive()
    spy_invalidate.assert_called_once()
    assert manager.state.audio_buffer is None

    # Mute changed to False while playing: triggers restart
    restart_mock = mocker.patch.object(manager, "_restart_playback")
    manager.state.is_playing = True
    tbar.playback_container.muteChanged.emit(False)
    restart_mock.assert_called_once()

    # Mute changed to False while stopped: does not restart
    restart_mock.reset_mock()
    manager.state.is_playing = False
    tbar.playback_container.muteChanged.emit(False)
    restart_mock.assert_not_called()

    mock_outputs.current_aoutput = aoutput
    mock_outputs.aoutputs = [aoutput]
    assert aoutput.sink is None
    manager.state.audio_timer.start(100)
    tbar.playback_container.muteChanged.emit(True)
    assert not manager.state.audio_timer.isActive()


def test_playback_manager_muted_playback_lifecycle(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_audio_output: Callable[[int], AudioOutput],
    create_test_video_output: Callable[[int], VideoOutput],
) -> None:
    manager, _, _, mock_outputs, _, tbar = playback_manager_setup
    voutput = create_test_video_output(100)
    aoutput = create_test_audio_output(100)
    mock_outputs.current_voutput = voutput
    mock_outputs.current_aoutput = aoutput

    assert aoutput.sink is None

    # Start playback while muted
    tbar.playback_container.is_muted = True
    manager.toggle_playback()
    assert manager.state.is_playing is True
    assert manager.state.audio_buffer is None
    assert aoutput.sink is None

    # Stop playback while muted
    manager.toggle_playback()
    assert manager.state.is_playing is False


def test_playback_manager_seek_without_voutput(playback_manager_setup: PlaybackManagerSetup) -> None:
    manager, _, _, mock_outputs, _, _ = playback_manager_setup
    mock_outputs.current_voutput = None

    # Must safely ignore seek without raising
    manager.seek_frame(10)
    assert manager.state.current_frame == 0
    assert not manager.has_pending_frame


def test_playback_manager_audio_output_changed(
    playback_manager_setup: PlaybackManagerSetup, mocker: MockerFixture
) -> None:
    manager, _, _, _, _, tbar = playback_manager_setup
    audio_index_received = list[int]()
    manager.audioOutputChanged.connect(audio_index_received.append)

    restart_mock = mocker.patch.object(manager, "_restart_playback")

    # When not playing: emits signal, does not restart
    manager.state.is_playing = False
    tbar.playback_container.audio_output_combo.currentIndexChanged.emit(2)
    assert audio_index_received == [2]
    restart_mock.assert_not_called()

    # When playing: emits signal and restarts playback
    manager.state.is_playing = True
    tbar.playback_container.audio_output_combo.currentIndexChanged.emit(3)
    assert audio_index_received == [2, 3]
    restart_mock.assert_called_once()


def test_playback_manager_playback_settings_changed(
    playback_manager_setup: PlaybackManagerSetup, mocker: MockerFixture
) -> None:
    manager, _, _, _, _, tbar = playback_manager_setup
    restart_mock = mocker.patch.object(manager, "_restart_playback")

    # Not playing: updates settings, no restart
    manager.state.is_playing = False
    tbar.playback_container.settingsChanged.emit(10, 2.0, True)
    assert tbar.playback_container.settings.seek_step == 10
    assert tbar.playback_container.settings.speed == 2.0
    assert tbar.playback_container.settings.uncapped is True
    restart_mock.assert_not_called()

    # Playing: updates settings and restarts playback
    manager.state.is_playing = True
    tbar.playback_container.settingsChanged.emit(5, 1.0, False)
    assert tbar.playback_container.settings.seek_step == 5
    assert tbar.playback_container.settings.speed == 1.0
    assert tbar.playback_container.settings.uncapped is False
    restart_mock.assert_called_once()


def test_playback_manager_api_busy_frame_request(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_video_output: Callable[[int], VideoOutput],
) -> None:
    manager, _, plugin_controller, mock_outputs, _, _ = playback_manager_setup
    voutput = create_test_video_output(10)
    mock_outputs.current_voutput = voutput

    try:
        with plugin_controller.create_blocker(plugin_controller.api):
            assert plugin_controller.api.busy is True
            callback_results = list[int]()

            def on_rendered(fut: Future[int]) -> None:
                callback_results.append(fut.result())

            manager.request_frame(5, cb_render=on_rendered)
            assert callback_results == [2]
    finally:
        voutput.clear()
        mock_outputs.current_voutput = None


def test_playback_manager_reports_the_shown_frame_when_a_busy_plugin_refuses_a_seek(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_video_output: Callable[[int], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, _, plugin_controller, mock_outputs, _, tbar = playback_manager_setup
    voutput = create_test_video_output(100)
    mock_outputs.current_voutput = voutput
    manager.state.current_frame = 7

    try:
        with (
            plugin_controller.create_blocker(plugin_controller.api),
            qtbot.waitSignal(manager.seekRefused, timeout=1000) as blocker,
        ):
            tbar.playback_container.frame_edit.setValue(Frame(40))

        assert blocker.args == [7]
        assert manager.state.current_frame == 7
        assert not manager.is_rendering
    finally:
        voutput.clear()
        mock_outputs.current_voutput = None


def test_playback_manager_does_not_refuse_a_seek_queued_behind_a_render(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_video_output: Callable[[int], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, _, _, mock_outputs, _, tbar = playback_manager_setup
    voutput = create_test_video_output(100)
    mock_outputs.current_voutput = voutput
    refused = list[int]()
    manager.seekRefused.connect(refused.append)

    try:
        tbar.playback_container.frame_edit.setValue(Frame(10))
        tbar.playback_container.frame_edit.setValue(Frame(20))
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager.is_rendering, timeout=2000)

        assert manager.state.current_frame == 20
        assert refused == []
    finally:
        voutput.clear()
        mock_outputs.current_voutput = None


@pytest.mark.parametrize(
    ("sar_num", "sar_den", "expected"),
    [(0, 1, 1.0), (16, 0, 1.0), (-4, 3, 1.0), (4, -3, 1.0), (16, 11, 16 / 11)],
    ids=["zero_num", "zero_den", "negative_num", "negative_den", "valid"],
)
def test_playback_manager_treats_a_non_positive_sar_as_square_pixels(
    playback_manager_setup: PlaybackManagerSetup,
    plugin_controller: HarnessPluginController,
    init_plugin_manager: PluginManager,
    qtbot: QtBot,
    sar_num: int,
    sar_den: int,
    expected: float,
) -> None:
    manager, _, _, mock_outputs, _, _ = playback_manager_setup

    clip = vs.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10, fpsnum=24, fpsden=1)
    clip = vs.core.std.SetFrameProps(
        clip,
        _Matrix=vs.MATRIX_RGB,
        _Primaries=vs.PRIMARIES_BT709,
        _Transfer=vs.TRANSFER_BT709,
        _SARNum=sar_num,
        _SARDen=sar_den,
    )
    voutput = VideoOutput(vs.VideoOutputTuple(clip, None, 0), 0, Packer())
    voutput.prepare_video(plugin_controller.api)
    mock_outputs.current_voutput = voutput

    try:
        with qtbot.waitSignal(manager.frameRendered, timeout=2000) as blocker:
            manager.request_frame(0)
        qtbot.waitUntil(lambda: manager.is_idle, timeout=2000)

        assert blocker.args
        assert blocker.args[1] == expected
    finally:
        voutput.clear()
        mock_outputs.current_voutput = None


def test_playback_manager_buffered_audio_backpressure(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_audio_output: Callable[[int], AudioOutput],
    mocker: MockerFixture,
    qtbot: QtBot,
) -> None:
    manager, env, _, mock_outputs, _, _ = playback_manager_setup
    aoutput = create_test_audio_output(100)
    mock_outputs.current_aoutput = aoutput

    # Mock audio sink with backpressure (free bytes strictly less than bytes_per_frame)
    mock_sink = mocker.MagicMock()
    mock_sink.bytesFree.return_value = aoutput.bytes_per_frame - 1
    aoutput.sink = mock_sink

    audio_buffer = AudioBuffer(aoutput, env)
    manager.state.audio_buffer = audio_buffer
    manager.state.is_playing = True
    manager.state.audio_frame_interval_ns = 20_000_000

    target_next_time_ns = perf_counter_ns() + 100_000_000
    manager.state.next_audio_frame_time_ns = target_next_time_ns

    try:
        manager.state.audio_timer.timeout.emit()
        qtbot.waitUntil(lambda: manager.state.audio_timer.isActive(), timeout=2000)

        # Timer was rescheduled without advancing target audio frame time
        assert manager.state.audio_timer.isActive() is True
        assert manager.state.next_audio_frame_time_ns == target_next_time_ns
        mock_sink.bytesFree.assert_called()
    finally:
        manager.state.reset_audio()
        aoutput.clear()
        mock_outputs.current_aoutput = None


def test_playback_manager_buffered_audio_buffer_exhaustion(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_audio_output: Callable[[int], AudioOutput],
    mocker: MockerFixture,
    qtbot: QtBot,
) -> None:
    manager, env, _, mock_outputs, _, _ = playback_manager_setup
    aoutput = create_test_audio_output(100)
    mock_outputs.current_aoutput = aoutput

    # Mock audio sink with sufficient space
    mock_sink = mocker.MagicMock()
    mock_sink.bytesFree.return_value = aoutput.bytes_per_frame + 1024
    aoutput.sink = mock_sink

    # Real AudioBuffer with no buffered frames (get_next_frame returns None)
    audio_buffer = AudioBuffer(aoutput, env)
    assert audio_buffer.get_next_frame() is None

    manager.state.audio_buffer = audio_buffer
    manager.state.is_playing = True
    manager.state.audio_frame_interval_ns = 20_000_000

    target_next_time_ns = 500_000_000
    manager.state.next_audio_frame_time_ns = target_next_time_ns

    # Start audio timer initially so stopping it is an observable change
    manager.state.audio_timer.start(100)
    assert manager.state.audio_timer.isActive() is True

    try:
        manager.state.audio_timer.timeout.emit()
        qtbot.waitUntil(lambda: not manager.state.audio_timer.isActive(), timeout=2000)

        # Timer stopped on buffer exhaustion without advancing target audio frame time
        assert manager.state.audio_timer.isActive() is False
        assert manager.state.next_audio_frame_time_ns == target_next_time_ns
    finally:
        manager.state.reset_audio()
        aoutput.clear()
        mock_outputs.current_aoutput = None


def test_playback_manager_rapid_stop_cancels_startup(playback_manager_setup: PlaybackManagerSetup) -> None:
    manager, *_ = playback_manager_setup

    manager.toggle_playback()
    assert manager.state.is_playing is True

    manager.stop()
    assert manager.state.is_playing is False

    assert manager.state.is_playing is False
    assert manager.state.video_timer.isActive() is False
    assert manager.state.audio_timer.isActive() is False


def test_audio_delay_change_applies_to_every_output_when_one_fails(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_audio_output: Callable[[int], AudioOutput],
    init_plugin_manager: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, mock_outputs, _, tbar = playback_manager_setup
    failing, healthy = create_test_audio_output(100), create_test_audio_output(100)
    failing.vs_name, healthy.vs_name = "Failing audio", "Healthy audio"
    mock_outputs.aoutputs = [failing, healthy]
    orig_prepare = AudioOutput.prepare_audio
    prepared = list[tuple[str, float]]()

    def prepare(self: AudioOutput, delay_s: float, api: Any) -> None:
        if self is failing:
            raise RuntimeError("audio processor failed")
        orig_prepare(self, delay_s, api)
        prepared.append((self.vs_name, delay_s))

    monkeypatch.setattr(AudioOutput, "prepare_audio", prepare)

    tbar.playback_container.audioDelayChanged.emit(0.25)
    assert prepared == [("Healthy audio", 0.25)]


def test_playback_starts_without_audio_past_the_end_of_a_shorter_audio_track(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_video_output: Callable[[int], VideoOutput],
    init_plugin_manager: PluginManager,
    fake_audio_sinks: list[FakeAudioSink],
    qtbot: QtBot,
) -> None:
    manager, _, plugin_controller, mock_outputs, _, tbar = playback_manager_setup
    voutput = create_test_video_output(100)

    aoutput = AudioOutput(vs.core.std.BlankAudio(length=48000, samplerate=48000), 0)
    aoutput.prepare_audio(0.0, plugin_controller.api)

    mock_outputs.current_voutput = voutput
    mock_outputs.current_aoutput = aoutput
    mock_outputs.aoutputs = [aoutput]
    # Frame 50 is at 2.08 s, the audio ends at 1 s.
    manager.state.current_frame = 50

    try:
        with qtbot.waitSignal(
            manager.statusLoadingStarted, check_params_cb=lambda message: message == "Playing...", timeout=5000
        ):
            manager.toggle_playback()

        assert manager.state.is_playing is True
        assert tbar.playback_container.play_pause_btn.isChecked() is True
        assert manager.state.audio_buffer is None
    finally:
        manager.stop()
        qtbot.waitUntil(lambda: manager.is_idle, timeout=5000)
        manager.state.wait_for_cleanup()
        voutput.clear()
        aoutput.clear()


def test_stopping_playback_resets_the_sink_of_every_audio_track(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_audio_output: Callable[[int], AudioOutput],
) -> None:
    manager, _, _, mock_outputs, _, _ = playback_manager_setup
    aoutputs = [create_test_audio_output(10000) for _ in range(2)]
    sinks = [FakeAudioSink(None) for _ in aoutputs]
    for aoutput, sink in zip(aoutputs, sinks, strict=True):
        sink.setup(0, 1.0)
        aoutput.sink = sink  # pyright: ignore[reportAttributeAccessIssue]
    mock_outputs.aoutputs = aoutputs
    # After a track switch during playback, the current output is already the new track,
    # while the sink of the previous track is still started.
    mock_outputs.current_aoutput = aoutputs[1]
    manager.state.is_playing = True

    try:
        manager.stop()

        assert [sink.ready for sink in sinks] == [False, False]
        assert [sink.calls for sink in sinks] == [[("setup", "MainThread"), ("reset", "MainThread")]] * 2
    finally:
        for aoutput in aoutputs:
            aoutput.sink = None
            aoutput.clear()


# Playback loop
@pytest.fixture
def use_voutput(
    playback_manager_setup: PlaybackManagerSetup, qtbot: QtBot
) -> Generator[Callable[[VideoOutput], VideoOutput]]:
    """Make a video output current for the playback manager, then stop and clean up after the test."""
    manager, _, _, mock_outputs, _, _ = playback_manager_setup
    used = list[VideoOutput]()

    def use(voutput: VideoOutput) -> VideoOutput:
        mock_outputs.current_voutput = voutput
        used.append(voutput)
        return voutput

    yield use

    manager.stop()
    qtbot.waitUntil(lambda: manager.is_idle, timeout=5000)
    manager.state.wait_for_cleanup()
    mock_outputs.current_voutput = None
    for voutput in used:
        voutput.clear()


def record_frames(manager: PlaybackManager) -> list[int]:
    frames = list[int]()
    manager.timelineCursorChanged.connect(frames.append)
    return frames


def wait_until_stopped(manager: PlaybackManager, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: not manager.state.is_playing and manager.is_idle, timeout=5000)


def make_clip(length: int = 10, fpsnum: int = 24, **props: Any) -> vs.VideoNode:
    clip = vs.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=length, fpsnum=fpsnum, fpsden=1)
    return vs.core.std.SetFrameProps(
        clip, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709, **props
    )


def failing_clip(failing_frame: int) -> vs.VideoNode:
    base = make_clip()

    def select(n: int) -> vs.VideoNode:
        if n == failing_frame:
            raise RuntimeError("broken frame")
        return base

    return base.std.FrameEval(select)


@pytest.mark.parametrize("uncapped", [False, True], ids=["capped", "uncapped"])
def test_playback_shows_each_frame_in_order_and_stops_on_the_last_one(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    create_test_video_output: Callable[[int], VideoOutput],
    qtbot: QtBot,
    uncapped: bool,
) -> None:
    manager, _, _, _, _, tbar = playback_manager_setup
    use_voutput(create_test_video_output(10))
    tbar.playback_container.settings.uncapped = uncapped
    frames = record_frames(manager)

    manager.toggle_playback()
    wait_until_stopped(manager, qtbot)

    assert frames == list(range(1, 10))
    assert manager.state.current_frame == 9
    assert tbar.playback_container.play_pause_btn.isChecked() is False


@pytest.mark.parametrize(
    ("start", "zone_frames", "step", "expected"),
    [
        (2, 4, 1, [3, 4, 5, 6]),
        (5, 10, -1, [4, 3, 2, 1, 0]),
        (0, 6, 2, [2, 4, 6]),
        (7, 10, 1, [8, 9]),
    ],
    ids=["forward", "backward_clamped_at_first_frame", "every_second_frame", "clamped_at_last_frame"],
)
def test_play_zone_plays_from_the_current_frame_and_stops_at_the_zone_end(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    create_test_video_output: Callable[[int], VideoOutput],
    qtbot: QtBot,
    start: int,
    zone_frames: int,
    step: int,
    expected: list[int],
) -> None:
    manager, _, _, _, _, tbar = playback_manager_setup
    use_voutput(create_test_video_output(10))
    manager.state.current_frame = start
    frames = record_frames(manager)

    tbar.playback_container.playZone.emit(zone_frames, False, step)
    wait_until_stopped(manager, qtbot)

    assert frames == expected


def test_play_zone_with_loop_starts_again_from_the_zone_start(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    create_test_video_output: Callable[[int], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, _, _, _, _, tbar = playback_manager_setup
    use_voutput(create_test_video_output(10))
    frames = record_frames(manager)

    tbar.playback_container.playZone.emit(2, True, 1)
    qtbot.waitUntil(lambda: len(frames) >= 7, timeout=5000)
    manager.stop()

    assert frames[:7] == [1, 2, 0, 1, 2, 0, 1]


@pytest.mark.parametrize(
    ("current", "play_range", "expected"),
    [
        (0, range(5, 9), [5, 6, 7, 8]),
        (5, range(5, 9), [6, 7, 8]),
        (0, range(8, 4, -1), [8, 7, 6, 5]),
        (0, range(2, 9, 3), [2, 5, 8]),
    ],
    ids=["shows_the_first_frame_first", "first_frame_already_shown", "backward", "with_a_step"],
)
def test_play_range_plays_each_frame_of_the_range_once(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    create_test_video_output: Callable[[int], VideoOutput],
    qtbot: QtBot,
    current: int,
    play_range: range,
    expected: list[int],
) -> None:
    manager, _, _, _, _, _ = playback_manager_setup
    use_voutput(create_test_video_output(10))
    manager.state.current_frame = current
    frames = record_frames(manager)

    manager.play_range(play_range)
    qtbot.waitUntil(lambda: frames[-1:] == expected[-1:], timeout=5000)
    wait_until_stopped(manager, qtbot)

    assert frames == expected


@pytest.mark.parametrize(
    ("framedurs", "props"),
    [((0.01,) * 6, {}), (None, {"_DurationNum": 1, "_DurationDen": 100})],
    ids=["frame_durations", "duration_props"],
)
def test_variable_frame_rate_playback_reaches_the_last_frame(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    init_plugin_manager: PluginManager,
    qtbot: QtBot,
    framedurs: tuple[float, ...] | None,
    props: dict[str, int],
) -> None:
    manager, _, plugin_controller, _, _, _ = playback_manager_setup

    clip = make_clip(6, fpsnum=0, **props)
    voutput = VideoOutput(vs.VideoOutputTuple(clip, None, 0), 0, Packer(), VideoMetadata("VFR", framedurs))
    voutput.prepare_video(plugin_controller.api)
    voutput.prepared_clip.get_frame(0).close()
    use_voutput(voutput)

    frames = record_frames(manager)

    manager.toggle_playback()
    wait_until_stopped(manager, qtbot)

    assert frames == [1, 2, 3, 4, 5]


def test_variable_frame_rate_playback_without_durations_does_not_start(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    init_plugin_manager: PluginManager,
    qtbot: QtBot,
) -> None:
    manager, _, plugin_controller, _, _, tbar = playback_manager_setup

    voutput = VideoOutput(vs.VideoOutputTuple(make_clip(6, fpsnum=0), None, 0), 0, Packer())
    voutput.prepare_video(plugin_controller.api)
    voutput.prepared_clip.get_frame(0).close()
    use_voutput(voutput)

    frames = record_frames(manager)

    manager.toggle_playback()
    wait_until_stopped(manager, qtbot)

    assert frames == []
    assert tbar.playback_container.play_pause_btn.isChecked() is False


def test_a_frame_that_fails_during_playback_reports_a_load_failure(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, _, _, _, _, _ = playback_manager_setup

    clip = failing_clip(failing_frame=3)
    voutput = VideoOutput(vs.VideoOutputTuple(clip, None, 0), 0, Packer())
    voutput.prepared_clip = voutput.packer.pack_clip(clip, None)
    use_voutput(voutput)

    frames = record_frames(manager)

    with qtbot.waitSignal(manager.loadFailed, timeout=5000):
        manager.toggle_playback()

    assert frames == [1, 2]


def test_seeking_to_a_frame_the_script_cannot_render_reports_a_load_failure(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, _, _, _, _, _ = playback_manager_setup
    clip = failing_clip(failing_frame=3)

    voutput = VideoOutput(vs.VideoOutputTuple(clip, None, 0), 0, Packer())
    voutput.prepared_clip = voutput.packer.pack_clip(clip, None)
    use_voutput(voutput)

    with qtbot.waitSignal(manager.loadFailed, timeout=5000):
        manager.request_frame(3)


def test_seeking_to_a_frame_that_fails_after_the_script_shows_a_failure_image(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, _, _, _, _, tbar = playback_manager_setup
    tbar.show()

    voutput = VideoOutput(vs.VideoOutputTuple(make_clip(), None, 0), 0, Packer())
    # The script frame renders, but a later step of the prepared clip fails.
    voutput.prepared_clip = voutput.packer.pack_clip(failing_clip(failing_frame=3), None)
    use_voutput(voutput)
    load_failures = list[None]()
    manager.loadFailed.connect(lambda: load_failures.append(None))

    with qtbot.waitSignal(manager.frameRendered, timeout=5000) as blocker:
        manager.request_frame(3)
    qtbot.waitUntil(lambda: manager.is_idle, timeout=5000)

    image: QImage = blocker.args[0]  # pyright: ignore[reportOptionalSubscript]
    assert (image.width(), image.height()) == (64, 64)
    assert image.pixelColor(0, 0) != QColor(0, 0, 0)
    warnings = [w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()]
    assert ["broken frame" in w.text() for w in warnings] == [True]
    assert load_failures == []
    for w in warnings:
        w.close()


def test_rapid_seeks_render_the_first_and_the_last_target_only(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    create_test_video_output: Callable[[int], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, _, _, _, _, _ = playback_manager_setup
    use_voutput(create_test_video_output(100))
    frames = record_frames(manager)

    for target in (10, 20, 30):
        manager.seek_to_frame_coalesced(Frame(target))
    qtbot.waitUntil(
        lambda: manager.state.current_frame == 30 and not manager.has_pending_frame and not manager.is_rendering,
        timeout=5000,
    )

    assert frames == [10, 30]


@pytest.mark.parametrize(
    ("notify_plugins", "expected"),
    [(True, ["painted", "displayed 7 at 7"]), (False, ["painted"])],
    ids=["notify", "silent"],
)
def test_plugins_hear_that_a_seeked_frame_is_displayed_after_it_is_painted(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    create_test_video_output: Callable[[int], VideoOutput],
    qtbot: QtBot,
    mocker: MockerFixture,
    notify_plugins: bool,
    expected: list[str],
) -> None:
    manager, _, plugin_controller, _, _, _ = playback_manager_setup
    use_voutput(create_test_video_output(20))
    order = list[str]()
    manager.frameRendered.connect(lambda *_: order.append("painted"))
    mocker.patch.object(
        plugin_controller,
        "on_frame_displayed",
        side_effect=lambda n: order.append(f"displayed {n} at {manager.state.current_frame}"),
    )

    manager.request_frame(7, notify_plugins=notify_plugins)
    qtbot.waitUntil(lambda: manager.is_idle, timeout=5000)

    assert order == expected


def gated_voutput(gate: threading.Event, length: int = 30) -> VideoOutput:
    """A video output whose frames are rendered only once `gate` is set."""
    base = make_clip(length=length)

    def select(n: int) -> vs.VideoNode:
        gate.wait(10)
        return base

    clip = base.std.FrameEval(select)
    voutput = VideoOutput(vs.VideoOutputTuple(clip, None, 0), 0, Packer())
    voutput.prepared_clip = voutput.packer.pack_clip(clip, None)
    return voutput


def test_a_render_replaced_by_a_tab_change_does_not_reach_the_view(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, _, _, _, _, _ = playback_manager_setup
    gate = threading.Event()
    use_voutput(gated_voutput(gate))
    frames = record_frames(manager)
    painted = list[QImage]()
    manager.frameRendered.connect(lambda image, *_: painted.append(image))
    completed = list[Future[int]]()

    manager.request_frame(5, cb_render=completed.append)
    manager.invalidate_renders()
    gate.set()
    qtbot.waitUntil(lambda: bool(completed) and manager.is_idle, timeout=5000)

    assert painted == []
    assert frames == []
    assert manager.state.current_frame == 0
    assert completed[0].result() == 0


def test_play_stop_play_while_the_first_start_is_buffering_starts_playback_once(
    playback_manager_setup: PlaybackManagerSetup,
    use_voutput: Callable[[VideoOutput], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, _, _, _, _, _ = playback_manager_setup
    gate = threading.Event()
    use_voutput(gated_voutput(gate))
    statuses = list[str]()
    manager.statusLoadingStarted.connect(statuses.append)
    manager.toggle_playback()
    qtbot.waitUntil(lambda: "Buffering..." in statuses, timeout=5000)

    manager.toggle_playback()
    manager.toggle_playback()
    gate.set()
    qtbot.waitUntil(lambda: "Playing..." in statuses and manager.is_idle, timeout=5000)

    assert statuses.count("Playing...") == 1
    assert manager.state.is_playing is True
