use half::f16;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::sync::atomic::{AtomicU32, Ordering};

use crate::buffer::{BufferError, Plane, PlaneDescriptor, PlaneMut, RenderMode, SampleType};
use crate::utils::{render_density_to_plane, shift_int_sample};

#[derive(Clone, Copy)]
struct Mat3 {
    y: [f32; 3],
    u: [f32; 3],
    v: [f32; 3],
}

impl Mat3 {
    fn from_flat_scaled(coeffs: [f32; 9], scale: Option<f32>) -> Self {
        let c = if let Some(s) = scale {
            coeffs.map(|x| x * s)
        } else {
            coeffs
        };
        Self {
            y: [c[0], c[1], c[2]],
            u: [c[3], c[4], c[5]],
            v: [c[6], c[7], c[8]],
        }
    }

    #[inline]
    fn transform(&self, y: f32, u: f32, v: f32) -> [f32; 3] {
        [
            y * self.y[0] + u * self.u[0] + v * self.v[0],
            y * self.y[1] + u * self.u[1] + v * self.v[1],
            y * self.y[2] + u * self.u[2] + v * self.v[2],
        ]
    }
}

#[pyfunction]
#[pyo3(signature = (
    src_yuv,
    dst,
    width,
    height,
    bits,
    sample_type,
    subsampling_w,
    subsampling_h,
    is_limited,
    matrix_coeffs,
    canvas_size,
    render_mode,
    luma_scale
))]
pub(crate) fn compute_vectorscope(
    py: Python<'_>,
    src_yuv: [PlaneDescriptor; 3],
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    bits: u8,
    sample_type: SampleType,
    subsampling_w: u8,
    subsampling_h: u8,
    is_limited: bool,
    matrix_coeffs: [f32; 9],
    canvas_size: usize,
    render_mode: RenderMode,
    luma_scale: f32,
) -> PyResult<()> {
    py.detach(move || {
        Ok(compute_vectorscope_core(
            src_yuv,
            dst,
            width,
            height,
            bits,
            sample_type,
            subsampling_w,
            subsampling_h,
            is_limited,
            matrix_coeffs,
            canvas_size,
            render_mode,
            luma_scale,
        )?)
    })
}

fn compute_vectorscope_core(
    src_yuv: [PlaneDescriptor; 3],
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    bits: u8,
    sample_type: SampleType,
    subsampling_w: u8,
    subsampling_h: u8,
    is_limited: bool,
    matrix_coeffs: [f32; 9],
    canvas_size: usize,
    render_mode: RenderMode,
    luma_scale: f32,
) -> Result<(), BufferError> {
    debug_assert!(canvas_size != 0);

    let h_bits = (canvas_size.saturating_sub(1)).bit_width();

    match sample_type {
        SampleType::Float => {
            // Scale input float values to canvas coordinate space [0, canvas_size - 1].
            let neutral = (canvas_size / 2) as f32;
            let scale_shift = h_bits.cast_signed() - 8;
            let scale_mul = 2.0f32.powi(scale_shift);
            let (scale_c, offset_c) = if is_limited {
                let low = 16.0f32 * scale_mul;
                let peak = 240.0f32 * scale_mul;
                (peak - low, neutral)
            } else {
                let max_v = ((1u32 << h_bits) - 1) as f32;
                (max_v, neutral)
            };

            let (scale_y, offset_y) = if is_limited {
                let low = 16.0f32 * scale_mul;
                let peak = 235.0f32 * scale_mul;
                (peak - low, (canvas_size / 16) as f32)
            } else {
                let max_v = ((1u32 << h_bits) - 1) as f32;
                (max_v, 0.0f32)
            };

            let map_c = move |v: f32| (v * scale_c + offset_c).round() as i32;
            let map_y = move |v: f32| (v * scale_y + offset_y).round() as i32;

            if bits == 16 {
                dispatch_mode::<f16>(
                    src_yuv,
                    dst,
                    width,
                    height,
                    subsampling_w,
                    subsampling_h,
                    canvas_size,
                    render_mode,
                    matrix_coeffs,
                    luma_scale,
                    move |v| map_y(v.to_f32()),
                    move |v| map_c(v.to_f32()),
                )
            } else {
                dispatch_mode::<f32>(
                    src_yuv,
                    dst,
                    width,
                    height,
                    subsampling_w,
                    subsampling_h,
                    canvas_size,
                    render_mode,
                    matrix_coeffs,
                    luma_scale,
                    map_y,
                    map_c,
                )
            }
        }
        SampleType::Integer => {
            // Scale integer samples to canvas bit depth.
            let shift = i32::from(bits) - h_bits.cast_signed();
            let map_sample = move |raw: i32| shift_int_sample(raw, shift);

            if bits > 8 {
                dispatch_mode::<u16>(
                    src_yuv,
                    dst,
                    width,
                    height,
                    subsampling_w,
                    subsampling_h,
                    canvas_size,
                    render_mode,
                    matrix_coeffs,
                    luma_scale,
                    move |v| map_sample(i32::from(v)),
                    move |v| map_sample(i32::from(v)),
                )
            } else {
                dispatch_mode::<u8>(
                    src_yuv,
                    dst,
                    width,
                    height,
                    subsampling_w,
                    subsampling_h,
                    canvas_size,
                    render_mode,
                    matrix_coeffs,
                    luma_scale,
                    move |v| map_sample(i32::from(v)),
                    move |v| map_sample(i32::from(v)),
                )
            }
        }
    }
}

fn dispatch_mode<T: Copy + Sync + Send>(
    src_yuv: [PlaneDescriptor; 3],
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    subsampling_w: u8,
    subsampling_h: u8,
    canvas_size: usize,
    render_mode: RenderMode,
    matrix_coeffs: [f32; 9],
    luma_scale: f32,
    map_luma: impl Fn(T) -> i32 + Sync,
    map_chroma: impl Fn(T) -> i32 + Sync,
) -> Result<(), BufferError> {
    let planes = [
        Plane::<T>::new(src_yuv[0], width << subsampling_w, height << subsampling_h, "src_y")?,
        Plane::<T>::new(src_yuv[1], width, height, "src_u")?,
        Plane::<T>::new(src_yuv[2], width, height, "src_v")?,
    ];

    match render_mode {
        RenderMode::PixelColor => {
            let mut dst_plane = PlaneMut::new(dst, canvas_size, canvas_size, 4, "dst")?;
            render_pixel_color::<T>(
                planes,
                &mut dst_plane,
                width,
                height,
                subsampling_w,
                subsampling_h,
                canvas_size,
                matrix_coeffs,
                map_luma,
                map_chroma,
            );
            Ok(())
        }
        RenderMode::Density => {
            // Density mode: accumulate 2D chroma histogram and render with logarithmic scale
            let [_, u, v] = planes;
            let (counts, max_count) = accumulate_histogram::<T>([u, v], width, height, canvas_size, map_chroma);
            let mut dst_plane = PlaneMut::new(dst, canvas_size, canvas_size, 1, "dst")?;
            render_density_to_plane(&mut dst_plane, canvas_size, &counts, max_count);
            Ok(())
        }
        RenderMode::ChromaWheel => {
            // Chroma wheel mode: accumulate 2D chroma histogram and render over YUV color wheel background
            let [_, u, v] = planes;
            let (counts, max_count) = accumulate_histogram::<T>([u, v], width, height, canvas_size, map_chroma);
            let mut dst_plane = PlaneMut::new(dst, canvas_size, canvas_size, 4, "dst")?;
            render_chroma_wheel(
                &mut dst_plane,
                canvas_size,
                &counts,
                max_count,
                matrix_coeffs,
                luma_scale,
            );
            Ok(())
        }
    }
}

/// Renders pixel color mode.
///
/// In Python, this was:
/// ```python
/// # Map coordinates (size - V, U)
/// y_coords = np.clip(size - yuv_sliced[2], 0, size - 1)
/// x_coords = yuv_sliced[1]
/// # Convert to RGB
/// scale_factor = size / 256.0
/// yuv_flat[0] /= scale_factor
/// yuv_flat[1:] = (yuv_flat[1:] - neutral) / scale_factor
/// rgb = (yuv_flat.T @ self._resolved_matrix.yuv_to_rgb_mat).clip(0, 255).astype(np.uint8)
/// # Draw into canvas
/// grid[y_coords, x_coords, :3] = rgb
/// ```
fn render_pixel_color<T: Copy + Sync + Send>(
    planes: [Plane<'_, T>; 3],
    dst_plane: &mut PlaneMut,
    width: usize,
    height: usize,
    subsampling_w: u8,
    subsampling_h: u8,
    canvas_size: usize,
    matrix_coeffs: [f32; 9],
    map_luma: impl Fn(T) -> i32 + Sync,
    map_chroma: impl Fn(T) -> i32 + Sync,
) {
    let neutral = (canvas_size / 2) as f32;
    let max_coord = i32::try_from(canvas_size - 1).unwrap_or(i32::MAX);
    let inv_scale = 1.0f32 / (canvas_size as f32 / 256.0);
    let mat = Mat3::from_flat_scaled(matrix_coeffs, Some(inv_scale));
    let canvas_len = canvas_size * canvas_size;

    let mut raw_canvas = vec![0u32; canvas_len];
    let canvas = AtomicU32::from_mut_slice(&mut raw_canvas);

    // Iterate over chroma pixels (subsampled luma is aligned via shift)
    (0..height).into_par_iter().for_each(|cy| {
        let ly = cy << subsampling_h;
        let rows: [&[T]; 3] = unsafe { [planes[0].row(ly), planes[1].row(cy), planes[2].row(cy)] };

        for cx in 0..width {
            let lx: usize = cx << subsampling_w;
            let vals = [map_luma(rows[0][lx]), map_chroma(rows[1][cx]), map_chroma(rows[2][cx])]
                .map(|x| x.clamp(0, max_coord));

            // Map coordinates (x is U, y is canvas_size - V)
            let x_coord = vals[1] as usize;
            let y_coord = (max_coord - vals[2]) as usize;

            // Center chroma around neutral (canvas_size / 2)
            let y_flat = vals[0] as f32;
            let u_flat = vals[1] as f32 - neutral;
            let v_flat = vals[2] as f32 - neutral;

            // Matrix multiplication: YUV -> RGB using matrix coeffs
            let [r, g, b] = mat.transform(y_flat, u_flat, v_flat).map(|c| c.clamp(0.0, 255.0) as u8);

            let packed = u32::from_ne_bytes([r, g, b, 255]);
            canvas[y_coord * canvas_size + x_coord].store(packed, Ordering::Relaxed);
        }
    });

    dst_plane
        .par_rows_mut::<u32>()
        .zip(raw_canvas.par_chunks_exact(canvas_size))
        .for_each(|(dst_row, src_row)| {
            dst_row.copy_from_slice(src_row);
        });
}

/// Accumulates a 2D chroma density histogram grid.
///
/// In Python, this was:
/// ```python
/// # Map to 2D grid index (Y is size - V, X is U)
/// y_coords = np.clip(size - yuv_scaled[2].ravel(), 0, size - 1)
/// indices = y_coords * size + yuv_scaled[1].ravel()
/// bins = np.bincount(indices, minlength=(size * size))
/// grid = bins.reshape((size, size))
/// ```
fn accumulate_histogram<T: Copy + Sync + Send>(
    planes: [Plane<'_, T>; 2],
    width: usize,
    height: usize,
    canvas_size: usize,
    map_chroma: impl Fn(T) -> i32 + Sync,
) -> (std::vec::Vec<u32>, u32) {
    let max_coord = i32::try_from(canvas_size - 1).unwrap_or(i32::MAX);
    let canvas_len = canvas_size * canvas_size;
    let counts = (0..canvas_len).map(|_| AtomicU32::new(0)).collect::<Vec<_>>();

    (0..height).into_par_iter().for_each(|cy| {
        let rows = planes.map(|p| unsafe { p.row(cy) });

        for cx in 0..width {
            let [u_s, v_s] = rows.map(|row| map_chroma(row[cx]));

            let x_coord = u_s.clamp(0, max_coord) as usize;
            let y_coord = (max_coord - v_s.clamp(0, max_coord)) as usize;
            counts[y_coord * canvas_size + x_coord].fetch_add(1, Ordering::Relaxed);
        }
    });

    let counts_u32 = counts.into_par_iter().map(AtomicU32::into_inner).collect::<Vec<_>>();
    let max_count = counts_u32.par_iter().copied().max().unwrap_or(0);
    (counts_u32, max_count)
}

/// Renders chroma wheel mode.
///
/// In Python, this was:
/// ```python
/// # Color Mode: Density map over a high-resolution YUV color wheel background
/// scale_factor = size / 256.0
/// u_val = (x_idx.astype(np.float32) - neutral) / scale_factor
/// v_val = (np.clip(size - y_idx, 0, size - 1).astype(np.float32) - neutral) / scale_factor
///
/// # Use fixed moderate luma for accurate hue, then scale by density for brightness
/// luma = np.full_like(density, self.settings.global_.vectorscope.luma * 128)
/// base_rgb = (np.column_stack([luma, u_val, v_val]) @ self._resolved_matrix.yuv_to_rgb_mat).clip(0, 255)
/// colored = (base_rgb * density[:, np.newaxis]).clip(0, 255).astype(np.uint8)
/// ```
fn render_chroma_wheel(
    dst_plane: &mut PlaneMut,
    canvas_size: usize,
    counts: &[u32],
    max_count: u32,
    matrix_coeffs: [f32; 9],
    luma_scale: f32,
) {
    let scale_factor = canvas_size as f32 / 256.0;
    let inv_scale = 1.0f32 / scale_factor;
    let neutral = (canvas_size / 2) as f32;
    let inv_log_max = if max_count > 0 {
        1.0f32 / (max_count as f32).ln_1p()
    } else {
        0.0f32
    };
    let mat = Mat3::from_flat_scaled(matrix_coeffs, None);
    let luma_val = luma_scale * 128.0;

    dst_plane
        .par_rows_mut::<[u8; 4]>()
        .zip(counts.par_chunks_exact(canvas_size))
        .enumerate()
        .for_each(|(y_idx, (dst_row, count_row))| {
            let v_val = (neutral - y_idx as f32) * inv_scale;

            for (x_idx, (dst_px, &count)) in dst_row.iter_mut().zip(count_row).enumerate() {
                if count > 0 {
                    let density = ((count as f32).ln_1p() * inv_log_max).min(1.0);
                    let u_val = (x_idx as f32 - neutral) * inv_scale;

                    let rgb = mat.transform(luma_val, u_val, v_val);
                    let [r, g, b] = rgb.map(|c| (c * density).clamp(0.0, 255.0) as u8);

                    *dst_px = [r, g, b, 255];
                } else {
                    *dst_px = [0, 0, 0, 0];
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_vectorscope_modes_run() {
        let width = 32;
        let height = 32;
        let canvas_size = 64;

        let y_data = vec![128u8; width * height];
        let u_data = vec![128u8; width * height];
        let v_data = vec![128u8; width * height];
        let src_yuv: [PlaneDescriptor; 3] = [
            (y_data.as_ptr() as usize, width).into(),
            (u_data.as_ptr() as usize, width).into(),
            (v_data.as_ptr() as usize, width).into(),
        ];
        let coeffs = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];

        // Density
        let mut dst_density = vec![0u8; canvas_size * canvas_size];
        compute_vectorscope_core(
            src_yuv,
            (dst_density.as_mut_ptr() as usize, canvas_size).into(),
            width,
            height,
            8,
            SampleType::Integer,
            0,
            0,
            false,
            coeffs,
            canvas_size,
            RenderMode::Density,
            1.0,
        )
        .expect("Density mode");
        assert!(dst_density.iter().any(|&x| x > 0));

        // ChromaWheel
        let mut dst_chroma = vec![0u8; canvas_size * canvas_size * 4];
        compute_vectorscope_core(
            src_yuv,
            (dst_chroma.as_mut_ptr() as usize, canvas_size * 4).into(),
            width,
            height,
            8,
            SampleType::Integer,
            0,
            0,
            false,
            coeffs,
            canvas_size,
            RenderMode::ChromaWheel,
            1.0,
        )
        .expect("ChromaWheel mode");
        assert!(dst_chroma.iter().any(|&x| x > 0));

        // PixelColor
        let mut dst_pixel = vec![0u8; canvas_size * canvas_size * 4];
        compute_vectorscope_core(
            src_yuv,
            (dst_pixel.as_mut_ptr() as usize, canvas_size * 4).into(),
            width,
            height,
            8,
            SampleType::Integer,
            0,
            0,
            false,
            coeffs,
            canvas_size,
            RenderMode::PixelColor,
            1.0,
        )
        .expect("PixelColor mode");
        assert!(dst_pixel.iter().any(|&x| x > 0));
    }

    #[test]
    fn test_vectorscope_f16_and_subsampled() {
        let width = 32;
        let height = 16;
        let canvas_size = 64;

        let y_data = vec![f16::from_f32(0.5); width * 2 * height * 2];
        let u_data = vec![f16::from_f32(0.1); width * height];
        let v_data = vec![f16::from_f32(-0.1); width * height];

        let src_yuv: [PlaneDescriptor; 3] = [
            (y_data.as_ptr() as usize, width * 2 * size_of::<f16>()).into(),
            (u_data.as_ptr() as usize, width * size_of::<f16>()).into(),
            (v_data.as_ptr() as usize, width * size_of::<f16>()).into(),
        ];

        let mut dst = vec![0u8; canvas_size * canvas_size * 4];
        compute_vectorscope_core(
            src_yuv,
            (dst.as_mut_ptr() as usize, canvas_size * 4).into(),
            width,
            height,
            16,
            SampleType::Float,
            1,
            1,
            false,
            [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            canvas_size,
            RenderMode::PixelColor,
            1.0,
        )
        .expect("f16 subsampled 4:2:0 PixelColor");

        assert!(dst.iter().any(|&x| x > 0));
    }

    #[test]
    fn test_vectorscope_f32_and_u16_limited() {
        let width = 16;
        let height = 16;
        let canvas_size = 32;

        let y_f32 = vec![0.5f32; width * height];
        let u_f32 = vec![0.0f32; width * height];
        let v_f32 = vec![0.0f32; width * height];
        let src_f32: [PlaneDescriptor; 3] = [
            (y_f32.as_ptr() as usize, width * 4).into(),
            (u_f32.as_ptr() as usize, width * 4).into(),
            (v_f32.as_ptr() as usize, width * 4).into(),
        ];
        let mut dst_f32 = vec![0u8; canvas_size * canvas_size * 4];

        compute_vectorscope_core(
            src_f32,
            (dst_f32.as_mut_ptr() as usize, canvas_size * 4).into(),
            width,
            height,
            32,
            SampleType::Float,
            0,
            0,
            true,
            [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            canvas_size,
            RenderMode::PixelColor,
            1.0,
        )
        .expect("f32 limited vectorscope");
        assert!(dst_f32.iter().any(|&x| x > 0));

        let y_10 = vec![512u16; width * height];
        let u_10 = vec![512u16; width * height];
        let v_10 = vec![512u16; width * height];
        let src_10: [PlaneDescriptor; 3] = [
            (y_10.as_ptr() as usize, width * 2).into(),
            (u_10.as_ptr() as usize, width * 2).into(),
            (v_10.as_ptr() as usize, width * 2).into(),
        ];
        let mut dst_10 = vec![0u8; canvas_size * canvas_size];

        compute_vectorscope_core(
            src_10,
            (dst_10.as_mut_ptr() as usize, canvas_size).into(),
            width,
            height,
            10,
            SampleType::Integer,
            0,
            0,
            true,
            [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            canvas_size,
            RenderMode::Density,
            1.0,
        )
        .expect("10-bit integer limited vectorscope");
        assert!(dst_10.iter().any(|&x| x > 0));
    }
}
