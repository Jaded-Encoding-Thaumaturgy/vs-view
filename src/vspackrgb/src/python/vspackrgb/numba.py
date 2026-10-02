"""Numba-accelerated RGB packing functions."""

from __future__ import annotations

import ctypes

import numba
import numpy as np

from ._utils import cast


def pack_bgra_8bit(
    b_ptr: int,
    g_ptr: int,
    r_ptr: int,
    a_ptr: int | None,
    width: int,
    height: int,
    src_stride: int,
    dest_ptr: int,
    dest_stride: int,
) -> None:
    """Pack planar 8-bit RGB to interleaved BGRA with straight alpha using Numba."""
    b_arr = np.ctypeslib.as_array(cast(b_ptr, ctypes.c_uint8), shape=(height, src_stride))
    g_arr = np.ctypeslib.as_array(cast(g_ptr, ctypes.c_uint8), shape=(height, src_stride))
    r_arr = np.ctypeslib.as_array(cast(r_ptr, ctypes.c_uint8), shape=(height, src_stride))
    a_arr = (
        np.ctypeslib.as_array(cast(a_ptr, ctypes.c_uint8), shape=(height, src_stride)) if a_ptr is not None else None
    )

    ptr = cast(dest_ptr, ctypes.c_uint8)
    out_arr = np.ctypeslib.as_array(ptr, shape=(height, dest_stride))

    _pack_bgra_8bit_jit(b_arr, g_arr, r_arr, a_arr, out_arr, width, height)


def pack_rgb30_10bit(
    r_ptr: int,
    g_ptr: int,
    b_ptr: int,
    a_ptr: int | None,
    width: int,
    height: int,
    samples_per_row: int,
    dest_ptr: int,
    dest_stride: int,
) -> None:
    """Pack planar 10-bit RGB to A2R10G10B10 with premultiplied alpha using Numba."""
    r_arr = np.ctypeslib.as_array(cast(r_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
    g_arr = np.ctypeslib.as_array(cast(g_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
    b_arr = np.ctypeslib.as_array(cast(b_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
    a_arr = (
        np.ctypeslib.as_array(cast(a_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
        if a_ptr is not None
        else None
    )

    dest_samples_per_row = dest_stride // 4
    ptr = cast(dest_ptr, ctypes.c_uint32)
    out_arr = np.ctypeslib.as_array(ptr, shape=(height, dest_samples_per_row))

    _pack_rgb30_10bit_jit(r_arr, g_arr, b_arr, a_arr, out_arr, width, height)


def pack_rgba64_16bit(
    r_ptr: int,
    g_ptr: int,
    b_ptr: int,
    a_ptr: int | None,
    width: int,
    height: int,
    samples_per_row: int,
    dest_ptr: int,
    dest_stride: int,
) -> None:
    """Pack planar 16-bit RGB to interleaved RGBA64 using Numba."""
    r_arr = np.ctypeslib.as_array(cast(r_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
    g_arr = np.ctypeslib.as_array(cast(g_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
    b_arr = np.ctypeslib.as_array(cast(b_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
    a_arr = (
        np.ctypeslib.as_array(cast(a_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
        if a_ptr is not None
        else None
    )

    dest_samples_per_row = dest_stride // 2
    ptr = cast(dest_ptr, ctypes.c_uint16)
    out_arr = np.ctypeslib.as_array(ptr, shape=(height, dest_samples_per_row))

    _pack_rgba64_16bit_jit(r_arr, g_arr, b_arr, a_arr, out_arr, width, height)


def pack_rgba16f_16bit(
    r_ptr: int,
    g_ptr: int,
    b_ptr: int,
    a_ptr: int | None,
    width: int,
    height: int,
    samples_per_row: int,
    dest_ptr: int,
    dest_stride: int,
) -> None:
    """Pack planar 16-bit float RGB to interleaved float16 RGBA using Numba."""
    r_arr = np.ctypeslib.as_array(cast(r_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
    g_arr = np.ctypeslib.as_array(cast(g_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
    b_arr = np.ctypeslib.as_array(cast(b_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
    a_arr = (
        np.ctypeslib.as_array(cast(a_ptr, ctypes.c_uint16), shape=(height, samples_per_row))
        if a_ptr is not None
        else None
    )

    dest_samples_per_row = dest_stride // 2
    ptr = cast(dest_ptr, ctypes.c_uint16)
    out_arr = np.ctypeslib.as_array(ptr, shape=(height, dest_samples_per_row))

    _pack_rgba16f_16bit_jit(r_arr, g_arr, b_arr, a_arr, out_arr, width, height)


def pack_rgba32f_32bit(
    r_ptr: int,
    g_ptr: int,
    b_ptr: int,
    a_ptr: int | None,
    width: int,
    height: int,
    samples_per_row: int,
    dest_ptr: int,
    dest_stride: int,
) -> None:
    """Pack planar 32-bit float RGB to interleaved float32 RGBA using Numba."""
    r_arr = np.ctypeslib.as_array(cast(r_ptr, ctypes.c_uint32), shape=(height, samples_per_row))
    g_arr = np.ctypeslib.as_array(cast(g_ptr, ctypes.c_uint32), shape=(height, samples_per_row))
    b_arr = np.ctypeslib.as_array(cast(b_ptr, ctypes.c_uint32), shape=(height, samples_per_row))
    a_arr = (
        np.ctypeslib.as_array(cast(a_ptr, ctypes.c_uint32), shape=(height, samples_per_row))
        if a_ptr is not None
        else None
    )

    dest_samples_per_row = dest_stride // 4
    ptr = cast(dest_ptr, ctypes.c_uint32)
    out_arr = np.ctypeslib.as_array(ptr, shape=(height, dest_samples_per_row))

    _pack_rgba32f_32bit_jit(r_arr, g_arr, b_arr, a_arr, out_arr, width, height)


@numba.jit(nopython=True, cache=True, fastmath=True, nogil=True)
def _pack_bgra_8bit_jit(
    b_arr: np.ndarray[tuple[int, int], np.dtype[np.uint8]],
    g_arr: np.ndarray[tuple[int, int], np.dtype[np.uint8]],
    r_arr: np.ndarray[tuple[int, int], np.dtype[np.uint8]],
    a_arr: np.ndarray[tuple[int, int], np.dtype[np.uint8]] | None,
    out_arr: np.ndarray[tuple[int, int], np.dtype[np.uint8]],
    width: int,
    height: int,
) -> None:
    for y in range(height):
        for x in range(width):
            dst_idx = x * 4
            out_arr[y, dst_idx + 0] = b_arr[y, x]
            out_arr[y, dst_idx + 1] = g_arr[y, x]
            out_arr[y, dst_idx + 2] = r_arr[y, x]
            if a_arr is not None:
                out_arr[y, dst_idx + 3] = a_arr[y, x]
            else:
                out_arr[y, dst_idx + 3] = 255


@numba.jit(nopython=True, cache=True, fastmath=True, nogil=True)
def _pack_rgb30_10bit_jit(
    r_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    g_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    b_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    a_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]] | None,
    out_arr: np.ndarray[tuple[int, int], np.dtype[np.uint32]],
    width: int,
    height: int,
) -> None:
    for y in range(height):
        for x in range(width):
            r = r_arr[y, x]
            g = g_arr[y, x]
            b = b_arr[y, x]
            if a_arr is not None:
                a_bits = a_arr[y, x] >> 8
                if a_bits == 0:
                    r = 0
                    g = 0
                    b = 0
                elif a_bits == 1:
                    r = r // 3
                    g = g // 3
                    b = b // 3
                elif a_bits == 2:
                    r = (r * 2) // 3
                    g = (g * 2) // 3
                    b = (b * 2) // 3
                out_arr[y, x] = (a_bits << 30) | (r << 20) | (g << 10) | b
            else:
                out_arr[y, x] = 0xC0000000 | (r << 20) | (g << 10) | b


@numba.jit(nopython=True, cache=True, fastmath=True, nogil=True)
def _pack_rgba64_16bit_jit(
    r_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    g_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    b_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    a_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]] | None,
    out_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    width: int,
    height: int,
) -> None:
    for y in range(height):
        for x in range(width):
            dst_idx = x * 4
            out_arr[y, dst_idx + 0] = r_arr[y, x]
            out_arr[y, dst_idx + 1] = g_arr[y, x]
            out_arr[y, dst_idx + 2] = b_arr[y, x]
            if a_arr is not None:
                out_arr[y, dst_idx + 3] = a_arr[y, x]
            else:
                out_arr[y, dst_idx + 3] = 65535


@numba.jit(nopython=True, cache=True, fastmath=True, nogil=True)
def _pack_rgba16f_16bit_jit(
    r_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    g_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    b_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    a_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]] | None,
    out_arr: np.ndarray[tuple[int, int], np.dtype[np.uint16]],
    width: int,
    height: int,
) -> None:
    for y in range(height):
        for x in range(width):
            dst_idx = x * 4
            out_arr[y, dst_idx + 0] = r_arr[y, x]
            out_arr[y, dst_idx + 1] = g_arr[y, x]
            out_arr[y, dst_idx + 2] = b_arr[y, x]
            if a_arr is not None:
                out_arr[y, dst_idx + 3] = a_arr[y, x]
            else:
                out_arr[y, dst_idx + 3] = 0x3C00


@numba.jit(nopython=True, cache=True, fastmath=True, nogil=True)
def _pack_rgba32f_32bit_jit(
    r_arr: np.ndarray[tuple[int, int], np.dtype[np.uint32]],
    g_arr: np.ndarray[tuple[int, int], np.dtype[np.uint32]],
    b_arr: np.ndarray[tuple[int, int], np.dtype[np.uint32]],
    a_arr: np.ndarray[tuple[int, int], np.dtype[np.uint32]] | None,
    out_arr: np.ndarray[tuple[int, int], np.dtype[np.uint32]],
    width: int,
    height: int,
) -> None:
    for y in range(height):
        for x in range(width):
            dst_idx = x * 4
            out_arr[y, dst_idx + 0] = r_arr[y, x]
            out_arr[y, dst_idx + 1] = g_arr[y, x]
            out_arr[y, dst_idx + 2] = b_arr[y, x]
            if a_arr is not None:
                out_arr[y, dst_idx + 3] = a_arr[y, x]
            else:
                out_arr[y, dst_idx + 3] = 0x3F800000
