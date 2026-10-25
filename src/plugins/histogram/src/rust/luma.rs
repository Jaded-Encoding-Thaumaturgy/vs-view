//! Luma false-color / sawtooth / triangle wave computation backend.

use half::f16;
use pyo3::prelude::*;
use rayon::prelude::*;

use crate::buffer::{BufferError, Plane, PlaneDescriptor, PlaneMut, SampleType};

/// Computes luma sawtooth/triangle wave visualization.
///
/// Called from Python: `vsview_histogram.luma.ui.LumaView.modify_frame_func`.
#[pyfunction]
#[pyo3(signature = (src, dst, width, height, bits, sample_type, shift_in, use_sawtooth, is_limited))]
pub(crate) fn compute_luma(
    py: Python<'_>,
    src: PlaneDescriptor,
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    bits: u8,
    sample_type: SampleType,
    shift_in: u8,
    use_sawtooth: bool,
    is_limited: bool,
) -> PyResult<()> {
    py.detach(move || {
        Ok(compute_luma_core(
            src,
            dst,
            width,
            height,
            bits,
            sample_type,
            shift_in,
            use_sawtooth,
            is_limited,
        )?)
    })
}

fn compute_luma_core(
    src: PlaneDescriptor,
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    bits: u8,
    sample_type: SampleType,
    shift_in: u8,
    use_sawtooth: bool,
    is_limited: bool,
) -> Result<(), BufferError> {
    let mut dst_plane = PlaneMut::new(dst, width, height, 1, "dst")?;

    match sample_type {
        SampleType::Float => {
            let (scale, offset) = if is_limited {
                (56064.0f32 / 65535.0f32, 4096.0f32 / 65535.0f32)
            } else {
                (1.0f32, 0.0f32)
            };
            let to_p = move |val: f32| ((val * scale + offset) * 65535.0).clamp(0.0, 65535.0).round() as u32;

            if bits == 16 {
                process_luma_rows::<true, f16, _>(
                    src,
                    &mut dst_plane,
                    width,
                    height,
                    shift_in,
                    8,
                    65535,
                    65536,
                    use_sawtooth,
                    |v: f16| to_p(v.to_f32()),
                )
            } else {
                process_luma_rows::<true, f32, _>(
                    src,
                    &mut dst_plane,
                    width,
                    height,
                    shift_in,
                    8,
                    65535,
                    65536,
                    use_sawtooth,
                    to_p,
                )
            }
        }
        SampleType::Integer => {
            let max_val = (1u32 << bits) - 1;
            let shift_out = u32::from(bits).saturating_sub(8);
            let modulo_limit = max_val + 1;

            if bits > 8 {
                process_luma_rows::<false, u16, _>(
                    src,
                    &mut dst_plane,
                    width,
                    height,
                    shift_in,
                    shift_out,
                    max_val,
                    modulo_limit,
                    use_sawtooth,
                    u32::from,
                )
            } else {
                process_luma_rows::<false, u8, _>(
                    src,
                    &mut dst_plane,
                    width,
                    height,
                    shift_in,
                    shift_out,
                    max_val,
                    modulo_limit,
                    use_sawtooth,
                    u32::from,
                )
            }
        }
    }
}

/// Applies sawtooth or triangle wave transformation sequentially across image rows.
///
/// In Python (`process_luma_numba`):
/// ```python
/// if use_sawtooth:
///     p_shifted = p << shift_in
///     val = p_shifted & max_val
///     dst[y, x] = val >> shift_out
/// else:
///     p_shifted = p << shift_in
///     p_masked = p_shifted & max_val
///     val = max_val - p_masked if (p_shifted & modulo_limit) else p_masked
///     dst[y, x] = val >> shift_out
/// ```
fn process_luma_rows<const PARALLEL: bool, T: Copy + Sync + Send, F: Fn(T) -> u32 + Sync + Send>(
    src: PlaneDescriptor,
    dst_plane: &mut PlaneMut,
    width: usize,
    height: usize,
    shift_in: u8,
    shift_out: u32,
    max_val: u32,
    modulo_limit: u32,
    use_sawtooth: bool,
    to_p: F,
) -> Result<(), BufferError> {
    let src_plane = Plane::<T>::new(src, width, height, "src")?;

    let process_row = |dst_row: &mut [u8], src_row: &[T]| {
        // let src_row = unsafe { src_plane.row(y) };
        if use_sawtooth {
            // Sawtooth mode: wrap around on overflow
            for (&src_val, dst_val) in src_row.iter().zip(dst_row.iter_mut()) {
                let p = to_p(src_val);
                *dst_val = (((p << shift_in) & max_val) >> shift_out) as u8;
            }
        } else {
            // Triangle mode: fold back on overflow when (p_shifted & modulo_limit) != 0
            for (&src_val, dst_val) in src_row.iter().zip(dst_row.iter_mut()) {
                let p = to_p(src_val);
                let p_shifted = p << shift_in;
                let p_masked = p_shifted & max_val;
                let val = if (p_shifted & modulo_limit) != 0 {
                    max_val - p_masked
                } else {
                    p_masked
                };
                *dst_val = (val >> shift_out) as u8;
            }
        }
    };

    if PARALLEL {
        dst_plane
            .par_rows_mut::<u8>()
            .enumerate()
            .for_each(|(y, dst_row)| process_row(dst_row, unsafe { src_plane.row(y) }));
    } else {
        for (y, dst_row) in dst_plane.rows_mut::<u8>().enumerate() {
            process_row(dst_row, unsafe { src_plane.row(y) });
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_transform_8bit() {
        let max_val = 255u32;
        let modulo_limit = 256u32;
        // Sawtooth: (128 << 0) & 255 = 128
        let st_val = 128u32 & max_val;
        assert_eq!(st_val as u8, 128);

        // Triangle: 100 << 1 = 200 (< 256) => 200
        let p_sh = 100u32 << 1;
        let p_m = p_sh & max_val;
        let tri = if (p_sh & modulo_limit) != 0 { max_val - p_m } else { p_m };
        assert_eq!(tri as u8, 200);

        // Triangle: 150 << 1 = 300 (>= 256) => 255 - (300 & 255) = 211
        let p_sh2 = 150u32 << 1;
        let p_m2 = p_sh2 & max_val;
        let tri2 = if (p_sh2 & modulo_limit) != 0 {
            max_val - p_m2
        } else {
            p_m2
        };
        assert_eq!(tri2 as u8, 211);
    }

    #[test]
    fn test_compute_luma_core_integer_8bit_and_10bit() {
        let width = 8;
        let height = 4;
        let src_8 = vec![128u8; width * height];
        let mut dst_8 = vec![0u8; width * height];
        compute_luma_core(
            (src_8.as_ptr() as usize, width).into(),
            (dst_8.as_mut_ptr() as usize, width).into(),
            width,
            height,
            8,
            SampleType::Integer,
            0,
            true,
            false,
        )
        .expect("8-bit integer luma");
        assert_eq!(dst_8[0], 128);

        let src_10 = vec![512u16; width * height];
        let mut dst_10 = vec![0u8; width * height];
        compute_luma_core(
            (src_10.as_ptr() as usize, width * 2).into(),
            (dst_10.as_mut_ptr() as usize, width).into(),
            width,
            height,
            10,
            SampleType::Integer,
            0,
            false,
            false,
        )
        .expect("10-bit integer luma");
        assert_eq!(dst_10[0], 128);
    }

    #[test]
    fn test_compute_luma_core_f16_and_f32() {
        let width = 16;
        let height = 8;
        let src_f32: Vec<f32> = (0..width * height)
            .map(|i| i as f32 / (width * height) as f32)
            .collect();
        let src_f16: Vec<f16> = src_f32.iter().map(|&v| f16::from_f32(v)).collect();

        let mut dst_f32 = vec![0u8; width * height];
        let mut dst_f16 = vec![0u8; width * height];

        compute_luma_core(
            (src_f32.as_ptr() as usize, width * 4).into(),
            (dst_f32.as_mut_ptr() as usize, width).into(),
            width,
            height,
            32,
            SampleType::Float,
            1,
            true,
            true,
        )
        .expect("f32 luma");

        compute_luma_core(
            (src_f16.as_ptr() as usize, width * 2).into(),
            (dst_f16.as_mut_ptr() as usize, width).into(),
            width,
            height,
            16,
            SampleType::Float,
            1,
            true,
            true,
        )
        .expect("f16 luma");

        assert_eq!(dst_f32, dst_f16);
    }
}
