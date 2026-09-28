use super::{compute_row_ptrs, pack_rgba_range};
use crate::dispatch::dispatch_simd;

#[cfg(target_arch = "x86_64")]
#[allow(clippy::wildcard_imports)]
use std::arch::x86_64::*;

#[cfg(target_arch = "aarch64")]
use std::arch::aarch64::{uint32x4x4_t, vdupq_n_u32, vld1q_u32, vst4q_u32};

pub(crate) unsafe fn pack_rgba_32bit<const ALPHA_DEFAULT: u32>(
    r_ptr: *const u32,
    g_ptr: *const u32,
    b_ptr: *const u32,
    a_ptr: Option<*const u32>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    dispatch_simd!(
        avx512: pack_rgba_32bit_avx512::<ALPHA_DEFAULT>,
        avx2: pack_rgba_32bit_avx2::<ALPHA_DEFAULT>,
        sse2: pack_rgba_32bit_sse2::<ALPHA_DEFAULT>,
        neon: pack_rgba_32bit_neon::<ALPHA_DEFAULT>,
        scalar: pack_rgba_32bit_scalar::<ALPHA_DEFAULT>,
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

unsafe fn pack_rgba_32bit_scalar<const ALPHA_DEFAULT: u32>(
    r_ptr: *const u32,
    g_ptr: *const u32,
    b_ptr: *const u32,
    a_ptr: Option<*const u32>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
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
unsafe fn pack_rgba_32bit_sse2<const ALPHA_DEFAULT: u32>(
    r_ptr: *const u32,
    g_ptr: *const u32,
    b_ptr: *const u32,
    a_ptr: Option<*const u32>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    unsafe {
        let alpha_constant = _mm_set1_ps(f32::from_bits(ALPHA_DEFAULT));

        macro_rules! pack_loop {
            ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
                while $x + 4 <= $width {
                    let vr = _mm_loadu_ps($row.c0.add($x).cast::<f32>());
                    let vg = _mm_loadu_ps($row.c1.add($x).cast::<f32>());
                    let vb = _mm_loadu_ps($row.c2.add($x).cast::<f32>());
                    let va = $alpha;

                    let rg_lo = _mm_unpacklo_ps(vr, vg);
                    let rg_hi = _mm_unpackhi_ps(vr, vg);
                    let ba_lo = _mm_unpacklo_ps(vb, va);
                    let ba_hi = _mm_unpackhi_ps(vb, va);

                    let out0 = _mm_castpd_ps(_mm_unpacklo_pd(_mm_castps_pd(rg_lo), _mm_castps_pd(ba_lo)));
                    let out1 = _mm_castpd_ps(_mm_unpackhi_pd(_mm_castps_pd(rg_lo), _mm_castps_pd(ba_lo)));
                    let out2 = _mm_castpd_ps(_mm_unpacklo_pd(_mm_castps_pd(rg_hi), _mm_castps_pd(ba_hi)));
                    let out3 = _mm_castpd_ps(_mm_unpackhi_pd(_mm_castps_pd(rg_hi), _mm_castps_pd(ba_hi)));

                    let out_pixel = $row.out.add($x * 4);
                    _mm_storeu_ps(out_pixel.cast::<f32>(), out0);
                    _mm_storeu_ps(out_pixel.add(4).cast::<f32>(), out1);
                    _mm_storeu_ps(out_pixel.add(8).cast::<f32>(), out2);
                    _mm_storeu_ps(out_pixel.add(12).cast::<f32>(), out3);

                    $x += 4;
                }
            };
        }

        for y in 0..height {
            let row = compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride);
            let mut x = 0;

            if let Some(alpha_row) = row.alpha {
                pack_loop!(row, x, width, _mm_loadu_ps(alpha_row.add(x).cast::<f32>()));
            } else {
                pack_loop!(row, x, width, alpha_constant);
            }

            pack_rgba_range(&row, x..width, ALPHA_DEFAULT);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn pack_rgba_32bit_avx2<const ALPHA_DEFAULT: u32>(
    r_ptr: *const u32,
    g_ptr: *const u32,
    b_ptr: *const u32,
    a_ptr: Option<*const u32>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    unsafe {
        let alpha_constant = _mm256_set1_ps(f32::from_bits(ALPHA_DEFAULT));

        macro_rules! pack_loop {
            ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
                while $x + 8 <= $width {
                    let vr = _mm256_loadu_ps($row.c0.add($x).cast::<f32>());
                    let vg = _mm256_loadu_ps($row.c1.add($x).cast::<f32>());
                    let vb = _mm256_loadu_ps($row.c2.add($x).cast::<f32>());
                    let va = $alpha;

                    let rg_lo = _mm256_unpacklo_ps(vr, vg);
                    let rg_hi = _mm256_unpackhi_ps(vr, vg);
                    let ba_lo = _mm256_unpacklo_ps(vb, va);
                    let ba_hi = _mm256_unpackhi_ps(vb, va);

                    let row0 = _mm256_castpd_ps(_mm256_unpacklo_pd(
                        _mm256_castps_pd(rg_lo),
                        _mm256_castps_pd(ba_lo),
                    ));
                    let row1 = _mm256_castpd_ps(_mm256_unpackhi_pd(
                        _mm256_castps_pd(rg_lo),
                        _mm256_castps_pd(ba_lo),
                    ));
                    let row2 = _mm256_castpd_ps(_mm256_unpacklo_pd(
                        _mm256_castps_pd(rg_hi),
                        _mm256_castps_pd(ba_hi),
                    ));
                    let row3 = _mm256_castpd_ps(_mm256_unpackhi_pd(
                        _mm256_castps_pd(rg_hi),
                        _mm256_castps_pd(ba_hi),
                    ));

                    let out0 = _mm256_permute2f128_ps::<0x20>(row0, row1);
                    let out1 = _mm256_permute2f128_ps::<0x20>(row2, row3);
                    let out2 = _mm256_permute2f128_ps::<0x31>(row0, row1);
                    let out3 = _mm256_permute2f128_ps::<0x31>(row2, row3);

                    let out_pixel = $row.out.add($x * 4).cast::<f32>();
                    _mm256_storeu_ps(out_pixel, out0);
                    _mm256_storeu_ps(out_pixel.add(8), out1);
                    _mm256_storeu_ps(out_pixel.add(16), out2);
                    _mm256_storeu_ps(out_pixel.add(24), out3);

                    $x += 8;
                }
            };
        }

        for y in 0..height {
            let row = compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride);
            let mut x = 0;

            if let Some(alpha_row) = row.alpha {
                pack_loop!(row, x, width, _mm256_loadu_ps(alpha_row.add(x).cast::<f32>()));
            } else {
                pack_loop!(row, x, width, alpha_constant);
            }

            pack_rgba_range(&row, x..width, ALPHA_DEFAULT);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx512f", enable = "avx512dq", enable = "avx512vl")]
#[inline]
unsafe fn store_rgba_32bit_avx512(out_pixel: *mut f32, row0: __m512, row1: __m512, row2: __m512, row3: __m512) {
    unsafe {
        let l0 = _mm512_castps512_ps256(row0);
        let h0 = _mm512_extractf32x8_ps::<1>(row0);
        let l1 = _mm512_castps512_ps256(row1);
        let h1 = _mm512_extractf32x8_ps::<1>(row1);
        let l2 = _mm512_castps512_ps256(row2);
        let h2 = _mm512_extractf32x8_ps::<1>(row2);
        let l3 = _mm512_castps512_ps256(row3);
        let h3 = _mm512_extractf32x8_ps::<1>(row3);

        let out0 = _mm256_permute2f128_ps::<0x20>(l0, l1);
        let out1 = _mm256_permute2f128_ps::<0x20>(l2, l3);
        let out2 = _mm256_permute2f128_ps::<0x31>(l0, l1);
        let out3 = _mm256_permute2f128_ps::<0x31>(l2, l3);

        let out4 = _mm256_permute2f128_ps::<0x20>(h0, h1);
        let out5 = _mm256_permute2f128_ps::<0x20>(h2, h3);
        let out6 = _mm256_permute2f128_ps::<0x31>(h0, h1);
        let out7 = _mm256_permute2f128_ps::<0x31>(h2, h3);

        let z0 = _mm512_insertf32x8::<1>(_mm512_castps256_ps512(out0), out1);
        let z1 = _mm512_insertf32x8::<1>(_mm512_castps256_ps512(out2), out3);
        let z2 = _mm512_insertf32x8::<1>(_mm512_castps256_ps512(out4), out5);
        let z3 = _mm512_insertf32x8::<1>(_mm512_castps256_ps512(out6), out7);

        _mm512_storeu_ps(out_pixel, z0);
        _mm512_storeu_ps(out_pixel.add(16), z1);
        _mm512_storeu_ps(out_pixel.add(32), z2);
        _mm512_storeu_ps(out_pixel.add(48), z3);
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx512f", enable = "avx512dq", enable = "avx512vl")]
unsafe fn pack_rgba_32bit_avx512<const ALPHA_DEFAULT: u32>(
    r_ptr: *const u32,
    g_ptr: *const u32,
    b_ptr: *const u32,
    a_ptr: Option<*const u32>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    unsafe {
        let alpha_constant = _mm512_set1_ps(f32::from_bits(ALPHA_DEFAULT));

        macro_rules! pack_loop {
            ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
                while $x + 16 <= $width {
                    let vr = _mm512_loadu_ps($row.c0.add($x).cast::<f32>());
                    let vg = _mm512_loadu_ps($row.c1.add($x).cast::<f32>());
                    let vb = _mm512_loadu_ps($row.c2.add($x).cast::<f32>());
                    let va = $alpha;

                    let rg_lo = _mm512_unpacklo_ps(vr, vg);
                    let rg_hi = _mm512_unpackhi_ps(vr, vg);
                    let ba_lo = _mm512_unpacklo_ps(vb, va);
                    let ba_hi = _mm512_unpackhi_ps(vb, va);

                    let row0 = _mm512_castpd_ps(_mm512_unpacklo_pd(
                        _mm512_castps_pd(rg_lo),
                        _mm512_castps_pd(ba_lo),
                    ));
                    let row1 = _mm512_castpd_ps(_mm512_unpackhi_pd(
                        _mm512_castps_pd(rg_lo),
                        _mm512_castps_pd(ba_lo),
                    ));
                    let row2 = _mm512_castpd_ps(_mm512_unpacklo_pd(
                        _mm512_castps_pd(rg_hi),
                        _mm512_castps_pd(ba_hi),
                    ));
                    let row3 = _mm512_castpd_ps(_mm512_unpackhi_pd(
                        _mm512_castps_pd(rg_hi),
                        _mm512_castps_pd(ba_hi),
                    ));

                    let out_pixel = $row.out.add($x * 4).cast::<f32>();
                    store_rgba_32bit_avx512(out_pixel, row0, row1, row2, row3);

                    $x += 16;
                }
            };
        }

        for y in 0..height {
            let row = compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride);
            let mut x = 0;

            if let Some(alpha_row) = row.alpha {
                pack_loop!(row, x, width, _mm512_loadu_ps(alpha_row.add(x).cast::<f32>()));
            } else {
                pack_loop!(row, x, width, alpha_constant);
            }

            pack_rgba_range(&row, x..width, ALPHA_DEFAULT);
        }
    }
}

#[cfg(target_arch = "aarch64")]
unsafe fn pack_rgba_32bit_neon<const ALPHA_DEFAULT: u32>(
    r_ptr: *const u32,
    g_ptr: *const u32,
    b_ptr: *const u32,
    a_ptr: Option<*const u32>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    unsafe {
        let opaque_alpha = vdupq_n_u32(ALPHA_DEFAULT);

        macro_rules! pack_loop {
            ($row:ident, $x:ident, $width:ident, $alpha:expr) => {
                while $x + 4 <= $width {
                    let vr = vld1q_u32($row.c0.add($x));
                    let vg = vld1q_u32($row.c1.add($x));
                    let vb = vld1q_u32($row.c2.add($x));
                    let va = $alpha;

                    let interleaved = uint32x4x4_t(vr, vg, vb, va);
                    vst4q_u32($row.out.add($x * 4), interleaved);
                    $x += 4;
                }
            };
        }

        for y in 0..height {
            let row = compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride);
            let mut x = 0;

            if let Some(alpha_row) = row.alpha {
                pack_loop!(row, x, width, vld1q_u32(alpha_row.add(x)));
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
        F: Fn(*const u32, *const u32, *const u32, Option<*const u32>, usize, usize, usize, *mut u32, usize),
    {
        let widths = [1, 5, 7, 8, 13, 16, 23, 32, 64, 65, 128];
        let height = 2;

        for &has_alpha in &[true, false] {
            for &w in &widths {
                let samples_per_row = w + 4;
                let dest_stride = w * 4 * 4 + 8;

                let mut r = vec![0u32; samples_per_row * height];
                let mut g = vec![0u32; samples_per_row * height];
                let mut b = vec![0u32; samples_per_row * height];
                let mut a = vec![0u32; samples_per_row * height];
                let mut dest_expected = vec![0u8; dest_stride * height];
                let mut dest_actual = vec![0u8; dest_stride * height];

                for y in 0..height {
                    let y_val = u32::try_from(y).unwrap();
                    for x in 0..w {
                        let x_val = u32::try_from(x).unwrap();
                        let sum = x_val.wrapping_add(y_val);
                        r[y * samples_per_row + x] = sum.wrapping_mul(11);
                        g[y * samples_per_row + x] = sum.wrapping_mul(13).wrapping_add(1);
                        b[y * samples_per_row + x] = sum.wrapping_mul(17).wrapping_add(2);
                        a[y * samples_per_row + x] = sum.wrapping_mul(19).wrapping_add(3);
                    }
                }

                let alpha_opt = if has_alpha { Some(a.as_ptr()) } else { None };

                unsafe {
                    pack_rgba_32bit_scalar::<0x3F80_0000>(
                        r.as_ptr(),
                        g.as_ptr(),
                        b.as_ptr(),
                        alpha_opt,
                        w,
                        height,
                        samples_per_row,
                        dest_expected.as_mut_ptr().cast::<u32>(),
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
                    dest_actual.as_mut_ptr().cast::<u32>(),
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
    fn test_rgba_32bit_dispatch() {
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgba_32bit::<0x3F80_0000>(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_rgba_32bit_sse2() {
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgba_32bit_sse2::<0x3F80_0000>(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_rgba_32bit_avx2() {
        if !is_x86_feature_detected!("avx2") {
            eprintln!("[SKIPPED] AVX2 not supported on host CPU");
            return;
        }
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgba_32bit_avx2::<0x3F80_0000>(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_rgba_32bit_avx512() {
        if !(is_x86_feature_detected!("avx512f")
            && is_x86_feature_detected!("avx512dq")
            && is_x86_feature_detected!("avx512vl"))
        {
            eprintln!("[SKIPPED] AVX512 not supported on host CPU");
            return;
        }
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgba_32bit_avx512::<0x3F80_0000>(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "aarch64")]
    #[test]
    fn test_rgba_32bit_neon() {
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgba_32bit_neon::<0x3F80_0000>(r, g, b, a, w, h, s, out, d);
        });
    }
}
