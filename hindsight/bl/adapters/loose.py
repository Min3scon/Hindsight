"""Data adapters: loose data: texts, library materials and worlds (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
import contextlib
from ...core.types import MISSING
from .. import idmap, log
from . import core, materials
from .core import note_name
from .idprops import _ID_EMPTY4, _ID_EMPTY5, id_props, write_id_props
from .keys import (
    IDPROPS_KEY, INV_GROUPS, INV_LOOSE, LMAT_PREFIX, LOOSE_PREFIXES, LPROPS_PREFIX, LWORLD_PREFIX,
    META_PREFIX, NG_PREFIX, SYSPROPS_KEY, TEXT_PREFIX, _MAT_CACHE)
from .materials import group_owner_map, material_owner_map, write_material


LOOSE_READ_MAX = 32        # unused materials and worlds read again per operation, in turn (see forget_loose_data)
_TEXT_PROPS = ("filepath", "use_module", "indentation")
# Datablocks whose custom properties and add-on data no object records: their own, file-level data.
# (Object data, particle settings and materials an object uses are recorded with the object; node
# groups with D133.)
_PROP_COLLS = ("images", "actions", "palettes", "brushes", "linestyles", "masks", "movieclips", "sounds",
               "fonts", "cache_files", "paint_curves", "textures", "texts")
_LOOSE_TURN = [0]


def _unused(idb) -> bool:
    """No user but its fake user (a library kept for later, data an add-on keeps). One without even
    that isn't kept: a save leaves it out (#104: a reopen then counted it as deleted)."""
    return idb.library is None and idb.use_fake_user and idb.users <= 1


def _loose_mats():
    return [m for m in bpy.data.materials if _unused(m)] + [w for w in bpy.data.worlds if _unused(w)]


def scene_loose(sc) -> dict:
    """D151: datablocks no recorded object and no scene uses, recorded with the first recorded scene by
    name: texts, materials and worlds nothing uses, and every other datablock's custom properties and
    add-on data (where any are set). They aren't in the depsgraph: texts and properties are read
    again at each operation, unused materials and worlds in turn (forget_loose_data)."""
    from .. import datarec
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
        todo.append((values[INV_LOOSE], ("tx", "lm", "lw")))
    if isinstance(values.get(INV_GROUPS), list):
        todo.append((values[INV_GROUPS], ("ng",)))
    if not todo or not _loose_owner(sc):
        return
    from ..persistence import TEXT_NAME
    for want, kinds in todo:
        want = set(want)
        for kind in kinds:
            for idb in list(getattr(bpy.data, _LOOSE_COLL[kind])):
                if idb.library is not None or (kind == "tx" and idb.name == TEXT_NAME):
                    continue
                u = idmap.uid_of(idb)
                if u and u not in want:
                    _LOOSE_GONE[u] = "lw" if isinstance(idb, bpy.types.World) else kind


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
            getattr(bpy.data, _LOOSE_COLL[kind]).remove(idb)
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
