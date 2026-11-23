"""Data adapters: loose data: texts, library materials and worlds (see ADAPTERS.md)."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import re

import bpy
import numpy as np
from ...core import codec
from ...core.types import MISSING
from .. import idmap, log
from . import core, materials
from .core import note_name
from .idprops import _ID_EMPTY4, _ID_EMPTY5, id_props, write_id_props
from .keys import (
    DNA_PREFIX, IDPROPS_KEY, INV_GROUPS, INV_LOOSE, LMAT_PREFIX, LOOSE_PREFIXES, LPROPS_PREFIX, LWORLD_PREFIX,
    META_PREFIX, NG_PREFIX, SNAP_PREFIX, SYSPROPS_KEY, TEXT_PREFIX, _MAT_CACHE)
from .materials import group_owner_map, material_owner_map, write_material


LOOSE_READ_MAX = 32        # unused materials and worlds read again per operation, in turn (see forget_loose_data)
_TEXT_PROPS = ("filepath", "use_module", "indentation")
# Datablocks whose custom properties and add-on data no object records: their own, file-level data.
# (Object data, particle settings and materials an object uses are recorded with the object; node
# groups with D133.)
_SNAP_COLLS = ("images", "actions", "palettes", "brushes", "linestyles", "masks", "movieclips", "sounds",
               "fonts", "cache_files", "paint_curves", "textures")
_PROP_COLLS = _SNAP_COLLS + ("texts",)
_LOOSE_TURN = [0]
SNAP_READ_MAX = 2          # D154: datablocks there were at the first read, snapshotted again per operation, in turn
_SNAPS: dict = {}          # uid -> [session_uid, record, digest of its blocks, made after the first read]
_DNA: dict = {}            # DNA id -> the record of that DNA block (DNA_PREFIX)
_DINFO: dict = {}          # DNA id -> _dna_info of that DNA block
_SNAP_TURN = [0]
_UNMATCHED: set = set()    # D156: uids snapshotted since the file was opened, not yet compared with their record
_SNAP_READ = [False]
# D156: written, but given again when the datablock is read (a file opened, a snapshot loaded)
_ID_RUNTIME = (b"recalc", b"recalc_after_undo_push", b"session_uid")
_NOT_OWN = (b"REND", b"TEST", b"GLOB", b"DNA1", b"ENDB", b"USER")    # a .blend's file blocks, not the datablock's


def _unused(idb) -> bool:
    """No user but its fake user (a library kept for later, data an add-on keeps)."""
    return idb.library is None and idb.users - int(bool(idb.use_fake_user)) <= 0


def _loose_mats():
    return [m for m in bpy.data.materials if _unused(m)] + [w for w in bpy.data.worlds if _unused(w)]


def _snappable(name, idb) -> bool:
    """A file-level datablock D154 snapshots: local; not Blender's render and viewer images; not an action
    something uses (its object records it)."""
    if idb.library is not None or getattr(idb, "type", "") in ("RENDER_RESULT", "COMPOSITING"):
        return False
    return name != "actions" or _unused(idb)


def _b64(raw: bytes) -> str:
    return base64.b64encode(codec.compress(raw, "zstd")).decode("ascii")


def _unb64(s: str) -> bytes:
    return codec.decompress(base64.b64decode(s))


def _heads(raw: bytes):
    """(block head size, offset of the SDNA number in a head) of a .blend written by 4.2 to 5.x."""
    return (32, 4) if raw[7:9] == b"17" else (24, 16)


def _dna_info(raw: bytes, blocks: list) -> tuple:
    """From the snapshot's DNA block: (the SDNA number of IDProperty, -1 when it isn't found; {SDNA number:
    (struct size, byte offsets of its pointers, byte offsets of its IDs' _ID_RUNTIME fields)}); (-1, {}) when it
    isn't read."""
    try:
        hs = _heads(raw)[0]
        pos, n = next((p, n) for p, c, n in blocks if c == b"DNA1")
        d = raw[pos + hs:pos + n]

        def strings(tag, i):
            i = d.index(tag, i) + 8
            out = []
            for _ in range(int.from_bytes(d[i - 4:i], "little")):
                j = d.index(b"\0", i)
                out.append(d[i:j])
                i = j + 1
            return out, i
        names, i = strings(b"NAME", 0)
        types, i = strings(b"TYPE", i)
        i = d.index(b"TLEN", i) + 4
        tlen = np.frombuffer(d, np.uint16, len(types), i).tolist()
        i = d.index(b"STRC", i + 2 * len(types)) + 8
        strc = {}
        for k in range(int.from_bytes(d[i - 4:i], "little")):
            m = int.from_bytes(d[i + 2:i + 4], "little")
            fields = np.frombuffer(d, np.uint16, 2 * m, i + 4).reshape(-1, 2).tolist()
            strc[int.from_bytes(d[i:i + 2], "little")] = (k, fields)
            i += 4 + 4 * m
    except (StopIteration, ValueError):
        return -1, {}
    offs: dict = {}

    def ptr_offsets(t):             # byte offsets of the pointers in struct type t, its embedded structs' too
        if t not in offs:
            out, run, at = [], [], 0
            for ft, fn in strc[t][1]:
                nm = names[fn]
                dims = 1
                for x in re.findall(rb"\[(\d+)\]", nm):
                    dims *= int(x)
                if nm[:1] == b"*" or nm[:2] == b"(*":
                    out += range(at, at + 8 * dims, 8)
                    at += 8 * dims
                    continue
                if types[t] == b"ID" and nm in _ID_RUNTIME:
                    run += range(at, at + tlen[ft] * dims)
                if ft in strc:
                    sub = ptr_offsets(ft)
                    out += [at + e * tlen[ft] + o for e in range(dims) for o in sub[0]]
                    run += [at + e * tlen[ft] + o for e in range(dims) for o in sub[1]]
                at += tlen[ft] * dims
            offs[t] = out, run
        return offs[t]
    ptrs = {k: (tlen[t], *(np.array(x, dtype=np.int64) for x in ptr_offsets(t)))
            for t, (k, _) in strc.items()}
    idp = types.index(b"IDProperty") if b"IDProperty" in types else -1
    return (strc[idp][0] if idp in strc else -1), ptrs


def _ptr_words(n: int, info: tuple, sdna: int):
    """The numbers of the 8-byte words that are pointers in n bytes of struct sdna (info: _dna_info)."""
    size, offs = info[1].get(sdna, (0, ()))[:2]
    if not len(offs) or not size:
        return np.zeros(0, dtype=np.int64)
    b = (offs[None, :] + size * np.arange(n // size)[:, None]).ravel()
    return b[(b % 8 == 0) & (b < n - n % 8)] // 8


def _digest(raw: bytes, blocks: list, info: tuple = (-1, {})) -> bytes:
    """The hash of a snapshot's own blocks (not the file's: GLOB holds the .blend's path), with every
    block address (memory addresses, which change when Blender allocates the data again) and every
    pointer to one replaced by the block's number, and every pointer to nothing written by 0 (Blender reads
    it as null; 5.0 writes an image's runtime pointer as an address from the blocks' counter). info
    (_dna_info): the custom property blocks and the data they point to are left out (an empty group where
    there was none is the same; the D151 writer puts them back; 4.2 leaves a text's byte after its end)."""
    if not blocks:
        return hashlib.blake2b(raw, digest_size=16).digest()
    hs, so = _heads(raw)
    idprop = info[0]
    addr = np.array([int.from_bytes(raw[p + 8:p + 16], "little") for p, _, _ in blocks], dtype=np.uint64)
    sdna = [int.from_bytes(raw[p + so:p + so + 4], "little", signed=True) for p, _, _ in blocks]
    held = set()
    for (p, _, n), s in zip(blocks, sdna):
        if s == idprop >= 0:
            held.update(np.frombuffer(raw, np.uint64, (n - hs) // 8, p + hs)[_ptr_words(n - hs, info, s)].tolist())
    left = [c in _NOT_OWN or s == idprop or (c == b"DATA" and int(a) in held)
            for (p, c, _), s, a in zip(blocks, sdna, addr)]
    num = np.cumsum([not x for x in left], dtype=np.uint64) * np.array([not x for x in left], dtype=np.uint64)
    order = np.argsort(addr)
    keys = addr[order]
    h = hashlib.blake2b(digest_size=16)
    for (pos, code, n), s, skip in zip(blocks, sdna, left):
        if skip:
            continue
        h.update(raw[pos:pos + 8] + raw[pos + 16:pos + hs])
        k = (n - hs) // 8
        words = np.frombuffer(raw, dtype=np.uint64, count=k, offset=pos + hs).copy()
        at = np.minimum(np.searchsorted(keys, words), len(keys) - 1)
        hit = (keys[at] == words) & (words != 0)
        b = _ptr_words(n - hs, info, s)
        words[b[~hit[b]]] = 0
        words[hit] = num[order[at[hit]]]
        size, _p, run = info[1].get(s, (0, (), ()))
        if len(run) and size:
            r = (run[None, :] + size * np.arange((n - hs) // size)[:, None]).ravel()
            words.view(np.uint8)[r[r < 8 * k]] = 0
        h.update(words.tobytes() + raw[pos + hs + 8 * k:pos + n])
    return h.digest()


def _dna_digest(raw: bytes, blocks: list) -> tuple:
    """(DNA block, its id, _digest) of a snapshot written with its DNA."""
    mv = memoryview(raw)
    dna = b"".join(mv[p:p + n] for p, c, n in blocks if c == b"DNA1")
    did = hashlib.blake2b(dna, digest_size=8).hexdigest() if blocks else ""
    if did and did not in _DINFO:
        _DINFO[did] = _dna_info(raw, blocks)
    return dna, did, _digest(raw, blocks, _DINFO.get(did, (-1, {})))


def _take(name, idb, prev, new) -> list:
    """D154: snapshot the datablock (multires.snapshot_bytes: it and what it uses, as a .blend). Its DNA
    block, most of the bytes and the same for every snapshot this Blender writes, is kept once in _DNA.
    An unchanged snapshot (its own blocks, not the file's, hash the same) keeps its record."""
    from .. import multires
    raw = multires.snapshot_bytes(idb)
    blocks = multires.blend_blocks(raw)
    mv = memoryview(raw)
    dna, did, digest = _dna_digest(raw, blocks)
    if prev is not None and prev[2] == digest:
        return [idb.session_uid, prev[1], digest, new]
    if blocks:                 # (did is "" when the bytes aren't read as blocks: the record is the whole file)
        if did not in _DNA:
            _DNA[did] = _b64(dna)
        raw = raw[:blocks[0][0]] + b"".join(mv[p:p + n] for p, c, n in blocks if c not in (b"DNA1", b"REND", b"TEST"))
    return [idb.session_uid, [name, did, _b64(raw), idb.name, bool(idb.use_fake_user)], digest, new]


def _file_snapshots(out: dict, inv: list, recorded=None) -> None:
    """D154: a record of every file-level datablock, to make it again when going back over its deletion
    (nothing lighter is exact). New datablocks are snapshotted at once and at every operation after;
    those there were at the first read SNAP_READ_MAX per operation, in turn.
    shortcut: a change of one of those is found within a few operations (as D151's materials), so deleting
    it right after a change makes the older state again; a cheap per-type check would find it at once."""
    seen, old = {}, []
    for name in _SNAP_COLLS:
        for idb in getattr(bpy.data, name, ()):
            if not _snappable(name, idb):
                continue
            u = idmap.claim(idb)
            if not u:
                continue
            c = _SNAPS.get(u)
            if c is None or c[0] != idb.session_uid or c[3]:
                try:
                    if c is None:
                        _UNMATCHED.add(u)
                    c = _take(name, idb, c, c[3] if c is not None else _SNAP_READ[0])
                except Exception as e:
                    log.error_once(("snap", name, str(e)), f"could not record {idb.name}: {e}", e)
                    continue
            else:
                old.append((u, name, idb))
            seen[u] = c
    if old:
        k = _SNAP_TURN[0] % len(old)
        for u, name, idb in (old[k:] + old[:k])[:SNAP_READ_MAX]:
            with contextlib.suppress(Exception):
                seen[u] = _take(name, idb, seen[u], False)
        _SNAP_TURN[0] = k + SNAP_READ_MAX
    for u in list(_UNMATCHED.intersection(seen)) if recorded is not None else ():
        rec = recorded(SNAP_PREFIX + u)
        if isinstance(rec, list):
            _UNMATCHED.discard(u)
            if len(rec) >= 5 and rec != seen[u][1] and _same_as_recorded(u, seen[u], rec, recorded):
                seen[u][1] = rec
    _SNAP_READ[0] = True
    _SNAPS.clear()
    _SNAPS.update(seen)
    for u, c in seen.items():
        out[SNAP_PREFIX + u] = c[1]
        if c[1][1]:
            out[DNA_PREFIX + c[1][1]] = _DNA[c[1][1]]
        inv.append(u)


def forget_snapshots() -> None:
    _SNAPS.clear()
    _UNMATCHED.clear()
    _SNAP_READ[0] = False


def _with_dna(rec, values, recorded):
    """The recorded snapshot as a .blend (its DNA block put back), or None when that DNA wasn't recorded."""
    from .. import multires
    raw = _unb64(rec[2])
    if rec[1]:
        dna = _DNA.get(rec[1]) or values.get(DNA_PREFIX + rec[1])
        if dna is None and recorded is not None:
            dna = recorded(DNA_PREFIX + rec[1])
        if not isinstance(dna, str):
            return None
        end = multires.blend_blocks(raw)[-1][0]
        raw = raw[:end] + _unb64(dna) + raw[end:]
    return raw


def _same_as_recorded(uid, c, rec, recorded) -> bool:
    """D156: after a file is opened, a datablock is unchanged when its digest is its recorded snapshot's, or
    that snapshot's once Blender reads it (4.2 gives a brush with no cavity curve one; another Blender's
    snapshot is read with its own DNA): loaded into a separate main and written again from there."""
    from .. import multires
    raw = _unb64(rec[2])
    if _digest(raw, multires.blend_blocks(raw), _DINFO.get(rec[1], (-1, {}))) == c[2]:
        return True
    raw = _with_dna(rec, {}, recorded)
    if raw is None or not hasattr(bpy.data, "temp_data"):
        return False
    src_path, out = multires._tmp_path(), multires._tmp_path()
    try:
        with open(src_path, "wb") as fh:
            fh.write(raw)
        with bpy.data.temp_data() as td:
            with td.libraries.load(src_path, link=False) as (src, dst):
                setattr(dst, rec[0], list(getattr(src, rec[0])))
            x = next((i for i in getattr(td, rec[0]) if i.get(idmap.UID_PROP) == uid), None)
            if x is None:
                return False
            td.libraries.write(out, {x}, fake_user=False)
        with open(out, "rb") as fh:
            w = fh.read()
    except Exception as e:      # then it reads as changed: a step, never a lost change
        log.error_once(("snap_reread", str(e)), f"could not compare {rec[3]} with its snapshot: {e}", e)
        return False
    finally:
        for f in (src_path, out):
            with contextlib.suppress(OSError):
                os.remove(f)
    return _dna_digest(w, multires.blend_blocks(w))[2] == c[2]


def _load_snapshot(uid, rec, values, recorded):
    """D154: load a recorded snapshot (libraries.load). What it brought along that is in the file already
    (by id) is replaced by that; what nothing uses is removed. Returns the loaded datablock or None."""
    from .. import multires
    name = rec[0]
    raw = _with_dna(rec, values, recorded)
    if raw is None:
        log.warn(f"{rec[3]} can't be made again: the snapshot's DNA wasn't recorded")
        return None
    colls = _SNAP_COLLS + ("materials", "node_groups", "texts", "worlds")
    before = {c: set(getattr(bpy.data, c)) for c in colls}
    path = multires._tmp_path()
    try:
        with open(path, "wb") as fh:
            fh.write(raw)
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            setattr(dst, name, list(getattr(src, name)))
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    got = None
    for c in colls:
        for x in [x for x in getattr(bpy.data, c) if x not in before[c]]:
            xu = x.get(idmap.UID_PROP)
            if xu == uid and c == name and got is None:
                got = x
                continue
            have = idmap.find(xu) if xu else None
            if have is not None and have != x:
                x.user_remap(have)
            if x.users == 0:
                getattr(bpy.data, c).remove(x)
    return got


def _snapshots_back(values: dict, recorded=None, replace: bool = False) -> None:
    """D154: a file-level datablock deleted since is made again from its snapshot, with its id (before the
    other writers). replace: after them, one that still differs from its snapshot is replaced by it (what
    uses it uses the loaded one); a change the other writers put back (custom properties) keeps it."""
    from .. import multires
    for path, rec in values.items():
        if not path.startswith(SNAP_PREFIX) or not isinstance(rec, list) or len(rec) < 5:
            continue
        uid = path[len(SNAP_PREFIX):]
        old = idmap.find(uid)
        c = _SNAPS.get(uid)
        if (old is not None) != replace or (replace and c is not None and c[1] == rec):
            continue
        _LOOSE_GONE.pop(uid, None)
        try:
            if old is not None:
                now = _take(rec[0], old, None, bool(c is not None and c[3]))
                raw = _unb64(rec[2])
                if now[2] == _digest(raw, multires.blend_blocks(raw), _DINFO.get(rec[1], (-1, {}))):
                    now[1] = rec
                    _SNAPS[uid] = now
                    continue
            new = _load_snapshot(uid, rec, values, recorded)
            if new is None:
                continue
            if old is not None:
                old.user_remap(new)
                idmap.unnote(old)
                getattr(bpy.data, rec[0]).remove(old)
            new[idmap.UID_PROP] = uid
            idmap.note(new, uid)
            if new.name != rec[3]:
                new.name = rec[3]
            note_name(new, rec[3])
            if new.use_fake_user != rec[4]:          # loading clears it
                new.use_fake_user = rec[4]
            # the record it was read with, so the next read finds no change; its digest as it is now
            _SNAPS[uid] = _take(rec[0], new, None, bool(c is not None and c[3]))
            _SNAPS[uid][1] = rec
        except Exception as e:
            log.error_once(("snap_load", uid, str(e)), f"could not make {rec[3]} again: {e}", e)


def scene_loose(sc) -> dict:
    """D151: datablocks no recorded object and no scene uses, recorded with the first recorded scene by
    name: texts, materials and worlds nothing uses, and every other datablock's custom properties and
    add-on data (where any are set). They aren't in the depsgraph: texts and properties are read
    again at each operation, unused materials and worlds in turn (forget_loose_data)."""
    from .. import datarec, runtime
    from ..persistence import TEXT_NAME
    scenes = sorted(x.name for x in bpy.data.scenes if x.library is None and datarec.is_tracked(x))
    if scenes and scenes[0] != sc.name:
        return {}
    out = {}
    suid = idmap.uid_of(sc)
    inv = []
    for t in bpy.data.texts:
        if t.library is not None or t.name == TEXT_NAME:
            continue
        tu = idmap.claim(t)
        if tu:
            out[TEXT_PREFIX + tu] = {"body": t.as_string(),
                                     "props": {k: core.normalise(getattr(t, k)) for k in _TEXT_PROPS}}
            out[META_PREFIX + tu] = ["tx", t.name, bool(t.use_fake_user), ""]
            inv.append(tu)
    for m in _loose_mats():
        mu = idmap.claim(m)
        if mu:
            world = isinstance(m, bpy.types.World)
            out[(LWORLD_PREFIX if world else LMAT_PREFIX) + mu] = materials.material_record(m, suid)
            out[META_PREFIX + mu] = ["lw" if world else "lm", m.name, bool(m.use_fake_user), ""]
            inv.append(mu)
            p = id_props(m)
            if p is not _ID_EMPTY4 and p is not _ID_EMPTY5:
                out[LPROPS_PREFIX + mu] = p
    for name in _PROP_COLLS:
        for idb in getattr(bpy.data, name, ()):
            if idb.library is not None or (name == "texts" and idb.name == TEXT_NAME):
                continue
            p = id_props(idb)
            if p is _ID_EMPTY4 or p is _ID_EMPTY5:
                continue
            u = idmap.claim(idb)
            if u:
                out[LPROPS_PREFIX + u] = p
    st = runtime._STATES.get(suid)
    _file_snapshots(out, inv, (lambda k: st.values.get(("scene", k))) if st is not None else None)
    out[INV_LOOSE] = sorted(inv)
    return out


def write_loose(path: str, v) -> None:
    """Put back one D151 record. A datablock made or deleted since is left as it is; properties that
    weren't set then (no record) are taken off."""
    for pre in LOOSE_PREFIXES:
        if path.startswith(pre):
            uid = path[len(pre):]
            break
    else:
        return
    idb = idmap.find(uid)
    if idb is None:
        return
    try:
        if pre == LPROPS_PREFIX:
            write_id_props(idb, v if isinstance(v, dict) else {IDPROPS_KEY: {}, SYSPROPS_KEY: {}})
        elif not isinstance(v, dict):
            return
        elif pre == TEXT_PREFIX:
            if idb.as_string() != v.get("body", ""):
                idb.from_string(v.get("body", ""))
            for k, x in (v.get("props") or {}).items():
                if k in _TEXT_PROPS and getattr(idb, k) != x:
                    setattr(idb, k, x)
        else:
            write_material(uid, v)
    except Exception as e:
        log.error_once(("loose", path, str(e)), f"could not put back {getattr(idb, 'name', uid)}: {e}", e)


_LOOSE_COLL = {"tx": "texts", "lm": "materials", "lw": "worlds", "ng": "node_groups"}
_LOOSE_REC = {"tx": TEXT_PREFIX, "lm": LMAT_PREFIX, "lw": LWORLD_PREFIX, "ng": NG_PREFIX}
_LOOSE_GONE: dict = {}      # uid -> kind: made after the position a jump went to; removed when the jump is done


def _loose_meta(uid, values, recorded):
    m = values.get(META_PREFIX + uid, MISSING)
    if m is MISSING and recorded is not None:
        m = recorded(META_PREFIX + uid)
    return m if isinstance(m, (list, tuple)) and len(m) >= 4 else None


def _make_loose_again(values: dict, recorded=None) -> None:
    """D152: a text, unused material or world, or node group that was deleted since (its record is in the
    values and nothing has its id) is made again with its name, fake user and id; the record is written
    into it by the writers that follow. A name another datablock holds gets Blender's .001."""
    for kind, pre in _LOOSE_REC.items():
        for path, v in values.items():
            if not path.startswith(pre) or not isinstance(v, dict):
                continue
            uid = path[len(pre):]
            if idmap.find(uid) is not None:
                _LOOSE_GONE.pop(uid, None)
                continue
            meta = _loose_meta(uid, values, recorded)
            if meta is None or meta[0] != kind:
                log.warn(f"a {kind} datablock can't be made again: its details weren't recorded")
                continue
            try:
                coll = getattr(bpy.data, _LOOSE_COLL[kind])
                idb = coll.new(meta[1], meta[3]) if kind == "ng" else coll.new(meta[1])
                idb[idmap.UID_PROP] = uid
                idmap.note(idb, uid)
                note_name(idb, meta[1])
                if meta[2]:
                    idb.use_fake_user = True
            except Exception as e:
                log.error_once(("loose_new", kind, str(e)), f"could not make {meta[1]} again: {e}", e)
    _snapshots_back(values, recorded)


def _loose_owner(sc) -> bool:
    from .. import datarec
    scenes = sorted(x.name for x in bpy.data.scenes if x.library is None and datarec.is_tracked(x))
    return not scenes or scenes[0] == sc.name


def _queue_loose_removals(sc, values: dict) -> None:
    """D152: a text, unused material or world, or node group that exists but isn't in the inventory the
    jump went to was made after that position: it is removed when the jump is done (flush_loose_removals),
    and only if nothing uses it then (objects a later step of the same jump brings back may)."""
    todo = []
    if isinstance(values.get(INV_LOOSE), list):
        todo.append((values[INV_LOOSE], ("tx", "lm", "lw") + _SNAP_COLLS))
    if isinstance(values.get(INV_GROUPS), list):
        todo.append((values[INV_GROUPS], ("ng",)))
    if not todo or not _loose_owner(sc):
        return
    from ..persistence import TEXT_NAME
    for want, kinds in todo:
        want = set(want)
        for kind in kinds:
            for idb in list(getattr(bpy.data, _LOOSE_COLL.get(kind, kind))):
                if idb.library is not None or (kind == "tx" and idb.name == TEXT_NAME):
                    continue
                u = idmap.uid_of(idb)
                if u and u not in want:
                    _LOOSE_GONE[u] = "lw" if isinstance(idb, bpy.types.World) else kind



def _queue_left_action(ob, recorded) -> None:
    """D155: an object a jump takes out leaves its action unused (a duplicate's copy of it): it goes after the
    jump too, unless the position the jump went to had it as data nothing uses."""
    a = getattr(ob.animation_data, "action", None)
    inv = recorded(INV_LOOSE) if a is not None else None
    if isinstance(inv, list) and (u := idmap.claim(a)) and u not in inv:
        _LOOSE_GONE[u] = "actions"

def _kept_by_record(idb, kind) -> bool:
    """Used by something Hindsight records (an object, a scene): not a datablock to say anything about,
    its own history has it."""
    with contextlib.suppress(Exception):
        if kind == "lm":
            return idb.as_pointer() in material_owner_map()
        if kind == "ng":
            return idb.as_pointer() in group_owner_map() or any(
                getattr(sc, "compositing_node_group", None) == idb or getattr(sc, "node_tree", None) == idb
                for sc in bpy.data.scenes)
        if kind == "lw":
            return any(sc.world == idb for sc in bpy.data.scenes)
        if kind == "actions":
            return True               # an object uses it: the object records it
    return False


def flush_loose_removals() -> None:
    """D152: end of a jump: remove the datablocks it went back (or forward) over the making of, unless
    something uses them now (never a datablock an object or scene uses: it is said in a note)."""
    if not _LOOSE_GONE:
        return
    todo = dict(_LOOSE_GONE)
    _LOOSE_GONE.clear()
    for uid, kind in todo.items():
        idb = idmap.find(uid)
        if idb is None:
            continue
        try:
            if not _unused(idb):
                if not _kept_by_record(idb, kind):
                    log.warn(f"{idb.name} was made after this point but can't be taken away: something uses it now")
                continue
            idmap.unnote(idb)
            getattr(bpy.data, _LOOSE_COLL.get(kind, kind)).remove(idb)
        except Exception as e:
            log.error_once(("loose_rm", uid, str(e)), f"could not take away {getattr(idb, 'name', uid)}: {e}", e)


def forget_loose_data() -> None:
    """D151: an operation ended: unused materials and worlds are read again, at most LOOSE_READ_MAX of
    them each time, in turn (a file with hundreds of them, an asset library, would otherwise read every
    node tree at every operation); a change is found within a few operations."""
    with contextlib.suppress(Exception):
        if not len(bpy.data.materials) and not len(bpy.data.worlds):
            return
        loose = _loose_mats()
        n = len(loose)
        if n <= LOOSE_READ_MAX:
            pick = loose
        else:
            k = _LOOSE_TURN[0] % n
            pick = (loose[k:] + loose[:k])[:LOOSE_READ_MAX]
            _LOOSE_TURN[0] = k + LOOSE_READ_MAX
        for m in pick:
            _MAT_CACHE.pop(m.session_uid, None)
