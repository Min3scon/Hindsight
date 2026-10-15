"""Stable ids for every datablock Hindsight records (custom property `_hindsight_uid`).

Blender copies custom properties with the datablock (duplicate, copy, append), so two datablocks can
carry the same id. The first one Hindsight registered keeps it; any other gets a fresh id the first
time Hindsight looks it up (`claim`). Lookups go through a small cache of (collection, name).
"""
from __future__ import annotations

import bpy

from ..core.registry import new_uid

UID_PROP = "_hindsight_uid"

# bpy.data collections searched by find(), in a fixed order (objects first: most lookups)
COLLECTIONS = ("objects", "meshes", "materials", "node_groups", "worlds", "scenes", "collections",
               "cameras", "lights", "curves", "hair_curves", "armatures", "actions", "lattices",
               "metaballs", "grease_pencils", "texts", "images", "shape_keys")

_cache: dict = {}        # uid -> (collection name, datablock name)
_owner: dict = {}        # uid -> as_pointer() of the datablock that owns it this session
# D42: session_uid -> uid. Blender keeps a datablock's session_uid through its own undo and redo,
# but an id property set after Blender pushed its undo step (Hindsight hands out ids after the
# operator) isn't in that step: restore_after_undo() puts the ids back.
_session: dict = {}


def note(idb, u) -> None:
    """Remember which id this datablock carries in this session."""
    try:
        _session[idb.session_uid] = u
    except Exception:
        pass


def unnote(idb) -> None:
    try:
        _session.pop(idb.session_uid, None)
    except Exception:
        pass


def restore_after_undo() -> int:
    """After Blender's own undo or redo: every datablock gets back the id it had in this session
    (an id set after Blender's undo step was pushed is missing, a copy may carry the original's).
    Returns how many were put back."""
    if not _session:
        return 0
    n = 0
    for c in COLLECTIONS:
        col = getattr(bpy.data, c, None)
        if col is None:
            continue
        for idb in col:
            try:
                if idb.library is not None:
                    continue
                u = _session.get(idb.session_uid)
                if u is not None and idb.get(UID_PROP) != u:
                    idb[UID_PROP] = u
                    n += 1
            except Exception:
                continue
    if n:
        _cache.clear()
        _owner.clear()
    return n


def uid_of(idb, create: bool = False) -> str | None:
    """The datablock's id, or None. With create, give it one if it has none."""
    if idb is None:
        return None
    try:
        u = idb.get(UID_PROP)
    except Exception:
        return None
    if u is None:
        if not create:
            return None
        u = new_uid()
        try:
            idb[UID_PROP] = u
        except Exception:
            return None
        _owner[u] = idb.as_pointer()
        note(idb, u)
        return u
    note(idb, u)
    return u


def claim(idb) -> str | None:
    """The datablock's id after resolving copies: if another datablock that still exists already
    owns this id in this session, this one gets a new id. Call when a datablock is about to be
    recorded. Data linked from another file is read only: it never owns an id, so a linked copy of an
    appended object can never take the local one's id away (D28)."""
    if getattr(idb, "library", None) is not None:
        try:
            return idb.get(UID_PROP)
        except Exception:
            return None
    u = uid_of(idb, create=True)
    if u is None:
        return None
    ptr = idb.as_pointer()
    owner = _owner.get(u)
    if owner is None or owner == ptr:
        _owner[u] = ptr
        return u
    if any(x.as_pointer() == owner for x in _all_with(u)):
        nu = new_uid()
        idb[UID_PROP] = nu
        _owner[nu] = ptr
        note(idb, nu)
        return nu
    _owner[u] = ptr              # the old owner is gone (deleted, or undo rebuilt it)
    return u


LIB_PREFIX = "lib:"
_LIB_SEP = "\x1f"


def _collection_of(idb):
    """The bpy.data collection a datablock lives in (for the ones a recorded value can refer to)."""
    t = type(idb).__name__
    return {"Object": "objects", "Mesh": "meshes", "Material": "materials", "Collection": "collections",
            "Scene": "scenes", "World": "worlds", "Camera": "cameras", "Armature": "armatures",
            "Image": "images", "Text": "texts", "Action": "actions", "Lattice": "lattices",
            "MetaBall": "metaballs"}.get(t) or ("node_groups" if "NodeTree" in t else
                                                  "lights" if t.endswith("Light") else
                                                  "curves" if t in ("Curve", "TextCurve", "SurfaceCurve") else None)


def ref_of(idb):
    """An id to record a reference to idb with: a local datablock's Hindsight id, or for data linked
    from another file (D48) its library, type and name ("lib:<collection>|<library path>|<name>")."""
    if idb is None:
        return None
    lib = getattr(idb, "library", None)
    if lib is not None:
        coll = _collection_of(idb)
        if coll is None:
            return None
        return LIB_PREFIX + _LIB_SEP.join((coll, lib.filepath, idb.name))
    return claim(idb)


def _find_linked(ref: str):
    try:
        coll, path, name = ref[len(LIB_PREFIX):].split(_LIB_SEP, 2)
        col = getattr(bpy.data, coll, None)
        return col.get((name, path)) if col is not None else None
    except Exception:
        return None


def _all_with(uid: str):
    for c in COLLECTIONS:
        col = getattr(bpy.data, c, None)
        if col is None:
            continue
        for idb in col:
            try:
                if idb.library is None and idb.get(UID_PROP) == uid:
                    yield idb
            except Exception:
                continue


def find(uid: str):
    """The local datablock with this id, or None (searches bpy.data; cached). Data linked from
    another file is never returned: it can't be changed, and a linked copy of an appended object
    carries the same id (D28)."""
    if not uid:
        return None
    if uid.startswith(LIB_PREFIX):
        return _find_linked(uid)
    hit = _cache.get(uid)
    if hit is not None:
        col = getattr(bpy.data, hit[0], None)
        idb = col.get(hit[1]) if col is not None else None
        if idb is not None and idb.library is None and idb.get(UID_PROP) == uid:
            return idb
    for c in COLLECTIONS:
        col = getattr(bpy.data, c, None)
        if col is None:
            continue
        for idb in col:
            try:
                if idb.library is None and idb.get(UID_PROP) == uid:
                    _cache[uid] = (c, idb.name)
                    return idb
            except Exception:
                continue
    return None


def forget(session: bool = False) -> None:
    """Drop caches (file load, undo: datablocks are rebuilt and pointers change). session: also the
    session ids (a new file, or Hindsight's data removed from this one)."""
    _cache.clear()
    _owner.clear()
    if session:
        _session.clear()
