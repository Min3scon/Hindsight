//! #148 (24.3c): a recorded mesh's topology packed, hashed and unpacked (R069; the Python was
//! hindsight/core/topopack.py, now tests/reference/topopack.py). An integer array is delta-coded (each value minus
//! the one before, modulo 2**bits, so the running sum gives it back whatever the values), then zstd level 3; the
//! xxh3-128 of its bytes is kept with it. The fast path takes exact numpy arrays in native byte order and exact
//! tuples; anything else, and every error, runs the reference's Python (FALLBACK) with the engine's codec and
//! hasher, so the result and the exception are numpy's and the codec's, as before. The delta coding, the codec and
//! the checksum run without the GIL (pack_all runs on a helper thread).
use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyBool, PyByteArray, PyBytes, PyDict, PyInt, PyString, PyTuple};
use xxhash_rust::xxh3::xxh3_128;

use crate::{codec, guard, Buf};

const FALLBACK: &str = r#"
import numpy as np
import hindsight_native as _N


def hash_of(a):
    h = _N.Hasher128()
    h.update(np.ascontiguousarray(a).reshape(-1).view(np.uint8))
    return h.hexdigest()


def pack_array(a):
    a = np.ascontiguousarray(a)
    flat = a.reshape(-1)
    delta = flat.dtype.kind in "iu"
    if delta and len(flat):
        d = np.empty_like(flat)
        d[0] = flat[0]
        np.subtract(flat[1:], flat[:-1], out=d[1:])
    else:
        d = flat
    return _N.codec_compress(d.view(np.uint8), "zstd"), a.dtype.str, a.shape, hash_of(a), delta


def unpack_array(p):
    blob, dt, shape, _h, delta = p
    dtype = np.dtype(dt)
    n = int(np.prod(shape)) if len(shape) else 1
    raw = _N.codec_decompress(blob, n * dtype.itemsize)
    flat = np.frombuffer(raw, dtype)
    out = np.cumsum(flat, dtype=dtype) if delta and len(flat) else flat.copy()
    return out.reshape(shape)


def pack_all(arrays):
    return {k: pack_array(a) for k, a in arrays.items()}


def packed_bytes(packed):
    return sum(len(p[0]) for p in packed.values())
"#;

static PY: GILOnceCell<Py<PyModule>> = GILOnceCell::new();

fn slow<'py>(py: Python<'py>, name: &str, arg: &Bound<'py, PyAny>) -> PyResult<PyObject> {
    let m = PY.get_or_try_init(py, || -> PyResult<Py<PyModule>> {
        Ok(PyModule::from_code_bound(py, FALLBACK, "hindsight_native/topo.py", "hindsight_native._topo")?.unbind())
    })?;
    Ok(m.bind(py).getattr(name)?.call1((arg,))?.unbind())
}

/// The array's dtype if the fast path takes it: an exact ndarray, native (little-endian) order, kind i, u, b or f.
fn plain<'py>(a: &Bound<'py, PyAny>, np: &Bound<'py, PyModule>) -> PyResult<Option<(Bound<'py, PyAny>, char, usize)>> {
    if !a.get_type().is(&np.getattr("ndarray")?) {
        return Ok(None);
    }
    let dt = a.getattr("dtype")?;
    let s: String = dt.getattr("str")?.extract()?;
    let kind = s.chars().nth(1).unwrap_or('?');
    let isz: usize = dt.getattr("itemsize")?.extract()?;
    let native = cfg!(target_endian = "little") && (s.starts_with('<') || s.starts_with('|'));
    let ok = native && (matches!(kind, 'i' | 'u' if matches!(isz, 1 | 2 | 4 | 8)) || matches!(kind, 'b' | 'f'));
    Ok(if ok { Some((dt, kind, isz)) } else { None })
}

fn delta_code(s: &[u8], isz: usize) -> Vec<u8> {
    let mut d = s.to_vec();
    macro_rules! run {
        ($t:ty) => {{
            let mut prev: $t = 0;
            for c in d.chunks_exact_mut(isz) {
                let v = <$t>::from_le_bytes(c.try_into().unwrap());
                c.copy_from_slice(&v.wrapping_sub(prev).to_le_bytes());
                prev = v;
            }
        }};
    }
    match isz {
        1 => run!(u8),
        2 => run!(u16),
        4 => run!(u32),
        _ => run!(u64),
    }
    d
}

fn running_sum(d: &mut [u8], isz: usize) {
    macro_rules! run {
        ($t:ty) => {{
            let mut acc: $t = 0;
            for c in d.chunks_exact_mut(isz) {
                acc = acc.wrapping_add(<$t>::from_le_bytes((&*c).try_into().unwrap()));
                c.copy_from_slice(&acc.to_le_bytes());
            }
        }};
    }
    match isz {
        1 => run!(u8),
        2 => run!(u16),
        4 => run!(u32),
        _ => run!(u64),
    }
}

fn zstd_id() -> u8 {
    codec::id_of("zstd").unwrap_or(3)
}

fn pack_one<'py>(py: Python<'py>, a: &Bound<'py, PyAny>, np: &Bound<'py, PyModule>) -> PyResult<PyObject> {
    let Some((dt, kind, isz)) = plain(a, np)? else { return slow(py, "pack_array", a) };
    let c = np.call_method1("ascontiguousarray", (a,))?;
    let b = Buf::new(&c, false)?;
    let (p, n, delta) = (b.bytes().as_ptr() as usize, b.len(), matches!(kind, 'i' | 'u'));
    let out = py.allow_threads(|| {
        // SAFETY: `b` holds the array's buffer view until the end of this function (the codec did this before).
        let s = unsafe { std::slice::from_raw_parts(p as *const u8, n) };
        let h = xxh3_128(s);
        let z = if delta && n > 0 { codec::compress(&delta_code(s, isz), zstd_id(), false) } else { codec::compress(s, zstd_id(), false) };
        z.map(|z| (z, h))
    });
    let Ok((z, h)) = out else { return slow(py, "pack_array", a) };
    let t = (PyBytes::new_bound(py, &z), dt.getattr("str")?, c.getattr("shape")?, format!("{:032x}", h), delta);
    Ok(t.into_py(py))
}

fn unpack_one<'py>(py: Python<'py>, p: &Bound<'py, PyAny>, np: &Bound<'py, PyModule>) -> PyResult<PyObject> {
    let fast = || -> PyResult<Option<PyObject>> {
        let Ok(t) = p.downcast_exact::<PyTuple>() else { return Ok(None) };
        if t.len() != 5 {
            return Ok(None);
        }
        let (blob, dts, shape, delta) = (t.get_item(0)?, t.get_item(1)?, t.get_item(2)?, t.get_item(4)?);
        let (Ok(blob), Ok(shape), Ok(delta)) = (blob.downcast_exact::<PyBytes>(), shape.downcast_exact::<PyTuple>(),
                                                 delta.downcast_exact::<PyBool>()) else { return Ok(None) };
        if !dts.is_exact_instance_of::<PyString>() {
            return Ok(None);
        }
        let Ok(dtype) = np.call_method1("dtype", (&dts,)) else { return Ok(None) };
        let s: String = dtype.getattr("str")?.extract()?;
        let kind = s.chars().nth(1).unwrap_or('?');
        let isz: usize = dtype.getattr("itemsize")?.extract()?;
        let native = cfg!(target_endian = "little") && (s.starts_with('<') || s.starts_with('|'));
        let delta = delta.is_true();
        let int = matches!(kind, 'i' | 'u') && matches!(isz, 1 | 2 | 4 | 8);
        if !native || !(int || (!delta && matches!(kind, 'b' | 'f'))) {
            return Ok(None);
        }
        let mut n: u64 = 1;
        for d in shape.iter() {
            let Ok(d) = d.downcast_exact::<PyInt>() else { return Ok(None) };
            let Ok(d) = d.extract::<u64>() else { return Ok(None) };
            let Some(m) = n.checked_mul(d) else { return Ok(None) };
            n = m;
        }
        let Some(len) = n.checked_mul(isz as u64).filter(|&l| l <= codec::MAX_RAW) else { return Ok(None) };
        let src = blob.as_bytes();
        let Ok(raw) = py.allow_threads(|| {
            codec::decompress(src, Some(len)).map(|mut r| {
                if delta {
                    running_sum(&mut r, isz);
                }
                r
            })
        }) else { return Ok(None) };
        let ba = PyByteArray::new_bound(py, &raw);
        Ok(Some(np.call_method1("frombuffer", (ba, dtype))?.call_method1("reshape", (shape,))?.unbind()))
    };
    match fast()? {
        Some(o) => Ok(o),
        None => slow(py, "unpack_array", p),
    }
}

#[pyfunction]
fn topo_pack(py: Python<'_>, a: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    guard(|| pack_one(py, a, &py.import_bound("numpy")?))
}

#[pyfunction]
fn topo_unpack(py: Python<'_>, p: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    guard(|| unpack_one(py, p, &py.import_bound("numpy")?))
}

#[pyfunction]
fn topo_hash(py: Python<'_>, a: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    guard(|| {
        let np = py.import_bound("numpy")?;
        if plain(a, &np)?.is_none() {
            return slow(py, "hash_of", a);
        }
        let c = np.call_method1("ascontiguousarray", (a,))?;
        let b = Buf::new(&c, false)?;
        let (p, n) = (b.bytes().as_ptr() as usize, b.len());
        // SAFETY: as in pack_one
        let h = py.allow_threads(|| xxh3_128(unsafe { std::slice::from_raw_parts(p as *const u8, n) }));
        Ok(format!("{:032x}", h).into_py(py))
    })
}

#[pyfunction]
fn topo_pack_all(py: Python<'_>, arrays: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    guard(|| {
        let Ok(d) = arrays.downcast_exact::<PyDict>() else { return slow(py, "pack_all", arrays) };
        let np = py.import_bound("numpy")?;
        let out = PyDict::new_bound(py);
        for (k, a) in d.iter() {
            out.set_item(k, pack_one(py, &a, &np)?)?;
        }
        Ok(out.into_any().unbind())
    })
}

#[pyfunction]
fn topo_packed_bytes(py: Python<'_>, packed: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    guard(|| {
        let mut n: usize = 0;
        if let Ok(d) = packed.downcast_exact::<PyDict>() {
            for v in d.values().iter() {
                let b = v.downcast_exact::<PyTuple>().ok().filter(|t| !t.is_empty()).and_then(|t| t.get_item(0).ok());
                match b.as_ref().and_then(|b| b.downcast_exact::<PyBytes>().ok()) {
                    Some(b) => n += b.as_bytes().len(),
                    None => return slow(py, "packed_bytes", packed),
                }
            }
            return Ok(n.into_py(py));
        }
        slow(py, "packed_bytes", packed)
    })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(topo_pack, m)?)?;
    m.add_function(wrap_pyfunction!(topo_unpack, m)?)?;
    m.add_function(wrap_pyfunction!(topo_hash, m)?)?;
    m.add_function(wrap_pyfunction!(topo_pack_all, m)?)?;
    m.add_function(wrap_pyfunction!(topo_packed_bytes, m)?)?;
    Ok(())
}
