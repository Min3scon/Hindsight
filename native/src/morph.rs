//! R083: one dab of the Morph History brush (hindsight/bl/morph.py `apply_morph_at`) in one pass.
//!
//! The Python it replaces (kept as tests/reference/morph_dab.py, the bit-exact oracle) was numpy over the
//! whole mesh: a box test, the distances of the candidates, the falloff, the mask, the stroke's strongest
//! weight per vertex, and the blend from the stroke's start toward the target. Every number here is made
//! the way numpy made it: float64 from the float32 positions, the three squares summed left to right (what
//! numpy's `sum(axis=1)` does over three columns in every version Blender ships), `1 - mask` in float32,
//! numpy's clip and maximum for NaN and signed zeros, and the result rounded to float32.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::{guard, Buf};

fn f32s(b: &Buf) -> &[f32] {
    // SAFETY: callers check len % 4 == 0; float32 arrays from numpy are 4-byte aligned (checked below).
    unsafe { std::slice::from_raw_parts(b.bytes().as_ptr() as *const f32, b.len() / 4) }
}

/// The array's numpy dtype is of this kind ('f', 'b', ...) and item size; false for anything else.
fn is(o: &Bound<'_, PyAny>, kind: &str, size: usize) -> bool {
    let k = o.getattr("dtype").and_then(|d| d.getattr("kind")).and_then(|k| k.extract::<String>());
    let s = o.getattr("itemsize").and_then(|s| s.extract::<usize>());
    matches!((k, s), (Ok(k), Ok(s)) if k == kind && s == size)
}

fn aligned(b: &Buf, a: usize) -> bool {
    b.len() == 0 || (b.bytes().as_ptr() as usize) % a == 0
}

/// numpy's maximum for floats: `a >= b or isnan(a) ? a : b`.
#[inline(always)]
fn np_maximum(a: f64, b: f64) -> f64 {
    if a >= b || a.is_nan() { a } else { b }
}

/// numpy's clip(x, 0, 1) for floats: min(max(x, 0), 1), NaN kept.
#[inline(always)]
fn np_clip01(x: f64) -> f64 {
    if x.is_nan() {
        return x;
    }
    let m = if x > 0.0 { x } else { 0.0 };
    if m < 1.0 { m } else { 1.0 }
}

/// morph_dab(co, start, target, acc, mask, hidden, cx, cy, cz, r, strength) -> moved
///
/// co, start, target: float32, 3 per vertex (co is written: the rows the brush covers get the blend of
/// start toward target, as `apply_morph_at` set them). acc: float64, one per vertex (the stroke's strongest
/// weight so far; updated). mask: float32 per vertex or None. hidden: one byte per vertex (numpy bool) or
/// None. Returns how many covered vertices now differ from what co held before the call.
#[pyfunction]
#[pyo3(signature = (co, start, target, acc, mask, hidden, cx, cy, cz, r, strength))]
#[allow(clippy::too_many_arguments)]
pub fn morph_dab(
    co: &Bound<'_, PyAny>,
    start: &Bound<'_, PyAny>,
    target: &Bound<'_, PyAny>,
    acc: &Bound<'_, PyAny>,
    mask: Option<&Bound<'_, PyAny>>,
    hidden: Option<&Bound<'_, PyAny>>,
    cx: f64,
    cy: f64,
    cz: f64,
    r: f64,
    strength: f64,
) -> PyResult<usize> {
    guard(|| {
        let mut cob = Buf::new(co, true)?;
        let sb = Buf::new(start, false)?;
        let tb = Buf::new(target, false)?;
        let mut ab = Buf::new(acc, true)?;
        let mb = match mask {
            Some(m) if !m.is_none() => Some(Buf::new(m, false)?),
            _ => None,
        };
        let hb = match hidden {
            Some(h) if !h.is_none() => Some(Buf::new(h, false)?),
            _ => None,
        };
        if !(is(co, "f", 4) && is(start, "f", 4) && is(target, "f", 4) && is(acc, "f", 8))
            || mask.is_some_and(|m| !m.is_none() && !is(m, "f", 4))
            || hidden.is_some_and(|h| !h.is_none() && !is(h, "b", 1))
        {
            return Err(PyValueError::new_err("morph_dab: co, start, target and mask must be float32, acc float64, hidden bool"));
        }
        let n3 = cob.len() / 4;
        if cob.len() % 12 != 0 || sb.len() != cob.len() || tb.len() != cob.len() {
            return Err(PyValueError::new_err("morph_dab: co, start and target must be float32, 3 per vertex, all the same size"));
        }
        let n = n3 / 3;
        if ab.len() != n * 8 {
            return Err(PyValueError::new_err("morph_dab: acc must be float64, one per vertex"));
        }
        if mb.as_ref().is_some_and(|m| m.len() != n * 4) || hb.as_ref().is_some_and(|h| h.len() != n) {
            return Err(PyValueError::new_err("morph_dab: mask (float32) and hidden (bool) need one value per vertex"));
        }
        if !aligned(&cob, 4) || !aligned(&sb, 4) || !aligned(&tb, 4) || !aligned(&ab, 8) || mb.as_ref().is_some_and(|m| !aligned(m, 4)) {
            return Err(PyValueError::new_err("morph_dab: unaligned buffer"));
        }
        if cob.overlaps(&sb) || cob.overlaps(&tb) || cob.overlaps(&ab) || ab.overlaps(&sb) || ab.overlaps(&tb)
            || mb.as_ref().is_some_and(|m| cob.overlaps(m) || ab.overlaps(m))
            || hb.as_ref().is_some_and(|h| cob.overlaps(h) || ab.overlaps(h))
        {
            return Err(PyValueError::new_err("morph_dab: co and acc must not share memory with the other arrays"));
        }
        let s = f32s(&sb);
        let t = f32s(&tb);
        let m = mb.as_ref().map(f32s);
        let h = hb.as_ref().map(|b| b.bytes());
        // SAFETY: lengths and alignment checked above; co and acc overlap nothing else.
        let c = unsafe { std::slice::from_raw_parts_mut(cob.bytes_mut().as_mut_ptr() as *mut f32, n3) };
        let a = unsafe { std::slice::from_raw_parts_mut(ab.bytes_mut().as_mut_ptr() as *mut f64, n) };
        let mut moved = 0usize;
        for i in 0..n {
            let k = 3 * i;
            let (x, y, z) = (c[k] as f64, c[k + 1] as f64, c[k + 2] as f64);
            // the box test (morph.in_box): every axis, NaN never inside, hidden vertices out
            let (dx, dy, dz) = (x - cx, y - cy, z - cz);
            if !(dx.abs() <= r && dy.abs() <= r && dz.abs() <= r) {
                continue;
            }
            if h.is_some_and(|h| h[i] != 0) {
                continue;
            }
            let d = ((dx * dx + dy * dy) + dz * dz).sqrt();
            if !(d < r) {
                continue;
            }
            // core.morph.falloff, times the strength
            let q = d / r;
            let f = np_clip01(1.0 - q * q);
            let mut w = f * f * strength;
            if let Some(m) = m {
                w *= (1.0f32 - m[i]) as f64;
            }
            let w = np_maximum(a[i], np_clip01(w));
            a[i] = w;
            // core.morph.morph_blend from the stroke's start, strength 1
            let f = np_clip01(w * 1.0);
            let nf = 1.0 - f;
            let mut changed = false;
            for j in 0..3 {
                let v = ((s[k + j] as f64) * nf + (t[k + j] as f64) * f) as f32;
                changed |= v != c[k + j];
                c[k + j] = v;
            }
            moved += changed as usize;
        }
        Ok(moved)
    })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(morph_dab, m)?)?;
    Ok(())
}
