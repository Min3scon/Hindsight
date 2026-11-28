//! R085: the Movie Mode camera's extents (hindsight/core/framing.py `extents`, `extents_multi`, and the history
//! walk `history_extents_multi`) in one pass, without copies.
//!
//! For each basis b and its tangents (tx, ty), each point p gives x, y, z = p @ b and the extents are
//! [max(x + z*tx), max(z*tx - x), max(y + z*ty), max(z*ty - y), min z, max z]; a row whose x, y or z isn't finite
//! is left out for that basis. Here p @ b is p0*b0j + p1*b1j + p2*b2j, left to right, in float64, as the
//! Python reference (tests/reference/framing_extents.py) writes it with numpy's elementwise operations. numpy's
//! matmul, used before, goes through BLAS, whose sums are fused and ordered its own way: the extents differ
//! from it in the last bits only.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::{guard, Buf};

fn itemsize(o: &Bound<'_, PyAny>) -> PyResult<usize> {
    o.getattr("itemsize")?.extract::<usize>()
}

/// numpy's dtype.kind of an array ('f' float, 'u' unsigned, ...); None for anything without one.
fn kind(o: &Bound<'_, PyAny>) -> Option<String> {
    o.getattr("dtype").ok()?.getattr("kind").ok()?.extract::<String>().ok()
}

fn aligned(b: &Buf, a: usize) -> bool {
    b.len() == 0 || (b.bytes().as_ptr() as usize) % a == 0
}

/// Points as float64 triples, whatever their float type.
enum Pts<'a> {
    F32(&'a [f32]),
    F64(&'a [f64]),
}

impl Pts<'_> {
    #[inline(always)]
    fn row(&self, i: usize) -> [f64; 3] {
        match self {
            Pts::F32(p) => [p[3 * i] as f64, p[3 * i + 1] as f64, p[3 * i + 2] as f64],
            Pts::F64(p) => [p[3 * i], p[3 * i + 1], p[3 * i + 2]],
        }
    }
}

struct View {
    m: [f64; 9],
    tx: f64,
    ty: f64,
}

#[inline(always)]
fn take(v: &View, p: [f64; 3], e: &mut [f64; 6]) -> bool {
    let m = &v.m;
    let x = p[0] * m[0] + p[1] * m[3] + p[2] * m[6];
    let y = p[0] * m[1] + p[1] * m[4] + p[2] * m[7];
    let z = p[0] * m[2] + p[1] * m[5] + p[2] * m[8];
    if !(x.is_finite() && y.is_finite() && z.is_finite()) {
        return false;
    }
    let zx = z * v.tx;
    let zy = z * v.ty;
    let c = [x + zx, zx - x, y + zy, zy - y];
    for j in 0..4 {
        // numpy's max: NaN wins (x + zx can't be NaN here, the operands are finite, but it can be inf)
        if c[j] > e[j] || c[j].is_nan() {
            e[j] = c[j];
        }
    }
    if z < e[4] {
        e[4] = z;
    }
    if z > e[5] {
        e[5] = z;
    }
    true
}

/// extents_into(points, idx, bases, tans, out) -> rows used
///
/// points: float32 or float64, 3 per point. idx: None (every point) or uint32 indices of the points to take.
/// bases: float64 (k, 3, 3); tans: float64 (k, 2); out: float64 (k, 6), merged in place (columns 0 to 3 and 5
/// by maximum, column 4 by minimum; start it at -inf, -inf, -inf, -inf, inf, -inf). Returns how many rows were
/// finite and taken, over all bases (0: out unchanged).
#[pyfunction]
#[pyo3(signature = (points, idx, bases, tans, out))]
pub fn extents_into(
    py: Python<'_>,
    points: &Bound<'_, PyAny>,
    idx: Option<&Bound<'_, PyAny>>,
    bases: &Bound<'_, PyAny>,
    tans: &Bound<'_, PyAny>,
    out: &Bound<'_, PyAny>,
) -> PyResult<usize> {
    guard(|| {
        let pb = Buf::new(points, false)?;
        let isz = itemsize(points)?;
        if kind(points).as_deref() != Some("f") || !matches!(isz, 4 | 8) || pb.len() % (3 * isz) != 0 || !aligned(&pb, isz) {
            return Err(PyValueError::new_err("extents_into: points must be float32 or float64, 3 per point"));
        }
        let n = pb.len() / (3 * isz);
        let ib = match idx {
            Some(i) if !i.is_none() => {
                if kind(i).as_deref() != Some("u") || itemsize(i)? != 4 {
                    return Err(PyValueError::new_err("extents_into: idx must be uint32"));
                }
                let b = Buf::new(i, false)?;
                if b.len() % 4 != 0 || !aligned(&b, 4) {
                    return Err(PyValueError::new_err("extents_into: idx must be uint32"));
                }
                Some(b)
            }
            _ => None,
        };
        let bb = Buf::new(bases, false)?;
        let tb = Buf::new(tans, false)?;
        let mut ob = Buf::new(out, true)?;
        let f64_ok = |o: &Bound<'_, PyAny>| kind(o).as_deref() == Some("f") && itemsize(o).ok() == Some(8);
        if !f64_ok(bases) || !f64_ok(tans) || !f64_ok(out) {
            return Err(PyValueError::new_err("extents_into: bases, tans and out must be float64"));
        }
        let k = bb.len() / 72;
        if bb.len() != k * 72 || tb.len() != k * 16 || ob.len() != k * 48 || !aligned(&bb, 8) || !aligned(&tb, 8) || !aligned(&ob, 8) {
            return Err(PyValueError::new_err("extents_into: bases (k, 3, 3), tans (k, 2) and out (k, 6) must agree"));
        }
        if ob.overlaps(&pb) || ob.overlaps(&bb) || ob.overlaps(&tb) || ib.as_ref().is_some_and(|i| ob.overlaps(i)) {
            return Err(PyValueError::new_err("extents_into: out shares memory with an input"));
        }
        // SAFETY: lengths and alignment checked above; the views are held until the end of the call
        let f64s = |b: &Buf| unsafe { std::slice::from_raw_parts(b.bytes().as_ptr() as *const f64, b.len() / 8) };
        let views: Vec<View> = {
            let (m, t) = (f64s(&bb), f64s(&tb));
            (0..k).map(|i| {
                let mut a = [0.0; 9];
                a.copy_from_slice(&m[9 * i..9 * i + 9]);
                View { m: a, tx: t[2 * i], ty: t[2 * i + 1] }
            }).collect()
        };
        let pts = if isz == 4 {
            Pts::F32(unsafe { std::slice::from_raw_parts(pb.bytes().as_ptr() as *const f32, 3 * n) })
        } else {
            Pts::F64(unsafe { std::slice::from_raw_parts(pb.bytes().as_ptr() as *const f64, 3 * n) })
        };
        let ids: Option<&[u32]> = ib.as_ref().map(|b| unsafe { std::slice::from_raw_parts(b.bytes().as_ptr() as *const u32, b.len() / 4) });
        if let Some(ids) = ids {
            if ids.iter().any(|&i| i as usize >= n) {
                return Err(PyValueError::new_err("extents_into: an index is out of range"));
            }
        }
        let o = unsafe { std::slice::from_raw_parts_mut(ob.bytes_mut().as_mut_ptr() as *mut f64, 6 * k) };
        let mut ext: Vec<[f64; 6]> = (0..k).map(|i| {
            let mut e = [0.0; 6];
            e.copy_from_slice(&o[6 * i..6 * i + 6]);
            e
        }).collect();
        let used = py.allow_threads(|| {
            let mut used = 0usize;
            let mut one = |p: [f64; 3]| {
                for (v, e) in views.iter().zip(ext.iter_mut()) {
                    used += take(v, p, e) as usize;
                }
            };
            match ids {
                Some(ids) => ids.iter().for_each(|&i| one(pts.row(i as usize))),
                None => (0..n).for_each(|i| one(pts.row(i))),
            }
            used
        });
        for (i, e) in ext.iter().enumerate() {
            o[6 * i..6 * i + 6].copy_from_slice(e);
        }
        Ok(used)
    })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extents_into, m)?)?;
    Ok(())
}
