use std::ops::Range;

pub(crate) mod bgra_8bit;
pub(crate) mod rgb30_10bit;
pub(crate) mod rgba_16bit;
pub(crate) mod rgba_32bit;

#[derive(Debug, Clone, Copy)]
pub(crate) struct RowPointers<SrcT, DestT> {
    pub c0: *const SrcT,
    pub c1: *const SrcT,
    pub c2: *const SrcT,
    pub alpha: Option<*const SrcT>,
    pub out: *mut DestT,
}

#[inline]
pub(crate) unsafe fn compute_row_ptrs<SrcT, DestT>(
    c0_base: *const SrcT,
    c1_base: *const SrcT,
    c2_base: *const SrcT,
    a_base: Option<*const SrcT>,
    out_base: *mut DestT,
    y: usize,
    src_stride_samples: usize,
    dest_stride_bytes: usize,
) -> RowPointers<SrcT, DestT> {
    let offset = y * src_stride_samples;
    // SAFETY: The caller guarantees that the base pointers are valid and offset stays within allocation bounds.
    unsafe {
        RowPointers {
            c0: c0_base.add(offset),
            c1: c1_base.add(offset),
            c2: c2_base.add(offset),
            alpha: a_base.map(|p| p.add(offset)),
            out: out_base.byte_add(y * dest_stride_bytes),
        }
    }
}

#[inline]
pub(crate) unsafe fn pack_rgba_range<T: Copy>(row: &RowPointers<T, T>, range: Range<usize>, alpha_default: T) {
    if let Some(alpha_ptr) = row.alpha {
        for x in range {
            // SAFETY: Caller guarantees that indices in `range`
            // are within bounds of row source and destination buffers.
            unsafe {
                let out_pixel = row.out.add(x * 4);
                *out_pixel.add(0) = *row.c0.add(x);
                *out_pixel.add(1) = *row.c1.add(x);
                *out_pixel.add(2) = *row.c2.add(x);
                *out_pixel.add(3) = *alpha_ptr.add(x);
            }
        }
    } else {
        for x in range {
            // SAFETY: Caller guarantees that indices in `range`
            // are within bounds of row source and destination buffers.
            unsafe {
                let out_pixel = row.out.add(x * 4);
                *out_pixel.add(0) = *row.c0.add(x);
                *out_pixel.add(1) = *row.c1.add(x);
                *out_pixel.add(2) = *row.c2.add(x);
                *out_pixel.add(3) = alpha_default;
            }
        }
    }
}
