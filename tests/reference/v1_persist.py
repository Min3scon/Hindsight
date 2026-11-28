"""Registry serialisation, sidecar folder and journal replay (pure Python, no bpy).

Registry blob (``history.hsr`` / embedded ID property):

    b"HSR1" | u32 json_len | JSON | for each history, for each entry: u64 len | Entry.to_bytes()

JSON = {"version", "format", "writer", "next_seq", "use_global", "preserve_modes", "by_object",
        "disabled", "everything", "histories": [{"uid", "members", "cursor", "enabled", "morph_target",
                                   "n_entries", "metas": [entry.meta, ...], "crcs": [...]}]}

Entry metas are stored in the JSON so loading never unpacks a step: entries come back as
``Entry(blob=..., meta=...)``.

Format versions (``format``, falling back to ``version`` for files written before it existed):
  1  beta 1. No checksums.
  2  beta 2 work in progress. Adds ``format``, ``writer`` and a CRC-32 per step (``crcs``). The
     binary layout is unchanged, so beta 1 can still read format 2 files (it ignores the new keys).
  3  beta 2 work in progress. New steps use the format 3 step blob (see step.py: smaller, with its
     own checksum), and the file ends with b"HSRT" + u32 CRC-32 of the JSON header. Beta 1 can open
     these files but can't play back format 3 steps; it saves them back unchanged.
  4  beta 2. Steps may carry data records (adapters other than the mesh recorder; step blob
     version 4). Otherwise the same as format 3.
Loading formats 1 and 2 needs no conversion: their steps are read as they are (and re-packed in the
background later, see store.Packer); every step gets a checksum on the next save.
A file with a format newer than FORMAT_VERSION is refused with NewerFormatError (nothing is
guessed and nothing is overwritten). Damaged steps are never applied: a history keeps the steps
between the damaged ones that are reachable from the saved position, and the loss is listed
in ``reg.load_report``.
"""
from __future__ import annotations

import contextlib
import binascii
import json
import os
import struct
import time
import zlib

import numpy as np

from .v1_journal import atomic_write, atomic_write_json
from .types import Entry

REG_MAGIC = b"HSR1"
FORMAT_VERSION = 4
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
    return b"".join(registry_parts(reg, codec))


def registry_parts(reg, codec: str = "auto") -> list:
    """The registry blob as a list of buffers (the step blobs themselves, not copies): written to a
    file one after another, the history is never held twice while saving."""
    hists = list(reg.histories.values())
    blobs = []
    hjson = []
    for h in hists:
        metas = []
        crcs = []
        for e in h.entries:
            b, crc = e.blob_crc()               # R046: packed steps carry their CRC-32
            if b is None:
                b = e.to_bytes(codec, 0)        # raw steps: the quick form (main thread)
                crc = zlib.crc32(b) & 0xFFFFFFFF
            blobs.append(b)
            crcs.append(crc)
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
        "everything": sorted(getattr(reg, "everything", ()) or ()),
        "saved": getattr(reg, "saved", None) or {},
        "histories": hjson,
    }
    sid = getattr(reg, "save_id", None)
    if sid:
        doc["save_id"] = str(sid)       # ties the crash journal to this save (ignored by older builds)
    js = json.dumps(doc, separators=(",", ":"), default=_json_default).encode("utf-8")
    parts = [REG_MAGIC, _U32.pack(len(js)), js]
    for b in blobs:
        parts.append(_U64.pack(len(b)))
        parts.append(b)
    parts += [TRAILER, _U32.pack(zlib.crc32(js) & 0xFFFFFFFF)]
    return parts


_B64_CHUNK = 3 << 16     # bytes encoded per call (a multiple of 3, so the pieces join up exactly)


def parts_to_b64(parts) -> str:
    """base64 of the buffers in `parts` joined, as ASCII text: exactly
    ``base64.b64encode(b"".join(parts)).decode("ascii")``, but the joined bytes and a second full-size
    encoded copy are never made. The text is written piece by piece into one buffer of its final size
    and decoded once, so the peak is the encoded size twice instead of the history plus that twice."""
    total = sum(memoryview(p).nbytes for p in parts)
    out = bytearray(4 * ((total + 2) // 3))
    o = 0
    head = b""                  # up to 2 bytes left over from the previous buffer
    enc = binascii.b2a_base64
    for p in parts:
        mv = memoryview(p).cast("B")
        if head:
            need = 3 - len(head)
            if len(mv) < need:
                head += bytes(mv)
                continue
            e = enc(head + bytes(mv[:need]), newline=False)
            out[o:o + 4] = e
            o += 4
            mv = mv[need:]
        n = len(mv) - len(mv) % 3
        for i in range(0, n, _B64_CHUNK):
            e = enc(mv[i:min(i + _B64_CHUNK, n)], newline=False)
            out[o:o + len(e)] = e
            o += len(e)
        head = bytes(mv[n:])
    if head:
        e = enc(head, newline=False)
        out[o:o + len(e)] = e
        o += len(e)
    if o != len(out):           # never: the size is exact
        raise AssertionError("base64 size mismatch")
    return out.decode("ascii")


class _FileBytes:
    """A history file read in slices (each slice is read when asked for), so loading holds each
    step once instead of the whole file plus every step."""

    def __init__(self, fh):
        self._fh = fh
        self._n = os.fstat(fh.fileno()).st_size
        self._pos = None              # where the file is now (steps are read in order: no seeks)

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, sl: slice) -> bytes:
        start, stop = sl.start, sl.stop
        if start is None or stop is None or start < 0 or stop < 0 or stop > self._n:
            start, stop, _step = sl.indices(self._n)
        if start != self._pos:
            self._fh.seek(start)
        out = self._fh.read(max(0, stop - start))
        self._pos = start + len(out)
        return out


def registry_from_bytes(b):
    """Parse a registry blob (bytes, or a history file through _FileBytes). Raises ValueError if it
    is not a Hindsight blob at all, and NewerFormatError for a format from the future. Damaged or
    missing steps are dropped (never applied) and reported in ``reg.load_report``."""
    from .history import History
    from hindsight.core.registry import Registry

    mv = b if isinstance(b, _FileBytes) else memoryview(b)
    if len(mv) < 8 or bytes(mv[:4]) != REG_MAGIC:
        raise ValueError("not a Hindsight registry blob")
    (jlen,) = _U32.unpack(mv[4:8])
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
        (want,) = _U32.unpack(mv[len(mv) - 4:])
        if zlib.crc32(mv[8:pos]) & 0xFFFFFFFF != want:
            raise ValueError("damaged Hindsight registry header (checksum)")

    reg = Registry()
    report = {"format": found, "writer": doc.get("writer", "Hindsight 1.0.0-beta.1 or older"),
              "migrated": found < FORMAT_VERSION, "damaged": []}
    reg.next_seq = int(doc.get("next_seq", 1))
    reg.use_global = bool(doc.get("use_global", False))
    reg.preserve_modes = bool(doc.get("preserve_modes", True))
    reg.by_object = dict(doc.get("by_object", {}))
    saved = doc.get("saved")
    reg.saved = dict(saved) if isinstance(saved, dict) else {}
    sid = doc.get("save_id")
    reg.save_id = str(sid) if sid else None
    if hasattr(reg, "disabled"):
        reg.disabled = set(doc.get("disabled", []))
    if hasattr(reg, "everything"):
        reg.everything = set(doc.get("everything", []))
    max_seq = 0
    truncated = False
    size = len(mv)
    for hd in doc.get("histories", []):
        h = History(hd["uid"], list(hd.get("members", [])))
        metas = hd.get("metas", [])
        crcs = hd.get("crcs")
        want = int(hd.get("n_entries", 0))
        slots = []       # per stored step: its Entry, or None when it is damaged or missing
        for i in range(want):
            if truncated or pos + 8 > size:
                truncated = True
                slots.append(None)
                continue
            (n,) = _U64.unpack(mv[pos:pos + 8])
            pos += 8
            if pos + n > size:
                truncated = True
                slots.append(None)
                continue
            blob = bytes(mv[pos:pos + n])
            pos += n
            crc = zlib.crc32(blob) & 0xFFFFFFFF
            if crcs is not None and i < len(crcs) and crc != int(crcs[i]):
                slots.append(None)
                continue
            meta = metas[i] if i < len(metas) else None
            if meta is None:
                try:
                    from .step import step_from_bytes
                    meta = step_from_bytes(blob).meta()
                except Exception:
                    slots.append(None)
                    continue
            slots.append(Entry(blob=blob, meta=meta, crc=crc))
            with contextlib.suppress(TypeError, ValueError):
                max_seq = max(max_seq, int(meta.get("seq", 0)))
        saved_cursor = max(0, min(int(hd.get("cursor", want)), want))
        bad = [i for i, e in enumerate(slots) if e is None]
        mt = hd.get("morph_target")
        mt = None if mt is None else int(mt)
        if not bad:
            entries, cursor = slots, saved_cursor
        else:
            # Every step undoes and redoes itself, so a damaged step cuts the history in two: the
            # object was saved after the steps up to the cursor, and only the steps between the last
            # damaged one before the cursor and the first damaged one after it can be reached from
            # there. That run is kept (as if the oldest steps had been dropped by the step cap).
            early = [i for i in bad if i < saved_cursor]
            late = [i for i in bad if i >= saved_cursor]
            lo = early[-1] + 1 if early else 0
            hi = late[0] if late else want
            entries, cursor = slots[lo:hi], saved_cursor - lo
            if mt is not None:
                mt = mt - lo if lo <= mt <= hi else None
            report["damaged"].append({"uid": h.uid, "members": list(h.members), "kept": len(entries),
                                      "lost": want - len(entries), "before_saved": bool(early),
                                      "lost_before": lo})
        h.entries = entries
        h.cursor = cursor
        h.enabled = bool(hd.get("enabled", True))
        h.morph_target = mt
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
    parts = registry_parts(reg, getattr(SETTINGS, "compression", "auto") or "auto")
    atomic_write(os.path.join(folder, HSR_NAME), parts)
    with contextlib.suppress(OSError):
        # A note for people looking into the folder; loading never reads it. The history is saved
        # once history.hsr is in place, so failing to update the note doesn't fail the save.
        atomic_write_json(os.path.join(folder, INDEX_NAME), {
            "version": FORMAT_VERSION,
            "format": FORMAT_VERSION,
            "writer": WRITER,
            "blend": os.path.basename(blend_path),
            "saved_at": time.time(),
            "save_id": getattr(reg, "save_id", None),
        })


def load_sidecar(blend_path: str):
    path = os.path.join(sidecar_dir(blend_path), HSR_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, "rb", buffering=1 << 20) as fh:   # the steps are read one by one
        return registry_from_bytes(_FileBytes(fh))


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
def journal_belongs(reg, records: list) -> bool:
    """Whether the journal records were written on top of this very save of the file.

    Every save gives the history a new ``save_id`` and writes it into the SAVE record that starts the
    journal again. A journal whose last SAVE names another save (the .blend was replaced by another
    save of the same path: a .blend1 renamed back, a copy, a crash between writing the file and
    resetting the journal) describes positions in a different history and must not be replayed.
    Files and journals of builds without save ids have none on either side and match as before."""
    sid = None
    found = False
    for rtype, header, _p in records:
        if rtype == "SAVE":
            found = True
            sid = (header or {}).get("save_id") or None
    rid = getattr(reg, "save_id", None) or None
    if not found:
        return not records or rid is None
    return sid == rid


def _group(e) -> int:
    """The global position (one operation) a step belongs to: extra "op", or its own number."""
    meta = getattr(e, "meta", None)
    ex = (meta.get("extra") if isinstance(meta, dict) else getattr(e, "extra", None)) or {}
    try:
        return int(ex.get("op", e.seq))
    except (TypeError, ValueError):
        return e.seq


def replay(reg, records: list) -> int:
    """Apply journal records after the last SAVE onto reg. Returns records applied.

    Never touches meshes. The file on disk holds every mesh as it was at the last save, so each
    history has to come out with that state at its cursor and the steps recorded since then as steps
    ahead of it. The records describe what the session did to its own history, which can leave the
    saved state behind. Three cases get special care:
    - The session dropped more of the oldest steps than lie before the saved state (step cap, memory
      limit, Delete earlier steps). Those before it are dropped; the rest stay, and later positions in
      the records are shifted by their count (``skew``).
    - The session went back past the saved state and recorded a new step, which removed the steps
      leading to the saved state, or cleared the history while it stood somewhere else. Nothing
      recorded from there on can be reached from the saved mesh: that history stops being replayed
      (``stopped``) and keeps what it had (or stays empty after a clear).
    - A join rewrites the target's history as RESET plus every step again. Steps known from before
      are recognised by their number, and the cursor goes after the ones that are part of the file.
    - A join whose merged history lacks steps that lead from the file's state of one of its objects
      (that object's history was stopped, or the session had dropped steps recovery kept) has no
      position for the file's state either: it is left out too.
    - The steps of one operation in several histories are one global position (extra "op"). When one
      of them is left out, the others go too, with what follows them, so the global timeline never
      plays half an operation.
    A META record with "resync" and no history (written when a recovered file is opened) says that the
    session went on from the recovered state, so all of this bookkeeping starts again there.
    Records carry the id of the session that wrote them ("s"). The session named by the last SAVE (or a
    later "resync") owns the journal; records of another session (the same file open in a second
    Blender) are not replayed. Records without one (older builds) are. Opening a
    file that was changed without Hindsight writes what recovery needs to know about it: "mesh" in the
    RESET of a history left out (the fingerprints its later steps start from), "file_at" for a history
    that got a step for changed values (the file's state is after that step), "saved" for the values.

    ``reg.replay_report`` is {"stopped": {history uid: steps left out}}. The last journaled cursor of a
    history is kept in ``h.recovered_cursor``."""
    from .history import History
    from .step import step_from_bytes

    start = 0
    for i, rec in enumerate(records):
        if rec[0] == "SAVE":
            start = i + 1
    owner = (records[start - 1][1] or {}).get("s") if start else None
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

    skew = {}        # uid -> steps kept at the head that the session had dropped
    stopped = {}     # uid -> steps left out since replaying this history stopped
    live = {}        # uid -> the session's own cursor, as a position in the recovered history
    resets = {}      # uid -> [was the session at the saved state?, steps put back, sources replayed
                     #         in full?, numbers of the file's steps that must come back]
    home = {}        # step number -> (part of the file's state?, from a history replayed in full?)
    absorbed = []    # (history, replayed in full?, file's steps) of those a join cleared just before
    left_ops = set()  # operations (global positions) that lost a step

    def settle(h):
        """Decide what the last RESET of h was, once the records after it show it."""
        st = resets.pop(h.uid, None)
        if st is None:
            return
        synced, n, whole, need = st
        if n == 0:
            if not synced:
                stopped.setdefault(h.uid, 0)          # cleared somewhere else: later steps don't fit
            return
        flags = [home.get(e.seq) for e in h.entries[:n]]
        p = 0
        while p < n and flags[p] is not None and flags[p][0]:
            p += 1
        back = {e.seq for e in h.entries[:n]}
        if (whole and need <= back and all(f is not None and f[1] for f in flags)
                and not any(f[0] for f in flags[p:])):
            h.cursor = p
        else:           # the steps of the file's state aren't one run at the start: no place for it
            stopped[h.uid] = stopped.get(h.uid, 0) + len(h.entries)
            left_ops.update(_group(e) for e in h.entries)
            h.entries = []
            h.cursor = 0
        _clamp_hist(h)

    max_seq = None
    applied = 0
    for rtype, header, payload in records[start:]:
        header = header or {}
        uid = header.get("hist")
        if rtype == "META" and not uid and header.get("resync"):
            owner = header.get("s", owner)          # a recovered session took the journal over
        elif owner is not None and header.get("s", owner) != owner:
            continue                                # written by another Blender with the file open
        if rtype not in ("RESET", "META", "STEP"):
            absorbed.clear()
        if rtype == "STEP":
            if not uid:
                continue
            if absorbed:
                # the first step after a join cleared the histories it took in. Without a RESET of
                # this one (they had no steps left) this is the join itself: it starts from their
                # objects as the session had them, which is the file's state only if they were
                # replayed in full
                if absorbed[-1][0] != uid and not all(c for _u, c, _s in absorbed) and uid not in stopped:
                    stopped[uid] = 0
                absorbed.clear()
            step = step_from_bytes(payload)
            h = get_hist(uid, step.target)
            max_seq = step.seq if max_seq is None else max(max_seq, step.seq)
            st = resets.get(uid)
            if st is not None:
                if step.seq in home and uid not in stopped:
                    st[1] += 1                       # a step the history had before: put back
                else:
                    settle(h)
            k = skew.get(uid, 0)
            at = int(header.get("at", len(h.entries) - k)) + k
            if uid not in stopped and at < h.cursor:
                stopped[uid] = 0                     # replaces steps that lead to the saved state
            if uid in stopped:
                stopped[uid] += 1
                left_ops.add(_group(step))
                continue
            if 0 <= at < len(h.entries):
                h.entries = h.entries[:at]
            h.entries.append(Entry(step=step))
            live[uid] = len(h.entries)
            _clamp_hist(h)
        elif rtype == "CURSOR":
            if not uid:
                continue
            h = get_hist(uid)
            h.recovered_cursor = int(header.get("cursor", 0))
            live[uid] = h.recovered_cursor + skew.get(uid, 0)
        elif rtype == "TRUNC":
            if not uid:
                continue
            h = get_hist(uid)
            settle(h)
            keep = max(0, int(header.get("keep", len(h.entries)))) + skew.get(uid, 0)
            if uid not in stopped and keep < h.cursor:
                stopped[uid] = 0                     # removes steps that lead to the saved state
            if uid in stopped:
                continue
            h.entries = h.entries[:keep]
            _clamp_hist(h)
        elif rtype == "DROP":
            if not uid:
                continue
            h = get_hist(uid)
            settle(h)
            if uid in stopped:
                continue
            k = skew.get(uid, 0)
            n = max(0, min(int(header.get("n", 0)), len(h.entries) - k))
            d = min(n, h.cursor)                     # only steps before the saved state may go
            mt = h.morph_target
            if d:
                h.drop_oldest(d)
            if mt is not None and mt - k < n:
                h.morph_target = None                # the session dropped its morph target
            if n > d:
                skew[uid] = k + n - d
            if uid in live:
                live[uid] = max(0, live[uid] - d)
            _clamp_hist(h)
        elif rtype == "RESET":
            if not uid:
                continue
            h = get_hist(uid)
            settle(h)
            clean = uid not in stopped and not skew.get(uid)
            at = header.get("at")
            where = live.get(uid, h.cursor) if at is None else int(at) + skew.get(uid, 0)
            for i, e in enumerate(h.entries):
                home[e.seq] = (i < h.cursor, clean)
            infile = {e.seq for e in h.entries[:h.cursor]}
            if at is None and "mesh" not in header:
                # a join (joinsep writes no "at"): the histories it cleared just before go into this
                # one. The file's state of their objects is only reachable if every one of them, this
                # one too, was replayed in full, and once every step of the file's state is back
                # (a crash can stop the join half way through writing them)
                whole = clean and all(c for _u, c, _s in absorbed)
                need = infile.union(*(s for _u, _c, s in absorbed))
                resets[uid] = [clean and where == h.cursor, 0, whole, need]
                absorbed.append((uid, clean, infile))
            else:
                resets[uid] = [clean and where == h.cursor, 0, True, set()]
            prints = header.get("mesh")          # cleared at load because a mesh was changed outside
            if isinstance(prints, dict) and isinstance(getattr(reg, "saved", None), dict):
                for m, fp in prints.items():     # later steps were recorded on that mesh
                    rec = reg.saved.get(m)
                    reg.saved[m] = dict(rec, mesh=fp) if isinstance(rec, dict) else {"mesh": fp}
            h.entries = []
            h.cursor = 0
            h.morph_target = None
            skew.pop(uid, None)
            live[uid] = 0
        elif rtype == "META_EVERYTHING":
            t = header.get("uid")
            if t and hasattr(reg, "everything"):
                (reg.everything.add if header.get("on") else reg.everything.discard)(t)
        elif rtype == "META":
            if not uid:
                if header.get("resync"):
                    for u in list(resets):
                        settle(reg.histories[u])
                    skew.clear(); stopped.clear(); live.clear()
                    applied += 1
                elif isinstance(header.get("saved"), dict):      # objects as the file holds them now
                    if isinstance(getattr(reg, "saved", None), dict):
                        reg.saved.update(header["saved"])
                    applied += 1
                continue
            h = get_hist(uid)
            if "file_at" in header and uid not in stopped:
                # a step made when the file was opened, for changes the file already holds
                settle(h)
                h.cursor = int(header["file_at"]) + skew.get(uid, 0)
                live[uid] = h.cursor
            if header.get("members") is not None:
                h.members = list(header["members"])
                known_members[uid] = list(h.members)
                for m in h.members:
                    reg.by_object[m] = uid
            if "enabled" in header:
                h.enabled = bool(header["enabled"])
            if "morph_target" in header and uid not in stopped:
                mt = header["morph_target"]
                h.morph_target = None if mt is None else int(mt) + skew.get(uid, 0)
            if header.get("objects") is not None:
                h.recovered_objects = header["objects"]
            _clamp_hist(h)
        else:
            continue
        applied += 1
    for u in list(resets):
        settle(reg.histories[u])
    # A crash in the middle of a join can leave an object pointing at a history that no longer lists
    # it (the join cleared it and was cut off before the merged history was written). It stays that
    # object's history, or jumps would move the cursor without moving its mesh.
    for m, u in list(reg.by_object.items()):
        h = reg.histories.get(u)
        if h is None:
            del reg.by_object[m]
        elif m not in h.members:
            h.members.append(m)
    # One operation is one global position: no history keeps a step of an operation that lost a step
    # elsewhere, nor what comes after it (its later steps start from that step's result). Steps of the
    # file's state (before the cursor) are never touched: an operation is either all in the file or not.
    while left_ops:
        more = set()
        for h in reg.histories.values():
            for i in range(h.cursor, len(h.entries)):
                if _group(h.entries[i]) in left_ops:
                    cut = h.entries[i:]
                    more.update(_group(e) for e in cut)
                    h.entries = h.entries[:i]
                    stopped[h.uid] = stopped.get(h.uid, 0) + len(cut)
                    _clamp_hist(h)
                    break
        if not more - left_ops:
            break
        left_ops |= more
    reg.replay_report ={"stopped": {u: n for u, n in stopped.items() if n}}
    if max_seq is not None and reg.next_seq <= max_seq:
        reg.next_seq = int(max_seq) + 1
    return applied
