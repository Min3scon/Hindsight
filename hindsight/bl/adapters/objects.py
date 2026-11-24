"""Data adapters: the object adapter, object data bodies and re-making a deleted object (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
import contextlib
from ...core.types import MISSING
from .. import idmap, log
from . import settings
from .animation import write_action, write_anim
from .bones import write_bones, write_pose
from .core import (
    Adapter, _note_ref, animated_paths, kind_on, note_name, set_struct_values, struct_values, to_rna)
from .extparts import _FAILED, _ext_part, _part, _put, _upd
from .geometry import _GEOM_TYPES, geometry_of, write_geometry
from .idprops import (
    _gn_inputs, _group_json, _hidden, _nested_values, _owns_data, _set_gn_inputs, _set_hidden,
    _set_id_props, _sys_group, _write_group, anim_of, id_props, write_anim_of, write_id_props)
from .keys import (
    ACT_PREFIX, ANIM_KEY, BONES, DATA_PREFIX, DATA_VALUES, GEOM, MATA_PREFIX, MATP_PREFIX, MAT_PREFIX,
    NGA_PREFIX, NG_PREFIX, PART_PREFIX, POSE_KEY, SETTINGS_KEY, SK_PREFIX, _EXT_CACHE, _NO_SLOTS,
    _OBMAT_CACHE)
from .materials import (
    _owned_refs, material_props, material_slots, owned_materials, write_group, write_material,
    write_material_slots)
from .membership import _coll_from_ref, _coll_ref, _write_constraints, users_collection
from .loose import queue_left
from .meshlists import mesh_lists, write_mesh_lists
from .modifiers import modifier_values, write_modifiers
from .particles import _particles, _physics, _set_physics, _set_physics_modifier
from .settings import DATA_STRUCTS, _BODY_SKIP, _DATA_SKIP, _id_props, write_settings


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
            elif path.startswith(MAT_PREFIX) and v is MISSING and (m := idmap.find(path[len(MAT_PREFIX):])):
                queue_left(m, "lm")             # D163: back over the slot that made it
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
    out = {"data_type": t, "data_values": settings.settings_of(d, _DATA_SKIP, DATA_STRUCTS.get(t) or ()),
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


def _remove_object(ob) -> None:
    with contextlib.suppress(Exception):
        if bpy.context.view_layer.objects.active == ob and ob.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    bpy.data.objects.remove(ob, do_unlink=True)      # its data stays (it may come back)


def _recreate_object(scene, uid, body, snap):
    if not isinstance(body, dict):
        log.warn("an object can't be brought back: its details weren't recorded")
        return None
    data = idmap.find(body["data"]) if body.get("data") else None
    if data is None and body.get("type") == "MESH" and snap is not None:
        from .. import meshio
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
