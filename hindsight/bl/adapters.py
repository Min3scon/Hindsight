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
from . import idmap, log

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


def _fcurve_paths(action, slot) -> set:
    out = set()
    if action is None:
        return out
    try:
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(action, slot)
        if cb is not None:
            out.update(f.data_path for f in cb.fcurves)
        return out
    except Exception:
        pass
    try:                                                    # Blender 4.x: legacy actions
        out.update(f.data_path for f in action.fcurves)
    except Exception:
        pass
    return out


def animated_paths(idb) -> set:
    """RNA paths an animation, an NLA strip or a driver sets on this datablock. Their values change
    with the frame, not with the user, so the generic adapter never records them (Tier 4 records
    the animation itself)."""
    ad = getattr(idb, "animation_data", None)
    if ad is None:
        return set()
    out = {f.data_path for f in ad.drivers}
    out |= _fcurve_paths(ad.action, getattr(ad, "action_slot", None))
    for track in ad.nla_tracks:
        for strip in track.strips:
            out |= _fcurve_paths(strip.action, getattr(strip, "action_slot", None))
    return out


def normalise(v):
    """A property value as a JSON value (exact)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, bpy.types.ID):
        return idmap.ref_of(v)
    if hasattr(v, "to_list") and hasattr(v, "col"):          # Matrix
        return [list(map(float, row)) for row in v]
    try:
        return [normalise(x) for x in v]                      # Vector, Euler, Quaternion, Color, arrays
    except TypeError:
        return str(v)


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


class RNAAdapter(Adapter):
    """Properties from ALLOWLIST, read and written by RNA path."""
    id = "rna"

    @property
    def id_types(self):
        return tuple(ALLOWLIST)

    def rows(self, idb):
        return ALLOWLIST.get(idb.bl_rna.identifier, ())

    def capture(self, idb) -> dict:
        out = {}
        skip = animated_paths(idb)
        for path, _kind, cat in self.rows(idb):
            if path in skip or not kind_on(cat):
                continue
            try:
                out[path] = normalise(idb.path_resolve(path))
            except Exception:
                continue
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
            if v is MISSING:
                continue
            try:
                owner, attr = _split(idb, path)
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
            out["collections"] = sorted(_coll_ref(c) for c in idb.users_collection)
        if kind_on("visibility"):
            try:
                out["hide"] = bool(idb.hide_get())
            except Exception:
                pass
        if kind_on("constraints"):
            anim = animated_paths(idb)
            out["constraints"] = [{"type": c.type, "name": c.name, "values": struct_values(c, anim=anim)}
                                  for c in idb.constraints]
        return out

    def write(self, idb, values: dict, recorded=None) -> None:
        if "collections" in values and values["collections"] is not MISSING:
            want = set(values["collections"])
            have = {_coll_ref(c): c for c in idb.users_collection}
            for ref in want - set(have):
                c = _coll_from_ref(ref)
                if c is not None:
                    try:
                        c.objects.link(idb)
                    except Exception as e:
                        log.error_once(("coll_link", str(e)), f"could not put the object back in a collection: {e}", e)
            for ref in set(have) - want:
                if len(idb.users_collection) > 1:      # never take an object out of its last collection
                    try:
                        have[ref].objects.unlink(idb)
                    except Exception:
                        pass
        if "hide" in values and values["hide"] is not MISSING:
            try:
                idb.hide_set(bool(values["hide"]))
            except Exception:
                pass
        if "constraints" in values and values["constraints"] is not MISSING:
            _write_constraints(idb, values["constraints"])


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
    if have != [(w["type"], w["name"]) for w in want]:
        for c in list(ob.constraints):
            ob.constraints.remove(c)
        for w in want:
            try:
                c = ob.constraints.new(w["type"])
                c.name = w["name"]
            except Exception as e:
                log.error_once(("constraint_new", str(e)), f"could not restore a constraint: {e}", e)
    for c, w in zip(ob.constraints, want):
        set_struct_values(c, w.get("values") or {}, ob, f'constraints["{c.name}"].')


# Object settings a body leaves out: the name, data and parent are set on their own; matrices and
# dimensions (D26: setting dimensions turns them into a scale from the mesh as it is now) are
# derived from location, rotation and scale (which are kept exactly).
_BODY_SKIP = frozenset(_UI_PROPS | {"name", "data", "parent", "active_material", "active_material_index",
                                    "matrix_world", "matrix_local", "matrix_basis", "matrix_parent_inverse",
                                    "mode", "select", "active_shape_key_index", "dimensions"})


def object_body(ob) -> dict:
    """What it takes to bring a deleted object back exactly: its settings as values (transforms with
    their rotation mode, deltas, visibility, display, ...), its constraints and custom properties.
    Its mesh comes from the mesh datablock if it still exists, otherwise from the snapshot stored
    with the step."""
    data = ob.data
    return {
        "name": ob.name, "type": ob.type,
        "data": idmap.claim(data) if data is not None else None,
        "data_name": data.name if data is not None else None,
        "matrix": [list(map(float, r)) for r in ob.matrix_basis],
        "parent": idmap.ref_of(ob.parent) if ob.parent is not None else None,
        "parent_inverse": [list(map(float, r)) for r in ob.matrix_parent_inverse],
        "collections": sorted(_coll_ref(c) for c in ob.users_collection),
        "materials": [idmap.claim(s.material) if s.material is not None else None for s in ob.material_slots],
        "hide": [bool(ob.hide_viewport), bool(ob.hide_render), bool(ob.hide_select)],
        "props": struct_values(ob, _BODY_SKIP),
        "constraints": [{"type": c.type, "name": c.name, "values": struct_values(c)} for c in ob.constraints],
        "custom": {k: (ob[k].to_list() if hasattr(ob[k], "to_list") else ob[k]) for k in ob.keys()
                   if k != idmap.UID_PROP and isinstance(ob[k], (int, float, str)) or hasattr(ob[k], "to_list")},
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
            # linked objects are left out: bringing one back would make a local copy of it
            out["objects"] = sorted(u for u in (idmap.claim(o) for o in idb.objects if o.library is None) if u)
        return out

    def write(self, idb, values: dict, recorded=None) -> None:
        if "collections" in values and values["collections"] is not MISSING:
            _write_collection_tree(idb, values["collections"])
        if "objects" not in values or values["objects"] is MISSING or recorded is None:
            return
        want = set(values["objects"])
        have = {idmap.uid_of(o): o for o in idb.objects}
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
    for u, name, p in want:
        ch = idmap.find(u)
        if ch is None:
            ch = bpy.data.collections.new(name)
            ch[idmap.UID_PROP] = u
            idmap.note(ch, u)
        if ch.name != name:
            ch.name = name
        note_name(ch, name)
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
    for k, v in (body.get("custom") or {}).items():
        try:
            ob[k] = v
        except Exception:
            pass
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
