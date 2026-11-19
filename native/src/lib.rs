//! hindsight_native: the number work of hindsight/core/delta.py, bit for bit.
//!
//! Every function here is a twin of the Python code it replaces and must return exactly the same
//! bytes. Python keeps the header parsing, the argument checks and the error types; these functions
//! get raw byte buffers (anything with the buffer protocol, contiguous) and:
//!   diff    changed elements and their wrapping differences new - old
//!   apply   a delta applied in place, forwards or backwards (ADD) or as XOR
//!   pack    index coding, prediction, zigzag and byte planes of a format 3 record
//!   unpack  the reverse, with every length and index from the data checked
//!
//! Values are little-endian (numpy's byte view on every platform Blender supports). Nothing panics
//! on bad data: every slice is bounds-checked, and a panic (a bug) is still caught and turned into a
//! RuntimeError instead of crossing into Python.

#![allow(unexpected_cfgs)]

#[cfg(target_endian = "big")]
compile_error!("hindsight_native assumes a little-endian target");

use std::mem::MaybeUninit;
use std::panic::{catch_unwind, AssertUnwindSafe};

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;


use lz4_sys as _; // links the C library the codec module calls

pyo3::create_exception!(hindsight_native, CorruptStep, PyValueError, "A step blob is damaged or not a step at all.");
pyo3::create_exception!(hindsight_native, CorruptBlob, PyValueError, "Compressed data is damaged.");
pyo3::create_exception!(hindsight_native, CorruptDelta, PyValueError, "A packed delta is damaged or not a delta at all.");
pyo3::create_exception!(hindsight_native, NewerFormatError, PyValueError, "A file format newer than this build reads.");
use pyo3::types::{PyByteArray, PyBytes, PyDict, PyList, PyString};

/// Bumped when a function's meaning changes; hindsight/core/native.py refuses a different one.
const API: u32 = 4;

const TAG_RAW: u8 = 255;
const MAX_ORDER: u8 = 3;
const ORDER_GAIN: f64 = 0.97;
const IDX_GAPS: u8 = 0;
const IDX_RUNS: u8 = 1;
const IDX_BITMAP: u8 = 2;

const E_CUT: &str = "delta: index data is cut short";
const E_GAP_WIDTH: &str = "delta: bad gap width";
const E_RANGE: &str = "delta: index out of range";
const E_RUN_HDR: &str = "delta: bad run header";
const E_RUN: &str = "delta: bad run";
const E_RUN_SUM: &str = "delta: run lengths don't add up";
const E_BITMAP: &str = "delta: bitmap doesn't match the element count";
const E_CODING: &str = "delta: unknown index coding";
const E_TAG: &str = "delta: bad predictor tag";

// ----------------------------------------------------------------------------------- buffers
/// A contiguous buffer borrowed from a Python object for the duration of one call (GIL held).
struct Buf {
    view: Box<ffi::Py_buffer>,
}

impl Buf {
    fn new(obj: &Bound<'_, PyAny>, writable: bool) -> PyResult<Self> {
        let mut raw: Box<MaybeUninit<ffi::Py_buffer>> = Box::new(MaybeUninit::uninit());
        let flags = if writable { ffi::PyBUF_WRITABLE } else { ffi::PyBUF_SIMPLE };
        // SAFETY: `obj` is alive and the GIL is held (it is a Bound); `raw` is storage for one
        // Py_buffer. PyBUF_SIMPLE asks for a contiguous buffer of bytes, or fails.
        let rc = unsafe { ffi::PyObject_GetBuffer(obj.as_ptr(), raw.as_mut_ptr(), flags) };
        if rc != 0 {
            return Err(PyErr::fetch(obj.py()));
        }
        // SAFETY: PyObject_GetBuffer succeeded, so it filled the struct.
        let view = unsafe { raw.assume_init() };
        let b = Buf { view };
        if b.view.len < 0 || (b.view.len > 0 && b.view.buf.is_null()) {
            return Err(PyValueError::new_err("hindsight_native: bad buffer"));
        }
        Ok(b)
    }

    fn len(&self) -> usize {
        self.view.len as usize
    }

    fn bytes(&self) -> &[u8] {
        if self.len() == 0 {
            return &[];
        }
        // SAFETY: the exporter guarantees `len` readable bytes at `buf` while the view is held.
        unsafe { std::slice::from_raw_parts(self.view.buf as *const u8, self.len()) }
    }

    fn bytes_mut(&mut self) -> &mut [u8] {
        if self.len() == 0 {
            return &mut [];
        }
        // SAFETY: requested with PyBUF_WRITABLE; callers check it doesn't overlap other views.
        unsafe { std::slice::from_raw_parts_mut(self.view.buf as *mut u8, self.len()) }
    }

    fn overlaps(&self, other: &Buf) -> bool {
        let (a0, b0) = (self.view.buf as usize, other.view.buf as usize);
        self.len() > 0 && other.len() > 0 && a0 < b0 + other.len() && b0 < a0 + self.len()
    }
}

impl Drop for Buf {
    fn drop(&mut self) {
        // SAFETY: obtained by PyObject_GetBuffer and released once; the GIL is held (Bufs never
        // outlive the call that made them).
        unsafe { ffi::PyBuffer_Release(&mut *self.view) }
    }
}

/// Runs `f`, turning a panic (a bug, never expected) into a RuntimeError.
fn guard<T>(f: impl FnOnce() -> PyResult<T>) -> PyResult<T> {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(r) => r,
        Err(_) => Err(PyRuntimeError::new_err("hindsight_native: internal error")),
    }
}

fn bad(msg: &str) -> PyErr {
    PyValueError::new_err(msg.to_string())
}

fn check_size(isz: usize, w: usize) -> PyResult<usize> {
    if !matches!(isz, 1 | 2 | 4 | 8) || w == 0 {
        return Err(bad("hindsight_native: item size must be 1, 2, 4 or 8 and width at least 1"));
    }
    isz.checked_mul(w).ok_or_else(|| bad("hindsight_native: width too large"))
}

// ------------------------------------------------------------------------------ elements
#[inline(always)]
fn rd<const N: usize>(s: &[u8], i: usize) -> u64 {
    let mut a = [0u8; 8];
    a[..N].copy_from_slice(&s[i * N..i * N + N]);
    u64::from_le_bytes(a)
}

#[inline(always)]
fn wr<const N: usize>(s: &mut [u8], i: usize, v: u64) {
    s[i * N..i * N + N].copy_from_slice(&v.to_le_bytes()[..N]);
}

#[inline(always)]
const fn mask<const N: usize>() -> u64 {
    if N == 8 {
        u64::MAX
    } else {
        (1u64 << (8 * N)) - 1
    }
}

#[inline(always)]
fn zigzag<const N: usize>(x: u64) -> u64 {
    let sign = if (x >> (8 * N - 1)) & 1 == 1 { mask::<N>() } else { 0 };
    ((x << 1) & mask::<N>()) ^ sign
}

#[inline(always)]
fn unzigzag<const N: usize>(z: u64) -> u64 {
    ((z >> 1) ^ 0u64.wrapping_sub(z & 1)) & mask::<N>()
}

#[inline(always)]
fn idx_at(iv: &[u8], j: usize) -> u32 {
    u32::from_le_bytes([iv[4 * j], iv[4 * j + 1], iv[4 * j + 2], iv[4 * j + 3]])
}

macro_rules! by_size {
    ($isz:expr, $f:ident ( $($a:expr),* )) => {
        match $isz {
            1 => $f::<1>($($a),*),
            2 => $f::<2>($($a),*),
            4 => $f::<4>($($a),*),
            _ => $f::<8>($($a),*),
        }
    };
}

mod codec;
mod step;

// ------------------------------------------------------------------------------------ diff
fn sub_rows<const N: usize>(a: &[u8], b: &[u8], idx: &[u32], per: usize, o: &mut [u8]) {
    for (j, &i) in idx.iter().enumerate() {
        let (src, dst) = (i as usize * per, j * per);
        for c in 0..per {
            let v = rd::<N>(b, src + c).wrapping_sub(rd::<N>(a, src + c)) & mask::<N>();
            wr::<N>(o, dst + c, v);
        }
    }
}

/// Indices of the rows (of `w` values of `isz` bytes) whose bits differ, and new - old of those
/// rows (wrapping), as delta.diff; None if nothing differs. Sizes must match (the caller checks).
fn diff_core(a: &[u8], b: &[u8], isz: usize, w: usize) -> Option<(Vec<u32>, Vec<u8>)> {
    let row = isz * w;
    const ROWS: usize = 32; // compare blocks of rows first: most of a mesh is unchanged
    let blk = row * ROWS;
    let mut idx: Vec<u32> = Vec::new();
    for (bi, (ca, cb)) in a.chunks(blk).zip(b.chunks(blk)).enumerate() {
        if ca != cb {
            for (j, (ra, rb)) in ca.chunks_exact(row).zip(cb.chunks_exact(row)).enumerate() {
                if ra != rb {
                    idx.push((bi * ROWS + j) as u32); // as numpy's astype(uint32)
                }
            }
        }
    }
    if idx.is_empty() {
        return None;
    }
    let mut v = vec![0u8; idx.len() * row];
    by_size!(isz, sub_rows(a, b, &idx, w, &mut v));
    Some((idx, v))
}

/// diff(old, new, itemsize, width) -> None if no element changed, else (indices as uint32 bytes,
/// new - old of the changed elements, wrapping). As delta.diff: indices of the elements (rows of
/// `width` values) whose bits differ.
#[pyfunction]
fn diff<'py>(
    py: Python<'py>,
    old: &Bound<'py, PyAny>,
    new: &Bound<'py, PyAny>,
    isz: usize,
    w: usize,
) -> PyResult<Option<(Bound<'py, PyByteArray>, Bound<'py, PyByteArray>)>> {
    guard(|| {
        let row = check_size(isz, w)?;
        let (ob, nb) = (Buf::new(old, false)?, Buf::new(new, false)?);
        let (a, b) = (ob.bytes(), nb.bytes());
        if a.len() != b.len() || a.len() % row != 0 {
            return Err(bad("hindsight_native.diff: sizes don't match"));
        }
        let (idx, vals) = match diff_core(a, b, isz, w) {
            Some(r) => r,
            None => return Ok(None),
        };
        let k = idx.len();
        let ib = PyByteArray::new_bound_with(py, k * 4, |o| {
            for (j, &i) in idx.iter().enumerate() {
                o[4 * j..4 * j + 4].copy_from_slice(&i.to_le_bytes());
            }
            Ok(())
        })?;
        let vb = PyByteArray::new_bound_with(py, k * row, |o| {
            o.copy_from_slice(&vals);
            Ok(())
        })?;
        Ok(Some((ib, vb)))
    })
}

// ----------------------------------------------------------------------------------- apply
/// OP 0: x ^ d, 1: x + d, 2: x - d (wrapping), on each value of the rows named by the indices.
fn apply_op<const N: usize, const OP: u8>(dst: &mut [u8], iv: &[u8], v: &[u8], w: usize) {
    let row = N * w;
    for (j, vr) in v.chunks_exact(row).enumerate() {
        let at = idx_at(iv, j) as usize * row;
        for (dc, vc) in dst[at..at + row].chunks_exact_mut(N).zip(vr.chunks_exact(N)) {
            let (x, d) = (rd::<N>(dc, 0), rd::<N>(vc, 0));
            let y = match OP {
                0 => x ^ d,
                1 => x.wrapping_add(d),
                _ => x.wrapping_sub(d),
            };
            wr::<N>(dc, 0, y & mask::<N>());
        }
    }
}

fn apply_rows<const N: usize>(dst: &mut [u8], iv: &[u8], v: &[u8], w: usize, xor: bool, forward: bool) {
    match (xor, forward) {
        (true, _) => apply_op::<N, 0>(dst, iv, v, w),
        (false, true) => apply_op::<N, 1>(dst, iv, v, w),
        (false, false) => apply_op::<N, 2>(dst, iv, v, w),
    }
}

/// apply_op on buffers aligned to their types (numpy's are): no byte shuffling per value.
fn apply_t<L: Lane, const OP: u8, const W: usize>(dst: &mut [L], idx: &[u32], v: &[L], w: usize) {
    let w = if W > 0 { W } else { w };
    for (&i, vr) in idx.iter().zip(v.chunks_exact(w)) {
        let at = i as usize * w;
        for (x, &d) in dst[at..at + w].iter_mut().zip(vr) {
            *x = match OP {
                0 => x.xor(d),
                1 => x.wadd(d),
                _ => x.wsub(d),
            };
        }
    }
}

/// The typed apply_op, when every buffer is aligned; false (nothing done) otherwise.
fn apply_lane<L: Lane>(dst: &mut [u8], iv: &[u8], v: &[u8], w: usize, op: u8) -> bool {
    // SAFETY: any bit pattern is a valid unsigned integer; align_to only hands out the aligned
    // middle part, and each whole buffer must be that part.
    let (p0, d, q0) = unsafe { dst.align_to_mut::<L>() };
    let (p1, ix, q1) = unsafe { iv.align_to::<u32>() };
    let (p2, vv, q2) = unsafe { v.align_to::<L>() };
    if !(p0.is_empty() && q0.is_empty() && p1.is_empty() && q1.is_empty() && p2.is_empty() && q2.is_empty()) {
        return false;
    }
    macro_rules! by_w {
        ($op:expr) => {
            match w {
                1 => apply_t::<L, $op, 1>(d, ix, vv, w),
                2 => apply_t::<L, $op, 2>(d, ix, vv, w),
                3 => apply_t::<L, $op, 3>(d, ix, vv, w),
                4 => apply_t::<L, $op, 4>(d, ix, vv, w),
                _ => apply_t::<L, $op, 0>(d, ix, vv, w),
            }
        };
    }
    match op {
        0 => by_w!(0),
        1 => by_w!(1),
        _ => by_w!(2),
    }
    true
}

/// apply(array, indices, values, itemsize, width, xor, forward) -> True when applied in place,
/// False (array untouched) for anything the fast path doesn't cover: indices not sorted and
/// unique, past the end, a value count that doesn't match, overlapping buffers. The caller then
/// runs the Python code, which keeps its exact behaviour for those cases.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn apply(
    arr: &Bound<'_, PyAny>,
    idx: &Bound<'_, PyAny>,
    vals: &Bound<'_, PyAny>,
    isz: usize,
    w: usize,
    xor: bool,
    forward: bool,
) -> PyResult<bool> {
    guard(|| {
        let row = check_size(isz, w)?;
        let mut ab = match Buf::new(arr, true) {
            Ok(b) => b,
            Err(_) => return Ok(false), // read-only or not contiguous: Python decides
        };
        let (ib, vb) = (Buf::new(idx, false)?, Buf::new(vals, false)?);
        if ab.overlaps(&ib) || ab.overlaps(&vb) {
            return Ok(false);
        }
        let iv = ib.bytes();
        let k = iv.len() / 4;
        if iv.len() % 4 != 0 || k.checked_mul(row) != Some(vb.len()) || ab.len() % row != 0 {
            return Ok(false);
        }
        let n = ab.len() / row;
        let mut prev: i64 = -1;
        for j in 0..k {
            let i = idx_at(iv, j) as i64;
            if i <= prev || i as usize >= n {
                return Ok(false);
            }
            prev = i;
        }
        let v = vb.bytes();
        let dst = ab.bytes_mut();
        let op = if xor { 0 } else if forward { 1 } else { 2 };
        let typed = match isz {
            1 => apply_lane::<u8>(dst, iv, v, w, op),
            2 => apply_lane::<u16>(dst, iv, v, w, op),
            4 => apply_lane::<u32>(dst, iv, v, w, op),
            _ => apply_lane::<u64>(dst, iv, v, w, op),
        };
        if !typed {
            by_size!(isz, apply_rows(dst, iv, v, w, xor, forward));
        }
        Ok(true)
    })
}

// ------------------------------------------------------------------------------------ pack
fn width_code(m: u64) -> usize {
    if m <= 0xFF {
        1
    } else if m <= 0xFFFF {
        2
    } else {
        4
    }
}

/// Gaps between the sorted indices, minus one, as N-byte values.
fn put_gaps<const N: usize>(iv: &[u8], out: &mut [u8]) {
    let k = iv.len() / 4;
    for j in 1..k {
        wr::<N>(out, j - 1, (idx_at(iv, j) - idx_at(iv, j - 1) - 1) as u64);
    }
}

/// idx[0] = first, idx[j] = idx[j - 1] + gap + 1 (wrapping, as numpy's uint32 sums).
fn get_gaps<const N: usize>(g: &[u8], first: u32, out: &mut [u8]) {
    let mut cur = first;
    let Some((o0, rest)) = out.split_first_chunk_mut::<4>() else { return };
    *o0 = cur.to_le_bytes();
    for (o, gb) in rest.chunks_exact_mut(4).zip(g.chunks_exact(N)) {
        let mut a = [0u8; 8];
        a[..N].copy_from_slice(gb);
        cur = cur.wrapping_add(u64::from_le_bytes(a) as u32).wrapping_add(1);
        o.copy_from_slice(&cur.to_le_bytes());
    }
}

fn push_le(out: &mut Vec<u8>, v: u64, n: usize) {
    out.extend_from_slice(&v.to_le_bytes()[..n]);
}

#[inline(always)]
fn exp_bits(x: u64) -> i64 {
    // (value as float32) bits >> 23: the biased exponent (values are never negative)
    ((x as f32).to_bits() >> 23) as i64
}

/// The value differenced 0 to 3 times along the record (wrapping); `p` holds each order's value
/// at the element before (the first element is kept as it is at every order).
#[inline(always)]
fn orders<const N: usize>(x: u64, first: bool, p: &mut [u64; 3]) -> [u64; 4] {
    let mut z = [x; 4];
    if !first {
        for o in 1..4 {
            z[o] = z[o - 1].wrapping_sub(p[o - 1]) & mask::<N>();
        }
    }
    p.copy_from_slice(&z[..3]);
    z
}

#[inline(always)]
fn put_plane_bytes<const N: usize>(planes: &mut [u8], c: usize, k: usize, i: usize, x: u64) {
    for b in 0..N {
        planes[(c * N + b) * k + i] = (x >> (8 * b)) as u8;
    }
}

fn pack_vals<const N: usize>(v: &[u8], k: usize, w: usize, predict: bool, o: &mut [u8]) {
    let (tags, planes) = o.split_at_mut(w);
    if !predict || k < 2 {
        for c in 0..w {
            tags[c] = if predict { 0 } else { TAG_RAW };     // one element: order 0, zigzag only
            for i in 0..k {
                let x = rd::<N>(v, i * w + c);
                put_plane_bytes::<N>(planes, c, k, i, if predict { zigzag::<N>(x) } else { x });
            }
        }
        return;
    }
    // per component: the values differenced 0 to 3 times, zigzagged; the order with the fewest
    // estimated bits wins, a higher one only for a clear gain (as delta.pack_delta). Two passes
    // over the values (estimate, then write the chosen order) instead of a copy of every order.
    let n_ord = (MAX_ORDER as usize).min(k - 1) + 1;
    for c in 0..w {
        let mut est = [0i64; 4];
        let mut p = [0u64; 3];
        for i in 0..k {
            let z = orders::<N>(rd::<N>(v, i * w + c), i == 0, &mut p);
            for ord in 0..n_ord {
                let zz = zigzag::<N>(z[ord]);
                est[ord] += exp_bits(zz) - 126 * (zz != 0) as i64;
            }
        }
        let mut best = 0usize;
        for ord in 1..n_ord {
            if (est[ord] as f64) < (est[best] as f64) * ORDER_GAIN {
                best = ord;
            }
        }
        tags[c] = best as u8;
        let mut p = [0u64; 3];
        for i in 0..k {
            let z = orders::<N>(rd::<N>(v, i * w + c), i == 0, &mut p);
            put_plane_bytes::<N>(planes, c, k, i, zigzag::<N>(z[best]));
        }
    }
}

/// Index coding of a record (k >= 1): None if the indices aren't sorted and unique, else
/// (coding, bytes): one run when contiguous, else first index and gaps.
fn pack_index(iv: &[u8], k: usize) -> Option<(u8, Vec<u8>)> {
    let mut gmax: u64 = 0;
    for j in 1..k {
        let (a, b) = (idx_at(iv, j - 1), idx_at(iv, j));
        if b <= a {
            return None;
        }
        gmax = gmax.max((b - a - 1) as u64);
    }
    let first = idx_at(iv, 0) as u64;
    let last = idx_at(iv, k - 1) as u64;
    let mut ibytes = Vec::new();
    let code = if k > 1 && last - first + 1 == k as u64 {
        let lw = width_code(k as u64 - 1);
        ibytes.push((1 | (lw << 4)) as u8);
        push_le(&mut ibytes, 1, 4);
        push_le(&mut ibytes, first, 4);
        push_le(&mut ibytes, k as u64 - 1, lw);
        IDX_RUNS
    } else {
        let wc = width_code(gmax);
        ibytes.reserve(5 + (k - 1) * wc);
        ibytes.push(wc as u8);
        push_le(&mut ibytes, first, 4);
        let at = ibytes.len();
        ibytes.resize(at + (k - 1) * wc, 0);
        by_size!(wc, put_gaps(iv, &mut ibytes[at..]));
        IDX_GAPS
    };
    Some((code, ibytes))
}

/// pack(indices, values, itemsize, width, predict) -> None if the indices aren't sorted and
/// unique, else (index coding, index bytes, predictor tags + byte planes): the parts of a format 3
/// record after its header and key, as delta.pack_delta writes them (k >= 1).
#[pyfunction]
fn pack<'py>(
    py: Python<'py>,
    idx: &Bound<'py, PyAny>,
    vals: &Bound<'py, PyAny>,
    isz: usize,
    w: usize,
    predict: bool,
) -> PyResult<Option<(u8, Bound<'py, PyBytes>, Bound<'py, PyBytes>)>> {
    guard(|| {
        let row = check_size(isz, w)?;
        let (ib, vb) = (Buf::new(idx, false)?, Buf::new(vals, false)?);
        let iv = ib.bytes();
        let k = iv.len() / 4;
        if iv.len() % 4 != 0 || k == 0 || k.checked_mul(row) != Some(vb.len()) {
            return Err(bad("hindsight_native.pack: sizes don't match"));
        }
        let (code, ibytes) = match pack_index(iv, k) {
            Some(r) => r,
            None => return Ok(None),
        };
        let v = vb.bytes();
        let tv = PyBytes::new_bound_with(py, w + k * row, |o| {
            by_size!(isz, pack_vals(v, k, w, predict, o));
            Ok(())
        })?;
        Ok(Some((code, PyBytes::new_bound(py, &ibytes), tv)))
    })
}

// ---------------------------------------------------------------------------------- unpack
/// `n` bytes at `off` of the index data, or "cut short" (sizes in u128: they come from the data).
fn take(iv: &[u8], off: u128, n: u128) -> Result<&[u8], &'static str> {
    if off + n > iv.len() as u128 {
        return Err(E_CUT);
    }
    Ok(&iv[off as usize..(off + n) as usize])
}

fn le(b: &[u8], i: usize, n: usize) -> u64 {
    let mut a = [0u8; 8];
    a[..n].copy_from_slice(&b[i * n..i * n + n]);
    u64::from_le_bytes(a)
}

fn put_idx(out: &mut [u8], j: usize, v: u32) {
    out[4 * j..4 * j + 4].copy_from_slice(&v.to_le_bytes());
}

const LIM: u128 = 0xFFFF_FFFF;

fn decode_idx(code: u8, iv: &[u8], k: usize, out: &mut [u8]) -> Result<(), &'static str> {
    let k1 = k as u128;
    match code {
        IDX_GAPS => {
            let h = take(iv, 0, 5)?;
            let wc = h[0] as usize;
            let first = le(&h[1..5], 0, 4);
            if !matches!(wc, 1 | 2 | 4) {
                return Err(E_GAP_WIDTH);
            }
            let g = take(iv, 5, (k1 - 1) * wc as u128)?;
            if first as u128 + (k1 - 1) * (1u128 << (8 * wc)) > LIM {
                let s: u128 = (0..k - 1).map(|j| le(g, j, wc) as u128).sum();
                if first as u128 + s + k1 - 1 > LIM {
                    return Err(E_RANGE);
                }
            }
            by_size!(wc, get_gaps(g, first as u32, out));
            Ok(())
        }
        IDX_RUNS => {
            let h = take(iv, 0, 9)?;
            let widths = h[0] as usize;
            let nr = le(&h[1..5], 0, 4) as usize;
            let first = le(&h[5..9], 0, 4) as u128;
            let (gw, lw) = (widths & 15, widths >> 4);
            let ok = |x: usize| matches!(x, 1 | 2 | 4);
            if !ok(gw) || !ok(lw) || nr < 1 || nr > k {
                return Err(E_RUN_HDR);
            }
            let nr1 = nr as u128;
            if nr == 1 {
                let b = take(iv, 9, lw as u128)?;
                let ln1 = le(b, 0, lw) as u128;
                if ln1 + 1 != k1 || first + k1 - 1 > LIM {
                    return Err(E_RUN);
                }
                for j in 0..k {
                    put_idx(out, j, (first + j as u128) as u32);
                }
                return Ok(());
            }
            let gaps = take(iv, 9, (nr1 - 1) * gw as u128)?;
            let lens = take(iv, 9 + (nr1 - 1) * gw as u128, nr1 * lw as u128)?;
            let total: u128 = (0..nr).map(|j| le(lens, j, lw) as u128 + 1).sum();
            if total != k1 {
                return Err(E_RUN_SUM);
            }
            let last_off: u128 = first + (0..nr - 1).map(|j| le(gaps, j, gw) as u128 + 1).sum::<u128>();
            if last_off + k1 - 1 > LIM {
                return Err(E_RANGE);
            }
            let (mut pos, mut offs) = (0usize, first);
            for j in 0..nr {
                if j > 0 {
                    offs += le(gaps, j - 1, gw) as u128 + 1;
                }
                for _ in 0..le(lens, j, lw) + 1 {
                    put_idx(out, pos, (offs + pos as u128) as u32);
                    pos += 1;
                }
            }
            Ok(())
        }
        IDX_BITMAP => {
            let h = take(iv, 0, 8)?;
            let lo = le(h, 0, 4) as u128;
            let span = le(&h[4..8], 0, 4) as usize;
            let bits = take(iv, 8, (span as u128).div_ceil(8))?;
            let bit = |p: usize| (bits[p >> 3] >> (p & 7)) & 1 == 1;
            let full = span / 8;
            let mut count: usize = bits[..full].iter().map(|b| b.count_ones() as usize).sum();
            count += (full * 8..span).filter(|&p| bit(p)).count();
            if count != k {
                return Err(E_BITMAP);
            }
            let last = (0..span).rev().find(|&p| bit(p)).unwrap_or(0) as u128;
            if k > 0 && lo + last > LIM {
                return Err(E_RANGE);
            }
            let mut j = 0;
            for p in 0..span {
                if bit(p) {
                    put_idx(out, j, (lo + p as u128) as u32);
                    j += 1;
                }
            }
            Ok(())
        }
        _ => Err(E_CODING),
    }
}

/// Elements decoded at a time (a small buffer on the stack instead of a whole column).
const CHUNK: usize = 1024;

/// Undo the zigzag, then T running sums in one pass (the sums of the sums of ... the values),
/// carrying the sums from the chunk before in `acc`.
fn undo<const N: usize, const T: usize>(col: &mut [u64], acc: &mut [u64; 3]) {
    for x in col.iter_mut() {
        let mut y = unzigzag::<N>(*x);
        for a in acc[..T].iter_mut() {
            *a = a.wrapping_add(y) & mask::<N>();
            y = *a;
        }
        *x = y;
    }
}

/// The predictor tags of a record are valid (as delta.unpack_at checks them).
fn check_tags(tags: &[u8], add: bool) -> Result<(), &'static str> {
    let w = tags.len();
    let hi = *tags.iter().max().unwrap_or(&0);
    let n_raw = tags.iter().filter(|&&t| t == TAG_RAW).count();
    if hi <= MAX_ORDER {
        if !add {
            return Err(E_TAG);
        }
    } else if n_raw != w
        && (!add || hi != TAG_RAW || tags.iter().any(|&t| t > MAX_ORDER && t != TAG_RAW))
    {
        return Err(E_TAG);
    }
    Ok(())
}

fn decode_vals<const N: usize>(vin: &[u8], k: usize, w: usize, add: bool, out: &mut [u8]) -> Result<(), &'static str> {
    check_tags(&vin[..w], add)?;
    decode_cols::<N>(vin, k, w, |e, c, x| wr::<N>(out, e * w + c, x));
    Ok(())
}

/// The values of a record (tags checked), column by column: `sink(element, component, value)`.
#[inline(always)]
fn decode_cols<const N: usize>(vin: &[u8], k: usize, w: usize, mut sink: impl FnMut(usize, usize, u64)) {
    let (tags, planes) = vin.split_at(w);
    let mut buf = [0u64; CHUNK];
    for c in 0..w {
        let mut acc = [0u64; 3];
        let mut s = 0;
        while s < k {
            let n = CHUNK.min(k - s);
            let col = &mut buf[..n];
            col.fill(0);
            for b in 0..N {
                let at = (c * N + b) * k + s;
                for (x, &p) in col.iter_mut().zip(&planes[at..at + n]) {
                    *x |= (p as u64) << (8 * b);
                }
            }
            match tags[c] {
                0 => undo::<N, 0>(col, &mut acc),
                1 => undo::<N, 1>(col, &mut acc),
                2 => undo::<N, 2>(col, &mut acc),
                3 => undo::<N, 3>(col, &mut acc),
                _ => {} // stored as is (checked above)
            }
            for (i, &x) in col.iter().enumerate() {
                sink(s + i, c, x);
            }
            s += n;
        }
    }
}

/// unpack(index bytes, coding, k, tags + planes, itemsize, width, op_add, idx_out, vals_out):
/// decodes a format 3 record body (k >= 1) into the two given arrays (k uint32 and k * width
/// values). Damaged data raises ValueError with delta.py's CorruptDelta message.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn unpack(
    ibuf: &Bound<'_, PyAny>,
    code: u8,
    k: usize,
    vbuf: &Bound<'_, PyAny>,
    isz: usize,
    w: usize,
    add: bool,
    idx_out: &Bound<'_, PyAny>,
    vals_out: &Bound<'_, PyAny>,
) -> PyResult<()> {
    guard(|| {
        let row = check_size(isz, w)?;
        let (ib, vb) = (Buf::new(ibuf, false)?, Buf::new(vbuf, false)?);
        let (mut io, mut vo) = (Buf::new(idx_out, true)?, Buf::new(vals_out, true)?);
        let nx = k.checked_mul(row).ok_or_else(|| bad("hindsight_native.unpack: too large"))?;
        if k == 0
            || io.len() != k * 4
            || vo.len() != nx
            || vb.len() != w + nx
            || io.overlaps(&vo)
            || [&ib, &vb].iter().any(|b| io.overlaps(b) || vo.overlaps(b))
        {
            return Err(bad("hindsight_native.unpack: sizes don't match"));
        }
        decode_idx(code, ib.bytes(), k, io.bytes_mut()).map_err(bad)?;
        let v = vb.bytes();
        by_size!(isz, decode_vals(v, k, w, add, vo.bytes_mut())).map_err(bad)
    })
}

// ------------------------------------------------------------------------------ apply_steps
// A run of packed steps crossed in one call (history.History.goto). Only the plain case is taken:
// a format 3 DELTA step (no snapshots, data records or JSON extras), zstd or uncompressed, whose
// records are format 3, change channels that exist on both sides and are already in the state's
// arrays with the right item size. Anything else, and any damage at all, stops the run before
// that step with the arrays untouched by it; Python then crosses that step itself, so every
// exception and every unusual case keeps exactly its Python behaviour.

const STEP_MAGIC: &[u8; 4] = b"HSS3";
const REC_MAGIC: &[u8; 2] = b"HE";
const REC_HDR: usize = 16; // <2sBBBBHII
const STEP_FIXED: usize = 24; // <BBBBQdI
const QUICK_FLAG: u8 = 8;
const MAX_RAW: u64 = 1 << 30; // codec.MAX_RAW
const MAX_WIDTH: usize = 16;

/// One channel array of the state: a writable byte buffer and its numpy item size.
struct Arr {
    key: Vec<u8>,
    ptr: usize,
    len: usize,
    isz: usize,
}

/// One record of a decoded step, checked against its array and ready to apply.
struct Rec {
    arr: usize,
    isz: usize,
    w: usize,
    k: usize,
    vals: (usize, usize), // tags + planes, a range of the body
    idx: usize,           // where its k indices start in the step's index scratch
    xor: bool,
}

#[inline]
fn u16_at(b: &[u8], o: usize) -> Option<usize> {
    Some(u16::from_le_bytes(b.get(o..o + 2)?.try_into().ok()?) as usize)
}

#[inline]
fn u32_at(b: &[u8], o: usize) -> Option<usize> {
    Some(u32::from_le_bytes(b.get(o..o + 4)?.try_into().ok()?) as usize)
}

/// The step's header: Some((record count, raw body length, payload)) for a plain DELTA step.
fn step_header(blob: &[u8]) -> Option<(usize, usize, &[u8])> {
    if blob.len() < 10 || &blob[..4] != STEP_MAGIC {
        return None;
    }
    if crc32fast::hash(&blob[8..]) as usize != u32_at(blob, 4)? {
        return None;
    }
    let hl = u16_at(blob, 8)?;
    let h = blob.get(10..10 + hl)?;
    if hl < STEP_FIXED {
        return None;
    }
    // version 3 (4 has data records), kind DELTA, no flag but "written quickly"
    if h[0] != 3 || h[1] != 0 || h[2] & !QUICK_FLAG != 0 {
        return None;
    }
    let raw = u32_at(h, 20)?;
    let mut off = STEP_FIXED;
    let n = u16_at(h, off)?; // label
    std::str::from_utf8(h.get(off + 2..off + 2 + n)?).ok()?;
    off += 2 + n;
    for _ in 0..2 {
        // mode, target
        let n = *h.get(off)? as usize;
        std::str::from_utf8(h.get(off + 1..off + 1 + n)?).ok()?;
        off += 1 + n;
    }
    if u32_at(h, off)? != 0 {
        return None; // extra JSON: Python reads it
    }
    off += 4;
    let nd = u16_at(h, off)?;
    off += 2;
    if off != hl {
        return None;
    }
    Some((nd, raw, &blob[10 + hl..]))
}

/// Where the raw body of a step is, as codec.decompress(payload, raw) would return it: the
/// payload itself when stored as is (Some(true)), or one zstd frame of exactly `raw` bytes,
/// decompressed into `buf` (Some(false)).
fn step_body(payload: &[u8], raw: usize, dctx: &mut zstd_safe::DCtx<'static>, buf: &mut Vec<u8>) -> Option<bool> {
    let (&cid, data) = payload.split_first()?;
    let raw64 = raw as u64;
    if raw64 > MAX_RAW || raw64 > 65536 * data.len() as u64 + 65536 {
        return None;
    }
    match cid {
        0 => (data.len() == raw).then_some(true),
        3 | 4 => {
            if zstd_safe::get_frame_content_size(data).ok()?? != raw64 {
                return None;
            }
            if zstd_safe::find_frame_compressed_size(data).ok()? != data.len() {
                return None; // more than one frame, or trailing bytes: Python decides
            }
            buf.clear();
            buf.try_reserve_exact(raw).ok()?;
            let n = dctx.decompress(buf, data).ok()?;
            (n == raw && buf.len() == raw).then_some(false)
        }
        _ => None, // zlib, lz4: Python
    }
}

/// One step decoded and checked, ready to apply (buffers are reused from step to step).
#[derive(Default)]
struct Decoded {
    ok: bool,
    stored: bool, // the body is the blob's payload (stored as is), not `buf`
    buf: Vec<u8>,
    recs: Vec<Rec>,
    idx: Vec<u32>,
}

impl Decoded {
    fn body<'a>(&'a self, blob: &'a [u8]) -> &'a [u8] {
        if self.stored {
            let hl = u16::from_le_bytes([blob[8], blob[9]]) as usize;
            &blob[10 + hl + 1..]
        } else {
            &self.buf
        }
    }
}

/// Header, body and records of one step, all checked (d.ok false: not taken).
fn decode_step(blob: &[u8], arrs: &[Arr], dctx: &mut zstd_safe::DCtx<'static>, d: &mut Decoded) {
    d.ok = false;
    let Some((nd, raw, payload)) = step_header(blob) else { return };
    let Some(stored) = step_body(payload, raw, dctx, &mut d.buf) else { return };
    d.stored = stored;
    let body = if stored { &payload[1..] } else { &d.buf[..] };
    d.ok = step_records(body, nd, arrs, &mut d.recs, &mut d.idx).is_some();
}

/// Parses and checks every record of the body against the arrays; indices are decoded into
/// `idx`. None if any record isn't one this path takes, or is damaged.
fn step_records(body: &[u8], nd: usize, arrs: &[Arr], recs: &mut Vec<Rec>, idx: &mut Vec<u32>) -> Option<()> {
    recs.clear();
    idx.clear();
    let mut off = 0usize;
    for _ in 0..nd {
        let hdr = body.get(off..off + REC_HDR)?;
        if &hdr[..2] != REC_MAGIC {
            return None;
        }
        let (flags, isz, w, code) = (hdr[2], hdr[3] as usize, hdr[4] as usize, hdr[5]);
        let klen = u16_at(hdr, 6)?;
        let k = u32_at(hdr, 8)?;
        let ilen = u32_at(hdr, 12)?;
        // exists on both sides (the array stays); any other flag, size or width: Python
        if flags & !7 != 0 || flags & 3 != 3 || !matches!(isz, 1 | 2 | 4 | 8) || w == 0 || w > MAX_WIDTH {
            return None;
        }
        off += REC_HDR;
        let key = body.get(off..off + klen)?;
        std::str::from_utf8(key).ok()?;
        off += klen;
        let a = arrs.iter().position(|a| a.key == key)?;
        if k == 0 {
            recs.push(Rec { arr: a, isz, w, k: 0, vals: (0, 0), idx: 0, xor: false });
            continue;
        }
        let row = isz * w;
        let nx = (k as u128) * row as u128;
        let stop = off as u128 + ilen as u128 + w as u128 + nx;
        if stop > body.len() as u128 {
            return None;
        }
        let stop = stop as usize;
        let arr = &arrs[a];
        if arr.isz != isz || arr.len % row != 0 {
            return None;
        }
        let add = flags & 4 != 0;
        let vin = &body[off + ilen..stop];
        check_tags(&vin[..w], add).ok()?;
        let at = idx.len();
        idx.try_reserve(k).ok()?;
        idx.resize(at + k, 0);
        let out = &mut idx[at..];
        // SAFETY: the same memory as bytes (u8 has no alignment; the target is little-endian)
        let ob = unsafe { std::slice::from_raw_parts_mut(out.as_mut_ptr() as *mut u8, k * 4) };
        decode_idx(code, &body[off..off + ilen], k, ob).ok()?;
        // decoded indices always increase: the last one is the largest
        if out[k - 1] as usize >= arr.len / row {
            return None;
        }
        recs.push(Rec { arr: a, isz, w, k, vals: (off + ilen, stop), idx: at, xor: !add });
        off = stop;
    }
    (off == body.len()).then_some(())
}

/// An unsigned value of 1, 2, 4 or 8 bytes, for the typed path of apply_steps.
trait Lane: Copy + Default {
    const N: usize;
    fn or_byte(self, p: u8, b: usize) -> Self;
    fn unzz(self) -> Self;
    fn wadd(self, o: Self) -> Self;
    fn wsub(self, o: Self) -> Self;
    fn xor(self, o: Self) -> Self;
}

macro_rules! lane {
    ($t:ty, $n:expr) => {
        impl Lane for $t {
            const N: usize = $n;
            #[inline(always)]
            fn or_byte(self, p: u8, b: usize) -> Self {
                self | ((p as $t) << (8 * b))
            }
            #[inline(always)]
            fn unzz(self) -> Self {
                (self >> 1) ^ (0 as $t).wrapping_sub(self & 1)
            }
            #[inline(always)]
            fn wadd(self, o: Self) -> Self {
                self.wrapping_add(o)
            }
            #[inline(always)]
            fn wsub(self, o: Self) -> Self {
                self.wrapping_sub(o)
            }
            #[inline(always)]
            fn xor(self, o: Self) -> Self {
                self ^ o
            }
        }
    };
}
lane!(u8, 1);
lane!(u16, 2);
lane!(u32, 4);
lane!(u64, 8);

#[inline(always)]
fn undo_t<L: Lane, const T: usize>(col: &mut [L], acc: &mut [L; 3]) {
    for x in col.iter_mut() {
        let mut y = x.unzz();
        for a in acc[..T].iter_mut() {
            *a = a.wadd(y);
            y = *a;
        }
        *x = y;
    }
}

/// decode_apply on an aligned array: a chunk of every component is decoded, then the chunk's
/// rows are updated in one pass (OP 0 xor, 1 add, 2 subtract, wrapping).
/// W: the width when it is 1 to 4 (the compiler unrolls the rows), 0 for any other.
fn decode_apply_t<L: Lane, const OP: u8, const W: usize>(vin: &[u8], k: usize, w: usize, idx: &[u32], dst: &mut [L], tmp: &mut Vec<L>) {
    let w = if W > 0 { W } else { w };
    let (tags, planes) = vin.split_at(w);
    tmp.clear();
    tmp.resize(w * CHUNK, L::default());
    let mut acc = [[L::default(); 3]; MAX_WIDTH];
    let mut s = 0;
    while s < k {
        let n = CHUNK.min(k - s);
        for c in 0..w {
            let col = &mut tmp[c * CHUNK..c * CHUNK + n];
            let pl = |b: usize| {
                let at = (c * L::N + b) * k + s;
                &planes[at..at + n]
            };
            // the bytes of each value from its planes (zipped: no index checks, vectorised)
            match L::N {
                1 => {
                    for (x, &a) in col.iter_mut().zip(pl(0)) {
                        *x = L::default().or_byte(a, 0);
                    }
                }
                2 => {
                    for ((x, &a), &b) in col.iter_mut().zip(pl(0)).zip(pl(1)) {
                        *x = L::default().or_byte(a, 0).or_byte(b, 1);
                    }
                }
                4 => {
                    for ((((x, &a), &b), &c2), &d) in col.iter_mut().zip(pl(0)).zip(pl(1)).zip(pl(2)).zip(pl(3)) {
                        *x = L::default().or_byte(a, 0).or_byte(b, 1).or_byte(c2, 2).or_byte(d, 3);
                    }
                }
                _ => {
                    col.fill(L::default());
                    for b in 0..L::N {
                        for (x, &p) in col.iter_mut().zip(pl(b)) {
                            *x = x.or_byte(p, b);
                        }
                    }
                }
            }
            let a = &mut acc[c];
            match tags[c] {
                0 => undo_t::<L, 0>(col, a),
                1 => undo_t::<L, 1>(col, a),
                2 => undo_t::<L, 2>(col, a),
                3 => undo_t::<L, 3>(col, a),
                _ => {}
            }
        }
        for (i, &r) in idx[s..s + n].iter().enumerate() {
            let row = &mut dst[r as usize * w..r as usize * w + w];
            for (c, x) in row.iter_mut().enumerate() {
                let d = tmp[c * CHUNK + i];
                *x = match OP {
                    0 => x.xor(d),
                    1 => x.wadd(d),
                    _ => x.wsub(d),
                };
            }
        }
        s += n;
    }
}

fn decode_apply_lane<L: Lane>(vin: &[u8], k: usize, w: usize, idx: &[u32], dst: &mut [u8], op: u8, tmp: &mut Vec<L>) -> bool {
    // SAFETY: any bit pattern is a valid unsigned integer; align_to_mut only hands out the
    // aligned middle part, and the whole buffer must be that part.
    let (pre, mid, post) = unsafe { dst.align_to_mut::<L>() };
    if !pre.is_empty() || !post.is_empty() {
        return false;
    }
    macro_rules! by_w {
        ($op:expr) => {
            match w {
                1 => decode_apply_t::<L, $op, 1>(vin, k, w, idx, mid, tmp),
                2 => decode_apply_t::<L, $op, 2>(vin, k, w, idx, mid, tmp),
                3 => decode_apply_t::<L, $op, 3>(vin, k, w, idx, mid, tmp),
                4 => decode_apply_t::<L, $op, 4>(vin, k, w, idx, mid, tmp),
                _ => decode_apply_t::<L, $op, 0>(vin, k, w, idx, mid, tmp),
            }
        };
    }
    match op {
        0 => by_w!(0),
        1 => by_w!(1),
        _ => by_w!(2),
    }
    true
}

#[derive(Default)]
struct Scratch {
    t1: Vec<u8>,
    t2: Vec<u16>,
    t4: Vec<u32>,
    t8: Vec<u64>,
}

/// Decodes the values of a record and applies them to the rows `idx` of `dst` in place.
fn decode_apply_any(isz: usize, vin: &[u8], k: usize, w: usize, idx: &[u32], dst: &mut [u8], op: u8, sc: &mut Scratch) {
    let typed = match isz {
        1 => decode_apply_lane::<u8>(vin, k, w, idx, dst, op, &mut sc.t1),
        2 => decode_apply_lane::<u16>(vin, k, w, idx, dst, op, &mut sc.t2),
        4 => decode_apply_lane::<u32>(vin, k, w, idx, dst, op, &mut sc.t4),
        _ => decode_apply_lane::<u64>(vin, k, w, idx, dst, op, &mut sc.t8),
    };
    if !typed {
        // an array not aligned to its item size (numpy's are, unless made from an offset buffer)
        // SAFETY: u32 to bytes, same memory
        let iv = unsafe { std::slice::from_raw_parts(idx.as_ptr() as *const u8, idx.len() * 4) };
        by_size!(isz, decode_apply(vin, k, w, iv, dst, op));
    }
}

fn decode_apply<const N: usize>(vin: &[u8], k: usize, w: usize, iv: &[u8], dst: &mut [u8], op: u8) {
    decode_cols::<N>(vin, k, w, |e, c, d| {
        let at = idx_at(iv, e) as usize * w + c;
        let x = rd::<N>(dst, at);
        let y = match op {
            0 => x ^ d,
            1 => x.wrapping_add(d),
            _ => x.wrapping_sub(d),
        };
        wr::<N>(dst, at, y & mask::<N>());
    });
}

/// The plain arrays of a dict {key: numpy array}: (views to hold, arrays). An entry that isn't a
/// str key and a writable C-contiguous array with an item size of 1, 2, 4 or 8 is left out (a
/// record that names it stops the run), as are arrays sharing memory.
fn dict_arrays(arrays: &Bound<'_, PyDict>) -> (Vec<Buf>, Vec<Arr>, Vec<Py<PyAny>>) {
    let (mut bufs, mut arrs, mut keys) = (Vec::new(), Vec::new(), Vec::new());
    // a list of the items first: nothing run below can change what is iterated
    for item in arrays.items().iter() {
        let Ok((k, v)) = item.extract::<(Bound<'_, PyAny>, Bound<'_, PyAny>)>() else { continue };
        let Ok(ks) = k.downcast::<PyString>() else { continue };
        let Ok(kb) = ks.to_str() else { continue };
        let Ok(isz) = v.getattr("itemsize").and_then(|x| x.extract::<usize>()) else { continue };
        if !matches!(isz, 1 | 2 | 4 | 8) {
            continue;
        }
        let Ok(b) = Buf::new(&v, true) else { continue };
        arrs.push(Arr { key: kb.as_bytes().to_vec(), ptr: b.view.buf as usize, len: b.len(), isz });
        keys.push(k.unbind());
        bufs.push(b);
    }
    // arrays sharing memory are left out, all of them
    let shared: Vec<bool> = (0..bufs.len()).map(|i| (0..bufs.len()).any(|j| j != i && bufs[i].overlaps(&bufs[j]))).collect();
    let mut i = 0;
    arrs.retain(|_| { i += 1; !shared[i - 1] });
    let mut i = 0;
    keys.retain(|_| { i += 1; !shared[i - 1] });
    let mut i = 0;
    bufs.retain(|_| { i += 1; !shared[i - 1] });
    (bufs, arrs, keys)
}

/// apply_steps(blobs, arrays, forward, threads=None) -> (number of steps applied, keys of the
/// arrays changed). Crosses the packed steps in `blobs` (a list of bytes, in the order crossed)
/// one after the other on the channel arrays of one state (`arrays`, MeshState.arrays), in place,
/// exactly as History.goto does step by step. Stops before the first step it doesn't take (see
/// above), which it leaves untouched. The GIL is released while it works. Decompressing and
/// decoding run ahead on up to `threads` helper threads (None: none for short runs or small
/// steps, else up to 3, one less than the cores); the steps are applied in order on the calling
/// thread.
#[pyfunction]
#[pyo3(signature = (blobs, arrays, forward, threads=None))]
fn apply_steps(
    py: Python<'_>,
    blobs: &Bound<'_, PyList>,
    arrays: &Bound<'_, PyDict>,
    forward: bool,
    threads: Option<usize>,
) -> PyResult<(usize, Vec<Py<PyAny>>)> {
    guard(|| {
        // the blobs: bytes objects (immutable), kept alive by these references
        let owned: Vec<Bound<'_, PyBytes>> = blobs.iter().map_while(|b| b.downcast_into::<PyBytes>().ok()).collect();
        let slices: Vec<&[u8]> = owned.iter().map(|b| b.as_bytes()).collect();
        let (bufs, arrs, keys) = dict_arrays(arrays);
        let mut touched = vec![false; arrs.len()];
        let nt = threads.unwrap_or_else(|| auto_threads(&slices)).min(MAX_THREADS);
        let done = py.allow_threads(|| run_steps(&slices, &arrs, &mut touched, forward, nt));
        drop(bufs);
        let changed = touched.iter().zip(keys).filter(|(t, _)| **t).map(|(_, k)| k).collect();
        Ok((done, changed))
    })
}

const MAX_THREADS: usize = 3;
/// Helper threads only pay off when each step is a lot of work and there are many of them:
/// measured on a busy 4-core machine, a drag over 50-step jumps and any jump at 100k vertices
/// (about 4 KB a step) got slower with them, a 3,000-step jump at 1M vertices (about 20 KB a
/// step) faster. Below these, the calling thread does everything.
const MIN_STEP_BYTES: usize = 16 << 10;
const MIN_RUN_BYTES: usize = 4 << 20;

fn auto_threads(blobs: &[&[u8]]) -> usize {
    let total: usize = blobs.iter().map(|b| b.len()).sum();
    if blobs.len() < 64 || total < MIN_RUN_BYTES || total / blobs.len() < MIN_STEP_BYTES {
        return 0;
    }
    let cores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    cores.saturating_sub(1).min(MAX_THREADS)
}

/// Applies one decoded step (checked: nothing here can fail).
fn apply_decoded(blob: &[u8], d: &Decoded, arrs: &[Arr], touched: &mut [bool], forward: bool, sc: &mut Scratch) {
    let body = d.body(blob);
    for r in d.recs.iter() {
        touched[r.arr] = true;
        if r.k == 0 {
            continue;
        }
        let a = &arrs[r.arr];
        // SAFETY: `ptr`/`len` describe a writable buffer exported by a live array (the view is
        // held by apply_steps for the whole call) that overlaps no other array's buffer and none
        // of the blobs (bytes objects are immutable, numpy can't export them writable). Only this
        // thread writes to it, one record at a time.
        let dst = unsafe { std::slice::from_raw_parts_mut(a.ptr as *mut u8, a.len) };
        let op = if r.xor { 0 } else if forward { 1 } else { 2 };
        let (vin, iv) = (&body[r.vals.0..r.vals.1], &d.idx[r.idx..r.idx + r.k]);
        decode_apply_any(r.isz, vin, r.k, r.w, iv, dst, op, sc);
    }
}

fn run_steps(blobs: &[&[u8]], arrs: &[Arr], touched: &mut [bool], forward: bool, nt: usize) -> usize {
    let mut sc = Scratch::default();
    if nt == 0 || blobs.len() < 2 {
        let mut dctx = zstd_safe::DCtx::create();
        let mut d = Decoded::default();
        for (i, blob) in blobs.iter().enumerate() {
            decode_step(blob, arrs, &mut dctx, &mut d);
            if !d.ok {
                return i;
            }
            apply_decoded(blob, &d, arrs, touched, forward, &mut sc);
        }
        return blobs.len();
    }
    // helper t decodes steps t, t + nt, t + 2 nt, ... into a channel of AHEAD; the decoded steps
    // come back to it for reuse. Memory: at most nt * (AHEAD + 2) decoded steps at a time.
    const AHEAD: usize = 2;
    use std::sync::mpsc::{channel, sync_channel};
    std::thread::scope(|scope| {
        let mut rx = Vec::new();
        let mut back = Vec::new();
        for t in 0..nt {
            let (tx, r) = sync_channel::<Box<Decoded>>(AHEAD);
            let (btx, brx) = channel::<Box<Decoded>>();
            rx.push(r);
            back.push(btx);
            scope.spawn(move || {
                let mut dctx = zstd_safe::DCtx::create();
                for i in (t..blobs.len()).step_by(nt) {
                    let mut d = brx.try_recv().unwrap_or_default();
                    decode_step(blobs[i], arrs, &mut dctx, &mut d);
                    let ok = d.ok;
                    if tx.send(d).is_err() || !ok {
                        return; // the caller stopped, or this step isn't taken: no later step is
                    }
                }
            });
        }
        let mut done = blobs.len();
        for (i, blob) in blobs.iter().enumerate() {
            let Ok(d) = rx[i % nt].recv() else {
                done = i;
                break;
            };
            if !d.ok {
                done = i;
                break;
            }
            apply_decoded(blob, &d, arrs, touched, forward, &mut sc);
            let _ = back[i % nt].send(d);
        }
        drop(rx); // wakes any helper waiting to hand over a step
        done
    })
}

#[pymodule]
fn hindsight_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("API", API)?;
    m.add_function(wrap_pyfunction!(apply_steps, m)?)?;
    m.add_function(wrap_pyfunction!(diff, m)?)?;
    m.add_function(wrap_pyfunction!(apply, m)?)?;
    m.add_function(wrap_pyfunction!(pack, m)?)?;
    m.add_function(wrap_pyfunction!(unpack, m)?)?;
    step::register(m)?;
    Ok(())
}
