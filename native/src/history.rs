//! The history and its mesh state (#132, 24.4): `MeshState` (one datablock's baseline: arrays, the packed topology,
//! deltas, snapshots, recorded values) and `History` (entries, cursor, push and the chain sharing, truncate, drop the
//! oldest, `goto` and the crossing of runs of steps). The Python they replace is tests/reference/history.py. The
//! arrays, values and entries stay Python objects, held by the engine; every method does what the Python did, in the
//! same order, through the same Python operations, so results and exceptions are the reference's. `goto` hands runs
//! of packed DELTA steps of one object to `apply_steps` in one call (the GIL released there), as history.py's
//! `_cross_runs` did; any other step is crossed one at a time.
use pyo3::exceptions::PyTypeError;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyBytes, PyDict, PyList, PyLong, PyModule, PySet, PySlice, PyString, PyTuple};
use pyo3::{PyTraverseError, PyVisit};

use crate::types::{bind, unpack2};

static MODS: GILOnceCell<(PyObject, PyObject, PyObject)> = GILOnceCell::new(); // the delta, step, topopack facades

const TOPO_MARK: &str = "__TOPO__";
const DATA_MARK: &str = "@";
const SNAPSHOTS_KEY: &str = "_snapshots";

/// `hindsight/core/history.py` hands over the facades whose names the Python called (`delta.apply`,
/// `delta.view_bits`, `step.is_unknown`, `topopack.TOPO_KEYS`, `unpack_array`, `packed_bytes`).
#[pyfunction]
fn history_setup(py: Python<'_>, delta: PyObject, step: PyObject, topopack: PyObject) -> PyResult<()> {
    let _ = MODS.set(py, (delta, step, topopack));
    Ok(())
}

fn mods(py: Python<'_>) -> PyResult<&(PyObject, PyObject, PyObject)> {
    MODS.get(py).ok_or_else(|| PyTypeError::new_err("hindsight_native: history_setup was not called"))
}

fn delta_fn<'py>(py: Python<'py>, name: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyAny>> {
    mods(py)?.0.bind(py).getattr(name)
}

fn topo<'py>(py: Python<'py>, name: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyAny>> {
    mods(py)?.2.bind(py).getattr(name)
}

// The modules, imported once: an import per call was most of the 2.5 us a one-step jump lost against the Python.
static ENG: GILOnceCell<Py<PyModule>> = GILOnceCell::new();
static NP: GILOnceCell<Py<PyModule>> = GILOnceCell::new();
static BUILTINS: GILOnceCell<Py<PyModule>> = GILOnceCell::new();
static OPERATOR: GILOnceCell<Py<PyModule>> = GILOnceCell::new();

fn module<'py>(py: Python<'py>, cell: &'static GILOnceCell<Py<PyModule>>, name: &str) -> PyResult<&'py Bound<'py, PyModule>> {
    Ok(cell.get_or_try_init(py, || py.import_bound(name).map(Bound::unbind))?.bind(py))
}

fn eng(py: Python<'_>) -> PyResult<&Bound<'_, PyModule>> {
    module(py, &ENG, "hindsight_native")
}

fn np(py: Python<'_>) -> PyResult<&Bound<'_, PyModule>> {
    module(py, &NP, "numpy")
}

fn builtin<'py>(py: Python<'py>, name: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyAny>> {
    module(py, &BUILTINS, "builtins")?.getattr(name)
}

fn dict_of<'py>(py: Python<'py>, v: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    py.get_type_bound::<PyDict>().call1((v,))
}

fn set_of<'py>(py: Python<'py>, v: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    py.get_type_bound::<PySet>().call1((v,))
}

fn int_of<'py>(py: Python<'py>, v: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    py.get_type_bound::<PyLong>().call1((v,))
}

fn slice<'py>(py: Python<'py>, a: PyObject, b: PyObject) -> PyResult<Bound<'py, PyAny>> {
    py.get_type_bound::<PySlice>().call1((a, b))
}

/// `changed.setdefault(key, set())`
fn keys_of<'py>(changed: &Bound<'py, PyDict>, key: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    changed.call_method1(intern!(changed.py(), "setdefault"), (key, PySet::empty_bound(changed.py())?))
}

// A field's object, the borrow let go at once (a Python call may set a field meanwhile).
macro_rules! g { ($s:expr, $f:ident) => {{ let o = $s.borrow().$f.clone_ref($s.py()); o.into_bound($s.py()) }} }

// --------------------------------------------------------------------------- MeshState
const MS: &[&str] = &["counts", "_arrays", "removed", "values", "snaps", "_topo"];

/// Current recorded state of ONE datablock (the baseline shown at the cursor): mesh channels for meshes, plus the
/// values data adapters record ((adapter, path) -> value).
#[pyclass(name = "MeshState")]
pub struct MeshState {
    #[pyo3(get, set)] counts: PyObject,
    #[pyo3(get, set, name = "_arrays")] arr: PyObject,     // key -> flat native array (owned, not copied)
    #[pyo3(get, set)] removed: PyObject,                    // keys whose attribute must be deleted from the mesh
    #[pyo3(get, set)] values: PyObject,                     // (adapter, path) -> value; absent = MISSING
    #[pyo3(get, set)] snaps: PyObject,                      // uid -> Snapshot carried by crossed data steps
    #[pyo3(get, set, name = "_topo")] topo: PyObject,       // R069: {topology key: packed} while packed
}

impl MeshState {
    fn unpack(slf: &Bound<'_, Self>) -> PyResult<()> {
        let py = slf.py();
        let t = g!(slf, topo);
        slf.borrow_mut().topo = py.None();
        let arr = g!(slf, arr);
        let unpack = topo(py, intern!(py, "unpack_array"))?;
        for item in t.call_method0(intern!(py, "items"))?.iter()? {
            let (k, p) = unpack2(py, &item?)?;
            arr.set_item(k, unpack.call1((p,))?)?;
        }
        Ok(())
    }

    fn ensure<'py>(slf: &Bound<'py, Self>, key: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
        let py = slf.py();
        let arrays = g!(slf, arr);
        let mut arr = arrays.call_method1(intern!(py, "get"), (key,))?;
        if arr.is_none() {
            let e = eng(py)?;
            let domain = e.getattr(intern!(py, "split_key"))?.call1((key,))?.get_item(1)?;
            let n = int_of(py, &g!(slf, counts).get_item(domain)?)?;
            let n = n.mul(e.getattr(intern!(py, "key_width"))?.call1((key,))?)?;
            let kw = PyDict::new_bound(py);
            kw.set_item(intern!(py, "dtype"), e.getattr(intern!(py, "key_dtype"))?.call1((key,))?)?;
            arr = np(py)?.getattr(intern!(py, "zeros"))?.call((n,), Some(&kw))?;
            arrays.set_item(key, &arr)?;
        } else if !arr.getattr(intern!(py, "flags"))?.getattr(intern!(py, "c_contiguous"))?.is_truthy()? {
            arr = np(py)?.getattr(intern!(py, "ascontiguousarray"))?.call1((&arr,))?;
            arrays.set_item(key, &arr)?;
        }
        Ok(arr)
    }
}

#[pymethods]
impl MeshState {
    #[new]
    #[pyo3(signature = (*args, **kwargs))]
    fn new(py: Python<'_>, args: &Bound<'_, PyTuple>, kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<Self> {
        let a = bind(py, "MeshState", &["counts", "arrays", "values"], 2, args, kwargs)?;
        let counts = dict_of(py, a[0].as_ref().unwrap())?;
        let arr = a[1].clone().unwrap();
        let values = match &a[2] {
            Some(v) if v.is_truthy()? => v.clone(),
            _ => PyDict::new_bound(py).into_any(),
        };
        let values = dict_of(py, &values)?;
        Ok(MeshState { counts: counts.unbind(), arr: arr.unbind(), removed: PySet::empty_bound(py)?.into_any().unbind(),
                       values: values.unbind(), snaps: PyDict::new_bound(py).into_any().unbind(), topo: py.None() })
    }

    /// Every array of the baseline, the topology unpacked if it was packed (it stays unpacked until the next idle
    /// packing).
    #[getter(arrays)]
    fn get_arrays(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        if !g!(slf, topo).is_none() {
            Self::unpack(slf)?;
        }
        Ok(g!(slf, arr).unbind())
    }

    #[setter(arrays)]
    fn set_arrays(slf: &Bound<'_, Self>, value: PyObject) {
        let py = slf.py();
        let mut m = slf.borrow_mut();
        m.arr = value;
        m.topo = py.None();
    }

    /// The arrays without unpacking: every channel; the topology keys only while not packed.
    #[getter]
    fn light(slf: &Bound<'_, Self>) -> PyObject {
        g!(slf, arr).unbind()
    }

    fn is_packed(slf: &Bound<'_, Self>) -> bool {
        !g!(slf, topo).is_none()
    }

    /// {topology key: xxh3-128 checksum} while packed, else None.
    fn topo_hashes(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let t = g!(slf, topo);
        if t.is_none() {
            return Ok(py.None());
        }
        let out = PyDict::new_bound(py);
        for item in t.call_method0(intern!(py, "items"))?.iter()? {
            let (k, p) = unpack2(py, &item?)?;
            out.set_item(k, p.get_item(3)?)?;
        }
        Ok(out.into_any().unbind())
    }

    fn all_keys(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let a = set_of(py, &g!(slf, arr))?;
        let t = g!(slf, topo);
        let t = if t.is_truthy()? { t } else { PyTuple::empty_bound(py).into_any() };
        Ok(a.bitor(set_of(py, &t)?)?.unbind())
    }

    /// The topology arrays to pack now ({key: array}), or None (packed already, or none).
    fn pack_job(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        if !g!(slf, topo).is_none() {
            return Ok(py.None());
        }
        let keys = topo(py, intern!(py, "TOPO_KEYS"))?;
        let job = PyDict::new_bound(py);
        for k in keys.iter()? {
            let k = k?;
            let arr = g!(slf, arr);
            if arr.contains(&k)? {
                job.set_item(&k, arr.get_item(&k)?)?;
            }
        }
        Ok(if job.is_truthy()? { job.into_any().unbind() } else { py.None() })
    }

    /// Put the packed form in place of the arrays packed, unless any of them was replaced meanwhile (then the job
    /// is dropped). Main thread only.
    fn finish_pack(slf: &Bound<'_, Self>, job: &Bound<'_, PyAny>, packed: &Bound<'_, PyAny>) -> PyResult<bool> {
        let py = slf.py();
        if !g!(slf, topo).is_none() || set_of(py, job)?.ne(set_of(py, packed)?)? {
            return Ok(false);
        }
        for item in job.call_method0(intern!(py, "items"))?.iter()? {
            let (k, a) = unpack2(py, &item?)?;
            if !g!(slf, arr).call_method1(intern!(py, "get"), (k,))?.is(&a) {
                return Ok(false);
            }
        }
        for k in job.iter()? {
            g!(slf, arr).del_item(k?)?;
        }
        slf.borrow_mut().topo = packed.clone().unbind();
        Ok(true)
    }

    fn _unpack_topo(slf: &Bound<'_, Self>) -> PyResult<()> {
        Self::unpack(slf)
    }

    fn nbytes(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let packed_bytes = topo(py, intern!(py, "packed_bytes"))?;
        let mut total = 0i64.into_py(py).into_bound(py);
        for a in g!(slf, arr).call_method0(intern!(py, "values"))?.iter()? {
            total = total.add(a?.getattr(intern!(py, "nbytes"))?)?;
        }
        let total = int_of(py, &total)?;
        let t = g!(slf, topo);
        let extra = if t.is_truthy()? { packed_bytes.call1((t,))? } else { 0i64.into_py(py).into_bound(py) };
        Ok(total.add(extra)?.unbind())
    }

    /// Set the recorded values to the side of the change we move to. Returns the changed keys.
    fn apply_record(slf: &Bound<'_, Self>, rec: &Bound<'_, PyAny>, forward: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        let py = slf.py();
        let out = PySet::empty_bound(py)?;
        let a = rec.getattr(intern!(py, "adapter"))?;
        let missing = eng(py)?.getattr(intern!(py, "MISSING"))?;
        for item in rec.getattr(intern!(py, "changes"))?.call_method0(intern!(py, "items"))?.iter()? {
            let (path, pair) = unpack2(py, &item?)?;
            let (old, new) = unpack2(py, &pair)?;
            let v = if forward.is_truthy()? { new } else { old };
            let k = PyTuple::new_bound(py, [&a, &path]);
            let values = g!(slf, values);
            if v.is(&missing) {
                values.call_method1(intern!(py, "pop"), (k, py.None()))?;
            } else {
                values.set_item(k, v)?;
            }
            out.add(PyTuple::new_bound(py, [intern!(py, DATA_MARK).clone().into_any(), a.clone(), path]))?;
        }
        Ok(out.into_any().unbind())
    }

    fn _ensure(slf: &Bound<'_, Self>, key: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        Ok(Self::ensure(slf, key)?.unbind())
    }

    fn apply_delta(slf: &Bound<'_, Self>, d: &Bound<'_, PyAny>, forward: &Bound<'_, PyAny>) -> PyResult<()> {
        let py = slf.py();
        let key = d.getattr(intern!(py, "key"))?;
        let arr = Self::ensure(slf, &key)?;
        if d.getattr(intern!(py, "idx"))?.len()? != 0 {
            // idx is unique, so the fancy-index read/modify/write is exact
            delta_fn(py, intern!(py, "apply"))?.call1((arr, d, forward))?;
        }
        let ex = d.getattr(intern!(py, "exists"))?;
        let exists = if forward.is_truthy()? { ex.get_item(1)? } else { ex.get_item(0)? };
        if exists.is_truthy()? {
            g!(slf, removed).call_method1(intern!(py, "discard"), (&key,))?;
        } else {
            g!(slf, arr).call_method1(intern!(py, "pop"), (&key, py.None()))?;
            g!(slf, removed).call_method1(intern!(py, "add"), (&key,))?;
        }
        Ok(())
    }

    fn set_snapshot(slf: &Bound<'_, Self>, snap: &Bound<'_, PyAny>) -> PyResult<()> {
        let py = slf.py();
        let topo_keys = topo(py, intern!(py, "TOPO_KEYS"))?;
        let old_keys = Self::all_keys(slf)?.into_bound(py);   // no unpacking only to be replaced
        let counts = dict_of(py, &snap.getattr(intern!(py, "counts"))?)?;
        slf.borrow_mut().counts = counts.unbind();
        let arrays = PyDict::new_bound(py);
        let n = np(py)?;
        for item in snap.getattr(intern!(py, "arrays"))?.call_method0(intern!(py, "items"))?.iter()? {
            let (k, v) = unpack2(py, &item?)?;
            if topo_keys.contains(&k)? {
                // R080: nothing writes topology in place (a change of it is a new snapshot), so the baseline holds
                // the step's own arrays, read-only so that a write would fail instead of changing the step. The
                // channels are copied: strokes write the baseline's in place.
                let a = n.getattr(intern!(py, "asarray"))?.call1((v,))?;
                let flags = a.getattr(intern!(py, "flags"))?;
                if flags.getattr(intern!(py, "writeable"))?.is_truthy()? {
                    flags.setattr(intern!(py, "writeable"), false)?;
                }
                arrays.set_item(k, a)?;
            } else {
                let kw = PyDict::new_bound(py);
                kw.set_item(intern!(py, "copy"), true)?;
                arrays.set_item(k, n.getattr(intern!(py, "array"))?.call((v,), Some(&kw))?)?;
            }
        }
        Self::set_arrays(slf, arrays.into_any().unbind());
        let new_keys = set_of(py, &g!(slf, arr))?;
        let op = module(py, &OPERATOR, "operator")?;
        let r = op.getattr(intern!(py, "ior"))?.call1((g!(slf, removed), old_keys.sub(&new_keys)?))?;
        slf.borrow_mut().removed = r.unbind();
        let r = op.getattr(intern!(py, "isub"))?.call1((g!(slf, removed), new_keys))?;
        slf.borrow_mut().removed = r.unbind();
        Ok(())
    }

    fn to_snapshot(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let counts = dict_of(py, &g!(slf, counts))?;
        let arrays = PyDict::new_bound(py);
        for item in Self::get_arrays(slf)?.into_bound(py).call_method0(intern!(py, "items"))?.iter()? {
            let (k, v) = unpack2(py, &item?)?;
            arrays.set_item(k, v.call_method0(intern!(py, "copy"))?)?;
        }
        Ok(eng(py)?.getattr(intern!(py, "Snapshot"))?.call1((counts, arrays))?.unbind())
    }

    #[classattr]
    fn __slots__(py: Python<'_>) -> PyObject {
        PyTuple::new_bound(py, MS).into_py(py)
    }

    /// A copy (copy.deepcopy, pickle): rebuilt by the constructor, then every slot set as the reference's are.
    fn __reduce__(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let m = slf.borrow();
        let args = (m.counts.clone_ref(py), m.arr.clone_ref(py), m.values.clone_ref(py));
        let state = PyTuple::new_bound(py, [&m.counts, &m.arr, &m.removed, &m.values, &m.snaps, &m.topo]);
        Ok((slf.get_type(), args, state).into_py(py))
    }

    fn __setstate__(slf: &Bound<'_, Self>, state: &Bound<'_, PyTuple>) -> PyResult<()> {
        let mut m = slf.borrow_mut();
        m.counts = state.get_item(0)?.unbind();
        m.arr = state.get_item(1)?.unbind();
        m.removed = state.get_item(2)?.unbind();
        m.values = state.get_item(3)?.unbind();
        m.snaps = state.get_item(4)?.unbind();
        m.topo = state.get_item(5)?.unbind();
        Ok(())
    }

    fn __traverse__(&self, v: PyVisit<'_>) -> Result<(), PyTraverseError> {
        for f in [&self.counts, &self.arr, &self.removed, &self.values, &self.snaps, &self.topo] {
            v.call(f)?;
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        Python::with_gil(|py| {
            for f in [&mut self.counts, &mut self.arr, &mut self.removed, &mut self.values, &mut self.snaps,
                      &mut self.topo] {
                *f = py.None();
            }
        })
    }
}

// --------------------------------------------------------------------------- History
const HS: &[&str] = &["uid", "members", "entries", "cursor", "enabled", "morph_target", "unknown"];

/// Undo timeline of one object (or of several after a join).
#[pyclass(name = "History", dict, weakref, subclass)]
pub struct History {
    #[pyo3(get, set)] uid: PyObject,
    #[pyo3(get, set)] members: PyObject,
    #[pyo3(get, set)] entries: PyObject,
    #[pyo3(get, set)] cursor: PyObject,
    #[pyo3(get, set)] enabled: PyObject,
    #[pyo3(get, set)] morph_target: PyObject,
    #[pyo3(get, set)] unknown: PyObject,       // steps read at open in an encoding this build doesn't know (#66)
}

/// A TOPO step's `before` is the mesh the TOPO step before it left (its `after`): where the two are bit for bit the
/// same, the new step holds the older step's arrays, not a second copy of them. Only raw neighbours share.
fn share_chain<'py>(py: Python<'py>, prev: &Bound<'py, PyAny>, step: &Bound<'py, PyAny>) -> PyResult<()> {
    let ps = prev.getattr(intern!(py, "step"))?;
    let (kind, after, before, target) = (intern!(py, "kind"), intern!(py, "after"), intern!(py, "before"),
                                          intern!(py, "target"));
    if ps.is_none() || ps.getattr(kind)?.ne(intern!(py, "TOPO"))? || step.getattr(kind)?.ne(intern!(py, "TOPO"))?
        || ps.getattr(after)?.is_none() || step.getattr(before)?.is_none()
        || ps.getattr(target)?.ne(step.getattr(target)?)? {
        return Ok(());
    }
    let arrays = intern!(py, "arrays");
    let items = step.getattr(before)?.getattr(arrays)?.call_method0(intern!(py, "items"))?;
    let view_bits = delta_fn(py, intern!(py, "view_bits"))?;
    let equal = np(py)?.getattr(intern!(py, "array_equal"))?;
    for item in py.get_type_bound::<PyList>().call1((items,))?.iter()? {
        let (key, a) = unpack2(py, &item?)?;
        let b = ps.getattr(after)?.getattr(arrays)?.call_method1(intern!(py, "get"), (&key,))?;
        let (dtype, shape) = (intern!(py, "dtype"), intern!(py, "shape"));
        if b.is_none() || b.is(&a) || b.getattr(dtype)?.ne(a.getattr(dtype)?)? || b.getattr(shape)?.ne(a.getattr(shape)?)? {
            continue;
        }
        if equal.call1((view_bits.call1((&a,))?, view_bits.call1((&b,))?))?.is_truthy()? {
            step.getattr(before)?.getattr(arrays)?.set_item(key, b)?;
        }
    }
    Ok(())
}

fn cross_runs<'py>(py: Python<'py>, run: &Bound<'py, PyAny>, entries: &[Bound<'py, PyAny>], forward: bool,
                   states: &Bound<'py, PyAny>, join_hook: &Bound<'py, PyAny>, changed: &Bound<'py, PyDict>) -> PyResult<()> {
    let (target_s, step_s, meta_s) = (intern!(py, "target"), intern!(py, "step"), intern!(py, "meta"));
    let (n, mut i) = (entries.len(), 0usize);
    while i < n {
        let mut e = entries[i].clone();
        let target = e.getattr(target_s)?;
        if !states.contains(&target)? {
            i += 1;                     // object not being moved: skipped without unpacking
            continue;
        }
        let mut blobs = Vec::new();
        let mut j = i;
        while j < n {
            let x = &entries[j];
            if !x.getattr(step_s)?.is_none() || x.getattr(target_s)?.ne(&target)?
                || x.getattr(meta_s)?.call_method1(intern!(py, "get"), (intern!(py, "kind"), intern!(py, "DELTA")))?.ne(intern!(py, "DELTA"))? {
                break;
            }
            let b = x.getattr(intern!(py, "blob"))?;
            if !b.is_exact_instance_of::<PyBytes>() {
                break;
            }
            blobs.push(b);
            j += 1;
        }
        if !blobs.is_empty() {
            let st = states.get_item(&target)?;
            let r = run.call1((PyList::new_bound(py, &blobs), st.getattr(intern!(py, "light"))?, forward))?;
            let (done, keys) = unpack2(py, &r)?;     // deltas: channels only (R069)
            if keys.is_truthy()? {
                keys_of(changed, &target)?.call_method1(intern!(py, "update"), (&keys,))?;
                st.getattr(intern!(py, "removed"))?.call_method1(intern!(py, "difference_update"), (&keys,))?;
            }
            let done: usize = done.extract()?;
            i += done;
            if done < blobs.len() {
                e = entries[i].clone();  // the step it stopped at, crossed below
            } else {
                continue;
            }
        }
        let step = e.call_method0(intern!(py, "load"))?;
        cross(py, &e, &step, forward, states, join_hook, changed)?;
        i += 1;
    }
    Ok(())
}

fn cross<'py>(py: Python<'py>, entry: &Bound<'py, PyAny>, step: &Bound<'py, PyAny>, forward: bool,
              states: &Bound<'py, PyAny>, join_hook: &Bound<'py, PyAny>, changed: &Bound<'py, PyDict>) -> PyResult<()> {
    let target = entry.getattr(intern!(py, "target"))?;
    let st = states.get_item(&target)?;
    if step.getattr(intern!(py, "records"))?.is_truthy()? {
        // data records: applied before the mesh part going forward, after it going back, so a step is undone in the
        // reverse order of how it was done
        if forward {
            records(py, step, true, states, changed)?;
        } else {
            mesh(py, entry, step, false, &st, states, join_hook, changed)?;
            return records(py, step, false, states, changed);
        }
    }
    mesh(py, entry, step, forward, &st, states, join_hook, changed)
}

fn records<'py>(py: Python<'py>, step: &Bound<'py, PyAny>, forward: bool, states: &Bound<'py, PyAny>,
                changed: &Bound<'py, PyDict>) -> PyResult<()> {
    let snaps = if step.getattr(intern!(py, "kind"))?.eq(intern!(py, "DATA"))? {
        let extra = step.getattr(intern!(py, "extra"))?;
        let extra = if extra.is_truthy()? { extra } else { PyDict::new_bound(py).into_any() };
        extra.call_method1(intern!(py, "get"), (intern!(py, SNAPSHOTS_KEY),))?
    } else {
        py.None().into_bound(py)
    };
    let recs = step.getattr(intern!(py, "records"))?;
    let recs = if forward { recs } else { builtin(py, intern!(py, "reversed"))?.call1((recs,))? };
    for rec in recs.iter()? {
        let rec = rec?;
        let rs = states.call_method1(intern!(py, "get"), (rec.getattr(intern!(py, "target"))?,))?;
        if !rs.is_none() {
            if snaps.is_truthy()? {
                rs.getattr(intern!(py, "snaps"))?.call_method1(intern!(py, "update"), (&snaps,))?;
            }
            let keys = keys_of(changed, &rec.getattr(intern!(py, "target"))?)?;
            let upd = keys.getattr(intern!(py, "update"))?;
            upd.call1((rs.call_method1(intern!(py, "apply_record"), (&rec, forward))?,))?;
        }
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn mesh<'py>(py: Python<'py>, entry: &Bound<'py, PyAny>, step: &Bound<'py, PyAny>, forward: bool, st: &Bound<'py, PyAny>,
             states: &Bound<'py, PyAny>, join_hook: &Bound<'py, PyAny>, changed: &Bound<'py, PyDict>) -> PyResult<()> {
    let target = entry.getattr(intern!(py, "target"))?;
    let kind = step.getattr(intern!(py, "kind"))?;
    if kind.eq(intern!(py, "DATA"))? {
        return Ok(());
    }
    if kind.eq(intern!(py, "DELTA"))? {
        let deltas = step.getattr(intern!(py, "deltas"))?;
        if deltas.is_truthy()? {
            let keys = keys_of(changed, &target)?;
            for d in deltas.iter()? {
                let d = d?;
                st.call_method1(intern!(py, "apply_delta"), (&d, forward))?;
                keys.call_method1(intern!(py, "add"), (d.getattr(intern!(py, "key"))?,))?;
            }
        }
        return Ok(());
    }
    if kind.eq(intern!(py, "JOIN"))? && !join_hook.is_none() {
        let res = join_hook.call1((step, forward, states))?;
        keys_of(changed, &target)?.call_method1(intern!(py, "add"), (intern!(py, TOPO_MARK),))?;
        if res.is_instance_of::<PyDict>() {
            for item in res.call_method0(intern!(py, "items"))?.iter()? {
                let (uid, keys) = unpack2(py, &item?)?;
                keys_of(changed, &uid)?.call_method1(intern!(py, "update"), (keys,))?;
            }
        } else if !res.is_none() {
            for uid in res.iter()? {
                keys_of(changed, &uid?)?.call_method1(intern!(py, "add"), (intern!(py, TOPO_MARK),))?;
            }
        }
        return Ok(());
    }
    // TOPO, or JOIN without hook
    let snap = step.getattr(if forward { intern!(py, "after") } else { intern!(py, "before") })?;
    if !snap.is_none() {
        st.call_method1(intern!(py, "set_snapshot"), (snap,))?;
    }
    keys_of(changed, &target)?.call_method1(intern!(py, "add"), (intern!(py, TOPO_MARK),))?;
    Ok(())
}

/// min(a, b) and max(a, b) as Python's builtins pick them (the first unless the second is less, or greater).
fn py_min<'py>(a: Bound<'py, PyAny>, b: Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    Ok(if b.lt(&a)? { b } else { a })
}

fn py_max<'py>(a: Bound<'py, PyAny>, b: Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    Ok(if b.gt(&a)? { b } else { a })
}

#[pymethods]
impl History {
    #[new]
    #[pyo3(signature = (*args, **kwargs))]
    fn new(py: Python<'_>, args: &Bound<'_, PyTuple>, kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<Self> {
        let a = bind(py, "History", &["uid", "members"], 1, args, kwargs)?;
        let uid = a[0].clone().unwrap();
        let members = match &a[1] {
            Some(m) if m.is_truthy()? => py.get_type_bound::<PyList>().call1((m,))?,
            _ => PyList::new_bound(py, [&uid]).into_any(),
        };
        Ok(History { uid: uid.unbind(), members: members.unbind(), entries: PyList::empty_bound(py).into_any().unbind(),
                     cursor: 0i64.into_py(py), enabled: true.into_py(py), morph_target: py.None(),
                     unknown: 0i64.into_py(py) })
    }

    fn __len__(slf: &Bound<'_, Self>) -> PyResult<usize> {
        g!(slf, entries).len()
    }

    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<String> {
        let (uid, cur) = (g!(slf, uid).repr()?, g!(slf, cursor).str()?);
        Ok(format!("<History {} {}/{}>", uid, cur, g!(slf, entries).len()?))
    }

    fn future_count(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let n = g!(slf, entries).len()?.into_py(py).into_bound(py);
        Ok(n.sub(g!(slf, cursor))?.unbind())
    }

    /// (first, last) position the cursor can reach. Every step undoes and redoes itself, so the cursor can't cross a
    /// step in an encoding this build doesn't know (dev/FORMAT5_ENCODINGS.md).
    fn reach(slf: &Bound<'_, Self>) -> PyResult<(PyObject, PyObject)> {
        let py = slf.py();
        let mut lo = 0usize;
        let hi = g!(slf, entries).len()?;
        if g!(slf, unknown).is_truthy()? {
            let is_unknown = mods(py)?.1.bind(py).getattr(intern!(py, "is_unknown"))?;
            for (i, e) in g!(slf, entries).iter()?.enumerate() {
                let e = e?;
                if e.getattr(intern!(py, "step"))?.is_none()
                    && is_unknown.call1((e.getattr(intern!(py, "_bc"))?.get_item(0)?,))?.is_truthy()? {
                    if i.into_py(py).into_bound(py).ge(g!(slf, cursor))? {
                        return Ok((lo.into_py(py), i.into_py(py)));
                    }
                    lo = i + 1;
                }
            }
        }
        Ok((lo.into_py(py), hi.into_py(py)))
    }

    /// Is a step this build can't read after the cursor? Recording would cut it, so the history is held: kept as it
    /// is and not recorded into.
    fn held(slf: &Bound<'_, Self>) -> PyResult<bool> {
        let py = slf.py();
        let hi = slf.call_method0(intern!(py, "reach"))?.get_item(1)?;
        hi.lt(g!(slf, entries).len()?)
    }

    fn truncate_future(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let dropped = g!(slf, entries).get_item(slice(py, g!(slf, cursor).unbind(), py.None())?)?;
        g!(slf, entries).del_item(slice(py, g!(slf, cursor).unbind(), py.None())?)?;
        let mt = g!(slf, morph_target);
        if !mt.is_none() && mt.gt(g!(slf, cursor))? {
            slf.borrow_mut().morph_target = py.None();
        }
        Ok(dropped.unbind())
    }

    fn push(slf: &Bound<'_, Self>, step: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        let py = slf.py();
        let dropped = slf.call_method0(intern!(py, "truncate_future"))?;
        let e = eng(py)?;
        if step.is_instance(&e.getattr(intern!(py, "Step"))?)? && g!(slf, entries).is_truthy()? {
            share_chain(py, &g!(slf, entries).get_item(-1)?, step)?;
        }
        let entry = e.getattr(intern!(py, "Entry"))?;
        let item = if step.is_instance(&entry)? { step.clone() } else { entry.call1((step,))? };
        g!(slf, entries).call_method1(intern!(py, "append"), (item,))?;
        slf.borrow_mut().cursor = g!(slf, entries).len()?.into_py(py);
        Ok(dropped.unbind())
    }

    fn drop_oldest(slf: &Bound<'_, Self>, n: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        let py = slf.py();
        let n = int_of(py, n)?;
        let len = g!(slf, entries).len()?.into_py(py).into_bound(py);
        let n = py_max(0i64.into_py(py).into_bound(py), py_min(n, len)?)?;
        if n.eq(0)? {
            return Ok(0i64.into_py(py));
        }
        g!(slf, entries).del_item(slice(py, py.None(), n.clone().unbind())?)?;
        let c = py_max(0i64.into_py(py).into_bound(py), g!(slf, cursor).sub(&n)?)?;
        slf.borrow_mut().cursor = c.unbind();
        let mt = g!(slf, morph_target);
        if !mt.is_none() {
            let mt = mt.sub(&n)?;
            slf.borrow_mut().morph_target = if mt.ge(0)? { mt.unbind() } else { py.None() };
        }
        Ok(n.unbind())
    }

    fn delete_past(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        Ok(slf.call_method1(intern!(slf.py(), "drop_oldest"), (g!(slf, cursor),))?.unbind())
    }

    fn reset(slf: &Bound<'_, Self>) -> PyResult<()> {
        let py = slf.py();
        let mut m = slf.borrow_mut();
        m.entries = PyList::empty_bound(py).into_any().unbind();
        m.cursor = 0i64.into_py(py);
        m.morph_target = py.None();
        Ok(())
    }

    fn positions_of(slf: &Bound<'_, Self>, target: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        let py = slf.py();
        let out = PyList::empty_bound(py);
        for (i, e) in g!(slf, entries).iter()?.enumerate() {
            if e?.getattr(intern!(py, "target"))?.eq(target)? {
                out.append(i)?;
            }
        }
        Ok(out.into_any().unbind())
    }

    #[pyo3(signature = (pos, states, join_hook=None))]
    fn goto(slf: &Bound<'_, Self>, pos: &Bound<'_, PyAny>, states: &Bound<'_, PyAny>,
            join_hook: Option<&Bound<'_, PyAny>>) -> PyResult<PyObject> {
        let py = slf.py();
        let (lo, hi) = unpack2(py, &slf.call_method0(intern!(py, "reach"))?)?;
        let pos = py_max(lo, py_min(int_of(py, pos)?, hi)?)?;
        let changed = PyDict::new_bound(py);
        let cur = g!(slf, cursor);
        if pos.ne(&cur)? {
            let forward = pos.gt(&cur)?;
            let range = builtin(py, intern!(py, "range"))?;
            let order = if forward {
                range.call1((&cur, &pos))?
            } else {
                range.call1((cur.sub(1)?, pos.sub(1)?, -1))?
            };
            let all = g!(slf, entries);
            let entries = order.iter()?.map(|i| all.get_item(i?)).collect::<PyResult<Vec<_>>>()?;
            let run = eng(py)?.getattr(intern!(py, "apply_steps"))?;
            let none = py.None().into_bound(py);
            cross_runs(py, &run, &entries, forward, states, join_hook.unwrap_or(&none), &changed)?;
        }
        slf.borrow_mut().cursor = pos.unbind();
        Ok(changed.into_any().unbind())
    }

    /// A copy (copy.deepcopy, pickle): rebuilt by the constructor, then each attribute set from the state, as the
    /// reference's __dict__ is.
    fn __reduce__(slf: &Bound<'_, Self>) -> PyResult<PyObject> {
        let py = slf.py();
        let state = PyDict::new_bound(py);
        if let Ok(d) = slf.getattr(intern!(py, "__dict__")) {
            state.update(d.downcast::<PyDict>()?.as_mapping())?;
        }
        for name in HS {
            state.set_item(name, slf.getattr(*name)?)?;
        }
        Ok((slf.get_type(), (g!(slf, uid),), state).into_py(py))
    }

    fn __setstate__(slf: &Bound<'_, Self>, state: &Bound<'_, PyDict>) -> PyResult<()> {
        for (k, v) in state.iter() {
            slf.setattr(k.downcast_into::<PyString>()?, v)?;
        }
        Ok(())
    }

    fn __traverse__(&self, v: PyVisit<'_>) -> Result<(), PyTraverseError> {
        for f in [&self.uid, &self.members, &self.entries, &self.cursor, &self.enabled, &self.morph_target,
                  &self.unknown] {
            v.call(f)?;
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        Python::with_gil(|py| {
            for f in [&mut self.uid, &mut self.members, &mut self.entries, &mut self.cursor, &mut self.enabled,
                      &mut self.morph_target, &mut self.unknown] {
                *f = py.None();
            }
        })
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MeshState>()?;
    m.add_class::<History>()?;
    for name in ["MeshState", "History"] {
        m.getattr(name)?.setattr("__module__", "hindsight_native")?;
    }
    m.add_function(wrap_pyfunction!(history_setup, m)?)?;
    Ok(())
}
