"""NumPy-accelerated RGB packing functions."""

import ctypes


def pack_bgra_8bit(
    b_data: ctypes.Array[ctypes.c_uint8],
    g_data: ctypes.Array[ctypes.c_uint8],
    r_data: ctypes.Array[ctypes.c_uint8],
    a_data: ctypes.Array[ctypes.c_uint8] | None,
    width: int,
    height: int,
    src_stride: int,
    dest_ptr: int,
    dest_stride: int,
) -> None:
    """Pack planar 8-bit RGB to interleaved BGRA with straight alpha."""
    import numpy as np

    b_arr = np.frombuffer(b_data, dtype=np.uint8).reshape((height, src_stride))[:, :width]
    g_arr = np.frombuffer(g_data, dtype=np.uint8).reshape((height, src_stride))[:, :width]
    r_arr = np.frombuffer(r_data, dtype=np.uint8).reshape((height, src_stride))[:, :width]

    bgra = np.empty((height, width, 4), dtype=np.uint8)
    bgra[:, :, 0] = b_arr
    bgra[:, :, 1] = g_arr
    bgra[:, :, 2] = r_arr

    if a_data is not None:
        bgra[:, :, 3] = np.frombuffer(a_data, dtype=np.uint8).reshape((height, src_stride))[:, :width]
    else:
        bgra[:, :, 3] = 255  # Full alpha

    out = (ctypes.c_uint8 * (dest_stride * height)).from_address(dest_ptr)
    out_arr = np.frombuffer(out, dtype=np.uint8).reshape((height, dest_stride))

    row_bytes = width * 4
    out_arr[:, :row_bytes] = bgra.reshape((height, row_bytes))


def pack_rgb30_10bit(
    r_data: ctypes.Array[ctypes.c_uint16],
    g_data: ctypes.Array[ctypes.c_uint16],
    b_data: ctypes.Array[ctypes.c_uint16],
    a_data: ctypes.Array[ctypes.c_uint16] | None,
    width: int,
    height: int,
    samples_per_row: int,
    dest_ptr: int,
    dest_stride: int,
) -> None:
    """Pack planar 10-bit RGB to A2R10G10B10 with premultiplied alpha."""
    import numpy as np

    r_arr = np.frombuffer(r_data, dtype=np.uint16).reshape((height, samples_per_row))[:, :width]
    g_arr = np.frombuffer(g_data, dtype=np.uint16).reshape((height, samples_per_row))[:, :width]
    b_arr = np.frombuffer(b_data, dtype=np.uint16).reshape((height, samples_per_row))[:, :width]

    dest_samples_per_row = dest_stride // 4
    out = (ctypes.c_uint32 * (dest_samples_per_row * height)).from_address(dest_ptr)
    out_arr = np.frombuffer(out, dtype=np.uint32).reshape((height, dest_samples_per_row))
    out_view = out_arr[:, :width]

    temp = np.empty((height, width), dtype=np.uint32)

    if a_data is not None:
        a_arr = np.frombuffer(a_data, dtype=np.uint16).reshape((height, samples_per_row))[:, :width]
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
