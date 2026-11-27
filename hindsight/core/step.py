"""Step (de)serialisation. The container (header, checksum, codecs, every record of every kind of
step, formats 1 to 4) is done by the Rust engine (native/src/step.rs); this module turns Python
objects (Step, ChannelDelta, Snapshot, DataChange, extras) into buffers for it and back. Pure Python +
numpy, no bpy.

Step blob, formats 3 and 4 (written since beta 2):
  b"HSS3" | u32 CRC-32 of everything after this field | u16 header length | header | body
  header = <BBBBQdI>: version (3, or 4 when the step has data records), kind, flags (1 before,
           2 after, 4 snaps, 8 written quickly, values stored as they are, 16 data records),
           reserved, seq, time, raw body length; then label (u16 length + utf-8), mode and target
           (u8 length + utf-8 each), extra (u32 length + JSON, 0 when empty), deltas (u16 count),
           before / after sizes (u32 each, when flagged), snaps (u16 count, then per snapshot
           uid (u8 length + utf-8) and u32 size, when flagged), records (u32 size, when flagged)
  body   = codec.compress(packed deltas, then before / after / extra snapshot blobs, then the data
           records as JSON: [[adapter, target, [[path, old, new], ...]], ...], with {"$": "missing"}
           for MISSING and {"$d": {...}} for a dictionary value)
  A damaged blob fails its checksum and is never applied. Steps written quickly (journal, save)
  and steps from older formats are re-packed in the background by recompress().

Step blob, formats 1 and 2 (still read, never written):
  b"HSS1" | u32 json_len | header JSON (utf-8) | codec.compress(body)

Snapshot blob: b"HSN1" | u32 json_len | JSON {"counts", "arrays": [[key, dtype_str, length], ...]}
              | raw array bytes concatenated (uncompressed).

The JSON of extras and data records stays here (their values are Python objects); the engine
handles it as bytes.
"""
from __future__ import annotations

import os
import struct

import numpy as np

from . import native as _native
from .delta import NORMAL, QUICK
from .types import key_width, ADD, DATA, DELTA, JOIN, MISSING, TOPO, XOR, ChannelDelta, DataChange, Snapshot, Step

_N = _native.ENGINE

STEP_MAGIC_V1 = b"HSS1"
STEP_MAGIC = b"HSS3"
SNAP_MAGIC = b"HSN1"
_U32 = struct.Struct("<I")
_FIXED = struct.Struct("<BBBBQdI")
SNAPSHOTS_KEY = "_snapshots"
_KINDS = {DELTA: 0, TOPO: 1, JOIN: 2, DATA: 3}
RECORDS_FLAG = 16
QUICK_FLAG = 8
# Tests set this (or HINDSIGHT_VERIFY=1): every written step is read back and compared.
VERIFY = bool(os.environ.get("HINDSIGHT_VERIFY"))

_BITS = {1: np.uint8, 2: np.uint16, 4: np.uint32, 8: np.uint64}

CorruptStep = _N.CorruptStep
UnknownEncoding = _N.UnknownEncoding      # intact, but a later build's encoding (dev/FORMAT5_ENCODINGS.md)


# The JSON of extras and data records runs in the engine (#146): the same bytes and exceptions as
# the reference copy (tests/reference/step_json).
_json_default, _enc_value, _dec_value = _N.json_setup(MISSING, DataChange)
_dumps = _N.json_dumps


# --------------------------------------------------------------------------- snapshots
def _snapshot_parts(s: Snapshot) -> list:
    """The snapshot blob as a list of buffers (the arrays themselves, not copies of them)."""
    descr, parts = [], []
    for key, arr in s.arrays.items():
        a = np.ascontiguousarray(arr).reshape(-1)
        descr.append([key, a.dtype.str, int(a.size)])
        parts.append(a.view(np.uint8))
    head = _dumps({"counts": {k: int(v) for k, v in s.counts.items()}, "arrays": descr})
    return [SNAP_MAGIC, _U32.pack(len(head)), head] + parts


def snapshot_to_bytes(s: Snapshot) -> bytes:
    return b"".join(_snapshot_parts(s))


def _snapshot(parsed) -> Snapshot:
    counts, arrays = parsed
    return Snapshot(dict(counts), {key: np.frombuffer(buf, dtype=dstr) for key, dstr, buf in arrays})


def snapshot_from_bytes(b) -> Snapshot:
    return _snapshot(_N.snapshot_decode(b))


# --------------------------------------------------------------------------- data records
records_to_bytes = _N.records_to_bytes
records_from_bytes = _N.records_from_bytes


# --------------------------------------------------------------------------- writing
def _delta_args(d) -> tuple:
    """A ChannelDelta as the engine takes it: its arrays are borrowed, packed in the engine."""
    idx = np.ascontiguousarray(d.idx, dtype=np.uint32).reshape(-1)
    v = np.ascontiguousarray(d.vals)
    w = v.shape[1] if v.ndim == 2 else key_width(d.key)
    return (d.key, idx, v.reshape(-1), v.dtype.itemsize, w, bool(d.exists[0]), bool(d.exists[1]), d.op == ADD)


def record_step(kind, seq, t, label, mode, target, channels, extra=None, codec="auto", effort=NORMAL,
                strong=False):
    """The blob of a stroke in one engine call (diff, packing and codec, GIL released), or None if no
    channel changed. channels: [(key, old, new, exists before, exists after)] with flat native
    arrays; old or new None for a channel that appears or goes. Same bytes as diffing each channel,
    building the Step and step_to_bytes (the stroke's DELTA step)."""
    chs = []
    for key, old, new, e0, e1 in channels:
        ref = new if new is not None else old
        w = key_width(key)
        o = None if old is None else np.ascontiguousarray(old).reshape(-1).view(np.uint8)
        n = None if new is None else np.ascontiguousarray(new).reshape(-1).view(np.uint8)
        chs.append((key, o, n, np.dtype(ref.dtype).itemsize, w, bool(e0), bool(e1)))
    return _N.record(kind, int(seq), float(t or 0.0), label or "", mode or "", target or "",
                     _dumps(extra) if extra else b"", chs, effort == QUICK, codec, strong)


def step_to_bytes(step: Step, codec: str = "auto", effort: int = NORMAL, strong: bool = False) -> bytes:
    """Format 3 blob. effort QUICK (0) is for the crash-recovery journal, on the main thread."""
    if step.kind not in _KINDS:
        raise ValueError(f"unknown step kind {step.kind!r}")
    extra = dict(step.extra or {})
    snaps = extra.pop(SNAPSHOTS_KEY, None)
    parts = [_delta_args(d) for d in step.deltas]
    # snapshots stay lists of buffers until the engine joins the body (no full-size copies before it)
    before = _snapshot_parts(step.before) if step.before is not None else None
    after = _snapshot_parts(step.after) if step.after is not None else None
    snap_parts = [(str(uid), _snapshot_parts(s)) for uid, s in snaps.items()] if snaps is not None else None
    blob = _N.step_encode(
        step.kind, int(step.seq), float(step.time or 0.0), step.label or "", step.mode or "", step.target or "",
        _dumps(extra) if extra else b"", parts, before, after, snap_parts,
        records_to_bytes(step.records) if step.records else None, effort == QUICK, codec, strong)
    if VERIFY and not same_step(step, step_from_bytes(blob)):
        raise AssertionError(f"step {step.seq}: written blob does not read back the same")
    return blob


# --------------------------------------------------------------------------- reading
_extra = _N.step_extra
read_header = _N.read_header          # step metadata without decompressing the body


def step_from_bytes(b) -> Step:
    (ver, seq, t, kind, label, mode, target, extra_b, _quick, deltas, before, after, snaps,
     rec_b) = _N.step_decode(b)
    extra = _extra(extra_b)
    out = []
    for key, add, e0, e1, k, w, isz, ib, vb in deltas:
        bdt = _BITS[isz]
        if k == 0:
            idx, vals = np.zeros(0, dtype=np.uint32), np.zeros((0, w), dtype=bdt)
        else:
            idx, vals = np.frombuffer(ib, dtype=np.uint32), np.frombuffer(vb, dtype=bdt).reshape(k, w)
        out.append(ChannelDelta(key, idx, vals, (e0, e1), ADD if add else XOR))
    try:
        before = _snapshot(before) if before is not None else None
        after = _snapshot(after) if after is not None else None
        if snaps is not None:
            extra[SNAPSHOTS_KEY] = {uid: _snapshot(s) for uid, s in snaps}
        records = records_from_bytes(rec_b) if rec_b is not None else []
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, RecursionError) as e:
        raise CorruptStep(f"step blob: damaged ({e})") from e
    return Step(seq=seq, label=label, mode=mode, target=target, time=t, kind=kind, deltas=out,
                before=before, after=after, extra=extra, records=records)


def version_of(b) -> int:
    """3 for format 3 blobs, 1 for formats 1 and 2, 0 if not a step (the container; the encoding: is_unknown)."""
    magic = bytes(memoryview(b)[:4])
    return 3 if magic == STEP_MAGIC else (1 if magic == STEP_MAGIC_V1 else 0)


def is_unknown(b) -> bool:
    """Do step bytes `b` say they use an encoding this build has no decoder for (a later build's)? Read from the
    step prefix alone (dev/FORMAT5_ENCODINGS.md); bytes that aren't a step are damage, not unknown."""
    try:
        return _N.step_encoding(b[:11]) not in _N.ENCODINGS     # the prefix alone: a Slice isn't copied (#125)
    except CorruptStep:
        return False


def _same_arrays(a: dict, b: dict) -> bool:
    if a.keys() != b.keys():
        return False
    return all(a[k].dtype == b[k].dtype and a[k].tobytes() == b[k].tobytes() for k in a)


def _same_snap(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return a.counts == b.counts and _same_arrays(a.arrays, b.arrays)


def same_step(a: Step, b: Step) -> bool:
    """True if two steps hold exactly the same data (deltas compared after decoding to the same
    form: XOR deltas only equal XOR deltas, ADD only ADD). Used by HINDSIGHT_VERIFY and tests."""
    if (a.seq, a.label, a.mode, a.target, a.kind) != (b.seq, b.label, b.mode, b.target, b.kind):
        return False
    if float(a.time or 0.0) != float(b.time or 0.0) or len(a.deltas) != len(b.deltas):
        return False
    for x, y in zip(a.deltas, b.deltas):
        if (x.key, x.op, tuple(x.exists)) != (y.key, y.op, tuple(y.exists)):
            return False
        if not np.array_equal(np.asarray(x.idx, np.int64), np.asarray(y.idx, np.int64)):
            return False
        xv, yv = np.asarray(x.vals), np.asarray(y.vals)
        if xv.dtype != yv.dtype or xv.reshape(-1).tobytes() != yv.reshape(-1).tobytes():
            return False
    if not (_same_snap(a.before, b.before) and _same_snap(a.after, b.after)):
        return False
    if records_to_bytes(a.records) != records_to_bytes(b.records):
        return False
    ea, eb = dict(a.extra or {}), dict(b.extra or {})
    sa, sb = ea.pop(SNAPSHOTS_KEY, None), eb.pop(SNAPSHOTS_KEY, None)
    if _N.json_loads(_dumps(ea)) != _N.json_loads(_dumps(eb)):
        return False
    if (sa is None) != (sb is None):
        return False
    if sa is not None:
        if list(sa) != list(sb) or not all(_same_snap(sa[k], sb[k]) for k in sa):
            return False
    return True


def recompress(b) -> bytes | None:
    """A smaller blob for the same step: steps from older formats and steps written quickly are
    re-packed in full, and every step gets the strong codec setting. The new blob is read back
    inside the engine and must give exactly the same data. None if there's nothing to gain."""
    return _N.recompress(b)
