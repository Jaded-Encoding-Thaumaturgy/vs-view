from __future__ import annotations

import logging
from collections.abc import Callable, Generator

import pytest
import vapoursynth as vs
from pytest_mock import MockerFixture
from vsengine import ManagedEnvironment

from tests.harness import PluginTestHarness
from vsview.app.outputs.audio import AudioMetadata, AudioOutput
from vsview.app.outputs.manager import OutputsManager
from vsview.app.outputs.video import VideoMetadata, VideoOutput
from vsview.app.plugins import PluginAPI
from vsview.app.plugins.manager import PluginManager

pytestmark = [pytest.mark.integration, pytest.mark.qt, pytest.mark.vpy("no-core")]

core = vs.core


@pytest.fixture(autouse=True)
def use_plugin_manager(init_plugin_manager: PluginManager) -> None: ...


@pytest.fixture
def manager(
    plugin_harness: PluginTestHarness,
    vpy_env_factory: Callable[[], ManagedEnvironment],
) -> Generator[OutputsManager]:
    plugin_harness.env = vpy_env_factory()

    with plugin_harness.env.use():
        yield OutputsManager(plugin_harness)  # pyright: ignore[reportArgumentType]


# Defaults and Index Setters Tests
def test_outputs_manager_defaults(manager: OutputsManager) -> None:
    assert manager.voutputs == []
    assert manager.aoutputs == []
    assert manager.current_video_index == 0
    assert manager.current_audio_index == 0
    assert manager.current_voutput is None
    assert manager.current_aoutput is None


def test_outputs_manager_index_setters(manager: OutputsManager) -> None:
    manager.current_video_index = 3
    assert manager.current_video_index == 3

    manager.current_audio_index = 5
    assert manager.current_audio_index == 5


def test_outputs_manager_current_aoutput_empty(manager: OutputsManager) -> None:
    # When aoutputs is empty, any index returns None
    assert manager.current_aoutput is None
    manager.current_audio_index = 10
    assert manager.current_aoutput is None
    manager.current_audio_index = -1
    assert manager.current_audio_index == -1
    assert manager.current_aoutput is None


def test_outputs_manager_current_aoutput_in_bounds(manager: OutputsManager, sample_audio_node: vs.AudioNode) -> None:
    aouts = manager.create_aoutputs("test", {0: sample_audio_node}, {})
    assert len(aouts) == 1
    assert isinstance(aouts[0], AudioOutput)

    manager.current_audio_index = 0
    assert manager.current_aoutput is aouts[0]


def test_outputs_manager_current_aoutput_out_of_bounds(
    manager: OutputsManager, sample_audio_node: vs.AudioNode
) -> None:
    manager.create_aoutputs("test", {0: sample_audio_node}, {})

    # Out-of-bounds: index 1 and index -1 safely return None
    manager.current_audio_index = 1
    assert manager.current_aoutput is None

    manager.current_audio_index = -1
    assert manager.current_aoutput is None


def test_outputs_manager_current_voutput_empty(manager: OutputsManager) -> None:
    # When empty, returns None
    assert manager.current_voutput is None
    manager.current_video_index = 5
    assert manager.current_voutput is None


def test_outputs_manager_current_voutput_with_items(manager: OutputsManager, sample_video_clip: vs.VideoNode) -> None:
    tup0 = vs.VideoOutputTuple(sample_video_clip, None, 0)
    tup1 = vs.VideoOutputTuple(sample_video_clip, None, 0)
    vouts = manager.create_voutputs("test", {0: tup0, 1: tup1}, {})
    assert len(vouts) == 2
    assert isinstance(vouts[0], VideoOutput)
    assert isinstance(vouts[1], VideoOutput)

    manager.current_video_index = 0
    assert manager.current_voutput is vouts[0]

    manager.current_video_index = 1
    assert manager.current_voutput is vouts[1]


# create_voutputs Tests
def test_create_voutputs_empty_dict(manager: OutputsManager, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        result = manager.create_voutputs("empty_script", {}, {})

    assert result == []
    assert "No video outputs found" in caplog.text


def test_create_voutputs_valid(manager: OutputsManager, sample_video_clip: vs.VideoNode) -> None:
    tup0 = vs.VideoOutputTuple(sample_video_clip, None, 0)
    tup1 = vs.VideoOutputTuple(sample_video_clip, None, 0)
    meta = {0: VideoMetadata(name="Custom Clip 0"), 1: VideoMetadata(name="Custom Clip 1")}

    vouts = manager.create_voutputs("script_ok", {0: tup0, 1: tup1}, meta, last_frame=4)

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
    manager: OutputsManager,
    sample_video_clip: vs.VideoNode,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tup = vs.VideoOutputTuple(sample_video_clip, None, 0)

    # VSVIEW_HDR not set (SDR default: 8-bit integer)
    monkeypatch.delenv("VSVIEW_HDR", raising=False)
    vouts_sdr = manager.create_voutputs("sdr", {0: tup}, {})
    assert len(vouts_sdr) == 1
    assert vouts_sdr[0].packer.format.bitdepth == 8
    assert vouts_sdr[0].packer.format.sample_type == vs.INTEGER

    # VSVIEW_HDR=1 with default metadata / hdr=True -> HDR packer (16-bit float)
    monkeypatch.setenv("VSVIEW_HDR", "1")
    vouts_hdr = manager.create_voutputs("hdr", {0: tup}, {0: VideoMetadata(name="HDR", hdr=True)})
    assert len(vouts_hdr) == 1
    assert vouts_hdr[0].packer.format.bitdepth == 16
    assert vouts_hdr[0].packer.format.sample_type == vs.FLOAT

    # VSVIEW_HDR=1 with hdr=None in metadata -> HDR packer (16-bit float)
    vouts_hdr_none = manager.create_voutputs("hdr_none", {0: tup}, {0: VideoMetadata(name="HDRNone", hdr=None)})
    assert len(vouts_hdr_none) == 1
    assert vouts_hdr_none[0].packer.format.bitdepth == 16
    assert vouts_hdr_none[0].packer.format.sample_type == vs.FLOAT

    # VSVIEW_HDR=1 with explicit hdr=False in metadata -> Fallback to SDR packer (8-bit integer)
    vouts_hdr_false = manager.create_voutputs("hdr_false", {0: tup}, {0: VideoMetadata(name="SDR", hdr=False)})
    assert len(vouts_hdr_false) == 1
    assert vouts_hdr_false[0].packer.format.bitdepth == 8
    assert vouts_hdr_false[0].packer.format.sample_type == vs.INTEGER


def test_create_voutputs_exception_during_prepare(
    manager: OutputsManager,
    sample_video_clip: vs.VideoNode,
    caplog: pytest.LogCaptureFixture,
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

    with monkeypatch.context(), caplog.at_level(logging.ERROR):
        monkeypatch.setattr(VideoOutput, "prepare_video", failing_prepare)
        result = manager.create_voutputs("bad_script", {0: tup0, 1: tup1}, {})

    assert result == []
    assert manager.voutputs == []
    # Output 0 was prepared and added to voutputs, so it should be cleared when Output 1 fails
    assert spy_clear.call_count == 1
    assert "Failed to load script: 'bad_script'" in caplog.text


# create_aoutputs Tests
def test_create_aoutputs_empty_dict(manager: OutputsManager, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        result = manager.create_aoutputs("empty_audio", {}, {})

    assert result == []
    assert "No audio outputs found" in caplog.text


def test_create_aoutputs_valid(manager: OutputsManager, sample_audio_node: vs.AudioNode) -> None:
    meta = {0: AudioMetadata(name="Custom Audio 0", downmix=False), 1: AudioMetadata(name="Custom Audio 1")}

    aouts = manager.create_aoutputs("audio_script", {0: sample_audio_node, 1: sample_audio_node}, meta, delay_s=0.25)

    assert len(aouts) == 2
    assert manager.aoutputs == aouts
    assert aouts[0].vs_index == 0
    assert aouts[0].vs_name == "Custom Audio 0"
    assert aouts[0].downmix is False
    assert isinstance(aouts[0].prepared_audio, vs.AudioNode)
    assert aouts[1].vs_index == 1
    assert aouts[1].vs_name == "Custom Audio 1"
    assert isinstance(aouts[1].prepared_audio, vs.AudioNode)


def test_create_aoutputs_exception_during_prepare(
    manager: OutputsManager,
    sample_audio_node: vs.AudioNode,
    caplog: pytest.LogCaptureFixture,
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

    with monkeypatch.context(), caplog.at_level(logging.ERROR):
        monkeypatch.setattr(AudioOutput, "prepare_audio", failing_prepare)
        result = manager.create_aoutputs("bad_audio_script", {0: sample_audio_node, 1: sample_audio_node}, {})

    assert result == []
    assert manager.aoutputs == []
    # Output 0 was prepared and added to aoutputs, so it should be cleared when Output 1 fails
    assert spy_clear.call_count == 1
    assert "Failed to initialize aoutput: 'bad_audio_script'" in caplog.text


# clear Tests
def test_outputs_manager_clear(
    manager: OutputsManager,
    sample_video_clip: vs.VideoNode,
    sample_audio_node: vs.AudioNode,
    mocker: MockerFixture,
) -> None:
    tup = vs.VideoOutputTuple(sample_video_clip, None, 0)
    vouts = manager.create_voutputs("content", {0: tup}, {})
    aouts = manager.create_aoutputs("content", {0: sample_audio_node}, {})

    assert len(manager.voutputs) == 1
    assert len(manager.aoutputs) == 1

    vo_clear_spy = mocker.spy(vouts[0], "clear")
    ao_clear_spy = mocker.spy(aouts[0], "clear")

    manager.clear()

    vo_clear_spy.assert_called_once()
    ao_clear_spy.assert_called_once()
    assert manager.voutputs == []
    assert manager.aoutputs == []
