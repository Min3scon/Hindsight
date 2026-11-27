import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R

from hindsight.bl import adapters

# Every name hindsight/bl/adapters.py had before it became a package (#84).
OLD_NAMES = """
    ACT_PREFIX ADAPTERS ALLOWLIST ANIMATED ANIM_KEY Adapter BONES COLL_NAMES COLL_SETTINGS COMP_TREE DATA_PREFIX
    DATA_STRUCTS DATA_VALUES DEPENDENTS ENABLED_KINDS GEOM IDPROPS_KEY INV_GROUPS INV_LOOSE KINDS LMAT_PREFIX
    LOOSE_PREFIXES LOOSE_READ_MAX LPROPS_PREFIX LWORLD_PREFIX MATA_PREFIX MATP_PREFIX MAT_PREFIX META_PREFIX
    MISSING_REFS MOD_PREFIX NAME_TAKEN NGA_PREFIX NG_PREFIX OBJECT_TYPES_REMADE ObjectAdapter PART_PREFIX
    POSE_KEY REF_KEY REF_NAMES RNAAdapter SCENE_STRUCTS SETTINGS_KEY SK_PREFIX SYSPROPS_KEY SceneAdapter
    TEXT_PREFIX WANTED WORLDP_PREFIX WORLD_PREFIX _ACT_CACHE _AD_SKIP _ATTR_KEY _BEZ_3 _BEZ_INT _BODY_SKIP
    _BONES_CACHE _BONE_SKIP _COLL_SKIP _DATA_SKIP _EXT_CACHE _EXT_DEPS _F64 _FAILED _GEOM_CACHE _GEOM_TYPES
    _GPL_SKIP _GPP_FIELDS _GPS_SKIP _ID_EMPTY4 _ID_EMPTY5 _ID_SKIP _IFACE_SKIP _ITEM_COLLS_SKIP _ITEM_SKIP
    _KEY_1 _KEY_2 _KEY_INT _LC_FLAGS _LOOSE_COLL _LOOSE_GONE _LOOSE_REC _LOOSE_TURN _MAT_CACHE _MAT_GROUPS
    _MAT_OWNER_OF _MAT_SKIP _MB_SKIP _MOD_SKIP _MOD_STRUCTS _NODE_SKIP _NONE _NOT_REMADE _NO_ANIM _NO_SLOTS
    _OBJECT_KINDS _OBJ_SETTINGS_SKIP _OBMAT_CACHE _OWN_PATHS _PART_SKIP _PENDING_UV _PLAIN _POSE_FIELDS _PROPS
    _PROP_COLLS _PT_1 _RECREATED _REMAKE _RT_SKIP _SCENE_SKIP _SIMPLE_PATH _SK_FIELDS _SLOT_TYPES _SPLINE_SKIP
    _STRIP_SKIP _TARGET_SKIP _TEXT_PROPS _TREE_MAT _UC_DIRECT _UI_PROPS _VAL_CACHE _VL_SKIP _anim_base
    _anim_body _attrs_of _bone_shape _bulk _coll_from_ref _coll_ref _curve_sizes _data_body _drawing_record
    _driver_values _edit_bones _ext_part _fcurve_coll _fcurves _fill_made _geom_print _give_name _gn_inputs
    _gp_record _group_json _groups_in_tree _hidden _id_props _idp_json _idp_py _is_legacy_gp _item_key
    _kept_by_record _keys_of _live _loose_mats _loose_meta _loose_owner _make_loose_again _mark_animated
    _movie_on _movie_playing _nested_values _nla_values _node_items _note_ref _own_prop _owned_refs _owns_data
    _part _particles _physics _props _put _queue_loose_removals _read_geometry _recreate_object _remake_data
    _remove_object _reorder _scene_struct _set_gn_inputs _set_hidden _set_id_props _set_keys _set_modifier
    _set_physics _set_physics_modifier _set_socket _skip_with_runtime _slot_of _sock_index _socket_value _split
    _stroke_sizes _sys_group _unused _upd _walk_colls _without_animated _write_attrs _write_collection_tree
    _write_constraints _write_drawing _write_drivers _write_gp _write_group _write_hair _write_items
    _write_legacy_frame _write_nla _write_points _write_shapekeys _write_vgroups action_owner_map action_record
    after_mesh_write anim_of anim_values animated_paths animated_slots assignment_path bones_of capture_tree
    collection_settings collection_tree drop_changed_refs drop_recreated_orphans flush_loose_removals
    for_datablock forget_loose_data forget_loose_groups forget_materials forget_selected_values geometry_of
    group_owner_map group_record groups_of id_props interface_items kind_of kind_on material_changed
    material_owner_map material_props material_record material_slots mesh_lists missing_refs modifier_values
    name_taken normalise note_name object_body owned_action owned_groups owned_materials owned_particles
    owned_path owned_world particle_owner_map pose_values ref_of_material same scene_groups scene_loose
    scene_settings set_struct_values settings_of struct_values to_rna users_collection value_changed
    write_action write_anim write_anim_of write_bones write_collection_settings write_geometry write_group
    write_id_props write_interface write_loose write_material write_material_slots write_mesh_lists
    write_modifiers write_pose write_scene_settings write_settings write_tree
""".split()


def t_every_old_name_resolves():
    missing = [n for n in OLD_NAMES if not hasattr(adapters, n)]
    assert not missing, missing


def t_no_module_is_long():
    d = os.path.dirname(adapters.__file__)
    long = {f: n for f in os.listdir(d) if f.endswith(".py")
            for n in [len(open(os.path.join(d, f), encoding="utf-8").read().splitlines())] if n > 600}
    assert not long, long


for fn in (t_every_old_name_resolves, t_no_module_is_long):
    R.run(fn)
R.finish()
