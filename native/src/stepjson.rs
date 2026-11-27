//! The JSON of a step's extra and data records, and the header dict of read_header (#146, 24.3a): what
//! hindsight/core/step.py ran in Python (now tests/reference/step_json.py), byte for byte and exception for
//! exception.
//!
//! A fast path reads exact types only (dict with str keys, list, tuple, str, int, float, bool, None;
//! for the records the DataChange class and MISSING that `json_setup` gave) through the stable ABI with the GIL
//! held; writing runs no Python code, so nothing it walks can change under it. Floats are written by float's
//! own `repr`, NaN and the infinities as json writes them, strings with json's `ensure_ascii` escapes. Anything else
//! (another type or a subclass, nesting past 200, a lone surrogate, an int past CPython's digit limit, bytes that aren't plain JSON of
//! the expected shape) gives up before any effect and runs the reference's Python, embedded below, with the
//! stdlib json: so the result is the same and the exceptions are CPython's own, word for word (owner point 6 of
//! #144, provisional). No Python JSON runs while a step's values are of the types the add-on writes.

use std::io::Write;
use std::ptr::addr_of_mut;
use std::sync::atomic::{AtomicPtr, Ordering};

use pyo3::intern;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyFloat, PyModule, PyTuple};

use super::*;

const MAX_DEPTH: u32 = 200;

/// The fast path gave up: the reference's Python decides.
struct Bail;
type R<T> = Result<T, Bail>;

const FALLBACK: &str = r#"
import json
from json.decoder import _CONSTANTS

CONSTANTS = (_CONSTANTS["NaN"], _CONSTANTS["Infinity"], _CONSTANTS["-Infinity"])
MISSING = DataChange = CorruptStep = None      # set by the engine (json_setup)
_MISSING_JSON = {"$": "missing"}


def _json_default(o):
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def dumps(obj):
    return json.dumps(obj, separators=(",", ":"), default=_json_default).encode("utf-8")


def loads(b):
    return json.loads(bytes(b).decode("utf-8"))


def _enc_value(v):
    if v is MISSING:
        return _MISSING_JSON
    if isinstance(v, (tuple, list)):
        return [_enc_value(x) for x in v]
    if isinstance(v, dict):
        return {"$d": {str(k): _enc_value(x) for k, x in v.items()}}
    return v


def _dec_value(v):
    if isinstance(v, dict):
        if v == _MISSING_JSON:
            return MISSING
        if len(v) == 1 and isinstance(v.get("$d"), dict):
            return {k: _dec_value(x) for k, x in v["$d"].items()}
        raise ValueError("records: unexpected object value")
    if isinstance(v, list):
        return [_dec_value(x) for x in v]
    return v


def records_to_bytes(records):
    out = [[r.adapter, r.target, [[p, _enc_value(o), _enc_value(n)] for p, (o, n) in r.changes.items()]]
           for r in records]
    return json.dumps(out, separators=(",", ":"), allow_nan=True).encode("utf-8")


def records_from_bytes(b):
    out = []
    for adapter, target, changes in json.loads(bytes(b).decode("utf-8")):
        if not isinstance(adapter, str) or not isinstance(target, str):
            raise ValueError("records: bad adapter or target")
        out.append(DataChange(adapter, target, {str(p): (_dec_value(o), _dec_value(n)) for p, o, n in changes}))
    return out


def extra(raw):
    if not raw:
        return {}
    try:
        e = json.loads(raw.decode("utf-8"))
        return dict(e)
    except (ValueError, TypeError) as e:
        raise CorruptStep(f"step blob: bad header ({e})") from e
"#;

static PY: GILOnceCell<Py<PyModule>> = GILOnceCell::new();
static MISSING: AtomicPtr<ffi::PyObject> = AtomicPtr::new(std::ptr::null_mut());
static DATACHANGE: AtomicPtr<ffi::PyObject> = AtomicPtr::new(std::ptr::null_mut());

fn fallback(py: Python<'_>) -> PyResult<&Bound<'_, PyModule>> {
    Ok(PY
        .get_or_try_init(py, || -> PyResult<Py<PyModule>> {
            let m = PyModule::from_code_bound(py, FALLBACK, "hindsight_native/stepjson.py", "hindsight_native._stepjson")?;
            m.setattr("CorruptStep", py.get_type_bound::<CorruptStep>())?;
            Ok(m.unbind())
        })?
        .bind(py))
}

fn slow<'py>(py: Python<'py>, name: &str, arg: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let f = fallback(py)?.getattr(name)?;
    // Python 3.11 counts this builtin's own call against the limit Python frames use: give it back for the
    // call, so the reference's code runs as deep as it did in step.py (the same RecursionError, word for word)
    let shared = py.version_info() < (3, 12);
    if shared {
        // SAFETY: the GIL is held; the count taken on entering this builtin is given back below
        unsafe { ffi::Py_LeaveRecursiveCall() };
    }
    let r = f.call1((arg,));
    if shared && unsafe { ffi::Py_EnterRecursiveCall(b"\0".as_ptr() as *const std::os::raw::c_char) } != 0 {
        unsafe { ffi::PyErr_Clear() }; // the depth is back where it was when this builtin was entered
    }
    r
}

/// MISSING and DataChange, or an error if step.py never gave them.
fn records_types() -> PyResult<(*mut ffi::PyObject, *mut ffi::PyObject)> {
    let (m, d) = (MISSING.load(Ordering::Acquire), DATACHANGE.load(Ordering::Acquire));
    if m.is_null() || d.is_null() {
        return Err(PyRuntimeError::new_err("hindsight_native: json_setup(MISSING, DataChange) was not called"));
    }
    Ok((m, d))
}

// --------------------------------------------------------------------------- writing

fn hex4(out: &mut Vec<u8>, n: u32) {
    const H: &[u8; 16] = b"0123456789abcdef";
    out.extend_from_slice(b"\\u");
    for s in [12, 8, 4, 0] {
        out.push(H[((n >> s) & 15) as usize]);
    }
}

/// json's ensure_ascii string: printable ASCII as it is, the short escapes, everything else as \uXXXX
/// (lowercase, a surrogate pair above the BMP).
fn esc(s: &str, out: &mut Vec<u8>) {
    out.push(b'"');
    for c in s.chars() {
        match c {
            '"' => out.extend_from_slice(b"\\\""),
            '\\' => out.extend_from_slice(b"\\\\"),
            '\n' => out.extend_from_slice(b"\\n"),
            '\r' => out.extend_from_slice(b"\\r"),
            '\t' => out.extend_from_slice(b"\\t"),
            '\u{8}' => out.extend_from_slice(b"\\b"),
            '\u{c}' => out.extend_from_slice(b"\\f"),
            ' '..='~' => out.push(c as u8),
            _ => {
                let n = c as u32;
                if n >= 0x10000 {
                    hex4(out, 0xd800 | ((n - 0x10000) >> 10));
                    hex4(out, 0xdc00 | ((n - 0x10000) & 0x3ff));
                } else {
                    hex4(out, n);
                }
            }
        }
    }
    out.push(b'"');
}

/// SAFETY (all the unsafe fns below): o is a live object, the GIL is held.
unsafe fn utf8<'a>(o: *mut ffi::PyObject) -> R<&'a str> {
    let mut n: ffi::Py_ssize_t = 0;
    let p = ffi::PyUnicode_AsUTF8AndSize(o, &mut n);
    if p.is_null() {
        ffi::PyErr_Clear(); // a lone surrogate
        return Err(Bail);
    }
    Ok(std::str::from_utf8_unchecked(std::slice::from_raw_parts(p as *const u8, n as usize)))
}

unsafe fn is_str(o: *mut ffi::PyObject) -> bool {
    ffi::Py_TYPE(o) == addr_of_mut!(ffi::PyUnicode_Type)
}

/// Items of an exact list or tuple, else None.
unsafe fn seq(o: *mut ffi::PyObject) -> Option<Vec<*mut ffi::PyObject>> {
    let t = ffi::Py_TYPE(o);
    if t == addr_of_mut!(ffi::PyList_Type) {
        Some((0..ffi::PyList_Size(o)).map(|i| ffi::PyList_GetItem(o, i)).collect())
    } else if t == addr_of_mut!(ffi::PyTuple_Type) {
        Some((0..ffi::PyTuple_Size(o)).map(|i| ffi::PyTuple_GetItem(o, i)).collect())
    } else {
        None
    }
}

/// The key and value pairs of an exact dict, else None.
unsafe fn items(o: *mut ffi::PyObject) -> Option<Vec<(*mut ffi::PyObject, *mut ffi::PyObject)>> {
    if ffi::Py_TYPE(o) != addr_of_mut!(ffi::PyDict_Type) {
        return None;
    }
    let mut out = Vec::with_capacity(ffi::PyDict_Size(o) as usize);
    let (mut pos, mut k, mut v) = (0, std::ptr::null_mut(), std::ptr::null_mut());
    while ffi::PyDict_Next(o, &mut pos, &mut k, &mut v) != 0 {
        out.push((k, v));
    }
    Some(out)
}

/// The repr of an exact int or float (C code, no Python code runs), written.
unsafe fn repr(o: *mut ffi::PyObject, out: &mut Vec<u8>) -> R<()> {
    let r = ffi::PyObject_Repr(o);
    if r.is_null() {
        ffi::PyErr_Clear();
        return Err(Bail);
    }
    let t = utf8(r).map(|t| out.extend_from_slice(t.as_bytes()));
    ffi::Py_DECREF(r);
    t
}

/// One value as json.dumps(..., separators=(",", ":")) writes it.
unsafe fn enc(o: *mut ffi::PyObject, out: &mut Vec<u8>, depth: u32) -> R<()> {
    let t = ffi::Py_TYPE(o);
    if o == ffi::Py_None() {
        out.extend_from_slice(b"null");
    } else if o == ffi::Py_True() {
        out.extend_from_slice(b"true");
    } else if o == ffi::Py_False() {
        out.extend_from_slice(b"false");
    } else if t == addr_of_mut!(ffi::PyUnicode_Type) {
        esc(utf8(o)?, out);
    } else if t == addr_of_mut!(ffi::PyLong_Type) {
        let v = ffi::PyLong_AsLongLong(o);
        if v == -1 && !ffi::PyErr_Occurred().is_null() {
            ffi::PyErr_Clear();
            repr(o, out)?; // past i64: int's own repr, as json's encoder calls it (its digit limit bails)
        } else {
            let _ = write!(out, "{}", v);
        }
    } else if t == addr_of_mut!(ffi::PyFloat_Type) {
        let x = ffi::PyFloat_AsDouble(o);
        if x.is_nan() {
            out.extend_from_slice(b"NaN");
        } else if x.is_infinite() {
            out.extend_from_slice(if x > 0.0 { b"Infinity" as &[u8] } else { b"-Infinity" });
        } else {
            repr(o, out)?; // float's own repr, as json's encoder calls it (shortest digits, ties to even)
        }
    } else if depth >= MAX_DEPTH {
        return Err(Bail); // deep, or a cycle: json's own recursion check and messages
    } else if let Some(xs) = seq(o) {
        out.push(b'[');
        for (i, x) in xs.into_iter().enumerate() {
            if i > 0 {
                out.push(b',');
            }
            enc(x, out, depth + 1)?;
        }
        out.push(b']');
    } else if let Some(kv) = items(o) {
        out.push(b'{');
        for (i, (k, v)) in kv.into_iter().enumerate() {
            if !is_str(k) {
                return Err(Bail);
            }
            if i > 0 {
                out.push(b',');
            }
            esc(utf8(k)?, out);
            out.push(b':');
            enc(v, out, depth + 1)?;
        }
        out.push(b'}');
    } else {
        return Err(Bail);
    }
    Ok(())
}

/// A data record's value as _enc_value makes it, written.
unsafe fn enc_value(v: *mut ffi::PyObject, missing: *mut ffi::PyObject, out: &mut Vec<u8>, depth: u32) -> R<()> {
    if v == missing {
        out.extend_from_slice(br#"{"$":"missing"}"#);
        return Ok(());
    }
    if depth >= MAX_DEPTH {
        return Err(Bail);
    }
    if let Some(xs) = seq(v) {
        out.push(b'[');
        for (i, x) in xs.into_iter().enumerate() {
            if i > 0 {
                out.push(b',');
            }
            enc_value(x, missing, out, depth + 1)?;
        }
        out.push(b']');
    } else if let Some(kv) = items(v) {
        out.extend_from_slice(br#"{"$d":{"#);
        for (i, (k, x)) in kv.into_iter().enumerate() {
            if !is_str(k) {
                return Err(Bail); // str(k) of another type: keys can merge
            }
            if i > 0 {
                out.push(b',');
            }
            esc(utf8(k)?, out);
            out.push(b':');
            enc_value(x, missing, out, depth + 1)?;
        }
        out.extend_from_slice(b"}}");
    } else {
        enc(v, out, depth)?;
    }
    Ok(())
}

unsafe fn enc_records(py: Python<'_>, records: *mut ffi::PyObject, out: &mut Vec<u8>) -> PyResult<R<()>> {
    let (missing, dc) = records_types()?;
    if ffi::Py_TYPE(records) != addr_of_mut!(ffi::PyList_Type) {
        return Ok(Err(Bail));
    }
    let rs: Vec<Bound<'_, PyAny>> =
        seq(records).unwrap_or_default().into_iter().map(|r| Bound::from_borrowed_ptr(py, r)).collect();
    Ok((|| {
        out.push(b'[');
        for (i, r) in rs.iter().enumerate() {
            // an exact DataChange: its attributes are plain, reading them runs no Python code
            if ffi::Py_TYPE(r.as_ptr()) as *mut ffi::PyObject != dc {
                return Err(Bail);
            }
            let attr = |n: &Bound<'_, PyString>| r.getattr(n).map_err(|_| Bail);
            let (a, t, c) = (attr(intern!(py, "adapter"))?, attr(intern!(py, "target"))?, attr(intern!(py, "changes"))?);
            if i > 0 {
                out.push(b',');
            }
            out.push(b'[');
            enc(a.as_ptr(), out, 2)?;
            out.push(b',');
            enc(t.as_ptr(), out, 2)?;
            out.extend_from_slice(b",[");
            for (j, (p, on)) in items(c.as_ptr()).ok_or(Bail)?.into_iter().enumerate() {
                let pair = seq(on).ok_or(Bail)?;
                if pair.len() != 2 {
                    return Err(Bail);
                }
                if j > 0 {
                    out.push(b',');
                }
                out.push(b'[');
                enc(p, out, 3)?;
                out.push(b',');
                enc_value(pair[0], missing, out, 3)?;
                out.push(b',');
                enc_value(pair[1], missing, out, 3)?;
                out.push(b']');
            }
            out.extend_from_slice(b"]]");
        }
        out.push(b']');
        Ok(())
    })())
}

// --------------------------------------------------------------------------- reading

struct Parser<'a, 'py> {
    py: Python<'py>,
    s: &'a [u8],
    i: usize,
}

impl<'a, 'py> Parser<'a, 'py> {
    fn peek(&self) -> Option<u8> {
        self.s.get(self.i).copied()
    }

    fn ws(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.i += 1;
        }
    }

    fn lit(&mut self, w: &[u8]) -> R<()> {
        if self.s[self.i..].starts_with(w) {
            self.i += w.len();
            Ok(())
        } else {
            Err(Bail)
        }
    }

    fn constant(&mut self, w: &[u8], k: usize) -> R<Bound<'py, PyAny>> {
        self.lit(w)?;
        // json.loads gives json.decoder's own NaN and infinities (the same objects every time)
        let m = fallback(self.py).map_err(|_| Bail)?;
        let c = m.getattr("CONSTANTS").and_then(|c| c.get_item(k)).map_err(|_| Bail)?;
        Ok(c)
    }

    fn value(&mut self, depth: u32) -> R<Bound<'py, PyAny>> {
        let py = self.py;
        match self.peek().ok_or(Bail)? {
            b'"' => Ok(self.string()?.into_any()),
            b'{' => {
                if depth >= MAX_DEPTH {
                    return Err(Bail);
                }
                self.i += 1;
                let d = PyDict::new_bound(py);
                self.ws();
                if self.peek() == Some(b'}') {
                    self.i += 1;
                    return Ok(d.into_any());
                }
                loop {
                    if self.peek() != Some(b'"') {
                        return Err(Bail);
                    }
                    let k = self.string()?;
                    self.ws();
                    if self.peek() != Some(b':') {
                        return Err(Bail);
                    }
                    self.i += 1;
                    self.ws();
                    let v = self.value(depth + 1)?;
                    d.set_item(k, v).map_err(|_| Bail)?;
                    self.ws();
                    match self.peek() {
                        Some(b',') => {
                            self.i += 1;
                            self.ws();
                        }
                        Some(b'}') => {
                            self.i += 1;
                            return Ok(d.into_any());
                        }
                        _ => return Err(Bail),
                    }
                }
            }
            b'[' => {
                if depth >= MAX_DEPTH {
                    return Err(Bail);
                }
                self.i += 1;
                let l = PyList::empty_bound(py);
                self.ws();
                if self.peek() == Some(b']') {
                    self.i += 1;
                    return Ok(l.into_any());
                }
                loop {
                    l.append(self.value(depth + 1)?).map_err(|_| Bail)?;
                    self.ws();
                    match self.peek() {
                        Some(b',') => {
                            self.i += 1;
                            self.ws();
                        }
                        Some(b']') => {
                            self.i += 1;
                            return Ok(l.into_any());
                        }
                        _ => return Err(Bail),
                    }
                }
            }
            b'n' => self.lit(b"null").map(|_| py.None().into_bound(py)),
            b't' => self.lit(b"true").map(|_| true.into_py(py).into_bound(py)),
            b'f' => self.lit(b"false").map(|_| false.into_py(py).into_bound(py)),
            b'N' => self.constant(b"NaN", 0),
            b'I' => self.constant(b"Infinity", 1),
            b'-' if self.s.get(self.i + 1) == Some(&b'I') => self.constant(b"-Infinity", 2),
            b'-' | b'0'..=b'9' => self.number(),
            _ => Err(Bail),
        }
    }

    fn digits(&mut self) -> usize {
        let st = self.i;
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.i += 1;
        }
        self.i - st
    }

    /// json's number: -?(0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?, a float if it has a fraction or exponent.
    fn number(&mut self) -> R<Bound<'py, PyAny>> {
        let st = self.i;
        if self.peek() == Some(b'-') {
            self.i += 1;
        }
        match self.peek() {
            Some(b'0') => self.i += 1,
            Some(b'1'..=b'9') => {
                self.digits();
            }
            _ => return Err(Bail),
        }
        let mut float = false;
        if self.peek() == Some(b'.') && matches!(self.s.get(self.i + 1), Some(b'0'..=b'9')) {
            self.i += 1;
            self.digits();
            float = true;
        }
        if matches!(self.peek(), Some(b'e' | b'E')) {
            let back = self.i;
            self.i += 1;
            if matches!(self.peek(), Some(b'+' | b'-')) {
                self.i += 1;
            }
            if self.digits() == 0 {
                self.i = back; // not an exponent: the number ends before it
            } else {
                float = true;
            }
        }
        // SAFETY: ASCII digits and signs only
        let txt = unsafe { std::str::from_utf8_unchecked(&self.s[st..self.i]) };
        if float {
            let x: f64 = txt.parse().map_err(|_| Bail)?;
            Ok(PyFloat::new_bound(self.py, x).into_any())
        } else if txt.len() <= 18 {
            let v: i64 = txt.parse().map_err(|_| Bail)?;
            Ok(v.into_py(self.py).into_bound(self.py))
        } else {
            // a big int, as int() reads it (its digit limit bails)
            let c = std::ffi::CString::new(txt).map_err(|_| Bail)?;
            // SAFETY: c is a NUL-terminated string of digits, the GIL is held
            unsafe {
                let p = ffi::PyLong_FromString(c.as_ptr(), std::ptr::null_mut(), 10);
                if p.is_null() {
                    ffi::PyErr_Clear();
                    return Err(Bail);
                }
                Ok(Bound::from_owned_ptr(self.py, p))
            }
        }
    }

    fn hex(&self, at: usize) -> R<u32> {
        let h = self.s.get(at..at + 4).ok_or(Bail)?;
        let mut n = 0u32;
        for &c in h {
            n = n * 16 + (c as char).to_digit(16).ok_or(Bail)?;
        }
        Ok(n)
    }

    fn string(&mut self) -> R<Bound<'py, PyString>> {
        self.i += 1;
        let st = self.i;
        loop {
            match self.peek().ok_or(Bail)? {
                b'"' => {
                    // SAFETY: the whole input was checked to be UTF-8, and a quote ends a character
                    let s = unsafe { std::str::from_utf8_unchecked(&self.s[st..self.i]) };
                    self.i += 1;
                    return Ok(PyString::new_bound(self.py, s));
                }
                b'\\' => break,
                c if c < 0x20 => return Err(Bail),
                _ => self.i += 1,
            }
        }
        let mut buf = String::from(unsafe { std::str::from_utf8_unchecked(&self.s[st..self.i]) });
        loop {
            match self.peek().ok_or(Bail)? {
                b'"' => {
                    self.i += 1;
                    return Ok(PyString::new_bound(self.py, &buf));
                }
                b'\\' => {
                    let c = match self.s.get(self.i + 1).ok_or(Bail)? {
                        b'"' => '"',
                        b'\\' => '\\',
                        b'/' => '/',
                        b'b' => '\u{8}',
                        b'f' => '\u{c}',
                        b'n' => '\n',
                        b'r' => '\r',
                        b't' => '\t',
                        b'u' => {
                            let mut n = self.hex(self.i + 2)?;
                            if (0xdc00..0xe000).contains(&n) {
                                return Err(Bail); // a lone surrogate
                            }
                            if (0xd800..0xdc00).contains(&n) {
                                if self.s.get(self.i + 6..self.i + 8) != Some(b"\\u") {
                                    return Err(Bail);
                                }
                                let lo = self.hex(self.i + 8)?;
                                if !(0xdc00..0xe000).contains(&lo) {
                                    return Err(Bail);
                                }
                                n = 0x10000 + ((n - 0xd800) << 10) + (lo - 0xdc00);
                                self.i += 6;
                            }
                            self.i += 4;
                            char::from_u32(n).ok_or(Bail)?
                        }
                        _ => return Err(Bail),
                    };
                    buf.push(c);
                    self.i += 2;
                }
                c if c < 0x20 => return Err(Bail),
                _ => {
                    let st = self.i;
                    while !matches!(self.peek(), None | Some(b'"' | b'\\' | 0..=0x1f)) {
                        self.i += 1;
                    }
                    buf.push_str(unsafe { std::str::from_utf8_unchecked(&self.s[st..self.i]) });
                }
            }
        }
    }
}

/// json.loads(bytes(b).decode("utf-8")) for exact bytes holding plain JSON, else Bail.
fn parse<'py>(b: &Bound<'py, PyAny>) -> R<Bound<'py, PyAny>> {
    let b = b.downcast_exact::<PyBytes>().map_err(|_| Bail)?;
    let s = b.as_bytes();
    std::str::from_utf8(s).map_err(|_| Bail)?;
    let mut p = Parser { py: b.py(), s, i: 0 };
    p.ws();
    let v = p.value(0)?;
    p.ws();
    if p.i != s.len() {
        return Err(Bail);
    }
    Ok(v)
}

/// _dec_value of a freshly parsed value.
fn dec<'py>(v: Bound<'py, PyAny>, missing: &Bound<'py, PyAny>) -> R<Bound<'py, PyAny>> {
    let py = v.py();
    if let Ok(d) = v.downcast_exact::<PyDict>() {
        if d.len() != 1 {
            return Err(Bail);
        }
        if let Ok(Some(m)) = d.get_item("$") {
            return match m.downcast_exact::<PyString>() {
                Ok(s) if s.to_str().map_err(|_| Bail)? == "missing" => Ok(missing.clone()),
                _ => Err(Bail),
            };
        }
        let inner = d.get_item("$d").map_err(|_| Bail)?.ok_or(Bail)?;
        let inner = inner.downcast_exact::<PyDict>().map_err(|_| Bail)?;
        let out = PyDict::new_bound(py);
        for (k, x) in inner.iter() {
            out.set_item(k, dec(x, missing)?).map_err(|_| Bail)?;
        }
        return Ok(out.into_any());
    }
    if let Ok(l) = v.downcast_exact::<PyList>() {
        let out = PyList::empty_bound(py);
        for x in l.iter() {
            out.append(dec(x, missing)?).map_err(|_| Bail)?;
        }
        return Ok(out.into_any());
    }
    Ok(v)
}

fn exact_list<'a, 'py>(v: &'a Bound<'py, PyAny>, n: Option<usize>) -> R<&'a Bound<'py, PyList>> {
    let l = v.downcast_exact::<PyList>().map_err(|_| Bail)?;
    match n {
        Some(n) if l.len() != n => Err(Bail),
        _ => Ok(l),
    }
}

fn records_fast<'py>(b: &Bound<'py, PyAny>, missing: &Bound<'py, PyAny>, dc: &Bound<'py, PyAny>) -> R<Bound<'py, PyAny>> {
    let py = b.py();
    let top = parse(b)?;
    let out = PyList::empty_bound(py);
    for rec in exact_list(&top, None)?.iter() {
        let rec = exact_list(&rec, Some(3))?;
        let (a, t, ch) = (rec.get_item(0).map_err(|_| Bail)?, rec.get_item(1).map_err(|_| Bail)?, rec.get_item(2).map_err(|_| Bail)?);
        if !a.is_exact_instance_of::<PyString>() || !t.is_exact_instance_of::<PyString>() {
            return Err(Bail);
        }
        let changes = PyDict::new_bound(py);
        for c in exact_list(&ch, None)?.iter() {
            let c = exact_list(&c, Some(3))?;
            let p = c.get_item(0).map_err(|_| Bail)?;
            if !p.is_exact_instance_of::<PyString>() {
                return Err(Bail);
            }
            let o = dec(c.get_item(1).map_err(|_| Bail)?, missing)?;
            let n = dec(c.get_item(2).map_err(|_| Bail)?, missing)?;
            changes.set_item(p, PyTuple::new_bound(py, [o, n])).map_err(|_| Bail)?;
        }
        out.append(dc.call1((a, t, changes)).map_err(|_| Bail)?).map_err(|_| Bail)?;
    }
    Ok(out.into_any())
}

// --------------------------------------------------------------------------- the functions

/// json_setup(MISSING, DataChange) -> (_json_default, _enc_value, _dec_value): the records' marker and class
/// (step.py gives them when it is imported, again after a reload) and the reference's helpers.
#[pyfunction]
fn json_setup(py: Python<'_>, missing: &Bound<'_, PyAny>, datachange: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    guard(|| {
        let m = fallback(py)?;
        m.setattr("MISSING", missing)?;
        m.setattr("DataChange", datachange)?;
        // the old pair, if any, stays referenced: a reloaded add-on's old steps may still hold it
        MISSING.store(missing.clone().into_ptr(), Ordering::Release);
        DATACHANGE.store(datachange.clone().into_ptr(), Ordering::Release);
        Ok((m.getattr("_json_default")?, m.getattr("_enc_value")?, m.getattr("_dec_value")?).into_py(py))
    })
}

/// json_dumps(obj) -> bytes: step.py's _dumps (json.dumps, separators "," and ":", _json_default, utf-8).
#[pyfunction]
fn json_dumps<'py>(py: Python<'py>, obj: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    guard(|| {
        let mut out = Vec::with_capacity(64);
        // SAFETY: obj is borrowed for the call, the GIL is held
        match unsafe { enc(obj.as_ptr(), &mut out, 0) } {
            Ok(()) => Ok(PyBytes::new_bound(py, &out).into_any()),
            Err(Bail) => slow(py, "dumps", obj),
        }
    })
}

/// json_loads(b) -> object: json.loads(bytes(b).decode("utf-8")).
#[pyfunction]
fn json_loads<'py>(py: Python<'py>, b: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    guard(|| match parse(b) {
        Ok(v) => Ok(v),
        Err(Bail) => slow(py, "loads", b),
    })
}

/// records_to_bytes(records) -> bytes: step.py's records_to_bytes.
#[pyfunction]
fn records_to_bytes<'py>(py: Python<'py>, records: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    guard(|| {
        let mut out = Vec::with_capacity(256);
        // SAFETY: records is borrowed for the call, the GIL is held
        match unsafe { enc_records(py, records.as_ptr(), &mut out)? } {
            Ok(()) => Ok(PyBytes::new_bound(py, &out).into_any()),
            Err(Bail) => slow(py, "records_to_bytes", records),
        }
    })
}

/// records_from_bytes(b) -> [DataChange]: step.py's records_from_bytes.
#[pyfunction]
fn records_from_bytes<'py>(py: Python<'py>, b: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    guard(|| {
        let (m, d) = records_types()?;
        // SAFETY: both were kept referenced by json_setup
        let (m, d) = unsafe { (Bound::from_borrowed_ptr(py, m), Bound::from_borrowed_ptr(py, d)) };
        match records_fast(b, &m, &d) {
            Ok(v) => Ok(v),
            Err(Bail) => slow(py, "records_from_bytes", b),
        }
    })
}

fn extra<'py>(py: Python<'py>, raw: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    if let Ok(b) = raw.downcast_exact::<PyBytes>() {
        if b.as_bytes().is_empty() {
            return Ok(PyDict::new_bound(py).into_any());
        }
        if let Ok(v) = parse(raw) {
            if v.is_exact_instance_of::<PyDict>() {
                return Ok(v);
            }
        }
    }
    slow(py, "extra", raw)
}

/// step_extra(raw) -> dict: step.py's _extra (an extra's JSON; CorruptStep if it is damaged).
#[pyfunction]
fn step_extra<'py>(py: Python<'py>, raw: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    guard(|| extra(py, raw))
}

/// read_header(b) -> dict: step metadata without decompressing the body (step.py's read_header).
#[pyfunction]
fn read_header<'py>(py: Python<'py>, b: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let r = step::step_info(py, b)?.into_bound(py);
    guard(|| {
        let r = r.downcast::<PyTuple>()?;
        let g = |i: usize| r.get_item(i);
        if g(0)?.extract::<u8>()? == 1 {
            let h = g(1)?;
            return match parse(&h) {
                Ok(v) => Ok(v),
                Err(Bail) => slow(py, "loads", &h),
            };
        }
        let meta = PyDict::new_bound(py);
        for (k, i) in [("seq", 2), ("label", 4), ("mode", 5), ("target", 6), ("time", 3), ("kind", 1)] {
            meta.set_item(k, g(i)?)?;
        }
        meta.set_item("extra", extra(py, &g(7)?)?)?;
        let out = PyDict::new_bound(py);
        out.set_item("v", 3)?;
        out.set_item("meta", meta)?;
        for (k, i) in [("deltas", 8), ("before", 9), ("after", 10)] {
            out.set_item(k, g(i)?)?;
        }
        let snaps = g(11)?;
        if snaps.is_none() {
            out.set_item("snaps", snaps)?;
        } else {
            let l = PyList::empty_bound(py);
            for un in snaps.iter()? {
                let un = un?;
                l.append(PyList::new_bound(py, [un.get_item(0)?, un.get_item(1)?]))?;
            }
            out.set_item("snaps", l)?;
        }
        for (k, i) in [("raw", 12), ("quick", 13), ("records", 14)] {
            out.set_item(k, g(i)?)?;
        }
        Ok(out.into_any())
    })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(json_setup, m)?)?;
    m.add_function(wrap_pyfunction!(json_dumps, m)?)?;
    m.add_function(wrap_pyfunction!(json_loads, m)?)?;
    m.add_function(wrap_pyfunction!(records_to_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(records_from_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(step_extra, m)?)?;
    m.add_function(wrap_pyfunction!(read_header, m)?)?;
    Ok(())
}
