from __future__ import annotations

from collections.abc import Callable, Generator
from typing import override

import pytest
import vapoursynth as vs
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsengine import ManagedEnvironment

from tests.harness import HarnessPluginController
from vsview.app.outputs.audio import AudioMetadata, AudioOutput
from vsview.app.outputs.manager import OutputsManager
from vsview.app.outputs.video import VideoMetadata, VideoOutput
from vsview.app.packing import Packer
from vsview.app.plugins import PluginAPI, specs
from vsview.app.plugins.api import NodeProcessor
from vsview.app.plugins.manager import PluginManager

pytestmark = [pytest.mark.integration, pytest.mark.qt, pytest.mark.vpy("no-core")]

core = vs.core


@pytest.fixture(autouse=True)
def use_plugin_manager(init_plugin_manager: PluginManager) -> None: ...


@pytest.fixture
def manager(
    plugin_controller: HarnessPluginController,
    vpy_env_factory: Callable[[], ManagedEnvironment],
) -> Generator[OutputsManager]:
    plugin_controller.env = vpy_env_factory()

    with plugin_controller.env.use():
        yield OutputsManager()


# Current output selection
INDEX_CASES = [
    pytest.param(0, 0, None, id="empty"),
    pytest.param(0, 5, None, id="empty_with_index"),
    pytest.param(2, 0, 0, id="first"),
    pytest.param(2, 1, 1, id="second"),
    pytest.param(2, -1, None, id="negative"),
    pytest.param(2, 2, None, id="past_the_end"),
]


@pytest.mark.parametrize(("count", "index", "expected"), INDEX_CASES)
def test_current_voutput_follows_the_index_and_is_none_out_of_range(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    sample_video_clip: vs.VideoNode,
    count: int,
    index: int,
    expected: int | None,
) -> None:
    nodes = {i: vs.VideoOutputTuple(sample_video_clip, None, 0) for i in range(count)}
    vouts = manager.create_voutputs("test", nodes, {}, plugin_controller.api) if nodes else []

    manager.current_video_index = index

    assert manager.current_voutput is (None if expected is None else vouts[expected])


@pytest.mark.parametrize(("count", "index", "expected"), INDEX_CASES)
def test_current_aoutput_follows_the_index_and_is_none_out_of_range(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    sample_audio_node: vs.AudioNode,
    count: int,
    index: int,
    expected: int | None,
) -> None:
    nodes = dict.fromkeys(range(count), sample_audio_node)
    aouts = manager.create_aoutputs("test", nodes, {}, plugin_controller.api) if nodes else []

    manager.current_audio_index = index

    assert manager.current_aoutput is (None if expected is None else aouts[expected])


# create_voutputs Tests
def test_create_voutputs_empty_dict(plugin_controller: HarnessPluginController, manager: OutputsManager) -> None:
    result = manager.create_voutputs("empty_script", {}, {}, plugin_controller.api)

    assert result == []
    assert manager.voutputs == []


def test_create_voutputs_valid(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    sample_video_clip: vs.VideoNode,
) -> None:
    tup0 = vs.VideoOutputTuple(sample_video_clip, None, 0)
    tup1 = vs.VideoOutputTuple(sample_video_clip, None, 0)
    meta = {0: VideoMetadata(name="Custom Clip 0"), 1: VideoMetadata(name="Custom Clip 1")}

    vouts = manager.create_voutputs("script_ok", {0: tup0, 1: tup1}, meta, plugin_controller.api, last_frame=4)

    assert len(vouts) == 2
    assert manager.voutputs == vouts
    assert vouts[0].vs_index == 0
    assert vouts[0].vs_name == "Custom Clip 0"
    assert vouts[0].last_frame == 4
    assert isinstance(vouts[0].prepared_clip, vs.VideoNode)
    assert vouts[1].vs_index == 1
    assert vouts[1].vs_name == "Custom Clip 1"
    assert vouts[1].last_frame == 4
    assert isinstance(vouts[1].prepared_clip, vs.VideoNode)


def test_create_voutputs_respects_hdr_env(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    sample_video_clip: vs.VideoNode,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tup = vs.VideoOutputTuple(sample_video_clip, None, 0)

    # VSVIEW_HDR not set (SDR default: 8-bit integer)
    monkeypatch.delenv("VSVIEW_HDR", raising=False)
    vouts_sdr = manager.create_voutputs("sdr", {0: tup}, {}, plugin_controller.api)
    assert len(vouts_sdr) == 1
    assert vouts_sdr[0].packer.format.bitdepth == 8
    assert vouts_sdr[0].packer.format.sample_type == vs.INTEGER

    # VSVIEW_HDR=1 with default metadata / hdr=True -> HDR packer (16-bit float)
    monkeypatch.setenv("VSVIEW_HDR", "1")
    vouts_hdr = manager.create_voutputs(
        "hdr", {0: tup}, {0: VideoMetadata(name="HDR", hdr=True)}, plugin_controller.api
    )
    assert len(vouts_hdr) == 1
    assert vouts_hdr[0].packer.format.bitdepth == 16
    assert vouts_hdr[0].packer.format.sample_type == vs.FLOAT

    # VSVIEW_HDR=1 with hdr=None in metadata -> HDR packer (16-bit float)
    vouts_hdr_none = manager.create_voutputs(
        "hdr_none", {0: tup}, {0: VideoMetadata(name="HDRNone", hdr=None)}, plugin_controller.api
    )
    assert len(vouts_hdr_none) == 1
    assert vouts_hdr_none[0].packer.format.bitdepth == 16
    assert vouts_hdr_none[0].packer.format.sample_type == vs.FLOAT

    # VSVIEW_HDR=1 with explicit hdr=False in metadata -> Fallback to SDR packer (8-bit integer)
    vouts_hdr_false = manager.create_voutputs(
        "hdr_false", {0: tup}, {0: VideoMetadata(name="SDR", hdr=False)}, plugin_controller.api
    )
    assert len(vouts_hdr_false) == 1
    assert vouts_hdr_false[0].packer.format.bitdepth == 8
    assert vouts_hdr_false[0].packer.format.sample_type == vs.INTEGER


def test_create_voutputs_exception_during_prepare(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    sample_video_clip: vs.VideoNode,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    tup0 = vs.VideoOutputTuple(sample_video_clip, None, 0)
    tup1 = vs.VideoOutputTuple(sample_video_clip, None, 0)

    call_count = 0
    orig_prepare = VideoOutput.prepare_video

    def failing_prepare(self: VideoOutput, api: PluginAPI) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Simulated prepare failure on second output")
        orig_prepare(self, api)

    spy_clear = mocker.spy(VideoOutput, "clear")

    monkeypatch.setattr(VideoOutput, "prepare_video", failing_prepare)
    result = manager.create_voutputs("bad_script", {0: tup0, 1: tup1}, {}, plugin_controller.api)

    assert result == []
    assert manager.voutputs == []
    # Output 0 was prepared and added to voutputs, so it should be cleared when Output 1 fails
    assert spy_clear.call_count == 1


def test_create_voutputs_auto_gpu_download(plugin_controller: HarnessPluginController, manager: OutputsManager) -> None:
    if vs.__version__.release_major < 80:
        pytest.skip("VapourSynth < 80")

    try:
        vulkan_devices = core.vulkan_devices
    except vs.Error as exc:
        pytest.skip(f"Vulkan unavailable: {exc}")
    if not vulkan_devices:
        pytest.skip("No GPU available")

    clip = core.std.BlankClip(gpu=True)
    assert clip.gpu_resident is True
    tup0 = vs.VideoOutputTuple(core.std.BlankClip(gpu=True), None, 0)
    vouts = manager.create_voutputs("test", {0: tup0}, {}, plugin_controller.api)
    assert len(vouts) == 1
    vout = vouts[0]
    assert vout.prepared_clip.gpu_resident is False


# create_aoutputs Tests
def test_create_aoutputs_empty_dict(plugin_controller: HarnessPluginController, manager: OutputsManager) -> None:
    result = manager.create_aoutputs("empty_audio", {}, {}, plugin_controller.api)

    assert result == []
    assert manager.aoutputs == []


def test_create_aoutputs_valid(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    sample_audio_node: vs.AudioNode,
) -> None:
    meta = {0: AudioMetadata(name="Custom Audio 0", downmix=False), 1: AudioMetadata(name="Custom Audio 1")}

    aouts = manager.create_aoutputs(
        "audio_script", {0: sample_audio_node, 1: sample_audio_node}, meta, plugin_controller.api, delay_s=0.25
    )

    assert len(aouts) == 2
    assert manager.aoutputs == aouts
    assert aouts[0].vs_index == 0
    assert aouts[0].vs_name == "Custom Audio 0"
    assert aouts[0].downmix is False
    assert isinstance(aouts[0].prepared_audio, vs.AudioNode)
    assert aouts[1].vs_index == 1
    assert aouts[1].vs_name == "Custom Audio 1"
    assert isinstance(aouts[1].prepared_audio, vs.AudioNode)


@pytest.mark.parametrize("stale_index", [-1, 1, 5])
def test_create_aoutputs_clamps_an_audio_index_that_is_out_of_range(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    sample_audio_node: vs.AudioNode,
    stale_index: int,
) -> None:
    manager.current_audio_index = stale_index

    aouts = manager.create_aoutputs("one_track", {0: sample_audio_node}, {}, plugin_controller.api)

    assert manager.current_audio_index == 0
    assert manager.current_aoutput is aouts[0]


def test_create_aoutputs_keeps_an_audio_index_that_is_in_range(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    sample_audio_node: vs.AudioNode,
) -> None:
    manager.current_audio_index = 1

    aouts = manager.create_aoutputs(
        "two_tracks",
        {0: sample_audio_node, 1: sample_audio_node},
        {},
        plugin_controller.api,
    )

    assert manager.current_audio_index == 1
    assert manager.current_aoutput is aouts[1]


def test_create_aoutputs_exception_during_prepare(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    sample_audio_node: vs.AudioNode,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    call_count = 0
    orig_prepare = AudioOutput.prepare_audio

    def failing_prepare(self: AudioOutput, delay_s: float, api: PluginAPI) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Simulated audio prepare failure on second output")
        orig_prepare(self, delay_s, api)

    spy_clear = mocker.spy(AudioOutput, "clear")

    monkeypatch.setattr(AudioOutput, "prepare_audio", failing_prepare)
    result = manager.create_aoutputs(
        "bad_audio_script", {0: sample_audio_node, 1: sample_audio_node}, {}, plugin_controller.api
    )

    assert result == []
    assert manager.aoutputs == []
    # Output 0 was prepared and added to aoutputs, so it should be cleared when Output 1 fails
    assert spy_clear.call_count == 1


class StereoOnlyAudioProcessor(NodeProcessor[vs.AudioNode]):
    """Raises for any audio that is not stereo, and halves the gain of stereo audio."""

    identifier = "test_stereo_only_audio_processor"
    display_name = "Stereo Only"

    @override
    def prepare(self, node: vs.AudioNode, /) -> vs.AudioNode:
        if node.num_channels != 2:
            raise ValueError(f"cannot process {node.num_channels} channels")
        return node.std.AudioGain(0.5)


@pytest.fixture
def stereo_only_audio_processor(init_plugin_manager: PluginManager) -> Generator[None]:
    class _Plugin:
        @specs.hookimpl
        def vsview_get_audio_processor(self) -> type[StereoOnlyAudioProcessor]:
            return StereoOnlyAudioProcessor

    plugin = _Plugin()
    init_plugin_manager.manager.register(plugin, name="stereo_only")
    try:
        yield
    finally:
        init_plugin_manager.manager.unregister(plugin)


@pytest.mark.usefixtures("stereo_only_audio_processor")
def test_create_aoutputs_plays_the_raw_audio_of_an_output_whose_processor_fails(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    qtbot: QtBot,
) -> None:
    stereo = core.std.BlankAudio(length=10000)
    mono = core.std.BlankAudio(channels=[vs.AudioChannels.FRONT_LEFT], length=10000)

    with qtbot.waitSignal(plugin_controller.statusMessage, timeout=1000) as blocker:
        aouts = manager.create_aoutputs(
            "mixed_audio", {0: stereo, 1: mono}, {1: AudioMetadata(name="Mono")}, plugin_controller.api
        )

    assert manager.aoutputs == aouts
    assert [a.vs_index for a in aouts] == [0, 1]
    assert aouts[0].prepared_audio is not stereo
    assert aouts[1].prepared_audio is mono
    assert aouts[1].qformat.channelCount() == 1
    assert blocker.args == [
        "Audio processor 'Stereo Only' failed on audio output 'Mono', playing the raw audio: cannot process 1 channels"
    ]


@pytest.mark.usefixtures("stereo_only_audio_processor")
def test_create_aoutputs_keeps_the_delay_when_the_processor_fails(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
) -> None:
    mono = core.std.BlankAudio(channels=[vs.AudioChannels.FRONT_LEFT], length=10000)
    aouts = manager.create_aoutputs("delayed_mono", {0: mono}, {}, plugin_controller.api, delay_s=0.25)

    assert len(aouts) == 1
    assert aouts[0].prepared_audio is not mono
    assert aouts[0].prepared_audio.num_samples == mono.num_samples
    assert aouts[0].prepared_audio.num_channels == 1


# clear Tests
def test_outputs_manager_clear(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    sample_video_clip: vs.VideoNode,
    sample_audio_node: vs.AudioNode,
    mocker: MockerFixture,
) -> None:
    tup = vs.VideoOutputTuple(sample_video_clip, None, 0)
    vouts = manager.create_voutputs("content", {0: tup}, {}, plugin_controller.api)
    aouts = manager.create_aoutputs("content", {0: sample_audio_node}, {}, plugin_controller.api)

    assert len(manager.voutputs) == 1
    assert len(manager.aoutputs) == 1

    vo_clear_spy = mocker.spy(vouts[0], "clear")
    ao_clear_spy = mocker.spy(aouts[0], "clear")

    manager.clear()

    vo_clear_spy.assert_called_once()
    ao_clear_spy.assert_called_once()
    assert manager.voutputs == []
    assert manager.aoutputs == []


# VideoOutput Tests
@pytest.mark.vpy("unique-core")
@pytest.mark.parametrize(
    ("sar_num", "sar_den", "expected"),
    [(1, 0, 1.0), (0, 1, 1.0), (-4, 3, 1.0), (4, -3, 1.0), (16, 11, 16 / 11)],
    ids=["zero_den", "zero_num", "negative_num", "negative_den", "valid"],
)
def test_video_output_info_treats_an_invalid_sar_as_square_pixels(
    plugin_controller: HarnessPluginController,
    vpy_env: ManagedEnvironment,
    sar_num: int,
    sar_den: int,
    expected: float,
) -> None:
    plugin_controller.env = vpy_env
    clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10, fpsnum=24, fpsden=1)
    clip = core.std.SetFrameProps(
        clip,
        _Matrix=vs.MATRIX_RGB,
        _Primaries=vs.PRIMARIES_BT709,
        _Transfer=vs.TRANSFER_BT709,
        _SARNum=sar_num,
        _SARDen=sar_den,
    )
    packer = Packer()
    voutput = VideoOutput(vs.VideoOutputTuple(clip, None, 0), 0, packer)
    voutput.prepare_video(plugin_controller.api)
    try:
        voutput.prepared_clip.get_frame(0).close()
        assert voutput.info.sar == expected
    finally:
        voutput.clear()


@pytest.mark.vpy("unique-core")
def test_video_output_midpoints_cfr_and_vfr() -> None:
    # CFR clip (fps > 0 and no framedurs) has output.midpoints is None
    cfr_clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=5, fpsnum=24, fpsden=1)
    cfr_output = VideoOutput(vs.VideoOutputTuple(cfr_clip, None, 0), 0, Packer())
    try:
        assert cfr_output.framedurs is None
        assert cfr_output.vs_output.clip.fps > 0
        assert cfr_output.midpoints is None
    finally:
        cfr_output.clear()

    # VFR clip with framedurs (fps > 0) has output.midpoints as a valid tuple
    durs = (0.5, 0.25, 0.5, 0.25, 0.5)
    vfr_clip_1 = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=len(durs), fpsnum=24, fpsden=1)
    meta_1 = VideoMetadata(name="VFR1", framedurs=durs)
    vfr_output_1 = VideoOutput(vs.VideoOutputTuple(vfr_clip_1, None, 0), 1, Packer(), metadata=meta_1)
    try:
        assert vfr_output_1.framedurs == durs
        assert isinstance(vfr_output_1.midpoints, tuple)
        assert vfr_output_1.midpoints == (0.25, 0.625, 1.0, 1.375)
    finally:
        vfr_output_1.clear()

    # VFR clip with fps == 0 has output.midpoints as a valid tuple
    vfr_clip_2 = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=len(durs), fpsnum=0, fpsden=1)
    meta_2 = VideoMetadata(name="VFR2", framedurs=durs)
    vfr_output_2 = VideoOutput(vs.VideoOutputTuple(vfr_clip_2, None, 0), 2, Packer(), metadata=meta_2)
    try:
        assert vfr_output_2.vs_output.clip.fps == 0
        assert isinstance(vfr_output_2.midpoints, tuple)
        assert vfr_output_2.midpoints == (0.25, 0.625, 1.0, 1.375)
    finally:
        vfr_output_2.clear()


# AudioOutput Tests
@pytest.mark.parametrize(
    ("start_s", "stop_s", "expected"),
    [(0.5, 2.0, True), (1.0, 2.0, False), (1.5, 2.0, False)],
    ids=["inside", "at_the_end", "past_the_end"],
)
def test_audio_output_playback_window_starting_at_or_past_the_audio_end_has_no_audio(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    start_s: float,
    stop_s: float,
    expected: bool,
) -> None:
    one_second = core.std.BlankAudio(length=48000, samplerate=48000)
    [aout] = manager.create_aoutputs("short_audio", {0: one_second}, {}, plugin_controller.api)

    assert aout.prepare_playback_audio(start_s, stop_s) is expected


def test_audio_output_playback_window_inside_the_audio_is_trimmed_to_the_audio_end(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
) -> None:
    one_second = core.std.BlankAudio(length=48000, samplerate=48000)
    [aout] = manager.create_aoutputs("short_audio", {0: one_second}, {}, plugin_controller.api)

    aout.prepare_playback_audio(0.5, 2.0)

    assert aout.playback_audio.num_samples == 24000


@pytest.mark.parametrize("delay_s", [-0.5, -1.0, -1.5], ids=["shorter", "equal", "longer"])
def test_negative_audio_delay_keeps_the_track_length(
    plugin_controller: HarnessPluginController,
    manager: OutputsManager,
    delay_s: float,
) -> None:
    one_second = core.std.BlankAudio(length=48000, samplerate=48000)
    [aout] = manager.create_aoutputs("short_audio", {0: one_second}, {}, plugin_controller.api)

    aout.prepare_audio(delay_s, plugin_controller.api)

    assert aout.prepared_audio.num_samples == 48000
    assert aout.prepared_audio.sample_rate == 48000
