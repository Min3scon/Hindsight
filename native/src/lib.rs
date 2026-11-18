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

#[cfg(target_endian = "big")]
compile_error!("hindsight_native assumes a little-endian target");

use std::mem::MaybeUninit;
use std::panic::{catch_unwind, AssertUnwindSafe};

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyByteArray, PyBytes};

/// Bumped when a function's meaning changes; hindsight/core/native.py refuses a different one.
const API: u32 = 1;

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
            return Ok(None);
        }
        let k = idx.len();
        let ib = PyByteArray::new_bound_with(py, k * 4, |o| {
            for (j, &i) in idx.iter().enumerate() {
                o[4 * j..4 * j + 4].copy_from_slice(&i.to_le_bytes());
            }
            Ok(())
        })?;
        let vb = PyByteArray::new_bound_with(py, k * row, |o| {
            by_size!(isz, sub_rows(a, b, &idx, w, o));
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
        by_size!(isz, apply_rows(dst, iv, v, w, xor, forward));
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
    put_idx(out, 0, cur);
    for j in 1..out.len() / 4 {
        cur = cur.wrapping_add(rd::<N>(g, j - 1) as u32).wrapping_add(1);
        put_idx(out, j, cur);
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
        let mut gmax: u64 = 0;
        for j in 1..k {
            let (a, b) = (idx_at(iv, j - 1), idx_at(iv, j));
            if b <= a {
                return Ok(None);
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
            let bits = take(iv, 8, (span as u128 + 7) / 8)?;
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

fn decode_vals<const N: usize>(vin: &[u8], k: usize, w: usize, add: bool, out: &mut [u8]) -> Result<(), &'static str> {
    let (tags, planes) = vin.split_at(w);
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
                wr::<N>(out, (s + i) * w + c, x);
            }
            s += n;
        }
    }
    Ok(())
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

#[pymodule]
fn hindsight_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("API", API)?;
    m.add_function(wrap_pyfunction!(diff, m)?)?;
    m.add_function(wrap_pyfunction!(apply, m)?)?;
    m.add_function(wrap_pyfunction!(pack, m)?)?;
    m.add_function(wrap_pyfunction!(unpack, m)?)?;
    Ok(())
}
