use super::compute_row_ptrs;
use crate::dispatch::dispatch_simd;

#[cfg(target_arch = "x86_64")]
#[allow(clippy::wildcard_imports)]
use std::arch::x86_64::*;

#[cfg(target_arch = "aarch64")]
#[allow(clippy::wildcard_imports)]
use std::arch::aarch64::*;

use std::ops::Range;

pub(crate) unsafe fn pack_rgb30_10bit(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    dispatch_simd!(
        avx512: pack_rgb30_10bit_avx512,
        avx2: pack_rgb30_10bit_avx2,
        sse2: pack_rgb30_10bit_sse2,
        neon: pack_rgb30_10bit_neon,
        scalar: pack_rgb30_10bit_scalar,
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

#[inline]
const fn premultiply_10bit(val: u32, a_bits: u32) -> u32 {
    match a_bits {
        0 => 0,
        1 => val / 3,
        2 => (val * 2) / 3,
        _ => val,
    }
}

#[inline]
unsafe fn pack_rgb30_range(
    r_row: *const u16,
    g_row: *const u16,
    b_row: *const u16,
    a_row: Option<*const u16>,
    out_row: *mut u32,
    range: Range<usize>,
) {
    if let Some(alpha_ptr) = a_row {
        for x in range {
            // SAFETY: Caller guarantees indices in range are valid for row buffers.
            unsafe {
                let alpha_sample = u32::from(*alpha_ptr.add(x)) >> 8;
                let a_bits = alpha_sample & 0x3;
                let r_val = premultiply_10bit(u32::from(*r_row.add(x)) & 0x3FF, a_bits);
                let g_val = premultiply_10bit(u32::from(*g_row.add(x)) & 0x3FF, a_bits);
                let b_val = premultiply_10bit(u32::from(*b_row.add(x)) & 0x3FF, a_bits);
                *out_row.add(x) = (a_bits << 30) | (r_val << 20) | (g_val << 10) | b_val;
            }
        }
    } else {
        for x in range {
            // SAFETY: Caller guarantees indices in range are valid for row buffers.
            unsafe {
                let r_val = u32::from(*r_row.add(x)) & 0x3FF;
                let g_val = u32::from(*g_row.add(x)) & 0x3FF;
                let b_val = u32::from(*b_row.add(x)) & 0x3FF;
                *out_row.add(x) = 0xC000_0000 | (r_val << 20) | (g_val << 10) | b_val;
            }
        }
    }
}

unsafe fn pack_rgb30_10bit_scalar(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    for y in 0..height {
        // SAFETY: y < height, strides and pointers are valid for the frame.
        let row = unsafe { compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride) };
        // SAFETY: range 0..width is within buffer bounds.
        unsafe {
            pack_rgb30_range(row.c0, row.c1, row.c2, row.alpha, row.out, 0..width);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "sse2")]
unsafe fn pack_rgb30_10bit_sse2(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    let zero_reg = _mm_setzero_si128();
    let alpha_mask = _mm_set1_epi32(0xC000_0000u32.cast_signed());
    let div3_mul = _mm_set1_epi16(0x5556);
    let alpha_mask_3 = _mm_set1_epi16(3);

    macro_rules! pack_loop {
        ($row:ident, $x:ident, $width:ident, $alpha_opt:expr) => {
            let alpha_opt: Option<*const u16> = $alpha_opt;
            while $x + 8 <= $width {
                // SAFETY: Target feature sse2 is enabled.
                // $x + 8 <= $width guarantees all reads and writes are within buffer bounds.
                unsafe {
                    let red_raw_16 = _mm_loadu_si128($row.c0.add($x).cast::<__m128i>());
                    let green_raw_16 = _mm_loadu_si128($row.c1.add($x).cast::<__m128i>());
                    let blue_raw_16 = _mm_loadu_si128($row.c2.add($x).cast::<__m128i>());

                    let (r_val, g_val, b_val, a_mask_lo, a_mask_hi) = if let Some(alpha_row) = alpha_opt {
                        let alpha_raw_16 = _mm_loadu_si128(alpha_row.add($x).cast::<__m128i>());
                        let a_bits = _mm_and_si128(_mm_srli_epi16(alpha_raw_16, 8), alpha_mask_3);

                        let r = _mm_mulhi_epu16(_mm_mullo_epi16(red_raw_16, a_bits), div3_mul);
                        let g = _mm_mulhi_epu16(_mm_mullo_epi16(green_raw_16, a_bits), div3_mul);
                        let b = _mm_mulhi_epu16(_mm_mullo_epi16(blue_raw_16, a_bits), div3_mul);

                        let a_word_lo = _mm_unpacklo_epi16(a_bits, zero_reg);
                        let a_word_hi = _mm_unpackhi_epi16(a_bits, zero_reg);
                        (
                            r,
                            g,
                            b,
                            _mm_slli_epi32(a_word_lo, 30),
                            _mm_slli_epi32(a_word_hi, 30),
                        )
                    } else {
                        (red_raw_16, green_raw_16, blue_raw_16, alpha_mask, alpha_mask)
                    };

                    let red_word_lo = _mm_unpacklo_epi16(r_val, zero_reg);
                    let red_word_hi = _mm_unpackhi_epi16(r_val, zero_reg);
                    let green_word_lo = _mm_unpacklo_epi16(g_val, zero_reg);
                    let green_word_hi = _mm_unpackhi_epi16(g_val, zero_reg);
                    let blue_word_lo = _mm_unpacklo_epi16(b_val, zero_reg);
                    let blue_word_hi = _mm_unpackhi_epi16(b_val, zero_reg);

                    let red_shifted_lo = _mm_slli_epi32(red_word_lo, 20);
                    let green_shifted_lo = _mm_slli_epi32(green_word_lo, 10);
                    let rg_combined_lo = _mm_or_si128(red_shifted_lo, green_shifted_lo);
                    let rgb_channels_lo = _mm_or_si128(rg_combined_lo, blue_word_lo);
                    let final_pixel_lo = _mm_or_si128(rgb_channels_lo, a_mask_lo);

                    let red_shifted_hi = _mm_slli_epi32(red_word_hi, 20);
                    let green_shifted_hi = _mm_slli_epi32(green_word_hi, 10);
                    let rg_combined_hi = _mm_or_si128(red_shifted_hi, green_shifted_hi);
                    let rgb_channels_hi = _mm_or_si128(rg_combined_hi, blue_word_hi);
                    let final_pixel_hi = _mm_or_si128(rgb_channels_hi, a_mask_hi);

                    let out_pixel = $row.out.add($x);
                    _mm_storeu_si128(out_pixel.cast::<__m128i>(), final_pixel_lo);
                    _mm_storeu_si128(out_pixel.add(4).cast::<__m128i>(), final_pixel_hi);
                }

                $x += 8;
            }
        };
    }

    for y in 0..height {
        // SAFETY: y < height, strides and pointers are valid for the frame.
        let row = unsafe { compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride) };
        let mut x = 0;

        if let Some(alpha_row) = row.alpha {
            pack_loop!(row, x, width, Some(alpha_row));
        } else {
            pack_loop!(row, x, width, None);
        }

        // SAFETY: range x..width is within buffer bounds.
        unsafe {
            pack_rgb30_range(row.c0, row.c1, row.c2, row.alpha, row.out, x..width);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn pack_rgb30_10bit_avx2(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    let alpha_mask = _mm256_set1_epi32(0xC000_0000u32.cast_signed());
    let div3_mul = _mm256_set1_epi16(0x5556);
    let alpha_mask_3 = _mm256_set1_epi16(3);

    macro_rules! pack_loop {
        ($row:ident, $x:ident, $width:ident, $alpha_opt:expr) => {
            let alpha_opt: Option<*const u16> = $alpha_opt;
            while $x + 16 <= $width {
                // SAFETY: Target feature avx2 is enabled.
                // $x + 16 <= $width guarantees all reads and writes are within buffer bounds.
                unsafe {
                    let red_raw = _mm256_loadu_si256($row.c0.add($x).cast::<__m256i>());
                    let green_raw = _mm256_loadu_si256($row.c1.add($x).cast::<__m256i>());
                    let blue_raw = _mm256_loadu_si256($row.c2.add($x).cast::<__m256i>());

                    let (r_val, g_val, b_val, a_shift_0, a_shift_1) = if let Some(a_row) = alpha_opt {
                        let alpha_raw = _mm256_loadu_si256(a_row.add($x).cast::<__m256i>());
                        let a_bits = _mm256_and_si256(_mm256_srli_epi16(alpha_raw, 8), alpha_mask_3);

                        let r = _mm256_mulhi_epu16(_mm256_mullo_epi16(red_raw, a_bits), div3_mul);
                        let g = _mm256_mulhi_epu16(_mm256_mullo_epi16(green_raw, a_bits), div3_mul);
                        let b = _mm256_mulhi_epu16(_mm256_mullo_epi16(blue_raw, a_bits), div3_mul);

                        let a0 = _mm256_cvtepu16_epi32(_mm256_castsi256_si128(a_bits));
                        let a1 = _mm256_cvtepu16_epi32(_mm256_extracti128_si256::<1>(a_bits));
                        (r, g, b, _mm256_slli_epi32(a0, 30), _mm256_slli_epi32(a1, 30))
                    } else {
                        (red_raw, green_raw, blue_raw, alpha_mask, alpha_mask)
                    };

                    let r_wide_0 = _mm256_cvtepu16_epi32(_mm256_castsi256_si128(r_val));
                    let r_wide_1 = _mm256_cvtepu16_epi32(_mm256_extracti128_si256::<1>(r_val));
                    let g_wide_0 = _mm256_cvtepu16_epi32(_mm256_castsi256_si128(g_val));
                    let g_wide_1 = _mm256_cvtepu16_epi32(_mm256_extracti128_si256::<1>(g_val));
                    let b_wide_0 = _mm256_cvtepu16_epi32(_mm256_castsi256_si128(b_val));
                    let b_wide_1 = _mm256_cvtepu16_epi32(_mm256_extracti128_si256::<1>(b_val));

                    let r_shift_0 = _mm256_slli_epi32(r_wide_0, 20);
                    let g_shift_0 = _mm256_slli_epi32(g_wide_0, 10);
                    let red_green_0 = _mm256_or_si256(r_shift_0, g_shift_0);
                    let rgb_0 = _mm256_or_si256(red_green_0, b_wide_0);
                    let final_pixel_0 = _mm256_or_si256(rgb_0, a_shift_0);

                    let r_shift_1 = _mm256_slli_epi32(r_wide_1, 20);
                    let g_shift_1 = _mm256_slli_epi32(g_wide_1, 10);
                    let red_green_1 = _mm256_or_si256(r_shift_1, g_shift_1);
                    let rgb_1 = _mm256_or_si256(red_green_1, b_wide_1);
                    let final_pixel_1 = _mm256_or_si256(rgb_1, a_shift_1);

                    _mm256_storeu_si256($row.out.add($x).cast::<__m256i>(), final_pixel_0);
                    _mm256_storeu_si256($row.out.add($x + 8).cast::<__m256i>(), final_pixel_1);
                }

                $x += 16;
            }
        };
    }

    for y in 0..height {
        // SAFETY: y < height, strides and pointers are valid for the frame.
        let row = unsafe { compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride) };
        let mut x = 0;

        if let Some(alpha_row) = row.alpha {
            pack_loop!(row, x, width, Some(alpha_row));
        } else {
            pack_loop!(row, x, width, None);
        }

        // SAFETY: range x..width is within buffer bounds.
        unsafe {
            pack_rgb30_range(row.c0, row.c1, row.c2, row.alpha, row.out, x..width);
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx512f", enable = "avx512bw", enable = "avx512vl")]
unsafe fn pack_rgb30_10bit_avx512(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    let alpha_mask = _mm512_set1_epi32(0xC000_0000u32.cast_signed());
    let div3_mul = _mm256_set1_epi16(0x5556);
    let alpha_mask_3 = _mm256_set1_epi16(3);

    macro_rules! pack_loop {
        ($row:ident, $x:ident, $width:ident, $alpha_opt:expr) => {
            let alpha_opt: Option<*const u16> = $alpha_opt;
            while $x + 32 <= $width {
                // SAFETY: Target features avx512f/bw/vl are enabled.
                // $x + 32 <= $width guarantees all reads and writes are within buffer bounds.
                unsafe {
                    let red_raw_0 = _mm256_loadu_si256($row.c0.add($x).cast::<__m256i>());
                    let green_raw_0 = _mm256_loadu_si256($row.c1.add($x).cast::<__m256i>());
                    let blue_raw_0 = _mm256_loadu_si256($row.c2.add($x).cast::<__m256i>());

                    let red_raw_1 = _mm256_loadu_si256($row.c0.add($x + 16).cast::<__m256i>());
                    let green_raw_1 = _mm256_loadu_si256($row.c1.add($x + 16).cast::<__m256i>());
                    let blue_raw_1 = _mm256_loadu_si256($row.c2.add($x + 16).cast::<__m256i>());

                    let (r0, g0, b0, r1, g1, b1, a_mask_0, a_mask_1) = if let Some(alpha_row) = alpha_opt {
                        let alpha_raw_0 = _mm256_loadu_si256(alpha_row.add($x).cast::<__m256i>());
                        let alpha_raw_1 = _mm256_loadu_si256(alpha_row.add($x + 16).cast::<__m256i>());

                        let a_bits_0 = _mm256_and_si256(_mm256_srli_epi16(alpha_raw_0, 8), alpha_mask_3);
                        let a_bits_1 = _mm256_and_si256(_mm256_srli_epi16(alpha_raw_1, 8), alpha_mask_3);

                        let r_prem_0 = _mm256_mulhi_epu16(_mm256_mullo_epi16(red_raw_0, a_bits_0), div3_mul);
                        let g_prem_0 = _mm256_mulhi_epu16(_mm256_mullo_epi16(green_raw_0, a_bits_0), div3_mul);
                        let b_prem_0 = _mm256_mulhi_epu16(_mm256_mullo_epi16(blue_raw_0, a_bits_0), div3_mul);

                        let r_prem_1 = _mm256_mulhi_epu16(_mm256_mullo_epi16(red_raw_1, a_bits_1), div3_mul);
                        let g_prem_1 = _mm256_mulhi_epu16(_mm256_mullo_epi16(green_raw_1, a_bits_1), div3_mul);
                        let b_prem_1 = _mm256_mulhi_epu16(_mm256_mullo_epi16(blue_raw_1, a_bits_1), div3_mul);

                        let a0 = _mm512_cvtepu16_epi32(a_bits_0);
                        let a1 = _mm512_cvtepu16_epi32(a_bits_1);
                        (
                            r_prem_0,
                            g_prem_0,
                            b_prem_0,
                            r_prem_1,
                            g_prem_1,
                            b_prem_1,
                            _mm512_slli_epi32(a0, 30),
                            _mm512_slli_epi32(a1, 30),
                        )
                    } else {
                        (
                            red_raw_0,
                            green_raw_0,
                            blue_raw_0,
                            red_raw_1,
                            green_raw_1,
                            blue_raw_1,
                            alpha_mask,
                            alpha_mask,
                        )
                    };

                    let r_wide_0 = _mm512_cvtepu16_epi32(r0);
                    let g_wide_0 = _mm512_cvtepu16_epi32(g0);
                    let b_wide_0 = _mm512_cvtepu16_epi32(b0);

                    let r_wide_1 = _mm512_cvtepu16_epi32(r1);
                    let g_wide_1 = _mm512_cvtepu16_epi32(g1);
                    let b_wide_1 = _mm512_cvtepu16_epi32(b1);

                    let r_shift_0 = _mm512_slli_epi32(r_wide_0, 20);
                    let g_shift_0 = _mm512_slli_epi32(g_wide_0, 10);
                    let rg_0 = _mm512_or_si512(r_shift_0, g_shift_0);
                    let pixel_0 = _mm512_ternarylogic_epi32::<0xFE>(rg_0, b_wide_0, a_mask_0);

                    let r_shift_1 = _mm512_slli_epi32(r_wide_1, 20);
                    let g_shift_1 = _mm512_slli_epi32(g_wide_1, 10);
                    let rg_1 = _mm512_or_si512(r_shift_1, g_shift_1);
                    let pixel_1 = _mm512_ternarylogic_epi32::<0xFE>(rg_1, b_wide_1, a_mask_1);

                    _mm512_storeu_si512($row.out.add($x).cast::<__m512i>(), pixel_0);
                    _mm512_storeu_si512($row.out.add($x + 16).cast::<__m512i>(), pixel_1);
                }

                $x += 32;
            }
        };
    }

    for y in 0..height {
        // SAFETY: y < height, strides and pointers are valid for the frame.
        let row = unsafe { compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride) };
        let mut x = 0;

        if let Some(alpha_row) = row.alpha {
            pack_loop!(row, x, width, Some(alpha_row));
        } else {
            pack_loop!(row, x, width, None);
        }

        // SAFETY: range x..width is within buffer bounds.
        unsafe {
            pack_rgb30_range(row.c0, row.c1, row.c2, row.alpha, row.out, x..width);
        }
    }
}

#[cfg(target_arch = "aarch64")]
unsafe fn pack_rgb30_10bit_neon(
    r_ptr: *const u16,
    g_ptr: *const u16,
    b_ptr: *const u16,
    a_ptr: Option<*const u16>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: *mut u32,
    dest_stride: usize,
) {
    let alpha_mask = vdupq_n_u32(0xC000_0000);
    let div3_recip = vdup_n_u16(21846);
    let alpha_mask_3 = vdup_n_u16(3);

    macro_rules! pack_loop {
        ($row:ident, $x:ident, $width:ident, $alpha_opt:expr) => {
            let alpha_opt: Option<*const u16> = $alpha_opt;
            while $x + 8 <= $width {
                // SAFETY: $x + 8 <= $width guarantees all reads and writes are within bounds.
                unsafe {
                    let red_half_0 = vld1_u16($row.c0.add($x));
                    let green_half_0 = vld1_u16($row.c1.add($x));
                    let blue_half_0 = vld1_u16($row.c2.add($x));

                    let red_half_1 = vld1_u16($row.c0.add($x + 4));
                    let green_half_1 = vld1_u16($row.c1.add($x + 4));
                    let blue_half_1 = vld1_u16($row.c2.add($x + 4));

                    let (r_wide_0, g_wide_0, b_wide_0, r_wide_1, g_wide_1, b_wide_1, a_shift_0, a_shift_1) =
                        if let Some(alpha_row) = alpha_opt {
                            let alpha_half_0 = vld1_u16(alpha_row.add($x));
                            let alpha_half_1 = vld1_u16(alpha_row.add($x + 4));

                            let a_bits_0 = vand_u16(vshr_n_u16(alpha_half_0, 8), alpha_mask_3);
                            let a_bits_1 = vand_u16(vshr_n_u16(alpha_half_1, 8), alpha_mask_3);

                            let r_prod_0 = vmul_u16(red_half_0, a_bits_0);
                            let g_prod_0 = vmul_u16(green_half_0, a_bits_0);
                            let b_prod_0 = vmul_u16(blue_half_0, a_bits_0);

                            let r_prod_1 = vmul_u16(red_half_1, a_bits_1);
                            let g_prod_1 = vmul_u16(green_half_1, a_bits_1);
                            let b_prod_1 = vmul_u16(blue_half_1, a_bits_1);

                            let r0 = vshrq_n_u32(vmull_u16(r_prod_0, div3_recip), 16);
                            let g0 = vshrq_n_u32(vmull_u16(g_prod_0, div3_recip), 16);
                            let b0 = vshrq_n_u32(vmull_u16(b_prod_0, div3_recip), 16);
                            let a0 = vshlq_n_u32(vmovl_u16(a_bits_0), 30);

                            let r1 = vshrq_n_u32(vmull_u16(r_prod_1, div3_recip), 16);
                            let g1 = vshrq_n_u32(vmull_u16(g_prod_1, div3_recip), 16);
                            let b1 = vshrq_n_u32(vmull_u16(b_prod_1, div3_recip), 16);
                            let a1 = vshlq_n_u32(vmovl_u16(a_bits_1), 30);

                            (r0, g0, b0, r1, g1, b1, a0, a1)
                        } else {
                            (
                                vmovl_u16(red_half_0),
                                vmovl_u16(green_half_0),
                                vmovl_u16(blue_half_0),
                                vmovl_u16(red_half_1),
                                vmovl_u16(green_half_1),
                                vmovl_u16(blue_half_1),
                                alpha_mask,
                                alpha_mask,
                            )
                        };

                    let r_shift_0 = vshlq_n_u32(r_wide_0, 20);
                    let g_shift_0 = vshlq_n_u32(g_wide_0, 10);
                    let red_green_0 = vorrq_u32(r_shift_0, g_shift_0);
                    let rgb_0 = vorrq_u32(red_green_0, b_wide_0);
                    let final_pixel_0 = vorrq_u32(rgb_0, a_shift_0);

                    let r_shift_1 = vshlq_n_u32(r_wide_1, 20);
                    let g_shift_1 = vshlq_n_u32(g_wide_1, 10);
                    let red_green_1 = vorrq_u32(r_shift_1, g_shift_1);
                    let rgb_1 = vorrq_u32(red_green_1, b_wide_1);
                    let final_pixel_1 = vorrq_u32(rgb_1, a_shift_1);

                    vst1q_u32($row.out.add($x), final_pixel_0);
                    vst1q_u32($row.out.add($x + 4), final_pixel_1);
                }

                $x += 8;
            }
        };
    }

    for y in 0..height {
        // SAFETY: y < height, strides and pointers are valid for the frame.
        let row = unsafe { compute_row_ptrs(r_ptr, g_ptr, b_ptr, a_ptr, dest_ptr, y, samples_per_row, dest_stride) };
        let mut x = 0;

        if let Some(alpha_row) = row.alpha {
            pack_loop!(row, x, width, Some(alpha_row));
        } else {
            pack_loop!(row, x, width, None);
        }

        // SAFETY: range x..width is within buffer bounds.
        unsafe {
            pack_rgb30_range(row.c0, row.c1, row.c2, row.alpha, row.out, x..width);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run_test<F>(pack_fn: F)
    where
        F: Fn(*const u16, *const u16, *const u16, Option<*const u16>, usize, usize, usize, *mut u32, usize),
    {
        let widths = [1, 5, 7, 8, 13, 16, 23, 32, 64, 65, 128];
        let height = 2;

        for &has_alpha in &[true, false] {
            for &w in &widths {
                let samples_per_row = w + 4;
                let dest_stride = w * 4 + 8;

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
                        r[y * samples_per_row + x] = sum.wrapping_mul(17) & 0x3FF;
                        g[y * samples_per_row + x] = sum.wrapping_mul(23).wrapping_add(1) & 0x3FF;
                        b[y * samples_per_row + x] = sum.wrapping_mul(29).wrapping_add(2) & 0x3FF;
                        a[y * samples_per_row + x] = sum.wrapping_mul(31).wrapping_add(3) & 0x3FF;
                    }
                }

                let alpha_opt = if has_alpha { Some(a.as_ptr()) } else { None };

                unsafe {
                    pack_rgb30_10bit_scalar(
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
    fn test_rgb30_10bit_dispatch() {
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgb30_10bit(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_rgb30_10bit_sse2() {
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgb30_10bit_sse2(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_rgb30_10bit_avx2() {
        if !is_x86_feature_detected!("avx2") {
            eprintln!("[SKIPPED] AVX2 not supported on host CPU");
            return;
        }
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgb30_10bit_avx2(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "x86_64")]
    #[test]
    fn test_rgb30_10bit_avx512() {
        if !(is_x86_feature_detected!("avx512f")
            && is_x86_feature_detected!("avx512bw")
            && is_x86_feature_detected!("avx512vl"))
        {
            eprintln!("[SKIPPED] AVX512 not supported on host CPU");
            return;
        }
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgb30_10bit_avx512(r, g, b, a, w, h, s, out, d);
        });
    }

    #[cfg(target_arch = "aarch64")]
    #[test]
    fn test_rgb30_10bit_neon() {
        run_test(|r, g, b, a, w, h, s, out, d| unsafe {
            pack_rgb30_10bit_neon(r, g, b, a, w, h, s, out, d);
        });
    }
}
