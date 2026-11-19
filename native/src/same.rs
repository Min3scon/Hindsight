//! Strict equality of the data recorder's values (R070): the "nothing changed" check that every
//! recorded object goes through at every operation (hindsight/bl/datarec.py `_same_values`).
//!
//! `same_values(a, b)` is True only if the two are equal in the strictest sense, the one marshal
//! (version 2) bytes would show: the same exact type at every level (bool is not int, int is not
//! float, a subclass is another type), floats bit for bit (-0.0 is not 0.0, a NaN equals a NaN of
//! the same bits), str and int by value, None, lists and tuples element by element, dicts with the
//! same keys in the same order and strictly equal values. Anything else (another type, nesting
//! deeper than 64 levels) gives False, which is always safe: the caller then compares the slow,
//! exact way. True must never be given for values the recorder's diff would see as different.
//!
//! The walk reads the objects through the stable ABI's functions with the GIL held. Nothing it calls
//! runs Python code (exact int and str compare in C), so no container can change during the walk.

use pyo3::ffi;
use pyo3::prelude::*;
use std::ptr::addr_of_mut;

use crate::guard;

const MAX_DEPTH: u32 = 64;

/// SAFETY: a and b are live objects (borrowed), the GIL is held.
unsafe fn same(a: *mut ffi::PyObject, b: *mut ffi::PyObject, depth: u32) -> bool {
    if a == b {
        return true; // the same object (None, True, False, a shared value)
    }
    let ta = ffi::Py_TYPE(a);
    if ta != ffi::Py_TYPE(b) || depth > MAX_DEPTH {
        return false;
    }
    if ta == addr_of_mut!(ffi::PyFloat_Type) {
        return ffi::PyFloat_AsDouble(a).to_bits() == ffi::PyFloat_AsDouble(b).to_bits();
    }
    if ta == addr_of_mut!(ffi::PyUnicode_Type) || ta == addr_of_mut!(ffi::PyLong_Type) {
        return eq(a, b);
    }
    if ta == addr_of_mut!(ffi::PyList_Type) {
        let n = ffi::PyList_Size(a);
        if n != ffi::PyList_Size(b) {
            return false;
        }
        for i in 0..n {
            if !same(ffi::PyList_GetItem(a, i), ffi::PyList_GetItem(b, i), depth + 1) {
                return false;
            }
        }
        return true;
    }
    if ta == addr_of_mut!(ffi::PyDict_Type) {
        let n = ffi::PyDict_Size(a);
        if n != ffi::PyDict_Size(b) {
            return false;
        }
        let (mut pa, mut pb): (ffi::Py_ssize_t, ffi::Py_ssize_t) = (0, 0);
        let (mut ka, mut va, mut kb, mut vb) =
            (std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut());
        loop {
            let more_a = ffi::PyDict_Next(a, &mut pa, &mut ka, &mut va) != 0;
            let more_b = ffi::PyDict_Next(b, &mut pb, &mut kb, &mut vb) != 0;
            if more_a != more_b {
                return false;
            }
            if !more_a {
                return true;
            }
            if !same(ka, kb, depth + 1) || !same(va, vb, depth + 1) {
                return false;
            }
        }
    }
    if ta == addr_of_mut!(ffi::PyTuple_Type) {
        let n = ffi::PyTuple_Size(a);
        if n != ffi::PyTuple_Size(b) {
            return false;
        }
        for i in 0..n {
            if !same(ffi::PyTuple_GetItem(a, i), ffi::PyTuple_GetItem(b, i), depth + 1) {
                return false;
            }
        }
        return true;
    }
    // bool (a singleton: two different objects differ), None (likewise) and every other type
    false
}

/// Exact int or str (both of the same exact type): equal by value. An error (never expected) is
/// cleared and counts as not equal.
unsafe fn eq(a: *mut ffi::PyObject, b: *mut ffi::PyObject) -> bool {
    match ffi::PyObject_RichCompareBool(a, b, ffi::Py_EQ) {
        1 => true,
        0 => false,
        _ => {
            ffi::PyErr_Clear();
            false
        }
    }
}

/// same_values(a, b) -> True if a and b are strictly equal (see the module text), else False.
#[pyfunction]
pub fn same_values(a: &Bound<'_, PyAny>, b: &Bound<'_, PyAny>) -> PyResult<bool> {
    // SAFETY: both are live objects borrowed for the call, and the GIL is held (a Bound).
    guard(|| Ok(unsafe { same(a.as_ptr(), b.as_ptr(), 0) }))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(same_values, m)?)?;
    Ok(())
}
