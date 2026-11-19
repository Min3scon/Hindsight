//! CRC-32 for the Python side: the registry file, the journal and the Entry checksums.
//!
//! Same value as zlib.crc32 (IEEE polynomial, optional start value), any contiguous buffer, about
//! ten times as fast (carry-less multiply instead of table lookups). M5 measured the rest of the
//! registry and journal framing in Rust (reading the step run, scanning journal records) and it
//! did not pay: the reads are the kernel's copy, and the checksum was what Python spent its time on.

use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::{guard, Buf};

/// Buffers below this are hashed with the GIL held: releasing it costs more than the hash.
const NO_RELEASE: usize = 1 << 16;

#[pyfunction]
#[pyo3(signature = (data, init=0))]
pub fn crc32(py: Python<'_>, data: &Bound<'_, PyAny>, init: u32) -> PyResult<u32> {
    guard(|| {
        let b = Buf::new(data, false)?;
        let mut h = crc32fast::Hasher::new_with_initial(init);
        // Only an immutable bytes object is safe to read without the GIL (a bytearray or a memoryview
        // of one could change under us); anything else is hashed with the GIL held.
        if b.len() < NO_RELEASE || !data.is_exact_instance_of::<PyBytes>() {
            h.update(b.bytes());
            return Ok(h.finalize());
        }
        let (p, n) = (b.bytes().as_ptr() as usize, b.len());
        Ok(py.allow_threads(|| {
            // SAFETY: `data` is a bytes object kept alive (and immutable) by the caller's reference,
            // and `b` holds its view until this function returns.
            let s = unsafe { std::slice::from_raw_parts(p as *const u8, n) };
            h.update(s);
            h.finalize()
        }))
    })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(crc32, m)?)?;
    Ok(())
}
