"""Bit-exact channel deltas, computed by the Rust engine (native/src/lib.rs). No bpy.

A delta stores, for the elements that changed, how their bit patterns changed. New steps store the
wrapping difference new - old (op ADD): stepping forward adds it, stepping back subtracts it.
Histories written by format 1 and 2 store old ^ new (op XOR), which is applied the same way in
both directions. Both are exact for every bit pattern, NaN payloads, -0 and denormals included.

Packed record, format 3 (b"HE"), uncompressed; the step compresses all its records together:

  header  <2s B B B B H I I>  magic, flags (bit 0 existed before, bit 1 exists after, bit 2 op ADD),
          value itemsize, width, index coding, key length, k, index byte length
  key     utf-8
  index   coding 0, gaps:   u8 gap width (1, 2, 4), u32 first, k - 1 values (gap - 1)
          coding 1, runs:   u8 widths (gap | length << 4), u32 run count, u32 first,
                            run count - 1 values (gap between runs - 1), run count values (length - 1)
          coding 2, bitmap: u32 first, u32 span, ceil(span / 8) bytes, bit i = element first + i
  values  only when k > 0: one predictor tag per component, then k * width * itemsize bytes as
          byte planes, component-major (all low bytes of component 0, then its next byte, ...).
          Tag 255: the values as stored. Tag 0-3 (op ADD only): the values differenced that many
          times along the record (wrapping), then zigzag-mapped so small changes of either sign
          become small numbers.

Records always decode to sorted, unique indices: gaps and lengths are stored minus one.
Format 1 and 2 records (b"HD", XOR) are still read; they are never written any more.

diff, apply, pack and unpack all run in the engine. This module checks the arguments (types,
shapes, contiguity: the errors Python callers see) and wraps buffers into ChannelDelta objects. The
numpy implementation it replaced is tests/reference/delta.py, the oracle of the equivalence tests.
"""
from __future__ import annotations

import struct

import numpy as np

from . import native as _native
from .types import ADD, XOR, ChannelDelta, key_width

_N = _native.ENGINE
_U32 = np.uint32
_EMPTY_IDX = np.zeros(0, dtype=_U32)
_BITS_BY_SIZE = {1: np.uint8, 2: np.uint16, 4: np.uint32, 8: np.uint64}

# format 1 and 2: magic, flags, xor itemsize, width, idx-diff itemsize, key length, k, first index
_HDR_V1 = struct.Struct("<2sBBBBHII")
_MAGIC_V1 = b"HD"
# format 3
_HDR = struct.Struct("<2sBBBBHII")
_MAGIC = b"HE"

IDX_GAPS, IDX_RUNS, IDX_BITMAP = 0, 1, 2
TAG_RAW = 255
MAX_ORDER = 3
MAX_WIDTH = 16

# encoder effort
QUICK = 0     # crash-recovery journal, on the main thread: values stored as they are
NORMAL = 1    # packing and saving: per component, the order with the smallest estimated size

CorruptDelta = _N.CorruptDelta     # ValueError: a packed delta is damaged or not a delta


def view_bits(arr: np.ndarray) -> np.ndarray:
    """Flat native array -> same memory viewed as unsigned bits (bool -> uint8)."""
    if arr.dtype == np.bool_:
        return arr.view(np.uint8)
    return arr.view(_BITS_BY_SIZE[arr.dtype.itemsize])


DIFF_THREADS = [None]   # R071: threads for the engine's diff (None: its own rule, two from 4 MB); tests and A/B set it


def diff(key: str, old: np.ndarray, new: np.ndarray, exists=(True, True)):
    """Delta old -> new for channel `key` (op ADD). None if bit-identical and existence unchanged."""
    if old.size != new.size:
        raise ValueError(f"diff {key}: size mismatch {old.size} != {new.size}")
    if np.dtype(old.dtype).itemsize != np.dtype(new.dtype).itemsize:
        raise ValueError(f"diff {key}: dtype mismatch {old.dtype} vs {new.dtype}")
    w = key_width(key)
    exists = (bool(exists[0]), bool(exists[1]))
    ob = np.ascontiguousarray(view_bits(old)).reshape(-1, w)
    nb = np.ascontiguousarray(view_bits(new)).reshape(-1, w)
    r = _N.diff(ob, nb, ob.dtype.itemsize, w, DIFF_THREADS[0])
    if r is not None:
        return ChannelDelta(key, np.frombuffer(r[0], _U32), np.frombuffer(r[1], ob.dtype).reshape(-1, w),
                            exists, ADD)
    if exists[0] == exists[1]:
        return None
    return ChannelDelta(key, _EMPTY_IDX.copy(), np.zeros((0, w), dtype=ob.dtype), exists, ADD)


def apply(arr: np.ndarray, d: ChannelDelta, forward: bool = True) -> None:
    """In place: move `arr` across the delta (old -> new if forward, else new -> old)."""
    if d.idx.size == 0:
        return
    if not arr.flags.c_contiguous:
        raise ValueError("apply: array must be C-contiguous (in-place update)")
    v = d.vals
    w = v.shape[1] if v.ndim == 2 else key_width(d.key)
    b = view_bits(arr).reshape(-1, w)
    if b.dtype != v.dtype:
        raise ValueError(f"apply {d.key}: bits dtype {b.dtype} != delta {v.dtype}")
    if not b.flags.writeable:
        raise ValueError("assignment destination is read-only")     # numpy's own words, as before
    idx = np.ascontiguousarray(d.idx, dtype=_U32).reshape(-1)
    v = v.reshape(-1, w)
    if v.shape[0] == 1 and idx.size > 1:
        v = np.broadcast_to(v, (idx.size, w))        # one row of values for every index (as numpy did)
    v = np.ascontiguousarray(v)
    if v.shape[0] != idx.size:
        raise ValueError(f"apply {d.key}: {idx.size} indices but {v.shape[0]} values")
    try:
        _N.apply(b, idx, v, b.dtype.itemsize, w, d.op == XOR, forward)
    except ValueError as e:
        raise ValueError(f"apply {d.key}: {e}") from None


def as_xor(d: ChannelDelta, before: np.ndarray) -> np.ndarray:
    """old ^ new of the changed elements, given the array the delta starts from (forward)."""
    if d.op == XOR or d.idx.size == 0:
        return d.vals
    w = d.vals.shape[1]
    ob = view_bits(before).reshape(-1, w)[d.idx]
    return ob ^ (ob + d.vals)


def same_change(seen: ChannelDelta, rec: ChannelDelta, before: np.ndarray, inverse: bool) -> bool:
    """True if `seen` (diffed from `before`) is exactly `rec` undone (inverse) or redone."""
    if not np.array_equal(seen.idx, rec.idx):
        return False
    if seen.op == ADD and rec.op == ADD:
        want = (np.zeros_like(rec.vals) - rec.vals) if inverse else rec.vals
        return seen.vals.shape == want.shape and np.array_equal(seen.vals, want)
    # a format 2 step (XOR) is the same change in both directions
    a = as_xor(seen, before)
    b = rec.vals if rec.op == XOR else None
    if b is None:
        return False
    return a.shape == b.shape and np.array_equal(a, b)


def pack_delta(d: ChannelDelta, effort: int = NORMAL) -> bytes:
    """Format 3 record."""
    idx = np.ascontiguousarray(d.idx, dtype=_U32).reshape(-1)
    k = int(idx.size)
    v = d.vals
    w = v.shape[1] if v.ndim == 2 else key_width(d.key)
    v = np.ascontiguousarray(v).reshape(k, w)
    return _N.pack_record(d.key, idx, v, v.dtype.itemsize, w, bool(d.exists[0]), bool(d.exists[1]),
                          d.op == ADD, d.op == ADD and effort != QUICK)


def unpack_delta(b) -> ChannelDelta:
    d, _end = unpack_at(memoryview(b), 0)
    return d


def unpack_at(mv, off: int):
    """Decode the record starting at `off` of memoryview `mv`. -> (delta, offset after it)."""
    key, add, e0, e1, k, w, isz, ib, vb, end = _N.unpack_record(mv, off)
    bdt = _BITS_BY_SIZE[isz]
    if k == 0:
        return ChannelDelta(key, _EMPTY_IDX.copy(), np.zeros((0, w), dtype=bdt), (e0, e1), ADD if add else XOR), end
    return ChannelDelta(key, np.frombuffer(ib, _U32), np.frombuffer(vb, bdt).reshape(k, w), (e0, e1),
                        ADD if add else XOR), end
