//! The shapes every step is made of (#147, 24.3b): `Step`, `ChannelDelta`, `Snapshot`, `DataChange`, `Slice`,
//! `Entry`, the key helpers, `MISSING`; `step_from_bytes`, `snapshot_from_bytes` and `same_step`. The Python they
//! replace is in tests/reference/types.py and step_build.py. Their values stay Python objects, held by the engine.
//! Every method does what the Python did, in the same order, through the same Python operations, so results and
//! exceptions are the reference's. What the dataclasses generated (`__init__`'s argument errors, `__eq__`,
//! `__repr__`) is run from dataclasses made in `FALLBACK`, so it follows each Python version as the reference does.
use pyo3::basic::CompareOp;
use pyo3::exceptions::{PyIndexError, PyKeyError, PyRecursionError, PyTypeError, PyUnicodeDecodeError, PyValueError};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyDict, PyList, PyModule, PyString, PyTuple};
use pyo3::PyTypeInfo;
use pyo3::{PyTraverseError, PyVisit};

// The cycle collector sees what each object holds, as it does through a dataclass (gc.get_referents, cycles freed).
macro_rules! gc_visit { ($v:ident, $s:ident; $($f:ident),*) => {{ $($v.call(&$s.$f)?;)* Ok(()) }} }
macro_rules! gc_clear { ($s:ident; $($f:ident),*) => { Python::with_gil(|py| { $($s.$f = py.None();)* }) } }

const FALLBACK: &str = r#"
from dataclasses import dataclass, field
import builtins, operator
iadd = operator.iadd


@dataclass
class ChannelDelta:
    key: object
    idx: object
    vals: object
    exists: tuple = (True, True)
    op: int = 0


@dataclass
class Snapshot:
    counts: object
    arrays: object


@dataclass
class DataChange:
    adapter: object
    target: object
    changes: object


@dataclass
class Step:
    seq: object
    label: object
    mode: object
    target: object
    time: float = 0.0
    kind: str = "DELTA"
    deltas: list = field(default_factory=list)
    before: object = None
    after: object = None
    extra: dict = field(default_factory=dict)
    records: list = field(default_factory=list)


class Slice:
    def __init__(self, part, off, n):
        pass


class Entry:
    def __init__(self, step=None, blob=None, meta=None, crc=None):
        pass


def unpack2(v):
    a, b = v
    return a, b


def unpack3(v):
    a, b, c = v
    return a, b, c


def _value_size(v) -> int:
    if isinstance(v, (list, tuple)):
        return 16 + sum(_value_size(x) for x in v)
    if isinstance(v, str):
        return 16 + len(v)
    return 16
"#;

static PY: GILOnceCell<Py<PyModule>> = GILOnceCell::new();
static SETUP: GILOnceCell<(PyObject, PyObject, PyObject)> = GILOnceCell::new(); // CHANNEL_TYPES, BITS_DTYPE, step module
static NO_BLOB: GILOnceCell<PyObject> = GILOnceCell::new();
static EXISTS: GILOnceCell<PyObject> = GILOnceCell::new();
static TO_BYTES: GILOnceCell<PyObject> = GILOnceCell::new();

fn fb(py: Python<'_>) -> PyResult<&Bound<'_, PyModule>> {
    Ok(PY
        .get_or_try_init(py, || -> PyResult<Py<PyModule>> {
            Ok(PyModule::from_code_bound(py, FALLBACK, "hindsight_native/types.py", "hindsight_native._types")?.unbind())
        })?
        .bind(py))
}

fn builtin<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    fb(py)?.getattr(intern!(py, "builtins"))?.getattr(name)
}

fn np(py: Python<'_>) -> PyResult<Bound<'_, PyModule>> {
    py.import_bound(intern!(py, "numpy"))
}

fn eng(py: Python<'_>) -> PyResult<Bound<'_, PyModule>> {
    py.import_bound(intern!(py, "hindsight_native"))
}

fn setup(py: Python<'_>) -> PyResult<&(PyObject, PyObject, PyObject)> {
    SETUP.get(py).ok_or_else(|| PyTypeError::new_err("hindsight_native: types_setup was not called"))
}

/// `hindsight/core/types.py` hands its tables over: the channel types, the bits dtypes and the name of the step
/// module whose `step_to_bytes` `Entry.pack` and `Entry.to_bytes` call.
#[pyfunction]
fn types_setup(py: Python<'_>, channel_types: PyObject, bits_dtype: PyObject, step_module: PyObject) -> PyResult<()> {
    let _ = SETUP.set(py, (channel_types, bits_dtype, step_module));
    Ok(())
}

fn iadd<'py>(py: Python<'py>, a: &Bound<'py, PyAny>, b: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    fb(py)?.getattr(intern!(py, "iadd"))?.call1((a, b))
}

/// `a, b = v` (exact tuples and lists of two read here; anything else by Python, for its own errors).
fn unpack2<'py>(py: Python<'py>, v: &Bound<'py, PyAny>) -> PyResult<(Bound<'py, PyAny>, Bound<'py, PyAny>)> {
    if let Ok(t) = v.downcast_exact::<PyTuple>() {
        if t.len() == 2 {
            return Ok((t.get_item(0)?, t.get_item(1)?));
        }
    }
    let r = fb(py)?.getattr(intern!(py, "unpack2"))?.call1((v,))?;
    Ok((r.get_item(0)?, r.get_item(1)?))
}

fn unpack3<'py>(py: Python<'py>, v: &Bound<'py, PyAny>) -> PyResult<[Bound<'py, PyAny>; 3]> {
    let r = match v.downcast_exact::<PyList>() {
        Ok(l) if l.len() == 3 => v.clone(),
        _ => match v.downcast_exact::<PyTuple>() {
            Ok(t) if t.len() == 3 => v.clone(),
            _ => fb(py)?.getattr(intern!(py, "unpack3"))?.call1((v,))?,
        },
    };
    Ok([r.get_item(0)?, r.get_item(1)?, r.get_item(2)?])
}

/// The constructor's arguments by name, as the dataclass's `__init__` binds them; on anything it can't bind, that
/// `__init__` (made in FALLBACK) is called with them, so the TypeError is CPython's own.
fn bind<'py>(py: Python<'py>, cls: &str, names: &[&str], nreq: usize, args: &Bound<'py, PyTuple>,
             kw: Option<&Bound<'py, PyDict>>) -> PyResult<Vec<Option<Bound<'py, PyAny>>>> {
    let mut out: Vec<Option<Bound<'py, PyAny>>> = vec![None; names.len()];
    let mut ok = args.len() <= names.len();
    if ok {
        for (i, a) in args.iter().enumerate() {
            out[i] = Some(a);
        }
        if let Some(kw) = kw {
            for (k, v) in kw.iter() {
                let at = k.downcast_exact::<PyString>().ok().and_then(|s| s.to_str().ok())
                    .and_then(|s| names.iter().position(|n| *n == s));
                match at {
                    Some(i) if out[i].is_none() => out[i] = Some(v),
                    _ => { ok = false; break; }
                }
            }
        }
        ok = ok && out[..nreq].iter().all(|x| x.is_some());
    }
    if !ok {
        let c = fb(py)?.getattr(cls)?;
        let dummy = c.call_method1(intern!(py, "__new__"), (&c,))?;
        let mut full = vec![dummy];
        full.extend(args.iter());
        let full = PyTuple::new_bound(py, full);
        c.getattr(intern!(py, "__init__"))?.call(full, kw)?;
        return Err(PyTypeError::new_err(format!("{cls}(): bad arguments")));
    }
    Ok(out)
}

fn take(v: &mut [Option<Bound<'_, PyAny>>], i: usize, default: impl FnOnce() -> PyResult<PyObject>) -> PyResult<PyObject> {
    match v[i].take() {
        Some(x) => Ok(x.unbind()),
        None => default(),
    }
}

fn dc_cmp(slf: &Bound<'_, PyAny>, other: &Bound<'_, PyAny>, op: CompareOp, cls: &str) -> PyResult<PyObject> {
    let py = slf.py();
    let eq = || fb(py)?.getattr(cls)?.getattr(intern!(py, "__eq__"))?.call1((slf, other));
    match op {
        CompareOp::Eq => Ok(eq()?.unbind()),
        CompareOp::Ne => {
            let r = eq()?;
            if r.is(&py.NotImplemented()) {
                return Ok(r.unbind());
            }
            Ok((!r.is_truthy()?).into_py(py))
        }
        _ => Ok(py.NotImplemented()),
    }
}

fn dc_repr(slf: &Bound<'_, PyAny>, cls: &str) -> PyResult<PyObject> {
    let py = slf.py();
    Ok(fb(py)?.getattr(cls)?.getattr(intern!(py, "__repr__"))?.call1((slf,))?.unbind())
}

fn reduce(slf: &Bound<'_, PyAny>, names: &[&str]) -> PyResult<PyObject> {
    let py = slf.py();
    let vals = names.iter().map(|n| slf.getattr(*n)).collect::<PyResult<Vec<_>>>()?;
    Ok((slf.get_type(), PyTuple::new_bound(py, vals)).into_py(py))
}

// The classes are named without their module (tp_name "Step", not "hindsight_native.Step"), so CPython's messages
// ("unhashable type: 'Step'", "'Step' object has no attribute ...") read as the dataclasses' did: register() sets
// each class's __name__ (which sets tp_name) and its __module__, for repr and pickling.
const MODULE: &str = "hindsight_native";

fn slots(py: Python<'_>, names: &[&str]) -> PyObject {
    PyTuple::new_bound(py, names).into_py(py)
}

fn sum<'py>(py: Python<'py>, items: Vec<Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyAny>> {
    builtin(py, "sum")?.call1((PyList::new_bound(py, items),))
}

// --------------------------------------------------------------------------- ChannelDelta
const CD: &[&str] = &["key", "idx", "vals", "exists", "op"];

#[pyclass(name = "ChannelDelta")]
pub struct ChannelDelta {
    #[pyo3(get, set)] key: PyObject,
    #[pyo3(get, set)] idx: PyObject,
    #[pyo3(get, set)] vals: PyObject,
    #[pyo3(get, set)] exists: PyObject,
    #[pyo3(get, set)] op: PyObject,
}

#[pymethods]
impl ChannelDelta {
    #[new]
    #[pyo3(signature = (*args, **kwargs))]
    fn new(py: Python<'_>, args: &Bound<'_, PyTuple>, kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut a = bind(py, "ChannelDelta", CD, 3, args, kwargs)?;
        Ok(ChannelDelta {
            key: take(&mut a, 0, || unreachable!())?, idx: take(&mut a, 1, || unreachable!())?,
            vals: take(&mut a, 2, || unreachable!())?,
            exists: take(&mut a, 3, || Ok(EXISTS.get_or_init(py, || (true, true).into_py(py)).clone_ref(py)))?,
            op: take(&mut a, 4, || Ok(0.into_py(py)))?,
        })
    }

    fn nbytes(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let (i, v) = { let s = slf.borrow(); (s.idx.clone_ref(py), s.vals.clone_ref(py)) };
        let n = i.bind(py).getattr(intern!(py, "nbytes"))?.add(v.bind(py).getattr(intern!(py, "nbytes"))?)?;
        Ok(builtin(py, "int")?.call1((n,))?.add(64)?.unbind())
    }

    #[classattr]
    fn __slots__(py: Python<'_>) -> PyObject { slots(py, CD) }
    fn __richcmp__(slf: &Bound<'_, Self>, o: &Bound<'_, PyAny>, op: CompareOp) -> PyResult<PyObject> { dc_cmp(slf.as_any(), o, op, "ChannelDelta") }
    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<PyObject> { dc_repr(slf.as_any(), "ChannelDelta") }
    fn __reduce__(slf: &Bound<'_, Self>) -> PyResult<PyObject> { reduce(slf.as_any(), CD) }
    fn __traverse__(&self, v: PyVisit<'_>) -> Result<(), PyTraverseError> { gc_visit!(v, self; key, idx, vals, exists, op) }
    fn __clear__(&mut self) { gc_clear!(self; key, idx, vals, exists, op) }
}

// --------------------------------------------------------------------------- Snapshot
const SN: &[&str] = &["counts", "arrays"];

#[pyclass(name = "Snapshot")]
pub struct Snapshot {
    #[pyo3(get, set)] counts: PyObject,
    #[pyo3(get, set)] arrays: PyObject,
}

#[pymethods]
impl Snapshot {
    #[new]
    #[pyo3(signature = (*args, **kwargs))]
    fn new(py: Python<'_>, args: &Bound<'_, PyTuple>, kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut a = bind(py, "Snapshot", SN, 2, args, kwargs)?;
        Ok(Snapshot { counts: take(&mut a, 0, || unreachable!())?, arrays: take(&mut a, 1, || unreachable!())? })
    }

    fn nbytes(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let arrays = slf.borrow().arrays.clone_ref(py);
        let mut items = Vec::new();
        for a in arrays.bind(py).call_method0(intern!(py, "values"))?.iter()? {
            items.push(a?.getattr(intern!(py, "nbytes"))?);
        }
        Ok(builtin(py, "int")?.call1((sum(py, items)?,))?.add(128)?.unbind())
    }

    fn copy(slf: &Bound<'_, Self>) -> PyResult<Snapshot> {
        let py = slf.py();
        let (c, arrays) = { let s = slf.borrow(); (s.counts.clone_ref(py), s.arrays.clone_ref(py)) };
        let counts = builtin(py, "dict")?.call1((c,))?;
        let out = PyDict::new_bound(py);
        for kv in arrays.bind(py).call_method0(intern!(py, "items"))?.iter()? {
            let (k, v) = unpack2(py, &kv?)?;
            out.set_item(k, v.call_method0(intern!(py, "copy"))?)?;
        }
        Ok(Snapshot { counts: counts.unbind(), arrays: out.into_any().unbind() })
    }

    #[classattr]
    fn __slots__(py: Python<'_>) -> PyObject { slots(py, SN) }
    fn __richcmp__(slf: &Bound<'_, Self>, o: &Bound<'_, PyAny>, op: CompareOp) -> PyResult<PyObject> { dc_cmp(slf.as_any(), o, op, "Snapshot") }
    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<PyObject> { dc_repr(slf.as_any(), "Snapshot") }
    fn __reduce__(slf: &Bound<'_, Self>) -> PyResult<PyObject> { reduce(slf.as_any(), SN) }
    fn __traverse__(&self, v: PyVisit<'_>) -> Result<(), PyTraverseError> { gc_visit!(v, self; counts, arrays) }
    fn __clear__(&mut self) { gc_clear!(self; counts, arrays) }
}

// --------------------------------------------------------------------------- MISSING, DataChange
#[pyclass(name = "_Missing", frozen)]
pub struct Missing;

#[pymethods]
impl Missing {
    fn __repr__(&self) -> &'static str { "MISSING" }
    fn __reduce__(&self) -> &'static str { "MISSING" }
    #[classattr]
    fn __slots__(py: Python<'_>) -> PyObject { slots(py, &[]) }
}

fn value_size(py: Python<'_>, v: &Bound<'_, PyAny>, depth: u32) -> PyResult<i64> {
    if depth > 64 {
        // deep values: the reference's own recursion (and its RecursionError)
        return fb(py)?.getattr(intern!(py, "_value_size"))?.call1((v,))?.extract();
    }
    if v.is_instance_of::<PyList>() || v.is_instance_of::<PyTuple>() {
        let mut n = 16;
        for x in v.iter()? {
            n += value_size(py, &x?, depth + 1)?;
        }
        return Ok(n);
    }
    if v.is_instance_of::<PyString>() {
        return Ok(16 + v.len()? as i64);
    }
    Ok(16)
}

const DC: &[&str] = &["adapter", "target", "changes"];

#[pyclass(name = "DataChange")]
pub struct DataChange {
    #[pyo3(get, set)] adapter: PyObject,
    #[pyo3(get, set)] target: PyObject,
    #[pyo3(get, set)] changes: PyObject,
}

#[pymethods]
impl DataChange {
    #[new]
    #[pyo3(signature = (*args, **kwargs))]
    fn new(py: Python<'_>, args: &Bound<'_, PyTuple>, kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut a = bind(py, "DataChange", DC, 3, args, kwargs)?;
        Ok(DataChange { adapter: take(&mut a, 0, || unreachable!())?, target: take(&mut a, 1, || unreachable!())?,
                        changes: take(&mut a, 2, || unreachable!())? })
    }

    fn nbytes(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let ch = slf.borrow().changes.clone_ref(py);
        let ch = ch.bind(py);
        let n = 96 + 48 * ch.len()? as i64;
        let mut items = Vec::new();
        for v in ch.call_method0(intern!(py, "values"))?.iter()? {
            let (o, w) = unpack2(py, &v?)?;
            items.push((value_size(py, &o, 0)? + value_size(py, &w, 0)?).into_py(py).into_bound(py));
        }
        Ok(n.into_py(py).bind(py).add(sum(py, items)?)?.unbind())
    }

    #[classattr]
    fn __slots__(py: Python<'_>) -> PyObject { slots(py, DC) }
    fn __richcmp__(slf: &Bound<'_, Self>, o: &Bound<'_, PyAny>, op: CompareOp) -> PyResult<PyObject> { dc_cmp(slf.as_any(), o, op, "DataChange") }
    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<PyObject> { dc_repr(slf.as_any(), "DataChange") }
    fn __reduce__(slf: &Bound<'_, Self>) -> PyResult<PyObject> { reduce(slf.as_any(), DC) }
    fn __traverse__(&self, v: PyVisit<'_>) -> Result<(), PyTraverseError> { gc_visit!(v, self; adapter, target, changes) }
    fn __clear__(&mut self) { gc_clear!(self; adapter, target, changes) }
}

// --------------------------------------------------------------------------- Step
const ST: &[&str] = &["seq", "label", "mode", "target", "time", "kind", "deltas", "before", "after", "extra", "records"];

#[pyclass(name = "Step")]
pub struct Step {
    #[pyo3(get, set)] seq: PyObject,
    #[pyo3(get, set)] label: PyObject,
    #[pyo3(get, set)] mode: PyObject,
    #[pyo3(get, set)] target: PyObject,
    #[pyo3(get, set)] time: PyObject,
    #[pyo3(get, set)] kind: PyObject,
    #[pyo3(get, set)] deltas: PyObject,
    #[pyo3(get, set)] before: PyObject,
    #[pyo3(get, set)] after: PyObject,
    #[pyo3(get, set)] extra: PyObject,
    #[pyo3(get, set)] records: PyObject,
}

#[pymethods]
impl Step {
    #[new]
    #[pyo3(signature = (*args, **kwargs))]
    fn new(py: Python<'_>, args: &Bound<'_, PyTuple>, kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut a = bind(py, "Step", ST, 4, args, kwargs)?;
        let none = || Ok(py.None());
        Ok(Step {
            seq: take(&mut a, 0, || unreachable!())?, label: take(&mut a, 1, || unreachable!())?,
            mode: take(&mut a, 2, || unreachable!())?, target: take(&mut a, 3, || unreachable!())?,
            time: take(&mut a, 4, || Ok(0.0f64.into_py(py)))?,
            kind: take(&mut a, 5, || Ok(intern!(py, "DELTA").clone().into_any().unbind()))?,
            deltas: take(&mut a, 6, || Ok(PyList::empty_bound(py).into_any().unbind()))?,
            before: take(&mut a, 7, none)?, after: take(&mut a, 8, none)?,
            extra: take(&mut a, 9, || Ok(PyDict::new_bound(py).into_any().unbind()))?,
            records: take(&mut a, 10, || Ok(PyList::empty_bound(py).into_any().unbind()))?,
        })
    }

    fn nbytes(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let (d, r, b, a) = {
            let s = slf.borrow();
            (s.deltas.clone_ref(py), s.records.clone_ref(py), s.before.clone_ref(py), s.after.clone_ref(py))
        };
        let each = |l: &PyObject| -> PyResult<Vec<Bound<'_, PyAny>>> {
            l.bind(py).iter()?.map(|x| x?.call_method0(intern!(py, "nbytes"))).collect()
        };
        let d = sum(py, each(&d)?)?;
        let mut n = 256i64.into_py(py).into_bound(py).add(d)?;
        n = n.add(sum(py, each(&r)?)?)?;
        for s in [b, a] {
            if !s.is_none(py) {
                n = iadd(py, &n, &s.bind(py).call_method0(intern!(py, "nbytes"))?)?;
            }
        }
        Ok(n.unbind())
    }

    fn meta(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let s = slf.borrow();
        let m = PyDict::new_bound(py);
        for (k, v) in [("seq", &s.seq), ("label", &s.label), ("mode", &s.mode), ("target", &s.target),
                       ("time", &s.time), ("kind", &s.kind), ("extra", &s.extra)] {
            m.set_item(k, v)?;
        }
        Ok(m.into_any().unbind())
    }

    #[classattr]
    fn __slots__(py: Python<'_>) -> PyObject { slots(py, ST) }
    fn __richcmp__(slf: &Bound<'_, Self>, o: &Bound<'_, PyAny>, op: CompareOp) -> PyResult<PyObject> { dc_cmp(slf.as_any(), o, op, "Step") }
    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<PyObject> { dc_repr(slf.as_any(), "Step") }
    fn __reduce__(slf: &Bound<'_, Self>) -> PyResult<PyObject> { reduce(slf.as_any(), ST) }
    fn __traverse__(&self, v: PyVisit<'_>) -> Result<(), PyTraverseError> {
        gc_visit!(v, self; seq, label, mode, target, time, kind, deltas, before, after, extra, records)
    }
    fn __clear__(&mut self) { gc_clear!(self; seq, label, mode, target, time, kind, deltas, before, after, extra, records) }
}

// --------------------------------------------------------------------------- Slice
const SL: &[&str] = &["part", "off", "n"];

/// A saved step's bytes inside its step part (#125), read out of `part.bytes` by offset.
#[pyclass(name = "Slice")]
pub struct Slice {
    #[pyo3(get, set)] part: PyObject,
    #[pyo3(get, set)] off: PyObject,
    #[pyo3(get, set)] n: PyObject,
}

fn part_bytes<'py>(py: Python<'py>, s: &Bound<'py, Slice>) -> PyResult<(Bound<'py, PyAny>, Bound<'py, PyAny>, Bound<'py, PyAny>)> {
    let (p, o, n) = { let b = s.borrow(); (b.part.clone_ref(py), b.off.clone_ref(py), b.n.clone_ref(py)) };
    Ok((p.into_bound(py), o.into_bound(py), n.into_bound(py)))
}

fn span<'py>(py: Python<'py>, a: &Bound<'py, PyAny>, z: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    builtin(py, "slice")?.call1((a, z))
}

#[pymethods]
impl Slice {
    #[new]
    #[pyo3(signature = (*args, **kwargs))]
    fn new(py: Python<'_>, args: &Bound<'_, PyTuple>, kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut a = bind(py, "Slice", SL, 3, args, kwargs)?;
        let int = builtin(py, "int")?;
        let part = take(&mut a, 0, || unreachable!())?;
        let off = int.call1((a[1].take().unwrap(),))?.unbind();
        let n = int.call1((a[2].take().unwrap(),))?.unbind();
        Ok(Slice { part, off, n })
    }

    fn __len__(slf: &Bound<'_, Self>) -> PyResult<usize> {
        let py = slf.py();
        let n: i64 = slf.borrow().n.extract(py)?;
        usize::try_from(n).map_err(|_| PyValueError::new_err("__len__() should return >= 0"))
    }

    /// A byte, or a copy of a run of bytes (step 1), as the step's own bytes would give.
    fn __getitem__(slf: &Bound<'_, Self>, i: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        let py = slf.py();
        let (p, off, n) = part_bytes(py, slf)?;
        if i.get_type().is(&pyo3::types::PySlice::type_object_bound(py)) {
            let r = i.call_method1(intern!(py, "indices"), (&n,))?;
            let [a, z, _] = unpack3(py, &r)?;
            let hi = if z.gt(&a)? { z } else { a.clone() };
            let s = span(py, &off.add(&a)?, &off.add(hi)?)?;
            return Ok(p.getattr(intern!(py, "bytes"))?.get_item(s)?.unbind());
        }
        let mut i = i.clone();
        if i.lt(0)? {
            i = iadd(py, &i, &n)?;
        }
        let inside = 0i64.into_py(py).into_bound(py).le(&i)? && i.lt(&n)?;
        if !inside {
            return Err(PyIndexError::new_err(i.unbind()));
        }
        Ok(p.getattr(intern!(py, "bytes"))?.get_item(off.add(i)?)?.unbind())
    }

    /// The step's bytes, copied out (None while Blender holds the step part).
    fn copy(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let (p, off, n) = part_bytes(py, slf)?;
        let b = p.getattr(intern!(py, "bytes"))?;
        if b.is_none() {
            return Ok(py.None());
        }
        Ok(b.get_item(span(py, &off, &off.add(&n)?)?)?.unbind())
    }

    fn view(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let (p, off, n) = part_bytes(py, slf)?;
        let m = builtin(py, "memoryview")?.call1((p.getattr(intern!(py, "bytes"))?,))?;
        Ok(m.get_item(span(py, &off, &off.add(&n)?)?)?.unbind())
    }

    #[classattr]
    fn __slots__(py: Python<'_>) -> PyObject { slots(py, SL) }
    fn __reduce__(slf: &Bound<'_, Self>) -> PyResult<PyObject> { reduce(slf.as_any(), SL) }
    fn __traverse__(&self, v: PyVisit<'_>) -> Result<(), PyTraverseError> { gc_visit!(v, self; part, off, n) }
    fn __clear__(&mut self) { gc_clear!(self; part, off, n) }
}

// --------------------------------------------------------------------------- Entry
fn no_blob(py: Python<'_>) -> PyObject {
    NO_BLOB.get_or_init(py, || (py.None(), py.None()).into_py(py)).clone_ref(py)
}

fn crc_of<'py>(py: Python<'py>, b: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    eng(py)?.getattr(intern!(py, "crc32"))?.call1((b,))?.bitand(0xFFFF_FFFFu32)
}

fn is_slice(py: Python<'_>, b: &Bound<'_, PyAny>) -> bool {
    b.get_type().is(&Slice::type_object_bound(py))
}

fn step_to_bytes(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    if let Some(f) = TO_BYTES.get(py) {
        return Ok(f.bind(py).clone());
    }
    let name = setup(py)?.2.bind(py).clone();
    let f = py.import_bound(name.downcast_into::<PyString>()?)?.getattr(intern!(py, "step_to_bytes"))?;
    let _ = TO_BYTES.set(py, f.clone().unbind());
    Ok(f)
}

const EN: &[&str] = &["step", "blob", "meta", "crc"];

/// One slot in a History: the step raw (fast) or packed (small); `.meta` always there without unpacking. The
/// packed bytes and their CRC-32 are one tuple, `_bc`, replaced whole (R046).
#[pyclass(name = "Entry", subclass, weakref)]
pub struct Entry {
    #[pyo3(get, set)] meta: PyObject,
    #[pyo3(get, set)] step: PyObject,
    #[pyo3(get, set, name = "_bc")] bc: PyObject,
    #[pyo3(get, set, name = "_nbytes")] nb: PyObject,
}

#[pymethods]
impl Entry {
    #[new]
    #[pyo3(signature = (*args, **kwargs))]
    fn new(py: Python<'_>, args: &Bound<'_, PyTuple>, kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<Self> {
        let a = bind(py, "Entry", EN, 0, args, kwargs)?;
        let get = |i: usize| a[i].clone().unwrap_or_else(|| py.None().into_bound(py));
        let (step, blob, meta, crc) = (get(0), get(1), get(2), get(3));
        let bc = if blob.is_none() {
            no_blob(py)
        } else {
            let c = if !crc.is_none() { crc } else { crc_of(py, &blob)? };
            (blob, c).into_py(py)
        };
        let meta = if !meta.is_none() {
            meta
        } else if step.is_truthy()? {
            step.call_method0(intern!(py, "meta"))?
        } else {
            PyDict::new_bound(py).into_any()
        };
        Ok(Entry { meta: meta.unbind(), step: step.unbind(), bc, nb: py.None() })
    }

    #[getter]
    fn get_blob(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let b = slf.borrow().bc.bind(py).get_item(0)?;
        Ok(if is_slice(py, &b) { b.call_method0(intern!(py, "copy"))?.unbind() } else { b.unbind() })
    }

    #[setter]
    fn set_blob(slf: &Bound<'_, Self>, b: Option<&Bound<'_, PyAny>>) -> PyResult<()> {
        let py = slf.py();
        let bc = match b {
            Some(b) if !b.is_none() => (b, crc_of(py, b)?).into_py(py),
            _ => no_blob(py),
        };
        slf.borrow_mut().bc = bc;
        Ok(())
    }

    /// (packed bytes, their CRC-32), read together; (None, None) while the step is raw.
    fn blob_crc(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let bc = slf.borrow().bc.clone_ref(py).into_bound(py);
        let b = bc.get_item(0)?;
        if is_slice(py, &b) {
            return Ok((b.call_method0(intern!(py, "copy"))?, bc.get_item(1)?).into_py(py));
        }
        Ok(bc.unbind())
    }

    #[getter]
    fn seq(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let m = slf.borrow().meta.clone_ref(py);
        Ok(m.bind(py).get_item(intern!(py, "seq"))?.unbind())
    }

    #[getter]
    fn target(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let m = slf.borrow().meta.clone_ref(py);
        Ok(m.bind(py).get_item(intern!(py, "target"))?.unbind())
    }

    #[getter]
    fn kind(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let m = slf.borrow().meta.clone_ref(py);
        Ok(m.bind(py).call_method1(intern!(py, "get"), ("kind", "DELTA"))?.unbind())
    }

    fn is_packed(&self, py: Python<'_>) -> bool {
        self.step.is_none(py)
    }

    fn nbytes(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let (st, bc) = { let s = slf.borrow(); (s.step.clone_ref(py), s.bc.clone_ref(py)) };
        if !st.is_none(py) {
            return Ok(st.bind(py).call_method0(intern!(py, "nbytes"))?.unbind());
        }
        let b = bc.bind(py).get_item(0)?;     // a Slice gives its length without a copy
        Ok(if b.is_none() { 128.into_py(py) } else { (b.len()? + 128).into_py(py) })
    }

    /// Return the Step, unpacking (without caching) if packed.
    fn load(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let st = slf.borrow().step.clone_ref(py);
        if !st.is_none(py) {
            return Ok(st);
        }
        step_from_bytes(py, &slf.getattr(intern!(py, "blob"))?)
    }

    #[pyo3(signature = (codec = None))]
    fn pack(slf: &Bound<'_, Self>, codec: Option<PyObject>) -> PyResult<()> {
        let py = slf.py();
        // shortcut: an explicit codec=None is taken as "auto" (the Python passed None on); nothing passes None
        let codec = codec.unwrap_or_else(|| "auto".into_py(py));
        let st = slf.borrow().step.clone_ref(py);
        if st.is_none(py) {
            return Ok(());
        }
        let b = step_to_bytes(py)?.call1((st, codec))?;
        slf.setattr(intern!(py, "blob"), b)?;
        slf.borrow_mut().step = py.None();
        Ok(())
    }

    /// Packed form. effort 0 (fast) for writes on the main thread; 1 (best) otherwise.
    #[pyo3(signature = (codec = None, effort = None))]
    fn to_bytes(slf: &Bound<'_, Self>, codec: Option<PyObject>, effort: Option<PyObject>) -> PyResult<PyObject> {
        let py = slf.py();
        let codec = codec.unwrap_or_else(|| "auto".into_py(py));     // shortcut: as in pack
        let effort = effort.unwrap_or_else(|| 1i64.into_py(py));
        let b = slf.getattr(intern!(py, "blob"))?;
        if !b.is_none() {
            return Ok(b.unbind());
        }
        let st = slf.borrow().step.clone_ref(py);
        Ok(step_to_bytes(py)?.call1((st, codec, effort))?.unbind())
    }

    #[classattr]
    fn __slots__(py: Python<'_>) -> PyObject { slots(py, &["meta", "step", "_bc", "_nbytes"]) }

    fn __reduce__(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let s = slf.borrow();
        let state = (s.meta.clone_ref(py), s.step.clone_ref(py), s.bc.clone_ref(py), s.nb.clone_ref(py));
        Ok((slf.get_type(), PyTuple::empty_bound(py), state).into_py(py))
    }

    fn __traverse__(&self, v: PyVisit<'_>) -> Result<(), PyTraverseError> { gc_visit!(v, self; meta, step, bc, nb) }
    fn __clear__(&mut self) { gc_clear!(self; meta, step, bc, nb) }

    fn __setstate__(slf: &Bound<'_, Self>, state: (PyObject, PyObject, PyObject, PyObject)) {
        let mut s = slf.borrow_mut();
        (s.meta, s.step, s.bc, s.nb) = state;
    }
}

// --------------------------------------------------------------------------- key helpers
#[pyfunction]
fn make_key(py: Python<'_>, name: &Bound<'_, PyAny>, domain: &Bound<'_, PyAny>, data_type: &Bound<'_, PyAny>) -> PyResult<String> {
    let f = |x: &Bound<'_, PyAny>| -> PyResult<String> {
        if let Ok(s) = x.downcast_exact::<PyString>() {
            return Ok(s.to_str()?.to_owned());
        }
        builtin(py, "format")?.call1((x,))?.extract()
    };
    Ok(format!("{}|{}|{}", f(name)?, f(domain)?, f(data_type)?))
}

fn split<'py>(py: Python<'py>, key: &Bound<'py, PyAny>) -> PyResult<[Bound<'py, PyAny>; 3]> {
    unpack3(py, &key.call_method1(intern!(py, "rsplit"), ("|", 2))?)
}

#[pyfunction]
fn split_key<'py>(py: Python<'py>, key: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyTuple>> {
    Ok(PyTuple::new_bound(py, split(py, key)?))
}

fn channel<'py>(py: Python<'py>, key: &Bound<'py, PyAny>, i: usize) -> PyResult<Bound<'py, PyAny>> {
    let [_, _, t] = split(py, key)?;
    setup(py)?.0.bind(py).get_item(t)?.get_item(i)
}

/// Native numpy dtype for a channel key.
#[pyfunction]
fn key_dtype<'py>(py: Python<'py>, key: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    channel(py, key, 0)
}

#[pyfunction]
fn key_width<'py>(py: Python<'py>, key: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    channel(py, key, 1)
}

#[pyfunction]
fn bits_dtype<'py>(py: Python<'py>, native_dtype: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let size = np(py)?.getattr(intern!(py, "dtype"))?.call1((native_dtype,))?.getattr(intern!(py, "itemsize"))?;
    setup(py)?.1.bind(py).get_item(size)
}

// --------------------------------------------------------------------------- step_from_bytes, same_step
fn snapshot<'py>(py: Python<'py>, parsed: &Bound<'py, PyAny>) -> PyResult<PyObject> {
    let (counts, arrays) = unpack2(py, parsed)?;
    let counts = builtin(py, "dict")?.call1((counts,))?;
    let fromb = np(py)?.getattr(intern!(py, "frombuffer"))?;
    let out = PyDict::new_bound(py);
    for x in arrays.iter()? {
        let [key, dstr, buf] = unpack3(py, &x?)?;
        let kw = PyDict::new_bound(py);
        kw.set_item(intern!(py, "dtype"), dstr)?;
        out.set_item(key, fromb.call((buf,), Some(&kw))?)?;
    }
    Ok(Py::new(py, Snapshot { counts: counts.unbind(), arrays: out.into_any().unbind() })?.into_any())
}

#[pyfunction]
fn snapshot_from_bytes(py: Python<'_>, b: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    snapshot(py, &eng(py)?.getattr(intern!(py, "snapshot_decode"))?.call1((b,))?)
}

#[pyfunction]
pub fn step_from_bytes(py: Python<'_>, b: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    let e = eng(py)?;
    let t = e.getattr(intern!(py, "step_decode"))?.call1((b,))?;
    let g = |i: usize| t.get_item(i);
    let extra = e.getattr(intern!(py, "step_extra"))?.call1((g(7)?,))?;
    let numpy = np(py)?;
    let (fromb, zeros, u32t) = (numpy.getattr(intern!(py, "frombuffer"))?, numpy.getattr(intern!(py, "zeros"))?,
                                numpy.getattr(intern!(py, "uint32"))?);
    let bits = setup(py)?.1.bind(py);
    let deltas = PyList::empty_bound(py);
    for d in g(9)?.iter()? {
        let d = d?;
        let f = |i: usize| d.get_item(i);
        let (k, w) = (f(4)?, f(5)?);
        let bdt = bits.get_item(f(6)?)?;
        let dt = |x: &Bound<'_, PyAny>| -> PyResult<Bound<'_, PyDict>> {
            let kw = PyDict::new_bound(py);
            kw.set_item(intern!(py, "dtype"), x)?;
            Ok(kw)
        };
        let (idx, vals) = if k.eq(0)? {
            (zeros.call((0,), Some(&dt(&u32t)?))?, zeros.call(((0, &w),), Some(&dt(&bdt)?))?)
        } else {
            (fromb.call((f(7)?,), Some(&dt(&u32t)?))?,
             fromb.call((f(8)?,), Some(&dt(&bdt)?))?.call_method1(intern!(py, "reshape"), (&k, &w))?)
        };
        let op = if f(1)?.is_truthy()? { 1 } else { 0 };
        deltas.append(Py::new(py, ChannelDelta {
            key: f(0)?.unbind(), idx: idx.unbind(), vals: vals.unbind(), exists: (f(2)?, f(3)?).into_py(py),
            op: op.into_py(py),
        })?)?;
    }
    let rest = || -> PyResult<(PyObject, PyObject, PyObject)> {
        let side = |i: usize| -> PyResult<PyObject> {
            let s = g(i)?;
            if s.is_none() { Ok(py.None()) } else { snapshot(py, &s) }
        };
        let (before, after) = (side(10)?, side(11)?);
        let snaps = g(12)?;
        if !snaps.is_none() {
            let m = PyDict::new_bound(py);
            for x in snaps.iter()? {
                let (uid, s) = unpack2(py, &x?)?;
                m.set_item(uid, snapshot(py, &s)?)?;
            }
            extra.set_item(intern!(py, "_snapshots"), m)?;
        }
        let rb = g(13)?;
        let records = if rb.is_none() {
            PyList::empty_bound(py).into_any()
        } else {
            e.getattr(intern!(py, "records_from_bytes"))?.call1((rb,))?
        };
        Ok((before, after, records.unbind()))
    };
    let (before, after, records) = match rest() {
        Ok(x) => x,
        Err(err) => {
            let damaged = err.is_instance_of::<PyValueError>(py) || err.is_instance_of::<PyKeyError>(py)
                || err.is_instance_of::<PyTypeError>(py) || err.is_instance_of::<PyUnicodeDecodeError>(py)
                || err.is_instance_of::<PyRecursionError>(py);
            if !damaged {
                return Err(err);
            }
            let msg = format!("step blob: damaged ({})", err.value_bound(py).str()?);
            let new = PyErr::from_value_bound(e.getattr(intern!(py, "CorruptStep"))?.call1((msg,))?);
            new.set_cause(py, Some(err));
            return Err(new);
        }
    };
    Ok(Py::new(py, Step {
        seq: g(1)?.unbind(), label: g(4)?.unbind(), mode: g(5)?.unbind(), target: g(6)?.unbind(), time: g(2)?.unbind(),
        kind: g(3)?.unbind(), deltas: deltas.into_any().unbind(), before, after, extra: extra.unbind(), records,
    })?.into_any())
}

fn same_arrays(py: Python<'_>, a: &Bound<'_, PyAny>, b: &Bound<'_, PyAny>) -> PyResult<bool> {
    if a.call_method0(intern!(py, "keys"))?.ne(b.call_method0(intern!(py, "keys"))?)? {
        return Ok(false);
    }
    let dtype = intern!(py, "dtype");
    let tob = intern!(py, "tobytes");
    for k in a.iter()? {
        let k = k?;
        if !(a.get_item(&k)?.getattr(dtype)?.eq(b.get_item(&k)?.getattr(dtype)?)?
             && a.get_item(&k)?.call_method0(tob)?.eq(b.get_item(&k)?.call_method0(tob)?)?) {
            return Ok(false);
        }
    }
    Ok(true)
}

fn same_snap(py: Python<'_>, a: &Bound<'_, PyAny>, b: &Bound<'_, PyAny>) -> PyResult<bool> {
    if a.is_none() || b.is_none() {
        return Ok(a.is(b));
    }
    let c = a.getattr(intern!(py, "counts"))?.rich_compare(b.getattr(intern!(py, "counts"))?, CompareOp::Eq)?;
    if !c.is_truthy()? {
        return Ok(false);
    }
    same_arrays(py, &a.getattr(intern!(py, "arrays"))?, &b.getattr(intern!(py, "arrays"))?)
}

/// True if two steps hold exactly the same data (deltas compared after decoding to the same form: XOR deltas only
/// equal XOR deltas, ADD only ADD). Used by HINDSIGHT_VERIFY and tests.
#[pyfunction]
fn same_step<'py>(py: Python<'py>, a: &Bound<'py, PyAny>, b: &Bound<'py, PyAny>) -> PyResult<bool> {
    let head = |s: &Bound<'_, PyAny>| -> PyResult<Py<PyTuple>> {
        let v = ["seq", "label", "mode", "target", "kind"].iter().map(|n| s.getattr(*n)).collect::<PyResult<Vec<_>>>()?;
        Ok(PyTuple::new_bound(py, v).unbind())
    };
    if head(a)?.bind(py).ne(head(b)?)? {
        return Ok(false);
    }
    let float = builtin(py, "float")?;
    let time = |s: &Bound<'_, PyAny>| -> PyResult<PyObject> {
        let t = s.getattr(intern!(py, "time"))?;
        let t = if t.is_truthy()? { t } else { 0.0f64.into_py(py).into_bound(py) };
        Ok(float.call1((t,))?.unbind())
    };
    let deltas = intern!(py, "deltas");
    if time(a)?.bind(py).ne(time(b)?)? || a.getattr(deltas)?.len()? != b.getattr(deltas)?.len()? {
        return Ok(false);
    }
    let numpy = np(py)?;
    let (asarray, int64, array_equal) = (numpy.getattr(intern!(py, "asarray"))?, numpy.getattr(intern!(py, "int64"))?,
                                         numpy.getattr(intern!(py, "array_equal"))?);
    let tuple = builtin(py, "tuple")?;
    let (ia, ib) = (a.getattr(deltas)?.iter()?, b.getattr(deltas)?.iter()?);
    for (x, y) in ia.zip(ib) {
        let (x, y) = (x?, y?);
        let id = |d: &Bound<'_, PyAny>| -> PyResult<Py<PyTuple>> {
            let (k, o) = (d.getattr(intern!(py, "key"))?, d.getattr(intern!(py, "op"))?);
            let ex = tuple.call1((d.getattr(intern!(py, "exists"))?,))?;
            Ok(PyTuple::new_bound(py, [k, o, ex]).unbind())
        };
        if id(&x)?.bind(py).ne(id(&y)?)? {
            return Ok(false);
        }
        let i64 = |d: &Bound<'_, PyAny>| asarray.call1((d.getattr(intern!(py, "idx"))?, &int64));
        if !array_equal.call1((i64(&x)?, i64(&y)?))?.is_truthy()? {
            return Ok(false);
        }
        let (xv, yv) = (asarray.call1((x.getattr(intern!(py, "vals"))?,))?, asarray.call1((y.getattr(intern!(py, "vals"))?,))?);
        let flat = |v: &Bound<'py, PyAny>| -> PyResult<Bound<'py, PyAny>> { v.call_method1(intern!(py, "reshape"), (-1,))?.call_method0(intern!(py, "tobytes")) };
        if xv.getattr(intern!(py, "dtype"))?.ne(yv.getattr(intern!(py, "dtype"))?)? || flat(&xv)?.ne(flat(&yv)?)? {
            return Ok(false);
        }
    }
    if !(same_snap(py, &a.getattr(intern!(py, "before"))?, &b.getattr(intern!(py, "before"))?)?
         && same_snap(py, &a.getattr(intern!(py, "after"))?, &b.getattr(intern!(py, "after"))?)?) {
        return Ok(false);
    }
    let e = eng(py)?;
    let rtb = e.getattr(intern!(py, "records_to_bytes"))?;
    if rtb.call1((a.getattr(intern!(py, "records"))?,))?.ne(rtb.call1((b.getattr(intern!(py, "records"))?,))?)? {
        return Ok(false);
    }
    let dict = builtin(py, "dict")?;
    let ex = |s: &Bound<'_, PyAny>| -> PyResult<PyObject> {
        let x = s.getattr(intern!(py, "extra"))?;
        let x = if x.is_truthy()? { x } else { PyDict::new_bound(py).into_any() };
        Ok(dict.call1((x,))?.unbind())
    };
    let (ea, eb) = (ex(a)?.into_bound(py), ex(b)?.into_bound(py));
    let pop = |d: &Bound<'py, PyAny>| -> PyResult<Bound<'py, PyAny>> { d.call_method1(intern!(py, "pop"), ("_snapshots", py.None())) };
    let (sa, sb) = (pop(&ea)?, pop(&eb)?);
    let (dumps, loads) = (e.getattr(intern!(py, "json_dumps"))?, e.getattr(intern!(py, "json_loads"))?);
    let rt = |d: &Bound<'_, PyAny>| -> PyResult<PyObject> { Ok(loads.call1((dumps.call1((d,))?,))?.unbind()) };
    if rt(&ea)?.bind(py).ne(rt(&eb)?)? {
        return Ok(false);
    }
    if sa.is_none() != sb.is_none() {
        return Ok(false);
    }
    if !sa.is_none() {
        let list = builtin(py, "list")?;
        if list.call1((&sa,))?.ne(list.call1((&sb,))?)? {
            return Ok(false);
        }
        for k in sa.iter()? {
            let k = k?;
            if !same_snap(py, &sa.get_item(&k)?, &sb.get_item(&k)?)? {
                return Ok(false);
            }
        }
    }
    Ok(true)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = m.py();
    m.add_class::<ChannelDelta>()?;
    m.add_class::<Snapshot>()?;
    m.add_class::<DataChange>()?;
    m.add_class::<Step>()?;
    m.add_class::<Slice>()?;
    m.add_class::<Entry>()?;
    m.add_class::<Missing>()?;
    m.add("MISSING", Py::new(py, Missing)?)?;
    for name in ["ChannelDelta", "Snapshot", "DataChange", "Step", "Slice", "Entry", "_Missing"] {
        let c = m.getattr(name)?;
        c.setattr("__name__", name)?;           // tp_name too: "Step", as the dataclass's (see MODULE)
        c.setattr("__module__", MODULE)?;
    }
    m.add_function(wrap_pyfunction!(types_setup, m)?)?;
    m.add_function(wrap_pyfunction!(make_key, m)?)?;
    m.add_function(wrap_pyfunction!(split_key, m)?)?;
    m.add_function(wrap_pyfunction!(key_dtype, m)?)?;
    m.add_function(wrap_pyfunction!(key_width, m)?)?;
    m.add_function(wrap_pyfunction!(bits_dtype, m)?)?;
    m.add_function(wrap_pyfunction!(snapshot_from_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(step_from_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(same_step, m)?)?;
    Ok(())
}
