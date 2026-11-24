"""Data adapters: loose data: texts, library materials and worlds (see ADAPTERS.md)."""
from __future__ import annotations

import base64
import contextlib
import os

import bpy
import numpy as np
from ...core import codec
from ...core.types import MISSING
from .. import idmap, log
from . import core, materials
from .core import note_name
from .idprops import _ID_EMPTY4, _ID_EMPTY5, id_props, write_id_props
from .keys import (
    COLL_NAMES, DNA_PREFIX, IDPROPS_KEY, INV_GROUPS, INV_LOOSE, LMAT_PREFIX, LOOSE_PREFIXES, LPROPS_PREFIX,
    LWORLD_PREFIX, META_PREFIX, NG_PREFIX, SNAP_PREFIX, SYSPROPS_KEY, TEXT_PREFIX, _MAT_CACHE)
from .materials import group_owner_map, material_owner_map, write_material
from .snapdigest import _DINFO, _digest, _dna_digest, _dna_info, _heads  # noqa: F401 (tests)


LOOSE_READ_MAX = 32        # unused materials and worlds read again per operation, in turn (see forget_loose_data)
_TEXT_PROPS = ("filepath", "use_module", "indentation")
# Datablocks whose custom properties and add-on data no object records: their own, file-level data.
# (Object data, particle settings and materials an object uses are recorded with the object; node
# groups with D133.)
# object data: recorded with its object; snapshotted when it has none (kept with a fake user)
_DATA_COLLS = ("meshes", "curves", "metaballs", "lattices", "armatures", "cameras", "lights", "lightprobes",
               "speakers", "volumes", "pointclouds", "hair_curves")
_SNAP_COLLS = ("images", "actions", "palettes", "brushes", "linestyles", "masks", "movieclips", "sounds",
               "fonts", "cache_files", "paint_curves", "textures", "particles", "grease_pencils",
               "collections") + _DATA_COLLS
# D174/D175: of these only _ids' are recorded here; with no user at all they are left alone (dropped at save)
_OFF_SCENE = _DATA_COLLS + ("collections",)
# recorded with what uses them; snapshotted when nothing does (grease_pencils: 4.2 annotations, 5.0 on objects' data)
_WHEN_UNUSED = ("actions", "particles", "grease_pencils") + _DATA_COLLS
_PROP_COLLS = _SNAP_COLLS + ("texts",)
_LOOSE_TURN = [0]
SNAP_READ_MAX = 2          # D154: datablocks there were at the first read, snapshotted again per operation, in turn
_SNAPS: dict = {}          # uid -> [session_uid, record, digest of its blocks, made after the first read]
_DNA: dict = {}            # DNA id -> the record of that DNA block (DNA_PREFIX)
_SNAP_TURN = [0]
_UNMATCHED: set = set()    # D156: uids snapshotted since the file was opened, not yet compared with their record
_SNAP_READ = [False]
_LEFT: dict = {}           # uid -> snapshot record of an action a jump took away (D162, D155)


def _unused(idb) -> bool:
    """No user but its fake user (a library kept for later, data an add-on keeps)."""
    return idb.library is None and idb.users - int(bool(idb.use_fake_user)) <= 0


def _remove(coll: str, idb) -> None:
    """Remove a datablock from the file (cache files and paint curves have no remove(): batch_remove)."""
    c = getattr(bpy.data, coll)
    c.remove(idb) if hasattr(c, "remove") else bpy.data.batch_remove([idb])


def _ids(name: str):
    """bpy.data.<name>; of a kind recorded with what uses it, only those nothing uses (object data: but a fake
    user), found without a Python loop over the rest (foreach_get: a file has a mesh per object)."""
    coll = getattr(bpy.data, name, ())
    if name == "collections":       # D175: those no scene holds, kept by a fake user or a collection instance
        held = {c.as_pointer() for sc in bpy.data.scenes for c in sc.collection.children_recursive}
        return [c for c in coll if c.users and c.as_pointer() not in held]
    if name not in _WHEN_UNUSED or not len(coll):
        return coll
    users, fake = np.empty(len(coll), np.int32), np.empty(len(coll), bool)
    coll.foreach_get("users", users)
    coll.foreach_get("use_fake_user", fake)
    unused = users <= fake
    # D174: object data with no user at all is what deleting its object leaves (Blender drops it at save)
    return [coll[int(i)] for i in np.flatnonzero(unused & fake if name in _DATA_COLLS else unused)]


def _loose_mats():
    return [m for m in bpy.data.materials if _unused(m)] + [w for w in bpy.data.worlds if _unused(w)]


def _snappable(name, idb) -> bool:
    """A file-level datablock D154 snapshots: local; not Blender's render and viewer images; not an action
    particle settings or Grease Pencil data something uses (what uses it records it)."""
    if idb.library is not None or getattr(idb, "type", "") in ("RENDER_RESULT", "COMPOSITING"):
        return False
    return name not in _WHEN_UNUSED or _unused(idb)


def _b64(raw: bytes) -> str:
    return base64.b64encode(codec.compress(raw, "zstd")).decode("ascii")


def _unb64(s: str) -> bytes:
    return codec.decompress(base64.b64decode(s))


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
    shortcut: a change of one of those is found within len/SNAP_READ_MAX operations (as D151's materials;
    on 4.2 dozens, for its local default brushes), so deleting it right after a change makes the older
    state again; a cheap per-type check would find it at once."""
    seen, old, failed = {}, [], []
    # every id first: a snapshot holds the ids of what its datablock uses (a brush's paint curve made with it)
    todo = [(name, idb, u) for name in _SNAP_COLLS for idb in _ids(name)
            if _snappable(name, idb) and (u := idmap.claim(idb))]
    for name, idb, _u in todo:
        if name == "collections":   # D175: made again, its objects and child collections map back to the file's
            for x in (*idb.all_objects, *idb.children_recursive):
                idmap.claim(x)
    for name, idb, u in todo:
        c = _SNAPS.get(u)
        if c is None or c[0] != idb.session_uid or c[3]:
            try:
                if c is None:
                    _UNMATCHED.add(u)
                c = _take(name, idb, c, c[3] if c is not None else _SNAP_READ[0])
            except Exception as e:
                log.error_once(("snap", name, str(e)), f"could not record {idb.name}: {e}", e)
                if c is None:
                    failed.append(u)        # in the inventory all the same: no jump takes it away
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
        did = c[1][1]
        if did and did not in _DNA and recorded is not None and isinstance(dna := recorded(DNA_PREFIX + did), str):
            _DNA[did] = dna             # a record kept as another Blender wrote it (D156)
        if did in _DNA:
            out[DNA_PREFIX + did] = _DNA[did]
        inv.append(u)
    # the kinds this inventory holds: a jump to one recorded before a kind was snapshotted removes none of it
    inv += failed + ["@" + k for k in _SNAP_COLLS]


def forget_snapshots() -> None:
    _SNAPS.clear()
    _LEFT.clear()
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
        _DNA.setdefault(rec[1], dna)
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
    colls = _SNAP_COLLS + ("materials", "node_groups", "texts", "worlds", "objects", "collections", "meshes",
                           "curves", "metaballs", "lattices", "armatures", "cameras", "lights", "lightprobes",
                           "speakers", "volumes", "pointclouds", "hair_curves", "grease_pencils")
    colls = tuple(dict.fromkeys(c for c in colls if hasattr(bpy.data, c)))
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
    new = [(c, x) for c in colls for x in getattr(bpy.data, c) if x not in before[c]]
    for c, x in new:
        xu = x.get(idmap.UID_PROP)
        if xu == uid and c == name and got is None:
            got = x
            continue
        have = idmap.find(xu) if xu else None
        if have is not None and have != x:
            x.user_remap(have)
    while True:         # an object copy goes first, then its data
        left = {i for i, (c, x) in enumerate(new) if x != got and x.users == 0}
        if not left:
            break
        for i in left:
            _remove(*new[i])
        new = [p for i, p in enumerate(new) if i not in left]
    return got


def _keep_paint(old, new) -> None:
    """A snapshot holds no pixels of an image painted and not saved: its copy gets them."""
    if isinstance(old, bpy.types.Image) and old.is_dirty and len(old.pixels) == len(new.pixels) > 0:
        px = np.empty(len(old.pixels), np.float32)
        old.pixels.foreach_get(px)
        new.pixels.foreach_set(px)


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
                _keep_paint(old, new)
                old.user_remap(new)
                idmap.unnote(old)
                _remove(rec[0], old)
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
        for idb in _ids(name) if name in _OFF_SCENE else getattr(bpy.data, name, ()):
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
        inv = values[INV_LOOSE]
        todo.append((inv, ("tx", "lm", "lw") + tuple(k for k in _SNAP_COLLS if "@" + k in inv)))
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


def queue_left(idb, kind: str) -> None:
    """D162/D163: a jump back over the keying that made an action, or the slot that made a material, leaves
    it unused: it goes after the jump too, as with Blender's undo (its record makes it again going forward)."""
    if (u := idmap.uid_of(idb)) and not idb.use_fake_user:
        _LOOSE_GONE.setdefault(u, kind)


def _recorded_loose():
    """The loose inventory the scene that records it is at (None when none is recorded)."""
    from .. import datarec, runtime
    scenes = sorted((x for x in bpy.data.scenes if x.library is None and datarec.is_tracked(x)), key=lambda x: x.name)
    st = runtime._STATES.get(idmap.uid_of(scenes[0])) if scenes else None
    inv = st.values.get(("scene", INV_LOOSE)) if st is not None else None
    return inv if isinstance(inv, list) else None


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
        if kind in _WHEN_UNUSED or kind == "collections":
            return True               # an object (a scene) uses it: that records it
    return False


def made_again(uid: str):
    """An action a jump took away (D162, D155), made again intact from its snapshot: with the F-Curve
    modifiers, pose markers and frame range no action record holds. None when there is none."""
    rec = _LEFT.pop(uid, None)
    return _load_snapshot(uid, rec, {}, None) if rec else None


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
            if kind in _OFF_SCENE and not idb.use_fake_user and (kind != "collections" or uid in COLL_NAMES):
                continue          # D174: object data with no user isn't recorded (Blender drops it at save);
                                  # D175: nor a collection a scene held (the tree writer unlinks, never removes)
            if kind in ("actions", "lm") and ((inv := _recorded_loose()) is None or uid in inv):
                continue          # D162: data nothing uses where the jump went (or no inventory recorded)
            if kind == "actions":
                with contextlib.suppress(Exception):
                    _LEFT[uid] = _take(kind, idb, None, False)[1]
            idmap.unnote(idb)
            _remove(_LOOSE_COLL.get(kind, kind), idb)
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
