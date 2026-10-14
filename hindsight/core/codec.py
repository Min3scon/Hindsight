"""Byte compression with optional zstd / lz4 and an always-available zlib fallback.

Blob format: first byte = codec id, then payload. Ids: 0 none, 1 zlib level 1, 2 lz4 frame,
3 zstd level 3, 4 zstd level 19, 5 zlib level 9. Ids 4 and 5 are the "strong" forms written by
background recompression; they decode exactly like 3 and 1.
Pure Python, no bpy.
"""
from __future__ import annotations

import threading
import zlib

try:  # optional wheel
    import zstandard as _zstd
except Exception:  # pragma: no cover - depends on environment
    _zstd = None

try:  # optional wheel
    import lz4.frame as _lz4f
except Exception:  # pragma: no cover
    _lz4f = None

NONE, ZLIB, LZ4, ZSTD, ZSTD_MAX, ZLIB_MAX = 0, 1, 2, 3, 4, 5
_IDS = {"none": NONE, "zlib": ZLIB, "lz4": LZ4, "zstd": ZSTD}
_NAMES = {v: k for k, v in _IDS.items()}
_NAMES.update({ZSTD_MAX: "zstd", ZLIB_MAX: "zlib"})
STRONG_LEVEL = 19
# largest body a blob may claim, so a damaged length can't ask for a huge allocation
MAX_RAW = 1 << 30


class CorruptBlob(ValueError):
    """Compressed data is damaged."""

_tls = threading.local()


def available() -> list:
    """Codecs usable in this interpreter, in preference order."""
    out = []
    if _zstd is not None:
        out.append("zstd")
    if _lz4f is not None:
        out.append("lz4")
    out += ["zlib", "none"]
    return out


def resolve(name: str) -> str:
    """'auto' -> best available; unknown or unavailable -> 'zlib'."""
    n = (name or "auto").lower()
    if n == "auto":
        return available()[0]
    if n in _IDS and n in available():
        return n
    return "zlib"


def _zc():
    c = getattr(_tls, "zc", None)
    if c is None:
        c = _tls.zc = _zstd.ZstdCompressor(level=3)
    return c


def _zc_max():
    c = getattr(_tls, "zc_max", None)
    if c is None:
        c = _tls.zc_max = _zstd.ZstdCompressor(level=STRONG_LEVEL)
    return c


def _zd():
    d = getattr(_tls, "zd", None)
    if d is None:
        d = _tls.zd = _zstd.ZstdDecompressor()
    return d


def compress(data, codec: str = "auto", strong: bool = False) -> bytes:
    name = resolve(codec)
    data = bytes(data) if not isinstance(data, (bytes, bytearray, memoryview)) else data
    if name == "zstd":
        if strong:
            return bytes((ZSTD_MAX,)) + _zc_max().compress(data)
        return bytes((ZSTD,)) + _zc().compress(data)
    if name == "lz4":
        return bytes((LZ4,)) + _lz4f.compress(data)
    if name == "none":
        return bytes((NONE,)) + bytes(data)
    if strong:
        return bytes((ZLIB_MAX,)) + zlib.compress(data, 9)
    return bytes((ZLIB,)) + zlib.compress(data, 1)


def is_strong(blob) -> bool:
    """True if the blob can't get smaller by recompressing (strong form, or stored as is)."""
    return memoryview(blob)[0] in (ZSTD_MAX, ZLIB_MAX, NONE)


def decompress(blob, expected: int | None = None) -> bytes:
    """Payload bytes. With `expected`, the result must be exactly that long (checked before any
    large allocation), and damage raises CorruptBlob."""
    mv = memoryview(blob)
    if len(mv) == 0:
        raise ValueError("empty blob")
    cid = mv[0]
    payload = mv[1:]
    if expected is not None and (expected < 0 or expected > MAX_RAW
                                 or expected > 65536 * len(payload) + 65536):
        raise CorruptBlob("compressed data: implausible size")
    try:
        if cid == NONE:
            out = bytes(payload)
        elif cid in (ZLIB, ZLIB_MAX):
            if expected is None:
                return zlib.decompress(payload)
            d = zlib.decompressobj()
            out = d.decompress(payload, expected + 1)
            if not d.eof or d.unconsumed_tail:
                raise CorruptBlob("compressed data: zlib stream damaged")
        elif cid in (ZSTD, ZSTD_MAX):
            if _zstd is None:
                raise RuntimeError("blob is zstd-compressed but zstandard is not installed")
            if expected is None:
                # content size is written by ZstdCompressor.compress, so one-shot decompress works
                return _zd().decompress(payload)
            if _zstd.frame_content_size(payload) != expected:
                raise CorruptBlob("compressed data: zstd size doesn't match")
            out = _zd().decompress(payload)
        elif cid == LZ4:
            if _lz4f is None:
                raise RuntimeError("blob is lz4-compressed but lz4 is not installed")
            out = _lz4f.decompress(bytes(payload))
        else:
            raise CorruptBlob(f"unknown codec id {cid}")
    except (CorruptBlob, RuntimeError):
        raise
    except Exception as e:  # zlib.error, ZstdError, lz4 errors
        raise CorruptBlob(f"compressed data damaged: {e}") from e
    if expected is not None and len(out) != expected:
        raise CorruptBlob("compressed data: wrong length")
    return out


def codec_of(blob) -> str:
    """Name of the codec a blob was written with."""
    return _NAMES.get(memoryview(blob)[0], "?")
