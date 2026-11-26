"""Data adapters: what Hindsight records on a datablock besides mesh channels (see ADAPTERS.md).

An adapter reads a datablock's tracked values as a flat dict (path -> JSON value), and writes given
paths back. Diffing and playback are generic (core). The mesh recorder in runtime.py is adapter one
and keeps its own fast path; everything here is for other data.

The generic RNA adapter reads properties listed in ALLOWLIST by RNA path. Values are normalised:
floats stay Python floats (exact for Blender's float and double properties), vectors, colours,
eulers, quaternions and matrices become lists, enums their identifier, ID pointers the target's
Hindsight id (or None).

This package splits the adapters by data kind; every name the one module had still resolves here
(`adapters.X`). A name the tests replace (`normalise`, `settings_of`, `material_record`) is replaced in
the submodule that defines it.
"""
from __future__ import annotations

from .animation import (  # noqa: F401
    _AD_SKIP, _KEY_1, _KEY_2, _KEY_INT, _OWN_PATHS, _SLOT_TYPES, _STRIP_SKIP, _TARGET_SKIP, _driver_values,
    _fcurve_coll, _keys_of, _nla_values, _set_keys, _slot_of, _write_drivers, _write_nla, action_owner_map,
    action_record, anim_values, owned_action, write_action, write_anim)
from .bones import (  # noqa: F401
    _BONE_SKIP, _POSE_FIELDS, _bone_shape, _edit_bones, bones_of, pose_values, write_bones, write_pose)
from .colls import (  # noqa: F401
    _COLL_SKIP, _LC_FLAGS, _walk_colls, _write_collection_tree, collection_settings, collection_tree,
    write_collection_settings)
from .core import (  # noqa: F401
    ALLOWLIST, Adapter, DEPENDENTS, ENABLED_KINDS, KINDS, MISSING_REFS, NAME_TAKEN, REF_NAMES, RNAAdapter,
    WANTED, _F64, _MATH_SEQ, _PLAIN, _PROPS, _SIMPLE_PATH, _UI_PROPS, _anim_base, _fcurves, _live,
    _note_ref, _props, _split, _without_animated, animated_paths, animated_slots, drop_changed_refs,
    kind_on, missing_refs, name_taken, normalise, note_name, same, set_struct_values, struct_values, to_rna)
from .extparts import (  # noqa: F401
    _FAILED, _ext_part, _part, _put, _upd)
from .geometry import (  # noqa: F401
    _ATTR_KEY, _BEZ_3, _BEZ_INT, _GEOM_TYPES, _GPL_SKIP, _GPP_FIELDS, _GPS_SKIP, _MB_SKIP, _PT_1,
    _SPLINE_SKIP, _attrs_of, _bulk, _curve_sizes, _drawing_record, _geom_print, _gp_record, _is_legacy_gp,
    _read_geometry, _stroke_sizes, _write_attrs, _write_drawing, _write_gp, _write_hair,
    _write_legacy_frame, _write_points, geometry_of, write_geometry)
from .idprops import (  # noqa: F401
    _ID_EMPTY4, _ID_EMPTY5, _MOD_STRUCTS, _NONE, _NO_ANIM, _gn_inputs, _group_json, _hidden, _idp_json,
    _idp_py, _mark_animated, _nested_values, _own_prop, _owns_data, _set_gn_inputs, _set_hidden,
    _set_id_props, _sys_group, _write_group, anim_of, assignment_path, id_props, owned_path, write_anim_of,
    write_id_props)
from .keys import (  # noqa: F401
    ACT_PREFIX, ANIMATED, ANIM_KEY, BONES, COLL_NAMES, COLL_SETTINGS, COMP_TREE, DATA_PREFIX, DATA_VALUES,
    GEOM, IDPROPS_KEY, INV_GROUPS, INV_LOOSE, LMAT_PREFIX, LOOSE_PREFIXES, LPROPS_PREFIX, LWORLD_PREFIX,
    MATA_PREFIX, MATP_PREFIX, MAT_PREFIX, META_PREFIX, NGA_PREFIX, NG_PREFIX, PART_PREFIX, POSE_KEY,
    REF_KEY, SCENE_NAME, SETTINGS_KEY, SK_PREFIX, SYSPROPS_KEY, TEXT_PREFIX, WORLDP_PREFIX, WORLD_PREFIX, _ACT_CACHE,
    _BONES_CACHE, _EXT_CACHE, _EXT_DEPS, _GEOM_CACHE, _MAT_CACHE, _NO_SLOTS, _OBMAT_CACHE, _RECREATED,
    _VAL_CACHE)
from .loose import (  # noqa: F401
    LOOSE_READ_MAX, _LOOSE_COLL, _LOOSE_GONE, _LOOSE_REC, _LOOSE_TURN, _PROP_COLLS, _TEXT_PROPS,
    _kept_by_record, _loose_mats, _loose_meta, _loose_owner, _make_loose_again, _queue_loose_removals,
    _unused, flush_loose_removals, forget_loose_data, scene_loose, write_loose)
from .materials import (  # noqa: F401
    _IFACE_SKIP, _ITEM_COLLS_SKIP, _ITEM_SKIP, _MAT_GROUPS, _MAT_OWNER_OF, _MAT_SKIP, _NODE_SKIP, _TREE_MAT,
    _groups_in_tree, _item_key, _node_items, _owned_refs, _set_socket, _sock_index, _socket_value,
    _write_items, capture_tree, forget_materials, group_owner_map, group_record, groups_of, interface_items,
    material_changed, material_owner_map, material_props, material_record, material_slots, owned_groups,
    owned_materials, ref_of_material, write_group, write_interface, write_material, write_material_slots,
    write_tree)
from .membership import (  # noqa: F401
    _UC_DIRECT, _coll_from_ref, _coll_ref, _write_constraints, users_collection)
from .meshlists import (  # noqa: F401
    _PENDING_UV, _SK_FIELDS, _fill_made, _give_name, _reorder, _write_shapekeys, _write_vgroups,
    after_mesh_write, drop_recreated_orphans, mesh_lists, write_mesh_lists)
from .modifiers import (  # noqa: F401
    MOD_PREFIX, _MOD_SKIP, _NOT_REMADE, _set_modifier, modifier_values, write_modifiers)
from .objects import (  # noqa: F401
    OBJECT_TYPES_REMADE, ObjectAdapter, _REMAKE, _anim_body, _data_body, _recreate_object, _remake_data,
    _remove_object, object_body)
from .particles import (  # noqa: F401
    _PART_SKIP, _particles, _physics, _set_physics, _set_physics_modifier, owned_particles,
    particle_owner_map)
from .scene import (  # noqa: F401
    SCENE_STRUCTS, SceneAdapter, _SCENE_SKIP, _VL_SKIP, _movie_on, _movie_playing, _scene_struct,
    owned_world, scene_groups, scene_settings, write_scene_settings)
from .settings import (  # noqa: F401
    DATA_STRUCTS, _BODY_SKIP, _DATA_SKIP, _ID_SKIP, _OBJ_SETTINGS_SKIP, _RT_SKIP, _id_props,
    _skip_with_runtime, forget_loose_groups, forget_selected_values, settings_of, value_changed,
    write_settings)


ADAPTERS = {a.id: a for a in (RNAAdapter(), ObjectAdapter(), SceneAdapter())}
_OBJECT_KINDS = {"collections": "collections", "hide": "visibility", "constraints": "constraints"}


def kind_of(adapter_id: str, path: str):
    """The data kind (KINDS) a recorded path belongs to, or None."""
    if adapter_id == "scene":
        if path in (IDPROPS_KEY, SYSPROPS_KEY, "settings", COMP_TREE, SCENE_NAME) or path.startswith(
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


def for_datablock(idb) -> list:
    return [a for a in ADAPTERS.values() if a.handles(idb)]
