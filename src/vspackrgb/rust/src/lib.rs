use std::marker::PhantomData;

use pyo3::exceptions::PyBufferError;
use pyo3::ffi;
use pyo3::prelude::*;

pub struct RawBuffer<T> {
    view: Box<ffi::Py_buffer>,
    _marker: PhantomData<T>,
}

impl<T> RawBuffer<T> {
    fn get(obj: &Bound<'_, PyAny>) -> PyResult<Self> {
        let mut view = Box::new_uninit();
        let res = unsafe { ffi::PyObject_GetBuffer(obj.as_ptr(), view.as_mut_ptr(), ffi::PyBUF_SIMPLE) };
        if res != 0 {
            return Err(PyBufferError::new_err("Could not get buffer from object"));
        }
        Ok(Self {
            view: unsafe { view.assume_init() },
            _marker: PhantomData,
        })
    }

    #[inline]
    fn as_ptr(&self) -> *const T {
        self.view.buf.cast::<T>()
    }
}

impl<'a, 'py, T> FromPyObject<'a, 'py> for RawBuffer<T> {
    type Error = PyErr;

    fn extract(obj: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        Self::get(&obj)
    }
}

impl<T> Drop for RawBuffer<T> {
    fn drop(&mut self) {
        Python::attach(|_py| unsafe {
            ffi::PyBuffer_Release(self.view.as_mut());
        });
    }
}

#[pyfunction]
#[pyo3(signature = (b_data, g_data, r_data, a_data, width, height, src_stride, dest_ptr, dest_stride))]
pub fn pack_bgra_8bit(
    py: Python<'_>,
    b_data: RawBuffer<u8>,
    g_data: RawBuffer<u8>,
    r_data: RawBuffer<u8>,
    a_data: Option<RawBuffer<u8>>,
    width: usize,
    height: usize,
    src_stride: usize,
    dest_ptr: usize,
    dest_stride: usize,
) -> PyResult<()> {
    let b_ptr = b_data.as_ptr() as usize;
    let g_ptr = g_data.as_ptr() as usize;
    let r_ptr = r_data.as_ptr() as usize;
    let a_ptr = a_data.as_ref().map(|a| a.as_ptr() as usize);

    py.detach(move || {
        let out_base = dest_ptr as *mut u8;
        let b = b_ptr as *const u8;
        let g = g_ptr as *const u8;
        let r = r_ptr as *const u8;
        let a_opt = a_ptr.map(|p| p as *const u8);

        for y in 0..height {
            let src_row_offset = y * src_stride;
            let mut out_row = unsafe { out_base.add(y * dest_stride) };

            if let Some(a) = a_opt {
                for x in 0..width {
                    let offset = src_row_offset + x;
                    unsafe {
                        *out_row = *b.add(offset);
                        *out_row.add(1) = *g.add(offset);
                        *out_row.add(2) = *r.add(offset);
                        *out_row.add(3) = *a.add(offset);
                        out_row = out_row.add(4);
                    }
                }
            } else {
                for x in 0..width {
                    let offset = src_row_offset + x;
                    unsafe {
                        *out_row = *b.add(offset);
                        *out_row.add(1) = *g.add(offset);
                        *out_row.add(2) = *r.add(offset);
                        *out_row.add(3) = 255;
                        out_row = out_row.add(4);
                    }
                }
            }
        }
    });

    Ok(())
}

#[pyfunction]
#[pyo3(signature = (r_data, g_data, b_data, a_data, width, height, samples_per_row, dest_ptr, dest_stride))]
pub fn pack_rgb30_10bit(
    py: Python<'_>,
    r_data: RawBuffer<u16>,
    g_data: RawBuffer<u16>,
    b_data: RawBuffer<u16>,
    a_data: Option<RawBuffer<u16>>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: usize,
    dest_stride: usize,
) -> PyResult<()> {
    let r_ptr = r_data.as_ptr() as usize;
    let g_ptr = g_data.as_ptr() as usize;
    let b_ptr = b_data.as_ptr() as usize;
    let a_ptr = a_data.as_ref().map(|a| a.as_ptr() as usize);

    py.detach(move || {
        let out_base = dest_ptr as *mut u8;
        let r = r_ptr as *const u16;
        let g = g_ptr as *const u16;
        let b = b_ptr as *const u16;
        let a_opt = a_ptr.map(|p| p as *const u16);

        for y in 0..height {
            let src_row_offset = y * samples_per_row;
            let out_row = unsafe { out_base.add(y * dest_stride).cast::<u32>() };

            if let Some(a) = a_opt {
                for x in 0..width {
                    let offset = src_row_offset + x;
                    unsafe {
                        let mut r_val = u32::from(*r.add(offset));
                        let mut g_val = u32::from(*g.add(offset));
                        let mut b_val = u32::from(*b.add(offset));
                        let a_val = u32::from(*a.add(offset) >> 8);

                        match a_val {
                            0 => {
                                r_val = 0;
                                g_val = 0;
                                b_val = 0;
                            }
                            1 => {
                                r_val /= 3;
                                g_val /= 3;
                                b_val /= 3;
                            }
                            2 => {
                                r_val = (r_val * 2) / 3;
                                g_val = (g_val * 2) / 3;
                                b_val = (b_val * 2) / 3;
                            }
                            _ => {}
                        }

                        *out_row.add(x) = (a_val << 30) | (r_val << 20) | (g_val << 10) | b_val;
                    }
                }
            } else {
                let alpha_mask = 0xC000_0000;
                for x in 0..width {
                    let offset = src_row_offset + x;
                    unsafe {
                        let r_val = u32::from(*r.add(offset));
                        let g_val = u32::from(*g.add(offset));
                        let b_val = u32::from(*b.add(offset));
                        *out_row.add(x) = alpha_mask | (r_val << 20) | (g_val << 10) | b_val;
                    }
                }
            }
        }
    });

    Ok(())
}

#[pyfunction]
#[pyo3(signature = (r_data, g_data, b_data, a_data, width, height, samples_per_row, dest_ptr, dest_stride))]
pub fn pack_rgba64_16bit(
    py: Python<'_>,
    r_data: RawBuffer<u16>,
    g_data: RawBuffer<u16>,
    b_data: RawBuffer<u16>,
    a_data: Option<RawBuffer<u16>>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: usize,
    dest_stride: usize,
) -> PyResult<()> {
    let r_ptr = r_data.as_ptr() as usize;
    let g_ptr = g_data.as_ptr() as usize;
    let b_ptr = b_data.as_ptr() as usize;
    let a_ptr = a_data.as_ref().map(|a| a.as_ptr() as usize);

    py.detach(move || {
        let out_base = dest_ptr as *mut u8;
        let r = r_ptr as *const u16;
        let g = g_ptr as *const u16;
        let b = b_ptr as *const u16;
        let a_opt = a_ptr.map(|p| p as *const u16);

        for y in 0..height {
            let src_row_offset = y * samples_per_row;
            let mut out_row = unsafe { out_base.add(y * dest_stride).cast::<u16>() };

            if let Some(a) = a_opt {
                for x in 0..width {
                    let offset = src_row_offset + x;
                    unsafe {
                        *out_row = *r.add(offset);
                        *out_row.add(1) = *g.add(offset);
                        *out_row.add(2) = *b.add(offset);
                        *out_row.add(3) = *a.add(offset);
                        out_row = out_row.add(4);
                    }
                }
            } else {
                for x in 0..width {
                    let offset = src_row_offset + x;
                    unsafe {
                        *out_row = *r.add(offset);
                        *out_row.add(1) = *g.add(offset);
                        *out_row.add(2) = *b.add(offset);
                        *out_row.add(3) = 65535;
                        out_row = out_row.add(4);
                    }
                }
            }
        }
    });

    Ok(())
}

#[pyfunction]
#[pyo3(signature = (r_data, g_data, b_data, a_data, width, height, samples_per_row, dest_ptr, dest_stride))]
pub fn pack_rgba16f_16bit(
    py: Python<'_>,
    r_data: RawBuffer<u16>,
    g_data: RawBuffer<u16>,
    b_data: RawBuffer<u16>,
    a_data: Option<RawBuffer<u16>>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: usize,
    dest_stride: usize,
) -> PyResult<()> {
    let r_ptr = r_data.as_ptr() as usize;
    let g_ptr = g_data.as_ptr() as usize;
    let b_ptr = b_data.as_ptr() as usize;
    let a_ptr = a_data.as_ref().map(|a| a.as_ptr() as usize);

    py.detach(move || {
        let out_base = dest_ptr as *mut u8;
        let r = r_ptr as *const u16;
        let g = g_ptr as *const u16;
        let b = b_ptr as *const u16;
        let a_opt = a_ptr.map(|p| p as *const u16);
        let alpha_full_bits: u16 = 0x3C00;

        for y in 0..height {
            let src_row_offset = y * samples_per_row;
            let mut out_row = unsafe { out_base.add(y * dest_stride).cast::<u16>() };

            if let Some(a) = a_opt {
                for x in 0..width {
                    let offset = src_row_offset + x;
                    unsafe {
                        *out_row = *r.add(offset);
                        *out_row.add(1) = *g.add(offset);
                        *out_row.add(2) = *b.add(offset);
                        *out_row.add(3) = *a.add(offset);
                        out_row = out_row.add(4);
                    }
                }
            } else {
                for x in 0..width {
                    let offset = src_row_offset + x;
                    unsafe {
                        *out_row = *r.add(offset);
                        *out_row.add(1) = *g.add(offset);
                        *out_row.add(2) = *b.add(offset);
                        *out_row.add(3) = alpha_full_bits;
                        out_row = out_row.add(4);
                    }
                }
            }
        }
    });

    Ok(())
}

#[pyfunction]
#[pyo3(signature = (r_data, g_data, b_data, a_data, width, height, samples_per_row, dest_ptr, dest_stride))]
pub fn pack_rgba32f_32bit(
    py: Python<'_>,
    r_data: RawBuffer<u32>,
    g_data: RawBuffer<u32>,
    b_data: RawBuffer<u32>,
    a_data: Option<RawBuffer<u32>>,
    width: usize,
    height: usize,
    samples_per_row: usize,
    dest_ptr: usize,
    dest_stride: usize,
) -> PyResult<()> {
    let r_ptr = r_data.as_ptr() as usize;
    let g_ptr = g_data.as_ptr() as usize;
    let b_ptr = b_data.as_ptr() as usize;
    let a_ptr = a_data.as_ref().map(|a| a.as_ptr() as usize);

    py.detach(move || {
        let out_base = dest_ptr as *mut u8;
        let r = r_ptr as *const u32;
        let g = g_ptr as *const u32;
        let b = b_ptr as *const u32;
        let a_opt = a_ptr.map(|p| p as *const u32);
        let alpha_full_bits: u32 = 0x3F80_0000;

        for y in 0..height {
            let src_row_offset = y * samples_per_row;
            let mut out_row = unsafe { out_base.add(y * dest_stride).cast::<u32>() };

            if let Some(a) = a_opt {
                for x in 0..width {
                    let offset = src_row_offset + x;
                    unsafe {
                        *out_row = *r.add(offset);
                        *out_row.add(1) = *g.add(offset);
                        *out_row.add(2) = *b.add(offset);
                        *out_row.add(3) = *a.add(offset);
                        out_row = out_row.add(4);
                    }
                }
            } else {
                for x in 0..width {
                    let offset = src_row_offset + x;
                    unsafe {
                        *out_row = *r.add(offset);
                        *out_row.add(1) = *g.add(offset);
                        *out_row.add(2) = *b.add(offset);
                        *out_row.add(3) = alpha_full_bits;
                        out_row = out_row.add(4);
                    }
                }
            }
        }
    });

    Ok(())
}

#[pymodule]
fn rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(pack_bgra_8bit, m)?)?;
    m.add_function(wrap_pyfunction!(pack_rgb30_10bit, m)?)?;
    m.add_function(wrap_pyfunction!(pack_rgba64_16bit, m)?)?;
    m.add_function(wrap_pyfunction!(pack_rgba16f_16bit, m)?)?;
    m.add_function(wrap_pyfunction!(pack_rgba32f_32bit, m)?)?;
    Ok(())
}
