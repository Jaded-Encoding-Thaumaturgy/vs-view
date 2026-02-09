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
) -> None: ...
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
) -> None: ...
