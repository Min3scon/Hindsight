"""Step (de)serialisation. Pure Python + numpy, no bpy.

Step blob, format 3 (written since beta 2):
  b"HSS3" | u32 CRC-32 of everything after this field | u16 header length | header | body
  header = <BBBBQdI>: version (3), kind, flags (1 before, 2 after, 4 snaps, 8 written quickly,
           values stored as they are), reserved, seq, time, raw body length; then label (u16 length + utf-8), mode and target
           (u8 length + utf-8 each), extra (u32 length + JSON, 0 when empty), deltas (u16 count),
           before / after sizes (u32 each, when flagged), snaps (u16 count, then per snapshot
           uid (u8 length + utf-8) and u32 size, when flagged)
  body   = codec.compress(packed deltas, then before / after / extra snapshot blobs)
  A damaged blob fails its checksum and is never applied. Steps written quickly (journal, save)
  and steps from older formats are re-packed in the background by recompress().

Step blob, formats 1 and 2 (still read, never written):
  b"HSS1" | u32 json_len | header JSON (utf-8) | codec.compress(body)

Snapshot blob: b"HSN1" | u32 json_len | JSON {"counts", "arrays": [[key, dtype_str, length], ...]}
              | raw array bytes concatenated (uncompressed).
"""
from __future__ import annotations

import json
import os
import struct
import zlib

import numpy as np

from . import codec as _codec
from .delta import NORMAL, QUICK, CorruptDelta, pack_delta, unpack_at, unpack_delta
from .types import DELTA, JOIN, TOPO, Snapshot, Step

STEP_MAGIC_V1 = b"HSS1"
STEP_MAGIC = b"HSS3"
SNAP_MAGIC = b"HSN1"
_U32 = struct.Struct("<I")
_FIXED = struct.Struct("<BBBBQdI")
SNAPSHOTS_KEY = "_snapshots"
_KINDS = {DELTA: 0, TOPO: 1, JOIN: 2}
QUICK_FLAG = 8
# Tests set this (or HINDSIGHT_VERIFY=1): every written step is read back and compared.
VERIFY = bool(os.environ.get("HINDSIGHT_VERIFY"))
_KIND_NAMES = {v: k for k, v in _KINDS.items()}


class CorruptStep(ValueError):
    """A step blob is damaged or not a step at all."""


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
        if n < 0 or off + n > len(mv):
            raise ValueError("snapshot_from_bytes: cut short")
        # copy -> owned, aligned, writeable
        arrays[key] = np.frombuffer(mv[off:off + n], dtype=dt).copy()
        off += n
    return Snapshot(dict(head["counts"]), arrays)


# --------------------------------------------------------------------------- writing
def _str8(s: str) -> bytes:
    b = s.encode("utf-8")
    if len(b) > 255:
        raise ValueError(f"step field too long: {s[:40]!r}")
    return bytes((len(b),)) + b


def _str16(s: str) -> bytes:
    b = s.encode("utf-8")[:65535]
    return struct.pack("<H", len(b)) + b


def _parts(step: Step, effort: int):
    extra = dict(step.extra or {})
    snaps = extra.pop(SNAPSHOTS_KEY, None)
    parts = [pack_delta(d, effort) for d in step.deltas]
    before = snapshot_to_bytes(step.before) if step.before is not None else None
    after = snapshot_to_bytes(step.after) if step.after is not None else None
    snap_parts = None
    if snaps is not None:
        snap_parts = [(str(uid), snapshot_to_bytes(s)) for uid, s in snaps.items()]
    return extra, parts, before, after, snap_parts


def step_to_bytes(step: Step, codec: str = "auto", effort: int = NORMAL, strong: bool = False) -> bytes:
    """Format 3 blob. effort QUICK (0) is for the crash-recovery journal, on the main thread."""
    if step.kind not in _KINDS:
        raise ValueError(f"unknown step kind {step.kind!r}")
    extra, parts, before, after, snap_parts = _parts(step, effort)
    body_parts = list(parts)
    flags = 0
    tail = b""
    if before is not None:
        flags |= 1
        body_parts.append(before)
        tail += _U32.pack(len(before))
    if after is not None:
        flags |= 2
        body_parts.append(after)
        tail += _U32.pack(len(after))
    if snap_parts is not None:
        flags |= 4
        tail += struct.pack("<H", len(snap_parts))
        for uid, blob in snap_parts:
            body_parts.append(blob)
            tail += _str8(uid) + _U32.pack(len(blob))
    if effort == QUICK and step.deltas:
        flags |= QUICK_FLAG
    raw = b"".join(body_parts)
    extra_b = _dumps(extra) if extra else b""
    head = b"".join((
        _FIXED.pack(3, _KINDS[step.kind], flags, 0, int(step.seq), float(step.time or 0.0), len(raw)),
        _str16(step.label or ""), _str8(step.mode or ""), _str8(step.target or ""),
        _U32.pack(len(extra_b)), extra_b, struct.pack("<H", len(parts)), tail))
    body = _codec.compress(raw, codec, strong=strong)
    rest = b"".join((struct.pack("<H", len(head)), head, body))
    blob = b"".join((STEP_MAGIC, _U32.pack(zlib.crc32(rest)), rest))
    if VERIFY and not same_step(step, step_from_bytes(blob)):
        raise AssertionError(f"step {step.seq}: written blob does not read back the same")
    return blob


# --------------------------------------------------------------------------- reading
def _parse_v3(b):
    """-> (meta dict, header info dict, body memoryview). Checks the checksum."""
    mv = memoryview(b)
    if len(mv) < 10:
        raise CorruptStep("step blob: too short")
    (crc,) = _U32.unpack_from(mv, 4)
    if zlib.crc32(mv[8:]) != crc:
        raise CorruptStep("step blob: checksum mismatch")
    try:
        (hl,) = struct.unpack_from("<H", mv, 8)
        h = mv[10:10 + hl]
        if len(h) != hl:
            raise CorruptStep("step blob: header cut short")
        ver, kind, flags, _r, seq, t, raw_len = _FIXED.unpack_from(h, 0)
        if ver != 3 or kind not in _KIND_NAMES or flags & ~15:
            raise CorruptStep("step blob: bad header")
        off = _FIXED.size

        def s8():
            nonlocal off
            n = h[off]
            v = bytes(h[off + 1:off + 1 + n]).decode("utf-8")
            off += 1 + n
            return v

        (n,) = struct.unpack_from("<H", h, off)
        label = bytes(h[off + 2:off + 2 + n]).decode("utf-8")
        off += 2 + n
        mode = s8()
        target = s8()
        (n,) = _U32.unpack_from(h, off)
        extra = json.loads(bytes(h[off + 4:off + 4 + n]).decode("utf-8")) if n else {}
        off += 4 + n
        (nd,) = struct.unpack_from("<H", h, off)
        off += 2
        info = {"deltas": nd, "before": None, "after": None, "snaps": None, "raw": raw_len,
                "quick": bool(flags & QUICK_FLAG)}
        if flags & 1:
            (info["before"],) = _U32.unpack_from(h, off)
            off += 4
        if flags & 2:
            (info["after"],) = _U32.unpack_from(h, off)
            off += 4
        if flags & 4:
            (ns,) = struct.unpack_from("<H", h, off)
            off += 2
            snaps = []
            for _ in range(ns):
                uid = s8()
                (sz,) = _U32.unpack_from(h, off)
                off += 4
                snaps.append([uid, sz])
            info["snaps"] = snaps
        if off != hl:
            raise CorruptStep("step blob: header length mismatch")
    except CorruptStep:
        raise
    except (struct.error, IndexError, UnicodeDecodeError, ValueError) as e:
        raise CorruptStep(f"step blob: bad header ({e})") from e
    meta = {"seq": seq, "label": label, "mode": mode, "target": target, "time": t,
            "kind": _KIND_NAMES[kind], "extra": extra}
    return meta, info, mv[10 + hl:]


def read_header(b) -> dict:
    """Step metadata without decompressing the body."""
    mv = memoryview(b)
    magic = bytes(mv[:4])
    if magic == STEP_MAGIC:
        meta, info, _body = _parse_v3(mv)
        return {"v": 3, "meta": meta, **info}
    if magic == STEP_MAGIC_V1:
        (hl,) = _U32.unpack_from(mv, 4)
        return json.loads(bytes(mv[8:8 + hl]).decode("utf-8"))
    raise CorruptStep("step blob: not a Hindsight step (wrong header)")


def _from_v3(mv) -> Step:
    meta, info, payload = _parse_v3(mv)
    try:
        body = memoryview(_codec.decompress(payload, info["raw"]))
    except _codec.CorruptBlob as e:
        raise CorruptStep(str(e)) from e
    off = 0
    deltas = []
    try:
        for _ in range(info["deltas"]):
            d, off = unpack_at(body, off)
            deltas.append(d)

        def _snap(n):
            nonlocal off
            if n is None:
                return None
            if off + n > len(body):
                raise CorruptStep("step blob: snapshot cut short")
            s = snapshot_from_bytes(body[off:off + n])
            off += n
            return s

        before = _snap(info["before"])
        after = _snap(info["after"])
        extra = dict(meta["extra"])
        if info["snaps"] is not None:
            extra[SNAPSHOTS_KEY] = {uid: _snap(n) for uid, n in info["snaps"]}
    except CorruptStep:
        raise
    except (CorruptDelta, struct.error, ValueError, KeyError, TypeError) as e:
        raise CorruptStep(f"step blob: damaged ({e})") from e
    if off != len(body):
        raise CorruptStep("step blob: unexpected data after the last record")
    return Step(seq=meta["seq"], label=meta["label"], mode=meta["mode"], target=meta["target"],
                time=meta["time"], kind=meta["kind"], deltas=deltas, before=before, after=after,
                extra=extra)


def _from_v1(mv) -> Step:
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


def step_from_bytes(b) -> Step:
    mv = memoryview(b)
    magic = bytes(mv[:4])
    if magic == STEP_MAGIC:
        return _from_v3(mv)
    if magic == STEP_MAGIC_V1:
        return _from_v1(mv)
    raise CorruptStep("step blob: not a Hindsight step (wrong header)")


def version_of(b) -> int:
    """3 for format 3 blobs, 1 for formats 1 and 2, 0 if not a step."""
    magic = bytes(memoryview(b)[:4])
    return 3 if magic == STEP_MAGIC else (1 if magic == STEP_MAGIC_V1 else 0)


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
    form: XOR deltas only equal XOR deltas, ADD only ADD)."""
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
    ea, eb = dict(a.extra or {}), dict(b.extra or {})
    sa, sb = ea.pop(SNAPSHOTS_KEY, None), eb.pop(SNAPSHOTS_KEY, None)
    if json.loads(_dumps(ea)) != json.loads(_dumps(eb)):
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
    and must give exactly the same data. None if there's nothing to gain."""
    mv = memoryview(b)
    v = version_of(mv)
    if v == 0:
        return None
    if v == 3:
        _meta, info, payload = _parse_v3(mv)
        if _codec.is_strong(payload) and not info["quick"]:
            return None
        codec = _codec.codec_of(payload)
    else:
        codec = "auto"
    step = step_from_bytes(mv)
    new = step_to_bytes(step, codec, NORMAL, strong=True)
    if len(new) >= len(b) or not same_step(step, step_from_bytes(new)):
        return None
    return new
