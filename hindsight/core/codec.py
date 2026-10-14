"""Byte compression with optional zstd / lz4 and an always-available zlib fallback.

Blob format: first byte = codec id (0 none, 1 zlib level 1, 2 lz4 frame, 3 zstd level 3), then payload.
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

NONE, ZLIB, LZ4, ZSTD = 0, 1, 2, 3
_IDS = {"none": NONE, "zlib": ZLIB, "lz4": LZ4, "zstd": ZSTD}
_NAMES = {v: k for k, v in _IDS.items()}

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


def _zd():
    d = getattr(_tls, "zd", None)
    if d is None:
        d = _tls.zd = _zstd.ZstdDecompressor()
    return d


def compress(data, codec: str = "auto") -> bytes:
    name = resolve(codec)
    data = bytes(data) if not isinstance(data, (bytes, bytearray, memoryview)) else data
    if name == "zstd":
        return bytes((ZSTD,)) + _zc().compress(data)
    if name == "lz4":
        return bytes((LZ4,)) + _lz4f.compress(data)
    if name == "none":
        return bytes((NONE,)) + bytes(data)
    return bytes((ZLIB,)) + zlib.compress(data, 1)


def decompress(blob) -> bytes:
    mv = memoryview(blob)
    if len(mv) == 0:
        raise ValueError("empty blob")
    cid = mv[0]
    payload = mv[1:]
    if cid == NONE:
        return bytes(payload)
    if cid == ZLIB:
        return zlib.decompress(payload)
    if cid == ZSTD:
        if _zstd is None:
            raise RuntimeError("blob is zstd-compressed but zstandard is not installed")
        # content size is written by ZstdCompressor.compress, so one-shot decompress works
        return _zd().decompress(payload)
    if cid == LZ4:
        if _lz4f is None:
            raise RuntimeError("blob is lz4-compressed but lz4 is not installed")
        return _lz4f.decompress(bytes(payload))
    raise ValueError(f"unknown codec id {cid}")


def codec_of(blob) -> str:
    """Name of the codec a blob was written with."""
    return _NAMES.get(memoryview(blob)[0], "?")
