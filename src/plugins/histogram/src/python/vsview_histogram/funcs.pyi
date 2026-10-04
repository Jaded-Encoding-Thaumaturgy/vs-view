from collections.abc import Sequence

type PlaneDescriptor = tuple[int, int]

def compute_luma(
    src: PlaneDescriptor,
    dst: PlaneDescriptor,
    width: int,
    height: int,
    bits: int,
    sample_type: int,
    shift_in: int,
    use_sawtooth: bool,
    is_limited: bool,
) -> None: ...
def compute_waveform(
    src: PlaneDescriptor,
    dst: PlaneDescriptor,
    width: int,
    height: int,
    bits: int,
    sample_type: int,
    target_w: int,
    target_h: int,
    is_chroma: bool,
    is_limited: bool,
    gain: float,
    dynamic_gain: bool,
) -> None: ...
def compute_vectorscope(
    src_yuv: Sequence[PlaneDescriptor],
    dst: PlaneDescriptor,
    width: int,
    height: int,
    bits: int,
    sample_type: int,
    subsampling_w: int,
    subsampling_h: int,
    is_limited: bool,
    matrix_coeffs: Sequence[float],
    canvas_size: int,
    render_mode: int,
    luma_scale: float,
) -> None: ...
def compute_cie(
    src_xyz: Sequence[PlaneDescriptor],
    dst: PlaneDescriptor,
    width: int,
    height: int,
    canvas_size: int,
    cie_mode: int,
    render_mode: int,
    luma_scale: float,
    linear_rgb: Sequence[PlaneDescriptor] | None = None,
) -> None: ...
