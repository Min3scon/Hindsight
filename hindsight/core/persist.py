"""Registry serialisation, sidecar folder and journal replay (pure Python, no bpy).

Registry blob (``history.hsr`` / embedded ID property):

    b"HSR1" | u32 json_len | JSON | for each history, for each entry: u64 len | Entry.to_bytes()

JSON = {"version", "next_seq", "use_global", "preserve_modes", "by_object", "disabled",
        "histories": [{"uid", "members", "cursor", "enabled", "morph_target", "n_entries",
                       "metas": [entry.meta, ...]}]}

Entry metas are stored in the JSON so loading never unpacks a step: entries come back as
``Entry(blob=..., meta=...)``.
"""
from __future__ import annotations

import json
import os
import struct
import time

import numpy as np

from .journal import atomic_write, atomic_write_json
from .types import Entry

REG_MAGIC = b"HSR1"
REG_VERSION = 1
HSR_NAME = "history.hsr"
INDEX_NAME = "index.json"

_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return None   # non-serialisable (e.g. Snapshot objects); the blob keeps the real data


def _meta_for_json(meta: dict) -> dict:
    m = dict(meta or {})
    extra = m.get("extra")
    if isinstance(extra, dict) and "_snapshots" in extra:
        m["extra"] = {k: v for k, v in extra.items() if k != "_snapshots"}
    return m


def _clamp_hist(h) -> None:
    n = len(h.entries)
    if h.cursor > n:
        h.cursor = n
    if h.cursor < 0:
        h.cursor = 0
    mt = getattr(h, "morph_target", None)
    if mt is not None and (mt > n or mt < 0):
        h.morph_target = None


# ---------------------------------------------------------------------------
# registry <-> bytes
# ---------------------------------------------------------------------------
def registry_to_bytes(reg, codec: str = "auto") -> bytes:
    hists = list(reg.histories.values())
    blobs = []
    hjson = []
    for h in hists:
        metas = []
        for e in h.entries:
            blobs.append(e.to_bytes(codec))
            metas.append(_meta_for_json(e.meta))
        mt = h.morph_target
        hjson.append({
            "uid": h.uid,
            "members": list(h.members),
            "cursor": int(h.cursor),
            "enabled": bool(h.enabled),
            "morph_target": None if mt is None else int(mt),
            "n_entries": len(h.entries),
            "metas": metas,
        })
    doc = {
        "version": REG_VERSION,
        "next_seq": int(reg.next_seq),
        "use_global": bool(reg.use_global),
        "preserve_modes": bool(reg.preserve_modes),
        "by_object": dict(reg.by_object),
        "disabled": sorted(getattr(reg, "disabled", ()) or ()),
        "histories": hjson,
    }
    js = json.dumps(doc, separators=(",", ":"), default=_json_default).encode("utf-8")
    parts = [REG_MAGIC, _U32.pack(len(js)), js]
    for b in blobs:
        parts.append(_U64.pack(len(b)))
        parts.append(bytes(b))
    return b"".join(parts)


def registry_from_bytes(b: bytes):
    from .history import History
    from .registry import Registry

    mv = memoryview(b)
    if len(mv) < 8 or bytes(mv[:4]) != REG_MAGIC:
        raise ValueError("not a Hindsight registry blob")
    (jlen,) = _U32.unpack_from(mv, 4)
    pos = 8 + jlen
    if pos > len(mv):
        raise ValueError("truncated Hindsight registry blob")
    doc = json.loads(bytes(mv[8:pos]).decode("utf-8"))

    reg = Registry()
    reg.next_seq = int(doc.get("next_seq", 1))
    reg.use_global = bool(doc.get("use_global", False))
    reg.preserve_modes = bool(doc.get("preserve_modes", True))
    reg.by_object = dict(doc.get("by_object", {}))
    if hasattr(reg, "disabled"):
        reg.disabled = set(doc.get("disabled", []))
    max_seq = 0
    for hd in doc.get("histories", []):
        h = History(hd["uid"], list(hd.get("members", [])))
        metas = hd.get("metas", [])
        entries = []
        for i in range(int(hd.get("n_entries", 0))):
            if pos + 8 > len(mv):
                raise ValueError("truncated Hindsight registry blob")
            (n,) = _U64.unpack_from(mv, pos)
            pos += 8
            if pos + n > len(mv):
                raise ValueError("truncated Hindsight registry blob")
            blob = bytes(mv[pos:pos + n])
            pos += n
            meta = metas[i] if i < len(metas) else None
            if meta is None:
                from .step import step_from_bytes
                meta = step_from_bytes(blob).meta()
            entries.append(Entry(blob=blob, meta=meta))
            try:
                max_seq = max(max_seq, int(meta.get("seq", 0)))
            except (TypeError, ValueError):
                pass
        h.entries = entries
        h.cursor = int(hd.get("cursor", len(entries)))
        h.enabled = bool(hd.get("enabled", True))
        mt = hd.get("morph_target")
        h.morph_target = None if mt is None else int(mt)
        _clamp_hist(h)
        reg.histories[h.uid] = h
    if reg.next_seq <= max_seq:
        reg.next_seq = max_seq + 1
    return reg


# ---------------------------------------------------------------------------
# sidecar folder
# ---------------------------------------------------------------------------
def sidecar_dir(blend_path: str) -> str:
    d = os.path.dirname(os.path.abspath(blend_path))
    stem = os.path.splitext(os.path.basename(blend_path))[0]
    return os.path.join(d, stem + "_hindsight")


def save_sidecar(reg, blend_path: str) -> None:
    from .types import SETTINGS
    folder = sidecar_dir(blend_path)
    os.makedirs(folder, exist_ok=True)
    data = registry_to_bytes(reg, getattr(SETTINGS, "compression", "auto") or "auto")
    atomic_write(os.path.join(folder, HSR_NAME), data)
    atomic_write_json(os.path.join(folder, INDEX_NAME), {
        "version": REG_VERSION,
        "blend": os.path.basename(blend_path),
        "saved_at": time.time(),
    })


def load_sidecar(blend_path: str):
    path = os.path.join(sidecar_dir(blend_path), HSR_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        data = fh.read()
    return registry_from_bytes(data)


def sidecar_missing(blend_path: str, expected: bool) -> bool:
    return bool(expected) and not os.path.isdir(sidecar_dir(blend_path))


# ---------------------------------------------------------------------------
# journal replay
# ---------------------------------------------------------------------------
def replay(reg, records: list) -> int:
    """Apply journal records after the last SAVE onto reg. Never touches meshes; each
    history's cursor keeps its saved value (only clamped to len(entries)). The last
    journaled cursor is kept in ``h.recovered_cursor``. Returns records applied."""
    from .history import History

    start = 0
    for i, rec in enumerate(records):
        if rec[0] == "SAVE":
            start = i + 1
    known_members = {}
    for rtype, header, _p in records[:start]:
        if rtype == "META" and header.get("hist") and header.get("members") is not None:
            known_members[header["hist"]] = list(header["members"])

    def get_hist(uid, target=None):
        h = reg.histories.get(uid)
        if h is not None:
            return h
        members = known_members.get(uid)
        if not members:
            members = [target] if target else []
        h = History(uid, list(members))
        reg.histories[uid] = h
        for m in members:     # only real object uids (History may default members to [uid])
            reg.by_object.setdefault(m, uid)
        return h

    max_seq = None
    applied = 0
    for rtype, header, payload in records[start:]:
        header = header or {}
        uid = header.get("hist")
        if rtype == "STEP":
            if not uid:
                continue
            from .step import step_from_bytes
            step = step_from_bytes(payload)
            h = get_hist(uid, step.target)
            at = int(header.get("at", len(h.entries)))
            if 0 <= at < len(h.entries):
                h.entries = h.entries[:at]
            h.entries.append(Entry(step=step))
            _clamp_hist(h)
            max_seq = step.seq if max_seq is None else max(max_seq, step.seq)
        elif rtype == "CURSOR":
            if not uid:
                continue
            h = get_hist(uid)
            h.recovered_cursor = int(header.get("cursor", 0))
        elif rtype == "TRUNC":
            if not uid:
                continue
            h = get_hist(uid)
            keep = max(0, int(header.get("keep", len(h.entries))))
            h.entries = h.entries[:keep]
            _clamp_hist(h)
        elif rtype == "DROP":
            if not uid:
                continue
            h = get_hist(uid)
            n = max(0, min(int(header.get("n", 0)), len(h.entries)))
            if n:
                if callable(getattr(h, "drop_oldest", None)):
                    h.drop_oldest(n)
                else:
                    h.entries = h.entries[n:]
                    h.cursor = max(0, h.cursor - n)
                    mt = h.morph_target
                    h.morph_target = None if mt is None or mt - n < 0 else mt - n
            _clamp_hist(h)
        elif rtype == "RESET":
            if not uid:
                continue
            h = get_hist(uid)
            h.entries = []
            h.cursor = 0
            h.morph_target = None
        elif rtype == "META":
            if not uid:
                continue
            h = get_hist(uid)
            if header.get("members") is not None:
                h.members = list(header["members"])
                known_members[uid] = list(h.members)
                for m in h.members:
                    reg.by_object[m] = uid
            if "enabled" in header:
                h.enabled = bool(header["enabled"])
            if "morph_target" in header:
                mt = header["morph_target"]
                h.morph_target = None if mt is None else int(mt)
            if header.get("objects") is not None:
                h.recovered_objects = header["objects"]
            _clamp_hist(h)
        else:
            continue
        applied += 1
    if max_seq is not None and reg.next_seq <= max_seq:
        reg.next_seq = int(max_seq) + 1
    return applied
