import ctypes
import struct
from typing import Any, Protocol, cast

import pytest
import vapoursynth as vs

from vspackrgb import cython, helpers, numba, numpy, python


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

    def pack_rgba64_16bit(
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

    def pack_rgba16f_16bit(
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

    def pack_rgba32f_32bit(
        self,
        r_data: ctypes.Array[ctypes.c_uint32],
        g_data: ctypes.Array[ctypes.c_uint32],
        b_data: ctypes.Array[ctypes.c_uint32],
        a_data: ctypes.Array[ctypes.c_uint32] | None,
        width: int,
        height: int,
        samples_per_row: int,
        dest_ptr: int,
        dest_stride: int,
    ) -> None: ...


BACKENDS = ["python", "numpy", "cython", "numba"]


def get_backend_module(backend_name: str) -> BackendModule:
    match backend_name:
        case "python":
            return cast(BackendModule, python)
        case "numpy":
            return cast(BackendModule, numpy)
        case "cython":
            return cast(BackendModule, cython)
        case "numba":
            return cast(BackendModule, numba)
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
def test_pack_rgba64_16bit(backend_name: str) -> None:
    backend = get_backend_module(backend_name)
    width, height = 4, 4
    src_stride_samples = width
    dest_stride = width * 4 * 2  # 4 channels * 2 bytes

    r = (ctypes.c_uint16 * (width * height))(*(x * 100 for x in range(width * height)))
    g = (ctypes.c_uint16 * (width * height))(*(x * 200 for x in range(width * height)))
    b = (ctypes.c_uint16 * (width * height))(*(x * 300 for x in range(width * height)))
    a = (ctypes.c_uint16 * (width * height))(*(x * 400 for x in range(width * height)))

    dest = (ctypes.c_uint16 * (width * height * 4))()
    dest_ptr = ctypes.addressof(dest)

    backend.pack_rgba64_16bit(r, g, b, a, width, height, src_stride_samples, dest_ptr, dest_stride)

    for y in range(height):
        for x in range(width):
            idx = y * width + x
            out_idx = idx * 4
            assert dest[out_idx + 0] == r[idx]
            assert dest[out_idx + 1] == g[idx]
            assert dest[out_idx + 2] == b[idx]
            assert dest[out_idx + 3] == a[idx]


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_pack_rgba16f_16bit(backend_name: str) -> None:
    backend = get_backend_module(backend_name)
    width, height = 4, 4
    src_stride_samples = width
    dest_stride = width * 4 * 2

    def to_f16(val: float) -> int:
        return struct.unpack("H", struct.pack("e", val))[0]

    r = (ctypes.c_uint16 * (width * height))(*(to_f16(x * 0.1) for x in range(width * height)))
    g = (ctypes.c_uint16 * (width * height))(*(to_f16(x * 0.05) for x in range(width * height)))
    b = (ctypes.c_uint16 * (width * height))(*(to_f16(x * 0.02) for x in range(width * height)))

    dest = (ctypes.c_uint16 * (width * height * 4))()
    dest_ptr = ctypes.addressof(dest)

    backend.pack_rgba16f_16bit(r, g, b, None, width, height, src_stride_samples, dest_ptr, dest_stride)

    for y in range(height):
        for x in range(width):
            idx = y * width + x
            out_idx = idx * 4

            assert dest[out_idx + 0] == to_f16((x + y * width) * 0.1)
            assert dest[out_idx + 1] == to_f16((x + y * width) * 0.05)
            assert dest[out_idx + 2] == to_f16((x + y * width) * 0.02)
            assert dest[out_idx + 3] == to_f16(1.0)


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_pack_rgba32f_32bit(backend_name: str) -> None:
    backend = get_backend_module(backend_name)
    width, height = 4, 4
    src_stride_samples = width
    dest_stride = width * 4 * 4  # 4 channels * 4 bytes

    def to_f32(val: float) -> int:
        return struct.unpack("I", struct.pack("f", val))[0]

    r = (ctypes.c_uint32 * (width * height))(*(to_f32(x * 0.1) for x in range(width * height)))
    g = (ctypes.c_uint32 * (width * height))(*(to_f32(x * 0.05) for x in range(width * height)))
    b = (ctypes.c_uint32 * (width * height))(*(to_f32(x * 0.02) for x in range(width * height)))

    dest = (ctypes.c_uint32 * (width * height * 4))()
    dest_ptr = ctypes.addressof(dest)

    backend.pack_rgba32f_32bit(r, g, b, None, width, height, src_stride_samples, dest_ptr, dest_stride)

    for y in range(height):
        for x in range(width):
            idx = y * width + x
            out_idx = idx * 4

            assert dest[out_idx + 0] == to_f32((x + y * width) * 0.1)
            assert dest[out_idx + 1] == to_f32((x + y * width) * 0.05)
            assert dest[out_idx + 2] == to_f32((x + y * width) * 0.02)
            assert dest[out_idx + 3] == 0x3F800000  # 1.0f bits


@pytest.mark.vpy("initial-core")
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


@pytest.mark.vpy("initial-core")
@pytest.mark.parametrize("backend_name", BACKENDS)
def test_helpers_packrgb_rgba16f(backend_name: str) -> None:
    width, height = 8, 8

    # Test Integer packing
    src_int = vs.core.std.BlankClip(width=width, height=height, format=vs.RGB48, color=[65535, 32768, 0])
    packed_int = helpers.packrgb(src_int, backend=cast(Any, backend_name))
    assert packed_int.format.id == vs.GRAY16
    assert packed_int.width == width * 4
    assert packed_int.get_frame(0).props.get("VSViewPacked16") == 1

    # Test Float packing (now use RGBH input)
    src_float = vs.core.std.BlankClip(width=width, height=height, format=vs.RGBH, color=[1.0, 0.5, 0.0])
    packed_float = helpers.packrgb(src_float, backend=cast(Any, backend_name))

    assert packed_float.format.id == vs.GRAYH
    assert packed_float.width == width * 4
    assert packed_float.get_frame(0).props.get("VSViewPacked16F") == 1

    frame = packed_float.get_frame(0)
    ptr = frame.get_read_ptr(0)
    assert ptr.value is not None
    out = (ctypes.c_uint16 * (width * height * 4)).from_address(ptr.value)
    # First pixel [1.0, 0.5, 0.0] in float16
    # 1.0 = 0x3C00, 0.5 = 0x3800, 0.0 = 0x0000
    assert out[0] == 0x3C00  # R
    assert out[1] == 0x3800  # G
    assert out[2] == 0x0000  # B
    assert out[3] == 0x3C00  # A


@pytest.mark.vpy("initial-core")
@pytest.mark.parametrize("backend_name", BACKENDS)
def test_helpers_packrgb_rgbs(backend_name: str) -> None:
    width, height = 8, 8

    # Test Float packing (RGBS input)
    src_float = vs.core.std.BlankClip(width=width, height=height, format=vs.RGBS, color=[1.0, 0.5, 0.0])
    packed_float = helpers.packrgb(src_float, backend=cast(Any, backend_name))

    assert packed_float.format.id == vs.GRAYS
    assert packed_float.width == width * 4
    assert packed_float.get_frame(0).props.get("VSViewPacked32F") == 1

    frame = packed_float.get_frame(0)
    ptr = frame.get_read_ptr(0)
    assert ptr.value is not None
    out = (ctypes.c_uint32 * (width * height * 4)).from_address(ptr.value)

    def to_bits(val: float) -> int:
        return struct.unpack("I", struct.pack("f", val))[0]

    # First pixel [1.0, 0.5, 0.0] * 2.0 = [2.0, 1.0, 0.0]
    assert out[0] == to_bits(1.0)  # R
    assert out[1] == to_bits(0.5)  # G
    assert out[2] == to_bits(0.0)  # B
    assert out[3] == to_bits(1.0)  # A


@pytest.mark.vpy("initial-core")
@pytest.mark.parametrize("backend_name", BACKENDS)
def test_helpers_packrgb_frame_no_alpha(backend_name: str) -> None:
    width, height = 16, 16
    src = vs.core.std.BlankClip(width=width, height=height, format=vs.RGB24, color=[10, 20, 30])

    with src.get_frame(0) as src_frame:
        packed_frame = helpers.packrgb(src_frame, backend=cast(Any, backend_name))

    with packed_frame:
        assert isinstance(packed_frame, vs.VideoFrame)
        assert packed_frame.format.id == vs.GRAY32
        assert packed_frame.width == width
        assert packed_frame.height == height

        stride = packed_frame.get_stride(0)
        ptr = packed_frame.get_read_ptr(0)
        assert ptr.value is not None
        out = (ctypes.c_uint8 * stride).from_address(ptr.value)
        assert out[0] == 30  # B
        assert out[1] == 20  # G
        assert out[2] == 10  # R
        assert out[3] == 255  # A


@pytest.mark.vpy("initial-core")
@pytest.mark.parametrize("backend_name", BACKENDS)
def test_helpers_packrgb_frame_explicit_alpha(backend_name: str) -> None:
    width, height = 16, 16
    src = vs.core.std.BlankClip(width=width, height=height, format=vs.RGB24, color=[10, 20, 30])
    alpha = vs.core.std.BlankClip(width=width, height=height, format=vs.GRAY8, color=[150])

    with src.get_frame(0) as src_frame, alpha.get_frame(0) as alpha_frame:
        packed_frame_alpha = helpers.packrgb(src_frame, alpha=alpha_frame, backend=cast(Any, backend_name))

    assert isinstance(packed_frame_alpha, vs.VideoFrame)

    stride = packed_frame_alpha.get_stride(0)
    ptr_alpha = packed_frame_alpha.get_read_ptr(0)
    assert ptr_alpha.value is not None

    out_alpha = (ctypes.c_uint8 * stride).from_address(ptr_alpha.value)
    assert out_alpha[0] == 30
    assert out_alpha[1] == 20
    assert out_alpha[2] == 10
    assert out_alpha[3] == 150


@pytest.mark.vpy("initial-core")
@pytest.mark.parametrize("backend_name", BACKENDS)
def test_helpers_packrgb_frame_alpha_prop(backend_name: str) -> None:
    width, height = 16, 16
    src = vs.core.std.BlankClip(width=width, height=height, format=vs.RGB24, color=[10, 20, 30])
    alpha = vs.core.std.BlankClip(width=width, height=height, format=vs.GRAY8, color=[150])
    src_with_alpha = src.std.ClipToProp(alpha, prop="_Alpha")

    with src_with_alpha.get_frame(0) as src_frame_with_alpha:
        packed_frame_prop = helpers.packrgb(src_frame_with_alpha, alpha=True, backend=cast(Any, backend_name))

    assert isinstance(packed_frame_prop, vs.VideoFrame)

    stride = packed_frame_prop.get_stride(0)
    ptr_prop = packed_frame_prop.get_read_ptr(0)
    assert ptr_prop.value is not None

    out_prop = (ctypes.c_uint8 * stride).from_address(ptr_prop.value)
    assert out_prop[0] == 30
    assert out_prop[1] == 20
    assert out_prop[2] == 10
    assert out_prop[3] == 150
    assert "_Alpha" not in packed_frame_prop.props
