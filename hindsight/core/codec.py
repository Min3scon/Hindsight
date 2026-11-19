"""Byte compression: zstd, lz4, zlib and none, all done by the Rust engine (native/src/codec.rs).

Blob format: first byte = codec id, then payload. Ids: 0 none, 1 zlib level 1, 2 lz4 frame,
3 zstd level 3, 4 zstd level 19, 5 zlib level 9. Ids 4 and 5 are the "strong" forms written by
background recompression; they decode exactly like 3 and 1. This module only names things and
calls the engine. Pure Python, no bpy.
"""
from __future__ import annotations

from . import native as _native

_N = _native.ENGINE

NONE, ZLIB, LZ4, ZSTD, ZSTD_MAX, ZLIB_MAX = 0, 1, 2, 3, 4, 5
_NAMES = {NONE: "none", ZLIB: "zlib", LZ4: "lz4", ZSTD: "zstd", ZSTD_MAX: "zstd", ZLIB_MAX: "zlib"}
STRONG_LEVEL = 19
# largest body a blob may claim, so a damaged length can't ask for a huge allocation
MAX_RAW = 1 << 30

CorruptBlob = _N.CorruptBlob


def available() -> list:
    """Codecs usable, in preference order (all four are inside the engine)."""
    return ["zstd", "lz4", "zlib", "none"]


def resolve(name: str) -> str:
    """'auto' -> zstd; unknown -> 'zlib'."""
    return _N.codec_resolve(name)


def compress(data, codec: str = "auto", strong: bool = False) -> bytes:
    return _N.codec_compress(data, codec, strong)


def is_strong(blob) -> bool:
    """True if the blob can't get smaller by recompressing (strong form, or stored as is)."""
    return memoryview(blob)[0] in (ZSTD_MAX, ZLIB_MAX, NONE)


def decompress(blob, expected: int | None = None) -> bytes:
    """Payload bytes. With `expected`, the result must be exactly that long (checked before any
    large allocation), and damage raises CorruptBlob."""
    return _N.codec_decompress(blob, expected)


def codec_of(blob) -> str:
    """Name of the codec a blob was written with."""
    return _NAMES.get(memoryview(blob)[0], "?")
