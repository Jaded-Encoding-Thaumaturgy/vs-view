use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::error::Error;
use std::fmt::{self, Display};
use std::marker::PhantomData;
use std::slice;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum BufferError {
    NullPointer {
        label: &'static str,
    },
    InvalidStride {
        label: &'static str,
        stride: usize,
        min_stride: usize,
    },
    MissingPlanes {
        label: &'static str,
    },
}

impl Display for BufferError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> fmt::Result {
        match *self {
            Self::NullPointer { label } => write!(f, "{label} must be a non-null pointer"),
            Self::InvalidStride {
                label,
                stride,
                min_stride,
            } => {
                write!(
                    f,
                    "{label} stride ({stride} bytes) is less than row size ({min_stride} bytes)"
                )
            }
            Self::MissingPlanes { label } => {
                write!(f, "{label} planes must be provided")
            }
        }
    }
}

impl Error for BufferError {}

impl From<BufferError> for PyErr {
    fn from(err: BufferError) -> Self {
        PyValueError::new_err(err.to_string())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct PlaneDescriptor {
    ptr: usize,
    stride: usize,
}

impl<'a, 'py> FromPyObject<'a, 'py> for PlaneDescriptor {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        let (ptr, stride): (usize, usize) = ob.extract()?;
        Ok(Self { ptr, stride })
    }
}

impl From<(usize, usize)> for PlaneDescriptor {
    fn from((ptr, stride): (usize, usize)) -> Self {
        Self { ptr, stride }
    }
}

impl PlaneDescriptor {
    fn validate(&self, width: usize, elem_size: usize, label: &'static str) -> Result<(), BufferError> {
        if self.ptr == 0 {
            return Err(BufferError::NullPointer { label });
        }

        let stride = self.stride;
        let min_stride = width.saturating_mul(elem_size);
        if stride < min_stride {
            return Err(BufferError::InvalidStride {
                label,
                stride,
                min_stride,
            });
        }

        Ok(())
    }
}

/// Sample type corresponding to `VapourSynth` `vs.SampleType` (`INTEGER = 0`, `FLOAT = 1`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum SampleType {
    Integer = 0,
    Float = 1,
}

impl<'a, 'py> FromPyObject<'a, 'py> for SampleType {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        match ob.extract::<u8>()? {
            0 => Ok(Self::Integer),
            1 => Ok(Self::Float),
            other => Err(PyValueError::new_err(format!(
                "Invalid sample_type: {other} (expected 0: Integer, 1: Float)"
            ))),
        }
    }
}

/// Histogram render mode corresponding to `vsview_histogram.settings.RenderMode`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RenderMode {
    Density = 0,
    ChromaWheel = 1,
    PixelColor = 2,
}

impl<'a, 'py> FromPyObject<'a, 'py> for RenderMode {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        match ob.extract::<u8>()? {
            0 => Ok(Self::Density),
            1 => Ok(Self::ChromaWheel),
            2 => Ok(Self::PixelColor),
            other => Err(PyValueError::new_err(format!(
                "Invalid render_mode: {other} (expected 0: Density, 1: ChromaWheel, 2: PixelColor)"
            ))),
        }
    }
}

/// CIE mode corresponding to `vsview_histogram.settings.CieMode`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum CieMode {
    Cie1931 = 0,
    Cie1976 = 1,
}

impl<'a, 'py> FromPyObject<'a, 'py> for CieMode {
    type Error = PyErr;

    fn extract(ob: Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        match ob.extract::<u8>()? {
            0 => Ok(Self::Cie1931),
            1 => Ok(Self::Cie1976),
            other => Err(PyValueError::new_err(format!(
                "Invalid cie_mode: {other} (expected 0: Cie1931, 1: Cie1976)"
            ))),
        }
    }
}

#[derive(Copy, Clone)]
pub(crate) struct Plane<'a, T> {
    ptr: *const u8,
    stride: usize,
    width: usize,
    height: usize,
    marker: PhantomData<&'a T>,
}

unsafe impl<T: Sync> Send for Plane<'_, T> {}
unsafe impl<T: Sync> Sync for Plane<'_, T> {}

impl<'a, T: Copy> Plane<'a, T> {
    pub(crate) fn new(
        desc: PlaneDescriptor,
        width: usize,
        height: usize,
        label: &'static str,
    ) -> Result<Self, BufferError> {
        desc.validate(width, size_of::<T>(), label)?;

        Ok(Self {
            ptr: desc.ptr as *const u8,
            stride: desc.stride,
            width,
            height,
            marker: PhantomData,
        })
    }

    pub(crate) unsafe fn row(&self, y: usize) -> &'a [T] {
        debug_assert!(y < self.height);
        unsafe {
            let row_ptr = self.ptr.add(y * self.stride).cast::<T>();
            slice::from_raw_parts(row_ptr, self.width)
        }
    }
}

pub(crate) struct PlaneMut {
    ptr: *mut u8,
    stride: usize,
    width: usize,
    height: usize,
    elem_size: usize,
}

unsafe impl Send for PlaneMut {}
unsafe impl Sync for PlaneMut {}

impl PlaneMut {
    pub(crate) fn new(
        desc: PlaneDescriptor,
        width: usize,
        height: usize,
        elem_size: usize,
        label: &'static str,
    ) -> Result<Self, BufferError> {
        desc.validate(width, elem_size, label)?;
        Ok(Self {
            ptr: desc.ptr as *mut u8,
            stride: desc.stride,
            width,
            height,
            elem_size,
        })
    }

    pub(crate) fn rows_mut<'a, T: 'a>(&'a mut self) -> impl ExactSizeIterator<Item = &'a mut [T]> {
        let (ptr_addr, stride, row_len) = self.row_params::<T>();

        (0..self.height).map(move |y| {
            let row_ptr = (ptr_addr + y * stride) as *mut T;
            unsafe { slice::from_raw_parts_mut(row_ptr, row_len) }
        })
    }

    pub(crate) fn par_rows_mut<'a, T: Send + 'a>(&'a mut self) -> impl IndexedParallelIterator<Item = &'a mut [T]> {
        let (ptr_addr, stride, row_len) = self.row_params::<T>();

        (0..self.height).into_par_iter().map(move |y| {
            let row_ptr = (ptr_addr + y * stride) as *mut T;
            unsafe { slice::from_raw_parts_mut(row_ptr, row_len) }
        })
    }

    fn row_params<T>(&self) -> (usize, usize, usize) {
        assert!(size_of::<T>() > 0, "ZST elements are not supported");
        (
            self.ptr as usize,
            self.stride,
            (self.width * self.elem_size) / size_of::<T>(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_plane_descriptor_validate() {
        let desc = PlaneDescriptor { ptr: 0, stride: 100 };
        assert_eq!(
            desc.validate(20, 4, "src"),
            Err(BufferError::NullPointer { label: "src" })
        );

        let desc = PlaneDescriptor {
            ptr: 0x1000,
            stride: 50,
        };
        assert_eq!(
            desc.validate(20, 4, "src"),
            Err(BufferError::InvalidStride {
                label: "src",
                stride: 50,
                min_stride: 80,
            })
        );

        let desc = PlaneDescriptor {
            ptr: 0x1000,
            stride: 80,
        };
        assert!(desc.validate(20, 4, "src").is_ok());
    }

    #[test]
    fn test_plane_view_and_mut() {
        let mut data = vec![0u8; 100 * 20];
        let desc: PlaneDescriptor = (data.as_mut_ptr() as usize, 100).into();
        let view = Plane::<u8>::new(desc, 100, 20, "test_view").expect("valid plane");
        assert_eq!(view.width, 100);
        assert_eq!(view.height, 20);

        let mut plane_mut = PlaneMut::new(desc, 100, 20, 1, "test_mut").expect("valid plane mut");
        plane_mut.par_rows_mut::<u8>().enumerate().for_each(|(y, r)| {
            r[0] = y as u8;
        });
        assert_eq!(data[0], 0);
        assert_eq!(data[100], 1);
    }

    #[test]
    fn test_buffer_error_display() {
        let err_null = BufferError::NullPointer { label: "src" };
        assert!(err_null.to_string().contains("must be a non-null pointer"));

        let err_stride = BufferError::InvalidStride {
            label: "src",
            stride: 50,
            min_stride: 80,
        };
        assert!(err_stride.to_string().contains("is less than row size"));

        let err_missing = BufferError::MissingPlanes { label: "linear_rgb" };
        assert!(err_missing.to_string().contains("planes must be provided"));
    }
}
