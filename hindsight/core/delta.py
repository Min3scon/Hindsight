"""Bit-exact channel deltas. Pure numpy, fully vectorised, no bpy.

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
"""
from __future__ import annotations

import struct

import numpy as np

from .types import ADD, XOR, ChannelDelta, bits_dtype, key_width

_U32 = np.uint32
_EMPTY_IDX = np.zeros(0, dtype=_U32)
_WIDTH_DT = {1: np.uint8, 2: np.uint16, 4: np.uint32}
_LEN_FMT = {1: "<B", 2: "<H", 4: "<I"}
_BITS_BY_SIZE = {1: np.uint8, 2: np.uint16, 4: np.uint32, 8: np.uint64}
_SIGNED_BY_SIZE = {1: np.int8, 2: np.int16, 4: np.int32, 8: np.int64}

# format 1 and 2: magic, flags, xor itemsize, width, idx-diff itemsize, key length, k, first index
_HDR_V1 = struct.Struct("<2sBBBBHII")
_MAGIC_V1 = b"HD"
# format 3
_HDR = struct.Struct("<2sBBBBHII")
_MAGIC = b"HE"

IDX_GAPS, IDX_RUNS, IDX_BITMAP = 0, 1, 2
TAG_RAW = 255
MAX_ORDER = 3
ORDER_GAIN = 0.97     # a higher order must shrink the estimate by at least this factor
MAX_WIDTH = 16

# encoder effort
QUICK = 0     # crash-recovery journal, on the main thread: values stored as they are
NORMAL = 1    # packing and saving: per component, the order with the smallest estimated size


class CorruptDelta(ValueError):
    """A packed delta is damaged or not a delta at all."""


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
    """Delta old -> new for channel `key` (op ADD). None if bit-identical and existence unchanged."""
    if old.size != new.size:
        raise ValueError(f"diff {key}: size mismatch {old.size} != {new.size}")
    if np.dtype(old.dtype).itemsize != np.dtype(new.dtype).itemsize:
        raise ValueError(f"diff {key}: dtype mismatch {old.dtype} vs {new.dtype}")
    w = key_width(key)
    exists = (bool(exists[0]), bool(exists[1]))
    ob = view_bits(old).reshape(-1, w)
    nb = view_bits(new).reshape(-1, w)
    idx = np.flatnonzero(_changed_mask(ob ^ nb)).astype(_U32, copy=False)
    if idx.size == 0:
        if exists[0] == exists[1]:
            return None
        return ChannelDelta(key, _EMPTY_IDX.copy(), np.zeros((0, w), dtype=ob.dtype), exists, ADD)
    return ChannelDelta(key, idx, nb[idx] - ob[idx], exists, ADD)


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
    if int(d.idx[-1]) >= b.shape[0]:
        raise ValueError(f"apply {d.key}: index {int(d.idx[-1])} out of range ({b.shape[0]})")
    v = v.reshape(-1, w)
    idx = d.idx
    k = idx.size
    lo = int(idx[0])
    if int(idx[-1]) - lo + 1 == k:
        # one contiguous run (sorted, unique): a slice, no gather or scatter
        _combine(b[lo:lo + k], v, d.op, forward)
        return
    ii = idx.astype(np.intp)
    tmp = b.take(ii, axis=0)
    _combine(tmp, v, d.op, forward)
    b[ii] = tmp


def _combine(dst: np.ndarray, v: np.ndarray, op: int, forward: bool) -> None:
    if op == XOR:
        dst ^= v
    elif forward:
        dst += v
    else:
        dst -= v


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


# ------------------------------------------------------------------------------- transforms
def _zigzag(v: np.ndarray) -> np.ndarray:
    isz = v.dtype.itemsize
    s = v.view(_SIGNED_BY_SIZE[isz])
    return ((s << 1) ^ (s >> (8 * isz - 1))).view(v.dtype)


def _unzigzag(z: np.ndarray) -> np.ndarray:
    return (z >> 1) ^ (np.zeros_like(z) - (z & 1))


def _zigzag_inplace(a: np.ndarray) -> np.ndarray:
    """Zigzag in place on an unsigned array (two's complement view): 0, -1, 1, -2 -> 0, 1, 2, 3."""
    isz = a.dtype.itemsize
    sign = a.view(_SIGNED_BY_SIZE[isz]) >> (8 * isz - 1)        # 0 or -1
    a <<= 1
    a ^= sign.view(a.dtype)
    return a


def _difference(a: np.ndarray) -> np.ndarray:
    """(w, k) -> new array: first value of each row kept, then each value minus the one before
    it in its row (wrapping)."""
    out = np.empty_like(a)
    out[:, 0] = a[:, 0]
    np.subtract(a[:, 1:], a[:, :-1], out=out[:, 1:])
    return out


def _planes(cols: np.ndarray) -> bytes:
    """(k, w) unsigned -> byte planes, component-major."""
    k, w = cols.shape
    return np.ascontiguousarray(cols).view(np.uint8).reshape(k, w * cols.dtype.itemsize).T.tobytes()


def _row_planes(rows: np.ndarray) -> bytes:
    """(w, k) unsigned, C-contiguous -> the same byte planes as _planes(rows.T)."""
    w, k = rows.shape
    return rows.view(np.uint8).reshape(w, k, rows.dtype.itemsize).transpose(0, 2, 1).tobytes()


# ------------------------------------------------------------------------------- index coding
_RAMP = np.arange(1 << 16, dtype=_U32)


def _ramp(n: int) -> np.ndarray:
    """0, 1, ..., n - 1 as uint32, from a shared read-only table (grown on demand)."""
    global _RAMP
    if n > _RAMP.size:
        r = np.arange(max(n, 2 * _RAMP.size), dtype=_U32)
        r.flags.writeable = False
        _RAMP = r
    return _RAMP[:n]


_RAMP.flags.writeable = False


def _width_code(m: int) -> int:
    return 1 if m <= 0xFF else (2 if m <= 0xFFFF else 4)


def _idx_gaps(idx: np.ndarray, gaps=None) -> bytes:
    g = (np.diff(idx.astype(np.int64)) if gaps is None else gaps) - 1
    wc = _width_code(int(g.max()) if g.size else 0)
    return struct.pack("<BI", wc, int(idx[0])) + g.astype(_WIDTH_DT[wc]).tobytes()


def _runs(idx: np.ndarray):
    i64 = idx.astype(np.int64)
    br = np.flatnonzero(np.diff(i64) != 1) + 1
    starts = np.concatenate(([0], br))
    ends = np.concatenate((br, [i64.size]))
    return i64[starts], ends - starts


def _idx_runs(idx: np.ndarray) -> bytes:
    s, ln = _runs(idx)
    gaps = s[1:] - (s[:-1] + ln[:-1]) - 1
    gw = _width_code(int(gaps.max()) if gaps.size else 0)
    lw = _width_code(int(ln.max()) - 1)
    return (struct.pack("<BII", gw | (lw << 4), int(s.size), int(s[0]))
            + gaps.astype(_WIDTH_DT[gw]).tobytes() + (ln - 1).astype(_WIDTH_DT[lw]).tobytes())


def _idx_bitmap(idx: np.ndarray) -> bytes:
    lo = int(idx[0])
    span = int(idx[-1]) - lo + 1
    m = np.zeros(span, dtype=np.uint8)
    m[idx.astype(np.int64) - lo] = 1
    return struct.pack("<II", lo, span) + np.packbits(m, bitorder="little").tobytes()


_IDX_ENC = {IDX_GAPS: _idx_gaps, IDX_RUNS: _idx_runs, IDX_BITMAP: _idx_bitmap}


def _read(mv, off, n, end):
    if n < 0 or off + n > end:
        raise CorruptDelta("delta: index data is cut short")
    return mv[off:off + n]


def _decode_idx(code: int, mv, off: int, end: int, k: int) -> np.ndarray:
    if code == IDX_GAPS:
        wc, first = struct.unpack_from("<BI", _read(mv, off, 5, end))
        if wc not in _WIDTH_DT:
            raise CorruptDelta("delta: bad gap width")
        g = np.frombuffer(_read(mv, off + 5, (k - 1) * wc, end), dtype=_WIDTH_DT[wc])
        if first + (k - 1) * (1 << (8 * wc)) > 0xFFFFFFFF and \
                first + int(g.sum(dtype=np.uint64)) + k - 1 > 0xFFFFFFFF:
            raise CorruptDelta("delta: index out of range")
        out = np.empty(k, dtype=_U32)
        out[0] = first
        if k > 1:
            # idx[i] = first + i + sum of the first i stored values (each is gap - 1)
            tail = out[1:]
            np.add.accumulate(g, dtype=_U32, out=tail)
            tail += _ramp(k)[1:]
            if first:
                tail += _U32(first)
        return out
    elif code == IDX_RUNS:
        widths, nr, first = struct.unpack_from("<BII", _read(mv, off, 9, end))
        gw, lw = widths & 15, widths >> 4
        if gw not in _WIDTH_DT or lw not in _WIDTH_DT or nr < 1 or nr > k:
            raise CorruptDelta("delta: bad run header")
        p = off + 9
        if nr == 1:
            # the whole record is one run (a common case: whole-mesh changes, contiguous strokes)
            (ln1,) = struct.unpack_from(_LEN_FMT[lw], _read(mv, p, lw, end))
            if ln1 + 1 != k or first + k - 1 > 0xFFFFFFFF:
                raise CorruptDelta("delta: bad run")
            return _ramp(k) + _U32(first)
        gaps = np.frombuffer(_read(mv, p, (nr - 1) * gw, end), dtype=_WIDTH_DT[gw]).astype(np.int64)
        p += (nr - 1) * gw
        ln = np.frombuffer(_read(mv, p, nr * lw, end), dtype=_WIDTH_DT[lw]).astype(np.int64)
        ln += 1
        if int(ln.sum()) != k:
            raise CorruptDelta("delta: run lengths don't add up")
        # element i of run j is offset[j] + i, with offset[j] = first + sum over earlier runs of (gap + 1)
        offs = np.empty(nr, dtype=np.int64)
        offs[0] = first
        if nr > 1:
            np.cumsum(gaps + 1, out=offs[1:])
            offs[1:] += first
        if int(offs[-1]) + k - 1 > 0xFFFFFFFF:
            raise CorruptDelta("delta: index out of range")
        out = np.arange(k, dtype=_U32)
        if nr == 1:
            out += _U32(first)
        else:
            out += np.repeat(offs.astype(_U32), ln)
        return out
    elif code == IDX_BITMAP:
        lo, span = struct.unpack_from("<II", _read(mv, off, 8, end))
        nb = (span + 7) // 8
        m = np.unpackbits(np.frombuffer(_read(mv, off + 8, nb, end), dtype=np.uint8), count=span,
                          bitorder="little")
        out = np.flatnonzero(m).astype(np.int64)
        if out.size != k:
            raise CorruptDelta("delta: bitmap doesn't match the element count")
        out += lo
    else:
        raise CorruptDelta("delta: unknown index coding")
    if k and int(out[-1]) > 0xFFFFFFFF:
        raise CorruptDelta("delta: index out of range")
    return out.astype(_U32)


# ------------------------------------------------------------------------------- pack / unpack
def pack_delta(d: ChannelDelta, effort: int = NORMAL) -> bytes:
    """Format 3 record."""
    key_b = d.key.encode("utf-8")
    idx = np.ascontiguousarray(d.idx, dtype=_U32).reshape(-1)
    k = int(idx.size)
    v = d.vals
    w = v.shape[1] if v.ndim == 2 else key_width(d.key)
    v = np.ascontiguousarray(v).reshape(k, w)
    isz = np.dtype(v.dtype).itemsize
    flags = (1 if d.exists[0] else 0) | (2 if d.exists[1] else 0) | (4 if d.op == ADD else 0)
    if k == 0:
        return _HDR.pack(_MAGIC, flags, isz, w, IDX_GAPS, len(key_b), 0, 0) + key_b
    # index: gaps, or one run when the elements are contiguous. Measured on real strokes, runs and
    # bitmaps save under 1% of a step after compression and decode slower, so they aren't chosen
    # otherwise (the reader still accepts them).
    gaps = np.diff(idx.astype(np.int64))
    if gaps.size and int(gaps.min()) < 1:
        raise ValueError(f"pack_delta {d.key}: indices must be sorted and unique")
    if int(idx[-1]) - int(idx[0]) + 1 == k and k > 1:
        code, ib = IDX_RUNS, _idx_runs(idx)
    else:
        code, ib = IDX_GAPS, _idx_gaps(idx, gaps)
    if d.op != ADD or effort == QUICK:
        # stored as is: format 2 steps, and the crash-recovery journal (written on the main thread
        # during a stroke commit, so it must cost no more than before; it is temporary anyway)
        tags = bytes((TAG_RAW,)) * w
        vb = _planes(v)
    else:
        rows = v.T.copy()                         # (w, k): each component contiguous
        if k < 2:
            tags = bytes((0,)) * w
            vb = _row_planes(_zigzag_inplace(rows))
        else:
            # one predictor per component: the fewest estimated bits, a higher order only for a
            # clear gain since each one costs a pass when decoding. All orders are computed in one
            # stacked array, so the work is a handful of large operations.
            n_ord = min(MAX_ORDER, k - 1) + 1
            z = np.empty((n_ord, w, k), dtype=rows.dtype)
            z[0] = rows
            for order in range(1, n_ord):
                z[order, :, 0] = z[order - 1, :, 0]
                np.subtract(z[order - 1, :, 1:], z[order - 1, :, :-1], out=z[order, :, 1:])
            _zigzag_inplace(z)
            e = (z.astype(np.float32).view(np.int32) >> 23).sum(axis=2, dtype=np.int64)
            est = (e - 126 * np.count_nonzero(z, axis=2)).tolist()       # [order][component] bits
            tags = bytearray()
            out = np.empty_like(rows)
            for c in range(w):
                best = 0
                for order in range(1, n_ord):
                    if est[order][c] < est[best][c] * ORDER_GAIN:
                        best = order
                tags.append(best)
                out[c] = z[best, c]
            tags = bytes(tags)
            vb = _row_planes(out)
    hdr = _HDR.pack(_MAGIC, flags, isz, w, code, len(key_b), k, len(ib))
    return b"".join((hdr, key_b, ib, tags, vb))


def _unpack_v1(mv) -> ChannelDelta:
    magic, flags, isz, w, dsz, klen, k, first = _HDR_V1.unpack_from(mv, 0)
    off = _HDR_V1.size
    if isz not in _BITS_BY_SIZE or dsz not in _WIDTH_DT or not 0 < w <= MAX_WIDTH:
        raise CorruptDelta("delta: bad header")
    end = len(mv)
    key = bytes(_read(mv, off, klen, end)).decode("utf-8", errors="strict")
    off += klen
    bdt = np.dtype(_BITS_BY_SIZE[isz])
    exists = (bool(flags & 1), bool(flags & 2))
    if k == 0:
        return ChannelDelta(key, _EMPTY_IDX.copy(), np.zeros((0, w), dtype=bdt), exists, XOR)
    if off + max(k - 1, 0) * dsz + k * isz * w > end:
        raise CorruptDelta("delta: cut short")
    idx = np.empty(k, dtype=np.int64)
    idx[0] = first
    if k > 1:
        n = (k - 1) * dsz
        dif = np.frombuffer(mv[off:off + n], dtype=_WIDTH_DT[dsz]).astype(np.int64)
        off += n
        if not (dif > 0).all():
            raise CorruptDelta("delta: indices not increasing")
        np.cumsum(dif, out=idx[1:])
        idx[1:] += first
        if int(idx[-1]) > 0xFFFFFFFF:
            raise CorruptDelta("delta: index out of range")
    nx = k * isz * w
    planes = np.frombuffer(mv[off:off + nx], dtype=np.uint8).reshape(isz * w, k)
    xor = np.ascontiguousarray(planes.T).view(bdt).reshape(k, w)
    return ChannelDelta(key, idx.astype(_U32), xor, exists, XOR)


def unpack_delta(b) -> ChannelDelta:
    d, _end = unpack_at(memoryview(b), 0)
    return d


def unpack_at(mv, off: int):
    """Decode the record starting at `off` of memoryview `mv`. -> (delta, offset after it)."""
    end = len(mv)
    if end - off < _HDR.size:
        raise CorruptDelta("delta: too short")
    magic = bytes(mv[off:off + 2])
    if magic == _MAGIC_V1:
        n = packed_size(mv, off)
        if off + n > end:
            raise CorruptDelta("delta: cut short")
        return _unpack_v1(mv[off:off + n]), off + n
    if magic != _MAGIC:
        raise CorruptDelta("unpack_delta: not a Hindsight delta (wrong header)")
    _m, flags, isz, w, code, klen, k, ilen = _HDR.unpack_from(mv, off)
    if flags & ~7 or isz not in _BITS_BY_SIZE or not 0 < w <= MAX_WIDTH:
        raise CorruptDelta("delta: bad header")
    off += _HDR.size
    try:
        key = bytes(_read(mv, off, klen, end)).decode("utf-8")
    except UnicodeDecodeError as e:
        raise CorruptDelta("delta: bad key") from e
    off += klen
    bdt = _BITS_BY_SIZE[isz]
    exists = (bool(flags & 1), bool(flags & 2))
    op = ADD if flags & 4 else XOR
    if k == 0:
        return ChannelDelta(key, _EMPTY_IDX.copy(), np.zeros((0, w), dtype=bdt), exists, op), off
    nx = k * isz * w
    stop = off + ilen + w + nx
    if stop > end:
        raise CorruptDelta("delta: cut short")
    idx = _decode_idx(code, mv, off, off + ilen, k)
    off += ilen
    tags = bytes(mv[off:off + w])
    off += w
    planes = np.frombuffer(mv[off:stop], dtype=np.uint8).reshape(isz * w, k)
    vals = np.ascontiguousarray(planes.T).view(bdt).reshape(k, w)
    if not vals.flags.writeable:          # k == 1 or one byte per element: the transpose was a view
        vals = vals.copy()
    hi = max(tags)
    if hi <= MAX_ORDER:                    # the usual case: every component predicted
        if op == XOR:
            raise CorruptDelta("delta: bad predictor tag")
        _undo(vals, tags)
    elif tags.count(TAG_RAW) != w:
        if op == XOR or hi != TAG_RAW or any(t > MAX_ORDER and t != TAG_RAW for t in tags):
            raise CorruptDelta("delta: bad predictor tag")
        cols = [c for c, t in enumerate(tags) if t != TAG_RAW]
        vals[:, cols] = _undo(np.ascontiguousarray(vals[:, cols]), bytes(tags[c] for c in cols))
    return ChannelDelta(key, idx, vals, exists, op), stop


_LANE_SIGN = np.uint64(0x0000000100000001)
_LANE_LOW = np.uint64(0xFFFFFFFF)
_LANE_KEEP = np.uint64(0x7FFFFFFF7FFFFFFF)
_ONE64 = np.uint64(1)


def _undo(a: np.ndarray, orders) -> np.ndarray:
    """In place on a (k, w) array: undo the zigzag mapping, then the differencing per column."""
    if a.dtype == np.uint32 and a.size >= 4096 and a.size % 2 == 0:
        # two 32-bit values per 64-bit word: fewer, wider passes over memory
        q = a.reshape(-1).view(np.uint64)
        sign = q & _LANE_SIGN
        sign *= _LANE_LOW
        q >>= _ONE64
        q &= _LANE_KEEP
        q ^= sign
    else:
        sign = a & 1
        np.negative(sign, out=sign)
        a >>= 1
        a ^= sign
    low = min(orders)
    for _ in range(low):
        np.add.accumulate(a, axis=0, out=a)
    for c, o in enumerate(orders):
        col = a[:, c]
        for _ in range(o - low):
            np.add.accumulate(col, out=col)
    return a


def packed_size(b, offset: int = 0) -> int:
    """Byte length of the packed delta starting at `offset` (for streaming parsers)."""
    magic = bytes(b[offset:offset + 2])
    if magic == _MAGIC_V1:
        _m, _f, isz, w, dsz, klen, k, _first = _HDR_V1.unpack_from(b, offset)
        return _HDR_V1.size + klen + max(k - 1, 0) * dsz + k * isz * w
    _m, _f, isz, w, _c, klen, k, ilen = _HDR.unpack_from(b, offset)
    return _HDR.size + klen + (ilen + w + k * isz * w if k else 0)
