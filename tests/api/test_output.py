from __future__ import annotations

import sys
from collections.abc import Generator
from fractions import Fraction
from pathlib import Path
from types import ModuleType

import pytest
import vapoursynth as vs
from jetpytools import CustomValueError
from jetpytools.exceptions import CustomRuntimeError

from vsview.api import catch_output, get_output, get_outputs, set_output
from vsview.api._helpers import output_metadata as _output_metadata
from vsview.app.outputs import AudioMetadata, VideoMetadata

pytestmark = [pytest.mark.unit, pytest.mark.vpy]

core = vs.core


@pytest.fixture(autouse=True)
def isolate_api_output() -> Generator[None]:
    """Isolate VapourSynth output state, metadata store, and __vsview__ module between tests."""
    vs.clear_outputs()
    _output_metadata.clear()
    original_vsview = sys.modules.pop("__vsview__", None)

    try:
        yield
    finally:
        vs.clear_outputs()
        _output_metadata.clear()
        if original_vsview is not None:
            sys.modules["__vsview__"] = original_vsview
        else:
            sys.modules.pop("__vsview__", None)


@pytest.fixture
def mock_vsview_script(tmp_path: Path) -> Path:
    """Create a mock __vsview__ module with a valid script __file__ attribute."""
    script_path = tmp_path / "test_script.vpy"
    script_path.touch()

    mod = ModuleType("__vsview__")
    mod.__file__ = str(script_path)
    sys.modules["__vsview__"] = mod

    return script_path


# set_output: VapourSynth Core Registration (Standalone Mode)
def test_set_output_single_video_default_index(sample_video_clip: vs.VideoNode) -> None:
    set_output(sample_video_clip)

    outputs = vs.get_outputs()
    assert 0 in outputs
    assert len(outputs) == 1
    assert outputs[0].clip == sample_video_clip
    assert outputs[0].alpha is None


def test_set_output_single_video_explicit_index(sample_video_clip: vs.VideoNode) -> None:
    set_output(sample_video_clip, 3)

    outputs = vs.get_outputs()
    assert 3 in outputs
    assert len(outputs) == 1
    assert outputs[3].clip == sample_video_clip


def test_set_output_video_with_alpha_clip(sample_video_clip: vs.VideoNode) -> None:
    alpha_clip = core.std.BlankClip(sample_video_clip, format=vs.GRAY8)
    set_output(sample_video_clip, 0, alpha=alpha_clip)

    outputs = vs.get_outputs()
    assert 0 in outputs
    assert outputs[0].alpha == alpha_clip


def test_set_output_video_with_alpha_true(sample_video_clip: vs.VideoNode) -> None:
    set_output(sample_video_clip, 0, alpha=True)

    outputs = vs.get_outputs()
    assert 0 in outputs
    assert outputs[0].alpha is None


def test_set_output_single_audio_default_index(sample_audio_node: vs.AudioNode) -> None:
    set_output(sample_audio_node)

    outputs = vs.get_outputs()
    assert 0 in outputs
    assert len(outputs) == 1
    assert outputs[0] == sample_audio_node


def test_set_output_single_audio_explicit_index(sample_audio_node: vs.AudioNode) -> None:
    set_output(sample_audio_node, 1)

    outputs = vs.get_outputs()
    assert 1 in outputs
    assert len(outputs) == 1
    assert outputs[1] == sample_audio_node


def test_set_output_multiple_nodes_auto_indexing(
    sample_video_clip: vs.VideoNode, sample_audio_node: vs.AudioNode
) -> None:
    clip2 = core.std.BlankClip(format=vs.RGB24, width=32, height=32, length=5)
    set_output([sample_video_clip, [clip2, sample_audio_node]])

    outputs = vs.get_outputs()
    assert len(outputs) == 3
    assert outputs[0].clip == sample_video_clip
    assert outputs[1].clip == clip2
    assert outputs[2] == sample_audio_node


def test_set_output_multiple_nodes_explicit_indices(
    sample_video_clip: vs.VideoNode, sample_audio_node: vs.AudioNode
) -> None:
    set_output([sample_video_clip, sample_audio_node], [4, 7])

    outputs = vs.get_outputs()
    assert len(outputs) == 2
    assert outputs[4].clip == sample_video_clip
    assert outputs[7] == sample_audio_node


def test_set_output_multiple_nodes_single_start_index(
    sample_video_clip: vs.VideoNode, sample_audio_node: vs.AudioNode
) -> None:
    set_output([sample_video_clip, sample_audio_node], 5)

    outputs = vs.get_outputs()
    assert len(outputs) == 2
    assert outputs[5].clip == sample_video_clip
    assert outputs[6] == sample_audio_node


def test_set_output_consecutive_calls_increment_index(
    sample_video_clip: vs.VideoNode, sample_audio_node: vs.AudioNode
) -> None:
    set_output(sample_video_clip)
    set_output(sample_audio_node)

    outputs = vs.get_outputs()
    assert len(outputs) == 2
    assert outputs[0].clip == sample_video_clip
    assert outputs[1] == sample_audio_node


def test_set_output_overwriting_index_logs_warning(
    sample_video_clip: vs.VideoNode, caplog: pytest.LogCaptureFixture
) -> None:
    set_output(sample_video_clip, 0)
    clip2 = core.std.BlankClip(format=vs.RGB24, width=32, height=32, length=5)

    with caplog.at_level("WARNING"):
        set_output(clip2, 0)

    assert "Output index 0 already in use; overwriting." in caplog.text
    outputs = vs.get_outputs()
    assert outputs[0].clip == clip2


# set_output: VSView Runtime Metadata Registration
def test_set_output_video_auto_variable_name(mock_vsview_script: Path) -> None:
    test_video_var = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)
    set_output(test_video_var)

    meta = get_output(0)
    assert isinstance(meta, VideoMetadata)
    assert meta.name == "test_video_var"
    assert meta.framedurs == ()
    assert meta.alpha_prop is None
    assert meta.hdr is None
    assert meta.kwargs == {}


@pytest.mark.parametrize(
    ("name_arg", "expected_name"),
    [("Explicit Title", "Explicit Title"), (False, "Clip 0"), (True, "video_node"), (None, "video_node")],
)
def test_set_output_video_name_variants(
    mock_vsview_script: Path, name_arg: str | bool | None, expected_name: str
) -> None:
    video_node = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)
    set_output(video_node, name_arg)

    meta = get_output(0)
    assert isinstance(meta, VideoMetadata)
    assert meta.name == expected_name


def test_set_output_video_positional_index_and_name(mock_vsview_script: Path) -> None:
    clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)
    set_output(clip, 2, "Second Clip")

    meta = get_output(2)
    assert isinstance(meta, VideoMetadata)
    assert meta.name == "Second Clip"


def test_set_output_video_positional_name_only(mock_vsview_script: Path) -> None:
    clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)
    set_output(clip, "Positional Name Clip")

    meta = get_output(0)
    assert isinstance(meta, VideoMetadata)
    assert meta.name == "Positional Name Clip"


def test_set_output_video_framedurs_valid(mock_vsview_script: Path) -> None:
    clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=3)
    durs = [0.04, Fraction(1, 25), 0.04]
    set_output(clip, framedurs=durs)

    meta = get_output(0)
    assert isinstance(meta, VideoMetadata)
    assert meta.framedurs == (0.04, 0.04, 0.04)


def test_set_output_video_framedurs_invalid_length_raises(mock_vsview_script: Path) -> None:
    clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=5)
    with pytest.raises(CustomValueError, match=r"framedurs length must match number of frames"):
        set_output(clip, framedurs=[0.04, 0.04])


@pytest.mark.parametrize("alpha_arg", [True, None])
def test_set_output_video_alpha_prop(mock_vsview_script: Path, alpha_arg: bool | None) -> None:
    clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=5)
    set_output(clip, alpha=alpha_arg)  # type: ignore[arg-type]

    meta = get_output(0)
    assert isinstance(meta, VideoMetadata)
    assert meta.alpha_prop is (True if alpha_arg is True else None)


@pytest.mark.parametrize("hdr_arg", [True, False, None])
def test_set_output_video_hdr_flag(mock_vsview_script: Path, hdr_arg: bool | None) -> None:
    clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=5)
    set_output(clip, hdr=hdr_arg)

    meta = get_output(0)
    assert isinstance(meta, VideoMetadata)
    assert meta.hdr is hdr_arg


def test_set_output_video_custom_kwargs(mock_vsview_script: Path) -> None:
    clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=5)
    set_output(clip, custom_param="alpha_blend", quality_score=98.5)

    meta = get_output(0)
    assert isinstance(meta, VideoMetadata)
    assert meta.kwargs == {"custom_param": "alpha_blend", "quality_score": 98.5}


def test_set_output_audio_metadata(mock_vsview_script: Path) -> None:
    audio_track = core.std.BlankAudio(length=1000)
    set_output(audio_track, "Background Music", downmix=True, sample_rate=48000)

    meta = get_output(0)
    assert isinstance(meta, AudioMetadata)
    assert meta.name == "Background Music"
    assert meta.downmix is True
    assert meta.kwargs == {"sample_rate": 48000}


def test_set_output_audio_auto_name_and_disabled_name(mock_vsview_script: Path) -> None:
    my_speech_audio = core.std.BlankAudio(length=1000)
    set_output(my_speech_audio)

    meta0 = get_output(0)
    assert isinstance(meta0, AudioMetadata)
    assert meta0.name == "my_speech_audio"

    other_audio = core.std.BlankAudio(length=1000)
    set_output(other_audio, False)

    meta1 = get_output(1)
    assert isinstance(meta1, AudioMetadata)
    assert meta1.name == "Audio 1"


def test_set_output_when_vsview_has_no_file_attribute() -> None:
    mod = ModuleType("__vsview__")
    sys.modules["__vsview__"] = mod

    clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=5)
    # Should not raise an error even though __file__ is absent
    set_output(clip)

    outputs = vs.get_outputs()
    assert 0 in outputs


# catch_output Decorator Tests
def test_catch_output_bare_decorator_video(mock_vsview_script: Path) -> None:
    @catch_output
    def generate_source_clip() -> vs.VideoNode:
        """Docstring preserved."""
        return core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)

    result = generate_source_clip()

    assert isinstance(result, vs.VideoNode)
    assert generate_source_clip.__name__ == "generate_source_clip"
    assert generate_source_clip.__doc__ == "Docstring preserved."

    outputs = vs.get_outputs()
    assert 0 in outputs
    assert outputs[0].clip == result

    meta = get_output(0)
    assert isinstance(meta, VideoMetadata)
    assert meta.name == "generate_source_clip"


def test_catch_output_bare_decorator_audio(mock_vsview_script: Path) -> None:
    @catch_output
    def generate_theme_audio() -> vs.AudioNode:
        return core.std.BlankAudio(length=2000)

    result = generate_theme_audio()
    assert isinstance(result, vs.AudioNode)

    outputs = vs.get_outputs()
    assert 0 in outputs
    assert outputs[0] == result

    meta = get_output(0)
    assert isinstance(meta, AudioMetadata)
    assert meta.name == "generate_theme_audio"


def test_catch_output_parameterized(mock_vsview_script: Path) -> None:
    @catch_output(
        index=3,
        name="Processed Master",
        alpha=True,
        framedurs=[0.05, 0.05],
        filter_type="bilinear",
    )
    def render_output(width: int, height: int) -> vs.VideoNode:
        return core.std.BlankClip(format=vs.RGB24, width=width, height=height, length=2)

    result = render_output(128, 128)

    assert result.width == 128
    assert result.height == 128

    outputs = vs.get_outputs()
    assert 3 in outputs
    assert outputs[3].clip == result

    meta = get_output(3)
    assert isinstance(meta, VideoMetadata)
    assert meta.name == "Processed Master"
    assert meta.alpha_prop is True
    assert meta.framedurs == (0.05, 0.05)
    assert meta.kwargs == {"filter_type": "bilinear"}


def test_catch_output_with_disabled_name(mock_vsview_script: Path) -> None:
    @catch_output(name=False)
    def anon_clip() -> vs.VideoNode:
        return core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=5)

    anon_clip()
    meta = get_output(0)
    assert isinstance(meta, VideoMetadata)
    assert meta.name == "Clip 0"


def test_catch_output_audio_downmix(mock_vsview_script: Path) -> None:
    @catch_output(downmix=False)
    def stereo_audio() -> vs.AudioNode:
        return core.std.BlankAudio(length=500)

    stereo_audio()
    meta = get_output(0)
    assert isinstance(meta, AudioMetadata)
    assert meta.downmix is False


def test_catch_output_multiple_nodes_returned(mock_vsview_script: Path) -> None:
    @catch_output(index=[1, 3])
    def dual_outputs() -> list[vs.VideoNode]:
        c1 = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=5)
        c2 = core.std.BlankClip(format=vs.RGB24, width=32, height=32, length=5)
        return [c1, c2]

    results = dual_outputs()
    assert len(results) == 2

    outputs = vs.get_outputs()
    assert 1 in outputs
    assert 3 in outputs
    assert outputs[1].clip == results[0]
    assert outputs[3].clip == results[1]


# get_output & get_outputs Tests
def test_get_output_without_vsview_module_raises() -> None:
    with pytest.raises(
        CustomRuntimeError,
        match=r"Output metadata is available only while running a script through VSView; "
        r"the '__vsview__' module is unavailable\.",
    ):
        get_output(0)


def test_get_output_with_missing_file_raises() -> None:
    mod = ModuleType("__vsview__")
    sys.modules["__vsview__"] = mod

    with pytest.raises(
        CustomRuntimeError,
        match=r"Unable to identify the current VSView script because '__vsview__.__file__' is missing\.",
    ):
        get_output(0)


def test_get_output_non_existent_index_raises(mock_vsview_script: Path) -> None:
    clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=5)
    set_output(clip, 0)

    with pytest.raises(KeyError):
        get_output(1)


def test_get_outputs_without_vsview_module_raises() -> None:
    with pytest.raises(
        CustomRuntimeError,
        match=r"Output metadata is available only while running a script through VSView; "
        r"the '__vsview__' module is unavailable\.",
    ):
        get_outputs()


def test_get_outputs_with_missing_file_raises() -> None:
    mod = ModuleType("__vsview__")
    sys.modules["__vsview__"] = mod

    with pytest.raises(
        CustomRuntimeError,
        match=r"Unable to identify the current VSView script because '__vsview__.__file__' is missing\.",
    ):
        get_outputs()


def test_get_outputs_returns_immutable_mapping(mock_vsview_script: Path) -> None:
    clip1 = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=5)
    clip2 = core.std.BlankClip(format=vs.RGB24, width=32, height=32, length=5)
    set_output(clip1, 0, "First")
    set_output(clip2, 2, "Second")

    all_outputs = get_outputs()
    assert len(all_outputs) == 2
    assert 0 in all_outputs
    assert 2 in all_outputs
    assert all_outputs[0].name == "First"
    assert all_outputs[2].name == "Second"

    with pytest.raises(TypeError):
        all_outputs[0] = VideoMetadata("Modified")  # type: ignore[index]
