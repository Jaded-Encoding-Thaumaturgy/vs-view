import ctypes
from typing import Any, Protocol, cast

import pytest
import vapoursynth as vs

from vspackrgb import cython, helpers, numpy, python


class BackendModule(Protocol):
    def pack_bgra_8bit(
        self,
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
        self,
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


BACKENDS = ["python", "numpy", "cython"]


def get_backend_module(backend_name: str) -> BackendModule:
    match backend_name:
        case "python":
            return cast(BackendModule, python)
        case "numpy":
            return cast(BackendModule, numpy)
        case "cython":
            return cast(BackendModule, cython)
        case _:
            raise ValueError(f"Unknown backend: {backend_name}")


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_pack_bgra_8bit(backend_name: str) -> None:
    backend = get_backend_module(backend_name)
    width, height = 4, 4
    src_stride = width
    dest_stride = width * 4

    b = (ctypes.c_uint8 * (width * height))(*range(width * height))
    g = (ctypes.c_uint8 * (width * height))(*(x + 10 for x in range(width * height)))
    r = (ctypes.c_uint8 * (width * height))(*(x + 20 for x in range(width * height)))
    a = (ctypes.c_uint8 * (width * height))(*(x + 30 for x in range(width * height)))

    dest = (ctypes.c_uint8 * (dest_stride * height))()
    dest_ptr = ctypes.addressof(dest)

    backend.pack_bgra_8bit(b, g, r, a, width, height, src_stride, dest_ptr, dest_stride)

    for y in range(height):
        for x in range(width):
            idx = y * width + x
            out_idx = y * dest_stride + x * 4
            assert dest[out_idx + 0] == b[idx]
            assert dest[out_idx + 1] == g[idx]
            assert dest[out_idx + 2] == r[idx]
            assert dest[out_idx + 3] == a[idx]


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_pack_bgra_8bit_no_alpha(backend_name: str) -> None:
    backend = get_backend_module(backend_name)
    width, height = 4, 4
    src_stride = width
    dest_stride = width * 4

    b = (ctypes.c_uint8 * (width * height))(*range(width * height))
    g = (ctypes.c_uint8 * (width * height))(*(x + 10 for x in range(width * height)))
    r = (ctypes.c_uint8 * (width * height))(*(x + 20 for x in range(width * height)))

    dest = (ctypes.c_uint8 * (dest_stride * height))()
    dest_ptr = ctypes.addressof(dest)

    backend.pack_bgra_8bit(b, g, r, None, width, height, src_stride, dest_ptr, dest_stride)

    for y in range(height):
        for x in range(width):
            idx = y * width + x
            out_idx = y * dest_stride + x * 4
            assert dest[out_idx + 0] == b[idx]
            assert dest[out_idx + 1] == g[idx]
            assert dest[out_idx + 2] == r[idx]
            assert dest[out_idx + 3] == 255


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_pack_rgb30_10bit(backend_name: str) -> None:
    backend = get_backend_module(backend_name)
    width, height = 4, 4
    src_stride_samples = width
    dest_stride = width * 4

    # 10-bit values (0-1023)
    r = (ctypes.c_uint16 * (width * height))(*(x * 50 for x in range(width * height)))
    g = (ctypes.c_uint16 * (width * height))(*(x * 40 for x in range(width * height)))
    b = (ctypes.c_uint16 * (width * height))(*(x * 30 for x in range(width * height)))

    dest = (ctypes.c_uint32 * (width * height))()
    dest_ptr = ctypes.addressof(dest)

    backend.pack_rgb30_10bit(r, g, b, None, width, height, src_stride_samples, dest_ptr, dest_stride)

    for y in range(height):
        for x in range(width):
            idx = y * width + x
            val = dest[idx]
            # A2R10G10B10: A(2 bits) R(10 bits) G(10 bits) B(10 bits)
            # Alpha is 11 (3 in decimal) when None provided (0xC0000000)
            assert (val >> 30) == 3
            assert ((val >> 20) & 0x3FF) == r[idx]
            assert ((val >> 10) & 0x3FF) == g[idx]
            assert (val & 0x3FF) == b[idx]


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_helpers_packrgb_integration(backend_name: str) -> None:
    width, height = 16, 16
    src = vs.core.std.BlankClip(width=width, height=height, format=vs.RGB24, color=[10, 20, 30])
    packed = helpers.packrgb(src, backend=cast(Any, backend_name))

    assert packed.format.id == vs.GRAY32
    assert packed.width == width
    assert packed.height == height

    frame = packed.get_frame(0)
    stride = frame.get_stride(0)
    ptr = frame.get_read_ptr(0)

    assert ptr.value is not None
    # Check first pixel
    out = (ctypes.c_uint8 * stride).from_address(ptr.value)
    assert out[0] == 30  # B
    assert out[1] == 20  # G
    assert out[2] == 10  # R
    assert out[3] == 255  # A
