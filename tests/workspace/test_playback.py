from __future__ import annotations

from collections.abc import Callable, Generator
from concurrent.futures import Future
from typing import Any, NamedTuple
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment

from tests.harness import PluginTestHarness
from vsview.app.outputs import AudioBuffer, FrameBuffer
from vsview.app.outputs.audio import AudioOutput
from vsview.app.outputs.video import VideoOutput
from vsview.app.views.timeline import TimelineControlBar
from vsview.app.workspace.playback import PlaybackManager, PlaybackState
from vsview.types import Frame, Time
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt]


# PlaybackState Tests
def test_playback_state_init(qapp: QApplication) -> None:
    state = PlaybackState(qapp)

    assert state.current_frame == 0
    assert state.current_time == Time()
    assert state.is_playing is False

    assert state.last_fps_update_ns == 0
    assert len(state.fps_history) == 0

    assert state.buffer is None
    assert state.frame_interval_ns == 0
    assert state.next_frame_time_ns == 0

    assert state.audio_buffer is None
    assert state.audio_frame_interval_ns == 0
    assert state.next_audio_frame_time_ns == 0

    assert isinstance(state.video_timer, QTimer)
    assert state.video_timer.isSingleShot()
    assert not state.video_timer.isActive()

    assert isinstance(state.audio_timer, QTimer)
    assert state.audio_timer.isSingleShot()
    assert not state.audio_timer.isActive()


@pytest.mark.vpy("no-core")
def test_playback_state_reset(
    qapp: QApplication,
    qt_event_loop: QtEventLoop,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
    create_test_audio_output: Callable[[ManagedEnvironment, int], AudioOutput],
) -> None:
    state = PlaybackState(qapp)
    env = vpy_env_factory()

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

    voutput = create_test_video_output(env, 100)
    video_buffer = FrameBuffer(voutput, env)
    state.buffer = video_buffer

    aoutput = create_test_audio_output(env, 1000)
    audio_buffer = AudioBuffer(aoutput, env)
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


@pytest.mark.vpy("no-core")
def test_playback_state_reset_audio(
    qapp: QApplication,
    qt_event_loop: QtEventLoop,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    create_test_audio_output: Callable[[ManagedEnvironment, int], AudioOutput],
) -> None:
    state = PlaybackState(qapp)
    env = vpy_env_factory()

    state.next_audio_frame_time_ns = 77_777_777
    state.audio_frame_interval_ns = 21_333_333
    state.audio_timer.start(100)
    assert state.audio_timer.isActive()

    aoutput = create_test_audio_output(env, 1000)
    audio_buffer = AudioBuffer(aoutput, env)
    state.audio_buffer = audio_buffer

    state.reset_audio()

    assert state.next_audio_frame_time_ns == 0
    assert state.audio_frame_interval_ns == 0
    assert not state.audio_timer.isActive()

    assert state.audio_buffer is None
    state.wait_for_cleanup()


@pytest.mark.vpy("no-core")
def test_playback_state_wait_for_cleanup(
    qapp: QApplication,
    qt_event_loop: QtEventLoop,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
    mocker: MockerFixture,
) -> None:
    state = PlaybackState(qapp)
    env = vpy_env_factory()

    # 1. No pending futures - completes immediately
    state.wait_for_cleanup()
    assert state.buffer is None
    assert state.audio_buffer is None

    # 2. Reset real buffer and wait for cleanup without stalling
    voutput = create_test_video_output(env, 10)
    state.buffer = FrameBuffer(voutput, env)
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

    slow_buffer = FrameBuffer(voutput, env)
    mocker.patch.object(FrameBuffer, "invalidate", return_value=undone_fut)
    state.buffer = slow_buffer
    state.reset()

    state.wait_for_cleanup(timeout=0.01, stall_cb=stall_handler)
    assert stall_triggered is True


# PlaybackManager Fixtures & Helpers
class PlaybackManagerSetup(NamedTuple):
    playback_manager: PlaybackManager
    env: ManagedEnvironment
    plugin_harness: PluginTestHarness
    mock_outputs: MagicMock
    mock_tabs: MagicMock
    tbar: TimelineControlBar


@pytest.fixture
def playback_manager_setup(
    qt_event_loop: QtEventLoop,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    plugin_harness: PluginTestHarness,
    qtbot: QtBot,
) -> Generator[PlaybackManagerSetup, None, None]:
    env = vpy_env_factory()

    mock_outputs = MagicMock()
    mock_outputs.current_voutput = None
    mock_outputs.current_aoutput = None
    mock_outputs.aoutputs = []

    mock_tabs = MagicMock()
    mock_tabs.tabs.currentIndex.return_value = 0

    tbar = TimelineControlBar()
    qtbot.addWidget(tbar)

    # Attach internal workspace lifecycle hooks expected by PlaybackManager
    for name in (
        "_on_playback_started",
        "_on_playback_stopped",
        "_on_current_frame_changed",
        "_register_plugin_nodes_to_buffer",
    ):
        if not hasattr(plugin_harness, name):
            setattr(plugin_harness, name, MagicMock())

    manager = PlaybackManager(
        loop=qt_event_loop,
        get_env=lambda: env,
        api=plugin_harness,  # pyright: ignore[reportArgumentType]
        outputs_manager=mock_outputs,
        tab_manager=mock_tabs,
        tbar=tbar,
    )

    try:
        yield PlaybackManagerSetup(manager, env, plugin_harness, mock_outputs, mock_tabs, tbar)
    finally:
        manager.stop()


# PlaybackManager Tests
@pytest.mark.vpy("no-core")
def test_playback_manager_seek_bounds(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
    qtbot: QtBot,
) -> None:
    manager, env, _, mock_outputs, _, _ = playback_manager_setup
    voutput = create_test_video_output(env, 100)
    mock_outputs.current_voutput = voutput

    try:
        # Initial frame at 0
        assert manager.state.current_frame == 0

        # Seek backward from frame 0 -> clamps at 0
        manager.seek_frame(-10)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager._timeline_rendering, timeout=2000)
        assert manager.state.current_frame == 0

        # Seek forward past frame 99 -> clamps at 99
        manager.seek_frame(200)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager._timeline_rendering, timeout=2000)
        assert manager.state.current_frame == 99

        # Seek relative step backward from 99
        # Default seek_step is 1, so seek_n_frames(-5) seeks by -5 frames
        manager.seek_n_frames(-5)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager._timeline_rendering, timeout=2000)
        assert manager.state.current_frame == 94

        # Seek relative step forward
        manager.seek_n_frames(5)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager._timeline_rendering, timeout=2000)
        assert manager.state.current_frame == 99

        # Seek to frame 50 then step forward and backward
        manager.seek_to_frame_coalesced(Frame(50))
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager._timeline_rendering, timeout=2000)
        assert manager.state.current_frame == 50

        manager.seek_n_frames(5)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager._timeline_rendering, timeout=2000)
        assert manager.state.current_frame == 55

        manager.seek_n_frames(-5)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager._timeline_rendering, timeout=2000)
        assert manager.state.current_frame == 50

        # Stepping backward past frame 0 clamps at 0
        manager.seek_to_frame_coalesced(Frame(3))
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager._timeline_rendering, timeout=2000)
        manager.seek_n_frames(-10)
        qtbot.waitUntil(lambda: not manager.has_pending_frame and not manager._timeline_rendering, timeout=2000)
        assert manager.state.current_frame == 0
    finally:
        voutput.clear()
        mock_outputs.current_voutput = None


def test_playback_manager_pending_frame_prop(
    playback_manager_setup: PlaybackManagerSetup,
    mocker: MockerFixture,
) -> None:
    manager, *_ = playback_manager_setup

    # Default state: no pending frame
    assert manager.has_pending_frame is False

    # During in-flight rendering, seeking sets pending frame without dispatching immediately
    mocker.patch.object(manager, "_timeline_rendering", True)
    manager.seek_to_frame_coalesced(Frame(15))
    assert manager.has_pending_frame is True

    # When rendering completes, pending frame is rendered and cleared
    mocker.patch.object(manager, "_timeline_rendering", False)
    manager._render_pending_frame()
    assert manager.has_pending_frame is False


@pytest.mark.vpy("no-core")
def test_playback_manager_volume_and_mute(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_audio_output: Callable[[ManagedEnvironment, int], AudioOutput],
    mocker: MockerFixture,
) -> None:
    manager, env, _, mock_outputs, _, tbar = playback_manager_setup

    mock_aoutput = MagicMock()
    mock_outputs.current_aoutput = mock_aoutput

    # Direct handler call
    manager._on_volume_changed(0.85)
    assert mock_aoutput.volume == 0.85

    # Triggered via TimelineControlBar volumeChanged signal
    tbar.playback_container.volumeChanged.emit(0.4)
    assert mock_aoutput.volume == 0.4

    # Guard check when current_aoutput is None
    mock_outputs.current_aoutput = None
    manager._on_volume_changed(0.5)

    # Mute changed to True: stops audio sink and invalidates audio buffer
    mock_outputs.current_aoutput = mock_aoutput
    aoutput = create_test_audio_output(env, 10000)
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


def test_playback_manager_signal_declarations(playback_manager_setup: PlaybackManagerSetup) -> None:
    manager, *_ = playback_manager_setup

    # frameRendered(QImage, object, float)
    rendered_data: list[tuple[QImage, Any, float]] = []
    manager.frameRendered.connect(lambda img, f, sar: rendered_data.append((img, f, sar)))
    sample_img = QImage(16, 16, QImage.Format.Format_RGB32)
    manager.frameRendered.emit(sample_img, None, 1.33)
    assert len(rendered_data) == 1
    assert rendered_data[0] == (sample_img, None, 1.33)

    # timelineCursorChanged(int)
    cursor_positions = list[int]()
    manager.timelineCursorChanged.connect(cursor_positions.append)
    manager.timelineCursorChanged.emit(77)
    assert cursor_positions == [77]

    # audioOutputChanged(int)
    audio_indices = list[int]()
    manager.audioOutputChanged.connect(audio_indices.append)
    manager.audioOutputChanged.emit(2)
    assert audio_indices == [2]

    # loadFailed()
    load_failed_called = False

    def on_load_failed() -> None:
        nonlocal load_failed_called
        load_failed_called = True

    manager.loadFailed.connect(on_load_failed)
    manager.loadFailed.emit()
    assert load_failed_called is True

    # status loading signals
    started_msgs = list[str]()
    manager.statusLoadingStarted.connect(started_msgs.append)
    manager.statusLoadingStarted.emit("Buffering...")
    assert started_msgs == ["Buffering..."]

    finished_msgs = list[str]()
    manager.statusLoadingFinished.connect(finished_msgs.append)
    manager.statusLoadingFinished.emit("Paused")
    assert finished_msgs == ["Paused"]

    errored_msgs = list[str]()
    manager.statusLoadingErrored.connect(errored_msgs.append)
    manager.statusLoadingErrored.emit("Playback error")
    assert errored_msgs == ["Playback error"]


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
    manager, _, _, _, _, _ = playback_manager_setup
    audio_index_received = list[int]()
    manager.audioOutputChanged.connect(audio_index_received.append)

    restart_mock = mocker.patch.object(manager, "_restart_playback")

    # When not playing: emits signal, does not restart
    manager.state.is_playing = False
    manager._on_audio_output_changed(2)
    assert audio_index_received == [2]
    restart_mock.assert_not_called()

    # When playing: emits signal and restarts playback
    manager.state.is_playing = True
    manager._on_audio_output_changed(3)
    assert audio_index_received == [2, 3]
    restart_mock.assert_called_once()


def test_playback_manager_playback_settings_changed(
    playback_manager_setup: PlaybackManagerSetup, mocker: MockerFixture
) -> None:
    manager, _, _, _, _, tbar = playback_manager_setup
    restart_mock = mocker.patch.object(manager, "_restart_playback")

    # Not playing: updates settings, no restart
    manager.state.is_playing = False
    manager._on_playback_settings_changed(seek_step=10, speed=2.0, uncapped=True)
    assert tbar.playback_container.settings.seek_step == 10
    assert tbar.playback_container.settings.speed == 2.0
    assert tbar.playback_container.settings.uncapped is True
    restart_mock.assert_not_called()

    # Playing: updates settings and restarts playback
    manager.state.is_playing = True
    manager._on_playback_settings_changed(seek_step=5, speed=1.0, uncapped=False)
    assert tbar.playback_container.settings.seek_step == 5
    assert tbar.playback_container.settings.speed == 1.0
    assert tbar.playback_container.settings.uncapped is False
    restart_mock.assert_called_once()


def test_playback_manager_stop(playback_manager_setup: PlaybackManagerSetup, mocker: MockerFixture) -> None:
    manager, _, _, _, _, _ = playback_manager_setup
    stop_playback_mock = mocker.patch.object(manager, "_stop_playback")

    manager.state.is_playing = False
    manager.stop()
    stop_playback_mock.assert_not_called()

    manager.state.is_playing = True
    manager.stop()
    stop_playback_mock.assert_called_once()


@pytest.mark.vpy("no-core")
def test_playback_manager_api_busy_frame_request(
    playback_manager_setup: PlaybackManagerSetup,
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
) -> None:
    manager, env, plugin_harness, mock_outputs, _, _ = playback_manager_setup
    voutput = create_test_video_output(env, 10)
    mock_outputs.current_voutput = voutput

    try:
        plugin_harness.busy = True
        callback_results = list[int]()

        def on_rendered(fut: Future[int]) -> None:
            callback_results.append(fut.result())

        manager.request_frame(5, cb_render=on_rendered)
        assert callback_results == [2]
    finally:
        voutput.clear()
        mock_outputs.current_voutput = None
        plugin_harness.busy = False
