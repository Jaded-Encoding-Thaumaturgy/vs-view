"""NumPy-accelerated RGB packing functions."""

import ctypes

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
    """Pack planar 8-bit RGB to interleaved BGRA with straight alpha."""
    b_arr = np.ctypeslib.as_array(cast(b_ptr, ctypes.c_uint8), shape=(height, src_stride))[:, :width]
    g_arr = np.ctypeslib.as_array(cast(g_ptr, ctypes.c_uint8), shape=(height, src_stride))[:, :width]
    r_arr = np.ctypeslib.as_array(cast(r_ptr, ctypes.c_uint8), shape=(height, src_stride))[:, :width]

    bgra = np.empty((height, width, 4), dtype=np.uint8)
    bgra[:, :, 0] = b_arr
    bgra[:, :, 1] = g_arr
    bgra[:, :, 2] = r_arr

    if a_ptr is not None:
        bgra[:, :, 3] = np.ctypeslib.as_array(cast(a_ptr, ctypes.c_uint8), shape=(height, src_stride))[:, :width]
    else:
        bgra[:, :, 3] = 255  # Full alpha

    out_arr = np.ctypeslib.as_array(cast(dest_ptr, ctypes.c_uint8), shape=(height, dest_stride))

    row_bytes = width * 4
    out_arr[:, :row_bytes] = bgra.reshape((height, row_bytes))


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
    """Pack planar 10-bit RGB to A2R10G10B10 with premultiplied alpha."""
    r_arr = np.ctypeslib.as_array(cast(r_ptr, ctypes.c_uint16), shape=(height, samples_per_row))[:, :width]
    g_arr = np.ctypeslib.as_array(cast(g_ptr, ctypes.c_uint16), shape=(height, samples_per_row))[:, :width]
    b_arr = np.ctypeslib.as_array(cast(b_ptr, ctypes.c_uint16), shape=(height, samples_per_row))[:, :width]

    dest_samples_per_row = dest_stride // 4
    out_view = np.ctypeslib.as_array(cast(dest_ptr, ctypes.c_uint32), shape=(height, dest_samples_per_row))[:, :width]

    temp = np.empty((height, width), dtype=np.uint32)

    if a_ptr is not None:
        a_arr = np.ctypeslib.as_array(cast(a_ptr, ctypes.c_uint16), shape=(height, samples_per_row))[:, :width]
        a_bits = (a_arr >> 8).astype(np.uint32)  # 10-bit to 2-bit

        # R: (r * a) // 3 << 20, plus alpha bits
        np.multiply(r_arr, a_bits, out=temp, dtype=np.uint32)
        temp //= 3
        np.left_shift(temp, 20, out=temp)
        np.left_shift(a_bits, 30, out=out_view)
        temp |= out_view

        # G: (g * a) // 3 << 10, use out_view as scratch
        np.multiply(g_arr, a_bits, out=out_view, dtype=np.uint32)
        out_view //= 3
        np.left_shift(out_view, 10, out=out_view)
        temp |= out_view

        # B: (b * a) // 3, final combine
        np.multiply(b_arr, a_bits, out=out_view, dtype=np.uint32)
        out_view //= 3
        np.add(temp, out_view, out=out_view)
    else:
        # R << 20 into temp, then add alpha mask
        np.left_shift(r_arr, 20, out=temp, dtype=np.uint32)
        temp |= 0xC0000000

        # G << 10, add to temp
        np.left_shift(g_arr, 10, out=out_view, dtype=np.uint32)
        temp |= out_view

        # B (just cast) and final OR into output
        np.add(temp, b_arr, out=out_view, dtype=np.uint32)


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
    """Pack planar 16-bit RGB to interleaved RGBA64."""
    r_arr = np.ctypeslib.as_array(cast(r_ptr, ctypes.c_uint16), shape=(height, samples_per_row))[:, :width]
    g_arr = np.ctypeslib.as_array(cast(g_ptr, ctypes.c_uint16), shape=(height, samples_per_row))[:, :width]
    b_arr = np.ctypeslib.as_array(cast(b_ptr, ctypes.c_uint16), shape=(height, samples_per_row))[:, :width]

    # RGBA64 is 16-bit per channel: R16 G16 B16 A16
    rgba64 = np.empty((height, width, 4), dtype=np.uint16)
    rgba64[:, :, 0] = r_arr
    rgba64[:, :, 1] = g_arr
    rgba64[:, :, 2] = b_arr

    if a_ptr is not None:
        spr = samples_per_row
        rgba64[:, :, 3] = np.ctypeslib.as_array(cast(a_ptr, ctypes.c_uint16), shape=(height, spr))[:, :width]
    else:
        rgba64[:, :, 3] = 65535  # Full alpha

    out_arr = np.ctypeslib.as_array(cast(dest_ptr, ctypes.c_uint16), shape=(height, dest_stride // 2))

    row_samples = width * 4
    out_arr[:, :row_samples] = rgba64.reshape((height, row_samples))


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
    """Pack planar 16-bit RGB to interleaved float16 RGBA."""
    shape = (height, samples_per_row)
    r_arr = np.ctypeslib.as_array(cast(r_ptr, ctypes.c_uint16), shape=shape)[:, :width].view(np.float16)
    g_arr = np.ctypeslib.as_array(cast(g_ptr, ctypes.c_uint16), shape=shape)[:, :width].view(np.float16)
    b_arr = np.ctypeslib.as_array(cast(b_ptr, ctypes.c_uint16), shape=shape)[:, :width].view(np.float16)

    rgba16f = np.empty((height, width, 4), dtype=np.float16)
    rgba16f[:, :, 0] = r_arr
    rgba16f[:, :, 1] = g_arr
    rgba16f[:, :, 2] = b_arr

    if a_ptr is not None:
        rgba16f[:, :, 3] = np.ctypeslib.as_array(cast(a_ptr, ctypes.c_uint16), shape=shape)[:, :width].view(np.float16)
    else:
        rgba16f[:, :, 3] = 1.0

    out_arr = np.ctypeslib.as_array(cast(dest_ptr, ctypes.c_uint16), shape=(height, dest_stride // 2))

    row_samples = width * 4
    out_arr[:, :row_samples] = rgba16f.view(np.uint16).reshape((height, row_samples))


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
    """Pack planar 32-bit RGB to interleaved float32 RGBA."""
    shape = (height, samples_per_row)
    r_arr = np.ctypeslib.as_array(cast(r_ptr, ctypes.c_float), shape=shape)[:, :width]
    g_arr = np.ctypeslib.as_array(cast(g_ptr, ctypes.c_float), shape=shape)[:, :width]
    b_arr = np.ctypeslib.as_array(cast(b_ptr, ctypes.c_float), shape=shape)[:, :width]

    rgba32f = np.empty((height, width, 4), dtype=np.float32)
    rgba32f[:, :, 0] = r_arr
    rgba32f[:, :, 1] = g_arr
    rgba32f[:, :, 2] = b_arr

    if a_ptr is not None:
        rgba32f[:, :, 3] = np.ctypeslib.as_array(cast(a_ptr, ctypes.c_float), shape=shape)[:, :width]
    else:
        rgba32f[:, :, 3] = 1.0

    out_arr = np.ctypeslib.as_array(cast(dest_ptr, ctypes.c_uint32), shape=(height, dest_stride // 4))

    row_samples = width * 4
    out_arr[:, :row_samples] = rgba32f.view(np.uint32).reshape((height, row_samples))
