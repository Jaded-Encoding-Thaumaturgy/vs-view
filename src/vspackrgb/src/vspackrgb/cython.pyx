# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
"""Cython-accelerated RGB packing functions."""

from libc.stdint cimport uint8_t, uint16_t, uint32_t, uintptr_t


cpdef void pack_bgra_8bit(
    const uint8_t[::1] b_data,
    const uint8_t[::1] g_data,
    const uint8_t[::1] r_data,
    const uint8_t[::1] a_data,
    int width,
    int height,
    int src_stride,
    uintptr_t dest_ptr,
    int dest_stride,
):
    """Pack planar 8-bit RGB to interleaved BGRA with straight alpha."""

    cdef:
        uint8_t* out_base = <uint8_t*>dest_ptr
        uint8_t* out_row

        const uint8_t* b_ptr = &b_data[0]
        const uint8_t* g_ptr = &g_data[0]
        const uint8_t* r_ptr = &r_data[0]
        const uint8_t* a_ptr = &a_data[0] if a_data is not None else NULL

        int x, y
        int src_row_offset
        uint8_t alpha_val

    with nogil:
        for y in range(height):
            src_row_offset = y * src_stride
            out_row = out_base + y * dest_stride

            for x in range(width):
                alpha_val = a_ptr[src_row_offset + x] if a_ptr is not NULL else 255
                out_row[0] = b_ptr[src_row_offset + x]
                out_row[1] = g_ptr[src_row_offset + x]
                out_row[2] = r_ptr[src_row_offset + x]
                out_row[3] = alpha_val
                out_row += 4


cpdef void pack_rgb30_10bit(
    const uint16_t[::1] r_data,
    const uint16_t[::1] g_data,
    const uint16_t[::1] b_data,
    const uint16_t[::1] a_data,
    int width,
    int height,
    int samples_per_row,
    uintptr_t dest_ptr,
    int dest_stride,
):
    """Pack planar 10-bit RGB to A2R10G10B10 with premultiplied alpha."""

    cdef:
        uint8_t* out_base = <uint8_t*>dest_ptr
        uint32_t* out_row
        
        const uint16_t* r_ptr = &r_data[0]
        const uint16_t* g_ptr = &g_data[0]
        const uint16_t* b_ptr = &b_data[0]
        const uint16_t* a_ptr = &a_data[0] if a_data is not None else NULL
        
        int x, y, src_row_offset
        uint32_t r, g, b, a
        uint32_t alpha_mask = 0xC0000000
        
    with nogil:
        for y in range(height):
            src_row_offset = y * samples_per_row
            out_row = <uint32_t*>(out_base + y * dest_stride)
            
            for x in range(width):
                r = r_ptr[src_row_offset + x]
                g = g_ptr[src_row_offset + x]
                b = b_ptr[src_row_offset + x]
                
                if a_ptr is not NULL:
                    a = <uint32_t>(a_ptr[src_row_offset + x] >> 8)  # 10-bit to 2-bit
                    
                    # Premultiply
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
                    
                    out_row[x] = (a << 30) | (r << 20) | (g << 10) | b
                else:
                    out_row[x] = alpha_mask | (r << 20) | (g << 10) | b
