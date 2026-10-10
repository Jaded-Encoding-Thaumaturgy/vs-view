from __future__ import annotations

import math

import pytest
import vapoursynth as vs

from vsview.app.tools.colorpicker.utils import (
    get_chroma_offsets,
    get_lowest_value,
    get_peak_value,
    scale_value_to_float,
)

core = vs.core
pytestmark = [pytest.mark.unit, pytest.mark.vpy("initial-core")]


@pytest.mark.parametrize(
    ("chroma_loc", "expected_offsets"),
    [
        (vs.CHROMA_LEFT, (0.0, 0.5)),
        (vs.CHROMA_TOP_LEFT, (0.5, 0.5)),
        (vs.CHROMA_TOP, (0.5, 0.0)),
        (vs.CHROMA_BOTTOM_LEFT, (0.0, 0.5)),
        (0, (0.0, 0.5)),
        (vs.CHROMA_CENTER, (0.0, 0.0)),
        (99, (0.0, 0.0)),
    ],
)
def test_get_chroma_offsets_420(chroma_loc: int, expected_offsets: tuple[float, float]) -> None:
    clip_420 = core.std.BlankClip(format=vs.YUV420P8, width=32, height=32, length=1)
    frame = core.std.SetFrameProp(clip_420, prop="_ChromaLocation", intval=chroma_loc).get_frame(0)

    assert get_chroma_offsets(frame) == expected_offsets


def test_get_chroma_offsets_420_missing_prop() -> None:
    clip_420 = core.std.BlankClip(format=vs.YUV420P8, width=32, height=32, length=1)
    frame = clip_420.get_frame(0)

    assert "_ChromaLocation" not in frame.props
    assert get_chroma_offsets(frame) == (0.0, 0.5)


@pytest.mark.parametrize("chroma_loc", [vs.CHROMA_LEFT, vs.CHROMA_TOP_LEFT, vs.CHROMA_TOP, None])
def test_get_chroma_offsets_444(chroma_loc: int | None) -> None:
    clip_444 = core.std.BlankClip(format=vs.YUV444P8, width=32, height=32, length=1)
    if chroma_loc is not None:
        frame = core.std.SetFrameProp(clip_444, prop="_ChromaLocation", intval=chroma_loc).get_frame(0)
    else:
        frame = clip_444.get_frame(0)

    assert get_chroma_offsets(frame) == (0.0, 0.0)


@pytest.mark.parametrize(
    ("fmt_id", "chroma", "range_in", "expected"),
    [
        (vs.YUV420P8, False, vs.RANGE_LIMITED, 16.0),
        (vs.YUV420P8, True, vs.RANGE_LIMITED, 16.0),
        (vs.YUV420P8, False, vs.RANGE_FULL, 0.0),
        (vs.YUV420P8, True, vs.RANGE_FULL, 0.0),
        (vs.YUV420P10, False, vs.RANGE_LIMITED, 64.0),
        (vs.YUV420P10, True, vs.RANGE_LIMITED, 64.0),
        (vs.YUV420P10, False, vs.RANGE_FULL, 0.0),
        (vs.YUV420P10, True, vs.RANGE_FULL, 0.0),
        (vs.YUV420P16, False, vs.RANGE_LIMITED, 4096.0),
        (vs.YUV420P16, True, vs.RANGE_LIMITED, 4096.0),
        (vs.YUV420P16, False, vs.RANGE_FULL, 0.0),
        (vs.YUV420P16, True, vs.RANGE_FULL, 0.0),
    ],
)
def test_get_lowest_value_integer(
    fmt_id: vs.PresetVideoFormat, chroma: bool, range_in: vs.Range, expected: float
) -> None:
    fmt = core.get_video_format(fmt_id)
    assert get_lowest_value(fmt, chroma=chroma, range_in=range_in) == expected


@pytest.mark.parametrize(
    ("fmt_id", "chroma", "range_in", "expected"),
    [
        (vs.YUV420P8, False, vs.RANGE_LIMITED, 235.0),
        (vs.YUV420P8, True, vs.RANGE_LIMITED, 240.0),
        (vs.YUV420P8, False, vs.RANGE_FULL, 255.0),
        (vs.YUV420P8, True, vs.RANGE_FULL, 255.0),
        (vs.YUV420P10, False, vs.RANGE_LIMITED, 940.0),
        (vs.YUV420P10, True, vs.RANGE_LIMITED, 960.0),
        (vs.YUV420P10, False, vs.RANGE_FULL, 1023.0),
        (vs.YUV420P10, True, vs.RANGE_FULL, 1023.0),
        (vs.YUV420P16, False, vs.RANGE_LIMITED, 60160.0),
        (vs.YUV420P16, True, vs.RANGE_LIMITED, 61440.0),
        (vs.YUV420P16, False, vs.RANGE_FULL, 65535.0),
        (vs.YUV420P16, True, vs.RANGE_FULL, 65535.0),
    ],
)
def test_get_peak_value_integer(
    fmt_id: vs.PresetVideoFormat, chroma: bool, range_in: vs.Range, expected: float
) -> None:
    fmt = core.get_video_format(fmt_id)
    assert get_peak_value(fmt, chroma=chroma, range_in=range_in) == expected


@pytest.mark.parametrize(
    ("fmt_id", "chroma", "expected_lowest", "expected_peak"),
    [
        (vs.YUV420PS, False, 0.0, 1.0),
        (vs.YUV420PS, True, -0.5, 0.5),
        (vs.GRAYH, False, 0.0, 1.0),
        (vs.GRAYH, True, -0.5, 0.5),
    ],
)
def test_get_lowest_and_peak_value_float(
    fmt_id: vs.PresetVideoFormat, chroma: bool, expected_lowest: float, expected_peak: float
) -> None:
    fmt = core.get_video_format(fmt_id)
    assert get_lowest_value(fmt, chroma=chroma, range_in=vs.RANGE_LIMITED) == expected_lowest
    assert get_lowest_value(fmt, chroma=chroma, range_in=vs.RANGE_FULL) == expected_lowest
    assert get_peak_value(fmt, chroma=chroma, range_in=vs.RANGE_LIMITED) == expected_peak
    assert get_peak_value(fmt, chroma=chroma, range_in=vs.RANGE_FULL) == expected_peak


@pytest.mark.parametrize(
    ("fmt_id", "range_in", "expected_lowest", "expected_peak"),
    [
        (vs.RGB24, vs.RANGE_LIMITED, 16.0, 235.0),
        (vs.RGB24, vs.RANGE_FULL, 0.0, 255.0),
        (vs.RGB30, vs.RANGE_LIMITED, 64.0, 940.0),
        (vs.RGBS, vs.RANGE_LIMITED, 0.0, 1.0),
        (vs.RGBS, vs.RANGE_FULL, 0.0, 1.0),
    ],
)
def test_get_lowest_and_peak_value_rgb_overrides_chroma(
    fmt_id: vs.PresetVideoFormat, range_in: vs.Range, expected_lowest: float, expected_peak: float
) -> None:
    fmt = core.get_video_format(fmt_id)
    # Chroma=True must be overridden to False for RGB
    assert get_lowest_value(fmt, chroma=True, range_in=range_in) == expected_lowest
    assert get_peak_value(fmt, chroma=True, range_in=range_in) == expected_peak


@pytest.mark.parametrize(
    ("fmt_id", "value", "chroma", "expected"),
    [
        (vs.GRAYS, 0.42, False, 0.42),
        (vs.GRAYS, -0.25, True, -0.25),
        (vs.RGBS, 0.85, False, 0.85),
    ],
)
def test_scale_value_to_float_32bit_passthrough(
    fmt_id: vs.PresetVideoFormat, value: float, chroma: bool, expected: float
) -> None:
    clip = core.std.BlankClip(format=fmt_id, width=16, height=16, length=1)
    frame = clip.get_frame(0)

    assert scale_value_to_float(value, frame, chroma=chroma) == expected


def test_scale_value_to_float_16bit_float_passthrough() -> None:
    clip_16f = core.std.BlankClip(format=vs.GRAYS, width=16, height=16, length=1).resize.Point(format=vs.GRAYH)
    frame_16f = clip_16f.get_frame(0)

    assert scale_value_to_float(0.75, frame_16f) == 0.75
    assert scale_value_to_float(-0.5, frame_16f, chroma=True) == -0.5


@pytest.mark.parametrize(
    ("range_prop", "chroma", "value", "expected"),
    [
        # Default (no _Range prop -> Limited)
        (None, False, 16.0, 0.0),
        (None, False, 235.0, 1.0),
        (None, False, 125.5, 0.5),
        (None, True, 128.0, 0.0),
        (None, True, 240.0, 0.5),
        (None, True, 16.0, -0.5),
        # Explicit Limited
        (int(vs.RANGE_LIMITED), False, 16.0, 0.0),
        (int(vs.RANGE_LIMITED), False, 235.0, 1.0),
        (int(vs.RANGE_LIMITED), True, 128.0, 0.0),
        (int(vs.RANGE_LIMITED), True, 240.0, 0.5),
        # Explicit Full
        (int(vs.RANGE_FULL), False, 0.0, 0.0),
        (int(vs.RANGE_FULL), False, 255.0, 1.0),
        (int(vs.RANGE_FULL), True, 128.0, 0.0),
        (int(vs.RANGE_FULL), True, 255.0, (255.0 - 128.0) / 255.0),
        (int(vs.RANGE_FULL), True, 0.0, (0.0 - 128.0) / 255.0),
    ],
)
def test_scale_value_to_float_yuv_8bit(range_prop: int | None, chroma: bool, value: float, expected: float) -> None:
    clip = core.std.BlankClip(format=vs.YUV420P8, width=16, height=16, length=1)
    if range_prop is not None:
        frame = core.std.SetFrameProp(clip, prop="_Range", intval=range_prop).get_frame(0)
    else:
        frame = clip.get_frame(0)

    result = scale_value_to_float(value, frame, chroma=chroma)
    assert math.isclose(result, expected, abs_tol=1e-7)


@pytest.mark.parametrize(
    ("range_prop", "chroma", "value", "expected"),
    [
        # Default Limited
        (None, False, 64.0, 0.0),
        (None, False, 940.0, 1.0),
        (None, True, 512.0, 0.0),
        (None, True, 960.0, 0.5),
        (None, True, 64.0, -0.5),
        # Explicit Full
        (int(vs.RANGE_FULL), False, 0.0, 0.0),
        (int(vs.RANGE_FULL), False, 1023.0, 1.0),
        (int(vs.RANGE_FULL), True, 512.0, 0.0),
        (int(vs.RANGE_FULL), True, 1023.0, (1023.0 - 512.0) / 1023.0),
    ],
)
def test_scale_value_to_float_yuv_10bit(range_prop: int | None, chroma: bool, value: float, expected: float) -> None:
    clip = core.std.BlankClip(format=vs.YUV420P10, width=16, height=16, length=1)
    if range_prop is not None:
        frame = core.std.SetFrameProp(clip, prop="_Range", intval=range_prop).get_frame(0)
    else:
        frame = clip.get_frame(0)

    result = scale_value_to_float(value, frame, chroma=chroma)
    assert math.isclose(result, expected, abs_tol=1e-7)


@pytest.mark.parametrize(
    ("fmt_id", "range_prop", "chroma", "value", "expected"),
    [
        # RGB 8-bit default (Full)
        (vs.RGB24, None, False, 0.0, 0.0),
        (vs.RGB24, None, False, 255.0, 1.0),
        (vs.RGB24, None, True, 0.0, 0.0),
        (vs.RGB24, None, True, 255.0, 1.0),
        # RGB 8-bit explicit Limited
        (vs.RGB24, int(vs.RANGE_LIMITED), False, 16.0, 0.0),
        (vs.RGB24, int(vs.RANGE_LIMITED), False, 235.0, 1.0),
        (vs.RGB24, int(vs.RANGE_LIMITED), True, 16.0, 0.0),
        (vs.RGB24, int(vs.RANGE_LIMITED), True, 235.0, 1.0),
        # RGB 10-bit default (Full)
        (vs.RGB30, None, False, 0.0, 0.0),
        (vs.RGB30, None, False, 1023.0, 1.0),
        # RGB 10-bit explicit Limited
        (vs.RGB30, int(vs.RANGE_LIMITED), False, 64.0, 0.0),
        (vs.RGB30, int(vs.RANGE_LIMITED), False, 940.0, 1.0),
    ],
)
def test_scale_value_to_float_rgb_8bit_and_10bit(
    fmt_id: vs.PresetVideoFormat, range_prop: int | None, chroma: bool, value: float, expected: float
) -> None:
    clip = core.std.BlankClip(format=fmt_id, width=16, height=16, length=1)
    if range_prop is not None:
        frame = core.std.SetFrameProp(clip, prop="_Range", intval=range_prop).get_frame(0)
    else:
        frame = clip.get_frame(0)

    result = scale_value_to_float(value, frame, chroma=chroma)
    assert math.isclose(result, expected, abs_tol=1e-7)


def test_scale_value_to_float_invalid_range_raises() -> None:
    clip = core.std.BlankClip(format=vs.YUV420P8, width=16, height=16, length=1)
    frame = core.std.SetFrameProp(clip, prop="_Range", intval=999).get_frame(0)

    with pytest.raises(ValueError, match=r"is not a valid Range"):
        scale_value_to_float(128, frame)
