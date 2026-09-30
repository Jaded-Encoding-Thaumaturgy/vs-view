use super::{compute_row_ptrs, pack_rgba_range};
use crate::dispatch::dispatch_simd;

#[cfg(target_arch = "x86_64")]
#[allow(clippy::wildcard_imports)]
use std::arch::x86_64::*;

#[cfg(target_arch = "aarch64")]
use std::arch::aarch64::{uint8x16x4_t, vdupq_n_u8, vld1q_u8, vst4q_u8};

pub(crate) unsafe fn pack_bgra_8bit(
    b_ptr: *const u8,
    g_ptr: *const u8,
    r_ptr: *const u8,
    a_ptr: Option<*const u8>,
    width: usize,
    height: usize,
    src_stride: usize,
    dest_ptr: *mut u8,
    dest_stride: usize,
) {
    dispatch_simd!(
        avx512: pack_bgra_8bit_avx512,
        avx2: pack_bgra_8bit_avx2,
        sse2: pack_bgra_8bit_sse2,
        neon: pack_bgra_8bit_neon,
        scalar: pack_bgra_8bit_scalar,
        args: (
            b_ptr,
            g_ptr,
            r_ptr,
            a_ptr,
            width,
            height,
            src_stride,
            dest_ptr,
            dest_stride,
        ),
    );
}

unsafe fn pack_bgra_8bit_scalar(
    b_ptr: *const u8,
    g_ptr: *const u8,
    r_ptr: *const u8,
    a_ptr: Option<*const u8>,
    width: usize,
    height: usize,
    src_stride: usize,
    dest_ptr: *mut u8,
    dest_stride: usize,
) {
    for y in 0..height {
        // SAFETY: y < height, strides and pointers are valid for the frame.
        let row = unsafe { compute_row_ptrs(b_ptr, g_ptr, r_ptr, a_ptr, dest_ptr, y, src_stride, dest_stride) };
        // SAFETY: range 0..width is within buffer bounds.
        unsafe {
            pack_rgba_range(&row, 0..width, 0xFF);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "sse2")]
unsafe fn pack_bgra_8bit_sse2(
    b_ptr: *const u8,
    g_ptr: *const u8,
    r_ptr: *const u8,
    a_ptr: Option<*const u8>,
    width: usize,
    height: usize,
    src_stride: usize,
    dest_ptr: *mut u8,
    dest_stride: usize,
) {
    let opaque_alpha = _mm_set1_epi8(-1);

    macro_rules! pack_loop {
        ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
            while $x + 16 <= $width {
                // SAFETY: Target feature sse2 is enabled.
                // $x + 16 <= $width guarantees all reads and writes are within buffer bounds.
                unsafe {
                    let vb = _mm_loadu_si128($row.c0.add($x).cast::<__m128i>());
                    let vg = _mm_loadu_si128($row.c1.add($x).cast::<__m128i>());
                    let vr = _mm_loadu_si128($row.c2.add($x).cast::<__m128i>());
                    let va = $alpha;

                    let bg_lo = _mm_unpacklo_epi8(vb, vg);
                    let bg_hi = _mm_unpackhi_epi8(vb, vg);
                    let ra_lo = _mm_unpacklo_epi8(vr, va);
                    let ra_hi = _mm_unpackhi_epi8(vr, va);

                    let out0 = _mm_unpacklo_epi16(bg_lo, ra_lo);
                    let out1 = _mm_unpackhi_epi16(bg_lo, ra_lo);
                    let out2 = _mm_unpacklo_epi16(bg_hi, ra_hi);
                    let out3 = _mm_unpackhi_epi16(bg_hi, ra_hi);

                    let out_pixel = $row.out.add($x * 4);
                    _mm_storeu_si128(out_pixel.cast::<__m128i>(), out0);
                    _mm_storeu_si128(out_pixel.add(16).cast::<__m128i>(), out1);
                    _mm_storeu_si128(out_pixel.add(32).cast::<__m128i>(), out2);
                    _mm_storeu_si128(out_pixel.add(48).cast::<__m128i>(), out3);
                }

                $x += 16;
            }
        };
    }

    for y in 0..height {
        // SAFETY: y < height, strides and pointers are valid for the frame.
        let row = unsafe { compute_row_ptrs(b_ptr, g_ptr, r_ptr, a_ptr, dest_ptr, y, src_stride, dest_stride) };
        let mut x = 0;

        if let Some(alpha_row) = row.alpha {
            pack_loop!(row, x, width, _mm_loadu_si128(alpha_row.add(x).cast::<__m128i>()));
        } else {
            pack_loop!(row, x, width, opaque_alpha);
        }

        // SAFETY: range x..width is within buffer bounds.
        unsafe {
            pack_rgba_range(&row, x..width, 0xFF);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn pack_bgra_8bit_avx2(
    b_ptr: *const u8,
    g_ptr: *const u8,
    r_ptr: *const u8,
    a_ptr: Option<*const u8>,
    width: usize,
    height: usize,
    src_stride: usize,
    dest_ptr: *mut u8,
    dest_stride: usize,
) {
    let opaque_alpha = _mm256_set1_epi8(-1);

    macro_rules! pack_loop {
        ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
            while $x + 32 <= $width {
                // SAFETY: Target feature avx2 is enabled.
                // $x + 32 <= $width guarantees all reads and writes are within buffer bounds.
                unsafe {
                    let vb = _mm256_loadu_si256($row.c0.add($x).cast::<__m256i>());
                    let vg = _mm256_loadu_si256($row.c1.add($x).cast::<__m256i>());
                    let vr = _mm256_loadu_si256($row.c2.add($x).cast::<__m256i>());
                    let va = $alpha;

                    let bg_lo = _mm256_unpacklo_epi8(vb, vg);
                    let bg_hi = _mm256_unpackhi_epi8(vb, vg);
                    let ra_lo = _mm256_unpacklo_epi8(vr, va);
                    let ra_hi = _mm256_unpackhi_epi8(vr, va);

                    let row0 = _mm256_unpacklo_epi16(bg_lo, ra_lo);
                    let row1 = _mm256_unpackhi_epi16(bg_lo, ra_lo);
                    let row2 = _mm256_unpacklo_epi16(bg_hi, ra_hi);
                    let row3 = _mm256_unpackhi_epi16(bg_hi, ra_hi);

                    let out0 = _mm256_permute2x128_si256::<0x20>(row0, row1);
                    let out1 = _mm256_permute2x128_si256::<0x20>(row2, row3);
                    let out2 = _mm256_permute2x128_si256::<0x31>(row0, row1);
                    let out3 = _mm256_permute2x128_si256::<0x31>(row2, row3);

                    let out_pixel = $row.out.add($x * 4);
                    _mm256_storeu_si256(out_pixel.cast::<__m256i>(), out0);
                    _mm256_storeu_si256(out_pixel.add(32).cast::<__m256i>(), out1);
                    _mm256_storeu_si256(out_pixel.add(64).cast::<__m256i>(), out2);
                    _mm256_storeu_si256(out_pixel.add(96).cast::<__m256i>(), out3);
                }

                $x += 32;
            }
        };
    }

    for y in 0..height {
        // SAFETY: y < height, strides and pointers are valid for the frame.
        let row = unsafe { compute_row_ptrs(b_ptr, g_ptr, r_ptr, a_ptr, dest_ptr, y, src_stride, dest_stride) };
        let mut x = 0;

        if let Some(alpha_row) = row.alpha {
            pack_loop!(row, x, width, _mm256_loadu_si256(alpha_row.add(x).cast::<__m256i>()));
        } else {
            pack_loop!(row, x, width, opaque_alpha);
        }

        // SAFETY: range x..width is within buffer bounds.
        unsafe {
            pack_rgba_range(&row, x..width, 0xFF);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx512f", enable = "avx512bw", enable = "avx512vl")]
unsafe fn pack_bgra_8bit_avx512(
    b_ptr: *const u8,
    g_ptr: *const u8,
    r_ptr: *const u8,
    a_ptr: Option<*const u8>,
    width: usize,
    height: usize,
    src_stride: usize,
    dest_ptr: *mut u8,
    dest_stride: usize,
) {
    let opaque_alpha = _mm512_set1_epi8(-1);

    macro_rules! pack_loop {
        ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
            while $x + 64 <= $width {
                // SAFETY: Target features avx512f/bw/vl are enabled.
                // $x + 64 <= $width guarantees all reads and writes are within buffer bounds.
                unsafe {
                    let vb = _mm512_loadu_si512($row.c0.add($x).cast::<__m512i>());
                    let vg = _mm512_loadu_si512($row.c1.add($x).cast::<__m512i>());
                    let vr = _mm512_loadu_si512($row.c2.add($x).cast::<__m512i>());
                    let va = $alpha;

                    let bg_lo = _mm512_unpacklo_epi8(vb, vg);
                    let bg_hi = _mm512_unpackhi_epi8(vb, vg);
                    let ra_lo = _mm512_unpacklo_epi8(vr, va);
                    let ra_hi = _mm512_unpackhi_epi8(vr, va);

                    let row0 = _mm512_unpacklo_epi16(bg_lo, ra_lo);
                    let row1 = _mm512_unpackhi_epi16(bg_lo, ra_lo);
                    let row2 = _mm512_unpacklo_epi16(bg_hi, ra_hi);
                    let row3 = _mm512_unpackhi_epi16(bg_hi, ra_hi);

                    let l0 = _mm512_castsi512_si256(row0);
                    let h0 = _mm512_extracti64x4_epi64::<1>(row0);
                    let l1 = _mm512_castsi512_si256(row1);
                    let h1 = _mm512_extracti64x4_epi64::<1>(row1);
                    let l2 = _mm512_castsi512_si256(row2);
                    let h2 = _mm512_extracti64x4_epi64::<1>(row2);
                    let l3 = _mm512_castsi512_si256(row3);
                    let h3 = _mm512_extracti64x4_epi64::<1>(row3);

                    let out0 = _mm256_permute2x128_si256::<0x20>(l0, l1);
                    let out1 = _mm256_permute2x128_si256::<0x20>(l2, l3);
                    let out2 = _mm256_permute2x128_si256::<0x31>(l0, l1);
                    let out3 = _mm256_permute2x128_si256::<0x31>(l2, l3);

                    let out4 = _mm256_permute2x128_si256::<0x20>(h0, h1);
                    let out5 = _mm256_permute2x128_si256::<0x20>(h2, h3);
                    let out6 = _mm256_permute2x128_si256::<0x31>(h0, h1);
                    let out7 = _mm256_permute2x128_si256::<0x31>(h2, h3);

                    let z0 = _mm512_inserti64x4::<1>(_mm512_castsi256_si512(out0), out1);
                    let z1 = _mm512_inserti64x4::<1>(_mm512_castsi256_si512(out2), out3);
                    let z2 = _mm512_inserti64x4::<1>(_mm512_castsi256_si512(out4), out5);
                    let z3 = _mm512_inserti64x4::<1>(_mm512_castsi256_si512(out6), out7);

                    let out_pixel = $row.out.add($x * 4);
                    _mm512_storeu_si512(out_pixel.cast::<__m512i>(), z0);
                    _mm512_storeu_si512(out_pixel.add(64).cast::<__m512i>(), z1);
                    _mm512_storeu_si512(out_pixel.add(128).cast::<__m512i>(), z2);
                    _mm512_storeu_si512(out_pixel.add(192).cast::<__m512i>(), z3);
                }

                $x += 64;
            }
        };
    }

    for y in 0..height {
        // SAFETY: y < height, strides and pointers are valid for the frame.
        let row = unsafe { compute_row_ptrs(b_ptr, g_ptr, r_ptr, a_ptr, dest_ptr, y, src_stride, dest_stride) };
        let mut x = 0;

        if let Some(alpha_row) = row.alpha {
            pack_loop!(row, x, width, _mm512_loadu_si512(alpha_row.add(x).cast::<__m512i>()));
        } else {
            pack_loop!(row, x, width, opaque_alpha);
        }

        // SAFETY: range x..width is within buffer bounds.
        unsafe {
            pack_rgba_range(&row, x..width, 0xFF);
        }
    }
}

#[cfg(target_arch = "aarch64")]
unsafe fn pack_bgra_8bit_neon(
    b_ptr: *const u8,
    g_ptr: *const u8,
    r_ptr: *const u8,
    a_ptr: Option<*const u8>,
    width: usize,
    height: usize,
    src_stride: usize,
    dest_ptr: *mut u8,
    dest_stride: usize,
) {
    // SAFETY: Target feature neon is available on aarch64.
    let opaque_alpha = unsafe { vdupq_n_u8(0xFF) };

    macro_rules! pack_loop {
        ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
            while $x + 16 <= $width {
                // SAFETY: $x + 16 <= $width guarantees all reads and writes are within bounds.
                unsafe {
                    let vb = vld1q_u8($row.c0.add($x));
                    let vg = vld1q_u8($row.c1.add($x));
                    let vr = vld1q_u8($row.c2.add($x));
                    let va = $alpha;

                    let interleaved = uint8x16x4_t(vb, vg, vr, va);
                    vst4q_u8($row.out.add($x * 4), interleaved);
                }
                $x += 16;
            }
        };
    }

    for y in 0..height {
        // SAFETY: y < height, strides and pointers are valid for the frame.
        let row = unsafe { compute_row_ptrs(b_ptr, g_ptr, r_ptr, a_ptr, dest_ptr, y, src_stride, dest_stride) };
        let mut x = 0;

        if let Some(alpha_row) = row.alpha {
            pack_loop!(row, x, width, vld1q_u8(alpha_row.add(x)));
        } else {
            pack_loop!(row, x, width, opaque_alpha);
        }

        // SAFETY: range x..width is within buffer bounds.
        unsafe {
            pack_rgba_range(&row, x..width, 0xFF);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run_test<F>(pack_fn: F)
    where
        F: Fn(*const u8, *const u8, *const u8, Option<*const u8>, usize, usize, usize, *mut u8, usize),
    {
        let widths = [1, 5, 7, 8, 13, 16, 23, 32, 64, 65, 128];
        let height = 2;

        for &has_alpha in &[true, false] {
            for &w in &widths {
                let src_stride = w + 4;
                let dest_stride = w * 4 + 8;

                let mut b = vec![0u8; src_stride * height];
                let mut g = vec![0u8; src_stride * height];
                let mut r = vec![0u8; src_stride * height];
                let mut a = vec![0u8; src_stride * height];
                let mut dest_expected = vec![0u8; dest_stride * height];
                let mut dest_actual = vec![0u8; dest_stride * height];

                for y in 0..height {
                    let y_val = u8::try_from(y % 256).unwrap();
                    for x in 0..w {
                        let x_val = u8::try_from(x % 256).unwrap();
                        let sum = x_val.wrapping_add(y_val);
                        b[y * src_stride + x] = sum.wrapping_mul(3);
                        g[y * src_stride + x] = sum.wrapping_mul(5).wrapping_add(1);
                        r[y * src_stride + x] = sum.wrapping_mul(7).wrapping_add(2);
                        a[y * src_stride + x] = sum.wrapping_mul(11).wrapping_add(3);
                    }
                }

                let alpha_opt = if has_alpha { Some(a.as_ptr()) } else { None };

                unsafe {
                    pack_bgra_8bit_scalar(
                        b.as_ptr(),
                        g.as_ptr(),
                        r.as_ptr(),
                        alpha_opt,
                        w,
                        height,
                        src_stride,
                        dest_expected.as_mut_ptr(),
                        dest_stride,
                    );
                }

                pack_fn(
                    b.as_ptr(),
                    g.as_ptr(),
                    r.as_ptr(),
                    alpha_opt,
                    w,
                    height,
                    src_stride,
                    dest_actual.as_mut_ptr(),
                    dest_stride,
                );

                assert_eq!(
                    dest_expected, dest_actual,
                    "mismatch at width {w}, has_alpha={has_alpha}"
                );
            }
        }
    }

    #[test]
    fn test_bgra_8bit_dispatch() {
        run_test(|b, g, r, a, w, h, s, out, d| unsafe {
            pack_bgra_8bit(b, g, r, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_bgra_8bit_sse2() {
        run_test(|b, g, r, a, w, h, s, out, d| unsafe {
            pack_bgra_8bit_sse2(b, g, r, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_bgra_8bit_avx2() {
        if !is_x86_feature_detected!("avx2") {
            eprintln!("[SKIPPED] AVX2 not supported on host CPU");
            return;
        }
        run_test(|b, g, r, a, w, h, s, out, d| unsafe {
            pack_bgra_8bit_avx2(b, g, r, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_bgra_8bit_avx512() {
        if !(is_x86_feature_detected!("avx512f")
            && is_x86_feature_detected!("avx512bw")
            && is_x86_feature_detected!("avx512vl"))
        {
            eprintln!("[SKIPPED] AVX512 not supported on host CPU");
            return;
        }
        run_test(|b, g, r, a, w, h, s, out, d| unsafe {
            pack_bgra_8bit_avx512(b, g, r, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "aarch64")]
    #[test]
    fn test_bgra_8bit_neon() {
        run_test(|b, g, r, a, w, h, s, out, d| unsafe {
            pack_bgra_8bit_neon(b, g, r, a, w, h, s, out, d);
        });
    }
}
