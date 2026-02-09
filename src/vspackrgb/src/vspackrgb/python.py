"""Pure Python RGB packing (reference only, too slow for real-time)."""

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

    out = (ctypes.c_uint8 * (dest_stride * height)).from_address(dest_ptr)

    for y in range(height):
        src_row = y * src_stride
        dst_row = y * dest_stride

        for x in range(width):
            dst_offset = dst_row + x * 4
            out[dst_offset + 0] = b_data[src_row + x]
            out[dst_offset + 1] = g_data[src_row + x]
            out[dst_offset + 2] = r_data[src_row + x]
            out[dst_offset + 3] = a_data[src_row + x] if a_data is not None else 255


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

    out = (ctypes.c_uint32 * ((dest_stride // 4) * height)).from_address(dest_ptr)
    dest_samples_per_row = dest_stride // 4

    for y in range(height):
        src_row = y * samples_per_row
        dst_row = y * dest_samples_per_row

        for x in range(width):
            r = r_data[src_row + x]
            g = g_data[src_row + x]
            b = b_data[src_row + x]

            if a_data is not None:
                a = a_data[src_row + x] >> 8
                if a == 0:
                    r = g = b = 0
                elif a == 1:
                    r //= 3
                    g //= 3
                    b //= 3
                elif a == 2:
                    r = (r * 2) // 3
                    g = (g * 2) // 3
                    b = (b * 2) // 3
                out[dst_row + x] = (a << 30) | (r << 20) | (g << 10) | b
            else:
                out[dst_row + x] = 0xC0000000 | (r << 20) | (g << 10) | b
