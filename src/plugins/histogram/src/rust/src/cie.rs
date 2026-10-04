use pyo3::prelude::*;
use rayon::prelude::*;
use std::sync::atomic::{AtomicU32, Ordering};

use crate::buffer::{BufferError, CieMode, Plane, PlaneDescriptor, PlaneMut, RenderMode};
use crate::utils::render_density_to_plane;

// Maximum chromaticity coordinate values displayed on axes (matches MAX_VAL_X / MAX_VAL_Y in cie/ui.py)
const MAX_VAL_X: f32 = 0.85;
const MAX_VAL_Y: f32 = 0.85;
const GAMMA: f32 = 1.0 / 2.2;

/// Projects XYZ color coordinates to canvas pixel indices `(y_pixel * canvas_size + x_pixel)`.
///
/// Corresponds to coordinate calculation:
/// - CIE 1931: `x = X / (X + Y + Z)`, `y = Y / (X + Y + Z)`
/// - CIE 1976: `u' = 4X / (X + 15Y + 3Z)`, `v' = 9Y / (X + 15Y + 3Z)`
/// - Map to canvas: `x_pixel = (coord_x / MAX_VAL_X) * size`, `y_pixel = (1.0 - (coord_y / MAX_VAL_Y)) * size`
#[derive(Clone, Copy)]
struct CieProjector {
    k_y: f32,
    k_z: f32,
    coeff_x: f32,
    coeff_y: f32,
    size_f: f32,
    max_coord_f: f32,
    canvas_size: usize,
}

impl CieProjector {
    fn new(mode: CieMode, canvas_size: usize) -> Self {
        let size_f = canvas_size as f32;
        let scale_x = size_f / MAX_VAL_X;
        let scale_y = size_f / MAX_VAL_Y;

        let (k_y, k_z, num_x, num_y) = match mode {
            CieMode::Cie1931 => (1.0, 1.0, 1.0, 1.0),
            CieMode::Cie1976 => (15.0, 3.0, 4.0, 9.0),
        };

        Self {
            k_y,
            k_z,
            coeff_x: num_x * scale_x,
            coeff_y: num_y * scale_y,
            size_f,
            max_coord_f: size_f - 1.0,
            canvas_size,
        }
    }

    /// Calculate target coordinates and map to size-based pixel grid coordinates.
    ///
    /// In Python, this was:
    /// ```python
    /// denom = (x + k_y * y + k_z * z).clip(1e-6, None)
    /// x_coord = num_x * x / denom
    /// y_coord = num_y * y / denom
    /// x_pixel = ((x_coord / MAX_VAL_X) * size).clip(0.0, size - 1.0).astype(np.int32)
    /// y_pixel = ((1.0 - (y_coord / MAX_VAL_Y)) * size).clip(0.0, size - 1.0).astype(np.int32)
    /// indices = y_pixel * size + x_pixel
    /// ```
    fn project_to_idx(&self, px: f32, py: f32, pz: f32) -> usize {
        let inv_denom = (px + self.k_y * py + self.k_z * pz).max(1e-6).recip();
        let x_pixel = (self.coeff_x * px * inv_denom).clamp(0.0, self.max_coord_f) as usize;
        let y_pixel = (self.size_f - self.coeff_y * py * inv_denom).clamp(0.0, self.max_coord_f) as usize;
        y_pixel * self.canvas_size + x_pixel
    }
}

/// Accumulates linear RGB colors and pixel counts per canvas bin for `RenderMode::PixelColor`.
///
/// In Python, this was:
/// ```python
/// rgb_sum = np.stack([np.bincount(indices, weights=p * 255.0, minlength=(size * size)) for p in lrgb_sliced_clipped])
/// ```
#[derive(Clone, Copy, Default)]
struct ColorAccumulator {
    count: u32,
    rgb: [f32; 3],
}

impl ColorAccumulator {
    fn add(&mut self, rgb: [f32; 3]) {
        self.count += 1;
        for (acc, c) in self.rgb.iter_mut().zip(rgb) {
            *acc += c.clamp(0.0, 1.0);
        }
    }

    fn max_ch(&self) -> f32 {
        self.rgb[0].max(self.rgb[1]).max(self.rgb[2])
    }
}

#[pyfunction]
#[pyo3(signature = (
    src_xyz,
    dst,
    width,
    height,
    canvas_size,
    cie_mode,
    render_mode,
    luma_scale,
    linear_rgb = None
))]
pub(crate) fn compute_cie(
    py: Python<'_>,
    src_xyz: [PlaneDescriptor; 3],
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    canvas_size: usize,
    cie_mode: CieMode,
    render_mode: RenderMode,
    luma_scale: f32,
    linear_rgb: Option<[PlaneDescriptor; 3]>,
) -> PyResult<()> {
    py.detach(move || {
        Ok(compute_cie_core(
            src_xyz,
            dst,
            width,
            height,
            canvas_size,
            cie_mode,
            render_mode,
            luma_scale,
            linear_rgb,
        )?)
    })
}

fn compute_cie_core(
    src_xyz: [PlaneDescriptor; 3],
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    canvas_size: usize,
    cie_mode: CieMode,
    render_mode: RenderMode,
    luma_scale: f32,
    linear_rgb: Option<[PlaneDescriptor; 3]>,
) -> Result<(), BufferError> {
    debug_assert!(canvas_size != 0);

    let src = [
        Plane::<f32>::new(src_xyz[0], width, height, "src_x")?,
        Plane::<f32>::new(src_xyz[1], width, height, "src_y")?,
        Plane::<f32>::new(src_xyz[2], width, height, "src_z")?,
    ];

    let size = canvas_size;
    let proj = CieProjector::new(cie_mode, size);

    match render_mode {
        RenderMode::PixelColor => render_pixel_color(&src, dst, width, height, size, proj, luma_scale, linear_rgb),
        RenderMode::ChromaWheel => render_chroma_wheel(&src, dst, width, height, size, proj, luma_scale),
        RenderMode::Density => {
            let (counts, max_count) = accumulate_counts(&src, width, height, size, proj);
            let mut dst_plane = PlaneMut::new(dst, size, size, 1, "dst")?;
            render_density_to_plane(&mut dst_plane, size, &counts, max_count);
            Ok(())
        }
    }
}

/// Renders pixel color mode.
///
/// In Python, this was:
/// ```python
/// # Extract and normalize original colors in linear space
/// # Accumulate linear R, G, B colors of the pixels falling into each bin
/// # Normalize to [0, 1], apply Gamma 2.2 correction on the size^2 canvas,
/// # and scale to 255.0 (counts cancel out during division)
/// # Scale pixel brightness by density log-scale:
/// # density_scale is in range [0.3, 1.0] for populated bins to ensure visibility
/// ```
fn render_pixel_color(
    src: &[Plane<'_, f32>; 3],
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    canvas_size: usize,
    projector: CieProjector,
    luma_scale: f32,
    linear_rgb: Option<[PlaneDescriptor; 3]>,
) -> Result<(), BufferError> {
    let Some(linear_rgb) = linear_rgb else {
        return Err(BufferError::MissingPlanes { label: "linear_rgb" });
    };

    // Extract original colors in linear space
    let linear = [
        Plane::<f32>::new(linear_rgb[0], width, height, "linear_r")?,
        Plane::<f32>::new(linear_rgb[1], width, height, "linear_g")?,
        Plane::<f32>::new(linear_rgb[2], width, height, "linear_b")?,
    ];
    let mut dst_plane = PlaneMut::new(dst, canvas_size, canvas_size, 4, "dst")?;
    let mut accumulated = vec![ColorAccumulator::default(); canvas_size * canvas_size];

    // Accumulate linear R, G, B colors of the pixels falling into each canvas bin
    for y in 0..height {
        let (sx, sy, sz, lr, lg, lb) = unsafe {
            (
                src[0].row(y),
                src[1].row(y),
                src[2].row(y),
                linear[0].row(y),
                linear[1].row(y),
                linear[2].row(y),
            )
        };

        for x in 0..width {
            let idx = projector.project_to_idx(sx[x], sy[x], sz[x]);
            accumulated[idx].add([lr[x], lg[x], lb[x]]);
        }
    }

    // Scale pixel brightness by density log-scale
    let max_count = accumulated.par_iter().map(|acc| acc.count).max().unwrap_or(0);
    let log_max_count = (max_count as f32).ln_1p();
    let inv_log_max_count = if log_max_count > 0.0 { 1.0 / log_max_count } else { 0.0 };

    // Normalize to [0, 1], apply Gamma 2.2 correction, apply density scale and luma factor
    dst_plane
        .par_rows_mut::<[u8; 4]>()
        .zip(accumulated.par_chunks_exact(canvas_size))
        .for_each(|(dst_row, acc_row)| {
            for (dst_px, acc) in dst_row.iter_mut().zip(acc_row) {
                if acc.count > 0 {
                    let max_ch = acc.max_ch();
                    let inv_max = if max_ch > 0.0 { max_ch.recip() } else { 1.0 };

                    // density_scale is in range [0.3, 1.0] for populated bins to ensure visibility
                    let density_scale = if log_max_count > 0.0 {
                        0.3 + 0.7 * ((acc.count as f32).ln_1p() * inv_log_max_count)
                    } else {
                        1.0
                    };

                    let mult = density_scale * luma_scale * 255.0;
                    let [r, g, b] = acc
                        .rgb
                        .map(|c| ((c * inv_max).powf(GAMMA) * mult).clamp(0.0, 255.0) as u8);
                    *dst_px = [r, g, b, 255];
                } else {
                    *dst_px = [0, 0, 0, 0];
                }
            }
        });

    Ok(())
}

/// Renders chroma wheel mode.
///
/// In Python, this was:
/// ```python
/// scale = 255.0 / np.log1p(max_count)
/// density_val = (np.log1p(counts_2d) * scale * luma).clip(0, 255).astype(np.uint8)
/// rgba[..., :3] = 255  # White glow
/// rgba[..., 3] = density_val
/// ```
fn render_chroma_wheel(
    src: &[Plane<'_, f32>; 3],
    dst: PlaneDescriptor,
    width: usize,
    height: usize,
    canvas_size: usize,
    projector: CieProjector,
    luma_scale: f32,
) -> Result<(), BufferError> {
    let (counts, max_count) = accumulate_counts(src, width, height, canvas_size, projector);
    let scale = if max_count > 0 {
        (255.0f32 * luma_scale) / (max_count as f32).ln_1p()
    } else {
        0.0f32
    };

    let mut dst_plane = PlaneMut::new(dst, canvas_size, canvas_size, 4, "dst")?;

    dst_plane
        .par_rows_mut::<[u8; 4]>()
        .zip(counts.par_chunks_exact(canvas_size))
        .for_each(|(dst_row, count_row)| {
            for (dst_px, &count) in dst_row.iter_mut().zip(count_row) {
                if count > 0 {
                    let density_val = ((count as f32).ln_1p() * scale).round().clamp(0.0, 255.0) as u8;
                    *dst_px = [255, 255, 255, density_val];
                } else {
                    *dst_px = [0, 0, 0, 0];
                }
            }
        });

    Ok(())
}

/// Generates 2D color cloud density counts across the CIE diagram canvas.
///
/// In Python, this was:
/// ```python
/// counts = np.bincount(indices, minlength=(size * size))
/// counts_2d = counts.reshape((size, size))
/// ```
fn accumulate_counts(
    src: &[Plane<'_, f32>; 3],
    width: usize,
    height: usize,
    canvas_size: usize,
    projector: CieProjector,
) -> (Vec<u32>, u32) {
    let canvas_len = canvas_size * canvas_size;
    let counts = (0..canvas_len).map(|_| AtomicU32::new(0)).collect::<Vec<_>>();

    (0..height).into_par_iter().for_each(|y| {
        let (sx, sy, sz) = unsafe { (src[0].row(y), src[1].row(y), src[2].row(y)) };

        for x in 0..width {
            let idx = projector.project_to_idx(sx[x], sy[x], sz[x]);
            counts[idx].fetch_add(1, Ordering::Relaxed);
        }
    });

    let counts_u32 = counts.into_par_iter().map(AtomicU32::into_inner).collect::<Vec<_>>();
    let max_count = counts_u32.par_iter().copied().max().unwrap_or(0);
    (counts_u32, max_count)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cie_modes_run() {
        let width = 32;
        let height = 32;
        let canvas_size = 64;

        let x_data = vec![0.3127f32; width * height];
        let y_data = vec![0.3290f32; width * height];
        let z_data = vec![0.3583f32; width * height];

        let src_xyz: [PlaneDescriptor; 3] = [
            (x_data.as_ptr() as usize, width * 4).into(),
            (y_data.as_ptr() as usize, width * 4).into(),
            (z_data.as_ptr() as usize, width * 4).into(),
        ];

        // Density CIE 1931
        let mut dst_density = vec![0u8; canvas_size * canvas_size];
        compute_cie_core(
            src_xyz,
            (dst_density.as_mut_ptr() as usize, canvas_size).into(),
            width,
            height,
            canvas_size,
            CieMode::Cie1931,
            RenderMode::Density,
            1.0,
            None,
        )
        .expect("Density mode 1931");
        assert!(dst_density.iter().any(|&x| x > 0));

        // Density CIE 1976
        let mut dst_1976 = vec![0u8; canvas_size * canvas_size];
        compute_cie_core(
            src_xyz,
            (dst_1976.as_mut_ptr() as usize, canvas_size).into(),
            width,
            height,
            canvas_size,
            CieMode::Cie1976,
            RenderMode::Density,
            1.0,
            None,
        )
        .expect("Density mode 1976");
        assert!(dst_1976.iter().any(|&x| x > 0));

        // ChromaWheel
        let mut dst_chroma = vec![0u8; canvas_size * canvas_size * 4];
        compute_cie_core(
            src_xyz,
            (dst_chroma.as_mut_ptr() as usize, canvas_size * 4).into(),
            width,
            height,
            canvas_size,
            CieMode::Cie1931,
            RenderMode::ChromaWheel,
            1.0,
            None,
        )
        .expect("ChromaWheel mode");
        assert!(dst_chroma.iter().any(|&x| x > 0));

        // PixelColor
        let r_data = vec![0.8f32; width * height];
        let g_data = vec![0.5f32; width * height];
        let b_data = vec![0.2f32; width * height];
        let linear_rgb: [PlaneDescriptor; 3] = [
            (r_data.as_ptr() as usize, width * 4).into(),
            (g_data.as_ptr() as usize, width * 4).into(),
            (b_data.as_ptr() as usize, width * 4).into(),
        ];
        let mut dst_pixel = vec![0u8; canvas_size * canvas_size * 4];
        compute_cie_core(
            src_xyz,
            (dst_pixel.as_mut_ptr() as usize, canvas_size * 4).into(),
            width,
            height,
            canvas_size,
            CieMode::Cie1931,
            RenderMode::PixelColor,
            1.0,
            Some(linear_rgb),
        )
        .expect("PixelColor mode");
        assert!(dst_pixel.chunks(4).any(|px| px[3] == 255 && px[0] > 0));
    }

    #[test]
    fn test_cie_pixel_color_missing_linear_rgb() {
        let width = 32;
        let height = 32;
        let canvas_size = 64;
        let x_data = vec![0.3127f32; width * height];
        let src_xyz = [(x_data.as_ptr() as usize, width * 4).into(); 3];
        let mut dst = vec![0u8; canvas_size * canvas_size * 4];

        let err = compute_cie_core(
            src_xyz,
            (dst.as_mut_ptr() as usize, canvas_size * 4).into(),
            width,
            height,
            canvas_size,
            CieMode::Cie1931,
            RenderMode::PixelColor,
            1.0,
            None,
        )
        .unwrap_err();

        assert_eq!(err, BufferError::MissingPlanes { label: "linear_rgb" });
    }
}
