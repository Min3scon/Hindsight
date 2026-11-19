"""Data adapters: what Hindsight records on a datablock besides mesh channels (see ADAPTERS.md).

An adapter reads a datablock's tracked values as a flat dict (path -> JSON value), and writes given
paths back. Diffing and playback are generic (core). The mesh recorder in runtime.py is adapter one
and keeps its own fast path; everything here is for other data.

The generic RNA adapter reads properties listed in ALLOWLIST by RNA path. Values are normalised:
floats stay Python floats (exact for Blender's float and double properties), vectors, colours,
eulers, quaternions and matrices become lists, enums their identifier, ID pointers the target's
Hindsight id (or None).
"""
from __future__ import annotations

import struct
import time

import bpy

from ..core.types import MISSING
from . import idmap, log, rigmark

# ID type (bl_rna identifier) -> ((path, kind, category), ...). kind: "value" or "ref" (ID pointer).
# category: the data kind the user can switch off in the Advanced panel (KINDS). Rows are written in
# this order: parenting first, modes before the values they convert.
ALLOWLIST: dict = {
    "Object": (
        ("data", "ref", "objects"),           # which mesh (or other data) it uses (D44: Make Single User)
        ("parent", "ref", "parenting"), ("parent_type", "value", "parenting"),
        ("parent_bone", "value", "parenting"), ("parent_vertices", "value", "parenting"),
        ("matrix_parent_inverse", "value", "parenting"),
        ("location", "value", "transforms"), ("rotation_mode", "value", "transforms"),
        ("rotation_euler", "value", "transforms"), ("rotation_quaternion", "value", "transforms"),
        ("rotation_axis_angle", "value", "transforms"), ("scale", "value", "transforms"),
        ("delta_location", "value", "transforms"), ("delta_rotation_euler", "value", "transforms"),
        ("delta_rotation_quaternion", "value", "transforms"), ("delta_scale", "value", "transforms"),
        ("hide_viewport", "value", "visibility"), ("hide_render", "value", "visibility"),
        ("hide_select", "value", "visibility"),
        ("name", "value", "names"),
    ),
}

# Data kinds the user can switch off (Undo History > Advanced). id -> label
KINDS = {
    "transforms": "Transforms",
    "parenting": "Parenting",
    "visibility": "Visibility",
    "names": "Names",
    "collections": "Collections",
    "constraints": "Constraints",
    "edit_steps": "Edit Mode step by step",
    "objects": "Adding and deleting objects",
    "scene_collections": "Making and deleting collections",
}


# Writing these paths makes Blender change others as a side effect (setting rotation_mode converts
# the rotation), so after writing one, its dependents are written again from the recorded state.
DEPENDENTS: dict = {
    "rotation_mode": ("rotation_euler", "rotation_quaternion", "rotation_axis_angle"),
    # D38: setting the parent or the parent bone resets the parent inverse to identity in Blender
    # (ED_object_parent), so the recorded inverse is written again after it
    "parent": ("matrix_parent_inverse",),
    "parent_bone": ("matrix_parent_inverse",),
}


def _fcurves(action, slot) -> list:
    if action is None:
        return []
    try:
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(action, slot)
        if cb is not None:
            return list(cb.fcurves)
        return []
    except Exception:
        pass
    try:                                                    # Blender 4.x: legacy actions
        return list(action.fcurves)
    except Exception:
        return []


def _live(f) -> bool:
    """An F-curve that sets its property: not muted, not in a muted group."""
    if getattr(f, "mute", False):
        return False
    g = getattr(f, "group", None)
    return not (g is not None and getattr(g, "mute", False))


def animated_slots(idb) -> dict:
    """RNA path -> the array indices an animation, an NLA strip or a driver sets on this datablock
    (muted F-curves, groups, tracks and strips and muted or invalid drivers set nothing)."""
    ad = getattr(idb, "animation_data", None)
    if ad is None:
        return {}
    out = {}

    def add(f):
        out.setdefault(f.data_path, set()).add(int(getattr(f, "array_index", 0)))
    for f in ad.drivers:
        drv = getattr(f, "driver", None)
        if _live(f) and getattr(f, "is_valid", True) and (drv is None or getattr(drv, "is_valid", True)):
            add(f)
    if ad.action is not None and getattr(ad, "action_influence", 1.0) > 0.0:
        for f in _fcurves(ad.action, getattr(ad, "action_slot", None)):
            if _live(f):
                add(f)
    if getattr(ad, "use_nla", True):
        for track in ad.nla_tracks:
            if getattr(track, "mute", False):
                continue
            for strip in track.strips:
                if getattr(strip, "mute", False):
                    continue
                for f in _fcurves(strip.action, getattr(strip, "action_slot", None)):
                    if _live(f):
                        add(f)
    return out


def animated_paths(idb) -> set:
    """RNA paths an animation, an NLA strip or a driver sets (any index of them) on this datablock.
    Their values change with the frame, not with the user (Tier 4 records the animation itself)."""
    return set(animated_slots(idb))


def normalise(v):
    """A property value as a JSON value (exact)."""
    t = type(v)
    if t in _PLAIN:
        return v
    if t in _MATH_SEQ:
        return list(v)                                        # their items are always floats
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, bpy.types.ID):
        return idmap.ref_of(v)
    if hasattr(v, "to_list") and hasattr(v, "col"):          # Matrix
        return [list(map(float, row)) for row in v]
    try:
        items = list(v)                                       # Vector, Euler, Quaternion, Color, arrays
    except TypeError:
        return str(v)
    for x in items:
        if type(x) not in _PLAIN:
            try:
                return [normalise(x) for x in items]
            except TypeError:
                return str(v)
    return items                                              # numbers: normalise(x) is x


_PLAIN = frozenset((float, int, bool, str))
try:
    import mathutils as _mu
    _MATH_SEQ = frozenset((_mu.Vector, _mu.Euler, _mu.Quaternion, _mu.Color))
except Exception:
    _MATH_SEQ = frozenset()


def to_rna(v):
    """A recorded value ready to assign: matrices go back as mathutils.Matrix (Blender reads a plain
    nested list assigned to a matrix property column by column, which would transpose it)."""
    if isinstance(v, list) and len(v) in (2, 3, 4) and all(isinstance(r, list) and len(r) == len(v) for r in v):
        import mathutils
        return mathutils.Matrix(v)
    return v


def same(a, b) -> bool:
    """Exact equality for normalised values: floats bit for bit (so -0.0 differs from 0.0 and a NaN
    equals the same NaN), lists element by element, everything else by type and value."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return _F64.pack(a) == _F64.pack(b)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


ENABLED_KINDS = [None]       # set of kind ids, or None for all (datarec keeps this up to date)
MISSING_REFS: dict = {}      # D37: object uid -> {path: (referenced id, adapter, adapter path)}
WANTED: dict = {}            # D60: (object uid, adapter, adapter path) -> the recorded value MISSING_REFS
                             # waits to put in (the object's state holds what Blender has meanwhile)
COLL_NAMES: dict = {}        # D55: collection id -> its name when last seen in a scene (for messages)
REF_NAMES: dict = {}         # D57: id -> how the sidebar named it when the file was saved
_RECREATED: set = set()      # D55: collections Hindsight made again (removed when taken out and empty)
NAME_TAKEN: dict = {}        # D47: uid -> the name it should have, which another datablock holds


def note_name(idb, wanted) -> None:
    """After giving a datablock a recorded name: Blender adds .001 when another one has that name."""
    u = idmap.uid_of(idb)
    if u is None or not isinstance(wanted, str):
        return
    if idb.name != wanted:
        NAME_TAKEN[u] = wanted
    else:
        NAME_TAKEN.pop(u, None)


def name_taken(idb):
    """The name idb should have at this step when another datablock holds it, else None."""
    w = NAME_TAKEN.get(idmap.uid_of(idb))
    return w if w is not None and idb.name != w else None


def _note_ref(owner, path: str, ref, found, aid: str, apath: str) -> None:
    """Remember a recorded reference that couldn't be put back because the object it points to is
    deleted now (the sidebar says so, datarec.resolve_missing_refs puts it in when that object is
    back); forget it once a write of that path succeeds."""
    ou = idmap.uid_of(owner) if owner is not None else None
    if ou is None:
        return
    if ref and found is None:
        MISSING_REFS.setdefault(ou, {})[path] = (ref, aid, apath)
    else:
        d = MISSING_REFS.get(ou)
        if d is not None:
            d.pop(path, None)
            if not d:
                MISSING_REFS.pop(ou, None)


def drop_changed_refs(ou, changes) -> None:
    """D60: a path the user changed since its reference couldn't be put back keeps their change: the
    recorded reference isn't put in when the object it points to comes back."""
    d = MISSING_REFS.get(ou)
    if not d:
        return
    for path, (_ref, aid, apath) in list(d.items()):
        if (aid, apath) in changes:
            d.pop(path, None)
            WANTED.pop((ou, aid, apath), None)
    if not d:
        MISSING_REFS.pop(ou, None)


def missing_refs(owner) -> list:
    """[(path, referenced id)] this object's last restore couldn't set, still deleted now."""
    ou = idmap.uid_of(owner) if owner is not None else None
    d = MISSING_REFS.get(ou) or {}
    return sorted((p, r) for p, (r, _a, _ap) in d.items() if idmap.find(r) is None)


def kind_on(kind: str) -> bool:
    k = ENABLED_KINDS[0]
    return k is None or kind in k


class Adapter:
    """Base class. Subclasses set `id` and `id_types` and implement capture() and write()."""
    id = ""
    id_types: tuple = ()
    budget_ms = 2.0          # capture() limit on the test models, per datablock (tests enforce it)

    def handles(self, idb) -> bool:
        return getattr(getattr(idb, "bl_rna", None), "identifier", None) in self.id_types

    def capture(self, idb) -> dict:
        raise NotImplementedError

    def write(self, idb, values: dict, recorded=None) -> None:
        """values: path -> value, or MISSING to remove (adapters that support removal).
        recorded(path) gives the recorded value of any other path (for side effects)."""
        raise NotImplementedError

    def changed(self, path: str, old, new) -> bool:
        """True if a recorded value differs from the one read now (exact by default)."""
        return not same(old, new)


def _without_animated(a, b) -> bool:
    """True if a and b differ outside what an animation sets (ANIMATED, whole or per item)."""
    if a == ANIMATED or b == ANIMATED:
        return False
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and (ANIMATED in a or ANIMATED in b):
        return any(not same(x, y) for x, y in zip(a, b) if x != ANIMATED and y != ANIMATED)
    return not same(a, b)


_SIMPLE_PATH: dict = {}      # RNA path -> True when it is one attribute name (no dot, no index)


class RNAAdapter(Adapter):
    """Properties from ALLOWLIST, read and written by RNA path."""
    id = "rna"

    def changed(self, path, old, new) -> bool:
        # Keying a value, or a driver taking it over, is not a change the user made: it isn't a step
        return _without_animated(old, new)

    changed.equal_means_unchanged = True    # identical values are never a change (datarec's fast path)

    @property
    def id_types(self):
        return tuple(ALLOWLIST)

    def rows(self, idb):
        return ALLOWLIST.get(idb.bl_rna.identifier, ())

    def capture(self, idb) -> dict:
        out = {}
        anim = animated_slots(idb)
        on = ENABLED_KINDS[0]
        for path, _kind, cat in self.rows(idb):
            if on is not None and cat not in on:
                continue
            try:
                s = _SIMPLE_PATH.get(path)
                if s is None:
                    s = _SIMPLE_PATH[path] = "." not in path and "[" not in path
                v = getattr(idb, path) if s else idb.path_resolve(path)    # a plain name needs no path parsing
                if type(v) not in _PLAIN:
                    v = normalise(v)
            except Exception:
                continue
            idx = anim.get(path)
            if idx:
                # What the animation sets is never recorded: the whole value, or (a vector with only
                # some items keyed) those items, which stand in as ANIMATED and are never written
                if isinstance(v, list) and len(idx) < len(v) and all(0 <= i < len(v) for i in idx):
                    v = [ANIMATED if i in idx else x for i, x in enumerate(v)]
                else:
                    v = ANIMATED
            out[path] = v
        if out.get("parent_type") == "" and getattr(idb, "parent", 1) is None:
            # Blender 4.2: Clear Parent keeps a vertex parent type with no parent; RNA reads it as ''
            # and accepts only OBJECT without a parent. OBJECT is what it can be put back as, and what
            # Blender 5 reads after the same operation.
            out["parent_type"] = "OBJECT"
        return out

    def write(self, idb, values: dict, recorded=None) -> None:
        # In allowlist order: modes before the values they convert (setting rotation_mode makes
        # Blender convert the rotation, so the recorded rotation values are written after it).
        values = dict(values)
        if recorded is not None:
            for path in list(values):
                for dep in DEPENDENTS.get(path, ()):
                    if dep not in values:
                        values[dep] = recorded(dep)
        for path, kind, _cat in self.rows(idb):
            if path not in values:
                continue
            v = values[path]
            if v is MISSING or v == ANIMATED:
                continue
            try:
                owner, attr = _split(idb, path)
                if isinstance(v, list) and ANIMATED in v:
                    cur = getattr(owner, attr)            # only the items the animation doesn't set
                    for i, x in enumerate(v):
                        if x != ANIMATED and i < len(cur):
                            cur[i] = x
                    continue
                if kind == "ref":
                    ref = v
                    v = idmap.find(v) if v else None
                    _note_ref(idb, path, ref, v, self.id, path)
                setattr(owner, attr, to_rna(v))
                if path == "name":
                    note_name(idb, v)
            except Exception as e:
                log.error_once(("rna_write", path, str(e)), f"could not restore {path}: {e}", e)


def _split(idb, path: str):
    """(owner struct, attribute) for an RNA path such as 'location' or 'constraints["A"].influence'."""
    dot = path.rfind(".")
    if dot < 0 or path.rfind("]") > dot:
        return idb, path
    return idb.path_resolve(path[:dot]), path[dot + 1:]


# ----------------------------------------------------------------------------- structs
# Properties that are UI state, not data: never recorded.
# set_inverse_pending (Child Of) is a one-shot request Blender consumes on the next evaluation:
# putting it back would recompute the inverse instead of restoring the recorded one.
_UI_PROPS = frozenset({"rna_type", "show_expanded", "is_override_data_editable", "is_valid", "active",
                       "error_location", "error_rotation", "is_override_data", "show_in_editmode",
                       "show_on_cage", "show_viewport_toggle", "is_active", "set_inverse_pending"})
_PROPS: dict = {}         # (struct type, skip set, skip prefixes) -> ((identifier, type, is_enum_flag), ...)
ANIMATED = "~animated"    # stands in for a value an animation or driver sets (never written back)


def _props(st, skip, skip_prefix):
    """The editable, recordable properties of a struct's type (cached: reading bl_rna.properties is
    the slow part of reading a struct)."""
    key = (st.bl_rna.identifier, id(skip), skip_prefix)
    rows = _PROPS.get(key)
    if rows is None:
        rows = []
        for prop in st.bl_rna.properties:
            ident = prop.identifier
            if ident in skip or prop.is_readonly or prop.type == "COLLECTION" or ident.startswith(skip_prefix):
                continue
            rows.append((ident, prop.type, bool(getattr(prop, "is_enum_flag", False))))
        rows = _PROPS[key] = tuple(rows)
    return rows


def _anim_base(st, anim):
    """The struct's RNA path prefix when its owner has animated paths, else None."""
    if not anim:
        return None
    try:
        return st.path_from_id()
    except Exception:
        return None


def struct_values(st, skip=_UI_PROPS, skip_prefix: tuple = (), anim=None) -> dict:
    """Every editable value of an RNA struct (a constraint, a modifier, ...) as JSON values. ID
    pointers become Hindsight ids. Nested structs and collections are left out. Values in `anim`
    (RNA paths an animation or driver sets) are recorded as ANIMATED."""
    out = {}
    base = _anim_base(st, anim)
    for ident, ptype, is_flag in _props(st, skip, skip_prefix):
        if base is not None and f"{base}.{ident}" in anim:
            out[ident] = ANIMATED
            continue
        try:
            v = getattr(st, ident)
        except Exception:
            continue
        if ptype == "POINTER":
            if v is None or isinstance(v, bpy.types.ID):
                out[ident] = {"ref": idmap.ref_of(v)} if v is not None else {"ref": None}
            continue
        if is_flag:
            out[ident] = sorted(v)
            continue
        out[ident] = normalise(v)
    return out


def set_struct_values(st, values: dict, owner=None, prefix: str = "") -> None:
    """Set recorded values back on a struct: ID pointers first (other values may depend on them).
    owner and prefix: the object the struct belongs to and its path there (for D37)."""
    items = sorted(values.items(), key=lambda kv: 0 if isinstance(kv[1], dict) else 1)
    for ident, v in items:
        if v == ANIMATED:
            continue
        try:
            cur = getattr(st, ident)
            if isinstance(v, dict) and "ref" in v:
                ref = v["ref"]
                v = idmap.find(ref) if ref else None
                _note_ref(owner, prefix + ident, ref, v, "object", "constraints")
                if cur == v:
                    continue          # unchanged: no write (Blender reports some writes as errors)
            elif isinstance(v, list) and getattr(st.bl_rna.properties.get(ident), "is_enum_flag", False):
                if sorted(cur) == sorted(v):
                    continue
                v = set(v)
            elif same(normalise(cur), v):
                continue
            setattr(st, ident, to_rna(v))
        except Exception:
            continue


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


# The parts of an object's record the depsgraph reports changes of (its settings, pose, bones,
# animation, node groups, particle settings, its data's settings, shape and animation): kept per
# object until the object or something it depends on changes (datarec.on_update calls
# value_changed), an operator ends with it selected, or Hindsight writes it.
_EXT_CACHE: dict = {}        # object session_uid -> values
_EXT_DEPS: dict = {}         # session_uid of a datablock -> session_uids of the objects using it


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
    _put(e, SETTINGS_KEY, "settings", settings_of, idb, _OBJ_SETTINGS_SKIP)  # D117
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
            _put(e, DATA_PREFIX + DATA_VALUES, "the data's settings", settings_of, d, _DATA_SKIP, nested)
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


class ObjectAdapter(Adapter):
    """Object data that isn't a plain property: the collections it is in, its view-layer hide state
    (the eye in the outliner) and its constraint stack."""
    id = "object"
    id_types = ("Object",)

    def capture(self, idb) -> dict:
        out = {}
        if kind_on("collections"):
            out["collections"] = sorted(_coll_ref(c) for c in users_collection(idb))
        if kind_on("visibility"):
            out["hide"] = _hidden(idb)      # D56: per view layer, not only the one the window shows
        if kind_on("constraints"):
            anim = animated_paths(idb)
            out["constraints"] = [{"type": c.type, "name": c.name, "values": struct_values(c, anim=anim)}
                                  for c in idb.constraints]
        if idb.type == "MESH" and idb.data is not None:
            out.update(mesh_lists(idb))        # D108: the lists the weights and shapes belong to
        if hasattr(idb, "modifiers"):
            out.update(modifier_values(idb))   # D110: the modifier stack and each modifier's settings
        out.update(_ext_part(idb))             # D117 to D137: kept until a dependency changes
        d = idb.data
        if (d is not None and d.bl_rna.identifier in _GEOM_TYPES and d.library is None and idb.mode != "EDIT"
                and d.bl_rna.identifier in DATA_STRUCTS and _owns_data(idb, d)):
            # not kept with the rest: a point cloud's resize doesn't tell the depsgraph (D134)
            _put(out, DATA_PREFIX + GEOM, "the shape", geometry_of, d)      # D121, D131
        if idb.data is not None and hasattr(idb.data, "materials"):
            if len(idb.material_slots):
                hit = _OBMAT_CACHE.get(idb.session_uid)
                if hit is None:
                    r = _part("materials", owned_materials, idb)
                    r = r if r is not _FAILED else None
                    hit = (material_slots(idb), r, _owned_refs(idb, r))          # D111, D112
                    _OBMAT_CACHE[idb.session_uid] = hit
                out["slots"] = hit[0]
                if hit[1]:
                    out.update(hit[1])
                    _upd(out, "material properties", material_props, hit[2])
            else:                               # what material_slots gives for none (shared: never changed)
                i = int(idb.active_material_index)
                out["slots"] = _NO_SLOTS.get(i) or _NO_SLOTS.setdefault(i, [i])
        # custom properties are read every time: ob["name"] = value doesn't tell the depsgraph
        _upd(out, "custom properties", id_props, idb,
             animated_paths(idb) if idb.animation_data is not None else None)   # D114
        d = idb.data
        if d is not None and d.library is None and _owns_data(idb, d):
            props = _part("the data's custom properties", id_props, d,
                          animated_paths(d) if getattr(d, "animation_data", None) is not None else None)
            for k, v in (props if props is not _FAILED else {}).items():
                out[DATA_PREFIX + k] = v      # the mesh's (curve's, light's, ...) own, with its owner
        return out

    def write(self, idb, values: dict, recorded=None) -> None:
        _OBMAT_CACHE.pop(idb.session_uid, None)
        _EXT_CACHE.pop(idb.session_uid, None)
        for path, v in values.items():          # D126: node groups before the modifiers using them
            if path.startswith(NG_PREFIX) and isinstance(v, dict):
                write_group(path[len(NG_PREFIX):], v)
            elif path.startswith(PART_PREFIX) and isinstance(v, dict):     # D137
                st = idmap.find(path[len(PART_PREFIX):])
                if st is not None:
                    write_settings(st, v)
        for path, v in values.items():          # D146: the groups' animation, after the groups
            if path.startswith(NGA_PREFIX) and isinstance(v, dict):
                ng = idmap.find(path[len(NGA_PREFIX):])
                if ng is not None:
                    write_anim_of(ng, v)
        if "collections" in values and values["collections"] is not MISSING:
            want = set(values["collections"])
            have = {_coll_ref(c): c for c in idb.users_collection}
            missing = []
            for ref in sorted(want - set(have)):
                c = _coll_from_ref(ref)
                if c is None:
                    if not ref.startswith("scene:"):
                        missing.append(ref)
                    continue
                try:
                    c.objects.link(idb)
                except Exception as e:
                    log.error_once(("coll_link", str(e)), f"could not put the object back in a collection: {e}", e)
            # D55: a collection that's deleted now: the object goes in when it's back (the global
            # history brings collections back before it puts objects in them)
            _note_ref(idb, "collections", missing[0] if missing else None, None, "object", "collections")
            for ref in set(have) - want:
                if len(idb.users_collection) > 1:      # never take an object out of its last collection
                    try:
                        have[ref].objects.unlink(idb)
                    except Exception:
                        pass
        if "hide" in values and values["hide"] is not MISSING:
            _set_hidden(idb, values["hide"])
        if "constraints" in values and values["constraints"] is not MISSING:
            _write_constraints(idb, values["constraints"])
        if idb.type == "MESH" and idb.data is not None:
            write_mesh_lists(idb, values)
        if hasattr(idb, "modifiers"):
            write_modifiers(idb, values, recorded)
        for path, v in values.items():          # D112: materials before the slots that hold them
            if path.startswith(MAT_PREFIX) and isinstance(v, dict):
                write_material(path[len(MAT_PREFIX):], v)
            elif path.startswith(MATP_PREFIX) and isinstance(v, dict):
                m = idmap.find(path[len(MATP_PREFIX):])
                if m is not None:
                    write_id_props(m, v)
        if "slots" in values and values["slots"] is not MISSING and idb.data is not None:
            write_material_slots(idb, values["slots"])
        write_id_props(idb, values)             # D114: custom properties and add-on data
        dv = {k[len(DATA_PREFIX):]: v for k, v in values.items() if k.startswith(DATA_PREFIX)}
        if dv and idb.data is not None and idb.data.library is None:
            write_id_props(idb.data, dv)
            if DATA_VALUES in dv and dv[DATA_VALUES] is not MISSING:
                write_settings(idb.data, dv[DATA_VALUES], DATA_STRUCTS.get(idb.data.bl_rna.identifier) or ())
            if GEOM in dv and dv[GEOM] is not MISSING and idb.mode != "EDIT":
                write_geometry(idb.data, dv[GEOM])          # after the settings (lattice resolution)
        if SETTINGS_KEY in values and values[SETTINGS_KEY] is not MISSING:
            write_settings(idb, values[SETTINGS_KEY])
        for path, v in values.items():          # D120: the actions, then which one it plays
            if path.startswith(ACT_PREFIX) and v is not MISSING:
                write_action(path, v)
        if ANIM_KEY in values and values[ANIM_KEY] is not MISSING:
            write_anim(idb, values[ANIM_KEY])
        for path, v in values.items():          # D125: its data's, shape keys' and materials'
            if v is MISSING:
                continue
            if path == DATA_PREFIX + ANIM_KEY and idb.data is not None:
                write_anim(idb.data, v)
            elif path == SK_PREFIX + ANIM_KEY and getattr(idb.data, "shape_keys", None) is not None:
                write_anim(idb.data.shape_keys, v)
            elif path.startswith(MATA_PREFIX):
                mu, _sep, k = path[len(MATA_PREFIX):].partition("|")
                m = idmap.find(mu)
                if m is not None and getattr(m, "node_tree", None) is not None and k == ANIM_KEY:
                    write_anim(m.node_tree, v)
        b = values.get(DATA_PREFIX + BONES, MISSING)
        if b is not MISSING and idb.type == "ARMATURE":
            write_bones(idb, b)                 # D129: the bones before their pose
        if POSE_KEY in values and values[POSE_KEY] is not MISSING and idb.type == "ARMATURE":
            write_pose(idb, values[POSE_KEY])


# ----------------------------------------------------------------------------- materials (D112)
MAT_PREFIX = "@mat:"
MATP_PREFIX = "@matp:"
_MAT_SKIP = frozenset(_UI_PROPS | {"preview_render_type", "use_preview_world", "paint_active_slot",
                                   "use_nodes", "pass_index"}) - {"pass_index"}
_NODE_SKIP = frozenset(_UI_PROPS | {"location", "location_absolute", "width", "height", "dimensions",
                                    "select", "show_options", "show_preview", "show_texture", "hide",
                                    "is_active_output", "internal_links", "parent", "warning_propagation",
                                    "use_custom_color", "color", "name", "bl_idname", "bl_label",
                                    "bl_description", "bl_icon", "bl_static_type", "bl_width_default",
                                    "bl_width_min", "bl_width_max", "bl_height_default", "bl_height_min",
                                    "bl_height_max", "type"})


# D112: a material's record is read again only after the material or its node tree changed (the
# depsgraph says so, datarec.on_update calls material_changed); reading every material's node tree on
# every operator was three times the cost of the whole operator at 500 objects.
_MAT_CACHE: dict = {}        # material session_uid -> record
_MAT_OWNER_OF: dict = {}     # material session_uid -> uid of the object that records it
_TREE_MAT: dict = {}         # node tree session_uid -> material session_uid


def material_changed(idb) -> str | None:
    """A Material or a node tree changed: forget its record. Returns the uid of the object that
    records it (to be compared again), or None."""
    try:
        k = idb.session_uid
    except Exception:
        return None
    if isinstance(idb, bpy.types.NodeTree):
        _MAT_GROUPS.clear()
        k = _TREE_MAT.get(k, k)          # a material's own tree, or a node group (D126)
    _MAT_CACHE.pop(k, None)
    return _MAT_OWNER_OF.get(k)


def forget_materials() -> None:
    _EXT_CACHE.clear()
    _EXT_DEPS.clear()
    _OBMAT_CACHE.clear()
    _BONES_CACHE.clear()
    _MAT_GROUPS.clear()
    _VAL_CACHE.clear()
    _GEOM_CACHE.clear()
    _ACT_CACHE.clear()
    _MAT_CACHE.clear()
    _MAT_OWNER_OF.clear()
    _TREE_MAT.clear()


# ----------------------------------------------------------------------------- node groups (D126)
NG_PREFIX = "@ng:"
NGA_PREFIX = "@nga:"       # a node group's own animation (keys on its inputs, drivers, NLA): "@nga:<group id>" -> anim_of
_IFACE_SKIP = frozenset(_UI_PROPS | {"name", "socket_type", "select", "bl_socket_idname", "identifier",
                                     "item_type", "in_out", "index", "position", "parent", "is_panel_toggle"})


def _groups_in_tree(nt, out: list, seen: set) -> None:
    for n in nt.nodes:
        g = getattr(n, "node_tree", None)
        if g is not None and g.library is None and g.as_pointer() not in seen:
            seen.add(g.as_pointer())
            out.append(g)
            _groups_in_tree(g, out, seen)


def groups_of(ob) -> list:
    """Node groups this object's Geometry Nodes modifiers and its materials use (nested ones too)."""
    if not len(bpy.data.node_groups):
        return []
    out, seen = [], set()
    for m in getattr(ob, "modifiers", ()):
        g = getattr(m, "node_group", None)
        if g is not None and g.library is None and g.as_pointer() not in seen:
            seen.add(g.as_pointer())
            out.append(g)
            _groups_in_tree(g, out, seen)
    for sl in getattr(ob, "material_slots", ()):
        mt = sl.material
        nt = getattr(mt, "node_tree", None) if mt is not None else None
        if nt is None:
            continue
        lst = _MAT_GROUPS.get(nt.session_uid)
        if lst is None:                  # kept until a node tree changes (walking every node is slow)
            lst = []
            _groups_in_tree(nt, lst, set())
            _MAT_GROUPS[nt.session_uid] = lst
        for g in lst:
            if g.as_pointer() not in seen:
                seen.add(g.as_pointer())
                out.append(g)
    return out


_MAT_GROUPS: dict = {}       # material node tree session_uid -> node groups it uses (nested too)


def group_owner_map() -> dict:
    """node group pointer -> uid of the object that records it (the first recorded object by name
    that uses it). Kept for one recorder frame."""
    fc = idmap.frame_cache()
    if fc is not None and "ng_owner" in fc:
        return fc["ng_owner"]
    from . import datarec
    users = []
    for u in datarec.tracked_uids():
        ob = idmap.find(u)
        if isinstance(ob, bpy.types.Object):
            users.append((ob.name, u, ob))
    out = {}
    for _n, u, ob in sorted(users, key=lambda t: t[0]):
        for g in groups_of(ob):
            out.setdefault(g.as_pointer(), u)
    if fc is not None:
        fc["ng_owner"] = out
    return out


def interface_items(ng) -> list:
    iface = getattr(ng, "interface", None)
    if iface is None:
        return []
    rows = []
    for it in iface.items_tree:
        if it.item_type == "SOCKET":
            rows.append(["SOCKET", it.in_out, it.name, it.socket_type, it.identifier,
                         struct_values(it, _IFACE_SKIP)])
        else:
            rows.append(["PANEL", "", it.name, "", "", struct_values(it, _IFACE_SKIP)])
    return rows


def group_record(ng, owner=None) -> dict:
    k = ng.session_uid
    if owner is not None:
        _MAT_OWNER_OF[k] = owner
    rec = _MAT_CACHE.get(k)
    if rec is None:
        rec = _MAT_CACHE[k] = {"tree": capture_tree(ng), "iface": interface_items(ng)}
    return rec


def owned_groups(ob) -> dict:
    groups = groups_of(ob)
    if not groups:
        return {}
    uid = idmap.uid_of(ob)
    owners = None
    out = {}
    for g in groups:
        if g.users > 1 + bool(g.use_fake_user):
            if owners is None:
                owners = group_owner_map()
            if owners.get(g.as_pointer()) != uid:
                continue
        gu = idmap.claim(g)
        if gu:
            try:
                out[NG_PREFIX + gu] = group_record(g, uid)
                out[NGA_PREFIX + gu] = anim_of(g)            # D146: always there (None without animation)
            except Exception:
                continue
    return out


def write_interface(ng, rows) -> None:
    iface = getattr(ng, "interface", None)
    if iface is None or not isinstance(rows, list):
        return
    want_ids = {r[4] for r in rows if r[0] == "SOCKET"}
    for it in [x for x in iface.items_tree if x.item_type == "SOCKET" and x.identifier not in want_ids]:
        iface.remove(it)
    have = {x.identifier: x for x in iface.items_tree if x.item_type == "SOCKET"}
    for r in rows:
        if r[0] != "SOCKET" or r[4] in have:
            continue
        try:
            it = iface.new_socket(r[2], in_out=r[1], socket_type=r[3])
            have[r[4]] = it              # (a socket made again gets a new identifier: Blender's)
        except Exception as e:
            log.error_once(("iface", str(e)), f"could not restore a group input or output: {e}", e)
    order = [have[r[4]] for r in rows if r[0] == "SOCKET" and r[4] in have]
    for pos, it in enumerate(order):
        try:
            if it.position != pos:
                iface.move(it, pos)
        except Exception:
            pass
    for r in rows:
        it = have.get(r[4]) if r[0] == "SOCKET" else None
        if it is None:
            continue
        try:
            if it.name != r[2]:
                it.name = r[2]
            set_struct_values(it, r[5] or {})
        except Exception:
            pass


def write_group(gu: str, rec) -> None:
    ng = idmap.find(gu)
    if ng is None or not isinstance(rec, dict):
        return
    _MAT_CACHE.pop(ng.session_uid, None)
    try:
        write_interface(ng, rec.get("iface"))
        if rec.get("tree"):
            write_tree(ng, rec["tree"])
    except Exception as e:
        log.error_once(("group", str(e)), f"could not restore the node group {ng.name}: {e}", e)
    _MAT_CACHE.pop(ng.session_uid, None)


def material_owner_map() -> dict:
    """material pointer -> uid of the tracked object that records it: the first by name among the
    tracked objects that use it (as for shared meshes, D39). Kept for one recorder frame."""
    fc = idmap.frame_cache()
    if fc is not None and "mat_owner" in fc:
        return fc["mat_owner"]
    from . import datarec
    out = {}
    users = []
    for u in datarec.tracked_uids():
        ob = idmap.find(u)
        if isinstance(ob, bpy.types.Object) and ob.data is not None and hasattr(ob.data, "materials"):
            users.append((ob.name, u, ob))
    for _n, u, ob in sorted(users):
        for sl in ob.material_slots:
            m = sl.material
            if m is not None and m.library is None:
                out.setdefault(m.as_pointer(), u)
    if fc is not None:
        fc["mat_owner"] = out
    return out


def owned_materials(ob) -> dict:
    uid = idmap.uid_of(ob)
    out = {}
    seen = set()
    owners = None
    single = ob.data is not None and ob.data.users == 1
    for sl in ob.material_slots:
        m = sl.material
        if m is None or m.library is not None or m.as_pointer() in seen:
            continue
        seen.add(m.as_pointer())
        # used by this object alone (its only user is this slot): no need to look at the others
        if not (m.users == 1 and (single or sl.link == "OBJECT")):
            if owners is None:
                owners = material_owner_map()
            if owners.get(m.as_pointer()) != uid:
                continue
        mu = idmap.claim(m)
        if mu:
            try:
                out[MAT_PREFIX + mu] = material_record(m, uid)
            except Exception:
                continue
    return out


# D117 style: an object's material part (slots, owned material records) is kept until the depsgraph
# reports the object changed (a slot or its material changing tags it) or an operator ends with it
# selected; what the depsgraph doesn't see is read every time (material_props)
_OBMAT_CACHE: dict = {}      # object session_uid -> (slots, {MAT_PREFIX + id: record})


def _owned_refs(ob, owned) -> list:
    """[(material id, material)] for the owned records (kept with them: no search every time)."""
    if not owned:
        return []
    by_id = {}
    for sl in ob.material_slots:
        m = sl.material
        if m is not None:
            u = idmap.uid_of(m)
            if u is not None:
                by_id[u] = m
    return [(k[len(MAT_PREFIX):], by_id[k[len(MAT_PREFIX):]]) for k in owned
            if k[len(MAT_PREFIX):] in by_id]


def material_props(refs) -> dict:
    """D114/D125: the owned materials' custom properties and add-on data, and their node trees'
    animation, read every time (setting m["name"] = value doesn't tell the depsgraph)."""
    out = {}
    for mu, m in refs:
        out[MATP_PREFIX + mu] = id_props(m, animated_paths(m) if m.animation_data is not None else None)
        nt = getattr(m, "node_tree", None)
        if nt is not None:
            for k, v in anim_of(nt).items():
                out[(MATA_PREFIX + mu + "|" + k) if k == ANIM_KEY else k] = v
    return out


def material_record(m, owner=None) -> dict:
    k = m.session_uid
    if owner is not None:
        _MAT_OWNER_OF[k] = owner
    rec = _MAT_CACHE.get(k)
    if rec is not None:
        return rec
    vals = struct_values(m, _MAT_SKIP)
    for p in animated_paths(m):     # (a Material has no path_from_id: struct_values can't mark them)
        if p in vals:
            vals[p] = ANIMATED
    rec = {"values": vals}
    nt = getattr(m, "node_tree", None)
    if nt is not None:
        rec["tree"] = capture_tree(nt)
        _TREE_MAT[nt.session_uid] = k
    _MAT_CACHE[k] = rec
    return rec


def _socket_value(sock):
    if not hasattr(sock, "default_value"):
        return None
    v = sock.default_value
    if isinstance(v, bpy.types.ID):
        return {"ref": idmap.ref_of(v)}
    try:
        return normalise(v)
    except Exception:
        return None


def capture_tree(nt) -> dict:
    """A node tree: its nodes (name, type), each node's own settings and input values, and its links.
    A node's position isn't part of it (moving a node isn't a step)."""
    nodes = []
    vals = {}
    anim = animated_paths(nt)       # animated values change with the frame: left out
    for n in nt.nodes:
        nodes.append([n.name, n.bl_idname])
        pre = f'nodes["{bpy.utils.escape_identifier(n.name)}"].' if anim else ""
        rec = {"props": struct_values(n, _NODE_SKIP, anim=anim)}
        ins = []
        for i, sock in enumerate(n.inputs):
            if anim and f"{pre}inputs[{i}].default_value" in anim:
                continue
            v = _socket_value(sock)
            if v is not None:
                ins.append([sock.identifier, v])
        if ins:
            rec["in"] = ins
        if n.bl_idname in ("ShaderNodeValue", "ShaderNodeRGB") and n.outputs:
            ov = _socket_value(n.outputs[0])
            if ov is not None:
                rec["out"] = ov
        items = _node_items(n)                  # D130: zone and switch items (their sockets)
        if items:
            rec["items"] = items
        po = getattr(n, "paired_output", None)  # D130: a zone's input node and its output node
        if po is not None:
            rec["pair"] = po.name
        vals[n.name] = rec
    # sockets by identifier, and by position for item sockets made again (new identifiers)
    links = [[l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier,
              _sock_index(l.from_node.outputs, l.from_socket), _sock_index(l.to_node.inputs, l.to_socket)]
             for l in nt.links]
    return {"nodes": nodes, "node": vals, "links": links}


_ITEM_COLLS_SKIP = frozenset({"inputs", "outputs", "internal_links", "panel_states"})
_ITEM_SKIP = frozenset(_UI_PROPS | {"color"})


def _sock_index(socks, sock) -> int:
    for i, x in enumerate(socks):
        if x == sock:
            return i
    return -1


def _node_items(n) -> dict:
    out = {}
    for p in n.bl_rna.properties:
        if p.type != "COLLECTION" or p.identifier in _ITEM_COLLS_SKIP:
            continue
        coll = getattr(n, p.identifier, None)
        if coll is None or not hasattr(coll, "new"):
            continue
        out[p.identifier] = [struct_values(it, _ITEM_SKIP) for it in coll]
    return out


def _item_key(v) -> tuple:
    return (v.get("name"), v.get("socket_type") or v.get("data_type"))


def _write_items(node, items: dict) -> None:
    for cname, want in items.items():
        coll = getattr(node, cname, None)
        if coll is None:
            continue
        have = [_item_key(struct_values(it, _ITEM_SKIP)) for it in coll]
        if have != [_item_key(w) for w in want]:
            coll.clear()
            for w in want:
                t = w.get("socket_type") or w.get("data_type")
                name = w.get("name") or ""
                for args in ((t, name), (name,), ()):
                    if args and args[0] is None:
                        continue
                    try:
                        coll.new(*args)
                        break
                    except Exception:
                        continue
        for it, w in zip(coll, want):
            set_struct_values(it, {k: x for k, x in w.items() if k not in ("socket_type", "data_type")})


def write_material(muid: str, rec: dict) -> None:
    m = idmap.find(muid)
    if m is None:
        return
    _MAT_CACHE.pop(m.session_uid, None)
    _OBMAT_CACHE.clear()
    try:
        set_struct_values(m, rec.get("values") or {})
        tree = rec.get("tree")
        if tree and getattr(m, "node_tree", None) is not None:
            write_tree(m.node_tree, tree)
    except Exception as e:
        log.error_once(("material", str(e)), f"could not restore material {m.name}: {e}", e)


def _set_socket(sock, v) -> None:
    if isinstance(v, dict) and "ref" in v:
        v = idmap.find(v["ref"]) if v["ref"] else None
    cur = _socket_value(sock)
    if cur == v or (isinstance(v, bpy.types.ID) and sock.default_value == v):
        return
    sock.default_value = to_rna(v) if not isinstance(v, bpy.types.ID) else v


def write_tree(nt, tree: dict) -> None:
    want = [(n, t) for n, t in tree.get("nodes") or ()]
    names = {n for n, _t in want}
    for node in [x for x in nt.nodes if x.name not in names or (x.name, x.bl_idname) not in set(want)]:
        nt.nodes.remove(node)
    for n, t in want:
        if nt.nodes.get(n) is None:
            node = nt.nodes.new(t)
            node.name = n
    for n, rec in (tree.get("node") or {}).items():
        node = nt.nodes.get(n)
        if node is None:
            continue
        set_struct_values(node, rec.get("props") or {})
        if rec.get("items"):
            try:
                _write_items(node, rec["items"])
            except Exception as e:
                log.error_once(("node_items", str(e)), f"could not restore the items of {node.name}: {e}", e)
        by_id = {s.identifier: s for s in node.inputs}
        for ident, v in rec.get("in") or ():
            sock = by_id.get(ident)
            if sock is not None:
                try:
                    _set_socket(sock, v)
                except Exception:
                    pass
        if "out" in rec and node.outputs:
            try:
                _set_socket(node.outputs[0], rec["out"])
            except Exception:
                pass
    for n, rec in (tree.get("node") or {}).items():     # D130: zones paired once both nodes exist
        node, out = nt.nodes.get(n), nt.nodes.get(rec.get("pair") or "")
        if node is not None and out is not None and hasattr(node, "pair_with_output"):
            po = getattr(node, "paired_output", None)
            if po is None or po.name != out.name:
                try:
                    node.pair_with_output(out)
                except Exception:
                    pass
    want_links = [tuple(l) for l in tree.get("links") or ()]
    have = [(l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier,
             _sock_index(l.from_node.outputs, l.from_socket), _sock_index(l.to_node.inputs, l.to_socket))
            for l in nt.links]
    if [h[:len(w)] for h, w in zip(have, want_links)] != want_links or len(have) != len(want_links):
        nt.links.clear()
        for row in want_links:
            fn, fs, tn, ts = row[:4]
            fi, ti = (row[4], row[5]) if len(row) >= 6 else (-1, -1)
            a, b = nt.nodes.get(fn), nt.nodes.get(tn)
            if a is None or b is None:
                continue
            so = next((x for x in a.outputs if x.identifier == fs), None)
            si = next((x for x in b.inputs if x.identifier == ts), None)
            if so is None and 0 <= fi < len(a.outputs):
                so = a.outputs[fi]          # an item socket made again: same place, new identifier
            if si is None and 0 <= ti < len(b.inputs):
                si = b.inputs[ti]
            if so is not None and si is not None:
                nt.links.new(so, si)


# ----------------------------------------------------------------------------- material slots (D111)
def material_slots(ob) -> list:
    """[[link, material id], ...] and, last, the active slot's index."""
    rows = []
    mats = ob.data.materials
    for i, sl in enumerate(ob.material_slots):
        dm = mats[i] if i < len(mats) else None
        om = sl.material if sl.link == "OBJECT" else None
        rows.append([sl.link, ref_of_material(dm), ref_of_material(om)])
    return rows + [int(ob.active_material_index)]


def ref_of_material(m):
    return idmap.ref_of(m) if m is not None else None


def write_material_slots(ob, rec) -> None:
    if not isinstance(rec, list) or not rec:
        return
    want, active = rec[:-1], rec[-1]
    data = ob.data
    try:
        mats = data.materials
        if len(mats) != len(want) or len(ob.material_slots) != len(want):
            # pop() leaves the object's own slot count behind (Blender 4.2 to 5.3); clear() doesn't.
            # The mesh's materials are set again below with each slot
            keep = [m for m in mats]
            mats.clear()
            for i in range(len(want)):
                mats.append(keep[i] if i < len(keep) else None)
        for i, row in enumerate(want):
            link, dref = row[0], row[1]
            oref = row[2] if len(row) > 2 else None
            sl = ob.material_slots[i]
            dm = idmap.find(dref) if dref else None
            if dref and dm is None:
                _note_ref(ob, f"data.materials[{i}]", dref, None, "object", "slots")
            if mats[i] != dm:
                mats[i] = dm                      # the mesh's material (also behind an object link)
            if sl.link != link:
                sl.link = link
            if link == "OBJECT":
                om = idmap.find(oref) if oref else None
                if oref and om is None:
                    _note_ref(ob, f"material_slots[{i}].material", oref, None, "object", "slots")
                if sl.material != om:
                    sl.material = om
        if 0 <= int(active) < len(ob.material_slots) and ob.active_material_index != int(active):
            ob.active_material_index = int(active)
    except Exception as e:
        log.error_once(("material_slots", str(e)), f"could not restore the material slots: {e}", e)


# ----------------------------------------------------------------------------- modifiers (D110)
MOD_PREFIX = "mod:"
_MOD_SKIP = frozenset(_UI_PROPS | {"execution_time", "persistent_uid", "is_override_data", "show_expanded",
                                   "is_active", "use_pin_to_last"}) - {"show_in_editmode", "show_on_cage"}
_NOT_REMADE = {"MULTIRES"}       # its subdivision data can't be made again from settings


def modifier_values(ob) -> dict:
    """The stack as [[type, name], ...] and each modifier's settings (its own values, the settings it
    keeps in structs, Geometry Nodes inputs, custom properties), one path per modifier."""
    mods = list(ob.modifiers)
    out = {"modifiers": [[m.type, m.name] for m in mods]}
    anim = animated_paths(ob)
    for m in mods:
        try:
            rec = {"values": struct_values(m, _MOD_SKIP, anim=anim)}
            nested = _nested_values(m)
            if nested:
                rec["nested"] = nested
            gn = _gn_inputs(m)
            if gn is not None:
                rec["gn"] = gn
            custom = _id_props(m)
            if custom:
                rec["custom"] = custom
            pr = _particles(m)
            if pr is not None:
                rec["particles"] = {"seed": pr["seed"], "settings": pr["settings"]}
            out[MOD_PREFIX + m.name] = rec
        except Exception:
            continue
    return out


def write_modifiers(ob, values: dict, recorded=None) -> None:
    """Make the stack what it was recorded as: modifiers removed and added by name, moved into order
    (never all removed and added again: bind data and caches of the ones that stay are kept), then
    each changed modifier's settings put back."""
    layout = values.get("modifiers")
    if layout is MISSING:
        layout = None
    try:
        if isinstance(layout, list):
            want = [(t, n) for t, n in layout]
            names = [n for _t, n in want]
            for m in [m for m in ob.modifiers if m.name not in names or
                      (m.name, m.type) not in {(n, t) for t, n in want}]:
                ob.modifiers.remove(m)
            for t, n in want:
                if ob.modifiers.get(n) is None and t not in _NOT_REMADE:
                    m = ob.modifiers.new(n, t)
                    if m is not None and m.name != n:
                        m.name = n
                    rec = values.get(MOD_PREFIX + n)
                    if rec is None and recorded is not None:
                        rec = recorded(MOD_PREFIX + n)
                    if isinstance(rec, dict) and m is not None:
                        _set_modifier(ob, m, rec)
            present = [n for _t, n in want if ob.modifiers.get(n) is not None]
            for i, n in enumerate(present):
                cur = [m.name for m in ob.modifiers].index(n)
                if cur != i:
                    ob.modifiers.move(cur, i)
    except Exception as e:
        log.error_once(("modifier_stack", str(e)), f"could not restore the modifier stack: {e}", e)
    for path, rec in values.items():
        if not path.startswith(MOD_PREFIX) or not isinstance(rec, dict):
            continue
        m = ob.modifiers.get(path[len(MOD_PREFIX):])
        if m is not None:
            try:
                _set_modifier(ob, m, rec)
            except Exception as e:
                log.error_once(("modifier_values", str(e)), f"could not restore {m.name}: {e}", e)


def _set_modifier(ob, m, rec: dict) -> None:
    vals = rec.get("values") or {}
    if "node_group" in vals:                    # the node group first: the inputs are its sockets
        set_struct_values(m, {"node_group": vals["node_group"]}, ob, f'modifiers["{m.name}"].')
    set_struct_values(m, vals, ob, f'modifiers["{m.name}"].')
    _set_id_props(m, rec.get("custom"))
    _set_gn_inputs(m, rec.get("gn"))
    _set_physics_modifier(ob, m, {"nested": rec.get("nested"), "particles": None})
    pr = rec.get("particles")
    ps = getattr(m, "particle_system", None)
    if isinstance(pr, dict) and ps is not None:
        want = idmap.find(pr["settings"]) if pr.get("settings") else None
        if want is not None and ps.settings != want:
            made = ps.settings
            ps.settings = want
            if made is not None and made.users == 0:
                bpy.data.particles.remove(made)
        if ps.seed != int(pr.get("seed", ps.seed)):
            ps.seed = int(pr.get("seed", ps.seed))


# ----------------------------------------------------------------------------- mesh lists (D108)
_SK_FIELDS = ("value", "mute", "slider_min", "slider_max", "vertex_group", "interpolation")


def mesh_lists(ob) -> dict:
    """The vertex group list, the UV map list and the shape key list of a mesh object, with their
    settings (the weights, UV coordinates and key positions are mesh channels). Read cheaply: a few
    attributes per item."""
    me = ob.data
    out = {"vgroups": [[g.name, bool(g.lock_weight)] for g in ob.vertex_groups] + [
        int(ob.vertex_groups.active_index)]}
    uvs = me.uv_layers
    out["uvmaps"] = [[u.name, bool(u.active), bool(u.active_render)] for u in uvs]
    key = me.shape_keys
    if key is None:
        out["shapekeys"] = None
    else:
        blocks = []
        anim = animated_slots(key) if key.animation_data is not None else {}
        for kb in key.key_blocks:
            row = [kb.name] + [normalise(getattr(kb, f)) for f in _SK_FIELDS]
            if anim:
                pre = f'key_blocks["{bpy.utils.escape_identifier(kb.name)}"].'
                for j, f in enumerate(_SK_FIELDS):
                    if pre + f in anim:
                        row[1 + j] = ANIMATED     # D124: keyed or driven, it follows the frame
            row.append(kb.relative_key.name if kb.relative_key is not None else "")
            blocks.append(row)
        out["shapekeys"] = {"blocks": blocks, "use_relative": bool(key.use_relative),
                            "active": int(ob.active_shape_key_index),
                            "only": bool(ob.show_only_shape_key)}
    return out


_PENDING_UV: dict = {}       # object pointer -> UV map flags to set once the mesh is written


def write_mesh_lists(ob, values: dict) -> None:
    """Make the lists what they were recorded as: groups and shape keys removed, added, renamed and
    reordered by position, settings put back. The weights and key positions are written with the mesh
    right after (runtime._goto_history writes data before the mesh), into these lists."""
    made = []
    vg = values.get("vgroups")
    if isinstance(vg, list) and vg:
        try:
            made += _write_vgroups(ob, vg[:-1], vg[-1])
        except Exception as e:
            log.error_once(("vgroups", str(e)), f"could not restore the vertex group list: {e}", e)
    if "shapekeys" in values and values["shapekeys"] is not MISSING:
        try:
            made += _write_shapekeys(ob, values["shapekeys"])
        except Exception as e:
            log.error_once(("shapekeys", str(e)), f"could not restore the shape key list: {e}", e)
    if made:
        _fill_made(ob, made)
    uv = values.get("uvmaps")
    if isinstance(uv, list):
        _PENDING_UV[ob.as_pointer()] = uv


def after_mesh_write(ob) -> None:
    """UV maps are attributes of the mesh, written after the data: their flags go on now."""
    uv = _PENDING_UV.pop(ob.as_pointer(), None)
    if not uv or ob.data is None:
        return
    layers = ob.data.uv_layers
    try:
        for name, active, render in uv:
            lay = layers.get(name)
            if lay is None:
                continue
            if active and not lay.active:
                layers.active = lay
            if render and not lay.active_render:
                lay.active_render = True
    except Exception as e:
        log.error_once(("uvmaps", str(e)), f"could not restore which UV map is active: {e}", e)


def _write_vgroups(ob, want: list, active: int) -> list:
    made = []
    groups = ob.vertex_groups
    names = [n for n, _l in want]
    have = [g.name for g in groups]
    if have != names:
        # keep groups whose names are wanted (their weights stay with them), then rename by position
        for g in [g for g in groups if g.name not in names]:
            # a group at a position where another name is wanted and that name is missing: a rename
            i = list(groups).index(g)
            if i < len(names) and groups.get(names[i]) is None:
                g.name = names[i]
            else:
                groups.remove(g)
        for n in names:
            if groups.get(n) is None:
                groups.new(name=n)
                made.append(("vg", n))
        _reorder(ob, [g.name for g in groups], names, "vertex_group_move")
    for (n, lock) in want:
        g = groups.get(n)
        if g is not None and bool(g.lock_weight) != lock:
            g.lock_weight = lock
    if 0 <= active < len(groups) and groups.active_index != active:
        groups.active_index = active
    return made


def _write_shapekeys(ob, rec) -> list:
    made = []
    me = ob.data
    if rec is None:
        if me.shape_keys is not None:
            ob.shape_key_clear()
        return made
    blocks = rec.get("blocks") or []
    names = [b[0] for b in blocks]
    if me.shape_keys is None and names:
        ob.shape_key_add(name=names[0], from_mix=False)
        made.append(("sk", names[0]))
    key = me.shape_keys
    have = [kb.name for kb in key.key_blocks]
    if have != names:
        for kb in [kb for kb in key.key_blocks if kb.name not in names]:
            i = list(key.key_blocks).index(kb)
            if i < len(names) and key.key_blocks.get(names[i]) is None:
                kb.name = names[i]
            else:
                ob.shape_key_remove(kb)
        for n in names:
            if key.key_blocks.get(n) is None:
                ob.shape_key_add(name=n, from_mix=False)
                made.append(("sk", n))
        _reorder(ob, [kb.name for kb in key.key_blocks], names, "shape_key_move")
    for b in blocks:
        kb = key.key_blocks.get(b[0])
        if kb is None:
            continue
        for f, v in zip(_SK_FIELDS, b[1:1 + len(_SK_FIELDS)]):
            if v == ANIMATED:
                continue                      # D124: the animation sets it
            try:
                if getattr(kb, f) != v:
                    setattr(kb, f, v)
            except Exception:
                pass
        rel = b[1 + len(_SK_FIELDS)] if len(b) > 1 + len(_SK_FIELDS) else ""
        if rel and key.key_blocks.get(rel) is not None and kb.relative_key.name != rel:
            kb.relative_key = key.key_blocks[rel]
    if key.use_relative != rec.get("use_relative", True):
        key.use_relative = rec.get("use_relative", True)
    a = int(rec.get("active", 0))
    if 0 <= a < len(key.key_blocks) and ob.active_shape_key_index != a:
        ob.active_shape_key_index = a
    if ob.show_only_shape_key != bool(rec.get("only", False)):
        ob.show_only_shape_key = bool(rec.get("only", False))
    return made


def _fill_made(ob, made) -> None:
    """A group or shape key the list write made again gets its weights or positions from the object's
    mesh state (Blender 4.2 can record a removal and its weights in two steps, so the mesh write of
    the jump doesn't always carry them)."""
    from . import meshio, runtime
    st = runtime._STATES.get(idmap.uid_of(ob))
    if st is None:
        return
    me = ob.data
    n = len(me.vertices)
    for kind, name in made:
        if kind == "vg":
            arr = st.arrays.get(meshio.vg_key(name))
            g = ob.vertex_groups.get(name)
            if arr is not None and g is not None and arr.size == n:
                meshio._write_vgroup(g, n, arr)
        else:
            key = me.shape_keys
            kb = key.key_blocks.get(name) if key is not None else None
            if kb is None:
                continue
            for k, arr in st.arrays.items():
                if k.startswith(meshio.SK_PREFIX) and meshio._sk_name(k) == name and arr.size == n * 3:
                    kb.data.foreach_set("co", arr)
                    break


def _reorder(ob, have: list, want: list, op: str) -> None:
    """Move items until the order is `want` (the operators move the active item one place at a time)."""
    if have == want or sorted(have) != sorted(want):
        return
    is_vg = op == "vertex_group_move"
    for target, name in enumerate(want):
        cur = [g.name for g in ob.vertex_groups] if is_vg else [k.name for k in ob.data.shape_keys.key_blocks]
        i = cur.index(name)
        if i == target:
            continue
        if is_vg:
            ob.vertex_groups.active_index = i
        else:
            ob.active_shape_key_index = i
        with bpy.context.temp_override(object=ob, active_object=ob):
            for _ in range(i - target):
                if is_vg:
                    bpy.ops.object.vertex_group_move(direction="UP")
                else:
                    bpy.ops.object.shape_key_move(type="UP")


def _give_name(ch, name: str, names: dict) -> None:
    """D55: a collection gets its recorded name back from one that isn't in any scene now (taken out
    by this jump, or left over) or that gets another name in this same jump; one still in use keeps
    its name and this one gets '.001' (the sidebar says so)."""
    if ch.name != name and ch.library is None:
        holder = bpy.data.collections.get(name)
        if (holder is not None and holder != ch and holder.library is None
                and (holder.users == 0 or names.get(idmap.uid_of(holder), name) != name)):
            if hasattr(ch, "rename"):
                ch.rename(name, mode="ALWAYS")
            else:
                holder.name = name + ".001"
                ch.name = name
        else:
            ch.name = name
    note_name(ch, name)


def drop_recreated_orphans() -> None:
    """D55: a collection Hindsight made again that a later jump took out of the scene, with nothing
    left in it, is removed again, as it was when it was deleted (collections the user made stay, so
    their settings are kept when they come back)."""
    for u in list(_RECREATED):
        c = idmap.find(u)
        if c is None:
            _RECREATED.discard(u)
            continue
        if c.users == 0 and not c.objects and not c.children and c.library is None:
            _RECREATED.discard(u)
            idmap.unnote(c)
            bpy.data.collections.remove(c)


_UC_DIRECT = 4      # objects read with Object.users_collection in a frame before the table is built


def users_collection(ob) -> tuple:
    """ob.users_collection: the collections it is in, in the same order (bpy.data.collections, then
    each scene's own collection). Blender's version reads every collection's objects for each object
    it is asked about; in a frame (idmap.frame) that is done once for all of them."""
    fc = idmap.frame_cache()
    if fc is None:
        return ob.users_collection
    m = fc.get("users_collection")
    if m is None:
        n = fc["users_collection_n"] = fc.get("users_collection_n", 0) + 1
        if n <= _UC_DIRECT:
            return ob.users_collection      # a few objects: cheaper than reading everything
        m = {}
        for c in bpy.data.collections:
            for o in c.objects:
                m.setdefault(o.as_pointer(), []).append(c)
        for sc in bpy.data.scenes:
            c = sc.collection
            for o in c.objects:
                m.setdefault(o.as_pointer(), []).append(c)
        fc["users_collection"] = m
    return tuple(m.get(ob.as_pointer(), ()))


def _coll_ref(c) -> str:
    """A collection as a value: its Hindsight id, or "scene:<id>" for a scene's own collection."""
    for sc in bpy.data.scenes:
        if sc.collection == c:
            return "scene:" + (idmap.claim(sc) or "")
    return idmap.claim(c) or ""


def _coll_from_ref(ref: str):
    if ref.startswith("scene:"):
        sc = idmap.find(ref[6:])
        return sc.collection if sc is not None else None
    return idmap.find(ref)


def _write_constraints(ob, want: list, holder=None, base: str = "") -> None:
    """Put a constraint stack back on ob (or on holder, a pose bone of ob, whose path is base)."""
    st = holder if holder is not None else ob
    have = [(c.type, c.name) for c in st.constraints]
    if have == [(w["type"], w["name"]) for w in want]:
        pairs = list(zip(st.constraints, want))
    else:
        for c in list(st.constraints):
            st.constraints.remove(c)
        # each constraint made gets its own recorded values: one that can't be made is left out
        # (zipping the stack with the recorded list gave every later one the wrong values)
        pairs = []
        for w in want:
            try:
                c = st.constraints.new(w["type"])
            except Exception as e:
                log.error_once(("constraint_new", str(e)), f"could not restore a constraint: {e}", e)
                continue
            try:
                c.name = w["name"]
            except Exception as e:
                log.error_once(("constraint_name", str(e)), f"could not name a restored constraint: {e}", e)
            pairs.append((c, w))
    for c, w in pairs:
        set_struct_values(c, w.get("values") or {}, ob, f'{base}constraints["{c.name}"].')


# ----------------------------------------------------------------------------- bones (D129)
BONES = "bones"
_BONE_SKIP = frozenset(_UI_PROPS | {"name", "hide", "hide_select", "select", "select_head", "select_tail"})
_BONES_CACHE: dict = {}        # armature session_uid -> recorded bones


def bones_of(arm) -> dict:
    """An armature's bones as Edit Mode makes them: name, parent, head, tail, orientation (roll),
    connected or not, their settings and bone collections. Read from the armature's bones (Edit Mode
    changes reach them when Edit Mode ends)."""
    k = arm.session_uid
    rec = _BONES_CACHE.get(k)
    if rec is not None:
        return rec
    rows = []
    for b in arm.bones:
        rows.append({"name": b.name, "parent": b.parent.name if b.parent is not None else "",
                     "head": list(b.head_local), "tail": list(b.tail_local),
                     "matrix": [list(r) for r in b.matrix_local], "connect": bool(b.use_connect),
                     "values": struct_values(b, _BONE_SKIP),
                     "colls": sorted(c.name for c in getattr(b, "collections", ()))})
    colls = [[c.name, bool(getattr(c, "is_visible", True))] for c in getattr(arm, "collections_all", ())]
    rec = _BONES_CACHE[k] = {"bones": rows, "collections": colls}
    return rec


def _bone_shape(arm) -> list:
    return [(b.name, b.parent.name if b.parent is not None else "", [round(x, 6) for x in b.head_local],
             [round(x, 6) for x in b.tail_local], [round(x, 6) for r in b.matrix_local for x in r],
             bool(b.use_connect)) for b in arm.bones]


def write_bones(ob, rec) -> None:
    arm = ob.data
    if not isinstance(rec, dict) or arm is None:
        return
    _BONES_CACHE.pop(arm.session_uid, None)
    want = rec.get("bones") or []
    shape = [(w["name"], w["parent"], [round(x, 6) for x in w["head"]], [round(x, 6) for x in w["tail"]],
              [round(x, 6) for r in w["matrix"] for x in r], w["connect"]) for w in want]
    try:
        for name, vis in rec.get("collections") or ():       # bone collections first (by name)
            if arm.collections_all.get(name) is None:
                arm.collections.new(name)
        for c in list(getattr(arm, "collections_all", ())):
            if c.name not in {n for n, _v in rec.get("collections") or ()}:
                arm.collections.remove(c)
        for name, vis in rec.get("collections") or ():
            c = arm.collections_all.get(name)
            if c is not None and c.is_visible != vis:
                c.is_visible = vis
        if _bone_shape(arm) != shape:
            _edit_bones(ob, want)
        for w in want:
            b = arm.bones.get(w["name"])
            if b is None:
                continue
            set_struct_values(b, w.get("values") or {})
            have = {c.name for c in b.collections}
            for cn in set(w.get("colls") or ()) - have:
                c = arm.collections_all.get(cn)
                if c is not None:
                    c.assign(b)
            for cn in have - set(w.get("colls") or ()):
                c = arm.collections_all.get(cn)
                if c is not None:
                    c.unassign(b)
    except Exception as e:
        log.error_once(("bones", str(e)), f"could not restore the bones of {ob.name}: {e}", e)
    _BONES_CACHE.pop(arm.session_uid, None)


def _edit_bones(ob, want) -> None:
    """Put the bones back through a short Edit Mode visit (bones can only be made, moved and parented
    there), then back to the mode the object was in."""
    from mathutils import Matrix
    ctx = bpy.context
    vl = ctx.view_layer
    prev_active = vl.objects.active
    prev_mode = ob.mode
    with ctx.temp_override(active_object=ob, object=ob, selected_objects=[ob], selected_editable_objects=[ob]):
        vl.objects.active = ob
        if prev_mode != "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")
        try:
            eb = ob.data.edit_bones
            names = {w["name"] for w in want}
            for b in [b for b in eb if b.name not in names]:
                eb.remove(b)
            for w in want:
                b = eb.get(w["name"]) or eb.new(w["name"])
                b.use_connect = False
                b.head, b.tail = w["head"], w["tail"]
                b.matrix = Matrix(w["matrix"])
                b.tail = w["tail"]
            for w in want:
                b = eb.get(w["name"])
                b.parent = eb.get(w["parent"]) if w["parent"] else None
                b.use_connect = w["connect"]
        finally:
            if prev_mode != "EDIT":
                bpy.ops.object.mode_set(mode=prev_mode if prev_mode in ("OBJECT", "POSE") else "OBJECT")
    if prev_active is not None and prev_active != ob:
        vl.objects.active = prev_active


# ----------------------------------------------------------------------------- pose (D119)
POSE_KEY = "pose"
_POSE_FIELDS = (("location", 3), ("rotation_quaternion", 4), ("rotation_euler", 3),
                ("rotation_axis_angle", 4), ("scale", 3))


def pose_values(ob) -> dict:
    """An armature object's pose: each pose bone's transform and rotation mode, its constraint stack
    and its custom properties. What an animation or driver sets is ANIMATED."""
    pose = ob.pose
    if pose is None:
        return {}
    bones = pose.bones
    n = len(bones)
    flat = {}
    for f, k in _POSE_FIELDS:
        buf = [0.0] * (n * k)
        bones.foreach_get(f, buf)
        flat[f] = buf
    anim = animated_slots(ob) if ob.animation_data is not None else {}
    out = {}
    for i, pb in enumerate(bones):
        name = pb.name
        row = {}
        pre = f'pose.bones["{bpy.utils.escape_identifier(name)}"].' if anim else ""
        for f, k in _POSE_FIELDS:
            v = flat[f][i * k:(i + 1) * k]
            idx = anim.get(pre + f) if anim else None
            if idx:
                v = [ANIMATED if j in idx else x for j, x in enumerate(v)]
            row[f] = v
        row["rotation_mode"] = pb.rotation_mode
        if len(pb.constraints):
            row["constraints"] = [{"type": c.type, "name": c.name,
                                   "values": struct_values(c, anim=anim or None)} for c in pb.constraints]
        props = _group_json(pb)
        if props:
            row["props"] = props
        out[name] = row
    return out


def write_pose(ob, want: dict) -> None:
    pose = ob.pose
    if pose is None or not isinstance(want, dict):
        return
    for pb in pose.bones:
        row = want.get(pb.name)
        if row is None:
            continue
        try:
            if pb.rotation_mode != row.get("rotation_mode", pb.rotation_mode):
                pb.rotation_mode = row["rotation_mode"]
            for f, _k in _POSE_FIELDS:
                v = row.get(f)
                if not v:
                    continue
                cur = list(getattr(pb, f))
                new = [c if x == ANIMATED else x for x, c in zip(v, cur)]
                if new != cur:
                    setattr(pb, f, new)
            if row.get("constraints") or len(pb.constraints):
                _write_constraints(ob, row.get("constraints") or [], holder=pb,
                                   base=f'pose.bones["{bpy.utils.escape_identifier(pb.name)}"].')
            _write_group(pb, row.get("props") or {})
        except Exception as e:
            log.error_once(("pose", str(e)), f"could not restore the pose of {pb.name}: {e}", e)


# Object settings a body leaves out: the name, data and parent are set on their own; matrices and
# dimensions (D26: setting dimensions turns them into a scale from the mesh as it is now) are
# derived from location, rotation and scale (which are kept exactly).
_BODY_SKIP = frozenset(_UI_PROPS | {"name", "data", "parent", "active_material", "active_material_index",
                                    "matrix_world", "matrix_local", "matrix_basis", "matrix_parent_inverse",
                                    "mode", "select", "active_shape_key_index", "dimensions"})


def _id_props(owner) -> dict:
    """Custom properties as JSON values (groups as dicts, arrays as lists), Hindsight's id left out."""
    out = {}
    try:
        keys = list(owner.keys())
    except TypeError:
        return out
    for k in keys:
        if k == idmap.UID_PROP:
            continue
        v = owner[k]
        if isinstance(v, bpy.types.ID):              # a pointer (an object, a material, ...): by its id
            out[k] = {REF_KEY: idmap.ref_of(v)}
            continue
        if hasattr(v, "to_dict"):
            v = v.to_dict()
        elif hasattr(v, "to_list"):
            v = v.to_list()
        if isinstance(v, (int, float, str, list, dict)):
            out[k] = v
    return out


# ----------------------------------------------------------------------------- ID properties (D114)
# Custom properties and the data add-ons keep on a datablock (their PointerProperty and
# CollectionProperty groups and other registered properties: ID properties underneath). Blender 4 keeps
# both in the datablock's own ID properties; Blender 5 keeps an add-on's in a separate system group.
# They are recorded as they are stored, in one read each, so any add-on's data is recorded without
# knowing the add-on, and putting them back gives exactly what was stored (a value that wasn't set is
# taken out again, so it reads as the add-on's default).
IDPROPS_KEY = "idprops"
SYSPROPS_KEY = "sysprops"
DATA_PREFIX = "data."


# ----------------------------------------------------------------------------- settings (D117)
# An object's own settings (viewport display, ray visibility, instancing, empty display, locks, ...)
# and its data's settings (a light's, a camera's, a curve's or text's, a metaball's, a speaker's, a
# light probe's, a mesh's remesh and texture space settings). Every property the type has, except
# what is recorded elsewhere, what a mode or a panel only shows, and add-ons' properties (D114).
# Kept per datablock until the depsgraph says it changed (datarec.on_update calls value_changed) or an
# operator ends with it selected or active (a few properties don't tell the depsgraph), so reading them
# costs nothing on an operator that doesn't touch them.
_ID_SKIP = frozenset({"name", "use_fake_user", "use_extra_user", "is_runtime_data", "tag", "asset_data"})
_OBJ_SETTINGS_SKIP = frozenset(_UI_PROPS | _ID_SKIP | {p for p, _k, _c in ALLOWLIST["Object"]} | {
    "active_material", "active_material_index", "dimensions", "matrix_world", "matrix_local",
    "matrix_basis", "show_only_shape_key", "active_shape_key_index",
    "use_mesh_mirror_x", "use_mesh_mirror_y", "use_mesh_mirror_z"})     # sculpt symmetry: a tool option
_DATA_SKIP = frozenset(_UI_PROPS | _ID_SKIP | {
    "uv_layer_clone", "uv_layer_clone_index", "uv_layer_stencil", "uv_layer_stencil_index",
    "use_mirror_x", "use_mirror_y", "use_mirror_z", "use_mirror_vertex_groups", "radial_symmetry",
    "use_mirror_topology", "use_paint_bone_selection", "use_paint_mask", "use_paint_mask_vertex",
    "shape_keys", "materials"})
# data types whose settings are recorded, with the settings structs they keep apart
DATA_STRUCTS = {"Mesh": (), "Camera": ("dof", "stereo"), "SpotLight": (), "PointLight": (), "SunLight": (),
                "AreaLight": (), "Curve": (), "TextCurve": (), "SurfaceCurve": (), "MetaBall": (),
                "Speaker": (), "Armature": (), "Lattice": (), "GreasePencil": (), "GreasePencilv3": (), "Curves": (), "PointCloud": (),
                "Volume": ("display", "render"), "LightProbe": (), "LightProbeSphere": (), "LightProbePlane": (),
                "LightProbeVolume": ()}
SETTINGS_KEY = "settings"
DATA_VALUES = "values"
_VAL_CACHE: dict = {}          # session_uid -> recorded settings
_RT_SKIP: dict = {}            # (type, skip id, property count) -> skip set with add-ons' properties


def forget_selected_values() -> None:
    """An operator finished: the selected and active objects' settings are read again. Some
    properties (a mesh's remesh voxel size) don't tell the depsgraph when they change; operators and
    panels change those on the active or selected objects."""
    try:
        ctx = bpy.context
        obs = list(getattr(ctx, "selected_objects", None) or ())
        act = getattr(ctx, "active_object", None)
        if act is not None:
            obs.append(act)
    except Exception:
        return
    for o in obs:
        try:
            _VAL_CACHE.pop(o.session_uid, None)
            _OBMAT_CACHE.pop(o.session_uid, None)
            _EXT_CACHE.pop(o.session_uid, None)
            if o.data is not None:
                _VAL_CACHE.pop(o.data.session_uid, None)
                _GEOM_CACHE.pop(o.data.session_uid, None)
                _BONES_CACHE.pop(o.data.session_uid, None)
            ad = o.animation_data
            if ad is not None and ad.action is not None:
                _ACT_CACHE.pop(ad.action.session_uid, None)
            for ps in getattr(o, "particle_systems", ()):
                if ps.settings is not None:
                    _VAL_CACHE.pop(ps.settings.session_uid, None)
        except Exception:
            continue


def forget_loose_groups() -> None:
    """D133: node groups nothing evaluates aren't in the depsgraph, so it never says they changed:
    their kept records are read again when an operator ends."""
    try:
        nt = getattr(bpy.context.scene, "node_tree", None)
        if nt is not None:
            _MAT_CACHE.pop(nt.session_uid, None)
        if not len(bpy.data.node_groups):
            return                      # (most files: no walk over the objects)
        with idmap.frame():
            used = group_owner_map()
            for g in bpy.data.node_groups:
                if g.as_pointer() not in used:
                    _MAT_CACHE.pop(g.session_uid, None)
    except Exception:
        pass


def value_changed(idb) -> None:
    try:
        _VAL_CACHE.pop(idb.session_uid, None)
        _ACT_CACHE.pop(idb.session_uid, None)
        _GEOM_CACHE.pop(idb.session_uid, None)
        _BONES_CACHE.pop(idb.session_uid, None)
        _OBMAT_CACHE.pop(idb.session_uid, None)
        _EXT_CACHE.pop(idb.session_uid, None)
        for o in _EXT_DEPS.pop(idb.session_uid, ()):
            _EXT_CACHE.pop(o, None)
        if isinstance(idb, (bpy.types.Material, bpy.types.NodeTree)) and _OBMAT_CACHE:
            _OBMAT_CACHE.clear()        # its users' records are read again (cheap: records are kept)
    except Exception:
        pass


def _skip_with_runtime(idb, skip):
    """skip plus the type's add-on (runtime) properties: those are recorded with D114."""
    props = idb.bl_rna.properties
    key = (idb.bl_rna.identifier, id(skip), len(props))
    out = _RT_SKIP.get(key)
    if out is None:
        out = _RT_SKIP[key] = frozenset(skip | {p.identifier for p in props if p.is_runtime})
    return out


def settings_of(idb, skip, nested=()) -> dict:
    k = idb.session_uid
    v = _VAL_CACHE.get(k)
    if v is not None:
        return v
    anim = animated_paths(idb) if getattr(idb, "animation_data", None) is not None else ()
    v = struct_values(idb, _skip_with_runtime(idb, skip))
    if v.get("use_auto_texspace"):
        # worked out from the geometry while auto is on: not a setting then (it changed with every edit)
        v.pop("texspace_location", None)
        v.pop("texspace_size", None)
    for p in anim:                      # (an ID has no path_from_id: struct_values can't mark these)
        if p in v:
            v[p] = ANIMATED
    for n in nested:
        st = getattr(idb, n, None)
        if st is not None:
            v[n] = struct_values(st, anim=anim or None)
    _VAL_CACHE[k] = v
    return v


def write_settings(idb, v, nested=()) -> None:
    if not isinstance(v, dict):
        return
    _VAL_CACHE.pop(idb.session_uid, None)
    try:
        set_struct_values(idb, {k: x for k, x in v.items() if k not in nested})
        for n in nested:
            st = getattr(idb, n, None)
            if st is not None and isinstance(v.get(n), dict):
                set_struct_values(st, v[n])
    except Exception as e:
        log.error_once(("settings", str(e)), f"could not restore the settings of {idb.name}: {e}", e)


# ----------------------------------------------------------------------------- animation (D120)
# Which action an object plays (and its slot on Blender 4.4+), how (blend, influence, extrapolation),
# and the action itself: every F-curve of the object's slot with all its keyframes, read in bulk. An
# action is recorded with the first recorded object by name that plays it with that slot, and kept
# until the depsgraph says it changed (keyframe edits tell it) or an operator ends with a user of it
# selected.
ANIM_KEY = "anim"
ACT_PREFIX = "@act:"
_AD_SKIP = frozenset(_UI_PROPS | {"action_tweak_storage", "action_slot_handle_tweak_storage", "use_tweak_mode",
                                  "use_pin", "action_slot_handle", "last_slot_identifier", "action_slot"})
_KEY_2 = ("co", "handle_left", "handle_right")
_KEY_INT = ("interpolation", "type", "handle_left_type", "handle_right_type", "easing")
_KEY_1 = ("back", "amplitude", "period")
_ACT_CACHE: dict = {}          # action session_uid -> {slot identifier: record}
_OWN_PATHS = ("hindsight.",)   # Hindsight's own animated properties (Movie Mode keys the scene's step)
_SLOT_TYPES = {"OB": "OBJECT", "ME": "MESH", "MA": "MATERIAL", "WO": "WORLD", "SC": "SCENE", "CA": "CAMERA",
               "LA": "LIGHT", "KE": "KEY", "NT": "NODETREE", "CU": "CURVE", "AR": "ARMATURE"}


_TARGET_SKIP = frozenset(_UI_PROPS | {"id_type"})
_STRIP_SKIP = frozenset(_UI_PROPS | {"select", "frame_start_raw", "frame_end_raw", "frame_start_ui", "frame_end_ui",
                                     "strip_time", "action_slot_handle", "last_slot_identifier", "action_slot",
                                     "name"})


def _keys_of(fc) -> dict:
    kp = fc.keyframe_points
    n = len(kp)
    r = {}
    for f in _KEY_2:
        buf = [0.0] * (2 * n)
        kp.foreach_get(f, buf)
        r[f] = buf
    for f in _KEY_INT:
        buf = [0] * n
        kp.foreach_get(f, buf)
        r[f] = buf
    for f in _KEY_1:
        buf = [0.0] * n
        kp.foreach_get(f, buf)
        r[f] = buf
    return r


def _set_keys(fc, r) -> None:
    kp = fc.keyframe_points
    n = len(r.get("interpolation") or ())
    if len(kp) != n:
        kp.clear()
        kp.add(n)
    if n:
        for f in _KEY_2 + _KEY_INT + _KEY_1:
            kp.foreach_set(f, r[f])
    fc.update()


def _driver_values(fc) -> dict:
    d = fc.driver
    out = {"path": fc.data_path, "index": int(fc.array_index), "mute": bool(fc.mute),
           "type": d.type, "expression": d.expression, "use_self": bool(d.use_self), "keys": _keys_of(fc)}
    out["vars"] = [{"name": v.name, "type": v.type,
                    "targets": [dict(struct_values(t, _TARGET_SKIP), id_type=t.id_type) for t in v.targets]}
                   for v in d.variables]
    return out


def _nla_values(ad) -> list:
    out = []
    for t in ad.nla_tracks:
        strips = []
        for st in t.strips:
            v = struct_values(st, _STRIP_SKIP)
            slot = getattr(st, "action_slot", None)
            v["slot"] = slot.identifier if slot is not None else None
            strips.append([st.name, v])
        out.append({"name": t.name, "mute": bool(t.mute), "is_solo": bool(t.is_solo), "lock": bool(t.lock),
                    "strips": strips})
    return out


def anim_values(ob):
    """How the object plays its action, its drivers and its NLA tracks, or None when it has no
    animation data."""
    ad = ob.animation_data
    if ad is None:
        return None
    v = struct_values(ad, _AD_SKIP)
    slot = getattr(ad, "action_slot", None)
    v["slot"] = slot.identifier if slot is not None else None
    if len(ad.drivers):
        v["drivers"] = [_driver_values(fc) for fc in ad.drivers]
    if len(ad.nla_tracks):
        v["nla"] = _nla_values(ad)
    return v


def _write_drivers(ob, ad, want: list) -> None:
    if [(_d["path"], _d["index"]) for _d in want] != [(f.data_path, f.array_index) for f in ad.drivers]:
        for fc in list(ad.drivers):
            ad.drivers.remove(fc)
        for w in want:
            try:
                fc = ob.driver_add(w["path"], w["index"]) if w["index"] >= 0 else ob.driver_add(w["path"])
            except Exception:
                try:
                    fc = ob.driver_add(w["path"])
                except Exception as e:
                    log.error_once(("driver_add", str(e)), f"could not restore a driver: {e}", e)
                    continue
            if isinstance(fc, list):
                fc = fc[0]
    by = {(f.data_path, f.array_index): f for f in ad.drivers}
    for w in want:
        fc = by.get((w["path"], w["index"]))
        if fc is None:
            continue
        cur = _driver_values(fc)
        if cur == w:
            continue
        d = fc.driver
        fc.mute = w.get("mute", False)
        d.type = w["type"]
        d.expression = w.get("expression", "")
        d.use_self = w.get("use_self", False)
        while len(d.variables):
            d.variables.remove(d.variables[0])
        for vw in w.get("vars") or ():
            v = d.variables.new()
            v.name = vw["name"]
            v.type = vw["type"]
            for t, tw in zip(v.targets, vw.get("targets") or ()):
                try:
                    if t.id_type != tw.get("id_type", t.id_type):
                        t.id_type = tw["id_type"]
                except Exception:
                    pass
                set_struct_values(t, {k: x for k, x in tw.items() if k != "id_type"})
        _set_keys(fc, w.get("keys") or {})


def _write_nla(ad, want: list) -> None:
    shape = [(t["name"], [n for n, _v in t["strips"]]) for t in want]
    if shape != [(t.name, [s.name for s in t.strips]) for t in ad.nla_tracks]:
        for t in list(ad.nla_tracks):
            ad.nla_tracks.remove(t)
        for tw in want:
            t = ad.nla_tracks.new()
            t.name = tw["name"]
            for name, sv in tw["strips"]:
                ref = (sv.get("action") or {}).get("ref")
                act = idmap.find(ref) if ref else None
                if act is None:
                    continue
                try:
                    st = t.strips.new(name, int(sv.get("frame_start", 1)), act)
                except Exception as e:
                    log.error_once(("nla_strip", str(e)), f"could not restore an NLA strip: {e}", e)
    for t, tw in zip(ad.nla_tracks, want):
        t.mute, t.lock = tw.get("mute", False), tw.get("lock", False)
        if t.is_solo != tw.get("is_solo", False):
            t.is_solo = tw.get("is_solo", False)
        for st, (name, sv) in zip(t.strips, tw["strips"]):
            set_struct_values(st, {k: x for k, x in sv.items() if k != "slot"})
            ident = sv.get("slot")
            if ident and hasattr(st, "action_slot") and st.action is not None:
                slot = _slot_of(st.action, ident)
                if slot is not None and st.action_slot != slot:
                    st.action_slot = slot


def _slot_of(action, ident):
    if not ident or not hasattr(action, "slots"):
        return None
    return next((x for x in action.slots if x.identifier == ident), None)


def action_record(action, ident) -> dict:
    per = _ACT_CACHE.get(action.session_uid)
    if per is not None and ident in per:
        return per[ident]
    rows = []
    for fc in _fcurves(action, _slot_of(action, ident)):
        if fc.data_path.startswith(_OWN_PATHS):
            continue                    # Hindsight's own keys (Movie Mode's step) are never recorded
        r = {"path": fc.data_path, "index": int(fc.array_index),
             "group": fc.group.name if fc.group is not None else "", "mute": bool(fc.mute),
             "extrapolation": fc.extrapolation, "lock": bool(fc.lock)}
        r.update(_keys_of(fc))
        rows.append(r)
    rec = {"name": action.name, "fcurves": rows}
    _ACT_CACHE.setdefault(action.session_uid, {})[ident] = rec
    return rec


def action_owner_map() -> dict:
    """(action pointer, slot identifier) -> uid of the object that records that action (the first
    recorded object by name that plays it). Kept for one recorder frame."""
    fc = idmap.frame_cache()
    if fc is not None and "act_owner" in fc:
        return fc["act_owner"]
    from . import datarec
    users = []
    for u in datarec.tracked_uids():
        ob = idmap.find(u)
        ad = getattr(ob, "animation_data", None) if isinstance(ob, bpy.types.Object) else None
        if ad is not None and ad.action is not None and ad.action.library is None:
            slot = getattr(ad, "action_slot", None)
            users.append((ob.name, u, ad.action.as_pointer(), slot.identifier if slot is not None else None))
    out = {}
    for _n, u, ap, ident in sorted(users, key=lambda t: t[0]):
        out.setdefault((ap, ident), u)
    if fc is not None:
        fc["act_owner"] = out
    return out


def owned_action(ob) -> dict:
    ad = ob.animation_data
    if ad is None or ad.action is None or ad.action.library is not None:
        return {}
    a = ad.action
    slot = getattr(ad, "action_slot", None)
    ident = slot.identifier if slot is not None else None
    if a.users > 1 + bool(a.use_fake_user):
        if action_owner_map().get((a.as_pointer(), ident)) != idmap.uid_of(ob):
            return {}
    au = idmap.claim(a)
    if not au:
        return {}
    return {ACT_PREFIX + au + "|" + (ident or ""): action_record(a, ident)}


def _fcurve_coll(action, ident, create: bool):
    if hasattr(action, "slots"):                       # Blender 4.4+: layered actions, a slot's channels
        from bpy_extras import anim_utils
        slot = _slot_of(action, ident)
        if slot is None:
            if not create or not ident:
                return None, None
            slot = action.slots.new(id_type=_SLOT_TYPES.get(ident[:2], "OBJECT"), name=ident[2:])
        cb = (anim_utils.action_ensure_channelbag_for_slot(action, slot) if create
              else anim_utils.action_get_channelbag_for_slot(action, slot))
        return (cb.fcurves if cb is not None else None), cb
    return action.fcurves, None


def write_action(key: str, rec) -> None:
    if not isinstance(rec, dict):
        return
    au, _sep, ident = key[len(ACT_PREFIX):].partition("|")
    ident = ident or None
    a = idmap.find(au)
    try:
        if a is None:
            # gone from the file (nothing used it when it was saved): made again under its id
            a = bpy.data.actions.new(rec.get("name") or "Action")
            a[idmap.UID_PROP] = au
            idmap.note(a, au)
        _ACT_CACHE.pop(a.session_uid, None)
        coll, cb = _fcurve_coll(a, ident, create=True)
        if coll is None:
            return
        want = {(r["path"], r["index"]): r for r in rec.get("fcurves") or ()}
        for fc in [f for f in coll if (f.data_path, f.array_index) not in want
                   and not f.data_path.startswith(_OWN_PATHS)]:
            coll.remove(fc)
        for r in rec.get("fcurves") or ():
            fc = coll.find(r["path"], index=r["index"])
            if fc is None:
                if cb is None:
                    fc = coll.new(r["path"], index=r["index"], action_group=r.get("group") or "")
                else:
                    fc = coll.new(r["path"], index=r["index"])
                    if r.get("group"):
                        g = cb.groups.get(r["group"]) or cb.groups.new(r["group"])
                        fc.group = g
            fc.mute = r.get("mute", False)
            fc.lock = r.get("lock", False)
            if fc.extrapolation != r.get("extrapolation", fc.extrapolation):
                fc.extrapolation = r["extrapolation"]
            _set_keys(fc, r)
        _ACT_CACHE.pop(a.session_uid, None)
    except Exception as e:
        log.error_once(("action", str(e)), f"could not restore the animation {rec.get('name')}: {e}", e)


def write_anim(ob, want) -> None:
    try:
        if want is None:
            if ob.animation_data is not None:      # animated later than this: it had none then
                ob.animation_data_clear()
            return
        ad = ob.animation_data or ob.animation_data_create()
        ref = (want.get("action") or {}).get("ref")
        act = idmap.find(ref) if ref else None
        if ad.action != act:
            ad.action = act
        ident = want.get("slot")
        if act is not None and ident and hasattr(ad, "action_slot"):
            slot = _slot_of(act, ident)
            if slot is not None and ad.action_slot != slot:
                ad.action_slot = slot
        set_struct_values(ad, {k: v for k, v in want.items() if k not in ("action", "slot", "drivers", "nla")})
        if want.get("drivers") or len(ad.drivers):
            _write_drivers(ob, ad, want.get("drivers") or [])
        if want.get("nla") or len(ad.nla_tracks):
            _write_nla(ad, want.get("nla") or [])
    except Exception as e:
        log.error_once(("anim", str(e)), f"could not restore which animation {ob.name} plays: {e}", e)


# ----------------------------------------------------------------------------- geometry of other types (D121)
# Curve and surface splines with their points, lattice points and metaball elements: editable in
# Object Mode, so recorded like settings (kept until the depsgraph reports the data changed). Changes
# made in Edit Mode reach the data when Edit Mode ends, and are recorded then.
GEOM = "geom"
_GEOM_CACHE: dict = {}         # data session_uid -> recorded geometry
_SPLINE_SKIP = frozenset(_UI_PROPS | {"hide"})
_BEZ_3 = ("co", "handle_left", "handle_right")
_BEZ_INT = ("handle_left_type", "handle_right_type")
_PT_1 = ("radius", "tilt", "weight_softbody")
_MB_SKIP = frozenset(_UI_PROPS | {"select", "hide"})


def _bulk(coll, field, n, width=1, kind=float):
    buf = [kind(0)] * (n * width)
    if n:
        coll.foreach_get(field, buf)
    return buf


# ---- Grease Pencil (D131): 4.3+ keeps strokes as curves with attributes in drawings; 4.2's legacy
# Grease Pencil keeps layers, frames, strokes and points
_ATTR_KEY = {"FLOAT": ("value", 1), "INT": ("value", 1), "BOOLEAN": ("value", 1), "INT8": ("value", 1),
             "FLOAT_VECTOR": ("vector", 3), "FLOAT2": ("vector", 2), "FLOAT_COLOR": ("color", 4),
             "BYTE_COLOR": ("color", 4), "INT32_2D": ("value", 2), "QUATERNION": ("value", 4),
             "FLOAT4X4": ("value", 16)}
_GPL_SKIP = frozenset(_UI_PROPS | {"name", "info", "select", "parent", "parent_bone", "matrix_inverse",
                                   "line_change", "annotation_opacity", "annotation_hide",
                                   "use_annotation_onion_skinning", "annotation_onion_before_range",
                                   "annotation_onion_after_range", "annotation_onion_before_color",
                                   "annotation_onion_after_color"})
_GPS_SKIP = frozenset(_UI_PROPS | {"select", "select_index"})
_GPP_FIELDS = (("co", 3), ("pressure", 1), ("strength", 1), ("vertex_color", 4), ("uv_factor", 1),
               ("uv_rotation", 1), ("uv_fill", 2))


def _is_legacy_gp(gp) -> bool:
    """4.2's Grease Pencil (layers of GPencilLayer with frames of strokes)."""
    try:
        return gp.bl_rna.properties["layers"].fixed_type.identifier == "GPencilLayer"
    except Exception:
        return False


def _stroke_sizes(d) -> list:
    n = len(d.strokes)
    if not n:
        return []                       # an empty drawing (its offsets can't be read in bulk)
    offs = [0] * (n + 1)
    d.curve_offsets.foreach_get("value", offs)
    return [b - a for a, b in zip(offs, offs[1:])]


def _drawing_record(d) -> dict:
    sizes = _stroke_sizes(d)
    attrs = {}
    for a in d.attributes:
        kw = _ATTR_KEY.get(a.data_type)
        if kw is None or a.name.startswith("."):
            continue
        key, width = kw
        kind = float if a.data_type in ("FLOAT", "FLOAT_VECTOR", "FLOAT2", "FLOAT_COLOR", "BYTE_COLOR",
                                        "QUATERNION", "FLOAT4X4") else int
        attrs[a.name] = [a.domain, a.data_type, _bulk(a.data, key, len(a.data), width, kind)]
    return {"sizes": sizes, "attrs": attrs}


def _write_drawing(d, rec) -> None:
    sizes = rec.get("sizes") or []
    if _stroke_sizes(d) != sizes:
        if len(d.strokes):
            d.remove_strokes()
        if sizes:
            d.add_strokes(sizes)
    want = rec.get("attrs") or {}
    for a in [a for a in d.attributes if a.name not in want and not a.name.startswith(".")]:
        try:
            d.attributes.remove(a)
        except Exception:
            pass
    for name, (domain, dtype, vals) in want.items():
        a = d.attributes.get(name)
        if a is None or a.data_type != dtype or a.domain != domain:
            if a is not None:
                try:
                    d.attributes.remove(a)
                except Exception:
                    pass
            a = d.attributes.new(name, dtype, domain)
        key, width = _ATTR_KEY[dtype]
        if len(a.data) * width == len(vals):
            a.data.foreach_set(key, vals)
    try:
        d.tag_positions_changed()
    except Exception:
        pass


def _gp_record(gp) -> dict:
    layers = []
    if _is_legacy_gp(gp):
        for l in gp.layers:
            frames = []
            for f in l.frames:
                strokes = []
                for st in f.strokes:
                    pts = st.points
                    n = len(pts)
                    r = {"values": struct_values(st, _GPS_SKIP), "n": n}
                    for fld, w in _GPP_FIELDS:
                        r[fld] = _bulk(pts, fld, n, w)
                    strokes.append(r)
                frames.append([int(f.frame_number), {"strokes": strokes}])
            layers.append({"name": l.info, "values": struct_values(l, _GPL_SKIP), "frames": frames})
        return {"legacy": True, "layers": layers}
    for l in gp.layers:
        frames = [[int(f.frame_number), _drawing_record(f.drawing)] for f in l.frames if f.drawing is not None]
        layers.append({"name": l.name, "values": struct_values(l, _GPL_SKIP), "frames": frames})
    return {"legacy": False, "layers": layers}


def _write_gp(gp, rec) -> None:
    want = rec.get("layers") or []
    names = [w["name"] for w in want]
    legacy = _is_legacy_gp(gp)
    lname = (lambda l: l.info) if legacy else (lambda l: l.name)
    for l in [l for l in gp.layers if lname(l) not in names]:
        gp.layers.remove(l)
    for w in want:
        l = next((x for x in gp.layers if lname(x) == w["name"]), None)
        if l is None:
            l = gp.layers.new(w["name"])
        set_struct_values(l, w.get("values") or {})
        wf = {fn: fr for fn, fr in w.get("frames") or ()}
        for f in [f for f in l.frames if int(f.frame_number) not in wf]:
            l.frames.remove(f) if legacy else l.frames.remove(int(f.frame_number))
        for fn, fr in w.get("frames") or ():
            f = next((x for x in l.frames if int(x.frame_number) == fn), None)
            if f is None:
                f = l.frames.new(fn)
            if legacy:
                _write_legacy_frame(f, fr)
            elif f.drawing is not None:
                _write_drawing(f.drawing, fr)


def _write_legacy_frame(f, fr) -> None:
    want = fr.get("strokes") or []
    if [len(st.points) for st in f.strokes] != [w["n"] for w in want]:
        for st in list(f.strokes):          # (legacy strokes have no clear())
            f.strokes.remove(st)
        for w in want:
            st = f.strokes.new()
            if w["n"]:
                st.points.add(w["n"])
    for st, w in zip(f.strokes, want):
        set_struct_values(st, w.get("values") or {})
        for fld, _w in _GPP_FIELDS:
            if fld in w and w["n"]:
                st.points.foreach_set(fld, w[fld])


# ---- hair curves and point clouds (D134): generic attributes
def _attrs_of(owner) -> dict:
    out = {}
    for a in owner.attributes:
        kw = _ATTR_KEY.get(a.data_type)
        if kw is None or a.name.startswith("."):
            continue
        key, width = kw
        kind = float if a.data_type in ("FLOAT", "FLOAT_VECTOR", "FLOAT2", "FLOAT_COLOR", "BYTE_COLOR",
                                        "QUATERNION", "FLOAT4X4") else int
        out[a.name] = [a.domain, a.data_type, _bulk(a.data, key, len(a.data), width, kind)]
    return out


def _write_attrs(owner, want: dict) -> None:
    for a in [a for a in owner.attributes if a.name not in want and not a.name.startswith(".")]:
        try:
            owner.attributes.remove(a)
        except Exception:
            pass
    for name, (domain, dtype, vals) in want.items():
        a = owner.attributes.get(name)
        if a is None or a.data_type != dtype or a.domain != domain:
            if a is not None:
                try:
                    owner.attributes.remove(a)
                except Exception:
                    pass
            a = owner.attributes.new(name, dtype, domain)
        key, width = _ATTR_KEY[dtype]
        if len(a.data) * width == len(vals):
            a.data.foreach_set(key, vals)


def _curve_sizes(c) -> list:
    n = len(c.curves)
    if not n:
        return []
    offs = [0] * (n + 1)
    c.curve_offset_data.foreach_get("value", offs)
    return [b - a for a, b in zip(offs, offs[1:])]


def _write_hair(c, g) -> None:
    sizes = g.get("sizes") or []
    if _curve_sizes(c) != sizes:
        if hasattr(c, "remove_curves"):
            if len(c.curves):
                c.remove_curves()
            if sizes:
                c.add_curves(sizes)
        else:                                    # Blender 4.2 can only add curves
            log.error_once(("hair", "resize"), "hair curves added or removed can't be put back on this Blender version")
            return
    _write_attrs(c, g.get("attrs") or {})
    c.update_tag()


def _write_points(pc, g) -> None:
    n = g.get("n", 0)
    if len(pc.points) != n:
        if hasattr(pc, "resize"):
            pc.resize(n)
        else:
            log.error_once(("points", "resize"), "points added or removed can't be put back on this Blender version")
            return
    _write_attrs(pc, g.get("attrs") or {})
    pc.update_tag()


def _geom_print(d, t):
    """Counts that change when points, strokes or elements are added or removed: some of those calls
    (a point cloud's resize) don't tell the depsgraph, so a kept record is checked against them."""
    if t == "Curves":
        return (len(d.curves), len(d.points), len(d.attributes))
    if t == "PointCloud":
        return (len(d.points), len(d.attributes))
    if t == "Lattice":
        return (len(d.points),)
    if t == "MetaBall":
        return (len(d.elements),)
    if t in ("Curve", "SurfaceCurve"):
        return tuple(len(sp.bezier_points) + len(sp.points) for sp in d.splines)
    return (len(d.layers),)


def geometry_of(d):
    """The geometry of curve, surface, lattice, metaball, Grease Pencil, hair curves and point cloud
    data, or None for other data."""
    t = d.bl_rna.identifier
    if t not in _GEOM_TYPES:
        return None
    k = d.session_uid
    fp = _geom_print(d, t)
    hit = _GEOM_CACHE.get(k)
    if hit is not None and hit[0] == fp:
        return hit[1]
    g = _read_geometry(d, t)
    _GEOM_CACHE[k] = (fp, g)
    return g


_GEOM_TYPES = frozenset({"Curves", "PointCloud", "GreasePencil", "GreasePencilv3", "Curve", "SurfaceCurve",
                         "Lattice", "MetaBall"})


def _read_geometry(d, t):
    if t == "Curves":
        return {"sizes": _curve_sizes(d), "attrs": _attrs_of(d)}
    if t == "PointCloud":
        return {"n": len(d.points), "attrs": _attrs_of(d)}
    if t in ("GreasePencil", "GreasePencilv3"):
        return _gp_record(d)
    if t == "Lattice":
        n = len(d.points)
        g = {"co_deform": _bulk(d.points, "co_deform", n, 3), "weight_softbody": _bulk(d.points, "weight_softbody", n)}
    elif t == "MetaBall":
        g = {"elements": [struct_values(e, _MB_SKIP) for e in d.elements]}
    else:
        rows = []
        for sp in d.splines:
            r = {"values": struct_values(sp, _SPLINE_SKIP)}
            if sp.type == "BEZIER":
                pts = sp.bezier_points
                n = len(pts)
                for f in _BEZ_3:
                    r[f] = _bulk(pts, f, n, 3)
                for f in _BEZ_INT:
                    r[f] = _bulk(pts, f, n, 1, int)
            else:
                pts = sp.points
                n = len(pts)
                r["co"] = _bulk(pts, "co", n, 4)
                r["weight"] = _bulk(pts, "weight", n)
            for f in _PT_1:
                r[f] = _bulk(pts, f, n)
            r["n"] = n
            rows.append(r)
        g = {"splines": rows}
    return g


def write_geometry(d, g) -> None:
    if not isinstance(g, dict):
        return
    _GEOM_CACHE.pop(d.session_uid, None)
    t = d.bl_rna.identifier
    try:
        if t in ("GreasePencil", "GreasePencilv3"):
            _write_gp(d, g)
            _GEOM_CACHE.pop(d.session_uid, None)
            return
        if t == "Curves":
            _write_hair(d, g)
            _GEOM_CACHE.pop(d.session_uid, None)
            return
        if t == "PointCloud":
            _write_points(d, g)
            _GEOM_CACHE.pop(d.session_uid, None)
            return
        if t == "Lattice":
            if len(d.points) * 3 == len(g.get("co_deform") or ()):
                d.points.foreach_set("co_deform", g["co_deform"])
                d.points.foreach_set("weight_softbody", g["weight_softbody"])
                d.update_tag()
        elif t == "MetaBall":
            want = g.get("elements") or []
            if [e.type for e in d.elements] != [w.get("type") for w in want]:
                d.elements.clear()
                for w in want:
                    d.elements.new(type=w.get("type", "BALL"))
            for e, w in zip(d.elements, want):
                set_struct_values(e, w)
        elif t in ("Curve", "SurfaceCurve"):
            want = g.get("splines") or []
            same = [(sp.type, len(sp.bezier_points) if sp.type == "BEZIER" else len(sp.points)) for sp in d.splines] \
                == [(w["values"].get("type"), w["n"]) for w in want]
            if not same:
                d.splines.clear()
                for w in want:
                    sp = d.splines.new(w["values"].get("type", "POLY"))
                    pts = sp.bezier_points if sp.type == "BEZIER" else sp.points
                    if w["n"] > len(pts):
                        pts.add(w["n"] - len(pts))
            for sp, w in zip(d.splines, want):
                set_struct_values(sp, {k: x for k, x in w["values"].items() if k != "type"})
                if sp.type == "BEZIER":
                    pts = sp.bezier_points
                    for f in _BEZ_INT:          # handle types first: setting one moves the handles
                        pts.foreach_set(f, w[f])
                    for f in _BEZ_3:
                        pts.foreach_set(f, w[f])
                else:
                    pts = sp.points
                    pts.foreach_set("co", w["co"])
                    pts.foreach_set("weight", w["weight"])
                for f in _PT_1:
                    pts.foreach_set(f, w[f])
            d.update_tag()
    except Exception as e:
        log.error_once(("geometry", str(e)), f"could not restore the shape of {d.name}: {e}", e)


SK_PREFIX = "sk."               # the shape keys' own (their animation)
MATA_PREFIX = "@mata:"          # a recorded material's node tree animation: "@mata:<material id>|<key>"


_NO_ANIM = {"anim": None}
_NO_SLOTS: dict = {}            # active index -> [index], the slots value of an object without slots


def anim_of(x) -> dict:
    """{ANIM_KEY: how x plays its animation} and, when x is the only user of its action, the action
    (D120 for data other than objects)."""
    if x.animation_data is None:
        return _NO_ANIM
    out = {ANIM_KEY: anim_values(x)}
    ad = x.animation_data
    if ad is not None and ad.action is not None and ad.action.library is None:
        a = ad.action
        if a.users <= 1 + bool(a.use_fake_user):
            slot = getattr(ad, "action_slot", None)
            ident = slot.identifier if slot is not None else None
            au = idmap.claim(a)
            if au:
                out[ACT_PREFIX + au + "|" + (ident or "")] = action_record(a, ident)
    return out


def write_anim_of(x, vals: dict) -> None:
    for path, v in vals.items():
        if path.startswith(ACT_PREFIX) and v is not MISSING:
            write_action(path, v)
    if ANIM_KEY in vals and vals[ANIM_KEY] is not MISSING:
        write_anim(x, vals[ANIM_KEY])


def assignment_path(path: str) -> bool:
    """A recorded "which animation it plays" value (an object's, its data's, its shape keys', a
    material's node tree's)."""
    return path == ANIM_KEY or path.endswith(("." + ANIM_KEY, "|" + ANIM_KEY))


def owned_path(adapter_id: str, path: str) -> bool:
    """A value an object records only while it owns the data (D112, D114)."""
    if adapter_id == "scene":
        # (the scene's loose node groups are not: they coming and going is a step, D152, like D151's data)
        return path in ("settings", COMP_TREE) or path.startswith((WORLD_PREFIX, WORLDP_PREFIX))
    return adapter_id == "object" and path.startswith((DATA_PREFIX, MAT_PREFIX, MATP_PREFIX, ACT_PREFIX,
                                                       SK_PREFIX, MATA_PREFIX, NG_PREFIX, NGA_PREFIX, PART_PREFIX))


def _owns_data(ob, d) -> bool:
    """The object that records its data's properties: the only user, or for a shared mesh the object
    that records the mesh (D39)."""
    if d.users <= 1 + bool(getattr(d, "use_fake_user", False)):
        return True
    if ob.type != "MESH":
        return False
    from . import datarec
    return datarec._MESH_OWNER.get(d.as_pointer()) == idmap.uid_of(ob)


def _own_prop(k: str) -> bool:
    """Hindsight's own properties (ids, the camera rig mark, its scene settings) are never part of
    what is recorded."""
    return k == "hindsight" or k.startswith("_hindsight") or k.startswith("hindsight_")


def _sys_group(idb, create: bool = False):
    """Blender 5: the group add-ons' properties are stored in, or None (none yet, or Blender 4)."""
    get = getattr(idb, "bl_system_properties_get", None)
    if get is None:
        return None
    try:
        pg = get(do_create=create)
        return pg.id_properties_ensure() if pg is not None else None
    except Exception:
        return None


def _idp_json(v):
    """An ID property value (from to_dict) as a JSON value: a datablock becomes {REF_KEY: its id}. An
    empty group is left out: it reads as the defaults, like no group (Blender 4 makes one when a panel
    only reads an add-on's settings, which isn't a change)."""
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            x = _idp_json(x)
            if not (isinstance(x, dict) and not x):
                out[k] = x
        return out
    if isinstance(v, list):
        return [_idp_json(x) for x in v]
    if isinstance(v, bpy.types.ID):
        return {REF_KEY: idmap.ref_of(v)}
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return normalise(v)


def _idp_py(v, cur=MISSING):
    """What _idp_json gave, ready to store again; ANIMATED parts take the value stored now (cur)."""
    if v == ANIMATED:
        return cur
    if isinstance(v, dict):
        if set(v) == {REF_KEY}:
            return idmap.find(v[REF_KEY]) if v[REF_KEY] else None
        c = cur if isinstance(cur, dict) else {}
        return {k: _idp_py(x, c.get(k, MISSING)) for k, x in v.items()
                if not (x == ANIMATED and c.get(k, MISSING) is MISSING)}
    if isinstance(v, list):
        c = cur if isinstance(cur, list) and len(cur) == len(v) else [MISSING] * len(v)
        return [_idp_py(x, y) for x, y in zip(v, c)]
    return v


def _group_json(g) -> dict:
    out = {}
    if g is None:
        return out
    try:
        keys = list(g.keys())
    except Exception:
        return out
    for k in keys:
        if _own_prop(k):
            continue
        try:
            v = g[k]
            v = _idp_json(v.to_dict() if hasattr(v, "to_dict") else
                          v.to_list() if hasattr(v, "to_list") else v)
        except Exception:
            continue
        if not (isinstance(v, dict) and not v):
            out[k] = v
    return out


def _mark_animated(d: dict, anim) -> None:
    """Paths an animation or driver sets ('["prop"]', 'addon.value') become ANIMATED in d."""
    for p in anim:
        if p.startswith('["'):
            k = p[2:p.find('"]', 2)]
            rest = p[p.find('"]', 2) + 2:]
        else:
            head = p.split(".", 1)
            k, rest = head[0], ("." + head[1] if len(head) > 1 else "")
            k = k.split("[", 1)[0]
        if k not in d:
            continue
        node = d
        key = k
        ok = True
        for part in [x for x in rest.split(".") if x]:
            if "[" in part or not isinstance(node.get(key), dict):
                ok = False
                break
            node, key = node[key], part
        if ok and isinstance(node, dict) and key in node:
            node[key] = ANIMATED
        else:
            d[k] = ANIMATED


_NONE = {}                       # a shared empty group: never changed, so a value that is the same
                                 # object on both sides of a compare is skipped (datarec._same_values)
_ID_EMPTY4 = {IDPROPS_KEY: _NONE}
_ID_EMPTY5 = {IDPROPS_KEY: _NONE, SYSPROPS_KEY: _NONE}


def id_props(idb, anim=None) -> dict:
    """{IDPROPS_KEY: the datablock's own ID properties, SYSPROPS_KEY: Blender 5's add-on group}. Both
    are always there (empty when nothing is set): a value that disappears would read as a kind that
    isn't recorded, not as properties taken off."""
    own = _group_json(idb) or _NONE
    five = hasattr(idb, "bl_system_properties_get")
    sysd = (_group_json(_sys_group(idb)) or _NONE) if five else None
    if own is _NONE and not sysd:
        return _ID_EMPTY5 if five else _ID_EMPTY4
    out = {IDPROPS_KEY: own}
    if five:
        out[SYSPROPS_KEY] = sysd
    if anim:
        if own is not _NONE:
            _mark_animated(own, anim)
        if sysd:
            _mark_animated(sysd, anim)
    return out


def _write_group(g, want: dict) -> None:
    if g is None:
        return
    want = want or {}
    for k in [k for k in g.keys() if not _own_prop(k) and k not in want]:
        try:
            del g[k]                     # not set then: back to the add-on's default
        except Exception:
            pass
    for k, v in want.items():
        if _own_prop(k):
            continue
        try:
            cur = g[k] if k in g else MISSING
            curv = cur.to_dict() if hasattr(cur, "to_dict") else cur.to_list() if hasattr(cur, "to_list") else cur
            if cur is not MISSING and _idp_json(curv) == v:
                continue
            val = _idp_py(v, curv)
            if val is MISSING:
                continue
            if isinstance(v, dict) and set(v) == {REF_KEY} and val is None and v[REF_KEY]:
                continue                 # the datablock it points to is deleted now: left as it is
            if k in g:
                del g[k]                 # Blender won't store a dict over an existing group
            g[k] = val
        except Exception as e:
            log.error_once(("idprop", k, str(e)), f"could not put back the property {k}: {e}", e)


def write_id_props(idb, values: dict) -> None:
    if IDPROPS_KEY in values and values[IDPROPS_KEY] is not MISSING:
        _write_group(idb, values[IDPROPS_KEY])
    elif IDPROPS_KEY in values:          # recorded as none: remove what is set now
        _write_group(idb, {})
    if SYSPROPS_KEY in values:
        want = values[SYSPROPS_KEY]
        g = _sys_group(idb, create=want is not MISSING and bool(want))
        _write_group(g, {} if want is MISSING else want)


def _gn_inputs(m):
    """A Geometry Nodes modifier's input values on Blender 5.2 and later, where they are RNA
    (modifier.properties.inputs.<identifier>: value, type, attribute_name) instead of custom properties.
    None where the modifier keeps them as custom properties (read by _id_props)."""
    props = getattr(m, "properties", None)
    inputs = getattr(props, "inputs", None) if props is not None else None
    ng = getattr(m, "node_group", None)
    if inputs is None or ng is None:
        return None
    out = {}
    for it in ng.interface.items_tree:
        if getattr(it, "item_type", "SOCKET") != "SOCKET" or getattr(it, "in_out", "") != "INPUT":
            continue
        x = getattr(inputs, it.identifier, None)
        if x is None:
            continue
        rec = {}
        for a in ("type", "attribute_name", "value"):
            if not hasattr(x, a):
                continue
            try:
                v = getattr(x, a)
            except Exception:
                continue
            if isinstance(v, bpy.types.ID):
                rec[a] = {"ref": idmap.ref_of(v)}
                continue
            if v is None:
                rec[a] = None
                continue
            try:
                v = normalise(v)
            except Exception:
                continue
            if isinstance(v, (int, float, str, list, bool)):
                rec[a] = v
        out[it.identifier] = rec
    return out


def _set_gn_inputs(m, recs) -> None:
    props = getattr(m, "properties", None)
    inputs = getattr(props, "inputs", None) if props is not None else None
    if inputs is None or not isinstance(recs, dict):
        return
    for ident, rec in recs.items():
        x = getattr(inputs, ident, None)
        if x is None or not isinstance(rec, dict):
            continue
        for a in ("type", "attribute_name", "value"):      # the type first: it decides what is used
            if a not in rec:
                continue
            v = rec[a]
            if isinstance(v, dict) and "ref" in v:
                v = idmap.find(v["ref"]) if v["ref"] else None
            try:
                setattr(x, a, to_rna(v) if not isinstance(v, bpy.types.ID) else v)
            except Exception as e:
                log.error_once(("gn_input", ident, a, str(e)), f"could not restore input {ident}.{a}: {e}", e)


REF_KEY = "__hindsight_ref__"   # a custom property that points to a datablock, recorded by its id


def _set_id_props(owner, props: dict) -> None:
    for k, v in (props or {}).items():
        if isinstance(v, dict) and set(v) == {REF_KEY}:
            v = idmap.find(v[REF_KEY]) if v[REF_KEY] else None
            if v is None:
                continue                              # deleted now: left unset, like other references
        try:
            owner[k] = v
        except Exception:
            pass


def _hidden(ob) -> dict:
    """Hidden (H) in each view layer of the scenes it is in."""
    out = {}
    for sc in bpy.data.scenes:
        for vl in sc.view_layers:
            try:
                out[f"{sc.name}/{vl.name}"] = bool(ob.hide_get(view_layer=vl))
            except Exception:
                continue
    return out


def _set_hidden(ob, hidden) -> None:
    """Put back what _hidden read. A single True/False (recorded before D56) is for the view layer the
    window shows."""
    if isinstance(hidden, bool):
        try:
            ob.hide_set(hidden)
        except Exception:
            pass
        return
    for key, h in (hidden or {}).items():
        sc_name, _sep, vl_name = key.partition("/")
        sc = bpy.data.scenes.get(sc_name)
        vl = sc.view_layers.get(vl_name) if sc is not None else None
        if vl is not None:
            try:
                if ob.hide_get(view_layer=vl) != bool(h):
                    ob.hide_set(bool(h), view_layer=vl)
            except Exception:
                pass


# Settings a physics modifier keeps in structs of its own (struct_values leaves nested structs out).
_MOD_STRUCTS = {"CLOTH": ("settings", "collision_settings"), "SOFT_BODY": ("settings",),
                "FLUID": ("domain_settings", "flow_settings", "effector_settings")}


def _nested_values(m) -> dict:
    out = {}
    for name in _MOD_STRUCTS.get(m.type, ()):
        st = getattr(m, name, None)
        if st is not None:
            try:
                out[name] = struct_values(st)
            except Exception:
                continue
    return out


# ----------------------------------------------------------------------------- particle settings (D137)
PART_PREFIX = "@part:"
_PART_SKIP = frozenset(_UI_PROPS | _ID_SKIP | {"active_texture_index", "active_instanceweight_index"})


def particle_owner_map() -> dict:
    """particle settings pointer -> uid of the object that records them (the first recorded object
    by name with a particle system using them). Kept for one recorder frame."""
    fc = idmap.frame_cache()
    if fc is not None and "part_owner" in fc:
        return fc["part_owner"]
    from . import datarec
    users = []
    for u in datarec.tracked_uids():
        ob = idmap.find(u)
        if isinstance(ob, bpy.types.Object) and len(getattr(ob, "particle_systems", ())):
            users.append((ob.name, u, ob))
    out = {}
    for _n, u, ob in sorted(users, key=lambda t: t[0]):
        for ps in ob.particle_systems:
            if ps.settings is not None:
                out.setdefault(ps.settings.as_pointer(), u)
    if fc is not None:
        fc["part_owner"] = out
    return out


def owned_particles(ob) -> dict:
    systems = getattr(ob, "particle_systems", None)
    if not systems or not len(systems):
        return {}
    uid = idmap.uid_of(ob)
    out = {}
    owners = None
    for ps in systems:
        st = ps.settings
        if st is None or st.library is not None:
            continue
        if st.users > 1 + bool(st.use_fake_user):
            if owners is None:
                owners = particle_owner_map()
            if owners.get(st.as_pointer()) != uid:
                continue
        su = idmap.claim(st)
        if su:
            out[PART_PREFIX + su] = settings_of(st, _PART_SKIP)
    return out


def _particles(m):
    """A particle system modifier: its settings datablock (by id, and its values in case it is gone
    when the object comes back) and its seed. Baked particles are not part of it."""
    ps = getattr(m, "particle_system", None) if m.type == "PARTICLE_SYSTEM" else None
    if ps is None or ps.settings is None:
        return None
    return {"seed": int(ps.seed), "settings": idmap.claim(ps.settings),
            "values": struct_values(ps.settings)}


def _physics(ob) -> dict:
    """Physics settings that live on the object: collision, force field, rigid body."""
    out = {}
    for name, used in (("collision", any(m.type == "COLLISION" for m in ob.modifiers)),
                       ("field", getattr(getattr(ob, "field", None), "type", "NONE") != "NONE"),
                       ("rigid_body", getattr(ob, "rigid_body", None) is not None)):
        st = getattr(ob, name, None)
        if used and st is not None:
            try:
                out[name] = struct_values(st)
            except Exception:
                continue
    return out


def _set_physics_modifier(ob, m, w) -> None:
    for name, vals in (w.get("nested") or {}).items():
        st = getattr(m, name, None)
        if st is not None:
            set_struct_values(st, vals, ob, f'modifiers["{m.name}"].{name}.')
    pr = w.get("particles")
    ps = getattr(m, "particle_system", None)
    if isinstance(pr, dict) and ps is not None:
        made = ps.settings
        want = idmap.find(pr["settings"]) if pr.get("settings") else None
        if want is not None and want != made:
            ps.settings = want
            if made is not None and made.users == 0:
                bpy.data.particles.remove(made)
        elif want is None and ps.settings is not None:
            set_struct_values(ps.settings, pr.get("values") or {})
        ps.seed = int(pr.get("seed", ps.seed))


def _set_physics(ob, phys) -> None:
    if not isinstance(phys, dict):
        return
    if phys.get("collision") and getattr(ob, "collision", None) is not None:
        set_struct_values(ob.collision, phys["collision"])
    f = phys.get("field")
    if f and getattr(ob, "field", None) is not None:
        if "type" in f:                           # the type first: it decides which settings exist
            set_struct_values(ob.field, {"type": f["type"]})
        set_struct_values(ob.field, f)
    rb = phys.get("rigid_body")
    if rb:
        try:
            if ob.rigid_body is None:
                with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
                    bpy.ops.rigidbody.object_add()
            if ob.rigid_body is not None:
                if "type" in rb:
                    set_struct_values(ob.rigid_body, {"type": rb["type"]})
                set_struct_values(ob.rigid_body, rb)
        except Exception as e:
            log.error_once(("rigid_body", str(e)), f"could not bring back a rigid body: {e}", e)


def object_body(ob) -> dict:
    """What it takes to bring a deleted object back exactly: its settings as values (transforms with
    their rotation mode, deltas, visibility, display, ...), its constraints and modifiers with their
    settings, its custom properties, material slots and hidden state (D49). Its mesh comes from the
    mesh datablock if it still exists, otherwise from the snapshot stored with the step."""
    data = ob.data
    return {
        "name": ob.name, "type": ob.type,
        "data": idmap.claim(data) if data is not None else None,
        "data_name": data.name if data is not None else None,
        "matrix": [list(map(float, r)) for r in ob.matrix_basis],
        "parent": idmap.ref_of(ob.parent) if ob.parent is not None else None,
        "parent_inverse": [list(map(float, r)) for r in ob.matrix_parent_inverse],
        "collections": sorted(_coll_ref(c) for c in users_collection(ob)),
        "materials": [idmap.claim(s.material) if s.material is not None else None for s in ob.material_slots],
        "hide": [bool(ob.hide_viewport), bool(ob.hide_render), bool(ob.hide_select)],
        "props": struct_values(ob, _BODY_SKIP),
        "constraints": [{"type": c.type, "name": c.name, "values": struct_values(c)} for c in ob.constraints],
        "modifiers": [{"type": m.type, "name": m.name, "values": struct_values(m), "custom": _id_props(m),
                       "gn": _gn_inputs(m), "nested": _nested_values(m), "particles": _particles(m)}
                      for m in ob.modifiers],
        "physics": _physics(ob),
        "slots": [[s.link, idmap.ref_of(s.material) if s.material is not None else None] for s in ob.material_slots],
        "hidden": _hidden(ob),
        "custom": _group_json(ob),              # D114: nested groups and their pointers too
        "sys": _group_json(_sys_group(ob)),     # D114: Blender 5 keeps add-ons' data apart
        **_data_body(data),                     # D122: other data, to make it again when it's gone
        **_anim_body(ob),                       # D135: its animation, and its action if it's the only user
    }


def _anim_body(ob) -> dict:
    if ob.animation_data is None:
        return {}
    r = _part("the animation", anim_of, ob)
    return {"anim": r} if r is not _FAILED else {}


# data types a deleted object's data can be made again from (D122): bl_rna type -> how to make it
_REMAKE = {"Camera": lambda n, v: bpy.data.cameras.new(n),
           "Speaker": lambda n, v: bpy.data.speakers.new(n),
           "Lattice": lambda n, v: bpy.data.lattices.new(n),
           "MetaBall": lambda n, v: bpy.data.metaballs.new(n),
           "Volume": lambda n, v: bpy.data.volumes.new(n),
           "Curve": lambda n, v: bpy.data.curves.new(n, type="CURVE"),
           "SurfaceCurve": lambda n, v: bpy.data.curves.new(n, type="SURFACE"),
           "TextCurve": lambda n, v: bpy.data.curves.new(n, type="FONT")}
for _lt in ("PointLight", "SunLight", "SpotLight", "AreaLight"):
    _REMAKE[_lt] = (lambda t: lambda n, v: bpy.data.lights.new(n, type=t))(_lt[:-5].upper())
OBJECT_TYPES_REMADE = frozenset({"MESH", "EMPTY", "CAMERA", "SPEAKER", "LATTICE", "META", "CURVE", "SURFACE",
                                 "FONT", "LIGHT", "VOLUME"})


def _data_body(d) -> dict:
    if d is None or d.library is not None or d.bl_rna.identifier not in _REMAKE:
        return {}
    t = d.bl_rna.identifier
    out = {"data_type": t, "data_values": settings_of(d, _DATA_SKIP, DATA_STRUCTS.get(t) or ()),
           "data_props": id_props(d)}
    geo = geometry_of(d)
    if geo is not None:
        out["data_geom"] = geo
    return out


def _remake_data(body):
    t = body.get("data_type")
    make = _REMAKE.get(t)
    if make is None:
        return None
    vals = body.get("data_values") or {}
    d = make(body.get("data_name") or body.get("name") or "Data", vals)
    if body.get("data"):
        d[idmap.UID_PROP] = body["data"]
        idmap.note(d, body["data"])
    write_settings(d, vals, DATA_STRUCTS.get(t) or ())
    write_id_props(d, body.get("data_props") or {})
    if body.get("data_geom"):
        write_geometry(d, body["data_geom"])
    return d


class SceneAdapter(Adapter):
    """Which objects exist in the scene (adding, deleting, duplicating objects). Bodies and mesh
    snapshots of the objects that come or go are stored with the step by datarec."""
    id = "scene"
    id_types = ("Scene",)

    def capture(self, idb) -> dict:
        out = {}
        if kind_on("scene_collections"):
            out["collections"] = collection_tree(idb)
            out[COLL_SETTINGS] = collection_settings(idb)       # D127
        if kind_on("objects"):
            # linked objects are left out: bringing one back would make a local copy of it. Hindsight's
            # own Movie Mode objects (rigmark) are left out and never get an id
            out["objects"] = sorted(u for u in (idmap.claim(o) for o in idb.objects
                                                if o.library is None and not rigmark.is_rig(o)) if u)
        out.update(id_props(idb, animated_paths(idb) if idb.animation_data is not None else None))  # D114
        if not _movie_on(idb):
            # D116: while Movie Mode is on it sets the camera and the frame range itself; the
            # settings aren't read then (their coming and going is no step, datarec._owner_moves)
            out["settings"] = scene_settings(idb)
            out.update(owned_world(idb))
            _upd(out, "node groups", scene_groups, idb)          # D133
            _upd(out, "data no object uses", scene_loose, idb)   # D151
        return out

    def write(self, idb, values: dict, recorded=None) -> None:
        write_id_props(idb, values)             # D114: custom properties and add-on data
        for path, v in values.items():          # D116: the world before the settings that point at it
            if path.startswith(WORLD_PREFIX) and isinstance(v, dict):
                write_material(path[len(WORLD_PREFIX):], v)
            elif path.startswith(WORLDP_PREFIX) and isinstance(v, dict):
                w = idmap.find(path[len(WORLDP_PREFIX):])
                if w is not None:
                    write_id_props(w, v)
                    if isinstance(v.get("anim"), dict):
                        write_anim_of(w, v["anim"])
                    if isinstance(v.get("tree_anim"), dict) and getattr(w, "node_tree", None) is not None:
                        write_anim_of(w.node_tree, v["tree_anim"])
        _make_loose_again(values, recorded)       # D152: what was deleted since is made again first
        for path, v in values.items():          # D133: groups no object uses
            if path.startswith(NG_PREFIX) and isinstance(v, dict):
                write_group(path[len(NG_PREFIX):], v)
        for path, v in values.items():          # D151: data no object or scene uses
            if path.startswith(LOOSE_PREFIXES):
                write_loose(path, v)
        for path, v in values.items():          # D146: and their animation
            if path.startswith(NGA_PREFIX) and isinstance(v, dict):
                ng = idmap.find(path[len(NGA_PREFIX):])
                if ng is not None:
                    write_anim_of(ng, v)
        _queue_loose_removals(idb, values)        # D152: what was made since goes after the jump
        if COMP_TREE in values and isinstance(values[COMP_TREE], dict) and getattr(idb, "node_tree", None) is not None:
            try:
                write_tree(idb.node_tree, values[COMP_TREE])
            except Exception as e:
                log.error_once(("comp_tree", str(e)), f"could not restore the compositor: {e}", e)
        if "settings" in values and values["settings"] is not MISSING and not _movie_playing():
            write_scene_settings(idb, values["settings"])
        if "collections" in values and values["collections"] is not MISSING:
            _write_collection_tree(idb, values["collections"])
        if COLL_SETTINGS in values and values[COLL_SETTINGS] is not MISSING:
            write_collection_settings(idb, values[COLL_SETTINGS])
        if "objects" not in values or values["objects"] is MISSING or recorded is None:
            return
        want = set(values["objects"])
        have = {idmap.uid_of(o): o for o in idb.objects if not rigmark.is_rig(o)}   # never removes those
        for uid in sorted(set(have) - want):
            _remove_object(have[uid])
        made = []
        for uid in sorted(want - set(have)):
            ob = _recreate_object(idb, uid, recorded(f"body:{uid}"), recorded(f"snapshot:{uid}"))
            if ob is not None:
                made.append((ob, recorded(f"body:{uid}")))
        for ob, body in made:                       # parents after every object exists
            if isinstance(body, dict) and body.get("parent"):
                par = idmap.find(body["parent"])
                if par is not None:
                    ob.parent = par
                    ob.matrix_parent_inverse = to_rna(body["parent_inverse"])


# ----------------------------------------------------------------------------- scene settings (D116)
WORLD_PREFIX = "world:"
_SCENE_SKIP = frozenset(_UI_PROPS | {
    "name", "use_fake_user", "use_extra_user", "is_runtime_data", "tag", "asset_data", "annotation",
    "frame_current", "frame_subframe", "frame_float", "time_jump_unit", "time_jump_delta",
    "lock_frame_selection_to_range", "use_preview_range", "frame_preview_start", "frame_preview_end",
    "show_subframe", "show_keys_from_selected_only", "wrap_timeline_navigation", "use_nodes"})
# the scene's settings structs that are part of the project (tool settings, the 3D cursor and the
# timeline's view aren't: changing a brush or the current frame is never a step)
SCENE_STRUCTS = ("render", "render.image_settings", "render.ffmpeg", "unit_settings", "display_settings",
                 "view_settings", "eevee", "display", "grease_pencil_settings")


def _scene_struct(sc, path):
    st = sc
    for part in path.split("."):
        st = getattr(st, part, None)
        if st is None:
            return None
    return st


def _movie_playing() -> bool:
    """Movie Mode is moving the history for a frame: render, output and frame range settings must
    stay as they are while it renders."""
    try:
        from . import runtime
        return bool(getattr(runtime.goto, "_movie", False))
    except Exception:
        return False


def _movie_on(sc) -> bool:
    p = getattr(sc, "hindsight", None)
    return bool(p is not None and getattr(p, "movie_mode", False))


def scene_settings(sc) -> dict:
    """The scene's own settings and its settings structs (render, output, units, colour management,
    EEVEE, Workbench), as {"scene": values, path: values, ...}."""
    vals = struct_values(sc, _SCENE_SKIP)
    anim = animated_paths(sc) if sc.animation_data is not None else ()
    for p in anim:                      # (a Scene has no path_from_id: struct_values can't mark these)
        if p in vals:
            vals[p] = ANIMATED
    out = {"scene": vals}
    for path in SCENE_STRUCTS:
        st = _scene_struct(sc, path)
        if st is not None:
            out[path] = struct_values(st, anim=anim or None)
    out["anim"] = anim_of(sc)           # D125: the scene's own animation (frame range keys, ...)
    vls = {}
    for vl in sc.view_layers:           # D128: passes, overrides, filters, add-ons' view layer data
        r = {"values": struct_values(vl, _VL_SKIP)}
        ee = getattr(vl, "eevee", None)
        if ee is not None:
            r["eevee"] = struct_values(ee)
        r.update(id_props(vl))
        vls[vl.name] = r
    out["view_layers"] = vls
    return out


_VL_SKIP = frozenset(_UI_PROPS | {"name", "active_aov_index", "active_lightgroup_index"})


def write_scene_settings(sc, rec) -> None:
    if not isinstance(rec, dict):
        return
    try:
        set_struct_values(sc, rec.get("scene") or {})
        for path in SCENE_STRUCTS:
            st = _scene_struct(sc, path)
            if st is not None and path in rec:
                set_struct_values(st, rec[path] or {})
        if isinstance(rec.get("anim"), dict):
            write_anim_of(sc, rec["anim"])
        for vl in sc.view_layers:
            r = (rec.get("view_layers") or {}).get(vl.name)
            if not isinstance(r, dict):
                continue
            set_struct_values(vl, r.get("values") or {})
            if r.get("eevee") and getattr(vl, "eevee", None) is not None:
                set_struct_values(vl.eevee, r["eevee"])
            write_id_props(vl, r)
    except Exception as e:
        log.error_once(("scene_settings", str(e)), f"could not restore the scene settings: {e}", e)


def owned_world(sc) -> dict:
    """{WORLD_PREFIX + id: record} for the scene's world when this scene records it (the first
    recorded scene by name that uses it). Kept like a material's record (D112)."""
    w = sc.world
    if w is None or w.library is not None:
        return {}
    from . import datarec
    users = sorted(x.name for x in bpy.data.scenes if x.world == w and datarec.is_tracked(x))
    if users and users[0] != sc.name:
        return {}
    wu = idmap.claim(w)
    if not wu:
        return {}
    out = {WORLD_PREFIX + wu: material_record(w, idmap.uid_of(sc))}
    p = dict(id_props(w, animated_paths(w) if w.animation_data is not None else None))   # (a copy: id_props
    p["anim"] = anim_of(w)                                           # D125     can give a shared constant)
    if getattr(w, "node_tree", None) is not None:
        p["tree_anim"] = anim_of(w.node_tree)
    out[WORLDP_PREFIX + wu] = p
    return out


WORLDP_PREFIX = "worldp:"
COMP_TREE = "comp_tree"


def scene_groups(sc) -> dict:
    """D133: node groups no recorded object uses (the compositor's on Blender 5, add-ons' custom
    node trees, groups kept for later), recorded with the first recorded scene by name; and 4.2's
    compositor tree, which belongs to the scene."""
    from . import datarec
    scenes = sorted(x.name for x in bpy.data.scenes if x.library is None and datarec.is_tracked(x))
    out = {}
    nt = getattr(sc, "node_tree", None)          # Blender 4: the compositor's own tree
    if nt is not None:
        out[COMP_TREE] = capture_tree(nt)
    if scenes and scenes[0] != sc.name:
        return out
    if not len(bpy.data.node_groups):
        out[INV_GROUPS] = []                                  # D152: nothing there (not "no information")
        return out
    used = group_owner_map()
    uid = idmap.uid_of(sc)
    inv = []
    for g in bpy.data.node_groups:
        if g.library is not None or g.as_pointer() in used:
            continue
        gu = idmap.claim(g)
        if gu:
            out[NG_PREFIX + gu] = group_record(g, uid)
            out[NGA_PREFIX + gu] = anim_of(g)                # D146
            out[META_PREFIX + gu] = ["ng", g.name, bool(g.use_fake_user), g.bl_idname]
            inv.append(gu)
    out[INV_GROUPS] = sorted(inv)
    return out


# ----------------------------------------------------------------------------- data no object uses (D151)
TEXT_PREFIX = "@tx:"       # a text: {"body": what it says, "props": its settings}
LMAT_PREFIX = "@lm:"       # a material no object uses (kept with a fake user, a library): material_record
LWORLD_PREFIX = "@lw:"     # a world no scene uses: material_record
LPROPS_PREFIX = "@lp:"     # custom properties and add-on data of a datablock nothing else records: id_props
LOOSE_PREFIXES = (TEXT_PREFIX, LMAT_PREFIX, LWORLD_PREFIX, LPROPS_PREFIX)
# D152: what it takes to make one of these (and the node groups of D133) again, and which of them there were
META_PREFIX = "@lx:"       # [kind "tx" | "lm" | "lw" | "ng", name, fake user, node tree bl_idname]
INV_LOOSE = "@inv:loose"   # the ids of the texts, unused materials and worlds there are (sorted); [] when none
INV_GROUPS = "@inv:groups"  # the same for the node groups no object uses (D133)
LOOSE_READ_MAX = 32        # unused materials and worlds read again per operation, in turn (see forget_loose_data)
_TEXT_PROPS = ("filepath", "use_module", "indentation")
# Datablocks whose custom properties and add-on data no object records: their own, file-level data.
# (Object data, particle settings and materials an object uses are recorded with the object; node
# groups with D133.)
_PROP_COLLS = ("images", "actions", "palettes", "brushes", "linestyles", "masks", "movieclips", "sounds",
               "fonts", "cache_files", "paint_curves", "textures", "texts")
_LOOSE_TURN = [0]


def _unused(idb) -> bool:
    """No user but its fake user (a library kept for later, data an add-on keeps)."""
    return idb.library is None and idb.users - int(bool(idb.use_fake_user)) <= 0


def _loose_mats():
    return [m for m in bpy.data.materials if _unused(m)] + [w for w in bpy.data.worlds if _unused(w)]


def scene_loose(sc) -> dict:
    """D151: datablocks no recorded object and no scene uses, recorded with the first recorded scene by
    name: texts, materials and worlds nothing uses, and every other datablock's custom properties and
    add-on data (where any are set). They aren't in the depsgraph: texts and properties are read
    again at each operation, unused materials and worlds in turn (forget_loose_data)."""
    from . import datarec
    from .persistence import TEXT_NAME
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
                                     "props": {k: normalise(getattr(t, k)) for k in _TEXT_PROPS}}
            out[META_PREFIX + tu] = ["tx", t.name, bool(t.use_fake_user), ""]
            inv.append(tu)
    for m in _loose_mats():
        mu = idmap.claim(m)
        if mu:
            world = isinstance(m, bpy.types.World)
            out[(LWORLD_PREFIX if world else LMAT_PREFIX) + mu] = material_record(m, suid)
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
    from . import datarec
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
    from .persistence import TEXT_NAME
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
    try:
        if kind == "lm":
            return idb.as_pointer() in material_owner_map()
        if kind == "ng":
            return idb.as_pointer() in group_owner_map() or any(
                getattr(sc, "compositing_node_group", None) == idb or getattr(sc, "node_tree", None) == idb
                for sc in bpy.data.scenes)
        if kind == "lw":
            return any(sc.world == idb for sc in bpy.data.scenes)
    except Exception:
        pass
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
    try:
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
    except Exception:
        pass


# ----------------------------------------------------------------------------- collection settings (D127)
COLL_SETTINGS = "coll_settings"
_COLL_SKIP = frozenset(_UI_PROPS | _ID_SKIP | {"active_exporter_index"})
_LC_FLAGS = ("exclude", "hide_viewport", "holdout", "indirect_only")


def _walk_colls(c, out):
    for ch in c.children:
        out.append(ch)
        _walk_colls(ch, out)


def collection_settings(sc) -> dict:
    """Every collection's own settings (hide in viewport and render, selectable, colour tag, Line
    Art, instance offset) and, per view layer, each collection's Exclude, Hide in Viewport, Holdout
    and Indirect Only."""
    colls = []
    _walk_colls(sc.collection, colls)
    vals = {}
    for c in colls:
        if c.library is not None:
            continue
        cu = idmap.claim(c)
        if cu:
            vals[cu] = struct_values(c, _COLL_SKIP)
    layers = {}
    for vl in sc.view_layers:
        rows = {}
        stack = list(vl.layer_collection.children)
        while stack:
            lc = stack.pop()
            cu = idmap.uid_of(lc.collection)
            if cu:
                rows[cu] = [bool(getattr(lc, f)) for f in _LC_FLAGS]
            stack.extend(lc.children)
        layers[vl.name] = rows
    return {"colls": vals, "layers": layers}


def write_collection_settings(sc, rec) -> None:
    if not isinstance(rec, dict):
        return
    try:
        for cu, v in (rec.get("colls") or {}).items():
            c = idmap.find(cu)
            if c is not None and isinstance(v, dict):
                set_struct_values(c, v)
        for vl in sc.view_layers:
            rows = (rec.get("layers") or {}).get(vl.name)
            if not rows:
                continue
            stack = list(vl.layer_collection.children)
            while stack:
                lc = stack.pop()
                want = rows.get(idmap.uid_of(lc.collection))
                if want:
                    for f, w in zip(_LC_FLAGS, want):     # Exclude first: it changes the others
                        if bool(getattr(lc, f)) != w:
                            setattr(lc, f, w)
                stack.extend(lc.children)
    except Exception as e:
        log.error_once(("coll_settings", str(e)), f"could not restore collection settings: {e}", e)


def collection_tree(scene) -> list:
    """The scene's collections as [[id, name, parent id or "scene"], ...] in tree order."""
    rows = []

    def walk(c, parent):
        for ch in c.children:
            u = idmap.claim(ch)
            rows.append([u, ch.name, parent])
            if u:
                COLL_NAMES[u] = ch.name
            walk(ch, u)
    walk(scene.collection, "scene")
    return rows


def _write_collection_tree(scene, want: list) -> None:
    """Make the scene's collection tree match: collections taken out are only unlinked (their
    objects and the datablock stay, so an object step can still put objects back in them),
    collections that no longer exist are made again with their id and name."""
    if collection_tree(scene) == want:
        return

    def coll(ref):
        return scene.collection if ref == "scene" else idmap.find(ref)
    want_links = {(u, p) for u, _n, p in want}
    for u, _n, p in collection_tree(scene)[::-1]:            # children before parents
        if (u, p) not in want_links:
            par, ch = coll(p), idmap.find(u)
            if par is not None and ch is not None and ch.name in par.children:
                par.children.unlink(ch)
    names = {u: name for u, name, _p in want}
    for u, name, p in want:
        ch = idmap.find(u)
        if ch is None:
            ch = bpy.data.collections.new(name)
            ch[idmap.UID_PROP] = u
            idmap.note(ch, u)
            _RECREATED.add(u)
        _give_name(ch, name, names)
        par = coll(p)
        if par is not None and ch.name not in par.children:
            par.children.link(ch)
    # child order: relink the children of a parent whose order differs
    order = {}
    for u, _n, p in want:
        order.setdefault(p, []).append(u)
    for p, kids in order.items():
        par = coll(p)
        if par is None:
            continue
        have = [idmap.uid_of(c) for c in par.children]
        if [k for k in have if k in kids] != kids:
            for k in kids:
                c = idmap.find(k)
                if c is not None and c.name in par.children:
                    par.children.unlink(c)
                    par.children.link(c)


def _remove_object(ob) -> None:
    try:
        if bpy.context.view_layer.objects.active == ob and ob.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    bpy.data.objects.remove(ob, do_unlink=True)      # its data stays (it may come back)


def _recreate_object(scene, uid, body, snap):
    if not isinstance(body, dict):
        log.warn("an object can't be brought back: its details weren't recorded")
        return None
    data = idmap.find(body["data"]) if body.get("data") else None
    if data is None and body.get("type") == "MESH" and snap is not None:
        from . import meshio
        data = bpy.data.meshes.new(body.get("data_name") or body.get("name") or "Mesh")
        meshio.write_snapshot(data, snap)
        if body.get("data"):
            data[idmap.UID_PROP] = body["data"]
            idmap.note(data, body["data"])
    if data is None and body.get("data_type"):
        try:
            data = _remake_data(body)                # D122: its data left the file: made again
        except Exception as e:
            log.error_once(("remake", str(e)), f"could not make {body.get('name')}'s data again: {e}", e)
            data = None
    if data is None and body.get("type") not in ("EMPTY",):
        log.warn(f"{body.get('name')} can't be brought back: its data is gone")
        return None
    ob = bpy.data.objects.new(body.get("name") or "Object", data)
    ob[idmap.UID_PROP] = uid
    idmap.note(ob, uid)
    note_name(ob, body.get("name") or "Object")
    linked = False
    for ref in body.get("collections") or ():
        c = _coll_from_ref(ref)
        if c is not None:
            c.objects.link(ob)
            linked = True
    if not linked:
        scene.collection.objects.link(ob)
    ob.matrix_basis = to_rna(body["matrix"])
    for slot_mat, mu in zip(range(len(body.get("materials") or ())), body.get("materials") or ()):
        if data is not None and len(ob.material_slots) <= slot_mat and hasattr(data, "materials"):
            data.materials.append(idmap.find(mu) if mu else None)
    hv = body.get("hide") or [False, False, False]
    ob.hide_viewport, ob.hide_render, ob.hide_select = hv
    props = dict(body.get("props") or {})
    if "rotation_mode" in props:                 # the mode first: setting it converts the rotation
        set_struct_values(ob, {"rotation_mode": props.pop("rotation_mode")})
    set_struct_values(ob, props)                 # exact location, rotation, scale and the rest
    if body.get("constraints"):
        _write_constraints(ob, body["constraints"])
    for w in body.get("modifiers") or ():
        try:
            m = ob.modifiers.new(w["name"], w["type"])
            m.name = w["name"]
            set_struct_values(m, w.get("values") or {}, ob, f'modifiers["{m.name}"].')
            _set_id_props(m, w.get("custom"))
            _set_gn_inputs(m, w.get("gn"))
            _set_physics_modifier(ob, m, w)
        except Exception as e:
            log.error_once(("modifier_new", str(e)), f"could not bring back a modifier: {e}", e)
    for i, (link, ref) in enumerate(body.get("slots") or ()):
        if i >= len(ob.material_slots):
            break
        try:
            slot = ob.material_slots[i]
            if slot.link != link:
                slot.link = link
            if link == "OBJECT":
                slot.material = idmap.find(ref) if ref else None
        except Exception:
            pass
    _set_physics(ob, body.get("physics"))
    _set_hidden(ob, body.get("hidden"))
    _write_group(ob, body.get("custom") or {})
    if body.get("sys"):
        _write_group(_sys_group(ob, create=True), body["sys"])
    if isinstance(body.get("anim"), dict):
        write_anim_of(ob, body["anim"])         # D135
    return ob


ADAPTERS = {a.id: a for a in (RNAAdapter(), ObjectAdapter(), SceneAdapter())}
_OBJECT_KINDS = {"collections": "collections", "hide": "visibility", "constraints": "constraints"}


def kind_of(adapter_id: str, path: str):
    """The data kind (KINDS) a recorded path belongs to, or None."""
    if adapter_id == "scene":
        if path in (IDPROPS_KEY, SYSPROPS_KEY, "settings", COMP_TREE) or path.startswith(
                (WORLD_PREFIX, WORLDP_PREFIX, NG_PREFIX, NGA_PREFIX)):
            return None
        return "scene_collections" if path in ("collections", COLL_SETTINGS) else "objects"
    if adapter_id == "object":
        return _OBJECT_KINDS.get(path)
    if adapter_id == "rna":
        for rows in ALLOWLIST.values():
            for p, _k, cat in rows:
                if p == path:
                    return cat
    return None
_F64 = struct.Struct("<d")


def for_datablock(idb) -> list:
    return [a for a in ADAPTERS.values() if a.handles(idb)]


def timed_capture(adapter, idb) -> tuple:
    t = time.perf_counter()
    v = adapter.capture(idb)
    return v, (time.perf_counter() - t) * 1000.0
