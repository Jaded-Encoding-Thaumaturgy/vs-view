"""Ported back from vstools functions"""

import vapoursynth as vs


def get_chroma_offsets(frame: vs.VideoFrame) -> tuple[float, float]:
    off_top, off_left = 0.0, 0.0
    chromaloc = frame.props.get("_ChromaLocation", 0)

    if chromaloc in [vs.CHROMA_TOP_LEFT, vs.CHROMA_TOP]:
        off_top = 2 ** (frame.format.subsampling_h - 1) - 0.5

    if chromaloc in [vs.CHROMA_LEFT, vs.CHROMA_TOP_LEFT, vs.CHROMA_BOTTOM_LEFT]:
        off_left = 2 ** (frame.format.subsampling_w - 1) - 0.5

    return off_top, off_left


def get_lowest_value(fmt: vs.VideoFormat, chroma: bool, range_in: vs.ColorRange) -> float:
    if fmt.color_family == vs.RGB:
        chroma = False

    if fmt.sample_type is vs.FLOAT:
        return -0.5 if chroma else 0.0

    if range_in == vs.ColorRange.RANGE_LIMITED:
        return 16 << fmt.bits_per_sample - 8

    return 0


def get_peak_value(fmt: vs.VideoFormat, chroma: bool, range_in: vs.ColorRange) -> float:
    if fmt.color_family == vs.RGB:
        chroma = False

    if fmt.sample_type is vs.FLOAT:
        return 0.5 if chroma else 1.0

    if range_in == vs.ColorRange.RANGE_LIMITED:
        return (240 if chroma else 235) << fmt.bits_per_sample - 8

    return (1 << fmt.bits_per_sample) - 1


def scale_value_to_float(value: float, input_frame: vs.VideoFrame, chroma: bool = False) -> float:
    out_value = float(value)

    in_fmt = input_frame.format
    out_fmt = in_fmt.replace(sample_type=vs.FLOAT, bits_per_sample=32)

    prop = input_frame.props.get("_ColorRange")

    if prop is not None:
        range_in = vs.ColorRange(prop)  # type: ignore[arg-type]
    elif in_fmt.color_family == vs.RGB:
        range_in = vs.ColorRange.RANGE_FULL
    else:
        range_in = vs.ColorRange.RANGE_LIMITED

    if input_frame.format.bits_per_sample == 32 or (
        input_frame.format.bits_per_sample == 16 and input_frame.format.sample_type == vs.FLOAT
    ):
        return out_value

    if in_fmt.color_family == vs.RGB:
        chroma = False

    input_peak = get_peak_value(in_fmt, chroma, range_in)
    input_lowest = get_lowest_value(in_fmt, chroma, range_in)
    output_peak = get_peak_value(out_fmt, chroma, range_in)
    output_lowest = get_lowest_value(out_fmt, chroma, range_in)

    if in_fmt.sample_type is vs.INTEGER:
        if chroma:
            out_value -= 128 << (in_fmt.bits_per_sample - 8)
        elif range_in == vs.ColorRange.RANGE_LIMITED:
            out_value -= 16 << (in_fmt.bits_per_sample - 8)

    out_value *= (output_peak - output_lowest) / (input_peak - input_lowest)

    return out_value
