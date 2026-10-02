"""VapourSynth frame packing helpers."""

import ctypes
from collections.abc import Callable
from types import ModuleType
from typing import Literal, Protocol, assert_never, overload

import vapoursynth as vs

core = vs.core


def packrgb[V: (vs.VideoNode, vs.VideoFrame)](
    clip: V,
    alpha: V | Literal[True] | None = None,
    backend: Literal["rust", "numba", "numpy", "python"] = "rust",
) -> V:
    """
    Pack a planar RGB clip or frame into a display-ready format.

    Converts:

    - RGB24 -> interleaved BGRA32 (with straight alpha)
    - RGB30 -> packed A2R10G10B10 (with premultiplied alpha)
    - RGB48 -> interleaved RGBA64 (with straight alpha) stored in a 4x wider GRAY16 clip/frame.
    - RGBH  -> interleaved RGBA16F (with straight alpha) stored in a 4x wider GRAYH clip/frame.
    - RGBS  -> interleaved RGBA32F (with straight alpha) stored in a 4x wider GRAYS clip/frame.

    Args:
        clip: Input clip or frame in RGB24, RGB30, RGB48, RGBH or RGBS format.
        alpha: Optional alpha channel clip/frame or if True, fetch the `_Alpha` prop.
        backend: Packing backend ("rust", "numba", "numpy", "python").

    Returns:
        GRAY32, GRAY16, GRAYH, GRAYS clip or frame with packed pixel data.

    Raises:
        ValueError: If format or backend is unsupported or resolution is variable.
    """

    module: ModuleType

    match backend:
        case "rust" | "cython":
            from . import rust as module
        case "numpy":
            from . import numpy as module
        case "python":
            from . import python as module
        case "numba":
            from . import numba as module
        case _:
            assert_never(backend)

    width, height = clip.width, clip.height

    match clip.format.id:
        case vs.RGB24:
            out_format = vs.GRAY32
            pack_fn = _make_pack_frame_8bit(module.pack_bgra_8bit)
        case vs.RGB30:
            out_format = vs.GRAY32
            pack_fn = _make_pack_frame_10bit(module.pack_rgb30_10bit)
        case vs.RGB48:
            width *= 4
            out_format = vs.GRAY16
            pack_fn = _make_pack_frame_16bit(module.pack_rgba64_16bit)
        case vs.RGBH:
            width *= 4
            out_format = vs.GRAYH
            pack_fn = _make_pack_frame_16f(module.pack_rgba16f_16bit)
        case vs.RGBS:
            width *= 4
            out_format = vs.GRAYS
            pack_fn = _make_pack_frame_32f(module.pack_rgba32f_32bit)
        case _:
            raise ValueError(f"Unsupported input format: {clip.format.name}")

    match clip, alpha:
        case vs.VideoNode(), vs.VideoNode() | True | None:
            if 0 in [clip.width, clip.height]:
                raise ValueError("Variable resolution clips are not supported")

            blank = clip.std.BlankClip(width=width, height=height, format=out_format, keep=True)

            if alpha is True:
                alpha = clip.std.PropToClip("_Alpha")
                clip = clip.std.RemoveFrameProps("_Alpha")

            if alpha and alpha.format != (afmt := clip.format.replace(color_family=vs.GRAY)):
                raise ValueError(f"Alpha bit depth must be {afmt!r}")

            return blank.std.ModifyFrame(clip if not alpha else [clip, alpha], pack_fn)

        case vs.VideoFrame(), vs.VideoFrame() | True | None:
            if alpha is True:
                alpha = clip.props["_Alpha"]

            if alpha and alpha.format != (afmt := clip.format.replace(color_family=vs.GRAY)):
                raise ValueError(f"Alpha bit depth must be {afmt!r}")

            out_frame = pack_fn(n=0, f=[clip, alpha] if alpha else clip)

            if alpha:
                out_frame.props.pop("_Alpha", None)

            return out_frame  # pyright: ignore[reportReturnType]

        case _:
            raise TypeError("Mismatched types for clip and alpha")


class _ModifyFrameFunction(Protocol):
    def __call__(self, *, n: int, f: vs.VideoFrame | list[vs.VideoFrame]) -> vs.VideoFrame: ...


def _make_pack_frame_8bit(pack_bgra_8bit: Callable[..., None]) -> _ModifyFrameFunction:
    def _pack_frame(n: int, f: vs.VideoFrame | list[vs.VideoFrame]) -> vs.VideoFrame:
        frame_src, frame_alpha = (f, None) if isinstance(f, vs.VideoFrame) else f

        frame_dst = core.create_video_frame(vs.GRAY32, frame_src.width, frame_src.height)

        width, height = frame_src.width, frame_src.height
        src_stride = frame_src.get_stride(0)
        dst_stride = frame_dst.get_stride(0)
        dst_ptr = frame_dst.get_write_ptr(0).value

        if dst_ptr is None:
            raise ValueError("Destination frame pointer is NULL")

        b_ptr = frame_src.get_read_ptr(2).value
        g_ptr = frame_src.get_read_ptr(1).value
        r_ptr = frame_src.get_read_ptr(0).value
        a_ptr = frame_alpha.get_read_ptr(0).value if frame_alpha is not None else None

        if b_ptr is None or g_ptr is None or r_ptr is None:
            raise ValueError("Source frame pointer is NULL")

        pack_bgra_8bit(b_ptr, g_ptr, r_ptr, a_ptr, width, height, src_stride, dst_ptr, dst_stride)

        frame_dst.props.update(frame_src.props)

        return frame_dst

    return _pack_frame


def _make_pack_frame_10bit(pack_rgb30_10bit: Callable[..., None]) -> _ModifyFrameFunction:
    def _pack_frame(n: int, f: vs.VideoFrame | list[vs.VideoFrame]) -> vs.VideoFrame:
        frame_src, frame_alpha = (f, None) if isinstance(f, vs.VideoFrame) else f

        frame_dst = core.create_video_frame(vs.GRAY32, frame_src.width, frame_src.height)

        width, height = frame_src.width, frame_src.height
        src_stride = frame_src.get_stride(0)
        samples_per_row = src_stride // 2
        dst_stride = frame_dst.get_stride(0)
        dst_ptr = frame_dst.get_write_ptr(0).value

        if dst_ptr is None:
            raise ValueError("Destination frame pointer is NULL")

        r_ptr = frame_src.get_read_ptr(0).value
        g_ptr = frame_src.get_read_ptr(1).value
        b_ptr = frame_src.get_read_ptr(2).value
        a_ptr = frame_alpha.get_read_ptr(0).value if frame_alpha is not None else None

        if r_ptr is None or g_ptr is None or b_ptr is None:
            raise ValueError("Source frame pointer is NULL")

        pack_rgb30_10bit(r_ptr, g_ptr, b_ptr, a_ptr, width, height, samples_per_row, dst_ptr, dst_stride)

        frame_dst.props.update(frame_src.props)

        return frame_dst

    return _pack_frame


def _make_pack_frame_16bit(pack_rgba64_16bit: Callable[..., None]) -> _ModifyFrameFunction:
    def _pack_frame(n: int, f: vs.VideoFrame | list[vs.VideoFrame]) -> vs.VideoFrame:
        frame_src, frame_alpha = (f, None) if isinstance(f, vs.VideoFrame) else f

        frame_dst = core.create_video_frame(vs.GRAY16, frame_src.width * 4, frame_src.height)

        width, height = frame_src.width, frame_src.height
        src_stride = frame_src.get_stride(0)
        samples_per_row = src_stride // 2
        dst_stride = frame_dst.get_stride(0)
        dst_ptr = frame_dst.get_write_ptr(0).value

        if dst_ptr is None:
            raise ValueError("Destination frame pointer is NULL")

        r_ptr = frame_src.get_read_ptr(0).value
        g_ptr = frame_src.get_read_ptr(1).value
        b_ptr = frame_src.get_read_ptr(2).value
        a_ptr = frame_alpha.get_read_ptr(0).value if frame_alpha is not None else None

        if r_ptr is None or g_ptr is None or b_ptr is None:
            raise ValueError("Source frame pointer is NULL")

        pack_rgba64_16bit(r_ptr, g_ptr, b_ptr, a_ptr, width, height, samples_per_row, dst_ptr, dst_stride)

        frame_dst.props.update(frame_src.props)
        frame_dst.props["VSViewPacked16"] = 1
        return frame_dst

    return _pack_frame


def _make_pack_frame_16f(pack_rgba16f_16bit: Callable[..., None]) -> _ModifyFrameFunction:
    def _pack_frame(n: int, f: vs.VideoFrame | list[vs.VideoFrame]) -> vs.VideoFrame:
        frame_src, frame_alpha = (f, None) if isinstance(f, vs.VideoFrame) else f

        frame_dst = core.create_video_frame(vs.GRAYH, frame_src.width * 4, frame_src.height)

        width, height = frame_src.width, frame_src.height
        src_stride = frame_src.get_stride(0)
        samples_per_row = src_stride // 2
        dst_stride = frame_dst.get_stride(0)
        dst_ptr = frame_dst.get_write_ptr(0).value

        if dst_ptr is None:
            raise ValueError("Destination frame pointer is NULL")

        r_ptr = frame_src.get_read_ptr(0).value
        g_ptr = frame_src.get_read_ptr(1).value
        b_ptr = frame_src.get_read_ptr(2).value
        a_ptr = frame_alpha.get_read_ptr(0).value if frame_alpha is not None else None

        if r_ptr is None or g_ptr is None or b_ptr is None:
            raise ValueError("Source frame pointer is NULL")

        pack_rgba16f_16bit(r_ptr, g_ptr, b_ptr, a_ptr, width, height, samples_per_row, dst_ptr, dst_stride)

        frame_dst.props.update(frame_src.props)
        frame_dst.props["VSViewPacked16F"] = 1
        return frame_dst

    return _pack_frame


def _make_pack_frame_32f(pack_rgba32f_32bit: Callable[..., None]) -> _ModifyFrameFunction:
    def _pack_frame(n: int, f: vs.VideoFrame | list[vs.VideoFrame]) -> vs.VideoFrame:
        frame_src, frame_alpha = (f, None) if isinstance(f, vs.VideoFrame) else f

        frame_dst = core.create_video_frame(vs.GRAYS, frame_src.width * 4, frame_src.height)

        width, height = frame_src.width, frame_src.height
        src_stride = frame_src.get_stride(0)
        samples_per_row = src_stride // 4
        dst_stride = frame_dst.get_stride(0)
        dst_ptr = frame_dst.get_write_ptr(0).value

        if dst_ptr is None:
            raise ValueError("Destination frame pointer is NULL")

        r_ptr = frame_src.get_read_ptr(0).value
        g_ptr = frame_src.get_read_ptr(1).value
        b_ptr = frame_src.get_read_ptr(2).value
        a_ptr = frame_alpha.get_read_ptr(0).value if frame_alpha is not None else None

        if r_ptr is None or g_ptr is None or b_ptr is None:
            raise ValueError("Source frame pointer is NULL")

        pack_rgba32f_32bit(r_ptr, g_ptr, b_ptr, a_ptr, width, height, samples_per_row, dst_ptr, dst_stride)

        frame_dst.props.update(frame_src.props)
        frame_dst.props["VSViewPacked32F"] = 1
        return frame_dst

    return _pack_frame


@overload
def get_plane_buffer(
    frame: vs.VideoFrame, plane: int, bytes_per_sample: Literal[1] = 1
) -> ctypes.Array[ctypes.c_uint8]: ...


@overload
def get_plane_buffer(
    frame: vs.VideoFrame, plane: int, bytes_per_sample: Literal[2]
) -> ctypes.Array[ctypes.c_uint16]: ...


@overload
def get_plane_buffer(
    frame: vs.VideoFrame, plane: int, bytes_per_sample: Literal[4]
) -> ctypes.Array[ctypes.c_uint32]: ...


def get_plane_buffer(
    frame: vs.VideoFrame, plane: int, bytes_per_sample: int = 1
) -> ctypes.Array[ctypes.c_uint8] | ctypes.Array[ctypes.c_uint16] | ctypes.Array[ctypes.c_uint32]:
    """
    Get a ctypes array from a VideoFrame plane.

    Args:
        frame: VideoFrame to read from.
        plane: Plane index (0=R, 1=G, 2=B for RGB).
        bytes_per_sample: 1 for 8-bit, 2 for 10/16-bit, 4 for 32-bit

    Returns:
        ctypes array of the plane's pixel data.

    Raises:
        ValueError: If pointer is NULL or bytes_per_sample invalid.
    """
    stride = frame.get_stride(plane)
    height = frame.height
    ptr = frame.get_read_ptr(plane)

    if (ptr_val := ptr.value) is None:
        raise ValueError(f"Plane {plane} pointer is NULL")

    buf_size = stride * height

    match bytes_per_sample:
        case 1:
            c_buffer = (ctypes.c_uint8 * buf_size).from_address(ptr_val)
        case 2:
            c_buffer = (ctypes.c_uint16 * (buf_size // 2)).from_address(ptr_val)
        case 4:
            c_buffer = (ctypes.c_uint32 * (buf_size // 4)).from_address(ptr_val)
        case _:
            raise ValueError(f"Unsupported bytes_per_sample: {bytes_per_sample}")

    return c_buffer
