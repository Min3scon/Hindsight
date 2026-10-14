"""Bit-exact XOR channel deltas. Pure numpy, fully vectorised, no bpy.

A delta stores, for the elements that changed, the XOR of the old and new bit
patterns. Applying it flips old -> new and new -> old, so one record serves
both undo and redo.
"""
from __future__ import annotations

import struct

import numpy as np

from .types import ChannelDelta, bits_dtype, key_width

_U32 = np.uint32
_EMPTY_IDX = np.zeros(0, dtype=_U32)

# pack_delta header: magic, flags(exists bits), xor itemsize, width, idx-diff itemsize,
#                    key length, k, first index
_HDR = struct.Struct("<2sBBBBHII")
_MAGIC = b"HD"
_IDX_DT = {1: np.uint8, 2: np.uint16, 4: np.uint32}


def view_bits(arr: np.ndarray) -> np.ndarray:
    """Flat native array -> same memory viewed as unsigned bits (bool -> uint8)."""
    a = arr.reshape(-1)
    return a.view(bits_dtype(a.dtype))


def _changed_mask(x: np.ndarray) -> np.ndarray:
    """x: (n, w) unsigned -> bool (n,) True where any column is non-zero."""
    w = x.shape[1]
    if w == 1:
        return x[:, 0] != 0
    m = x[:, 0] | x[:, 1]
    for j in range(2, w):          # loop over width (<= 4), not elements
        m |= x[:, j]
    return m != 0


def diff(key: str, old: np.ndarray, new: np.ndarray, exists=(True, True)):
    """Delta old -> new for channel `key`. None if bit-identical and existence unchanged."""
    if old.size != new.size:
        raise ValueError(f"diff {key}: size mismatch {old.size} != {new.size}")
    if np.dtype(old.dtype).itemsize != np.dtype(new.dtype).itemsize:
        raise ValueError(f"diff {key}: dtype mismatch {old.dtype} vs {new.dtype}")
    w = key_width(key)
    exists = (bool(exists[0]), bool(exists[1]))
    ob = view_bits(old).reshape(-1, w)
    nb = view_bits(new).reshape(-1, w)
    x = ob ^ nb
    idx = np.flatnonzero(_changed_mask(x)).astype(_U32, copy=False)
    if idx.size == 0:
        if exists[0] == exists[1]:
            return None
        return ChannelDelta(key, _EMPTY_IDX.copy(), np.zeros((0, w), dtype=ob.dtype), exists)
    return ChannelDelta(key, idx, x[idx], exists)


def apply(arr: np.ndarray, d: ChannelDelta) -> None:
    """In place: view_bits(arr).reshape(-1, width)[d.idx] ^= d.xor."""
    if d.idx.size == 0:
        return
    if not arr.flags.c_contiguous:
        raise ValueError("apply: array must be C-contiguous (in-place update)")
    w = d.xor.shape[1] if d.xor.ndim == 2 else key_width(d.key)
    b = view_bits(arr).reshape(-1, w)
    if b.dtype != d.xor.dtype:
        raise ValueError(f"apply {d.key}: bits dtype {b.dtype} != delta {d.xor.dtype}")
    b[d.idx] ^= d.xor.reshape(-1, w)


def pack_delta(d: ChannelDelta) -> bytes:
    """Uncompressed binary: header + key + delta-encoded indices + byte-shuffled xor planes."""
    key_b = d.key.encode("utf-8")
    idx = np.ascontiguousarray(d.idx, dtype=_U32).reshape(-1)
    k = int(idx.size)
    xor = d.xor
    w = xor.shape[1] if xor.ndim == 2 else key_width(d.key)
    isz = np.dtype(xor.dtype).itemsize
    flags = (1 if d.exists[0] else 0) | (2 if d.exists[1] else 0)
    if k > 1:
        dif = np.diff(idx)
        m = int(dif.max())
        dsz = 1 if m <= 0xFF else (2 if m <= 0xFFFF else 4)
        dif_b = dif.astype(_IDX_DT[dsz], copy=False).tobytes()
    else:
        dsz, dif_b = 1, b""
    first = int(idx[0]) if k else 0
    hdr = _HDR.pack(_MAGIC, flags, isz, w, dsz, len(key_b), k, first)
    if k:
        planes = np.ascontiguousarray(xor).view(np.uint8).reshape(k, isz * w).T.tobytes()
    else:
        planes = b""
    return b"".join((hdr, key_b, dif_b, planes))


def unpack_delta(b) -> ChannelDelta:
    mv = memoryview(b)
    magic, flags, isz, w, dsz, klen, k, first = _HDR.unpack_from(mv, 0)
    if magic != _MAGIC:
        raise ValueError("unpack_delta: bad magic")
    off = _HDR.size
    key = bytes(mv[off:off + klen]).decode("utf-8")
    off += klen
    bdt = np.dtype(np.uint8 if isz == 1 else {2: np.uint16, 4: np.uint32, 8: np.uint64}[isz])
    exists = (bool(flags & 1), bool(flags & 2))
    if k == 0:
        return ChannelDelta(key, _EMPTY_IDX.copy(), np.zeros((0, w), dtype=bdt), exists)
    idx = np.empty(k, dtype=_U32)
    idx[0] = first
    if k > 1:
        n = (k - 1) * dsz
        dif = np.frombuffer(mv[off:off + n], dtype=_IDX_DT[dsz])
        off += n
        np.cumsum(dif, dtype=_U32, out=idx[1:])
        idx[1:] += _U32(first)
    nx = k * isz * w
    planes = np.frombuffer(mv[off:off + nx], dtype=np.uint8).reshape(isz * w, k)
    xor = np.ascontiguousarray(planes.T).view(bdt).reshape(k, w)
    return ChannelDelta(key, idx, xor, exists)


def packed_size(b, offset: int = 0) -> int:
    """Byte length of the packed delta starting at `offset` (for streaming parsers)."""
    _m, _f, isz, w, dsz, klen, k, _first = _HDR.unpack_from(b, offset)
    return _HDR.size + klen + max(k - 1, 0) * dsz + k * isz * w
