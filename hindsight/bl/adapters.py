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
        for path, _kind, cat in self.rows(idb):
            if not kind_on(cat):
                continue
            try:
                v = normalise(idb.path_resolve(path))
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
        return out

    def write(self, idb, values: dict, recorded=None) -> None:
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
        for kb in key.key_blocks:
            row = [kb.name] + [normalise(getattr(kb, f)) for f in _SK_FIELDS]
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


def _write_constraints(ob, want: list) -> None:
    have = [(c.type, c.name) for c in ob.constraints]
    if have == [(w["type"], w["name"]) for w in want]:
        pairs = list(zip(ob.constraints, want))
    else:
        for c in list(ob.constraints):
            ob.constraints.remove(c)
        # each constraint made gets its own recorded values: one that can't be made is left out
        # (zipping the stack with the recorded list gave every later one the wrong values)
        pairs = []
        for w in want:
            try:
                c = ob.constraints.new(w["type"])
            except Exception as e:
                log.error_once(("constraint_new", str(e)), f"could not restore a constraint: {e}", e)
                continue
            try:
                c.name = w["name"]
            except Exception as e:
                log.error_once(("constraint_name", str(e)), f"could not name a restored constraint: {e}", e)
            pairs.append((c, w))
    for c, w in pairs:
        set_struct_values(c, w.get("values") or {}, ob, f'constraints["{c.name}"].')


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
        "custom": _id_props(ob),
    }


class SceneAdapter(Adapter):
    """Which objects exist in the scene (adding, deleting, duplicating objects). Bodies and mesh
    snapshots of the objects that come or go are stored with the step by datarec."""
    id = "scene"
    id_types = ("Scene",)

    def capture(self, idb) -> dict:
        out = {}
        if kind_on("scene_collections"):
            out["collections"] = collection_tree(idb)
        if kind_on("objects"):
            # linked objects are left out: bringing one back would make a local copy of it. Hindsight's
            # own Movie Mode objects (rigmark) are left out and never get an id
            out["objects"] = sorted(u for u in (idmap.claim(o) for o in idb.objects
                                                if o.library is None and not rigmark.is_rig(o)) if u)
        return out

    def write(self, idb, values: dict, recorded=None) -> None:
        if "collections" in values and values["collections"] is not MISSING:
            _write_collection_tree(idb, values["collections"])
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
    _set_id_props(ob, body.get("custom"))
    return ob


ADAPTERS = {a.id: a for a in (RNAAdapter(), ObjectAdapter(), SceneAdapter())}
_OBJECT_KINDS = {"collections": "collections", "hide": "visibility", "constraints": "constraints"}


def kind_of(adapter_id: str, path: str):
    """The data kind (KINDS) a recorded path belongs to, or None."""
    if adapter_id == "scene":
        return "scene_collections" if path == "collections" else "objects"
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
