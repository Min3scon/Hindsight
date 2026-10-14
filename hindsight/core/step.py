"""Step (de)serialisation. Pure Python + numpy, no bpy.

Step blob:  b"HSS1" | u32 json_len | header JSON (utf-8) | codec.compress(body)
  header = {"v": 1, "meta": step meta (extra without "_snapshots"),
            "deltas": [{"key", "exists", "k", "n"}], "before": n|None, "after": n|None,
            "snaps": [[uid, n], ...] | None}
  body   = packed deltas, then before / after / extra snapshot blobs, concatenated (n = byte length).
Snapshot blob: b"HSN1" | u32 json_len | JSON {"counts", "arrays": [[key, dtype_str, length], ...]}
              | raw array bytes concatenated (uncompressed).
"""
from __future__ import annotations

import json
import struct

import numpy as np

from . import codec as _codec
from .delta import pack_delta, unpack_delta
from .types import Snapshot, Step

STEP_MAGIC = b"HSS1"
SNAP_MAGIC = b"HSN1"
_U32 = struct.Struct("<I")
SNAPSHOTS_KEY = "_snapshots"


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def _dumps(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), default=_json_default).encode("utf-8")


# --------------------------------------------------------------------------- snapshots
def snapshot_to_bytes(s: Snapshot) -> bytes:
    descr, parts = [], []
    for key, arr in s.arrays.items():
        a = np.ascontiguousarray(arr).reshape(-1)
        descr.append([key, a.dtype.str, int(a.size)])
        parts.append(a.tobytes())
    head = _dumps({"counts": {k: int(v) for k, v in s.counts.items()}, "arrays": descr})
    return b"".join([SNAP_MAGIC, _U32.pack(len(head)), head] + parts)


def snapshot_from_bytes(b) -> Snapshot:
    mv = memoryview(b)
    if bytes(mv[:4]) != SNAP_MAGIC:
        raise ValueError("snapshot_from_bytes: not a Hindsight snapshot (wrong header)")
    (hl,) = _U32.unpack_from(mv, 4)
    head = json.loads(bytes(mv[8:8 + hl]).decode("utf-8"))
    off = 8 + hl
    arrays = {}
    for key, dstr, length in head["arrays"]:
        dt = np.dtype(dstr)
        n = int(length) * dt.itemsize
        # copy -> owned, aligned, writeable
        arrays[key] = np.frombuffer(mv[off:off + n], dtype=dt).copy()
        off += n
    return Snapshot(dict(head["counts"]), arrays)


# --------------------------------------------------------------------------- steps
def step_to_bytes(step: Step, codec: str = "auto") -> bytes:
    extra = dict(step.extra or {})
    snaps = extra.pop(SNAPSHOTS_KEY, None)
    meta = {"seq": step.seq, "label": step.label, "mode": step.mode, "target": step.target,
            "time": step.time, "kind": step.kind, "extra": extra}
    parts, dheads = [], []
    for d in step.deltas:
        p = pack_delta(d)
        parts.append(p)
        dheads.append({"key": d.key, "exists": [bool(d.exists[0]), bool(d.exists[1])],
                       "k": int(d.idx.size), "n": len(p)})

    def _snap(s):
        if s is None:
            return None
        p = snapshot_to_bytes(s)
        parts.append(p)
        return len(p)

    before_n = _snap(step.before)
    after_n = _snap(step.after)
    snap_heads = None
    if snaps is not None:
        snap_heads = [[str(uid), _snap(s)] for uid, s in snaps.items()]
    head = _dumps({"v": 1, "meta": meta, "deltas": dheads, "before": before_n,
                   "after": after_n, "snaps": snap_heads})
    body = _codec.compress(b"".join(parts), codec)
    return b"".join((STEP_MAGIC, _U32.pack(len(head)), head, body))


def read_header(b) -> dict:
    """Header JSON of a step blob (no decompression)."""
    mv = memoryview(b)
    if bytes(mv[:4]) != STEP_MAGIC:
        raise ValueError("step blob: not a Hindsight step (wrong header)")
    (hl,) = _U32.unpack_from(mv, 4)
    return json.loads(bytes(mv[8:8 + hl]).decode("utf-8"))


def step_from_bytes(b) -> Step:
    mv = memoryview(b)
    if bytes(mv[:4]) != STEP_MAGIC:
        raise ValueError("step blob: not a Hindsight step (wrong header)")
    (hl,) = _U32.unpack_from(mv, 4)
    head = json.loads(bytes(mv[8:8 + hl]).decode("utf-8"))
    body = memoryview(_codec.decompress(mv[8 + hl:]))
    off = 0
    deltas = []
    for dh in head["deltas"]:
        n = dh["n"]
        deltas.append(unpack_delta(body[off:off + n]))
        off += n

    def _snap(n):
        nonlocal off
        if n is None:
            return None
        s = snapshot_from_bytes(body[off:off + n])
        off += n
        return s

    before = _snap(head.get("before"))
    after = _snap(head.get("after"))
    m = head["meta"]
    extra = dict(m.get("extra") or {})
    if head.get("snaps") is not None:
        extra[SNAPSHOTS_KEY] = {uid: _snap(n) for uid, n in head["snaps"]}
    return Step(seq=m["seq"], label=m["label"], mode=m["mode"], target=m["target"],
                time=m.get("time", 0.0), kind=m.get("kind", "DELTA"), deltas=deltas,
                before=before, after=after, extra=extra)
