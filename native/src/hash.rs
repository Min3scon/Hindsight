//! Mesh fingerprints: xxh3-128 over the same byte stream the Python code fed blake2b, one `update` per
//! piece, so a mesh is read, hashed and let go array by array.
//!
//! The digest is xxh3-128 with seed 0 of the concatenated pieces (streaming and one-shot give the same
//! value); hexdigest is the 128-bit value as 32 lowercase hex digits, high half first (xxh3's canonical
//! form). Large `bytes` pieces are hashed with the GIL released.

use pyo3::prelude::*;
use pyo3::types::PyBytes;
use xxhash_rust::xxh3::Xxh3;

use crate::{guard, Buf};

const NO_RELEASE: usize = 1 << 16;

#[pyclass(module = "hindsight_native")]
pub struct Hasher128 {
    st: Xxh3,
}

#[pymethods]
impl Hasher128 {
    #[new]
    fn new() -> Self {
        Hasher128 { st: Xxh3::new() }
    }

    /// Feed one buffer (any contiguous buffer: bytes, a numpy array, a memoryview).
    fn update(&mut self, py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<()> {
        guard(|| {
            let b = Buf::new(data, false)?;
            if b.len() < NO_RELEASE || !data.is_exact_instance_of::<PyBytes>() {
                self.st.update(b.bytes());
                return Ok(());
            }
            let (p, n) = (b.bytes().as_ptr() as usize, b.len());
            let st = &mut self.st;
            py.allow_threads(|| {
                // SAFETY: `data` is an immutable bytes object kept alive by the caller, `b` holds its view.
                st.update(unsafe { std::slice::from_raw_parts(p as *const u8, n) });
            });
            Ok(())
        })
    }

    fn hexdigest(&self) -> String {
        format!("{:032x}", self.st.digest128())
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Hasher128>()?;
    Ok(())
}
