//! Byte compression: the twin of hindsight/core/codec.py (now in tests/reference).
//!
//! Blob = one codec id byte, then the payload. Ids: 0 none, 1 zlib level 1, 2 lz4 frame, 3 zstd
//! level 3, 4 zstd level 19, 5 zlib level 9. Ids 4 and 5 ("strong", written by background
//! recompression) decode exactly like 3 and 1.
//!
//! All three libraries are C, built from source (zstd-sys, libz-sys, lz4-sys), so the bytes written
//! are those of the libraries Python used. Reading takes the first frame or stream and ignores
//! what follows it, as the Python readers did. Sizes are bounded before any allocation.

use std::cell::RefCell;
use std::os::raw::c_void;

pub const NONE: u8 = 0;
pub const ZLIB: u8 = 1;
pub const LZ4: u8 = 2;
pub const ZSTD: u8 = 3;
pub const ZSTD_MAX: u8 = 4;
pub const ZLIB_MAX: u8 = 5;
pub const STRONG_LEVEL: i32 = 19;
/// Largest body a blob may claim, so a damaged length can't ask for a huge allocation.
pub const MAX_RAW: u64 = 1 << 30;

pub enum Err {
    /// A blob of no bytes (ValueError in Python, not a damaged blob).
    Empty,
    /// Damaged data (CorruptBlob).
    Blob(String),
    /// An allocation failed.
    Mem,
}

fn blob(s: &str) -> Err {
    Err::Blob(s.to_string())
}

fn damaged(e: impl std::fmt::Display) -> Err {
    Err::Blob(format!("compressed data damaged: {e}"))
}

pub fn id_of(name: &str) -> Option<u8> {
    match name {
        "none" => Some(NONE),
        "zlib" => Some(ZLIB),
        "lz4" => Some(LZ4),
        "zstd" => Some(ZSTD),
        _ => None,
    }
}

pub fn name_of(id: u8) -> &'static str {
    match id {
        NONE => "none",
        ZLIB | ZLIB_MAX => "zlib",
        LZ4 => "lz4",
        ZSTD | ZSTD_MAX => "zstd",
        _ => "?",
    }
}

/// True if the blob (codec id first) can't get smaller by recompressing.
pub fn is_strong(id: u8) -> bool {
    matches!(id, ZSTD_MAX | ZLIB_MAX | NONE)
}

fn vec_with(n: usize) -> Result<Vec<u8>, Err> {
    let mut v = Vec::new();
    v.try_reserve_exact(n).map_err(|_| Err::Mem)?;
    Ok(v)
}

thread_local! {
    static CCTX: RefCell<Option<(zstd_safe::CCtx<'static>, zstd_safe::CCtx<'static>)>> = const { RefCell::new(None) };
    pub static DCTX: RefCell<Option<zstd_safe::DCtx<'static>>> = const { RefCell::new(None) };
}

fn zstd_compress(data: &[u8], strong: bool) -> Result<Vec<u8>, Err> {
    CCTX.with(|c| {
        let mut g = c.borrow_mut();
        if g.is_none() {
            let mk = |level: i32| -> Result<zstd_safe::CCtx<'static>, Err> {
                let mut x = zstd_safe::CCtx::create();
                x.set_parameter(zstd_safe::CParameter::CompressionLevel(level))
                    .map_err(|_| blob("zstd: bad level"))?;
                Ok(x)
            };
            *g = Some((mk(3)?, mk(STRONG_LEVEL)?));
        }
        let (n, s) = g.as_mut().unwrap();
        let ctx = if strong { s } else { n };
        let bound = zstd_safe::compress_bound(data.len());
        let mut out = vec_with(1 + bound)?;
        out.resize(1 + bound, 0);
        out[0] = if strong { ZSTD_MAX } else { ZSTD };
        let n = ctx.compress2(&mut out[1..], data).map_err(|c| damaged(zstd_safe::get_error_name(c)))?;
        out.truncate(1 + n);
        Ok(out)
    })
}

#[repr(C)]
struct Lz4Info {
    block_size_id: u32,
    block_mode: u32,
    content_checksum: u32,
    frame_type: u32,
    content_size: u64,
    dict_id: u32,
    block_checksum: u32,
}

#[repr(C)]
struct Lz4Prefs {
    info: Lz4Info,
    level: i32,
    auto_flush: u32,
    favor_dec_speed: u32,
    reserved: [u32; 3],
}

extern "C" {
    fn LZ4F_compressFrameBound(src_size: usize, prefs: *const Lz4Prefs) -> usize;
    fn LZ4F_compressFrame(dst: *mut c_void, cap: usize, src: *const c_void, n: usize, prefs: *const Lz4Prefs) -> usize;
}

fn lz4_compress(data: &[u8]) -> Result<Vec<u8>, Err> {
    // what lz4.frame.compress writes by default: 64 KB linked blocks, no checksums, size stored
    let prefs = Lz4Prefs {
        info: Lz4Info {
            block_size_id: 0,
            block_mode: 0,
            content_checksum: 0,
            frame_type: 0,
            content_size: data.len() as u64,
            dict_id: 0,
            block_checksum: 0,
        },
        level: 0,
        auto_flush: 0,
        favor_dec_speed: 0,
        reserved: [0; 3],
    };
    // SAFETY: the bound and the frame are computed from valid slices and a valid preferences struct.
    unsafe {
        let bound = LZ4F_compressFrameBound(data.len(), &prefs);
        let mut out = vec_with(bound.checked_add(1).ok_or(Err::Mem)?)?;
        out.push(LZ4);
        let n = LZ4F_compressFrame(out.as_mut_ptr().add(1) as *mut c_void, bound, data.as_ptr() as *const c_void, data.len(), &prefs);
        if lz4_sys::LZ4F_isError(n) != 0 {
            return Err(blob("compressed data damaged: lz4 failed"));
        }
        out.set_len(1 + n);
        Ok(out)
    }
}

fn zlib_compress(data: &[u8], strong: bool) -> Result<Vec<u8>, Err> {
    let level = if strong { 9 } else { 1 };
    let n = data.len();
    let bound = n + (n >> 12) + (n >> 14) + (n >> 25) + 64;
    let mut out = vec_with(bound + 1)?;
    out.push(if strong { ZLIB_MAX } else { ZLIB });
    let mut c = flate2::Compress::new(flate2::Compression::new(level), true);
    loop {
        match c.compress_vec(&data[c.total_in() as usize..], &mut out, flate2::FlushCompress::Finish) {
            Ok(flate2::Status::StreamEnd) => return Ok(out),
            Ok(_) => out.try_reserve(out.capacity() / 2 + 64).map_err(|_| Err::Mem)?,
            Err(e) => return Err(damaged(e)),
        }
    }
}

/// codec id byte + compressed `data`. `id` is NONE, ZLIB, LZ4 or ZSTD; `strong` picks the
/// slower, smaller setting of zlib and zstd (ignored by the others).
pub fn compress(data: &[u8], id: u8, strong: bool) -> Result<Vec<u8>, Err> {
    match id {
        ZSTD => zstd_compress(data, strong),
        LZ4 => lz4_compress(data),
        NONE => {
            let mut out = vec_with(data.len() + 1)?;
            out.push(NONE);
            out.extend_from_slice(data);
            Ok(out)
        }
        _ => zlib_compress(data, strong),
    }
}

fn zstd_decompress(data: &[u8], expected: Option<u64>) -> Result<Vec<u8>, Err> {
    let fs = zstd_safe::find_frame_compressed_size(data).map_err(|c| damaged(zstd_safe::get_error_name(c)))?;
    let frame = &data[..fs];
    let size = match zstd_safe::get_frame_content_size(frame) {
        Ok(Some(n)) => n,
        Ok(None) => {
            return Err(match expected {
                Some(_) => blob("compressed data: zstd size doesn't match"),
                None => damaged("could not determine content size in frame header"),
            })
        }
        Err(_) => return Err(damaged("bad zstd frame header")),
    };
    if let Some(e) = expected {
        if size != e {
            return Err(blob("compressed data: zstd size doesn't match"));
        }
    } else if size > MAX_RAW || size > 65536 * data.len() as u64 + 65536 {
        return Err(blob("compressed data: implausible size"));
    }
    let mut out = vec_with(size as usize)?;
    DCTX.with(|d| {
        let mut g = d.borrow_mut();
        let ctx = g.get_or_insert_with(zstd_safe::DCtx::create);
        ctx.decompress(&mut out, frame).map_err(|c| damaged(zstd_safe::get_error_name(c)))
    })?;
    Ok(out)
}

fn zlib_decompress(data: &[u8], expected: Option<u64>) -> Result<Vec<u8>, Err> {
    // with `expected`: at most expected + 1 bytes, and the stream must end
    let limit = match expected {
        Some(e) => e + 1,
        None => MAX_RAW + 1,
    };
    let mut out = vec_with((expected.unwrap_or(data.len() as u64 * 4).min(MAX_RAW) as usize) + 1)?;
    let mut d = flate2::Decompress::new(true);
    loop {
        let before = d.total_out();
        let r = d.decompress_vec(&data[d.total_in() as usize..], &mut out, flate2::FlushDecompress::None);
        match r {
            Ok(flate2::Status::StreamEnd) => break,
            Ok(_) => {
                if out.len() as u64 >= limit {
                    return Err(match expected {
                        Some(_) => blob("compressed data: zlib stream damaged"),
                        None => blob("compressed data: implausible size"),
                    });
                }
                if d.total_out() == before && d.total_in() as usize >= data.len() {
                    // input used up, no end of stream
                    return Err(match expected {
                        Some(_) => blob("compressed data: zlib stream damaged"),
                        None => damaged("Error -5 while decompressing data: incomplete or truncated stream"),
                    });
                }
                if out.len() == out.capacity() {
                    let more = (out.capacity() / 2 + 64).min((limit as usize).saturating_sub(out.capacity()).max(1));
                    out.try_reserve(more).map_err(|_| Err::Mem)?;
                }
            }
            Err(e) => return Err(damaged(e)),
        }
    }
    if out.len() as u64 >= limit {
        return Err(blob("compressed data: wrong length"));
    }
    Ok(out)
}

fn lz4_decompress(data: &[u8], expected: Option<u64>) -> Result<Vec<u8>, Err> {
    use lz4_sys::*;
    struct Ctx(LZ4FDecompressionContext);
    impl Drop for Ctx {
        fn drop(&mut self) {
            // SAFETY: created by LZ4F_createDecompressionContext, freed once.
            unsafe { LZ4F_freeDecompressionContext(LZ4FDecompressionContext(self.0 .0)) };
        }
    }
    let mut raw = LZ4FDecompressionContext(std::ptr::null_mut());
    // SAFETY: standard LZ4F context creation; the version number is the API's.
    let rc = unsafe { LZ4F_createDecompressionContext(&mut raw, 100) };
    if unsafe { LZ4F_isError(rc) } != 0 {
        return Err(Err::Mem);
    }
    let ctx = Ctx(raw);
    let limit = expected.unwrap_or(MAX_RAW) + 1;
    let mut out = vec_with((expected.unwrap_or(data.len() as u64 * 4).min(MAX_RAW) as usize) + 1)?;
    let mut pos = 0usize;
    loop {
        if out.len() == out.capacity() {
            let more = (out.capacity() / 2 + 64).min((limit as usize).saturating_sub(out.capacity()).max(1));
            out.try_reserve(more).map_err(|_| Err::Mem)?;
        }
        let spare = out.capacity() - out.len();
        let mut dst_n = spare;
        let mut src_n = data.len() - pos;
        // SAFETY: dst is the spare capacity of `out`, src the unread part of `data`; both sizes are
        // in/out parameters of LZ4F_decompress, which writes at most dst_n bytes and reads src_n.
        let r = unsafe {
            LZ4F_decompress(
                LZ4FDecompressionContext(ctx.0 .0),
                out.as_mut_ptr().add(out.len()),
                &mut dst_n,
                data.as_ptr().add(pos),
                &mut src_n,
                std::ptr::null(),
            )
        };
        if unsafe { LZ4F_isError(r) } != 0 {
            let name = unsafe { std::ffi::CStr::from_ptr(LZ4F_getErrorName(r)) }.to_string_lossy().into_owned();
            return Err(damaged(name));
        }
        // SAFETY: LZ4F_decompress initialised dst_n bytes.
        unsafe { out.set_len(out.len() + dst_n) };
        pos += src_n;
        if out.len() as u64 >= limit {
            return Err(match expected {
                Some(_) => blob("compressed data: wrong length"),
                None => blob("compressed data: implausible size"),
            });
        }
        if r == 0 {
            break; // frame complete
        }
        // not done: more input is needed, or more room for output (the decoder filled `spare`)
        if (dst_n < spare && pos >= data.len()) || (src_n == 0 && dst_n == 0) {
            return Err(damaged("Frame incomplete"));
        }
    }
    Ok(out)
}

/// The payload of a blob (codec id first). With `expected`, the result is exactly that long.
pub fn decompress(blob_: &[u8], expected: Option<u64>) -> Result<Vec<u8>, Err> {
    let (&cid, payload) = blob_.split_first().ok_or(Err::Empty)?;
    if let Some(e) = expected {
        if e > MAX_RAW || e > 65536 * payload.len() as u64 + 65536 {
            return Err(blob("compressed data: implausible size"));
        }
    }
    let out = match cid {
        NONE => {
            let mut v = vec_with(payload.len())?;
            v.extend_from_slice(payload);
            v
        }
        ZLIB | ZLIB_MAX => zlib_decompress(payload, expected)?,
        ZSTD | ZSTD_MAX => zstd_decompress(payload, expected)?,
        LZ4 => lz4_decompress(payload, expected)?,
        _ => return Err(Err::Blob(format!("unknown codec id {cid}"))),
    };
    if let Some(e) = expected {
        if out.len() as u64 != e {
            return Err(blob("compressed data: wrong length"));
        }
    }
    Ok(out)
}
