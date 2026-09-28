use super::{compute_row_ptrs, pack_rgba_range};
use crate::dispatch::dispatch_simd;

#[cfg(target_arch = "x86_64")]
#[allow(clippy::wildcard_imports)]
use std::arch::x86_64::*;

#[cfg(target_arch = "aarch64")]
use std::arch::aarch64::{uint16x8x4_t, vdupq_n_u16, vld1q_u16, vst4q_u16};

pub(crate) unsafe fn pack_rgba_16bit<const ALPHA_DEFAULT: u16>(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u16,
    dest_stride: usize,
) {
    dispatch_simd!(
        avx512: pack_rgba_16bit_avx512::<ALPHA_DEFAULT>,
        avx2: pack_rgba_16bit_avx2::<ALPHA_DEFAULT>,
        sse2: pack_rgba_16bit_sse2::<ALPHA_DEFAULT>,
        neon: pack_rgba_16bit_neon::<ALPHA_DEFAULT>,
        scalar: pack_rgba_16bit_scalar::<ALPHA_DEFAULT>,
        args: (
            r_ptr,
            g_ptr,
            b_ptr,
            a_ptr,
            width,
            height,
            samples_per_row,
            dest_ptr,
            dest_stride,
        ),
    );
}

unsafe fn pack_rgba_16bit_scalar<const ALPHA_DEFAULT: u16>(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u16,
    dest_stride: usize,
) {
    unsafe {
        for y in 0..height {
            let row = compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride);
            pack_rgba_range(&row, 0..width, ALPHA_DEFAULT);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "sse2")]
unsafe fn pack_rgba_16bit_sse2<const ALPHA_DEFAULT: u16>(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u16,
    dest_stride: usize,
) {
    unsafe {
        let alpha_constant = _mm_set1_epi16(ALPHA_DEFAULT.cast_signed());

        macro_rules! pack_loop {
            ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
                while $x + 8 <= $width {
                    let vr = _mm_loadu_si128($row.c0.add($x).cast::<__m128i>());
                    let vg = _mm_loadu_si128($row.c1.add($x).cast::<__m128i>());
                    let vb = _mm_loadu_si128($row.c2.add($x).cast::<__m128i>());
                    let va = $alpha;

                    let rg_lo = _mm_unpacklo_epi16(vr, vg);
                    let rg_hi = _mm_unpackhi_epi16(vr, vg);
                    let ba_lo = _mm_unpacklo_epi16(vb, va);
                    let ba_hi = _mm_unpackhi_epi16(vb, va);

                    let out0 = _mm_unpacklo_epi32(rg_lo, ba_lo);
                    let out1 = _mm_unpackhi_epi32(rg_lo, ba_lo);
                    let out2 = _mm_unpacklo_epi32(rg_hi, ba_hi);
                    let out3 = _mm_unpackhi_epi32(rg_hi, ba_hi);

                    let out_pixel = $row.out.add($x * 4);
                    _mm_storeu_si128(out_pixel.cast::<__m128i>(), out0);
                    _mm_storeu_si128(out_pixel.add(8).cast::<__m128i>(), out1);
                    _mm_storeu_si128(out_pixel.add(16).cast::<__m128i>(), out2);
                    _mm_storeu_si128(out_pixel.add(24).cast::<__m128i>(), out3);

                    $x += 8;
                }
            };
        }

        for y in 0..height {
            let row = compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride);
            let mut x = 0;

            if let Some(alpha_row) = row.alpha {
                pack_loop!(row, x, width, _mm_loadu_si128(alpha_row.add(x).cast::<__m128i>()));
            } else {
                pack_loop!(row, x, width, alpha_constant);
            }

            pack_rgba_range(&row, x..width, ALPHA_DEFAULT);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn pack_rgba_16bit_avx2<const ALPHA_DEFAULT: u16>(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u16,
    dest_stride: usize,
) {
    unsafe {
        let alpha_constant = _mm256_set1_epi16(ALPHA_DEFAULT.cast_signed());

        macro_rules! pack_loop {
            ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
                while $x + 16 <= $width {
                    let vr = _mm256_loadu_si256($row.c0.add($x).cast::<__m256i>());
                    let vg = _mm256_loadu_si256($row.c1.add($x).cast::<__m256i>());
                    let vb = _mm256_loadu_si256($row.c2.add($x).cast::<__m256i>());
                    let va = $alpha;

                    let rg_lo = _mm256_unpacklo_epi16(vr, vg);
                    let rg_hi = _mm256_unpackhi_epi16(vr, vg);
                    let ba_lo = _mm256_unpacklo_epi16(vb, va);
                    let ba_hi = _mm256_unpackhi_epi16(vb, va);

                    let row0 = _mm256_unpacklo_epi32(rg_lo, ba_lo);
                    let row1 = _mm256_unpackhi_epi32(rg_lo, ba_lo);
                    let row2 = _mm256_unpacklo_epi32(rg_hi, ba_hi);
                    let row3 = _mm256_unpackhi_epi32(rg_hi, ba_hi);

                    let out0 = _mm256_permute2x128_si256::<0x20>(row0, row1);
                    let out1 = _mm256_permute2x128_si256::<0x20>(row2, row3);
                    let out2 = _mm256_permute2x128_si256::<0x31>(row0, row1);
                    let out3 = _mm256_permute2x128_si256::<0x31>(row2, row3);

                    let out_pixel = $row.out.add($x * 4);
                    _mm256_storeu_si256(out_pixel.cast::<__m256i>(), out0);
                    _mm256_storeu_si256(out_pixel.add(16).cast::<__m256i>(), out1);
                    _mm256_storeu_si256(out_pixel.add(32).cast::<__m256i>(), out2);
                    _mm256_storeu_si256(out_pixel.add(48).cast::<__m256i>(), out3);

                    $x += 16;
                }
            };
        }

        for y in 0..height {
            let row = compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride);
            let mut x = 0;

            if let Some(alpha_row) = row.alpha {
                pack_loop!(row, x, width, _mm256_loadu_si256(alpha_row.add(x).cast::<__m256i>()));
            } else {
                pack_loop!(row, x, width, alpha_constant);
            }

            pack_rgba_range(&row, x..width, ALPHA_DEFAULT);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx512f", enable = "avx512bw", enable = "avx512vl")]
unsafe fn pack_rgba_16bit_avx512<const ALPHA_DEFAULT: u16>(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u16,
    dest_stride: usize,
) {
    unsafe {
        let alpha_constant = _mm512_set1_epi16(ALPHA_DEFAULT.cast_signed());

        macro_rules! pack_loop {
            ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
                while $x + 32 <= $width {
                    let vr = _mm512_loadu_si512($row.c0.add($x).cast::<__m512i>());
                    let vg = _mm512_loadu_si512($row.c1.add($x).cast::<__m512i>());
                    let vb = _mm512_loadu_si512($row.c2.add($x).cast::<__m512i>());
                    let va = $alpha;

                    let rg_lo = _mm512_unpacklo_epi16(vr, vg);
                    let rg_hi = _mm512_unpackhi_epi16(vr, vg);
                    let ba_lo = _mm512_unpacklo_epi16(vb, va);
                    let ba_hi = _mm512_unpackhi_epi16(vb, va);

                    let row0 = _mm512_unpacklo_epi32(rg_lo, ba_lo);
                    let row1 = _mm512_unpackhi_epi32(rg_lo, ba_lo);
                    let row2 = _mm512_unpacklo_epi32(rg_hi, ba_hi);
                    let row3 = _mm512_unpackhi_epi32(rg_hi, ba_hi);

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
                    _mm512_storeu_si512(out_pixel.add(32).cast::<__m512i>(), z1);
                    _mm512_storeu_si512(out_pixel.add(64).cast::<__m512i>(), z2);
                    _mm512_storeu_si512(out_pixel.add(96).cast::<__m512i>(), z3);

                    $x += 32;
                }
            };
        }

        for y in 0..height {
            let row = compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride);
            let mut x = 0;

            if let Some(alpha_row) = row.alpha {
                pack_loop!(row, x, width, _mm512_loadu_si512(alpha_row.add(x).cast::<__m512i>()));
            } else {
                pack_loop!(row, x, width, alpha_constant);
            }

            pack_rgba_range(&row, x..width, ALPHA_DEFAULT);
        }
    }
}

#[cfg(target_arch = "aarch64")]
unsafe fn pack_rgba_16bit_neon<const ALPHA_DEFAULT: u16>(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u16,
    dest_stride: usize,
) {
    unsafe {
        let opaque_alpha = vdupq_n_u16(ALPHA_DEFAULT);

        macro_rules! pack_loop {
            ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
                while $x + 8 <= $width {
                    let vr = vld1q_u16($row.c0.add($x));
                    let vg = vld1q_u16($row.c1.add($x));
                    let vb = vld1q_u16($row.c2.add($x));
                    let va = $alpha;

                    let interleaved = uint16x8x4_t(vr, vg, vb, va);
                    vst4q_u16($row.out.add($x * 4), interleaved);
                    $x += 8;
                }
            };
        }

        for y in 0..height {
            let row = compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride);
            let mut x = 0;

            if let Some(alpha_row) = row.alpha {
                pack_loop!(row, x, width, vld1q_u16(alpha_row.add(x)));
            } else {
                pack_loop!(row, x, width, opaque_alpha);
            }

            pack_rgba_range(&row, x..width, ALPHA_DEFAULT);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run_test<F>(pack_fn: F)
    where
        F: Fn(*const u16, *const u16, *const u16, Option<*const u16>, usize, usize, usize, *mut u16, usize),
    {
        let widths = [1, 5, 7, 8, 13, 16, 23, 32, 64, 65, 128];
        let height = 2;

        for &has_alpha in &[true, false] {
            for &w in &widths {
                let samples_per_row = w + 4;
                let dest_stride = w * 4 * 2 + 8;

                let mut r = vec![0u16; samples_per_row * height];
                let mut g = vec![0u16; samples_per_row * height];
                let mut b = vec![0u16; samples_per_row * height];
                let mut a = vec![0u16; samples_per_row * height];
                let mut dest_expected = vec![0u8; dest_stride * height];
                let mut dest_actual = vec![0u8; dest_stride * height];

                for y in 0..height {
                    let y_val = u16::try_from(y % 65536).unwrap();
                    for x in 0..w {
                        let x_val = u16::try_from(x % 65536).unwrap();
                        let sum = x_val.wrapping_add(y_val);
                        r[y * samples_per_row + x] = sum.wrapping_mul(11);
                        g[y * samples_per_row + x] = sum.wrapping_mul(13).wrapping_add(1);
                        b[y * samples_per_row + x] = sum.wrapping_mul(17).wrapping_add(2);
                        a[y * samples_per_row + x] = sum.wrapping_mul(19).wrapping_add(3);
                    }
                }

                let alpha_opt = if has_alpha { Some(a.as_ptr()) } else { None };

                unsafe {
                    pack_rgba_16bit_scalar::<0xFFFF>(
                        r.as_ptr(),
                        g.as_ptr(),
                        b.as_ptr(),
                        alpha_opt,
                        w,
                        height,
                        samples_per_row,
                        dest_expected.as_mut_ptr().cast::<u16>(),
                        dest_stride,
                    );
                }

                pack_fn(
                    r.as_ptr(),
                    g.as_ptr(),
                    b.as_ptr(),
                    alpha_opt,
                    w,
                    height,
                    samples_per_row,
                    dest_actual.as_mut_ptr().cast::<u16>(),
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
    fn test_rgba_16bit_dispatch() {
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgba_16bit::<0xFFFF>(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_rgba_16bit_sse2() {
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgba_16bit_sse2::<0xFFFF>(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_rgba_16bit_avx2() {
        if !is_x86_feature_detected!("avx2") {
            eprintln!("[SKIPPED] AVX2 not supported on host CPU");
            return;
        }
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgba_16bit_avx2::<0xFFFF>(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_rgba_16bit_avx512() {
        if !(is_x86_feature_detected!("avx512f")
            && is_x86_feature_detected!("avx512bw")
            && is_x86_feature_detected!("avx512vl"))
        {
            eprintln!("[SKIPPED] AVX512 not supported on host CPU");
            return;
        }
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgba_16bit_avx512::<0xFFFF>(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "aarch64")]
    #[test]
    fn test_rgba_16bit_neon() {
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgba_16bit_neon::<0xFFFF>(r, g, b, a, w, h, s, out, d);
        });
    }
}
