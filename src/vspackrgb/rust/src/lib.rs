mod dispatch;
mod formats;

use formats::{bgra_8bit, rgb30_10bit, rgba_16bit, rgba_32bit};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[derive(Clone, Copy)]
struct ValidatedBuffers {
    c0: usize,
    c1: usize,
    c2: usize,
    alpha: Option<usize>,
    dest: usize,
}

#[inline]
fn validate_pack_args(
    fn_name: &str,
    c0: usize,
    c1: usize,
    c2: usize,
    alpha: Option<usize>,
    dest: usize,
    width: usize,
    src_stride: usize,
    dest_stride: usize,
    min_dest_bytes_per_pixel: usize,
) -> PyResult<ValidatedBuffers> {
    if c0 == 0 || c1 == 0 || c2 == 0 || dest == 0 {
        return Err(PyValueError::new_err(format!("Null pointer passed to {fn_name}")));
    }
    if src_stride < width {
        return Err(PyValueError::new_err(format!(
            "Source stride ({src_stride}) must be greater than or equal to width ({width})"
        )));
    }
    let min_dest_stride = width.saturating_mul(min_dest_bytes_per_pixel);
    if dest_stride < min_dest_stride {
        return Err(PyValueError::new_err(format!(
            "dest_stride ({dest_stride}) must be greater than or equal to width * {min_dest_bytes_per_pixel} ({min_dest_stride})"
        )));
    }

    Ok(ValidatedBuffers {
        c0,
        c1,
        c2,
        alpha,
        dest,
    })
}

/// Pack planar 8-bit RGB to interleaved BGRA with straight alpha.
#[pyfunction]
#[pyo3(signature = (b_ptr, g_ptr, r_ptr, a_ptr, width, height, src_stride, dest_ptr, dest_stride))]
fn pack_bgra_8bit(
    py: Python<'_>,
    b_ptr: usize,
    g_ptr: usize,
    r_ptr: usize,
    a_ptr: Option<usize>,
    width: usize,
    height: usize,
    src_stride: usize,
    dest_ptr: usize,
    dest_stride: usize,
) -> PyResult<()> {
    let buf = validate_pack_args(
        "pack_bgra_8bit",
        b_ptr,
        g_ptr,
        r_ptr,
        a_ptr,
        dest_ptr,
        width,
        src_stride,
        dest_stride,
        4,
    )?;

    py.detach(move || unsafe {
        bgra_8bit::pack_bgra_8bit(
            buf.c0 as *const u8,
            buf.c1 as *const u8,
            buf.c2 as *const u8,
            buf.alpha.map(|p| p as *const u8),
            width,
            height,
            src_stride,
            buf.dest as *mut u8,
            dest_stride,
        );
    });

    Ok(())
}

/// Pack planar 10-bit RGB to A2R10G10B10 with premultiplied alpha.
#[pyfunction]
#[pyo3(signature = (r_ptr, g_ptr, b_ptr, a_ptr, width, height, samples_per_row, dest_ptr, dest_stride))]
fn pack_rgb30_10bit(
    py: Python<'_>,
    r_ptr: usize,
    g_ptr: usize,
    b_ptr: usize,
    a_ptr: Option<usize>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: usize,
    dest_stride: usize,
) -> PyResult<()> {
    let buf = validate_pack_args(
        "pack_rgb30_10bit",
        r_ptr,
        g_ptr,
        b_ptr,
        a_ptr,
        dest_ptr,
        width,
        samples_per_row,
        dest_stride,
        4,
    )?;

    py.detach(move || unsafe {
        rgb30_10bit::pack_rgb30_10bit(
            buf.c0 as *const u16,
            buf.c1 as *const u16,
            buf.c2 as *const u16,
            buf.alpha.map(|p| p as *const u16),
            width,
            height,
            samples_per_row,
            buf.dest as *mut u32,
            dest_stride,
        );
    });

    Ok(())
}

/// Pack planar 16-bit RGB to interleaved RGBA64 (reference implementation).
#[pyfunction]
#[pyo3(signature = (r_ptr, g_ptr, b_ptr, a_ptr, width, height, samples_per_row, dest_ptr, dest_stride))]
fn pack_rgba64_16bit(
    py: Python<'_>,
    r_ptr: usize,
    g_ptr: usize,
    b_ptr: usize,
    a_ptr: Option<usize>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: usize,
    dest_stride: usize,
) -> PyResult<()> {
    let buf = validate_pack_args(
        "pack_rgba64_16bit",
        r_ptr,
        g_ptr,
        b_ptr,
        a_ptr,
        dest_ptr,
        width,
        samples_per_row,
        dest_stride,
        8,
    )?;

    py.detach(move || unsafe {
        rgba_16bit::pack_rgba_16bit::<0xFFFF>(
            buf.c0 as *const u16,
            buf.c1 as *const u16,
            buf.c2 as *const u16,
            buf.alpha.map(|p| p as *const u16),
            width,
            height,
            samples_per_row,
            buf.dest as *mut u16,
            dest_stride,
        );
    });

    Ok(())
}

/// Pack planar 16-bit RGB to interleaved float16 RGBA.
#[pyfunction]
#[pyo3(signature = (r_ptr, g_ptr, b_ptr, a_ptr, width, height, samples_per_row, dest_ptr, dest_stride))]
fn pack_rgba16f_16bit(
    py: Python<'_>,
    r_ptr: usize,
    g_ptr: usize,
    b_ptr: usize,
    a_ptr: Option<usize>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: usize,
    dest_stride: usize,
) -> PyResult<()> {
    let buf = validate_pack_args(
        "pack_rgba16f_16bit",
        r_ptr,
        g_ptr,
        b_ptr,
        a_ptr,
        dest_ptr,
        width,
        samples_per_row,
        dest_stride,
        8,
    )?;

    py.detach(move || unsafe {
        rgba_16bit::pack_rgba_16bit::<0x3C00>(
            buf.c0 as *const u16,
            buf.c1 as *const u16,
            buf.c2 as *const u16,
            buf.alpha.map(|p| p as *const u16),
            width,
            height,
            samples_per_row,
            buf.dest as *mut u16,
            dest_stride,
        );
    });

    Ok(())
}

/// Pack planar 32-bit RGB to interleaved float32 RGBA.
#[pyfunction]
#[pyo3(signature = (r_ptr, g_ptr, b_ptr, a_ptr, width, height, samples_per_row, dest_ptr, dest_stride))]
fn pack_rgba32f_32bit(
    py: Python<'_>,
    r_ptr: usize,
    g_ptr: usize,
    b_ptr: usize,
    a_ptr: Option<usize>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: usize,
    dest_stride: usize,
) -> PyResult<()> {
    let buf = validate_pack_args(
        "pack_rgba32f_32bit",
        r_ptr,
        g_ptr,
        b_ptr,
        a_ptr,
        dest_ptr,
        width,
        samples_per_row,
        dest_stride,
        16,
    )?;

    py.detach(move || unsafe {
        rgba_32bit::pack_rgba_32bit::<0x3F80_0000>(
            buf.c0 as *const u32,
            buf.c1 as *const u32,
            buf.c2 as *const u32,
            buf.alpha.map(|p| p as *const u32),
            width,
            height,
            samples_per_row,
            buf.dest as *mut u32,
            dest_stride,
        );
    });

    Ok(())
}

/// Rust-accelerated RGB packing functions.
#[pymodule]
mod rust {
    #[pymodule_export]
    use super::{pack_bgra_8bit, pack_rgb30_10bit, pack_rgba16f_16bit, pack_rgba32f_32bit, pack_rgba64_16bit};
}
