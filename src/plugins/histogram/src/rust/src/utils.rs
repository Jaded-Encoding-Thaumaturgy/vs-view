use rayon::prelude::*;
use std::cmp::Ordering;

use crate::buffer::PlaneMut;

/// Shifts integer sample with half-up rounding when downshifting.
///
/// In Python, this was:
/// ```python
/// shift = fmt.bits_per_sample - bits
/// if shift > 0:
///     arr = (arr + (1 << (shift - 1))) >> shift
/// elif shift < 0:
///     arr <<= -shift
/// ```
pub(crate) fn shift_int_sample(raw: i32, shift: i32) -> i32 {
    match shift.cmp(&0) {
        Ordering::Greater => {
            if shift >= 31 {
                0
            } else {
                ((i64::from(raw) + (1i64 << (shift - 1))) >> shift) as i32
            }
        }
        Ordering::Less => {
            let abs_shift = shift.unsigned_abs();
            if abs_shift >= 31 { 0 } else { raw << abs_shift }
        }
        Ordering::Equal => raw,
    }
}

/// Renders a logarithmic density map to an 8-bit indexed destination plane.
///
/// In Python, this was:
/// ```python
/// if (max_count := counts_2d.max()) > 0:
///     scale = 255.0 / np.log1p(max_count)
///     grid_img = (np.log1p(counts_2d) * scale).astype(np.uint8)
/// else:
///     grid_img = counts_2d.astype(np.uint8)
/// ```
pub(crate) fn render_density_to_plane(dst_plane: &mut PlaneMut, canvas_size: usize, counts: &[u32], max_count: u32) {
    let scale = if max_count > 0 {
        255.0f32 / (max_count as f32).ln_1p()
    } else {
        0.0f32
    };

    dst_plane
        .par_rows_mut::<u8>()
        .zip(counts.par_chunks_exact(canvas_size))
        .for_each(|(dst_row, count_row)| {
            for (dst_val, &count) in dst_row.iter_mut().zip(count_row) {
                *dst_val = if count > 0 {
                    ((count as f32).ln_1p() * scale).round().clamp(0.0, 255.0) as u8
                } else {
                    0
                };
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_shift_int_sample() {
        assert_eq!(shift_int_sample(100, 0), 100);
        assert_eq!(shift_int_sample(100, 2), 25);
        assert_eq!(shift_int_sample(102, 2), 26);
        assert_eq!(shift_int_sample(25, -2), 100);
        assert_eq!(shift_int_sample(100, 32), 0);
        assert_eq!(shift_int_sample(100, -32), 0);
    }

    #[test]
    fn test_render_density_empty() {
        let mut dst = vec![123u8; 16];
        let mut dst_plane = PlaneMut::new((dst.as_mut_ptr() as usize, 4).into(), 4, 4, 1, "dst").unwrap();
        let counts = vec![0u32; 16];
        render_density_to_plane(&mut dst_plane, 4, &counts, 0);
        assert!(dst.iter().all(|&x| x == 0));
    }
}
