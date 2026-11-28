//! The crash journal: hindsight/core/journal.py's Journal (24.2a, #152; its Python is tests/reference/journal.py).
//!
//! Record (little endian): MAGIC(4) | type u8 | header_len u32 | payload_len u64 | header JSON | payload | crc32 of
//! everything before it. Reading stops at the first short or broken record, opening cuts a broken tail off, and a
//! failed write is cut back so the records after it aren't hidden behind a broken one: the reference's guarantees,
//! call for call. The file work runs with the GIL released and a lock held, so a helper thread's append never
//! blocks the main thread's Python. A test-only fault hook (owner point 2, dev/RUST_PLAN.md), off unless a test arms
//! it, fails the nth write, flush or fsync with an errno; `_drop` lets the file go without a clean close.

use std::fs::{self, File, OpenOptions};
use std::io::{self, BufReader, BufWriter, Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, MutexGuard};

use pyo3::exceptions::{PyKeyError, PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyString};

use crate::{guard, stepjson};

const MAGIC: &[u8; 4] = b"HSJ1";
const NAMES: [&str; 8] = ["STEP", "CURSOR", "TRUNC", "DROP", "RESET", "META", "SAVE", "META_EVERYTHING"];
const HEAD: usize = 17;
const MAX_HEADER: u64 = 64 << 20; // sanity bound for header JSON length
const CHUNK: usize = 4 << 20;
const READ_BUF: usize = 1 << 20; // R065: a record's parts come from memory, not one read of the file each
const WRITE_BUF: usize = 8192; // io.DEFAULT_BUFFER_SIZE, what open(path, "ab") buffered

// ------------------------------------------------------------------------------------ fault hook
struct Fault {
    op: Option<usize>, // 0 write, 1 flush, 2 fsync
    n: u64,
    errno: i32,
    short: bool,
    calls: [u64; 3],
    scans: Vec<u64>,
}

static ON: AtomicBool = AtomicBool::new(false);
static FAULT: Mutex<Fault> = Mutex::new(Fault { op: None, n: 0, errno: 0, short: false, calls: [0; 3], scans: Vec::new() });
const OPS: [&str; 3] = ["write", "flush", "fsync"];

fn hook() -> MutexGuard<'static, Fault> {
    FAULT.lock().unwrap_or_else(|e| e.into_inner())
}

/// The armed fault, if this is its call of `op`; counts the call while the hook is on.
fn fault(op: usize) -> Option<(i32, bool)> {
    if !ON.load(Ordering::Relaxed) {
        return None;
    }
    let mut f = hook();
    f.calls[op] += 1;
    if f.op == Some(op) {
        f.n -= 1;
        if f.n == 0 {
            f.op = None;
            return Some((f.errno, f.short));
        }
    }
    None
}

#[derive(Debug)]
struct Hooked(i32);

impl std::fmt::Display for Hooked {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "test fault, errno {}", self.0)
    }
}

impl std::error::Error for Hooked {}

fn hooked(e: i32) -> io::Error {
    io::Error::new(io::ErrorKind::Other, Hooked(e))
}

/// journal_fault(op=None, n=1, errno=0, short=False): test only. The nth `op` ("write", "flush" or "fsync") from now
/// fails with OSError(errno), a short one after writing half its bytes, as a full disk does; op None turns the hook off.
#[pyfunction]
#[pyo3(signature = (op=None, n=1, errno=0, short=false))]
fn journal_fault(op: Option<&str>, n: u64, errno: i32, short: bool) -> PyResult<()> {
    let at = match op {
        None => None,
        Some(o) => Some(OPS.iter().position(|x| *x == o).ok_or_else(|| PyValueError::new_err(format!("unknown op {o:?}")))?),
    };
    if at.is_some() && n == 0 {
        return Err(PyValueError::new_err("n counts from 1"));
    }
    let mut f = hook();
    (f.op, f.n, f.errno, f.short) = (at, n, errno, short);
    ON.store(at.is_some(), Ordering::Relaxed);
    Ok(())
}

/// journal_calls() -> dict: test only. The writes, flushes and fsyncs, and the limit of each scan, since the last
/// call; counting goes on from now until journal_fault(None).
#[pyfunction]
fn journal_calls(py: Python<'_>) -> PyResult<PyObject> {
    let mut f = hook();
    ON.store(true, Ordering::Relaxed);
    let d = PyDict::new_bound(py);
    for (i, k) in OPS.iter().enumerate() {
        d.set_item(k, f.calls[i])?;
    }
    d.set_item("scans", std::mem::take(&mut f.scans))?;
    f.calls = [0; 3];
    Ok(d.into_any().unbind())
}

// ------------------------------------------------------------------------------------ errors
/// The OSError Python's own call would raise: errno and the OS's words (on Windows from its error code, as CPython
/// does), the file name.
fn os_error(py: Python<'_>, e: &io::Error, name: Option<&str>) -> PyErr {
    let r = (|| -> PyResult<PyErr> {
        let os = py.import_bound("os")?;
        let ty = py.get_type_bound::<PyOSError>();
        let v = if let Some(h) = e.get_ref().and_then(|x| x.downcast_ref::<Hooked>()) {
            ty.call1((h.0, os.call_method1("strerror", (h.0,))?))?
        } else if let Some(code) = e.raw_os_error() {
            if cfg!(windows) {
                let s = e.to_string();
                let words = s.rsplit_once(" (os error").map_or(s.as_str(), |x| x.0);
                ty.call1((0, words.trim_end_matches(|c: char| c == '.' || c.is_whitespace()), name, code))?
            } else {
                ty.call1((code, os.call_method1("strerror", (code,))?, name))?
            }
        } else {
            ty.call1((e.to_string(),))?
        };
        Ok(PyErr::from_value_bound(v))
    })();
    r.unwrap_or_else(|x| x)
}

/// An fsync error that means "this file system has nothing to sync" (some network and FUSE ones), not "the data
/// didn't reach the disk".
fn no_sync(py: Python<'_>, err: &PyErr) -> PyResult<bool> {
    let n = err.value_bound(py).getattr("errno")?;
    let m = py.import_bound("errno")?;
    for k in ["EINVAL", "ENOTSUP", "EOPNOTSUPP"] {
        if let Ok(v) = m.getattr(k) {
            if n.eq(v)? {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

enum Fail {
    Closed,
    Io(io::Error),
    Sync(io::Error),
}

fn done(py: Python<'_>, r: Result<(), Fail>, path: &str) -> PyResult<()> {
    match r {
        Ok(()) => Ok(()),
        Err(Fail::Closed) => Err(PyValueError::new_err("journal is closed")),
        Err(Fail::Io(e)) => Err(os_error(py, &e, Some(path))),
        Err(Fail::Sync(e)) => {
            let err = os_error(py, &e, None);
            if no_sync(py, &err)? {
                Ok(())
            } else {
                Err(err)
            }
        }
    }
}

// ------------------------------------------------------------------------------------ the file
/// The file a journal appends to; a write goes through the fault hook.
struct Out(File);

impl Write for Out {
    fn write(&mut self, b: &[u8]) -> io::Result<usize> {
        if let Some((e, short)) = fault(0) {
            if short {
                self.0.write_all(&b[..b.len() / 2])?;
            }
            return Err(hooked(e));
        }
        self.0.write(b)
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn open_append(path: &str) -> io::Result<BufWriter<Out>> {
    Ok(BufWriter::with_capacity(WRITE_BUF, Out(OpenOptions::new().append(true).create(true).open(path)?)))
}

fn set_len(path: &str, n: u64) -> io::Result<()> {
    OpenOptions::new().write(true).open(path)?.set_len(n)
}

struct Inner {
    size: u64,
    good: u64, // size up to which every record was written out whole
    fh: Option<BufWriter<Out>>,
}

impl Inner {
    /// After a failed write (disk full, I/O error): drop whatever part of a record reached the file. `size` is where
    /// the failed record started; if what was buffered before it can't be written out either, the file goes back
    /// to the last good flush.
    fn cut_back(&mut self, path: &str, mut size: u64) -> Result<(), Fail> {
        if let Some(mut w) = self.fh.take() {
            if w.flush().is_err() {
                size = self.good;
            }
            drop(w.into_parts()); // closed without writing again
        }
        let _ = set_len(path, size);
        self.size = size;
        self.good = self.good.min(size);
        self.fh = Some(open_append(path).map_err(Fail::Io)?);
        Ok(())
    }

    fn append(&mut self, path: &str, prefix: &[u8], payload: &[u8], crc: u32) -> Result<u64, Fail> {
        let start = self.size;
        let w = self.fh.as_mut().ok_or(Fail::Closed)?;
        let r = w
            .write_all(prefix)
            .and_then(|_| if payload.is_empty() { Ok(()) } else { w.write_all(payload) })
            .and_then(|_| w.write_all(&crc.to_le_bytes()));
        if let Err(e) = r {
            self.cut_back(path, start)?;
            return Err(Fail::Io(e));
        }
        self.size = start + (prefix.len() + payload.len() + 4) as u64;
        Ok(start)
    }

    fn flush(&mut self, path: &str, fsync: bool) -> Result<(), Fail> {
        let Some(w) = self.fh.as_mut() else { return Ok(()) };
        let r = match fault(1) {
            Some((e, short)) => {
                if short {
                    let half = w.buffer()[..w.buffer().len() / 2].to_vec();
                    let _ = w.get_mut().0.write_all(&half);
                }
                Err(hooked(e))
            }
            None => w.flush(),
        };
        if let Err(e) = r {
            let good = self.good;
            self.cut_back(path, good)?;
            return Err(Fail::Io(e));
        }
        self.good = self.size;
        if fsync {
            let f = &self.fh.as_ref().expect("flushed above").get_ref().0;
            match fault(2) {
                Some((e, _)) => return Err(Fail::Sync(hooked(e))),
                None => f.sync_all().map_err(Fail::Sync)?,
            }
        }
        Ok(())
    }
}

/// _scan and _records: the records of `f` up to `limit`, stopping at the first short or broken one; with `out`,
/// (name, header, payload) of each goes into it up to the first header that isn't JSON. Where the last good record
/// ends, and the read error that stopped it, if one did.
fn scan(py: Python<'_>, f: File, limit: u64, want: bool, out: Option<&Bound<'_, PyList>>) -> PyResult<(u64, Option<io::Error>)> {
    if ON.load(Ordering::Relaxed) {
        hook().scans.push(limit);
    }
    let mut r = BufReader::with_capacity(READ_BUF, f);
    let mut pos = 0u64;
    let mut ok = true;
    let mut chunk = Vec::new();
    macro_rules! get {
        ($e:expr) => {
            match $e {
                Ok(v) => v,
                Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Ok((pos, None)),
                Err(e) => return Ok((pos, Some(e))),
            }
        };
    }
    loop {
        if pos + HEAD as u64 > limit {
            return Ok((pos, None));
        }
        let mut head = [0u8; HEAD];
        get!(r.read_exact(&mut head));
        let code = head[4];
        let hlen = u32::from_le_bytes(head[5..9].try_into().unwrap()) as u64;
        let plen = u64::from_le_bytes(head[9..17].try_into().unwrap());
        if &head[..4] != MAGIC || !(1..=8).contains(&code) || hlen > MAX_HEADER {
            return Ok((pos, None));
        }
        let end = pos as u128 + HEAD as u128 + hlen as u128 + plen as u128 + 4;
        if end > limit as u128 {
            return Ok((pos, None)); // so every length below is within the file
        }
        let mut hb = vec![0u8; hlen as usize];
        get!(r.read_exact(&mut hb));
        let mut h = crc32fast::Hasher::new();
        h.update(&head);
        h.update(&hb);
        let payload = if want {
            let mut failed = None;
            let p = PyBytes::new_bound_with(py, plen as usize, |b| {
                failed = r.read_exact(b).err();
                Ok(())
            })?;
            if let Some(e) = failed {
                get!(Err::<(), _>(e));
            }
            h.update(p.as_bytes());
            Some(p)
        } else {
            let mut left = plen;
            while left > 0 {
                let n = (left as usize).min(CHUNK);
                chunk.resize(n, 0);
                get!(r.read_exact(&mut chunk));
                h.update(&chunk);
                left -= n as u64;
            }
            None
        };
        let mut tail = [0u8; 4];
        get!(r.read_exact(&mut tail));
        if u32::from_le_bytes(tail) != h.finalize() {
            return Ok((pos, None));
        }
        if let (Some(out), true) = (out, ok) {
            let header = if hb.is_empty() {
                Some(PyDict::new_bound(py).into_any())
            } else {
                match stepjson::json_loads(py, PyBytes::new_bound(py, &hb).as_any()) {
                    Ok(v) => Some(v),
                    Err(e) if e.is_instance_of::<PyValueError>(py) => None, // UnicodeDecodeError too
                    Err(e) => return Err(e),
                }
            };
            match header {
                Some(hd) => out.append((NAMES[code as usize - 1], hd, payload))?,
                None => ok = false,
            }
        }
        pos = end as u64;
    }
}

/// int(v) clamped to 0..=u64::MAX, as min and max of the reference clamp it.
fn as_size(py: Python<'_>, v: &Bound<'_, PyAny>) -> PyResult<u64> {
    let n = py.get_type_bound::<pyo3::types::PyLong>().call1((v,))?;
    if n.lt(0)? {
        return Ok(0);
    }
    Ok(n.extract::<u64>().unwrap_or(u64::MAX))
}

fn rtype_code(r: &Bound<'_, PyAny>) -> PyResult<u8> {
    if let Ok(s) = r.downcast::<PyString>() {
        if let Ok(s) = s.to_cow() {
            if let Some(i) = NAMES.iter().position(|n| *n == s) {
                return Ok(i as u8 + 1);
            }
        }
    } else {
        r.hash()?; // unhashable: the TypeError of RTYPES[rtype]
    }
    Err(PyKeyError::new_err(r.clone().unbind()))
}

/// An argument as given, None included (None as a Rust value means "not given").
fn given<'py>(o: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    Ok(Some(o.clone()))
}

/// Append-only record file (see the module docstring of journal.py).
#[pyclass(module = "hindsight_native")]
pub struct Journal {
    #[pyo3(get)]
    path: String,
    inner: Mutex<Inner>,
    kept: Mutex<Option<(u64, Py<PyList>)>>, // R065: (size, records) of the scan on open, until read() takes them
}

impl Journal {
    fn inner(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn kept(&self) -> MutexGuard<'_, Option<(u64, Py<PyList>)>> {
        self.kept.lock().unwrap_or_else(|e| e.into_inner())
    }
}

#[pymethods]
impl Journal {
    /// keep (R065): the caller reads the journal right away (crash recovery): the scan that finds where the good
    /// records end keeps them for the first read(). discard (R067): the caller starts the journal again: what is in
    /// it is let go unread.
    #[new]
    #[pyo3(signature = (path, keep=None, discard=None))]
    fn new(py: Python<'_>, path: &Bound<'_, PyAny>, keep: Option<&Bound<'_, PyAny>>, discard: Option<&Bound<'_, PyAny>>) -> PyResult<Self> {
        guard(|| {
            let truthy = |v: Option<&Bound<'_, PyAny>>| v.map_or(Ok(false), |v| v.is_truthy());
            let (keep, discard) = (truthy(keep)?, truthy(discard)?);
            let osp = py.import_bound("os")?.getattr("path")?;
            let p: String = osp.call_method1("abspath", (path,))?.extract()?;
            let parent: String = osp.call_method1("dirname", (&p,))?.extract()?;
            if !parent.is_empty() {
                fs::create_dir_all(&parent).map_err(|e| os_error(py, &e, Some(&parent)))?;
            }
            let oserr = |e: io::Error| os_error(py, &e, Some(&p));
            let mut size = 0u64;
            let mut kept = None;
            let exists = Path::new(&p).exists();
            if discard && !keep && exists {
                set_len(&p, 0).map_err(oserr)?;
            } else if exists {
                let file_size = fs::metadata(&p).map_err(oserr)?.len();
                let recs = if keep { Some(PyList::empty_bound(py)) } else { None };
                let (end, err) = scan(py, File::open(&p).map_err(oserr)?, file_size, keep, recs.as_ref())?;
                if let Some(e) = err {
                    return Err(oserr(e));
                }
                size = end;
                if file_size > size {
                    set_len(&p, size).map_err(oserr)?;
                }
                kept = recs.map(|r| (size, r.unbind()));
            }
            let fh = open_append(&p).map_err(oserr)?;
            Ok(Journal { path: p, inner: Mutex::new(Inner { size, good: size, fh: Some(fh) }), kept: Mutex::new(kept) })
        })
    }

    #[pyo3(signature = (rtype, header, payload=None))]
    fn append(
        &self,
        py: Python<'_>,
        rtype: &Bound<'_, PyAny>,
        header: &Bound<'_, PyAny>,
        #[pyo3(from_py_with = "given")] payload: Option<Bound<'_, PyAny>>,
    ) -> PyResult<u64> {
        guard(|| {
            if py.allow_threads(|| self.inner().fh.is_none()) {
                return Err(PyValueError::new_err("journal is closed"));
            }
            *self.kept() = None;
            let code = rtype_code(rtype)?;
            let hb = if header.is_none() { b"{}".to_vec() } else { stepjson::journal_dumps(py, header)? };
            let payload = match payload {
                None => PyBytes::new_bound(py, b""),
                Some(p) => match p.downcast_exact::<PyBytes>() {
                    Ok(b) => b.clone(),
                    Err(_) => py.get_type_bound::<PyBytes>().call1((p,))?.downcast_into::<PyBytes>()?,
                },
            };
            let data = payload.as_bytes();
            let mut prefix = Vec::with_capacity(HEAD + hb.len());
            prefix.extend_from_slice(MAGIC);
            prefix.push(code);
            prefix.extend_from_slice(&(hb.len() as u32).to_le_bytes());
            prefix.extend_from_slice(&(data.len() as u64).to_le_bytes());
            prefix.extend_from_slice(&hb);
            let (at, n) = (data.as_ptr() as usize, data.len());
            let r = py.allow_threads(|| {
                // SAFETY: an exact bytes object (immutable), kept alive by `payload` until this returns
                let data = unsafe { std::slice::from_raw_parts(at as *const u8, n) };
                let mut h = crc32fast::Hasher::new();
                h.update(&prefix);
                h.update(data);
                self.inner().append(&self.path, &prefix, data, h.finalize())
            });
            r.or_else(|f| done(py, Err(f), &self.path).map(|_| 0))
        })
    }

    /// Write the appended records out. Raises OSError if they could not be written (the records since the last good
    /// flush are dropped from the file again) or, with fsync, if the disk did not confirm them.
    #[pyo3(signature = (fsync=None))]
    fn flush(&self, py: Python<'_>, #[pyo3(from_py_with = "given")] fsync: Option<Bound<'_, PyAny>>) -> PyResult<()> {
        guard(|| {
            let fsync = match fsync {
                None => true,
                Some(v) => v.is_truthy()?,
            };
            let r = py.allow_threads(|| self.inner().flush(&self.path, fsync));
            done(py, r, &self.path)
        })
    }

    fn size(&self, py: Python<'_>) -> u64 {
        py.allow_threads(|| self.inner().size)
    }

    #[pyo3(signature = (upto=None))]
    fn read(&self, py: Python<'_>, upto: Option<&Bound<'_, PyAny>>) -> PyResult<Py<PyList>> {
        guard(|| {
            let kept = self.kept().take();
            let size = py.allow_threads(|| self.inner().size);
            if let (Some((at, recs)), None) = (kept, upto) {
                if at == size {
                    return Ok(recs);
                }
            }
            let r = py.allow_threads(|| match self.inner().fh.as_mut() {
                Some(w) => w.flush(),
                None => Ok(()),
            });
            r.map_err(|e| os_error(py, &e, Some(&self.path)))?;
            let limit = match upto {
                None => size,
                Some(u) => as_size(py, u)?.min(size),
            };
            let out = PyList::empty_bound(py);
            if let Ok(f) = File::open(&self.path) {
                scan(py, f, limit, true, Some(&out))?; // a read error ends it with what was read (OSError suppressed)
            }
            Ok(out.unbind())
        })
    }

    fn truncate(&self, py: Python<'_>, size: &Bound<'_, PyAny>) -> PyResult<()> {
        guard(|| {
            *self.kept() = None;
            let want = as_size(py, size)?;
            let r = py.allow_threads(|| {
                let mut g = self.inner();
                let size = want.min(g.size);
                if let Some(mut w) = g.fh.take() {
                    w.flush().map_err(Fail::Io)?;
                }
                set_len(&self.path, size).map_err(Fail::Io)?;
                (g.size, g.good) = (size, size);
                g.fh = Some(open_append(&self.path).map_err(Fail::Io)?);
                Ok(())
            });
            done(py, r, &self.path)
        })
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        guard(|| {
            let r = py.allow_threads(|| {
                let mut g = self.inner();
                if g.fh.is_none() {
                    return Ok(());
                }
                let r = g.flush(&self.path, true);
                g.fh = None;
                r
            });
            done(py, r, &self.path)
        })
    }

    /// Test only: the process dies. What is buffered is written out (as the reference's file object closing), with
    /// no fsync and no close of the journal's own.
    fn _drop(&self, py: Python<'_>) -> PyResult<()> {
        let r = py.allow_threads(|| match self.inner().fh.take() {
            Some(mut w) => w.flush().map_err(Fail::Io),
            None => Ok(()),
        });
        done(py, r, &self.path)
    }

    #[getter]
    fn closed(&self, py: Python<'_>) -> bool {
        py.allow_threads(|| self.inner().fh.is_none())
    }

    fn __enter__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    #[pyo3(signature = (*_exc))]
    fn __exit__(&self, py: Python<'_>, _exc: &Bound<'_, pyo3::types::PyTuple>) -> PyResult<()> {
        self.close(py)
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Journal>()?;
    m.add_function(wrap_pyfunction!(journal_fault, m)?)?;
    m.add_function(wrap_pyfunction!(journal_calls, m)?)?;
    Ok(())
}
