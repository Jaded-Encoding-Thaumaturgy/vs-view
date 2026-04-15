use std::path::Path;

use pyo3_stub_gen::Result;
use pyo3_stub_gen::StubInfo;

fn main() -> Result<()> {
    let _ = _cli::gather_stub_info;

    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let stub = StubInfo::from_pyproject_toml(manifest_dir.join("../pyproject.toml"));
    stub?.generate()?;
    Ok(())
}
