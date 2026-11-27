"""Data adapters: the shared helpers, the generic RNA adapter and the property-struct readers (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
import contextlib
import struct
from ...core.types import MISSING
from .. import idmap, log
from .keys import ANIMATED


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
    with contextlib.suppress(Exception):
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(action, slot)
        if cb is not None:
            return list(cb.fcurves)
        return []
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
REF_NAMES: dict = {}         # D57: id -> how the sidebar named it when the file was saved
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
_F64 = struct.Struct("<d")
