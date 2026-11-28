"""REFERENCE COPY (tests only; nothing under hindsight/ may import it). The Python that hindsight/core/step.py still ran
to build a step's objects and compare two steps: `_snapshot`, `snapshot_from_bytes`, `step_from_bytes`, `_same_arrays`,
`_same_snap` and `same_step`, step.py's at cfa1293, unchanged but for their imports (#147, 24.3b)."""
from __future__ import annotations

import numpy as np

from hindsight.core import native as _native
from .types import ADD, XOR, ChannelDelta, Snapshot, Step

_N = _native.ENGINE
SNAPSHOTS_KEY = "_snapshots"
_BITS = {1: np.uint8, 2: np.uint16, 4: np.uint32, 8: np.uint64}
CorruptStep = _N.CorruptStep
_dumps = _N.json_dumps
_extra = _N.step_extra
records_to_bytes = _N.records_to_bytes
records_from_bytes = _N.records_from_bytes


def _snapshot(parsed) -> Snapshot:
    counts, arrays = parsed
    return Snapshot(dict(counts), {key: np.frombuffer(buf, dtype=dstr) for key, dstr, buf in arrays})


def snapshot_from_bytes(b) -> Snapshot:
    return _snapshot(_N.snapshot_decode(b))


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
