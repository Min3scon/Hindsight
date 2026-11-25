"""Data adapters: the parts of an object record that the depsgraph reports changes of (see ADAPTERS.md)."""
from __future__ import annotations

from .. import log
from . import settings
from .animation import anim_values, owned_action
from .bones import bones_of, pose_values
from .idprops import _owns_data, anim_of
from .keys import (
    ANIM_KEY, BONES, DATA_PREFIX, DATA_VALUES, POSE_KEY, SETTINGS_KEY, SK_PREFIX, _EXT_CACHE, _EXT_DEPS)
from .materials import groups_of, owned_groups
from .particles import owned_particles
from .settings import DATA_STRUCTS, _DATA_SKIP, _OBJ_SETTINGS_SKIP


def _part(what: str, fn, *args):
    """One part of what an object records; if reading it fails, that part is left out (one log line)
    and the rest is still recorded (the object recorder isn't switched off for it)."""
    try:
        return fn(*args)
    except Exception as e:
        log.error_once(("capture", what, type(e).__name__), f"couldn't read {what}; it is left out: {e}", e)
        return _FAILED


_FAILED = object()


def _upd(out: dict, what: str, fn, *args) -> None:
    r = _part(what, fn, *args)
    if r and r is not _FAILED:
        out.update(r)


def _put(out: dict, key: str, what: str, fn, *args) -> None:
    """out[key] = what fn gives (None too: "no animation" is a value); nothing when reading failed."""
    r = _part(what, fn, *args)
    if r is not _FAILED and (r is not None or key == ANIM_KEY):
        out[key] = r


def _ext_part(idb) -> dict:
    sid = idb.session_uid
    e = _EXT_CACHE.get(sid)
    if e is not None:
        return e
    e = {}
    deps = []
    groups = _part("node groups", owned_groups, idb)                        # D126
    if groups and groups is not _FAILED:
        e.update(groups)
    deps += [g for g in groups_of(idb)] if groups is not _FAILED else []
    for g in (groups_of(idb) if groups is not _FAILED else ()):          # D146: and the actions they play
        gad = g.animation_data
        if gad is not None and gad.action is not None:
            deps.append(gad.action)
    _upd(e, "particle settings", owned_particles, idb)                      # D137
    deps += [ps.settings for ps in getattr(idb, "particle_systems", ()) if ps.settings is not None]
    _put(e, SETTINGS_KEY, "settings", settings.settings_of, idb, _OBJ_SETTINGS_SKIP)  # D117
    if idb.type == "ARMATURE":
        _put(e, POSE_KEY, "pose", pose_values, idb)                          # D119
        if idb.data is not None and idb.data.library is None and _owns_data(idb, idb.data):
            _put(e, DATA_PREFIX + BONES, "bones", bones_of, idb.data)        # D129
    _put(e, ANIM_KEY, "animation", anim_values, idb)                         # D120
    ad = idb.animation_data
    if ad is not None:
        _upd(e, "the action", owned_action, idb)
        if ad.action is not None:
            deps.append(ad.action)
    d = idb.data
    if d is not None and d.library is None and _owns_data(idb, d):
        deps.append(d)
        nested = DATA_STRUCTS.get(d.bl_rna.identifier)
        if nested is not None:
            _put(e, DATA_PREFIX + DATA_VALUES, "the data's settings", settings.settings_of, d, _DATA_SKIP, nested)
        # the shape (D121, D131) is read in capture: geometry_of checks its own counts every time
        # D125: always there (None without animation), so the first keyframe is a change
        if hasattr(d, "animation_data"):
            r = _part("the data's animation", anim_of, d)
            for k, v in (r if r is not _FAILED else {}).items():
                e[(DATA_PREFIX + k) if k == ANIM_KEY else k] = v
            dad = getattr(d, "animation_data", None)
            if dad is not None and dad.action is not None:
                deps.append(dad.action)
        key = getattr(d, "shape_keys", None)
        if key is not None:
            deps.append(key)
            r = _part("the shape keys' animation", anim_of, key)
            for k, v in (r if r is not _FAILED else {}).items():
                e[(SK_PREFIX + k) if k == ANIM_KEY else k] = v
            if key.animation_data is not None and key.animation_data.action is not None:
                deps.append(key.animation_data.action)
    for x in deps:
        try:
            _EXT_DEPS.setdefault(x.session_uid, set()).add(sid)
        except Exception:
            continue
    _EXT_CACHE[sid] = e
    return e
