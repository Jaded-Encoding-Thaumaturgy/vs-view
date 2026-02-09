"""VapourSynth frame packing helpers."""

import ctypes
from collections.abc import Callable
from typing import Literal, Protocol, overload

import vapoursynth as vs

from . import cython, numpy, python


def packrgb(
    clip: vs.VideoNode,
    alpha: vs.VideoNode | None = None,
    backend: Literal["cython", "numpy", "python"] = "cython",
) -> vs.VideoNode:
    """
    Pack a planar RGB clip into a display-ready format.

    Converts RGB24 to interleaved BGRA32 (with straight alpha)
    or RGB30 to packed A2R10G10B10 (with premultiplied alpha), stored in a GRAY32 clip.

    Args:
        clip: Input clip in RGB24 or RGB30 format.
        alpha: Optional alpha channel clip (GRAY8 for RGB24, GRAY10 for RGB30).
        backend: Packing backend ("cython", "numpy", "python"). "python" is *very* slow.

    Returns:
        GRAY32 clip with packed pixel data.

    Raises:
        ValueError: If format or backend is unsupported or resolution is variable.
    """
    if 0 in [clip.width, clip.height]:
        raise ValueError("Variable resolution clips are not supported")

    match clip.format.id, alpha.format.id if alpha else None, backend:
        case vs.RGB24, vs.GRAY8 | None, "cython":
            pack_fn = _make_pack_frame_8bit(cython.pack_bgra_8bit)
        case vs.RGB24, vs.GRAY8 | None, "numpy":
            pack_fn = _make_pack_frame_8bit(numpy.pack_bgra_8bit)
        case vs.RGB24, vs.GRAY8 | None, "python":
            pack_fn = _make_pack_frame_8bit(python.pack_bgra_8bit)
        case vs.RGB30, vs.GRAY10 | None, "cython":
            pack_fn = _make_pack_frame_10bit(cython.pack_rgb30_10bit)
        case vs.RGB30, vs.GRAY10 | None, "numpy":
            pack_fn = _make_pack_frame_10bit(numpy.pack_rgb30_10bit)
        case vs.RGB30, vs.GRAY10 | None, "python":
            pack_fn = _make_pack_frame_10bit(python.pack_rgb30_10bit)
        case _:
            raise ValueError("Unsupported input format or backend")

    blank = clip.std.BlankClip(format=vs.GRAY32, keep=True)
    clips = [clip, blank]

    if alpha is not None:
        clips.append(alpha)

    return blank.std.ModifyFrame(clips, pack_fn)


class _ModifyFrameFunction(Protocol):
    def __call__(self, *, n: int, f: list[vs.VideoFrame]) -> vs.VideoFrame: ...


def _make_pack_frame_8bit(pack_bgra_8bit: Callable[..., None]) -> _ModifyFrameFunction:
    def _pack_frame(n: int, f: list[vs.VideoFrame]) -> vs.VideoFrame:
        frame_src, frame_dst = f[0], f[1].copy()
        frame_alpha = f[2] if len(f) > 2 else None

        width, height = frame_src.width, frame_src.height
        src_stride = frame_src.get_stride(0)
        dst_stride = frame_dst.get_stride(0)
        dst_ptr = frame_dst.get_write_ptr(0).value

        if dst_ptr is None:
            raise ValueError("Destination frame pointer is NULL")

        b_plane = get_plane_buffer(frame_src, 2)
        g_plane = get_plane_buffer(frame_src, 1)
        r_plane = get_plane_buffer(frame_src, 0)
        a_plane = get_plane_buffer(frame_alpha, 0) if frame_alpha is not None else None

        pack_bgra_8bit(b_plane, g_plane, r_plane, a_plane, width, height, src_stride, dst_ptr, dst_stride)

        return frame_dst

    return _pack_frame


def _make_pack_frame_10bit(pack_rgb30_10bit: Callable[..., None]) -> _ModifyFrameFunction:
    def _pack_frame(n: int, f: list[vs.VideoFrame]) -> vs.VideoFrame:
        frame_src, frame_dst = f[0], f[1].copy()
        frame_alpha = f[2] if len(f) > 2 else None

        width, height = frame_src.width, frame_src.height
        src_stride = frame_src.get_stride(0)
        samples_per_row = src_stride // 2
        dst_stride = frame_dst.get_stride(0)
        dst_ptr = frame_dst.get_write_ptr(0).value

        if dst_ptr is None:
            raise ValueError("Destination frame pointer is NULL")

        r_plane = get_plane_buffer(frame_src, 0, bytes_per_sample=2)
        g_plane = get_plane_buffer(frame_src, 1, bytes_per_sample=2)
        b_plane = get_plane_buffer(frame_src, 2, bytes_per_sample=2)
        a_plane = get_plane_buffer(frame_alpha, 0, bytes_per_sample=2) if frame_alpha is not None else None

        pack_rgb30_10bit(r_plane, g_plane, b_plane, a_plane, width, height, samples_per_row, dst_ptr, dst_stride)

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


def get_plane_buffer(
    frame: vs.VideoFrame, plane: int, bytes_per_sample: int = 1
) -> ctypes.Array[ctypes.c_uint8] | ctypes.Array[ctypes.c_uint16]:
    """
    Get a ctypes array from a VideoFrame plane.

    Args:
        frame: VideoFrame to read from.
        plane: Plane index (0=R, 1=G, 2=B for RGB).
        bytes_per_sample: 1 for 8-bit, 2 for 10/16-bit.

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

    if bytes_per_sample == 1:
        c_buffer = (ctypes.c_uint8 * buf_size).from_address(ptr_val)
    elif bytes_per_sample == 2:
        c_buffer = (ctypes.c_uint16 * (buf_size // 2)).from_address(ptr_val)
    else:
        raise ValueError(f"Unsupported bytes_per_sample: {bytes_per_sample}")

    return c_buffer
