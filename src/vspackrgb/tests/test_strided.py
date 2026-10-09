from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

if TYPE_CHECKING:
    from conftest import BackendModule

STRIDE_CONFIGS = [
    # (width, height, src_pad, dest_pad)
    (17, 5, 15, 16),
    (7, 3, 9, 8),
]

pytestmark = [pytest.mark.parametrize(("width", "height", "src_pad", "dest_pad"), STRIDE_CONFIGS)]


def test_pack_bgra_8bit_strided(backend: BackendModule, width: int, height: int, src_pad: int, dest_pad: int) -> None:
    src_stride = width + src_pad
    dest_stride = width * 4 + dest_pad

    b = np.full((height, src_stride), 0xAA, dtype=np.uint8)
    g = np.full((height, src_stride), 0xAA, dtype=np.uint8)
    r = np.full((height, src_stride), 0xAA, dtype=np.uint8)
    a = np.full((height, src_stride), 0xAA, dtype=np.uint8)

    base = np.arange(width * height, dtype=np.uint8).reshape(height, width)
    b[:, :width] = base
    g[:, :width] = base + np.uint8(10)
    r[:, :width] = base + np.uint8(20)
    a[:, :width] = base + np.uint8(30)

    canary = 0xEE
    dest = np.full((height, dest_stride), canary, dtype=np.uint8)

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

    expected = np.column_stack((b[:, :width].ravel(), g[:, :width].ravel(), r[:, :width].ravel(), a[:, :width].ravel()))
    np.testing.assert_array_equal(dest[:, : width * 4].reshape(-1, 4), expected)
    assert (dest[:, width * 4 :] == canary).all()


def test_pack_bgra_8bit_no_alpha_strided(
    backend: BackendModule, width: int, height: int, src_pad: int, dest_pad: int
) -> None:
    src_stride = width + src_pad
    dest_stride = width * 4 + dest_pad

    b = np.full((height, src_stride), 0xAA, dtype=np.uint8)
    g = np.full((height, src_stride), 0xAA, dtype=np.uint8)
    r = np.full((height, src_stride), 0xAA, dtype=np.uint8)

    base = np.arange(width * height, dtype=np.uint8).reshape(height, width)
    b[:, :width] = base
    g[:, :width] = base + np.uint8(10)
    r[:, :width] = base + np.uint8(20)

    canary = 0xEE
    dest = np.full((height, dest_stride), canary, dtype=np.uint8)

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

    expected = np.column_stack(
        (b[:, :width].ravel(), g[:, :width].ravel(), r[:, :width].ravel(), np.full(width * height, 255, dtype=np.uint8))
    )
    np.testing.assert_array_equal(dest[:, : width * 4].reshape(-1, 4), expected)
    assert (dest[:, width * 4 :] == canary).all()


def test_pack_rgb30_10bit_strided(backend: BackendModule, width: int, height: int, src_pad: int, dest_pad: int) -> None:
    samples_per_row = width + src_pad
    dest_stride_bytes = width * 4 + dest_pad

    r = np.full((height, samples_per_row), 0x3FF, dtype=np.uint16)
    g = np.full((height, samples_per_row), 0x3FF, dtype=np.uint16)
    b = np.full((height, samples_per_row), 0x3FF, dtype=np.uint16)

    indices = np.arange(width * height, dtype=np.uint32).reshape(height, width)
    r[:, :width] = ((indices * 50) % 1024).astype(np.uint16)
    g[:, :width] = ((indices * 40) % 1024).astype(np.uint16)
    b[:, :width] = ((indices * 30) % 1024).astype(np.uint16)

    canary = 0xDEADBEEF
    dest = np.full((height, dest_stride_bytes // 4), canary, dtype=np.uint32)

    backend.pack_rgb30_10bit(
        r.ctypes.data,
        g.ctypes.data,
        b.ctypes.data,
        None,
        width,
        height,
        samples_per_row,
        dest.ctypes.data,
        dest_stride_bytes,
    )

    expected = (
        (np.uint32(3) << 30)
        | (r[:, :width].ravel().astype(np.uint32) << 20)
        | (g[:, :width].ravel().astype(np.uint32) << 10)
        | b[:, :width].ravel().astype(np.uint32)
    )
    np.testing.assert_array_equal(dest[:, :width].ravel(), expected)
    assert (dest[:, width:] == canary).all()


def test_pack_rgb30_10bit_with_alpha_strided(
    backend: BackendModule, width: int, height: int, src_pad: int, dest_pad: int
) -> None:
    samples_per_row = width + src_pad
    dest_stride_bytes = width * 4 + dest_pad

    r = np.full((height, samples_per_row), 0x3FF, dtype=np.uint16)
    g = np.full((height, samples_per_row), 0x3FF, dtype=np.uint16)
    b = np.full((height, samples_per_row), 0x3FF, dtype=np.uint16)
    a = np.full((height, samples_per_row), 0x3FF, dtype=np.uint16)

    indices = np.arange(width * height, dtype=np.uint32).reshape(height, width)
    r[:, :width] = ((indices * 50) % 1024).astype(np.uint16)
    g[:, :width] = ((indices * 40) % 1024).astype(np.uint16)
    b[:, :width] = ((indices * 30) % 1024).astype(np.uint16)
    a[:, :width] = ((indices % 4) << 8).astype(np.uint16)

    canary = 0xDEADBEEF
    dest = np.full((height, dest_stride_bytes // 4), canary, dtype=np.uint32)

    backend.pack_rgb30_10bit(
        r.ctypes.data,
        g.ctypes.data,
        b.ctypes.data,
        a.ctypes.data,
        width,
        height,
        samples_per_row,
        dest.ctypes.data,
        dest_stride_bytes,
    )

    a_bits = a[:, :width].ravel().astype(np.uint32) >> 8
    r_flat = r[:, :width].ravel().astype(np.uint32)
    g_flat = g[:, :width].ravel().astype(np.uint32)
    b_flat = b[:, :width].ravel().astype(np.uint32)

    expected_r = np.where(a_bits != 3, (r_flat * a_bits) // 3, r_flat)
    expected_g = np.where(a_bits != 3, (g_flat * a_bits) // 3, g_flat)
    expected_b = np.where(a_bits != 3, (b_flat * a_bits) // 3, b_flat)

    expected = (a_bits << 30) | (expected_r << 20) | (expected_g << 10) | expected_b
    np.testing.assert_array_equal(dest[:, :width].ravel(), expected)
    assert (dest[:, width:] == canary).all()


def test_pack_rgba64_16bit_strided(
    backend: BackendModule, width: int, height: int, src_pad: int, dest_pad: int
) -> None:
    samples_per_row = width + src_pad
    dest_stride_bytes = width * 8 + dest_pad * 2

    r = np.full((height, samples_per_row), 0xAAAA, dtype=np.uint16)
    g = np.full((height, samples_per_row), 0xAAAA, dtype=np.uint16)
    b = np.full((height, samples_per_row), 0xAAAA, dtype=np.uint16)
    a = np.full((height, samples_per_row), 0xAAAA, dtype=np.uint16)

    indices = np.arange(width * height, dtype=np.uint32).reshape(height, width)
    r[:, :width] = (indices * 100).astype(np.uint16)
    g[:, :width] = (indices * 200).astype(np.uint16)
    b[:, :width] = (indices * 300).astype(np.uint16)
    a[:, :width] = (indices * 400).astype(np.uint16)

    canary = 0xEEEE
    dest = np.full((height, dest_stride_bytes // 2), canary, dtype=np.uint16)

    backend.pack_rgba64_16bit(
        r.ctypes.data,
        g.ctypes.data,
        b.ctypes.data,
        a.ctypes.data,
        width,
        height,
        samples_per_row,
        dest.ctypes.data,
        dest_stride_bytes,
    )

    expected = np.column_stack((r[:, :width].ravel(), g[:, :width].ravel(), b[:, :width].ravel(), a[:, :width].ravel()))
    np.testing.assert_array_equal(dest[:, : width * 4].reshape(-1, 4), expected)
    assert (dest[:, width * 4 :] == canary).all()


def test_pack_rgba16f_16bit_strided(
    backend: BackendModule, width: int, height: int, src_pad: int, dest_pad: int
) -> None:
    samples_per_row = width + src_pad
    dest_stride_bytes = width * 8 + dest_pad * 2

    r = np.full((height, samples_per_row), 0xAAAA, dtype=np.uint16)
    g = np.full((height, samples_per_row), 0xAAAA, dtype=np.uint16)
    b = np.full((height, samples_per_row), 0xAAAA, dtype=np.uint16)

    indices = np.arange(width * height, dtype=np.float64).reshape(height, width)
    r[:, :width] = (indices * 0.1).astype(np.float16).view(np.uint16)
    g[:, :width] = (indices * 0.05).astype(np.float16).view(np.uint16)
    b[:, :width] = (indices * 0.02).astype(np.float16).view(np.uint16)

    canary = 0xEEEE
    dest = np.full((height, dest_stride_bytes // 2), canary, dtype=np.uint16)

    backend.pack_rgba16f_16bit(
        r.ctypes.data,
        g.ctypes.data,
        b.ctypes.data,
        None,
        width,
        height,
        samples_per_row,
        dest.ctypes.data,
        dest_stride_bytes,
    )

    expected = np.column_stack(
        (
            r[:, :width].ravel(),
            g[:, :width].ravel(),
            b[:, :width].ravel(),
            np.full(width * height, np.float16(1.0).view(np.uint16)),
        )
    )
    np.testing.assert_array_equal(dest[:, : width * 4].reshape(-1, 4), expected)
    assert (dest[:, width * 4 :] == canary).all()


def test_pack_rgba32f_32bit_strided(
    backend: BackendModule, width: int, height: int, src_pad: int, dest_pad: int
) -> None:
    samples_per_row = width + src_pad
    dest_stride_bytes = width * 16 + dest_pad * 4

    r = np.full((height, samples_per_row), 0xAAAAAAAA, dtype=np.uint32)
    g = np.full((height, samples_per_row), 0xAAAAAAAA, dtype=np.uint32)
    b = np.full((height, samples_per_row), 0xAAAAAAAA, dtype=np.uint32)

    indices = np.arange(width * height, dtype=np.float64).reshape(height, width)
    r[:, :width] = (indices * 0.1).astype(np.float32).view(np.uint32)
    g[:, :width] = (indices * 0.05).astype(np.float32).view(np.uint32)
    b[:, :width] = (indices * 0.02).astype(np.float32).view(np.uint32)

    canary = 0xEEEEEEEE
    dest = np.full((height, dest_stride_bytes // 4), canary, dtype=np.uint32)

    backend.pack_rgba32f_32bit(
        r.ctypes.data,
        g.ctypes.data,
        b.ctypes.data,
        None,
        width,
        height,
        samples_per_row,
        dest.ctypes.data,
        dest_stride_bytes,
    )

    expected = np.column_stack(
        (
            r[:, :width].ravel(),
            g[:, :width].ravel(),
            b[:, :width].ravel(),
            np.full(width * height, 0x3F800000, dtype=np.uint32),
        )
    )
    np.testing.assert_array_equal(dest[:, : width * 4].reshape(-1, 4), expected)
    assert (dest[:, width * 4 :] == canary).all()
