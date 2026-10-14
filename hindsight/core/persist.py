"""Registry serialisation, sidecar folder and journal replay (pure Python, no bpy).

Registry blob (``history.hsr`` / embedded ID property):

    b"HSR1" | u32 json_len | JSON | for each history, for each entry: u64 len | Entry.to_bytes()

JSON = {"version", "format", "writer", "next_seq", "use_global", "preserve_modes", "by_object",
        "disabled", "histories": [{"uid", "members", "cursor", "enabled", "morph_target",
                                   "n_entries", "metas": [entry.meta, ...], "crcs": [...]}]}

Entry metas are stored in the JSON so loading never unpacks a step: entries come back as
``Entry(blob=..., meta=...)``.

Format versions (``format``, falling back to ``version`` for files written before it existed):
  1  beta 1. No checksums.
  2  beta 2 work in progress. Adds ``format``, ``writer`` and a CRC-32 per step (``crcs``). The
     binary layout is unchanged, so beta 1 can still read format 2 files (it ignores the new keys).
  3  beta 2. New steps use the format 3 step blob (see step.py: smaller, with its own checksum), and
     the file ends with b"HSRT" + u32 CRC-32 of the JSON header. Beta 1 can open these files but
     can't play back format 3 steps; it saves them back unchanged.
Loading formats 1 and 2 needs no conversion: their steps are read as they are (and re-packed in the
background later, see store.Packer); every step gets a checksum on the next save.
A file with a format newer than FORMAT_VERSION is refused with NewerFormatError (nothing is
guessed and nothing is overwritten). Damaged steps are never applied: a history keeps the steps
before the first damaged one and the loss is listed in ``reg.load_report``.
"""
from __future__ import annotations

import json
import os
import struct
import time
import zlib

import numpy as np

from .journal import atomic_write, atomic_write_json
from .types import Entry

REG_MAGIC = b"HSR1"
FORMAT_VERSION = 3
REG_VERSION = FORMAT_VERSION   # kept for older imports
TRAILER = b"HSRT"
WRITER = "Hindsight"            # replaced with "Hindsight <version>" by the add-on at register time


class NewerFormatError(ValueError):
    """The history was written by a newer Hindsight; this one must not guess at it."""

    def __init__(self, found: int):
        super().__init__(f"history format {found} is newer than this Hindsight understands ({FORMAT_VERSION})")
        self.found = found
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
        crcs = []
        for e in h.entries:
            b = e.to_bytes(codec, 0)            # raw steps: the quick form (main thread)
            blobs.append(b)
            crcs.append(zlib.crc32(b) & 0xFFFFFFFF)
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
            "crcs": crcs,
        })
    doc = {
        "version": FORMAT_VERSION,
        "format": FORMAT_VERSION,
        "writer": WRITER,
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
    parts += [TRAILER, _U32.pack(zlib.crc32(js) & 0xFFFFFFFF)]
    return b"".join(parts)


def registry_from_bytes(b: bytes):
    """Parse a registry blob. Raises ValueError if it is not a Hindsight blob at all, and
    NewerFormatError for a format from the future. Damaged or missing steps are dropped (never
    applied) and reported in ``reg.load_report``."""
    from .history import History
    from .registry import Registry

    mv = memoryview(b)
    if len(mv) < 8 or bytes(mv[:4]) != REG_MAGIC:
        raise ValueError("not a Hindsight registry blob")
    (jlen,) = _U32.unpack_from(mv, 4)
    pos = 8 + jlen
    if pos > len(mv):
        raise ValueError("truncated Hindsight registry blob (header)")
    try:
        doc = json.loads(bytes(mv[8:pos]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"damaged Hindsight registry header: {exc}") from exc
    found = int(doc.get("format", doc.get("version", 1)) or 1)
    if found > FORMAT_VERSION:
        raise NewerFormatError(found)
    if found >= 3 and len(mv) >= pos + 8 and bytes(mv[-8:-4]) == TRAILER:
        (want,) = _U32.unpack_from(mv, len(mv) - 4)
        if zlib.crc32(mv[8:pos]) & 0xFFFFFFFF != want:
            raise ValueError("damaged Hindsight registry header (checksum)")

    reg = Registry()
    report = {"format": found, "writer": doc.get("writer", "Hindsight 1.0.0-beta.1 or older"),
              "migrated": found < FORMAT_VERSION, "damaged": []}
    reg.next_seq = int(doc.get("next_seq", 1))
    reg.use_global = bool(doc.get("use_global", False))
    reg.preserve_modes = bool(doc.get("preserve_modes", True))
    reg.by_object = dict(doc.get("by_object", {}))
    if hasattr(reg, "disabled"):
        reg.disabled = set(doc.get("disabled", []))
    max_seq = 0
    truncated = False
    for hd in doc.get("histories", []):
        h = History(hd["uid"], list(hd.get("members", [])))
        metas = hd.get("metas", [])
        crcs = hd.get("crcs")
        want = int(hd.get("n_entries", 0))
        entries = []
        bad_at = None
        for i in range(want):
            if truncated or pos + 8 > len(mv):
                truncated = True
                bad_at = i if bad_at is None else bad_at
                break
            (n,) = _U64.unpack_from(mv, pos)
            pos += 8
            if pos + n > len(mv):
                truncated = True
                bad_at = i if bad_at is None else bad_at
                break
            blob = bytes(mv[pos:pos + n])
            pos += n
            if bad_at is not None:
                continue   # keep reading past it so later histories stay aligned
            if crcs is not None and i < len(crcs) and (zlib.crc32(blob) & 0xFFFFFFFF) != int(crcs[i]):
                bad_at = i
                continue
            meta = metas[i] if i < len(metas) else None
            if meta is None:
                try:
                    from .step import step_from_bytes
                    meta = step_from_bytes(blob).meta()
                except Exception:
                    bad_at = i
                    continue
            entries.append(Entry(blob=blob, meta=meta))
            try:
                max_seq = max(max_seq, int(meta.get("seq", 0)))
            except (TypeError, ValueError):
                pass
        if bad_at is not None:
            report["damaged"].append({"uid": h.uid, "members": list(h.members), "kept": len(entries),
                                      "lost": want - len(entries)})
        h.entries = entries
        h.cursor = int(hd.get("cursor", len(entries)))
        h.enabled = bool(hd.get("enabled", True))
        mt = hd.get("morph_target")
        h.morph_target = None if mt is None else int(mt)
        _clamp_hist(h)
        reg.histories[h.uid] = h
    if reg.next_seq <= max_seq:
        reg.next_seq = max_seq + 1
    reg.load_report = report
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
        "version": FORMAT_VERSION,
        "format": FORMAT_VERSION,
        "writer": WRITER,
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


def sidecar_file(blend_path: str) -> str:
    return os.path.join(sidecar_dir(blend_path), HSR_NAME)


def set_aside(path: str) -> str | None:
    """Rename an unreadable history file so the next save can't overwrite it. Returns the new name."""
    if not os.path.isfile(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.unreadable-{stamp}"
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = f"{path}.unreadable-{stamp}-{n}"
    os.replace(path, dst)
    return dst


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
