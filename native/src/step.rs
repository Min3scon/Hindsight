//! The step container: the twin of hindsight/core/step.py (now in tests/reference).
//!
//! Step blob, formats 3 and 4:
//!   b"HSS3" | u32 CRC-32 of everything after this field | u16 header length | header | body
//!   header = <BBBBQdI> version (3, or 4 with data records), kind, flags (1 before, 2 after, 4 snaps,
//!   8 written quickly, 16 data records), reserved, seq, time, raw body length; then label (u16 length
//!   + utf-8), mode and target (u8 length + utf-8), extra (u32 length + JSON), deltas (u16 count),
//!   then the sizes the flags announce (before, after, snaps, records).
//!   body = codec blob of: packed deltas, before, after and extra snapshot blobs, data records JSON.
//! Format 1 and 2 (read only): b"HSS1" | u32 JSON length | header JSON | codec blob of the body.
//!
//! JSON that holds Python objects (extras and data records) stays bytes here: the facade reads and
//! writes it. Everything else, including the records of every delta, is decoded and encoded here.
//! All lengths from the data are checked in u128 (or with `get`) before they size an allocation.

use super::*;
use crate::codec;

const MAGIC_V3: &[u8; 4] = b"HSS3";
const MAGIC_V1: &[u8; 4] = b"HSS1";
const SNAP_MAGIC: &[u8; 4] = b"HSN1";
const FIXED: usize = 24;
const FLAG_BEFORE: u8 = 1;
const FLAG_AFTER: u8 = 2;
const FLAG_SNAPS: u8 = 4;
const FLAG_QUICK: u8 = 8;
const FLAG_RECORDS: u8 = 16;
const KINDS: [&str; 4] = ["DELTA", "TOPO", "JOIN", "DATA"];
/// A decoded step may not hold more than this many bytes of decoded arrays (twice the body limit).
const MAX_OUT: u64 = 2 * codec::MAX_RAW;

pub enum StepErr {
    Corrupt(String),
    Value(String),
    Mem,
}

fn corrupt<T>(s: impl Into<String>) -> Result<T, StepErr> {
    Err(StepErr::Corrupt(s.into()))
}

impl From<codec::Err> for StepErr {
    fn from(e: codec::Err) -> Self {
        match e {
            codec::Err::Empty => StepErr::Corrupt("step blob: empty body".into()),
            codec::Err::Blob(s) => StepErr::Corrupt(s),
            codec::Err::Mem => StepErr::Mem,
        }
    }
}

fn try_vec(n: usize) -> Result<Vec<u8>, StepErr> {
    let mut v = Vec::new();
    v.try_reserve_exact(n).map_err(|_| StepErr::Mem)?;
    Ok(v)
}

fn u16le(b: &[u8], o: usize) -> Option<usize> {
    Some(u16::from_le_bytes(b.get(o..o.checked_add(2)?)?.try_into().ok()?) as usize)
}
fn u32le(b: &[u8], o: usize) -> Option<usize> {
    Some(u32::from_le_bytes(b.get(o..o.checked_add(4)?)?.try_into().ok()?) as usize)
}

// ---------------------------------------------------------------------------------- records
pub struct DDelta {
    pub key: String,
    pub exists: (bool, bool),
    pub add: bool,
    pub isz: usize,
    pub w: usize,
    pub k: usize,
    pub idx: Vec<u8>,
    pub vals: Vec<u8>,
}

/// One record read from the body, checked, not yet decoded.
struct Plan<'a> {
    key: String,
    exists: (bool, bool),
    add: bool,
    isz: usize,
    w: usize,
    k: usize,
    v1: Option<(usize, u64, &'a [u8], &'a [u8])>, // format 1: gap size, first index, gaps, planes
    code: u8,
    ibytes: &'a [u8],
    vin: &'a [u8], // tags + planes
}

const HDR: usize = 16; // <2sBBBBHII

/// delta.unpack_at: the record at `off` of `body`, up to its checks. -> (plan, offset after it)
fn plan_record(body: &[u8], off: usize) -> Result<(Plan<'_>, usize), String> {
    let end = body.len();
    if end < off + HDR {
        return Err("delta: too short".into());
    }
    let magic = &body[off..off + 2];
    let (flags, isz, w, code) = (body[off + 2], body[off + 3] as usize, body[off + 4] as usize, body[off + 5]);
    let klen = u16le(body, off + 6).unwrap() as u128;
    let k = u32le(body, off + 8).unwrap();
    let last = u32le(body, off + 12).unwrap(); // ilen (format 3) or first index (format 1)
    if magic == b"HD" {
        // format 1 and 2: <2sBBBBHII = magic, flags, xor itemsize, width, gap itemsize, key length, k, first
        let dsz = code as u128;
        let n = HDR as u128 + klen + (k.max(1) as u128 - 1) * dsz + k as u128 * isz as u128 * w as u128;
        if off as u128 + n > end as u128 {
            return Err("delta: cut short".into());
        }
        let n = n as usize;
        if !matches!(isz, 1 | 2 | 4 | 8) || !matches!(code, 1 | 2 | 4) || w == 0 || w > MAX_WIDTH {
            return Err("delta: bad header".into());
        }
        let key_at = off + HDR;
        let key = std::str::from_utf8(&body[key_at..key_at + klen as usize]).map_err(|_| "delta: bad key")?;
        let exists = (flags & 1 != 0, flags & 2 != 0);
        let mut p = Plan { key: key.to_string(), exists, add: false, isz, w, k, v1: None, code: 0, ibytes: &[], vin: &[] };
        let at = key_at + klen as usize;
        if k > 0 {
            let gl = (k - 1) * code as usize;
            p.v1 = Some((code as usize, last as u64, &body[at..at + gl], &body[at + gl..off + n]));
        }
        return Ok((p, off + n));
    }
    if magic != b"HE" {
        return Err("unpack_delta: not a Hindsight delta (wrong header)".into());
    }
    if flags & !7 != 0 || !matches!(isz, 1 | 2 | 4 | 8) || w == 0 || w > MAX_WIDTH {
        return Err("delta: bad header".into());
    }
    let key_at = off + HDR;
    if key_at as u128 + klen > end as u128 {
        return Err(E_CUT.into());
    }
    let key = std::str::from_utf8(&body[key_at..key_at + klen as usize]).map_err(|_| "delta: bad key")?;
    let at = key_at + klen as usize;
    let exists = (flags & 1 != 0, flags & 2 != 0);
    let add = flags & 4 != 0;
    let mut p = Plan { key: key.to_string(), exists, add, isz, w, k, v1: None, code, ibytes: &[], vin: &[] };
    if k == 0 {
        return Ok((p, at));
    }
    let ilen = last as u128;
    let nx = k as u128 * isz as u128 * w as u128;
    let stop = at as u128 + ilen + w as u128 + nx;
    if stop > end as u128 {
        return Err("delta: cut short".into());
    }
    let (ie, stop) = (at + ilen as usize, stop as usize);
    p.ibytes = &body[at..ie];
    p.vin = &body[ie..stop];
    Ok((p, stop))
}

fn decode_plan(p: &Plan<'_>) -> Result<DDelta, StepErr> {
    let (k, isz, w) = (p.k, p.isz, p.w);
    let mut d = DDelta { key: p.key.clone(), exists: p.exists, add: p.add, isz, w, k, idx: Vec::new(), vals: Vec::new() };
    if k == 0 {
        return Ok(d);
    }
    let nvals = (k as u128 * isz as u128 * w as u128).min(u64::MAX as u128) as u64;
    if (k as u64 * 4).saturating_add(nvals) > MAX_OUT {
        return corrupt("step blob: damaged (delta: implausible size)");
    }
    d.idx = try_vec(k * 4)?;
    d.idx.resize(k * 4, 0);
    d.vals = try_vec(k * w * isz)?;
    d.vals.resize(k * w * isz, 0);
    let dam = |e: &str| StepErr::Corrupt(format!("step blob: damaged ({e})"));
    if let Some((dsz, first, gaps, planes)) = p.v1 {
        put_idx(&mut d.idx, 0, first as u32);
        if k > 1 {
            let mut cur = first;
            if (0..k - 1).any(|j| le(gaps, j, dsz) == 0) {
                return Err(dam("delta: indices not increasing"));
            }
            for j in 0..k - 1 {
                cur += le(gaps, j, dsz);
                if cur > 0xFFFF_FFFF {
                    return Err(dam(E_RANGE));
                }
                put_idx(&mut d.idx, j + 1, cur as u32);
            }
        }
        for e in 0..k {
            for r in 0..isz * w {
                d.vals[e * isz * w + r] = planes[r * k + e];
            }
        }
        return Ok(d);
    }
    decode_idx(p.code, p.ibytes, k, &mut d.idx).map_err(dam)?;
    by_size!(isz, decode_vals(p.vin, k, w, p.add, &mut d.vals)).map_err(dam)?;
    Ok(d)
}

// ---------------------------------------------------------------------------------- snapshots
fn dtype_size(s: &str) -> Option<usize> {
    let b = s.as_bytes();
    if b.len() != 3 || !matches!(b[0], b'<' | b'>' | b'|' | b'=') {
        return None;
    }
    let n = (b[2] as char).to_digit(10)? as usize;
    match (b[1], n) {
        (b'b', 1) | (b'i' | b'u', 1 | 2 | 4 | 8) | (b'f', 2 | 4 | 8) => Some(n),
        _ => None,
    }
}

pub struct SnapDesc {
    pub counts: Vec<(String, i64)>,
    pub arrays: Vec<(String, String, std::ops::Range<usize>)>, // key, dtype, bytes within the blob
}

/// snapshot_from_bytes up to the arrays' bytes: b"HSN1" | u32 json_len | JSON | raw arrays.
pub fn parse_snapshot(b: &[u8]) -> Result<SnapDesc, String> {
    let cut = || "snapshot_from_bytes: cut short".to_string();
    if b.get(..4) != Some(SNAP_MAGIC) {
        return Err("snapshot_from_bytes: not a Hindsight snapshot (wrong header)".into());
    }
    let hl = u32le(b, 4).ok_or_else(cut)?;
    let head = b.get(8..8 + hl).ok_or_else(cut)?;
    let bad = |m: &str| format!("snapshot_from_bytes: bad header ({m})");
    let v: serde_json::Value = serde_json::from_slice(head).map_err(|e| bad(&e.to_string()))?;
    let mut counts = Vec::new();
    for (k, c) in v.get("counts").and_then(|c| c.as_object()).ok_or_else(|| bad("counts"))? {
        counts.push((k.clone(), c.as_i64().ok_or_else(|| bad("counts"))?));
    }
    let mut arrays = Vec::new();
    let mut off = 8 + hl;
    for a in v.get("arrays").and_then(|a| a.as_array()).ok_or_else(|| bad("arrays"))? {
        let t = a.as_array().filter(|t| t.len() == 3).ok_or_else(|| bad("arrays"))?;
        let (key, dt) = (t[0].as_str().ok_or_else(|| bad("key"))?, t[1].as_str().ok_or_else(|| bad("dtype"))?);
        let isz = dtype_size(dt).ok_or_else(|| bad("dtype"))?;
        let len = t[2].as_i64().ok_or_else(|| bad("length"))?;
        let n = len as i128 * isz as i128;
        if n < 0 || off as i128 + n > b.len() as i128 {
            return Err(cut());
        }
        arrays.push((key.to_string(), dt.to_string(), off..off + n as usize));
        off += n as usize;
    }
    Ok(SnapDesc { counts, arrays })
}

// ---------------------------------------------------------------------------------- decoding
type Range = std::ops::Range<usize>;

pub struct Dec {
    pub version: u8,
    pub seq: u64,
    pub time: f64,
    pub kind: String,
    pub label: String,
    pub mode: String,
    pub target: String,
    pub extra: Vec<u8>,
    pub quick: bool,
    pub deltas: Vec<DDelta>,
    pub body: Vec<u8>,
    pub before: Option<Range>,
    pub after: Option<Range>,
    pub snaps: Option<Vec<(String, Range)>>,
    pub records: Option<Range>,
    pub codec: u8,
}

/// What the header of a format 3 or 4 step says (everything but the body).
pub struct Head<'a> {
    pub kind: u8,
    pub seq: u64,
    pub time: f64,
    pub raw: usize,
    pub label: &'a str,
    pub mode: &'a str,
    pub target: &'a str,
    pub extra: &'a [u8],
    pub n_deltas: usize,
    pub before: Option<usize>,
    pub after: Option<usize>,
    pub snaps: Option<Vec<(&'a str, usize)>>,
    pub records: Option<usize>,
    pub quick: bool,
    pub payload: &'a [u8],
}

pub fn parse_head(b: &[u8]) -> Result<Head<'_>, StepErr> {
    if b.len() < 10 {
        return corrupt("step blob: too short");
    }
    if crc32fast::hash(&b[8..]) as usize != u32le(b, 4).unwrap() {
        return corrupt("step blob: checksum mismatch");
    }
    let hl = u16le(b, 8).unwrap();
    let h = b.get(10..10 + hl).ok_or_else(|| StepErr::Corrupt("step blob: header cut short".into()))?;
    let bad = |m: &str| StepErr::Corrupt(format!("step blob: bad header ({m})"));
    if hl < FIXED {
        return Err(bad("too short"));
    }
    let (ver, kind, flags) = (h[0], h[1], h[2]);
    if !matches!(ver, 3 | 4) || kind as usize >= KINDS.len() || flags & !31 != 0 || (ver == 4) != (flags & FLAG_RECORDS != 0) {
        return corrupt("step blob: bad header");
    }
    let seq = u64::from_le_bytes(h[4..12].try_into().unwrap());
    let time = f64::from_le_bytes(h[12..20].try_into().unwrap());
    let raw = u32le(h, 20).unwrap();
    let mut off = FIXED;
    let s16 = |off: &mut usize| -> Result<&str, StepErr> {
        let n = u16le(h, *off).ok_or_else(|| bad("cut"))?;
        let s = h.get(*off + 2..*off + 2 + n).ok_or_else(|| bad("cut"))?;
        *off += 2 + n;
        std::str::from_utf8(s).map_err(|_| bad("utf-8"))
    };
    let s8 = |off: &mut usize| -> Result<&str, StepErr> {
        let n = *h.get(*off).ok_or_else(|| bad("cut"))? as usize;
        let s = h.get(*off + 1..*off + 1 + n).ok_or_else(|| bad("cut"))?;
        *off += 1 + n;
        std::str::from_utf8(s).map_err(|_| bad("utf-8"))
    };
    let label = s16(&mut off)?;
    let mode = s8(&mut off)?;
    let target = s8(&mut off)?;
    let n = u32le(h, off).ok_or_else(|| bad("cut"))?;
    let extra = h.get(off + 4..off + 4 + n).ok_or_else(|| bad("cut"))?;
    off += 4 + n;
    let n_deltas = u16le(h, off).ok_or_else(|| bad("cut"))?;
    off += 2;
    let mut hd = Head {
        kind, seq, time, raw, label, mode, target, extra, n_deltas,
        before: None, after: None, snaps: None, records: None, quick: flags & FLAG_QUICK != 0,
        payload: &b[10 + hl..],
    };
    if flags & FLAG_BEFORE != 0 {
        hd.before = Some(u32le(h, off).ok_or_else(|| bad("cut"))?);
        off += 4;
    }
    if flags & FLAG_AFTER != 0 {
        hd.after = Some(u32le(h, off).ok_or_else(|| bad("cut"))?);
        off += 4;
    }
    if flags & FLAG_SNAPS != 0 {
        let ns = u16le(h, off).ok_or_else(|| bad("cut"))?;
        off += 2;
        let mut v = Vec::new();
        for _ in 0..ns {
            let uid = s8(&mut off)?;
            v.push((uid, u32le(h, off).ok_or_else(|| bad("cut"))?));
            off += 4;
        }
        hd.snaps = Some(v);
    }
    if flags & FLAG_RECORDS != 0 {
        hd.records = Some(u32le(h, off).ok_or_else(|| bad("cut"))?);
        off += 4;
    }
    if off != hl {
        return corrupt("step blob: header length mismatch");
    }
    Ok(hd)
}

fn sections(body: &[u8], off: &mut usize, n: usize, what: &str) -> Result<Range, StepErr> {
    if *off as u128 + n as u128 > body.len() as u128 {
        return corrupt(format!("step blob: {what} cut short"));
    }
    let r = *off..*off + n;
    *off += n;
    Ok(r)
}

fn decode_v3(b: &[u8]) -> Result<Dec, StepErr> {
    let h = parse_head(b)?;
    let body = codec::decompress(h.payload, Some(h.raw as u64))?;
    let mut off = 0usize;
    let mut deltas = Vec::new();
    let mut out_bytes = 0u64;
    for _ in 0..h.n_deltas {
        let (p, next) = plan_record(&body, off).map_err(|e| StepErr::Corrupt(format!("step blob: damaged ({e})")))?;
        let d = decode_plan(&p)?;
        out_bytes += (d.idx.len() + d.vals.len()) as u64;
        if out_bytes > MAX_OUT {
            return corrupt("step blob: damaged (delta: implausible size)");
        }
        deltas.push(d);
        off = next;
    }
    let before = h.before.map(|n| sections(&body, &mut off, n, "snapshot")).transpose()?;
    let after = h.after.map(|n| sections(&body, &mut off, n, "snapshot")).transpose()?;
    let snaps = match &h.snaps {
        Some(v) => {
            let mut o = Vec::new();
            for (uid, n) in v {
                o.push((uid.to_string(), sections(&body, &mut off, *n, "snapshot")?));
            }
            Some(o)
        }
        None => None,
    };
    let records = h.records.map(|n| sections(&body, &mut off, n, "records")).transpose()?;
    if off != body.len() {
        return corrupt("step blob: unexpected data after the last record");
    }
    // the snapshots are parsed (and so checked) here, as the step is read, not when first used
    for r in before.iter().chain(after.iter()).chain(snaps.iter().flatten().map(|(_, r)| r)) {
        parse_snapshot(&body[r.clone()]).map_err(|e| StepErr::Corrupt(format!("step blob: damaged ({e})")))?;
    }
    Ok(Dec {
        version: 3,
        seq: h.seq,
        time: h.time,
        kind: KINDS[h.kind as usize].to_string(),
        label: h.label.to_string(),
        mode: h.mode.to_string(),
        target: h.target.to_string(),
        extra: h.extra.to_vec(),
        quick: h.quick,
        deltas,
        body,
        before,
        after,
        snaps,
        records,
        codec: h.payload[0],
    })
}

#[derive(serde::Deserialize)]
struct V1Meta {
    seq: u64,
    label: String,
    mode: String,
    target: String,
    time: Option<f64>,
    kind: Option<String>,
    extra: Option<serde_json::Value>,
}
#[derive(serde::Deserialize)]
struct V1Delta {
    n: u64,
}
#[derive(serde::Deserialize)]
struct V1Head {
    meta: V1Meta,
    deltas: Vec<V1Delta>,
    before: Option<u64>,
    after: Option<u64>,
    snaps: Option<Vec<(String, u64)>>,
}

fn decode_v1(b: &[u8]) -> Result<Dec, StepErr> {
    let hl = u32le(b, 4).ok_or_else(|| StepErr::Corrupt("step blob: too short".into()))?;
    let hj = b.get(8..8 + hl).ok_or_else(|| StepErr::Corrupt("step blob: header cut short".into()))?;
    let head: V1Head = serde_json::from_slice(hj).map_err(|e| StepErr::Corrupt(format!("step blob: bad header ({e})")))?;
    let payload = &b[8 + hl..];
    let body = codec::decompress(payload, None)?;
    let mut off = 0usize;
    let mut deltas = Vec::new();
    let mut out_bytes = 0u64;
    let dam = |e: String| StepErr::Corrupt(format!("step blob: damaged ({e})"));
    for dh in &head.deltas {
        // the record is read from the start of its slice; bytes of the slice after it are ignored
        let end = (off as u128 + dh.n as u128).min(body.len() as u128) as usize;
        let (p, _next) = plan_record(&body[..end], off).map_err(dam)?;
        let d = decode_plan(&p)?;
        out_bytes += (d.idx.len() + d.vals.len()) as u64;
        if out_bytes > MAX_OUT {
            return corrupt("step blob: damaged (delta: implausible size)");
        }
        deltas.push(d);
        off = off.saturating_add(dh.n as usize).min(usize::MAX);
        if off > body.len() {
            off = body.len();
        }
    }
    let snap = |off: &mut usize, n: u64| -> Result<Range, StepErr> {
        let n = usize::try_from(n).map_err(|_| dam("snapshot size".into()))?;
        sections(&body, off, n, "snapshot")
    };
    let before = head.before.map(|n| snap(&mut off, n)).transpose()?;
    let after = head.after.map(|n| snap(&mut off, n)).transpose()?;
    let snaps = match &head.snaps {
        Some(v) => {
            let mut o = Vec::new();
            for (uid, n) in v {
                o.push((uid.clone(), snap(&mut off, *n)?));
            }
            Some(o)
        }
        None => None,
    };
    for r in before.iter().chain(after.iter()).chain(snaps.iter().flatten().map(|(_, r)| r)) {
        parse_snapshot(&body[r.clone()]).map_err(|e| StepErr::Corrupt(format!("step blob: damaged ({e})")))?;
    }
    let m = head.meta;
    let extra = match &m.extra {
        Some(serde_json::Value::Object(o)) if !o.is_empty() => serde_json::to_vec(o).map_err(|e| dam(e.to_string()))?,
        Some(serde_json::Value::Object(_)) | Some(serde_json::Value::Null) | None => Vec::new(),
        _ => return corrupt("step blob: damaged (extra is not an object)"),
    };
    Ok(Dec {
        version: 1,
        seq: m.seq,
        time: m.time.unwrap_or(0.0),
        kind: m.kind.unwrap_or_else(|| "DELTA".into()),
        label: m.label,
        mode: m.mode,
        target: m.target,
        extra,
        quick: false,
        deltas,
        body,
        before,
        after,
        snaps,
        records: None,
        codec: payload.first().copied().unwrap_or(0),
    })
}

pub fn decode(b: &[u8]) -> Result<Dec, StepErr> {
    match b.get(..4) {
        Some(m) if m == MAGIC_V3 => decode_v3(b),
        Some(m) if m == MAGIC_V1 => decode_v1(b),
        _ => corrupt("step blob: not a Hindsight step (wrong header)"),
    }
}

// ---------------------------------------------------------------------------------- encoding
/// What the header of a step to write says; the caller has the body.
pub struct Layout<'a> {
    pub kind: &'a str,
    pub seq: u64,
    pub time: f64,
    pub label: &'a str,
    pub mode: &'a str,
    pub target: &'a str,
    pub extra: &'a [u8],
    pub n_deltas: usize,
    pub before: Option<usize>,
    pub after: Option<usize>,
    pub snaps: Option<Vec<(&'a str, usize)>>,
    pub records: Option<usize>,
    pub quick: bool,
}

fn val(s: String) -> StepErr {
    StepErr::Value(s)
}

fn str8(out: &mut Vec<u8>, s: &str) -> Result<(), StepErr> {
    if s.len() > 255 {
        let head: String = s.chars().take(40).collect();
        return Err(val(format!("step field too long: {head:?}")));
    }
    out.push(s.len() as u8);
    out.extend_from_slice(s.as_bytes());
    Ok(())
}

fn size32(n: usize) -> Result<[u8; 4], StepErr> {
    u32::try_from(n).map(u32::to_le_bytes).map_err(|_| val("step part larger than 4 GB".into()))
}

/// The whole blob for a body (`raw`, uncompressed): header, codec, checksum.
pub fn assemble(l: &Layout, raw: &[u8], codec_id: u8, strong: bool) -> Result<Vec<u8>, StepErr> {
    let kind = KINDS.iter().position(|k| *k == l.kind).ok_or_else(|| val(format!("unknown step kind {:?}", l.kind)))?;
    let mut flags = 0u8;
    let mut tail = Vec::new();
    if let Some(n) = l.before {
        flags |= FLAG_BEFORE;
        tail.extend_from_slice(&size32(n)?);
    }
    if let Some(n) = l.after {
        flags |= FLAG_AFTER;
        tail.extend_from_slice(&size32(n)?);
    }
    if let Some(s) = &l.snaps {
        flags |= FLAG_SNAPS;
        let n = u16::try_from(s.len()).map_err(|_| val("too many snapshots".into()))?;
        tail.extend_from_slice(&n.to_le_bytes());
        for (uid, n) in s {
            str8(&mut tail, uid)?;
            tail.extend_from_slice(&size32(*n)?);
        }
    }
    if let Some(n) = l.records {
        flags |= FLAG_RECORDS;
        tail.extend_from_slice(&size32(n)?);
    }
    if l.quick && l.n_deltas > 0 {
        flags |= FLAG_QUICK;
    }
    let mut head = Vec::new();
    head.push(if l.records.is_some() { 4 } else { 3 });
    head.extend_from_slice(&[kind as u8, flags, 0]);
    head.extend_from_slice(&l.seq.to_le_bytes());
    head.extend_from_slice(&l.time.to_le_bytes());
    head.extend_from_slice(&size32(raw.len())?);
    let lb = &l.label.as_bytes()[..l.label.len().min(65535)];
    head.extend_from_slice(&(lb.len() as u16).to_le_bytes());
    head.extend_from_slice(lb);
    str8(&mut head, l.mode)?;
    str8(&mut head, l.target)?;
    head.extend_from_slice(&size32(l.extra.len())?);
    head.extend_from_slice(l.extra);
    let nd = u16::try_from(l.n_deltas).map_err(|_| val("too many deltas".into()))?;
    head.extend_from_slice(&nd.to_le_bytes());
    head.extend_from_slice(&tail);
    let hl = u16::try_from(head.len()).map_err(|_| val("step header too long".into()))?;
    let body = codec::compress(raw, codec_id, strong)?;
    let mut hasher = crc32fast::Hasher::new();
    hasher.update(&hl.to_le_bytes());
    hasher.update(&head);
    hasher.update(&body);
    let mut blob = try_vec(10 + head.len() + body.len())?;
    blob.extend_from_slice(MAGIC_V3);
    blob.extend_from_slice(&hasher.finalize().to_le_bytes());
    blob.extend_from_slice(&hl.to_le_bytes());
    blob.extend_from_slice(&head);
    blob.extend_from_slice(&body);
    Ok(blob)
}

/// delta.pack_delta for a decoded record (NORMAL effort: predictors for ADD).
fn pack_record(d: &DDelta, out: &mut Vec<u8>) -> Result<(), StepErr> {
    let key = d.key.as_bytes();
    let flags = d.exists.0 as u8 | (d.exists.1 as u8) << 1 | (d.add as u8) << 2;
    let hdr = |out: &mut Vec<u8>, code: u8, ilen: usize| -> Result<(), StepErr> {
        out.extend_from_slice(b"HE");
        out.extend_from_slice(&[flags, d.isz as u8, d.w as u8, code]);
        out.extend_from_slice(&(key.len() as u16).to_le_bytes());
        out.extend_from_slice(&size32(d.k)?);
        out.extend_from_slice(&size32(ilen)?);
        out.extend_from_slice(key);
        Ok(())
    };
    if d.k == 0 {
        return hdr(out, IDX_GAPS, 0);
    }
    let (code, ib) = pack_index(&d.idx, d.k).ok_or_else(|| val(format!("pack_delta {}: indices must be sorted and unique", d.key)))?;
    hdr(out, code, ib.len())?;
    out.extend_from_slice(&ib);
    let at = out.len();
    let n = d.w + d.k * d.isz * d.w;
    out.try_reserve(n).map_err(|_| StepErr::Mem)?;
    out.resize(at + n, 0);
    let (isz, k, w, predict) = (d.isz, d.k, d.w, d.add);
    by_size!(isz, pack_vals(&d.vals, k, w, predict, &mut out[at..]));
    Ok(())
}

fn same_deltas(a: &[DDelta], b: &[DDelta]) -> bool {
    a.len() == b.len()
        && a.iter().zip(b).all(|(x, y)| {
            x.key == y.key && x.exists == y.exists && x.add == y.add && x.isz == y.isz && x.w == y.w && x.k == y.k && x.idx == y.idx && x.vals == y.vals
        })
}

static FAULT: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn sect<'a>(d: &'a Dec, r: &Option<Range>) -> Option<&'a [u8]> {
    r.as_ref().map(|r| &d.body[r.clone()])
}

/// Two decoded steps hold the same data (the extras of an older format are not compared: they
/// are written as compact JSON, not copied).
fn same_dec(a: &Dec, b: &Dec) -> bool {
    a.seq == b.seq
        && a.time.to_bits() == b.time.to_bits()
        && a.kind == b.kind
        && a.label.as_bytes()[..a.label.len().min(65535)] == *b.label.as_bytes()
        && a.mode == b.mode
        && a.target == b.target
        && (a.version == 1 || a.extra == b.extra)
        && same_deltas(&a.deltas, &b.deltas)
        && sect(a, &a.before) == sect(b, &b.before)
        && sect(a, &a.after) == sect(b, &b.after)
        && sect(a, &a.records) == sect(b, &b.records)
        && a.snaps.as_ref().map(|v| v.len()) == b.snaps.as_ref().map(|v| v.len())
        && a.snaps.iter().flatten().zip(b.snaps.iter().flatten()).all(|((u, r), (v, q))| u == v && a.body[r.clone()] == b.body[q.clone()])
}

/// step.recompress: a smaller blob for the same step, or None. Steps from older formats and steps
/// written quickly are re-packed in full; every step gets the strong codec setting. The new blob is
/// read back and must hold exactly the same data.
pub fn recompress_core(b: &[u8]) -> Result<Option<Vec<u8>>, StepErr> {
    let magic = b.get(..4);
    if magic != Some(MAGIC_V3) && magic != Some(MAGIC_V1) {
        return Ok(None);
    }
    let d = decode(b)?;
    if d.version == 3 && codec::is_strong(d.codec) && !d.quick {
        return Ok(None);
    }
    let codec_id = if d.version == 3 { codec::id_of(codec::name_of(d.codec)).unwrap_or(codec::ZLIB) } else { codec::ZSTD };
    let mut raw = try_vec(d.body.len() + 64)?;
    for x in &d.deltas {
        pack_record(x, &mut raw)?;
    }
    let before = d.before.as_ref().map(|r| r.len());
    let after = d.after.as_ref().map(|r| r.len());
    let records = d.records.as_ref().map(|r| r.len());
    for r in d.before.iter().chain(d.after.iter()).chain(d.snaps.iter().flatten().map(|(_, r)| r)).chain(d.records.iter()) {
        raw.extend_from_slice(&d.body[r.clone()]);
    }
    let snaps = d.snaps.as_ref().map(|v| v.iter().map(|(u, r)| (u.as_str(), r.len())).collect::<Vec<_>>());
    let l = Layout {
        kind: &d.kind, seq: d.seq, time: d.time, label: &d.label, mode: &d.mode, target: &d.target, extra: &d.extra,
        n_deltas: d.deltas.len(), before, after, snaps, records, quick: false,
    };
    let new = assemble(&l, &raw, codec_id, true)?;
    drop(raw);
    if new.len() >= b.len() {
        return Ok(None);
    }
    let mut back = decode_v3(&new)?;
    if FAULT.load(std::sync::atomic::Ordering::Relaxed) {
        back.seq ^= 1; // tests: the read-back check must notice a step that came back different
    }
    if !same_dec(&d, &back) {
        return Ok(None);
    }
    Ok(Some(new))
}

// ---------------------------------------------------------------------------------- Python side
use pyo3::types::{PyByteArray, PyList, PyTuple};

fn err(e: StepErr) -> PyErr {
    match e {
        StepErr::Corrupt(s) => CorruptStep::new_err(s),
        StepErr::Value(s) => PyValueError::new_err(s),
        StepErr::Mem => pyo3::exceptions::PyMemoryError::new_err("hindsight_native: out of memory"),
    }
}

fn codec_err(e: codec::Err) -> PyErr {
    match e {
        codec::Err::Empty => PyValueError::new_err("empty blob"),
        codec::Err::Blob(s) => CorruptBlob::new_err(s),
        codec::Err::Mem => pyo3::exceptions::PyMemoryError::new_err("hindsight_native: out of memory"),
    }
}

fn no_mem() -> PyErr {
    pyo3::exceptions::PyMemoryError::new_err("hindsight_native: out of memory")
}

/// The bytes of an argument: borrowed from a `bytes` (immutable, safe to read without the GIL),
/// copied from anything else with the buffer protocol.
enum In<'a> {
    B(&'a [u8]),
    V(Vec<u8>),
}

impl In<'_> {
    fn get(&self) -> &[u8] {
        match self {
            In::B(b) => b,
            In::V(v) => v,
        }
    }
}

fn input<'a>(obj: &'a Bound<'_, PyAny>) -> PyResult<In<'a>> {
    if let Ok(b) = obj.downcast::<PyBytes>() {
        return Ok(In::B(b.as_bytes()));
    }
    let buf = Buf::new(obj, false)?;
    Ok(In::V(buf.bytes().to_vec()))
}

/// resolve(name): "auto" is zstd, a known name stays, anything else is zlib.
fn resolve_name(name: &str) -> u8 {
    let n = if name.is_empty() { "auto".to_string() } else { name.to_lowercase() };
    if n == "auto" {
        return codec::ZSTD;
    }
    codec::id_of(&n).unwrap_or(codec::ZLIB)
}

#[pyfunction]
#[pyo3(signature = (name=None))]
fn codec_resolve(name: Option<&str>) -> &'static str {
    codec::name_of(resolve_name(name.unwrap_or("auto")))
}

#[pyfunction]
#[pyo3(signature = (data, name="auto", strong=false))]
fn codec_compress<'py>(py: Python<'py>, data: &Bound<'py, PyAny>, name: &str, strong: bool) -> PyResult<Bound<'py, PyBytes>> {
    guard(|| {
        let inp = input(data)?;
        let b = inp.get();
        let id = resolve_name(name);
        let out = py.allow_threads(|| codec::compress(b, id, strong)).map_err(codec_err)?;
        Ok(PyBytes::new_bound(py, &out))
    })
}

#[pyfunction]
#[pyo3(signature = (blob, expected=None))]
fn codec_decompress<'py>(py: Python<'py>, blob: &Bound<'py, PyAny>, expected: Option<i128>) -> PyResult<Bound<'py, PyBytes>> {
    guard(|| {
        let inp = input(blob)?;
        let b = inp.get();
        let exp = match expected {
            Some(e) if e < 0 || e > codec::MAX_RAW as i128 => return Err(CorruptBlob::new_err("compressed data: implausible size")),
            Some(e) => Some(e as u64),
            None => None,
        };
        let out = py.allow_threads(|| codec::decompress(b, exp)).map_err(codec_err)?;
        Ok(PyBytes::new_bound(py, &out))
    })
}

fn snap_obj(py: Python<'_>, body: &[u8], r: &Range) -> PyResult<PyObject> {
    let b = &body[r.clone()];
    let d = parse_snapshot(b).map_err(PyValueError::new_err)?;
    let counts = PyList::empty_bound(py);
    for (k, v) in &d.counts {
        counts.append((k, *v))?;
    }
    let arrays = PyList::empty_bound(py);
    for (k, dt, rg) in &d.arrays {
        arrays.append((k, dt, PyByteArray::new_bound(py, &b[rg.clone()])))?;
    }
    Ok((counts, arrays).into_py(py))
}

/// snapshot_decode(blob) -> ([(count name, n)], [(key, dtype string, bytearray)]).
#[pyfunction]
fn snapshot_decode(py: Python<'_>, blob: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    guard(|| {
        let inp = input(blob)?;
        let b = inp.get();
        snap_obj(py, b, &(0..b.len()))
    })
}

fn opt_snap(py: Python<'_>, body: &[u8], r: &Option<Range>) -> PyResult<PyObject> {
    match r {
        Some(r) => snap_obj(py, body, r),
        None => Ok(py.None()),
    }
}

/// step_decode(blob) -> (version, seq, time, kind, label, mode, target, extra JSON bytes, written
/// quickly, [(key, add, exists before, exists after, k, width, itemsize, index bytearray, value
/// bytearray)], before, after, [(uid, snapshot)] or None, records JSON bytes or None). A snapshot is
/// (counts, arrays) as snapshot_decode returns it. Damage raises CorruptStep.
#[pyfunction]
fn step_decode(py: Python<'_>, blob: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    guard(|| {
        let inp = input(blob)?;
        let b = inp.get();
        let d = py.allow_threads(|| decode(b)).map_err(err)?;
        let deltas = PyList::empty_bound(py);
        for x in &d.deltas {
            let (ib, vb) = if x.k == 0 {
                (py.None(), py.None())
            } else {
                (PyByteArray::new_bound(py, &x.idx).into_py(py), PyByteArray::new_bound(py, &x.vals).into_py(py))
            };
            deltas.append((x.key.as_str(), x.add as u8, x.exists.0, x.exists.1, x.k, x.w, x.isz, ib, vb))?;
        }
        let snaps = match &d.snaps {
            Some(v) => {
                let l = PyList::empty_bound(py);
                for (uid, r) in v {
                    l.append((uid.as_str(), snap_obj(py, &d.body, r)?))?;
                }
                l.into_py(py)
            }
            None => py.None(),
        };
        let records = match &d.records {
            Some(r) => PyBytes::new_bound(py, &d.body[r.clone()]).into_py(py),
            None => py.None(),
        };
        let out: Vec<PyObject> = vec![
            d.version.into_py(py),
            d.seq.into_py(py),
            d.time.into_py(py),
            d.kind.as_str().into_py(py),
            d.label.as_str().into_py(py),
            d.mode.as_str().into_py(py),
            d.target.as_str().into_py(py),
            PyBytes::new_bound(py, &d.extra).into_py(py),
            d.quick.into_py(py),
            deltas.into_py(py),
            opt_snap(py, &d.body, &d.before)?,
            opt_snap(py, &d.body, &d.after)?,
            snaps,
            records,
        ];
        Ok(PyTuple::new_bound(py, out).into_py(py))
    })
}

/// step_info(blob): format 3 and 4 -> (3, kind, seq, time, label, mode, target, extra JSON bytes,
/// delta count, before size, after size, [(uid, size)] or None, raw body length, written quickly,
/// records size); format 1 and 2 -> (1, header JSON bytes). Checks the checksum, not the body.
#[pyfunction]
fn step_info(py: Python<'_>, blob: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    guard(|| {
        let inp = input(blob)?;
        let b = inp.get();
        match b.get(..4) {
            Some(m) if m == MAGIC_V1 => {
                let hl = u32le(b, 4).ok_or_else(|| CorruptStep::new_err("step blob: too short"))?;
                let hj = b.get(8..8 + hl).ok_or_else(|| CorruptStep::new_err("step blob: header cut short"))?;
                Ok((1u8, PyBytes::new_bound(py, hj)).into_py(py))
            }
            Some(m) if m == MAGIC_V3 => {
                let h = parse_head(b).map_err(err)?;
                let snaps = match &h.snaps {
                    Some(v) => v.iter().map(|(u, n)| (*u, *n)).collect::<Vec<_>>().into_py(py),
                    None => py.None(),
                };
                let out: Vec<PyObject> = vec![
                    3u8.into_py(py),
                    KINDS[h.kind as usize].into_py(py),
                    h.seq.into_py(py),
                    h.time.into_py(py),
                    h.label.into_py(py),
                    h.mode.into_py(py),
                    h.target.into_py(py),
                    PyBytes::new_bound(py, h.extra).into_py(py),
                    h.n_deltas.into_py(py),
                    h.before.into_py(py),
                    h.after.into_py(py),
                    snaps,
                    h.raw.into_py(py),
                    h.quick.into_py(py),
                    h.records.into_py(py),
                ];
                Ok(PyTuple::new_bound(py, out).into_py(py))
            }
            _ => Err(CorruptStep::new_err("step blob: not a Hindsight step (wrong header)")),
        }
    })
}

/// The bytes of a list of buffers, joined into `out`; also their total length.
fn join_parts(parts: &Bound<'_, PyAny>, out: &mut Vec<u8>) -> PyResult<usize> {
    let mut n = 0;
    for p in parts.iter()? {
        let buf = Buf::new(&p?, false)?;
        out.try_reserve(buf.len()).map_err(|_| no_mem())?;
        out.extend_from_slice(buf.bytes());
        n += buf.len();
    }
    Ok(n)
}

/// step_encode(kind, seq, time, label, mode, target, extra JSON bytes, deltas, before, after, snaps,
/// records, quick, codec, strong) -> the step blob. `deltas`, `before`, `after` and each snapshot of
/// `snaps` ([(uid, parts)]) are lists of buffers (packed records, snapshot blob pieces), `records`
/// is JSON bytes or None. The parts are copied once into the body; the codec and the checksum run
/// without the GIL.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn step_encode<'py>(
    py: Python<'py>,
    kind: &str,
    seq: u64,
    time: f64,
    label: &str,
    mode: &str,
    target: &str,
    extra: &Bound<'py, PyAny>,
    deltas: &Bound<'py, PyAny>,
    before: &Bound<'py, PyAny>,
    after: &Bound<'py, PyAny>,
    snaps: &Bound<'py, PyAny>,
    records: &Bound<'py, PyAny>,
    quick: bool,
    name: &str,
    strong: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    guard(|| {
        let ex = input(extra)?;
        let mut raw: Vec<u8> = Vec::new();
        let n_deltas = deltas.len()?;
        join_parts(deltas, &mut raw)?;
        let before_n = if before.is_none() { None } else { Some(join_parts(before, &mut raw)?) };
        let after_n = if after.is_none() { None } else { Some(join_parts(after, &mut raw)?) };
        let mut uids: Vec<(String, usize)> = Vec::new();
        let has_snaps = !snaps.is_none();
        if has_snaps {
            for it in snaps.iter()? {
                let (uid, sparts): (String, Bound<'_, PyAny>) = it?.extract()?;
                uids.push((uid, join_parts(&sparts, &mut raw)?));
            }
        }
        let rec_n = if records.is_none() {
            None
        } else {
            let r = input(records)?;
            raw.try_reserve(r.get().len()).map_err(|_| no_mem())?;
            raw.extend_from_slice(r.get());
            Some(r.get().len())
        };
        let l = Layout {
            kind,
            seq,
            time,
            label,
            mode,
            target,
            extra: ex.get(),
            n_deltas,
            before: before_n,
            after: after_n,
            snaps: if has_snaps { Some(uids.iter().map(|(u, n)| (u.as_str(), *n)).collect()) } else { None },
            records: rec_n,
            quick,
        };
        let id = resolve_name(name);
        let out = py.allow_threads(|| assemble(&l, &raw, id, strong)).map_err(err)?;
        Ok(PyBytes::new_bound(py, &out))
    })
}

/// Test hook: while on, recompress reads its result back as a different step.
#[pyfunction]
fn _test_fault(on: bool) {
    FAULT.store(on, std::sync::atomic::Ordering::Relaxed);
}

/// recompress(blob) -> a smaller blob for the same step, or None (see step.recompress).
#[pyfunction]
fn recompress<'py>(py: Python<'py>, blob: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyBytes>>> {
    guard(|| {
        let inp = input(blob)?;
        let b = inp.get();
        let r = py.allow_threads(|| recompress_core(b)).map_err(err)?;
        Ok(r.map(|v| PyBytes::new_bound(py, &v)))
    })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(codec_resolve, m)?)?;
    m.add_function(wrap_pyfunction!(codec_compress, m)?)?;
    m.add_function(wrap_pyfunction!(codec_decompress, m)?)?;
    m.add_function(wrap_pyfunction!(snapshot_decode, m)?)?;
    m.add_function(wrap_pyfunction!(step_decode, m)?)?;
    m.add_function(wrap_pyfunction!(step_info, m)?)?;
    m.add_function(wrap_pyfunction!(step_encode, m)?)?;
    m.add_function(wrap_pyfunction!(recompress, m)?)?;
    m.add_function(wrap_pyfunction!(_test_fault, m)?)?;
    let py = m.py();
    m.add("CorruptStep", py.get_type_bound::<CorruptStep>())?;
    m.add("CorruptBlob", py.get_type_bound::<CorruptBlob>())?;
    m.add("CorruptDelta", py.get_type_bound::<CorruptDelta>())?;
    m.add("NewerFormatError", py.get_type_bound::<NewerFormatError>())?;
    Ok(())
}
