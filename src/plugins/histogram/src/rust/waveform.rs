use half::f16;
use pyo3::prelude::*;
use rayon::prelude::*;

use crate::buffer::{BufferError, Plane, PlaneDescriptor, PlaneMut, SampleType};
use crate::utils::shift_int_sample;

/// Computes waveform intensity bins for a video plane.
#[pyfunction]
#[pyo3(signature = (
    src,
    dst,
    width,
    height,
    bits,
    sample_type,
    target_w,
    target_h,
    is_chroma,
    is_limited,
    gain,
    dynamic_gain
))]
pub(crate) fn compute_waveform(
    py: Python<'_>,
    src: PlaneDescriptor,
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    bits: u8,
    sample_type: SampleType,
    target_w: usize,
    target_h: usize,
    is_chroma: bool,
    is_limited: bool,
    gain: f32,
    dynamic_gain: bool,
) -> PyResult<()> {
    py.detach(move || {
        Ok(compute_waveform_core(
            src,
            dst,
            width,
            height,
            bits,
            sample_type,
            target_w,
            target_h,
            is_chroma,
            is_limited,
            gain,
            dynamic_gain,
        )?)
    })
}

fn compute_waveform_core(
    src: PlaneDescriptor,
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    bits: u8,
    sample_type: SampleType,
    target_w: usize,
    target_h: usize,
    is_chroma: bool,
    is_limited: bool,
    gain: f32,
    dynamic_gain: bool,
) -> Result<(), BufferError> {
    debug_assert!(target_w != 0);
    debug_assert!(target_h != 0);

    let mut dst_plane = PlaneMut::new(dst, target_w, target_h, 1, "dst")?;
    let mut raw_grid = vec![0u32; target_h * target_w];
    let step = (width / target_w).max(1);
    let h_bits = (target_h.saturating_sub(1)).bit_width();
    let scale_shift = h_bits.cast_signed() - 8;
    let scale_mul = 2.0f32.powi(scale_shift);

    match sample_type {
        SampleType::Float => {
            // Scale input float plane array to target range [0, target_h - 1].
            // In Python, this was:
            //   output_peak = get_peak_value(bits, chroma, color_range, fmt.color_family)
            //   output_lowest = get_lowest_value(bits, chroma, color_range, fmt.color_family)
            //   arr_scaled = arr * (output_peak - output_lowest) + (128 << (bits - 8) if chroma else ...)
            let (scale, offset) = if is_chroma {
                if is_limited {
                    let low = 16.0f32 * scale_mul;
                    let peak = 240.0f32 * scale_mul;
                    (peak - low, 128.0f32 * scale_mul)
                } else {
                    let max_v = ((1u32 << h_bits) - 1) as f32;
                    (max_v, 128.0f32 * scale_mul)
                }
            } else if is_limited {
                let low = 16.0f32 * scale_mul;
                let peak = 235.0f32 * scale_mul;
                (peak - low, 16.0f32 * scale_mul)
            } else {
                let max_v = ((1u32 << h_bits) - 1) as f32;
                (max_v, 0.0f32)
            };

            let max_bin = target_h.saturating_sub(1).cast_signed();
            let to_bin = |v: f32| ((v * scale + offset).round() as isize).clamp(0, max_bin) as usize;

            if bits == 16 {
                fill_grid(src, width, height, step, target_h, &mut raw_grid, |v: f16| {
                    to_bin(v.to_f32())
                })?;
            } else {
                fill_grid(src, width, height, step, target_h, &mut raw_grid, to_bin)?;
            }
        }
        SampleType::Integer => {
            // Scale integer samples to target height bit depth.
            // In Python:
            //   shift = fmt.bits_per_sample - bits
            //   arr = (arr + (1 << (shift - 1))) >> shift (if shift > 0)
            let shift = i32::from(bits) - h_bits.cast_signed();
            let max_bin = i32::try_from(target_h.saturating_sub(1)).unwrap_or(i32::MAX);
            let to_bin = |raw: i32| shift_int_sample(raw, shift).clamp(0, max_bin) as usize;

            if bits > 8 {
                fill_grid(src, width, height, step, target_h, &mut raw_grid, |v: u16| {
                    to_bin(v.into())
                })?;
            } else {
                fill_grid(src, width, height, step, target_h, &mut raw_grid, |v: u8| {
                    to_bin(v.into())
                })?;
            }
        }
    }

    // Logarithmic density scale (0 to 255 index range).
    // In Python, this was:
    //   if self.settings.global_.waveform.dynamic_gain:
    //       scale = 255.0 / np.log1p(max_val) if (max_val := grid.max()) > 0 else 0.0
    //   else:
    //       # Static gain reference is the height of the frame
    //       scale = 255.0 / np.log1p(h)
    //   grid = np.log1p(grid) * scale * self.settings.global_.waveform.gain
    let max_count = raw_grid.par_iter().copied().max().unwrap_or(0);

    let scale = if dynamic_gain && max_count > 0 {
        255.0f32 / (max_count as f32).ln_1p()
    } else {
        255.0f32 / (height as f32).ln_1p()
    };

    let mult = scale * gain;

    if mult <= 0.0 || max_count == 0 {
        dst_plane.par_rows_mut::<u8>().for_each(|dst_row| {
            dst_row[..target_w].fill(0);
        });
        return Ok(());
    }

    // Precompute logarithmic response LUT for count values: lut[count] = ln_1p(count) * mult
    let mut lut = vec![0u8; (max_count as usize) + 1];
    for (c, entry) in lut.iter_mut().enumerate().skip(1) {
        *entry = ((c as f32).ln_1p() * mult).round().clamp(0.0, 255.0) as u8;
    }

    // Flip vertically so value target_h-1 is top (row 0), 0 is bottom (row target_h-1).
    // In Python: grid = np.flipud(grid)
    dst_plane.par_rows_mut::<u8>().enumerate().for_each(|(dst_y, dst_row)| {
        let bin_idx = (target_h - 1) - dst_y;

        for (col_idx, dst_pixel) in dst_row[..target_w].iter_mut().enumerate() {
            let count = raw_grid[col_idx * target_h + bin_idx] as usize;
            *dst_pixel = if count > 0 { lut[count] } else { 0 };
        }
    });

    Ok(())
}

/// Column-wise bin accumulation.
///
/// In Python, this was:
/// ```python
/// cols = np.tile(np.arange(actual_w, dtype=np.int32), (h, 1))
/// grid = np.bincount((arr_down * actual_w + cols).ravel(), minlength=target_h * actual_w).reshape((target_h, actual_w))
/// ```
fn fill_grid<T: Copy + Sync>(
    src: PlaneDescriptor,
    width: usize,
    height: usize,
    step: usize,
    target_h: usize,
    raw_grid: &mut [u32],
    sample_to_bin: impl Fn(T) -> usize + Sync,
) -> Result<(), BufferError> {
    let src_plane = Plane::<T>::new(src, width, height, "src")?;

    raw_grid
        .par_chunks_exact_mut(target_h)
        .enumerate()
        .for_each(|(col_idx, col_bins)| {
            let x = col_idx * step;
            for y in 0..height {
                let sample = unsafe { src_plane.row(y)[x] };
                let bin = sample_to_bin(sample);
                col_bins[bin] += 1;
            }
        });

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_waveform_small_target_h_no_panic() {
        let width = 64;
        let height = 32;
        let target_w = 64;
        let target_h = 100;
        let src_data = vec![128u8; width * height];
        let mut dst_data = vec![0u8; target_w * target_h];

        let src: PlaneDescriptor = (src_data.as_ptr() as usize, width).into();
        let dst: PlaneDescriptor = (dst_data.as_mut_ptr() as usize, target_w).into();

        compute_waveform_core(
            src,
            dst,
            width,
            height,
            8,
            SampleType::Integer,
            target_w,
            target_h,
            false,
            false,
            1.0,
            false,
        )
        .expect("compute_waveform_core should succeed without panic");

        assert_eq!(*dst_data.iter().max().unwrap(), 255);
    }

    #[test]
    fn test_waveform_float_f16_and_f32_dynamic_gain() {
        let width = 32;
        let height = 16;
        let target_w = 32;
        let target_h = 64;
        let src_f32 = vec![0.5f32; width * height];
        let src_f16: Vec<f16> = src_f32.iter().map(|&v| f16::from_f32(v)).collect();

        let mut dst_f32 = vec![0u8; target_w * target_h];
        let mut dst_f16 = vec![0u8; target_w * target_h];

        compute_waveform_core(
            (src_f32.as_ptr() as usize, width * 4).into(),
            (dst_f32.as_mut_ptr() as usize, target_w).into(),
            width,
            height,
            32,
            SampleType::Float,
            target_w,
            target_h,
            true,
            true,
            1.0,
            true,
        )
        .expect("f32 waveform dynamic gain limited chroma");

        compute_waveform_core(
            (src_f16.as_ptr() as usize, width * 2).into(),
            (dst_f16.as_mut_ptr() as usize, target_w).into(),
            width,
            height,
            16,
            SampleType::Float,
            target_w,
            target_h,
            true,
            true,
            1.0,
            true,
        )
        .expect("f16 waveform dynamic gain limited chroma");

        assert_eq!(*dst_f32.iter().max().unwrap(), 255);
        assert_eq!(dst_f32, dst_f16);
    }

    #[test]
    fn test_waveform_integer_10bit() {
        let width = 32;
        let height = 16;
        let target_w = 32;
        let target_h = 64;
        let src_10 = vec![512u16; width * height];
        let mut dst = vec![0u8; target_w * target_h];

        compute_waveform_core(
            (src_10.as_ptr() as usize, width * 2).into(),
            (dst.as_mut_ptr() as usize, target_w).into(),
            width,
            height,
            10,
            SampleType::Integer,
            target_w,
            target_h,
            false,
            false,
            1.0,
            false,
        )
        .expect("10-bit integer waveform");

        assert_eq!(*dst.iter().max().unwrap(), 255);
    }

    #[test]
    fn test_waveform_dynamic_gain_zero_height() {
        let width = 16;
        let height = 0;
        let target_w = 16;
        let target_h = 32;
        let src = [0u8; 16];
        let mut dst = vec![255u8; target_w * target_h];

        compute_waveform_core(
            (src.as_ptr() as usize, width).into(),
            (dst.as_mut_ptr() as usize, target_w).into(),
            width,
            height,
            8,
            SampleType::Integer,
            target_w,
            target_h,
            false,
            false,
            1.0,
            true,
        )
        .expect("empty waveform with dynamic gain");

        assert!(dst.iter().all(|&x| x == 0));
    }
}
