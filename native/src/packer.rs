//! store.Packer (24.5b, #155): the packer is a Rust thread. It packs the raw steps handed to it, in order, once no new
//! one has come for QUIET_S (or right away while a drain waits), and when it and the user have been idle (IDLE_S,
//! USER_IDLE_S) it recompresses packed steps with the strong setting, oldest first, using at most DUTY of one core.
//! The thread holds no Python object (dev/RUST_DESIGN.md, GIL rule 3): a step comes as its parts' bytes and pinned
//! arrays (made on the main thread at `submit`, owner point 1), a blob to recompress as its pinned bytes, handed over
//! one at a time when the thread is ready for it. Its results wait in `done` until the main thread takes them in, at
//! its next call into the packer (provisional owner point 6, dev/RUST_PLAN.md), where the entries change as the
//! Python worker changed them. The Python it replaces is tests/reference/store.py's `Packer`.
use std::collections::{HashSet, VecDeque};
use std::mem::ManuallyDrop;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use pyo3::exceptions::PyRuntimeError;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyBytes, PyList};

use crate::step::{self, recompress_core, Enc, StepErr};
use crate::store::store_can_gain;
use crate::Buf;

// ------------------------------------------------------------------------------------------- test hooks and clock
static FAKE: AtomicU64 = AtomicU64::new(u64::MAX); // the test's clock (f64 bits); MAX: the real one
static LOG_ON: AtomicBool = AtomicBool::new(false);
static LOG: Mutex<Vec<(&'static str, u32)>> = Mutex::new(Vec::new());

/// time.monotonic() for the packer: seconds, or the test's clock while one is set.
fn now() -> f64 {
    let b = FAKE.load(Relaxed);
    if b != u64::MAX {
        return f64::from_bits(b);
    }
    static T0: OnceLock<Instant> = OnceLock::new();
    T0.get_or_init(Instant::now).elapsed().as_secs_f64()
}

fn log(op: &'static str, b: &[u8]) {
    if LOG_ON.load(Relaxed) {
        LOG.lock().unwrap_or_else(|e| e.into_inner()).push((op, crc32fast::hash(b)));
    }
}

/// packer_hook(clock=None, log=False) -> [(op, crc32)]: test only. Sets the packers' clock (None: the real one) and
/// whether the thread logs what it encodes ("pack", the blob's crc) and recompresses ("recompress", the input's);
/// returns the log so far and empties it.
#[pyfunction]
#[pyo3(signature = (clock=None, log=false))]
fn packer_hook(clock: Option<f64>, log: bool) -> Vec<(&'static str, u32)> {
    FAKE.store(clock.map_or(u64::MAX, f64::to_bits), Relaxed);
    LOG_ON.store(log, Relaxed);
    std::mem::take(&mut *LOG.lock().unwrap_or_else(|e| e.into_inner()))
}

static MODS: GILOnceCell<(PyObject, PyObject)> = GILOnceCell::new();

/// packer_setup(store, step): the modules a Packer reads when it is made: store's QUIET_S, IDLE_S, USER_IDLE_S, DUTY
/// and STRONG_MAX_BYTES (tests and benchmarks set them first) and step's step_parts.
#[pyfunction]
fn packer_setup(py: Python<'_>, store: PyObject, step: PyObject) {
    let _ = MODS.set(py, (store, step));
}

fn secs(s: f64) -> Duration {
    Duration::from_secs_f64(if s.is_nan() { 0.0 } else { s.clamp(0.0, 1e6) })
}

// ------------------------------------------------------------------------------------------- what crosses threads
/// Bytes pinned with the GIL on the main thread, read by the packer thread, released on the main thread (`release`).
/// One dropped anywhere else is leaked, never released without the GIL.
struct Pin<T>(ManuallyDrop<T>);

// SAFETY: the buffers are only read off the main thread; the only release is `Pin::release`, with the GIL.
unsafe impl<T> Send for Pin<T> {}

impl<T> Pin<T> {
    fn new(v: T) -> Self {
        Pin(ManuallyDrop::new(v))
    }

    fn release(mut self, _py: Python<'_>) {
        // SAFETY: taken once, here, with the GIL held (the token)
        unsafe { ManuallyDrop::drop(&mut self.0) }
    }
}

/// None: a panic in the engine (a bug).
type Res<T> = Result<T, Option<StepErr>>;

enum Work {
    Pack(Pin<Enc>),
    Failed, // step_parts raised on the main thread: counted when its turn comes, as the Python worker did
}

enum Done {
    Packed(Res<Vec<u8>>, Pin<Enc>),
    Failed,
    Strong(Res<Option<Vec<u8>>>, Pin<Buf>),
    Unused(Pin<Buf>), // handed over, not started: the packer stopped
}

struct St {
    work: VecDeque<Work>,
    pending: usize, // handed over, not done by the thread (the Python packer's _pending)
    hurry: usize,   // drains waiting: pack right away
    stopped: bool,
    alive: bool,
    last_submit: f64,
    last_activity: f64,
    offers: bool,             // the main thread holds steps to recompress, not handed over yet
    want: bool,               // the thread is idle enough to recompress one: hand it over
    staged: Option<Pin<Buf>>, // the blob handed over, not started (taken once `want`: the idle wait is the thread's)
    done: Vec<Done>,
    tried: u64, // recompressions started (bench/packer_bench.py's "looked_at")
    gen: u64,   // bumped by the main thread on every change the thread waits for
}

struct Shared {
    st: Mutex<St>,
    cv: Condvar,
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, St> {
        self.st.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn poke(&self, mut g: MutexGuard<'_, St>) {
        g.gen += 1;
        drop(g);
        self.cv.notify_all();
    }
}

#[derive(Clone, Copy)]
struct Times {
    quiet: f64,
    idle: f64,
    user_idle: f64,
    duty: f64,
}

// ------------------------------------------------------------------------------------------- the thread
fn run(sh: Arc<Shared>, t: Times) {
    let mut g = sh.lock();
    loop {
        if let Some(w) = g.work.pop_front() {
            while !g.stopped && g.hurry == 0 && now() - g.last_submit < t.quiet {
                g = sh.cv.wait_timeout(g, Duration::from_millis(50)).unwrap_or_else(|e| e.into_inner()).0;
            }
            drop(g);
            // shortcut: a step packed or replaced on the main thread meanwhile is still encoded (the Python worker
            // skipped it); its result is dropped at take-in. Upgrade if that waste ever shows in a profile.
            let d = match w {
                Work::Pack(enc) => {
                    let r = catch_unwind(AssertUnwindSafe(|| enc.0.encode())).map_err(|_| None).and_then(|r| r.map_err(Some));
                    if let Ok(b) = &r {
                        log("pack", b);
                    }
                    Done::Packed(r, enc)
                }
                Work::Failed => Done::Failed,
            };
            g = sh.lock();
            g.done.push(d);
            g.pending -= 1;
            sh.cv.notify_all();
            continue;
        }
        if g.stopped {
            break;
        }
        if let Some(blob) = g.want.then(|| g.staged.take()).flatten() {
            g.want = false;
            g.tried += 1;
            drop(g);
            log("recompress", blob.0.bytes());
            let t0 = Instant::now();
            let r = catch_unwind(AssertUnwindSafe(|| recompress_core(blob.0.bytes()))).map_err(|_| None).and_then(|r| r.map_err(Some));
            let dt = t0.elapsed().as_secs_f64();
            let ok = r.is_ok();
            g = sh.lock();
            g.done.push(Done::Strong(r, blob));
            sh.cv.notify_all();
            if ok && dt > 0.0 {
                // rest in proportion; only stopping ends the rest early
                let rest = secs((dt * (1.0 - t.duty) / t.duty).min(1.0));
                g = sh.cv.wait_timeout_while(g, rest, |s| !s.stopped).unwrap_or_else(|e| e.into_inner()).0;
            }
            continue;
        }
        let gen = g.gen;
        if !(g.offers || g.staged.is_some()) || g.want {
            g = sh.cv.wait_while(g, |s| s.gen == gen).unwrap_or_else(|e| e.into_inner());
            continue;
        }
        let wait = secs(t.idle.max(t.user_idle - (now() - g.last_activity)));
        let (g2, to) = sh.cv.wait_timeout_while(g, wait, |s| s.gen == gen).unwrap_or_else(|e| e.into_inner());
        g = g2;
        if to.timed_out() && now() - g.last_activity >= t.user_idle {
            g.want = true;
        }
    }
    if let Some(b) = g.staged.take() {
        g.done.push(Done::Unused(b));
    }
    g.alive = false;
    drop(g);
    sh.cv.notify_all();
}

// ------------------------------------------------------------------------------------------- the main thread's side
struct Main {
    packs: VecDeque<(PyObject, PyObject, Option<PyErr>)>, // entry, its step when handed over, step_parts' error
    inflight: HashSet<usize>,                             // id(entry) handed over, not taken in
    strong: VecDeque<PyObject>,                           // weak refs to packed entries not recompressed yet
    staged: Option<(PyObject, PyObject)>,                 // the entry handed over to recompress and its _bc then
    packed: u64,
    recompressed: u64,
    errors: u64,
    last_error: Option<String>,
}

impl Main {
    fn fail(&mut self, py: Python<'_>, e: PyErr) {
        self.errors += 1;
        self.last_error = Some(e.value_bound(py).repr().map(|s| s.to_string()).unwrap_or_default());
    }
}

fn py_err(e: Option<StepErr>) -> PyErr {
    match e {
        Some(e) => step::err(e),
        None => PyRuntimeError::new_err("hindsight_native: internal error"),
    }
}

#[pyclass(module = "hindsight_native", name = "Packer")]
pub struct Packer {
    sh: Arc<Shared>,
    m: Mutex<Main>,
    thread: Mutex<Option<thread::JoinHandle<()>>>,
    recompress: bool,
    user_idle: f64,
    strong_max: u64,
    step_parts: PyObject,
    wref: PyObject,
}

impl Packer {
    fn main(&self) -> MutexGuard<'_, Main> {
        self.m.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// The next offer that may gain, oldest first, as the Python worker picked it: the entry, its `_bc` and its blob.
    fn next_offer<'py>(&self, py: Python<'py>, m: &mut Main) -> Option<(Bound<'py, PyAny>, Bound<'py, PyAny>, Bound<'py, PyAny>)> {
        while let Some(w) = m.strong.pop_front() {
            let Ok(e) = w.call0(py).map(|e| e.into_bound(py)) else { continue };
            if e.is_none() {
                continue;
            }
            let (Ok(bc), Ok(blob)) = (e.getattr(intern!(py, "_bc")), e.getattr(intern!(py, "blob"))) else { continue };
            if blob.is_none() || blob.len().map_or(true, |n| n as u64 > self.strong_max) || !store_can_gain(py, &blob).unwrap_or(false) {
                continue;
            }
            return Some((e, bc, blob));
        }
        None
    }

    fn take_strong(&self, py: Python<'_>, m: &mut Main, e: &Bound<'_, PyAny>, bc: &Bound<'_, PyAny>, r: Res<Option<Vec<u8>>>) {
        let r = r.map_err(py_err).and_then(|new| match new {
            Some(v) if e.getattr(intern!(py, "_bc"))?.is(bc) => {
                e.setattr(intern!(py, "blob"), PyBytes::new_bound(py, &v))?;
                m.recompressed += 1;
                Ok(())
            }
            _ => Ok(()),
        });
        if let Err(err) = r {
            m.fail(py, err);
        }
    }

    fn take_pack(&self, py: Python<'_>, m: &mut Main, e: &Bound<'_, PyAny>, step: &Bound<'_, PyAny>, r: Res<Vec<u8>>) {
        let r = r.map_err(py_err).and_then(|v| {
            if e.getattr(intern!(py, "step"))?.is(step) {
                // not replaced or packed meanwhile
                e.setattr(intern!(py, "blob"), PyBytes::new_bound(py, &v))?;
                e.setattr(intern!(py, "step"), py.None())?;
                m.packed += 1;
                if self.recompress {
                    m.strong.push_back(self.wref.call1(py, (e,))?);
                }
            }
            Ok(())
        });
        if let Err(err) = r {
            m.fail(py, err);
        }
    }

    /// Take the thread's results in (main thread), then hand over the next step to recompress if it is ready for one.
    fn take_in(&self, py: Python<'_>) {
        let done = std::mem::take(&mut self.sh.lock().done);
        let mut m = self.main();
        for d in done {
            match d {
                Done::Packed(r, enc) => {
                    enc.release(py);
                    let (e, step, _) = m.packs.pop_front().expect("one hand-over per result");
                    self.take_pack(py, &mut m, e.bind(py), step.bind(py), r);
                    m.inflight.remove(&(e.as_ptr() as usize));
                }
                Done::Failed => {
                    let (e, _, err) = m.packs.pop_front().expect("one hand-over per result");
                    m.fail(py, err.expect("a failed hand-over has its error"));
                    m.inflight.remove(&(e.as_ptr() as usize));
                }
                Done::Strong(r, blob) => {
                    blob.release(py);
                    if let Some((e, bc)) = m.staged.take() {
                        self.take_strong(py, &mut m, e.bind(py), bc.bind(py), r);
                    }
                }
                Done::Unused(blob) => {
                    blob.release(py);
                    m.staged = None;
                }
            }
        }
        let g = self.sh.lock();
        // the next one is handed over as soon as the user is idle, so it is there when the thread's idle wait ends
        let ready = (g.want || now() - g.last_activity >= self.user_idle) && g.staged.is_none() && m.staged.is_none()
            && !g.stopped && !m.strong.is_empty();
        let offers = !m.strong.is_empty();
        if !ready && g.offers == offers {
            return;
        }
        drop(g);
        let next = if ready { self.next_offer(py, &mut m) } else { None };
        let pin = next.and_then(|(e, bc, blob)| {
            let pin = Buf::new(&blob, false).ok()?;
            m.staged = Some((e.unbind(), bc.unbind()));
            Some(Pin::new(pin))
        });
        let mut g = self.sh.lock();
        match pin {
            Some(p) => g.staged = Some(p),
            None if ready => g.want = false,
            None => {}
        }
        g.offers = !m.strong.is_empty();
        self.sh.poke(g);
    }
}

#[pymethods]
impl Packer {
    /// Packer(recompress_idle=True): starts the thread, its times store.py's as they are now (packer_setup).
    #[new]
    #[pyo3(signature = (recompress_idle=true))]
    fn new(py: Python<'_>, recompress_idle: bool) -> PyResult<Self> {
        let (store, step) = MODS.get(py).ok_or_else(|| PyRuntimeError::new_err("hindsight_native: packer_setup was not called"))?;
        let (store, step_parts) = (store.bind(py), step.bind(py).getattr("step_parts")?.unbind());
        let f = |k: &str| store.getattr(k)?.extract::<f64>();
        let (quiet, idle, user_idle, duty) = (f("QUIET_S")?, f("IDLE_S")?, f("USER_IDLE_S")?, f("DUTY")?);
        let strong_max: u64 = store.getattr("STRONG_MAX_BYTES")?.extract()?;
        let t = Times { quiet, idle, user_idle, duty };
        let sh = Arc::new(Shared {
            st: Mutex::new(St {
                work: VecDeque::new(),
                pending: 0,
                hurry: 0,
                stopped: false,
                alive: true,
                last_submit: 0.0,
                last_activity: f64::NEG_INFINITY,
                offers: false,
                want: false,
                staged: None,
                done: Vec::new(),
                tried: 0,
                gen: 0,
            }),
            cv: Condvar::new(),
        });
        let s2 = sh.clone();
        let th = thread::Builder::new()
            .name("HindsightPacker".into())
            .spawn(move || run(s2, t))
            .map_err(|e| PyRuntimeError::new_err(format!("hindsight_native: no packer thread ({e})")))?;
        Ok(Packer {
            sh,
            m: Mutex::new(Main {
                packs: VecDeque::new(),
                inflight: HashSet::new(),
                strong: VecDeque::new(),
                staged: None,
                packed: 0,
                recompressed: 0,
                errors: 0,
                last_error: None,
            }),
            thread: Mutex::new(Some(th)),
            recompress: recompress_idle,
            user_idle,
            strong_max,
            step_parts,
            wref: py.import_bound("weakref")?.getattr("ref")?.unbind(),
        })
    }

    /// Offer packed entries (for example after loading a file) for idle-time recompression.
    fn queue_recompress(&self, py: Python<'_>, entries: &Bound<'_, PyAny>) -> PyResult<()> {
        if !self.recompress {
            return Ok(());
        }
        {
            let mut m = self.main();
            for e in entries.iter()? {
                let e = e?;
                let b = e.getattr(intern!(py, "_bc"))?.get_item(0)?; // a Slice is read by index, not copied (#125)
                if !b.is_none() && store_can_gain(py, &b)? {
                    m.strong.push_back(self.wref.call1(py, (&e,))?);
                }
            }
        }
        self.take_in(py);
        Ok(())
    }

    /// The user is working (a step was recorded, a jump made): recompression waits until USER_IDLE_S has passed
    /// without another call. Packing new steps is not held back.
    fn note_activity(&self) {
        self.sh.lock().last_activity = now();
    }

    fn submit(&self, py: Python<'_>, entry: &Bound<'_, PyAny>, codec: &str) -> PyResult<()> {
        self.take_in(py);
        let step = entry.getattr(intern!(py, "step"))?;
        if step.is_none() {
            return Ok(());
        }
        {
            let g = self.sh.lock();
            if g.stopped || !g.alive {
                drop(g);
                entry.call_method1(intern!(py, "pack"), (codec,))?;
                return Ok(());
            }
        }
        if !self.main().inflight.insert(entry.as_ptr() as usize) {
            return Ok(());
        }
        // the JSON now, on the main thread: the thread gets no Python values (owner point 1)
        let parts = self.step_parts.call1(py, (&step,)).and_then(|p| step::take_parts(p.bind(py), codec, false));
        let (work, err) = match parts {
            Ok(enc) => (Work::Pack(Pin::new(enc)), None),
            Err(e) => (Work::Failed, Some(e)), // as the worker did: counted, the entry stays raw
        };
        self.main().packs.push_back((entry.clone().unbind(), step.unbind(), err));
        let mut g = self.sh.lock();
        g.last_submit = now();
        g.work.push_back(work);
        g.pending += 1;
        self.sh.poke(g);
        Ok(())
    }

    fn pending(&self, py: Python<'_>) -> usize {
        let n = self.sh.lock().pending;
        self.take_in(py);
        n
    }

    /// Wait (the GIL released) until every step handed over is packed, then take them in.
    #[pyo3(signature = (timeout=None))]
    fn drain(&self, py: Python<'_>, timeout: Option<f64>) {
        {
            let mut g = self.sh.lock();
            g.hurry += 1;
            self.sh.poke(g);
        }
        let sh = &*self.sh;
        py.allow_threads(|| {
            let g = sh.lock();
            let mut g = match timeout {
                None => sh.cv.wait_while(g, |s| s.pending > 0).unwrap_or_else(|e| e.into_inner()),
                Some(t) => sh.cv.wait_timeout_while(g, secs(t), |s| s.pending > 0).unwrap_or_else(|e| e.into_inner()).0,
            };
            g.hurry -= 1;
        });
        self.take_in(py);
    }

    /// Stop the thread once the steps handed over are packed (at most 10 s, the GIL released); later steps are
    /// packed right away on the main thread.
    fn stop(&self, py: Python<'_>) {
        {
            let mut g = self.sh.lock();
            if g.stopped {
                return;
            }
            g.stopped = true;
            self.sh.poke(g);
        }
        let sh = &*self.sh;
        let gone = py.allow_threads(|| {
            let g = sh.lock();
            !sh.cv.wait_timeout_while(g, Duration::from_secs(10), |s| s.alive).unwrap_or_else(|e| e.into_inner()).0.alive
        });
        if gone {
            if let Some(t) = self.thread.lock().unwrap_or_else(|e| e.into_inner()).take() {
                let _ = py.allow_threads(|| t.join());
            }
        }
        self.take_in(py);
    }

    /// Take the thread's results in now (Blender's tick calls this, so a packed step replaces its raw one soon).
    #[pyo3(name = "take_in")]
    fn take_in_py(&self, py: Python<'_>) {
        self.take_in(py);
    }

    #[getter]
    fn packed(&self, py: Python<'_>) -> u64 {
        self.take_in(py);
        self.main().packed
    }

    #[getter]
    fn recompressed(&self, py: Python<'_>) -> u64 {
        self.take_in(py);
        self.main().recompressed
    }

    #[getter]
    fn errors(&self, py: Python<'_>) -> u64 {
        self.take_in(py);
        self.main().errors
    }

    #[getter]
    fn last_error(&self, py: Python<'_>) -> Option<String> {
        self.take_in(py);
        self.main().last_error.clone()
    }

    /// Weak refs to the entries still to recompress (the one handed over and not started first).
    #[getter]
    fn _strong<'py>(&self, py: Python<'py>) -> Bound<'py, PyList> {
        self.take_in(py);
        let started = self.sh.lock().staged.is_none();
        let m = self.main();
        let mut out = Vec::new();
        if let (false, Some((e, _))) = (started, &m.staged) {
            out.push(self.wref.call1(py, (e,)).unwrap_or_else(|_| py.None()));
        }
        out.extend(m.strong.iter().map(|w| w.clone_ref(py)));
        PyList::new_bound(py, out)
    }

    #[getter]
    fn _alive(&self) -> bool {
        self.sh.lock().alive
    }

    #[getter]
    fn _last_activity(&self) -> f64 {
        self.sh.lock().last_activity
    }

    #[getter]
    fn _tried(&self) -> u64 {
        self.sh.lock().tried
    }

    fn _user_idle_for(&self) -> f64 {
        now() - self.sh.lock().last_activity
    }

    /// One turn of idle recompression on the calling thread (tests): the next offer that may gain, recompressed.
    fn _recompress_one(&self, py: Python<'_>) {
        self.take_in(py);
        let mut m = self.main();
        if self.sh.lock().stopped {
            return;
        }
        let Some((e, bc, blob)) = self.next_offer(py, &mut m) else { return };
        let Ok(buf) = Buf::new(&blob, false) else { return };
        self.sh.lock().tried += 1;
        let b = buf.bytes();
        let r = py.allow_threads(|| catch_unwind(AssertUnwindSafe(|| recompress_core(b))));
        let r = r.map_err(|_| None).and_then(|r| r.map_err(Some));
        drop(buf);
        self.take_strong(py, &mut m, &e, &bc, r);
    }
}

impl Drop for Packer {
    fn drop(&mut self) {
        // the thread finishes what it was handed and ends; what it pinned then is leaked, never released without the GIL
        let mut g = self.sh.lock();
        g.stopped = true;
        self.sh.poke(g);
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Packer>()?;
    m.add_function(wrap_pyfunction!(packer_hook, m)?)?;
    m.add_function(wrap_pyfunction!(packer_setup, m)?)?;
    Ok(())
}
