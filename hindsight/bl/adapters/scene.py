"""Data adapters: the scene adapter: scene settings, world and compositor (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
from ...core.types import MISSING
from .. import idmap, log, rigmark
from . import materials
from .colls import _write_collection_tree, collection_settings, collection_tree, write_collection_settings
from .core import Adapter, _UI_PROPS, animated_paths, kind_on, set_struct_values, struct_values, to_rna
from .extparts import _upd
from .idprops import anim_of, id_props, write_anim_of, write_id_props
from .keys import (
    ANIMATED, COLL_SETTINGS, COMP_TREE, INV_GROUPS, LOOSE_PREFIXES, META_PREFIX, NGA_PREFIX, NG_PREFIX,
    WORLDP_PREFIX, WORLD_PREFIX)
from .loose import _make_loose_again, _queue_loose_removals, _snapshots_back, scene_loose, write_loose
from .materials import capture_tree, group_owner_map, group_record, write_group, write_material, write_tree
from .objects import _recreate_object, _remove_object


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
        _snapshots_back(values, recorded, replace=True)     # D154: what still differs from its snapshot
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
        from .. import runtime
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
    from .. import datarec
    users = sorted(x.name for x in bpy.data.scenes if x.world == w and datarec.is_tracked(x))
    if users and users[0] != sc.name:
        return {}
    wu = idmap.claim(w)
    if not wu:
        return {}
    out = {WORLD_PREFIX + wu: materials.material_record(w, idmap.uid_of(sc))}
    p = dict(id_props(w, animated_paths(w) if w.animation_data is not None else None))   # (a copy: id_props
    p["anim"] = anim_of(w)                                           # D125     can give a shared constant)
    if getattr(w, "node_tree", None) is not None:
        p["tree_anim"] = anim_of(w.node_tree)
    out[WORLDP_PREFIX + wu] = p
    return out


def scene_groups(sc) -> dict:
    """D133: node groups no recorded object uses (the compositor's on Blender 5, add-ons' custom
    node trees, groups kept for later), recorded with the first recorded scene by name; and 4.2's
    compositor tree, which belongs to the scene."""
    from .. import datarec
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
