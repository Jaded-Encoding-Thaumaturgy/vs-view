from __future__ import annotations

import ctypes
import struct
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pytest
import vapoursynth as vs

from vspackrgb import helpers

if TYPE_CHECKING:
    from conftest import BackendModule

WIDTHS = [4, 17, 852]
HEIGHTS = [4, 17, 480]

pytestmark = [
    pytest.mark.parametrize("width", WIDTHS),
    pytest.mark.parametrize("height", HEIGHTS),
]


def test_pack_bgra_8bit(backend: BackendModule, width: int, height: int) -> None:
    src_stride = width
    dest_stride = width * 4

    base = np.arange(width * height, dtype=np.uint8)
    b = base
    g = base + np.uint8(10)
    r = base + np.uint8(20)
    a = base + np.uint8(30)

    dest = np.zeros((width * height, 4), dtype=np.uint8)

    backend.pack_bgra_8bit(
        b.ctypes.data,
        g.ctypes.data,
        r.ctypes.data,
        a.ctypes.data,
        width,
        height,
        src_stride,
        dest.ctypes.data,
        dest_stride,
    )

    expected = np.column_stack((b, g, r, a))
    np.testing.assert_array_equal(dest, expected)


def test_pack_bgra_8bit_no_alpha(backend: BackendModule, width: int, height: int) -> None:
    src_stride = width
    dest_stride = width * 4

    base = np.arange(width * height, dtype=np.uint8)
    b = base
    g = base + np.uint8(10)
    r = base + np.uint8(20)

    dest = np.zeros((width * height, 4), dtype=np.uint8)

    backend.pack_bgra_8bit(
        b.ctypes.data,
        g.ctypes.data,
        r.ctypes.data,
        None,
        width,
        height,
        src_stride,
        dest.ctypes.data,
        dest_stride,
    )

    expected = np.column_stack((b, g, r, np.full_like(b, 255)))
    np.testing.assert_array_equal(dest, expected)


def test_pack_rgb30_10bit(backend: BackendModule, width: int, height: int) -> None:
    src_stride_samples = width
    dest_stride = width * 4

    indices = np.arange(width * height, dtype=np.uint32)
    r = ((indices * 50) % 1024).astype(np.uint16)
    g = ((indices * 40) % 1024).astype(np.uint16)
    b = ((indices * 30) % 1024).astype(np.uint16)

    dest = np.zeros(width * height, dtype=np.uint32)

    backend.pack_rgb30_10bit(
        r.ctypes.data,
        g.ctypes.data,
        b.ctypes.data,
        None,
        width,
        height,
        src_stride_samples,
        dest.ctypes.data,
        dest_stride,
    )

    expected = (np.uint32(3) << 30) | (r.astype(np.uint32) << 20) | (g.astype(np.uint32) << 10) | b.astype(np.uint32)
    np.testing.assert_array_equal(dest, expected)


def test_pack_rgb30_10bit_with_alpha(backend: BackendModule, width: int, height: int) -> None:
    src_stride_samples = width
    dest_stride = width * 4

    indices = np.arange(width * height, dtype=np.uint32)
    r = ((indices * 50) % 1024).astype(np.uint16)
    g = ((indices * 40) % 1024).astype(np.uint16)
    b = ((indices * 30) % 1024).astype(np.uint16)
    a = ((indices % 4) << 8).astype(np.uint16)

    dest = np.zeros(width * height, dtype=np.uint32)

    backend.pack_rgb30_10bit(
        r.ctypes.data,
        g.ctypes.data,
        b.ctypes.data,
        a.ctypes.data,
        width,
        height,
        src_stride_samples,
        dest.ctypes.data,
        dest_stride,
    )

    a_bits = a.astype(np.uint32) >> 8
    expected_r = np.where(a_bits != 3, (r.astype(np.uint32) * a_bits) // 3, r.astype(np.uint32))
    expected_g = np.where(a_bits != 3, (g.astype(np.uint32) * a_bits) // 3, g.astype(np.uint32))
    expected_b = np.where(a_bits != 3, (b.astype(np.uint32) * a_bits) // 3, b.astype(np.uint32))

    expected = (a_bits << 30) | (expected_r << 20) | (expected_g << 10) | expected_b
    np.testing.assert_array_equal(dest, expected)


def test_pack_rgba64_16bit(backend: BackendModule, width: int, height: int) -> None:
    src_stride_samples = width
    dest_stride = width * 4 * 2  # 4 channels * 2 bytes

    indices = np.arange(width * height, dtype=np.uint32)
    r = (indices * 100).astype(np.uint16)
    g = (indices * 200).astype(np.uint16)
    b = (indices * 300).astype(np.uint16)
    a = (indices * 400).astype(np.uint16)

    dest = np.zeros((width * height, 4), dtype=np.uint16)

    backend.pack_rgba64_16bit(
        r.ctypes.data,
        g.ctypes.data,
        b.ctypes.data,
        a.ctypes.data,
        width,
        height,
        src_stride_samples,
        dest.ctypes.data,
        dest_stride,
    )

    expected = np.column_stack((r, g, b, a))
    np.testing.assert_array_equal(dest, expected)


def test_pack_rgba16f_16bit(backend: BackendModule, width: int, height: int) -> None:
    src_stride_samples = width
    dest_stride = width * 4 * 2

    indices = np.arange(width * height, dtype=np.float64)
    r = (indices * 0.1).astype(np.float16).view(np.uint16)
    g = (indices * 0.05).astype(np.float16).view(np.uint16)
    b = (indices * 0.02).astype(np.float16).view(np.uint16)

    dest = np.zeros((width * height, 4), dtype=np.uint16)

    backend.pack_rgba16f_16bit(
        r.ctypes.data,
        g.ctypes.data,
        b.ctypes.data,
        None,
        width,
        height,
        src_stride_samples,
        dest.ctypes.data,
        dest_stride,
    )

    expected = np.column_stack((r, g, b, np.full_like(r, np.float16(1.0).view(np.uint16))))
    np.testing.assert_array_equal(dest, expected)


def test_pack_rgba32f_32bit(backend: BackendModule, width: int, height: int) -> None:
    src_stride_samples = width
    dest_stride = width * 4 * 4  # 4 channels * 4 bytes

    indices = np.arange(width * height, dtype=np.float64)
    r = (indices * 0.1).astype(np.float32).view(np.uint32)
    g = (indices * 0.05).astype(np.float32).view(np.uint32)
    b = (indices * 0.02).astype(np.float32).view(np.uint32)

    dest = np.zeros((width * height, 4), dtype=np.uint32)

    backend.pack_rgba32f_32bit(
        r.ctypes.data,
        g.ctypes.data,
        b.ctypes.data,
        None,
        width,
        height,
        src_stride_samples,
        dest.ctypes.data,
        dest_stride,
    )

    expected = np.column_stack((r, g, b, np.full_like(r, 0x3F800000)))
    np.testing.assert_array_equal(dest, expected)


@pytest.mark.vpy("initial-core")
def test_helpers_packrgb_integration(backend_name: str, width: int, height: int) -> None:
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
def test_helpers_packrgb_rgba16f(backend_name: str, width: int, height: int) -> None:
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
def test_helpers_packrgb_rgbs(backend_name: str, width: int, height: int) -> None:
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
def test_helpers_packrgb_frame_no_alpha(backend_name: str, width: int, height: int) -> None:
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
def test_helpers_packrgb_frame_explicit_alpha(backend_name: str, width: int, height: int) -> None:
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
def test_helpers_packrgb_frame_alpha_prop(backend_name: str, width: int, height: int) -> None:
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


@pytest.mark.vpy("initial-core")
def test_helpers_packrgb_cython_fallback(width: int, height: int) -> None:
    src = vs.core.std.BlankClip(width=width, height=height, format=vs.RGB24, color=[10, 20, 30])
    packed = helpers.packrgb(src, backend=cast(Any, "cython"))
    assert packed.format.id == vs.GRAY32
    assert packed.width == width
    assert packed.height == height
