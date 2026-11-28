//! The caps, the tiers and the caps memo (#154, 24.5a): `enforce_tiers`, `enforce_caps`, `total_bytes`, `enforce`
//! with its one-history pass, `CapsMemo`, `caps_sig`, `packer_key` and `store_can_gain`. The Python they replace is
//! tests/reference/store.py. Histories, entries, the packer, the settings and the stats stay Python objects
//! (duck-typed); every function does what the Python did, in the same order, through the same Python operations
//! (attribute reads, calls, `int()`, `+=`, rich comparisons, heapq), so results and exceptions are the reference's.
//! Only exact `bytes` and `bytearray` are read in Rust (`store_can_gain`: their items are ints 0 to 255).
use pyo3::ffi;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::pyclass::CompareOp;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyByteArray, PyBytes, PyDict, PyFrozenSet, PyList, PyModule, PyTuple};
use pyo3::{PyTraverseError, PyVisit};

const STEP_MAGIC: &[u8; 4] = b"HSS3";
const QUICK_FLAG: u8 = 8; // step.QUICK_FLAG
const STRONG: [u8; 3] = [4, 5, 0]; // codec.ZSTD_MAX, ZLIB_MAX, NONE: codec.is_strong's codes
const EVICT_TO: f64 = 0.95; // store.EVICT_TO

static HEAPQ: GILOnceCell<Py<PyModule>> = GILOnceCell::new();
static STRONG_SET: GILOnceCell<PyObject> = GILOnceCell::new();

fn heapq(py: Python<'_>) -> PyResult<&Bound<'_, PyModule>> {
    Ok(HEAPQ.get_or_try_init(py, || py.import_bound("heapq").map(Bound::unbind))?.bind(py))
}

fn int_obj(py: Python<'_>, v: i64) -> Bound<'_, PyAny> {
    v.to_object(py).into_bound(py)
}

fn owned<'py>(py: Python<'py>, p: *mut ffi::PyObject) -> PyResult<Bound<'py, PyAny>> {
    unsafe { Bound::from_owned_ptr_or_err(py, p) }
}

/// `int(x)`
fn int_<'py>(x: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    owned(x.py(), unsafe { ffi::PyNumber_Long(x.as_ptr()) })
}

/// `a += b`
fn iadd<'py>(a: &Bound<'py, PyAny>, b: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    owned(a.py(), unsafe { ffi::PyNumber_InPlaceAdd(a.as_ptr(), b.as_ptr()) })
}

/// `a -= b`
fn isub<'py>(a: &Bound<'py, PyAny>, b: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    owned(a.py(), unsafe { ffi::PyNumber_InPlaceSubtract(a.as_ptr(), b.as_ptr()) })
}

/// `max(lo, v)`: v if v > lo else lo
fn max_<'py>(lo: i64, v: Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    Ok(if v.gt(lo)? { v } else { int_obj(v.py(), lo) })
}

/// `min(a, b)`: b if b < a else a
fn min_<'py>(a: Bound<'py, PyAny>, b: Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    Ok(if b.lt(&a)? { b } else { a })
}

/// `id(x)`
fn id_(x: &Bound<'_, PyAny>) -> PyObject {
    (x.as_ptr() as usize).to_object(x.py())
}

fn len_obj<'py>(x: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    Ok(x.len()?.to_object(x.py()).into_bound(x.py()))
}

fn nbytes<'py>(e: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    e.call_method0(intern!(e.py(), "nbytes"))
}

/// `x.meta.get("seq", 0)`
fn seq_of<'py>(e: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let py = e.py();
    e.getattr(intern!(py, "meta"))?.call_method1(intern!(py, "get"), (intern!(py, "seq"), 0))
}

/// `sum(e.nbytes() for e in entries)`
fn sum_nbytes<'py>(entries: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let mut s = int_obj(entries.py(), 0);
    for e in entries.iter()? {
        s = s.add(nbytes(&e?)?)?;
    }
    Ok(s)
}

fn can_gain_bytes(b: &[u8]) -> bool {
    let n = b.len();
    if n < 13 || &b[..4] != STEP_MAGIC {
        return true;
    }
    let hl = b[8] as usize | ((b[9] as usize) << 8);
    if n <= 10 + hl {
        return true;
    }
    b[12] & QUICK_FLAG != 0 || !STRONG.contains(&b[10 + hl])
}

/// store._can_gain: False for a blob step.recompress() would leave as it is without trying, told from its first
/// bytes alone.
#[pyfunction]
pub(crate) fn store_can_gain(py: Python<'_>, blob: &Bound<'_, PyAny>) -> PyResult<bool> {
    if let Ok(b) = blob.downcast_exact::<PyBytes>() {
        return Ok(can_gain_bytes(b.as_bytes()));
    }
    if let Ok(b) = blob.downcast_exact::<PyByteArray>() {
        return Ok(can_gain_bytes(&b.to_vec()));
    }
    let n = blob.len()?;
    if n < 13 {
        return Ok(true);
    }
    let four = int_obj(py, 4);
    let head = blob.get_item(owned(py, unsafe { ffi::PySlice_New(std::ptr::null_mut(), four.as_ptr(), std::ptr::null_mut()) })?)?;
    if head.rich_compare(PyBytes::new_bound(py, STEP_MAGIC), CompareOp::Ne)?.is_truthy()? {
        return Ok(true);
    }
    let b8 = blob.get_item(8)?;
    let b9 = blob.get_item(9)?;
    let hl = b8.bitor(b9.lshift(8)?)?;
    let n = n.to_object(py).into_bound(py);
    if n.le(int_obj(py, 10).add(&hl)?)? {
        return Ok(true);
    }
    if blob.get_item(12)?.bitand(QUICK_FLAG)?.is_truthy()? {
        return Ok(true);
    }
    let code = blob.get_item(int_obj(py, 10).add(&hl)?)?;
    let strong = STRONG_SET.get_or_try_init(py, || PyFrozenSet::new_bound(py, &STRONG).map(|s| s.into_any().unbind()))?;
    Ok(!strong.bind(py).contains(code)?)
}

/// store.enforce_tiers: pack every entry older than the newest `settings.raw_steps` of each history.
#[pyfunction]
#[pyo3(signature = (histories, settings, packer=None))]
fn enforce_tiers<'py>(py: Python<'py>, histories: &Bound<'py, PyAny>, settings: &Bound<'py, PyAny>,
                      packer: Option<&Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyAny>> {
    let raw = max_(0, int_(&settings.getattr(intern!(py, "raw_steps"))?)?)?;
    let codec = settings.getattr(intern!(py, "compression"))?;
    let mut saved = int_obj(py, 0);
    for h in histories.iter()? {
        let entries = h?.getattr(intern!(py, "entries"))?;
        // range(len(entries) - raw): raw >= 0, so only a hugely negative count doesn't fit
        let n: i64 = len_obj(&entries)?.sub(&raw)?.extract().unwrap_or(i64::MIN);
        for i in 0..n.max(0) {
            let e = entries.get_item(i)?;
            if e.getattr(intern!(py, "step"))?.is_none() {
                continue;
            }
            match packer {
                Some(p) => {
                    p.call_method1(intern!(py, "submit"), (&e, &codec))?;
                }
                None => {
                    let b = nbytes(&e)?;
                    e.call_method1(intern!(py, "pack"), (&codec,))?;
                    let d = b.sub(nbytes(&e)?)?;
                    saved = iadd(&saved, &d)?;
                }
            }
        }
    }
    Ok(saved)
}

/// store.total_bytes
#[pyfunction]
fn total_bytes<'py>(histories: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let py = histories.py();
    let mut s = int_obj(py, 0);
    for h in histories.iter()? {
        for e in h?.getattr(intern!(py, "entries"))?.iter()? {
            s = s.add(nbytes(&e?)?)?;
        }
    }
    int_(&s)
}

/// store._drop
fn drop_<'py>(h: &Bound<'py, PyAny>, n: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let py = h.py();
    if n.le(0)? {
        return Ok(int_obj(py, 0));
    }
    let r = h.call_method1(intern!(py, "drop_oldest"), (n,))?;
    if r.is_none() { Ok(n.clone()) } else { int_(&r) }
}

/// store.enforce_caps: max_steps per history, then the global memory limit (oldest seq first); never a step at or
/// past a history's cursor.
#[pyfunction]
#[pyo3(signature = (histories, settings, stats=None))]
fn enforce_caps<'py>(py: Python<'py>, histories: &Bound<'py, PyAny>, settings: &Bound<'py, PyAny>,
                     stats: Option<&Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyAny>> {
    let mut dropped = int_obj(py, 0);
    let max_steps = max_(1, int_(&settings.getattr(intern!(py, "max_steps"))?)?)?;
    for h in histories.iter()? {
        let h = h?;
        let a = len_obj(&h.getattr(intern!(py, "entries"))?)?.sub(&max_steps)?;
        let over = min_(a, int_(&h.getattr(intern!(py, "cursor"))?)?)?;
        if over.gt(0)? {
            dropped = iadd(&dropped, &drop_(&h, &over)?)?;
        }
    }
    let limit = int_(&settings.getattr(intern!(py, "memory_limit_mb"))?)?.mul(1i64 << 20)?;
    let mut total = total_bytes(histories)?;
    if let Some(st) = stats {
        st.set_item(intern!(py, "memory_dropped"), 0)?;
        st.set_item(intern!(py, "total"), &total)?;
        st.set_item(intern!(py, "stuck"), false)?;
    }
    if total.le(&limit)? {
        return Ok(dropped);
    }
    // plan: merge histories by seq of their oldest remaining entry; count drops per history
    let mut plan = vec![0i64; histories.len()?];
    let heap = PyList::empty_bound(py);
    for (i, h) in histories.iter()?.enumerate() {
        let h = h?;
        if !h.getattr(intern!(py, "entries"))?.is_truthy()? || !h.getattr(intern!(py, "cursor"))?.gt(0)? {
            continue;
        }
        let seq = seq_of(&h.getattr(intern!(py, "entries"))?.get_item(0)?)?;
        heap.append(PyTuple::new_bound(py, [seq.unbind(), i.to_object(py)]))?;
    }
    let hq = heapq(py)?;
    hq.call_method1(intern!(py, "heapify"), (&heap,))?;
    let target = int_(&limit.mul(EVICT_TO)?)?;
    while total.gt(&target)? && heap.len() > 0 {
        let item = hq.call_method1(intern!(py, "heappop"), (&heap,))?;
        let i: usize = item.get_item(1)?.extract()?;
        let entries = histories.get_item(i)?.getattr(intern!(py, "entries"))?;
        let j = plan[i];
        total = isub(&total, &nbytes(&entries.get_item(j)?)?)?;
        plan[i] = j + 1;
        let cap = min_(len_obj(&entries)?, int_(&histories.get_item(i)?.getattr(intern!(py, "cursor"))?)?)?;
        if int_obj(py, j + 1).lt(cap)? {
            let seq = seq_of(&entries.get_item(j + 1)?)?;
            hq.call_method1(intern!(py, "heappush"), (&heap, PyTuple::new_bound(py, [seq.unbind(), i.to_object(py)])))?;
        }
    }
    for (i, n) in plan.iter().enumerate() {
        dropped = iadd(&dropped, &drop_(&histories.get_item(i)?, &int_obj(py, *n))?)?;
    }
    if let Some(st) = stats {
        st.set_item(intern!(py, "memory_dropped"), plan.iter().sum::<i64>())?;
        st.set_item(intern!(py, "total"), &total)?;
        st.set_item(intern!(py, "stuck"), total.rich_compare(&target, CompareOp::Gt)?)?; // the loop ran out of steps that may go
    }
    Ok(dropped)
}

/// store._sig: changes when a history's steps or cursor change (not when its steps are packed).
#[pyfunction]
fn caps_sig<'py>(h: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyTuple>> {
    let py = h.py();
    let e = h.getattr(intern!(py, "entries"))?;
    let (ih, ie, n) = (id_(h), id_(&e), e.len()?.to_object(py));
    let cur = int_(&h.getattr(intern!(py, "cursor"))?)?.unbind();
    let first = if e.is_truthy()? { id_(&e.get_item(0)?) } else { 0.to_object(py) };
    let last = if e.is_truthy()? { id_(&e.get_item(-1)?) } else { 0.to_object(py) };
    Ok(PyTuple::new_bound(py, [ih, ie, n, cur, first, last]))
}

/// store._packer_key: changes whenever the packer has changed the size of a step.
#[pyfunction]
#[pyo3(signature = (packer))]
fn packer_key(py: Python<'_>, packer: Option<&Bound<'_, PyAny>>) -> PyResult<PyObject> {
    Ok(match packer {
        None => py.None(),
        Some(p) => PyTuple::new_bound(py, [p.getattr(intern!(py, "packed"))?, p.getattr(intern!(py, "recompressed"))?,
                                           p.getattr(intern!(py, "errors"))?]).into_any().unbind(),
    })
}

fn settings_key<'py>(s: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyTuple>> {
    let py = s.py();
    Ok(PyTuple::new_bound(py, [int_(&s.getattr(intern!(py, "max_steps"))?)?,
                               int_(&s.getattr(intern!(py, "memory_limit_mb"))?)?,
                               int_(&s.getattr(intern!(py, "raw_steps"))?)?,
                               s.getattr(intern!(py, "compression"))?]))
}

/// What a pass of `enforce` that couldn't get under the memory limit found (S4-3): while nothing but one history has
/// changed since, the next pass only looks at that one (`_enforce_one`), and does exactly what the whole pass would.
#[pyclass(name = "CapsMemo", module = "hindsight_native")]
struct CapsMemo {
    #[pyo3(get, set)]
    state: PyObject,
}

#[pymethods]
impl CapsMemo {
    #[new]
    fn new(py: Python<'_>) -> Self {
        CapsMemo { state: py.None() }
    }

    fn clear(&mut self, py: Python<'_>) {
        self.state = py.None();
    }

    fn remember(slf: &Bound<'_, Self>, histories: &Bound<'_, PyAny>, total: &Bound<'_, PyAny>, pkey: PyObject,
                skey: PyObject) -> PyResult<()> {
        let py = slf.py();
        let sigs = PyDict::new_bound(py);
        for h in histories.iter()? {
            let h = h?;
            let k = h.getattr(intern!(py, "uid"))?;
            sigs.set_item(k, caps_sig(&h)?)?;
        }
        let bytes = PyDict::new_bound(py);
        for h in histories.iter()? {
            let h = h?;
            let k = h.getattr(intern!(py, "uid"))?;
            bytes.set_item(k, sum_nbytes(&h.getattr(intern!(py, "entries"))?)?)?;
        }
        let st = PyDict::new_bound(py);
        st.set_item(intern!(py, "sigs"), sigs)?;
        st.set_item(intern!(py, "bytes"), bytes)?;
        st.set_item(intern!(py, "total"), int_(total)?)?;
        st.set_item(intern!(py, "packer"), pkey)?;
        st.set_item(intern!(py, "settings"), skey)?;
        slf.borrow_mut().state = st.into_any().unbind();
        Ok(())
    }

    #[pyo3(signature = (histories, changed, pkey, skey))]
    fn usable(slf: &Bound<'_, Self>, histories: &Bound<'_, PyAny>, changed: Option<&Bound<'_, PyAny>>,
              pkey: &Bound<'_, PyAny>, skey: &Bound<'_, PyAny>) -> PyResult<bool> {
        let py = slf.py();
        let st = slf.borrow().state.clone_ref(py).into_bound(py); // no borrow held while Python runs
        let Some(changed) = changed else { return Ok(false) };
        if st.is_none() || st.get_item(intern!(py, "packer"))?.ne(pkey)?
            || st.get_item(intern!(py, "settings"))?.ne(skey)? {
            return Ok(false);
        }
        let sigs = st.get_item(intern!(py, "sigs"))?;
        if sigs.len()? != histories.len()? || !sigs.contains(changed.getattr(intern!(py, "uid"))?)? {
            return Ok(false);
        }
        for h in histories.iter()? {
            let h = h?;
            if !h.is(changed) {
                let had = sigs.call_method1(intern!(py, "get"), (h.getattr(intern!(py, "uid"))?,))?;
                if had.ne(caps_sig(&h)?)? {
                    return Ok(false);
                }
            }
        }
        Ok(true)
    }

    fn __traverse__(&self, v: PyVisit<'_>) -> Result<(), PyTraverseError> {
        v.call(&self.state)
    }

    fn __clear__(&mut self) {
        Python::with_gil(|py| self.state = py.None());
    }
}

/// store.enforce: enforce_caps, then enforce_tiers. Returns [(history, steps dropped)] for the histories that lost
/// steps; on_caps(that list, stats) is called with it before the packing starts. With `memo` and `changed`, a pass
/// that can only drop steps of `changed` looks at nothing else (CapsMemo).
#[pyfunction]
#[pyo3(signature = (histories, settings, packer=None, stats=None, memo=None, changed=None, on_caps=None))]
fn enforce<'py>(py: Python<'py>, histories: &Bound<'py, PyAny>, settings: &Bound<'py, PyAny>,
                packer: Option<&Bound<'py, PyAny>>, stats: Option<&Bound<'py, PyAny>>,
                memo: Option<&Bound<'py, PyAny>>, changed: Option<&Bound<'py, PyAny>>,
                on_caps: Option<&Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyList>> {
    let stats = match stats {
        Some(s) => s.clone(),
        None => PyDict::new_bound(py).into_any(),
    };
    let pkey = packer_key(py, packer)?;
    let skey = settings_key(settings)?;
    if let Some(m) = memo {
        let ch = changed.map_or_else(|| py.None(), |c| c.clone().unbind());
        if m.call_method1(intern!(py, "usable"), (histories, ch, &pkey, &skey))?.is_truthy()? {
            return enforce_one(changed.unwrap_or(&py.None().into_bound(py)), settings, packer, &stats, m, on_caps);
        }
        m.call_method0(intern!(py, "clear"))?;
    }
    let mut before = Vec::new();
    for h in histories.iter()? {
        before.push(h?.getattr(intern!(py, "entries"))?.len()?);
    }
    enforce_caps(py, histories, settings, Some(&stats))?;
    let drops = PyList::empty_bound(py);
    for (h, n) in histories.iter()?.zip(before.iter()) {
        let h = h?;
        if *n != h.getattr(intern!(py, "entries"))?.len()? {
            let d = *n as i64 - h.getattr(intern!(py, "entries"))?.len()? as i64;
            drops.append(PyTuple::new_bound(py, [h.unbind(), d.to_object(py)]))?;
        }
    }
    if let Some(cb) = on_caps {
        cb.call1((&drops, &stats))?;
    }
    let saved = enforce_tiers(py, histories, settings, packer)?;
    let t = stats.get_item(intern!(py, "total"))?.sub(saved)?;
    stats.set_item(intern!(py, "total"), t)?;
    if let Some(m) = memo {
        if stats.get_item(intern!(py, "stuck"))?.is_truthy()? {
            m.call_method1(intern!(py, "remember"), (histories, stats.get_item(intern!(py, "total"))?, &pkey, &skey))?;
            let t = m.getattr(intern!(py, "state"))?.get_item(intern!(py, "total"))?;
            stats.set_item(intern!(py, "total"), t)?;
        }
    }
    Ok(drops)
}

/// store._enforce_one: enforce for the case CapsMemo describes, the other histories have nothing that may go.
fn enforce_one<'py>(h: &Bound<'py, PyAny>, settings: &Bound<'py, PyAny>, packer: Option<&Bound<'py, PyAny>>,
                    stats: &Bound<'py, PyAny>, memo: &Bound<'py, PyAny>, on_caps: Option<&Bound<'py, PyAny>>)
                    -> PyResult<Bound<'py, PyList>> {
    let py = h.py();
    let st = memo.getattr(intern!(py, "state"))?;
    let t0 = st.get_item(intern!(py, "total"))?;
    let mine = st.get_item(intern!(py, "bytes"))?.get_item(h.getattr(intern!(py, "uid"))?)?;
    let others = t0.sub(mine)?;
    let n0 = h.getattr(intern!(py, "entries"))?.len()? as i64;
    let max_steps = max_(1, int_(&settings.getattr(intern!(py, "max_steps"))?)?)?;
    let a = len_obj(&h.getattr(intern!(py, "entries"))?)?.sub(&max_steps)?;
    let over = min_(a, int_(&h.getattr(intern!(py, "cursor"))?)?)?;
    if over.gt(0)? {
        drop_(h, &over)?;
    }
    let hb = sum_nbytes(&h.getattr(intern!(py, "entries"))?)?;
    let mut total = others.add(&hb)?;
    let limit = int_(&settings.getattr(intern!(py, "memory_limit_mb"))?)?.mul(1i64 << 20)?;
    stats.set_item(intern!(py, "memory_dropped"), 0)?;
    let mut stuck = false.to_object(py).into_bound(py);
    if total.gt(&limit)? {
        let target = int_(&limit.mul(EVICT_TO)?)?;
        let entries = h.getattr(intern!(py, "entries"))?;
        let cap = min_(len_obj(&entries)?, int_(&h.getattr(intern!(py, "cursor"))?)?)?;
        let mut j = 0i64;
        while total.gt(&target)? && int_obj(py, j).lt(&cap)? {
            total = isub(&total, &nbytes(&entries.get_item(j)?)?)?;
            j += 1;
        }
        drop_(h, &int_obj(py, j))?;
        stats.set_item(intern!(py, "memory_dropped"), j)?;
        stuck = total.rich_compare(&target, CompareOp::Gt)?;
    }
    let n = n0 - h.getattr(intern!(py, "entries"))?.len()? as i64;
    let drops = PyList::empty_bound(py);
    if n != 0 {
        drops.append(PyTuple::new_bound(py, [h.clone().unbind(), n.to_object(py)]))?;
    }
    if let Some(cb) = on_caps {
        cb.call1((&drops, stats))?;
    }
    let saved = enforce_tiers(py, &PyList::new_bound(py, [h]), settings, packer)?;
    total = isub(&total, &saved)?;
    stats.set_item(intern!(py, "total"), &total)?;
    if stuck.is_truthy()? {
        let sig = caps_sig(h)?;
        st.get_item(intern!(py, "sigs"))?.set_item(h.getattr(intern!(py, "uid"))?, sig)?;
        let b = total.sub(&others)?;
        st.get_item(intern!(py, "bytes"))?.set_item(h.getattr(intern!(py, "uid"))?, b)?;
        st.set_item(intern!(py, "total"), &total)?;
    } else {
        memo.call_method0(intern!(py, "clear"))?;
    }
    Ok(drops)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CapsMemo>()?;
    m.add_function(wrap_pyfunction!(store_can_gain, m)?)?;
    m.add_function(wrap_pyfunction!(enforce_tiers, m)?)?;
    m.add_function(wrap_pyfunction!(total_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(enforce_caps, m)?)?;
    m.add_function(wrap_pyfunction!(caps_sig, m)?)?;
    m.add_function(wrap_pyfunction!(packer_key, m)?)?;
    m.add_function(wrap_pyfunction!(enforce, m)?)?;
    Ok(())
}
