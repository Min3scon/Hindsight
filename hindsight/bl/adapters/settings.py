"""Data adapters: per-datablock settings (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
import contextlib
from .. import idmap, log
from .core import ALLOWLIST, _UI_PROPS, animated_paths, set_struct_values, struct_values
from .keys import (
    ANIMATED, REF_KEY, _ACT_CACHE, _BONES_CACHE, _EXT_CACHE, _EXT_DEPS, _GEOM_CACHE, _MAT_CACHE,
    _OBMAT_CACHE, _VAL_CACHE)
from .materials import group_owner_map


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
    with contextlib.suppress(Exception):
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


def value_changed(idb) -> None:
    with contextlib.suppress(Exception):
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
