//! Rust acceleration module for the vsview-histogram plugin.
mod buffer;
mod cie;
mod luma;
mod utils;
mod vectorscope;
mod waveform;

use pyo3::prelude::pymodule;

#[pymodule]
mod funcs {
    #[pymodule_export]
    use super::cie::compute_cie;
    #[pymodule_export]
    use super::luma::compute_luma;
    #[pymodule_export]
    use super::vectorscope::compute_vectorscope;
    #[pymodule_export]
    use super::waveform::compute_waveform;
}
