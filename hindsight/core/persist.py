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

import binascii
import json
import os
import struct
import time
import weakref
from itertools import accumulate
from .crc import crc32

import numpy as np

from .types import Entry, Slice

REG_MAGIC = b"HSR1"
FORMAT_VERSION = 4
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


# meta keys whose (few distinct) string values are shared between the steps of a loaded file; `extra`
# is never shared (callers may change it)
_SHARED_META = ("label", "mode", "target", "kind")


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


# R066: the raw steps a save has to encode (the newest of each history are kept unpacked) are encoded on
# a few threads when there is much to encode: the engine packs and compresses without the GIL, so four
# threads take about a third of the time of one (64 strokes of a 1M mesh: about 50 ms on one).
_ENCODE_THREADS = min(4, os.cpu_count() or 1)
_PAR_MIN_BYTES = 4 << 20        # raw bytes to encode before threads pay
ENCODED_ON_THREADS = [0]        # tests: raw steps the last save encoded on threads


def _encode_raw(raw, codec: str) -> dict:
    """{id(entry): (bytes, crc)} of the raw steps `raw`, encoded on threads; {} when one thread is the better
    choice (then the save encodes them in place, as it always did). The bytes are the same."""
    ENCODED_ON_THREADS[0] = 0
    if _ENCODE_THREADS < 2:
        return {}
    if len(raw) < 2 or sum(e.nbytes() for e in raw) < _PAR_MIN_BYTES:
        return {}
    from concurrent.futures import ThreadPoolExecutor

    def enc(e):
        b = e.to_bytes(codec, 0)
        return b, crc32(b) & 0xFFFFFFFF
    with ThreadPoolExecutor(max_workers=min(_ENCODE_THREADS, len(raw))) as pool:
        done = list(pool.map(enc, raw))
    ENCODED_ON_THREADS[0] = len(raw)
    return {id(e): r for e, r in zip(raw, done)}


def registry_parts(reg, codec: str = "auto") -> list:
    """The registry blob as a list of buffers (the step blobs themselves, not copies): written to a
    file one after another, the history is never held twice while saving."""
    hists = list(reg.histories.values())
    pre = _encode_raw([e for h in hists for e in h.entries if e.step is not None], codec)
    blobs = []
    hjson = []
    for h in hists:
        b, crcs, metas = _hist_steps(h, pre, codec)
        blobs += b
        hjson.append(dict(_hist_doc(h), metas=metas, crcs=crcs))
    js = _registry_doc(reg, FORMAT_VERSION, hjson)
    parts = [REG_MAGIC, _U32.pack(len(js)), js]
    for b in blobs:
        parts.append(_U64.pack(len(b)))
        parts.append(b)
    parts += [TRAILER, _U32.pack(crc32(js) & 0xFFFFFFFF)]
    return parts


def _hist_steps(h, pre, codec: str):
    """A history's steps as a save writes them: (bytes, CRC-32s, metas). Packed steps as they are, raw ones in
    the quick encoding (from `pre` when they were encoded on threads)."""
    blobs, crcs, metas = [], [], []
    for e in h.entries:
        b, crc = e.blob_crc()               # R046: packed steps carry their CRC-32
        if b is None:
            hit = pre.get(id(e))
            if hit is not None:
                b, crc = hit
            else:
                b = e.to_bytes(codec, 0)    # raw steps: the quick form (main thread)
                crc = crc32(b) & 0xFFFFFFFF
        blobs.append(b)
        crcs.append(crc)
        metas.append(_meta_for_json(e.meta))
    return blobs, crcs, metas


def _hist_doc(h) -> dict:
    mt = h.morph_target
    return {"uid": h.uid, "members": list(h.members), "cursor": int(h.cursor), "enabled": bool(h.enabled),
            "morph_target": None if mt is None else int(mt), "n_entries": len(h.entries)}


def _registry_doc(reg, fmt: int, hjson: list) -> bytes:
    doc = {
        "version": fmt,
        "format": fmt,
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
    return json.dumps(doc, separators=(",", ":"), default=_json_default).encode("utf-8")


# ---------------------------------------------------------------------------
# format 5: the index part and the step parts (dev/FORMAT5_LAYOUT.md; their bytes are the engine's, format5.rs)
# ---------------------------------------------------------------------------
FORMAT5 = 5
PART_LIMIT = 4 << 20            # a step part is full at this many bytes of steps (a starting value, #41)
# The stub on V1's Text: a V1 history header with no steps, so older builds say "saved by a newer Hindsight"
# and keep the Text as it is. V2 writes it and never reads it: it knows format 5 by its parts.
_STUB_JSON = b'{"format":5,"version":5,"writer":"Hindsight 2.0.0"}'
STUB = binascii.b2a_base64(REG_MAGIC + _U32.pack(len(_STUB_JSON)) + _STUB_JSON, newline=False).decode("ascii")


SAVE_WORK = {"encoded": 0, "built": 0}     # tests (#65): raw steps the last format 5 save encoded, step parts built
_NOWHERE = (None, -1)


class _Part:
    """A step part as Hindsight last built or read it (#65). A save hands the same bytes over again while its steps
    are the same Entry objects with the same bytes, and copies a raw step's bytes out of it, by offset. #125: its
    packed steps are read out of it by offset (types.Slice), so the history is held once; `bytes` is None while
    Blender holds it during a save (hand_over, take_back)."""
    __slots__ = ("bytes", "first", "listed", "keys", "offs", "lens", "crcs", "size")

    def __init__(self, part, first, listed, offs, lens, crcs):
        self.bytes, self.first, self.listed = part, int(first), listed
        self.offs, self.lens, self.crcs = list(offs), list(lens), list(crcs)
        self.size = sum(self.lens)
        self.keys = []

    def hold(self, entries) -> None:
        """Its steps, in order: weakly (a step that goes, cut or dropped, isn't kept alive by the last save's step
        parts), each with the bytes it holds (the packer swaps them whole, which a save then sees)."""
        self.keys = [(weakref.ref(e), id(e._bc)) for e in entries]

    def own(self) -> None:
        """#125: each packed step whose bytes this step part was built from is read out of it from now on, so it
        lets go of its own bytes (a raw step stays raw for the packer)."""
        for k, (ref, bc) in enumerate(self.keys):
            e = ref()
            if e is not None and e.step is None and id(e._bc) == bc:
                e._bc = (Slice(self, self.offs[k], self.lens[k]), self.crcs[k])
                self.keys[k] = (ref, id(e._bc))


def _located(cached) -> dict:
    """{id(entry): (step part, place in it)} of the steps in `cached` that are still the same bytes (the packer
    swaps a step's bytes whole, so a step it packed since is not found)."""
    loc = {}
    for p in cached:
        for k, (ref, bc) in enumerate(p.keys):
            e = ref()
            if e is not None and id(e._bc) == bc:
                loc[id(e)] = (p, k)
    return loc


def _history_parts(h, cached, loc, step_bytes, built: list) -> list:
    """The history's step parts: each step part in `cached` whose steps are all still there, in order, under the
    same numbers, handed over as it is (the open one only while no new step fits in it); the rest built, each up
    to PART_LIMIT bytes of steps and never across the start of a step part that may be reused.
    A step part numbers its first step from the first step the history ever recorded (#65): dropping the
    oldest steps rebuilds only the step part they were in. The step parts built are added to `built` too."""
    from .native import ENGINE
    ents = h.entries
    # numbered from the first step still in a step part: the steps before it may be from a damaged one (#69)
    at = next(((i, hit) for i, e in enumerate(ents) if (hit := loc.get(id(e))) is not None), None)
    if at is not None:
        i, (p, k) = at
        base = max(0, p.first + k - i)
    else:
        base = cached[0].first if cached else 0
    out, i = [], 0
    while i < len(ents):
        p, k = loc.get(id(ents[i]), _NOWHERE)
        n = len(p.keys) if p is not None else 0
        if (k == 0 and p.first == base + i and i + n <= len(ents)
                and all(loc.get(id(ents[i + j])) == (p, j) for j in range(n))
                and (i + n == len(ents) or loc.get(id(ents[i + n]), _NOWHERE)[1] == 0     # a build stops there too
                     or p.size + len(step_bytes(ents[i + n])[0]) > PART_LIMIT)):
            out.append(p)
            i += n
            continue
        j, size, blobs, crcs = i, 0, [], []
        while j < len(ents):
            b, c = step_bytes(ents[j])
            if j > i and (size + len(b) > PART_LIMIT or loc.get(id(ents[j]), _NOWHERE)[1] == 0):
                break
            blobs.append(b)
            crcs.append(c)
            size += len(b)
            j += 1
        ms = json.dumps([_meta_for_json(e.meta) for e in ents[i:j]], separators=(",", ":"),
                        default=_json_default).encode("utf-8")
        part, begin, crc = ENGINE.part_build(base + i, blobs, crcs, ms)
        SAVE_WORK["built"] += 1
        lens = [len(b) for b in blobs]
        del blobs
        p = _Part(part, base + i, {"n": j - i, "len": len(part), "crc": crc, "begin": begin},
                  accumulate(lens[:-1], initial=begin), lens, crcs)
        p.hold(ents[i:j])
        out.append(p)
        built.append(p)
        i = j
    return out


def registry_to_format5(reg, codec: str = "auto"):
    """(index part, {history uid: [step parts]}): every step of the registry, in step parts of up to PART_LIMIT
    bytes of steps (a bigger step gets one of its own). The steps are copied, never decoded. #65: only the steps
    added since the step parts in `reg.parts` (the last save's, or the ones read at open) are encoded, and only
    the step parts they changed are built; `reg.parts` becomes this save's."""
    hists = list(reg.histories.values())
    old = getattr(reg, "parts", None) or {}
    locs = {h.uid: _located(old.get(h.uid, ())) for h in hists}
    raw = [e for h in hists for e in h.entries if e.step is not None and id(e) not in locs[h.uid]]
    SAVE_WORK.update(encoded=len(raw), built=0)
    done = _encode_raw(raw, codec)

    def step_bytes(e, loc):
        b, c = e._bc                    # R046: packed steps carry their CRC-32
        if b is not None:
            return (b.view() if type(b) is Slice else b), c
        hit = loc.get(id(e))
        if hit is not None:             # saved before: copied out of its step part, not encoded again
            p, k = hit
            return memoryview(p.bytes)[p.offs[k]:p.offs[k] + p.lens[k]], p.crcs[k]
        hit = done.get(id(e))
        if hit is None:
            b = e.to_bytes(codec, 0)    # raw steps: the quick form (main thread)
            hit = done[id(e)] = (b, crc32(b) & 0xFFFFFFFF)
        return hit

    hjson, out, new, built = [], {}, {}, []
    for h in hists:
        loc = locs[h.uid]
        ps = new[h.uid] = _history_parts(h, old.get(h.uid, ()), loc, lambda e: step_bytes(e, loc), built)
        out[h.uid] = [p.bytes for p in ps]
        hjson.append(dict(_hist_doc(h), parts=[p.listed for p in ps]))
    from .native import ENGINE
    index = ENGINE.index_build(_registry_doc(reg, FORMAT5, hjson))
    del locs, done
    for p in built:                     # only once every step part is built: a save that fails changes no step
        p.own()
    reg.parts = new
    return index, out


def hand_over(reg, parts, put) -> None:
    """#125: while a save runs, Blender's copy of the step parts is the only one. put(history uid, number, bytes)
    gives Blender each step part in `parts` (registry_to_format5's), and Hindsight lets go of it straight away,
    there and in reg.parts (the peak is one step part above the history). The packer is drained first: nothing
    may read a saved step until take_back."""
    for uid, ps in parts.items():
        held = reg.parts.get(uid, ())
        for i in range(len(ps)):
            put(uid, i, ps[i])
            ps[i] = None
            if i < len(held):
                held[i].bytes = None


def take_back(reg, take) -> int:
    """#125: after the save (or a failed one), each step part handed over comes back: take(history uid, number)
    gives Blender's copy and lets go of it, or None when it is gone. A step part that is gone or isn't the bytes
    handed over (length and CRC-32 as listed) takes its packed steps with it, and the history is cut as damage
    cuts it. Returns how many steps were lost."""
    from .step import is_unknown as _is_unknown
    lost = 0
    for uid, ps in reg.parts.items():
        gone = set()
        for i, p in enumerate(ps):
            if p.bytes is None:
                b = take(uid, i)
                if b is not None and len(b) == p.listed["len"] and crc32(b) & 0xFFFFFFFF == p.listed["crc"]:
                    p.bytes = b
                else:
                    gone.add(id(p))
        h = reg.histories.get(uid)
        if not gone or h is None:
            continue
        slots = [None if type(e._bc[0]) is Slice and id(e._bc[0].part) in gone else e for e in h.entries]
        _put_history(reg, h, _hist_doc(h), slots, {"damaged": []})
        lost += len(slots) - len(h.entries)
        h.unknown = sum(1 for e in h.entries if e.step is None and _is_unknown(e._bc[0]))
    for uid, ps in reg.parts.items():
        reg.parts[uid] = [p for p in ps if p.bytes is not None]
    return lost


NEWER_STEP_LABEL = "Saved by a newer Hindsight"     # an unknown step whose meta is lost


def index_doc(index, index_copy):
    """The index part's JSON as a dict (`index_copy`'s when `index` is damaged), None when neither can be read."""
    from .native import ENGINE
    for b in (index, index_copy):
        js = ENGINE.index_read(b) if b is not None else None
        if js is not None:
            return json.loads(bytes(js).decode("utf-8"))
    return None


def registry_from_format5(index, index_copy, take):
    """The registry from its index part (`index_copy` when `index` is damaged) and its step parts, asked for one
    at a time: take(history uid, number) gives a part's bytes, or None when it is missing (the add-on deletes
    Blender's copy as it hands it over). ValueError when neither index part can be read, NewerFormatError for
    a format from the future. A step part that doesn't match the index part is checked finer (#69,
    dev/FORMAT5_DAMAGE.md): the steps that are damaged, missing or can't be found are never applied, cut as
    registry_from_bytes cuts damaged steps; damage that lost nothing is listed in the report's "mended"."""
    from .history import History
    from .native import ENGINE
    from .registry import Registry
    from .step import is_unknown as _is_unknown
    doc = index_doc(index, index_copy)
    if doc is None:
        raise ValueError("damaged Hindsight history index (both copies)")
    found = int(doc.get("format", 0) or 0)
    if found > FORMAT5:
        raise NewerFormatError(found)
    reg = Registry()
    report = _settings_from_doc(reg, doc, found)
    report["unknown"] = []      # #66: only format 5 holds steps in a later encoding; formats 1 to 4 report as before
    report["mended"] = []       # #69: damage that lost nothing (an index copy, a step part's metas or step table)
    strings, max_seq = {}, [0]
    if index is None or ENGINE.index_read(index) is None:
        report["mended"].append({"index": 0})       # read from index_copy; the next save writes both
    for hd in doc.get("histories", []):
        slots = []
        held = reg.parts[hd["uid"]] = []
        for i, pd in enumerate(hd.get("parts", [])):
            n, p, got, rows, part = int(pd["n"]), take(hd["uid"], i), None, None, None
            if not 0 <= n <= int(pd["len"]) // 8:
                # each step takes 8 bytes or more; the engine and the slots below trust n (#127 review 5)
                raise ValueError(f"the index part lists {n} steps for a step part of {pd['len']} bytes")
            if p is not None:
                try:
                    whole = len(p) == int(pd["len"]) and crc32(p) & 0xFFFFFFFF == int(pd["crc"])
                    if whole:
                        _first, offs, lens, crcs, metas = ENGINE.part_read(p)
                        rows = list(zip(offs, lens, crcs)) if len(offs) == n else None
                        part = _Part(p, _first, dict(pd), offs, lens, crcs)     # #125: steps read out of it
                    else:           # #69: checked finer; its steps found from the step table or by prefix
                        metas, rows = ENGINE.part_salvage(p, n, int(pd.get("begin", 0)))
                    metas = json.loads(bytes(metas).decode("utf-8")) if metas is not None else []
                    if rows is not None:
                        got = []

                        def lost():     # #11 review 3: an unknown step whose meta is lost (dev/FORMAT5_DAMAGE.md)
                            prev = next((e for e in reversed(slots + got) if e is not None), None)
                            return {"seq": prev.seq if prev is not None else 0, "label": NEWER_STEP_LABEL,
                                    "target": (hd.get("members") or [""])[0]}
                        for k, r in enumerate(rows):
                            got.append(None if r is None else _entry(
                                Slice(part, r[0], r[1]) if whole else p[r[0]:r[0] + r[1]],
                                metas[k] if k < len(metas) else None, r[2], strings, max_seq, lost))
                        if None not in got and whole:       # #65: the next save hands these bytes over again
                            part.hold(got)
                            held.append(part)
                        elif None not in got:   # every step found: nothing lost; the next save rebuilds it
                            report["mended"].append({"uid": hd["uid"], "part": i})
                except ValueError:          # damage (the engine's Corrupt* are ValueErrors); anything else fails
                    got = None              # the load, which keeps every part (#11 review 1)
            del p, part
            slots += got if got is not None else [None] * n
        h = History(hd["uid"], list(hd.get("members", [])))
        _put_history(reg, h, hd, slots, report)
        h.unknown = sum(1 for e in h.entries if _is_unknown(e._bc[0]))   # #66: kept as bytes, never decoded
        if h.unknown:
            report["unknown"].append({"uid": h.uid, "members": list(h.members), "n": h.unknown, "held": h.held()})
    if reg.next_seq <= max_seq[0]:
        reg.next_seq = max_seq[0] + 1
    reg.load_report = report
    return reg


def parts_to_bytes(parts) -> bytes:
    """The buffers in `parts` joined: the history as the .blend stores it (R061; earlier builds stored its
    base64 as text). One allocation of the final size."""
    return b"".join(parts)


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
    from .registry import Registry

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
        if crc32(mv[8:pos]) & 0xFFFFFFFF != want:
            raise ValueError("damaged Hindsight registry header (checksum)")

    reg = Registry()
    report = _settings_from_doc(reg, doc, found)
    max_seq = [0]
    strings = {}                     # per load: label, mode, target and kind values shared between steps
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
            crc = crc32(blob) & 0xFFFFFFFF
            if crcs is not None and i < len(crcs) and crc != int(crcs[i]):
                slots.append(None)
                continue
            slots.append(_entry(blob, metas[i] if i < len(metas) else None, crc, strings, max_seq))
        _put_history(reg, h, hd, slots, report)
    if reg.next_seq <= max_seq[0]:
        reg.next_seq = max_seq[0] + 1
    reg.load_report = report
    return reg


def _settings_from_doc(reg, doc, found) -> dict:
    """The registry's settings from a saved header (format 1 to 5); the load report to fill in."""
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
    return report


def _entry(blob, meta, crc, strings: dict, max_seq: list, lost=None):
    """A saved step as an Entry, or None when it has no meta and its own bytes can't give one; a step in an
    unknown encoding gets the meta `lost()` gives instead, when there is a `lost`."""
    if meta is None:
        from .step import UnknownEncoding, step_from_bytes
        try:
            meta = step_from_bytes(blob[:]).meta()      # a Slice gives a copy
        except UnknownEncoding:
            if lost is None:
                return None
            meta = lost()
        except MemoryError:
            raise                       # not damage: the load fails and keeps every part (#11 review 1)
        except Exception:
            return None
    if type(meta) is dict:
        for k in _SHARED_META:      # equal strings become one object (json makes one per step)
            v = meta.get(k)
            if type(v) is str:
                meta[k] = strings.setdefault(v, v)
    try:
        max_seq[0] = max(max_seq[0], int(meta.get("seq", 0)))
    except (TypeError, ValueError, AttributeError):
        pass
    return Entry(blob=blob, meta=meta, crc=crc)


def _put_history(reg, h, hd, slots: list, report: dict) -> None:
    """History `h` from its saved header and its steps (None where one is damaged or missing), into `reg`."""
    want = len(slots)
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


# ---------------------------------------------------------------------------
# sidecar folder
# ---------------------------------------------------------------------------
def sidecar_dir(blend_path: str) -> str:
    d = os.path.dirname(os.path.abspath(blend_path))
    stem = os.path.splitext(os.path.basename(blend_path))[0]
    return os.path.join(d, stem + "_hindsight")


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
    Files and journals of builds without save ids have none on either side and match as before.
    A marker that the parts of this save hold every step journaled before it (#124) counts as its SAVE."""
    rid = getattr(reg, "save_id", None) or None
    at = _save_point(records, rid)
    if at < 0:
        return not records or rid is None
    rtype, header, _p = records[at]
    return ((header or {}).get("parts" if rtype == "META" else "save_id") or None) == rid


def _save_point(records: list, rid) -> int:
    """Where the journal starts again on top of save `rid`: its last SAVE, or a later marker that the parts of save
    `rid` hold every step journaled before it (put on the Text when Hindsight was turned off, #124; #11 review 7).
    -1 when there is neither."""
    at = -1
    for i, (rtype, header, _p) in enumerate(records):
        if rtype == "SAVE" or (rid and rtype == "META" and not header.get("hist") and header.get("parts") == rid):
            at = i
    return at


def _journal_entry(payload, strings: dict, decode) -> Entry:
    """R064: a journaled step as an Entry holding its bytes, with the meta read from the step's header (no
    decompression, as for a step loaded from a history file; the journal's checksum covers the bytes). A
    step whose header can't be read that way (an older step format) is decoded as before."""
    from .step import read_header
    blob = payload if type(payload) is bytes else bytes(payload)
    try:
        meta = read_header(blob).get("meta")
    except Exception:
        meta = None
    if type(meta) is not dict or "seq" not in meta or "target" not in meta:
        return Entry(step=decode(blob))
    for k in _SHARED_META:
        v = meta.get(k)
        if type(v) is str:
            meta[k] = strings.setdefault(v, v)
    return Entry(blob=blob, meta=meta)


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

    strings = {}                     # label, mode, target and kind values shared between the steps
    start = _save_point(records, getattr(reg, "save_id", None)) + 1
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
            entry = _journal_entry(payload, strings, step_from_bytes)
            h = get_hist(uid, entry.target)
            max_seq = entry.seq if max_seq is None else max(max_seq, entry.seq)
            st = resets.get(uid)
            if st is not None:
                if entry.seq in home and uid not in stopped:
                    st[1] += 1                       # a step the history had before: put back
                else:
                    settle(h)
            k = skew.get(uid, 0)
            at = int(header.get("at", len(h.entries) - k)) + k
            if uid not in stopped and at < h.cursor:
                stopped[uid] = 0                     # replaces steps that lead to the saved state
            if uid in stopped:
                stopped[uid] += 1
                left_ops.add(_group(entry))
                continue
            if 0 <= at < len(h.entries):
                h.entries = h.entries[:at]
            h.entries.append(entry)
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
