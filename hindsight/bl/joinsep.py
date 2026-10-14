"""Join with history merge (Ctrl+J) and unmerge on scrub.

Joining asks first, then merges the undo histories of all joined meshes into one
timeline that ends with a JOIN step. Scrubbing back past the JOIN re-creates the
absorbed objects (name, transform, collections, materials, exact mesh) so their
own earlier steps play back on them; going forward again re-applies the merge.
Joining never destroys history.
"""
from __future__ import annotations

import time

import bpy
from mathutils import Matrix

from ..core.history import MeshState, TOPO_MARK
from ..core.step import SNAPSHOTS_KEY
from ..core.types import JOIN, SETTINGS, Step, split_key
from . import log, meshio, runtime

MAP_ATTR = "_hs_src"
CONFIRM_MESSAGE = "Join these objects and merge their histories?"


# ----------------------------------------------------------------------------- helpers
def _mat_names(me) -> list:
    return [m.name if m is not None else "" for m in me.materials]


def _set_materials(me, names) -> None:
    if names is None or _mat_names(me) == list(names):
        return
    me.materials.clear()
    for n in names:
        me.materials.append(bpy.data.materials.get(n) if n else None)


def _matrix_list(ob) -> list:
    return [float(v) for row in ob.matrix_world for v in row]


def _strip_map_attr(snap) -> None:
    for k in [k for k in snap.arrays if split_key(k)[0] == MAP_ATTR]:
        del snap.arrays[k]


def _remove_map_attr(me) -> None:
    if me is None:
        return
    a = me.attributes.get(MAP_ATTR)
    if a is not None:
        me.attributes.remove(a)


def _set_mode(mode: str) -> None:
    with runtime.suspended():
        bpy.ops.object.mode_set(mode=mode)


def _plain_join(context) -> bool:
    try:
        return 'FINISHED' in bpy.ops.object.join()
    except RuntimeError as e:
        log.error_once(("join", str(e)), f"join failed: {e}", e)
        return False


def _members(context):
    a = context.view_layer.objects.active
    others = [o for o in context.selected_objects if o != a]
    return a, others


def _any_history(objs) -> bool:
    return any(o is not None and o.type == "MESH" and runtime.history_of(o) is not None for o in objs)


# ----------------------------------------------------------------------------- join
def join_with_history(context) -> bool:
    """Join the selected meshes into the active one and merge their histories.

    Returns True if a JOIN step was recorded, False for a plain join / failure."""
    A, others = _members(context)
    if A is None or A.type != "MESH" or not others or any(o.type != "MESH" for o in others):
        return _plain_join(context) and False
    if not _any_history([A] + others):
        _plain_join(context)
        return False
    R = runtime.reg()

    # (1) leave non-object modes and commit pending changes
    if A.mode == "EDIT":
        _set_mode("OBJECT")
        runtime.commit(A, label="Edit Mode", mode="EDIT", full=True)
    elif A.mode != "OBJECT":
        if A.mode in runtime.RECORD_MODES:
            runtime.commit(A)
        _set_mode("OBJECT")
    members = [A] + others
    for o in members:
        uid = runtime.obj_uid(o, create=True)
        h = R.history_for(uid)
        if h is not None and h.future_count():
            at = h.cursor
            h.truncate_future()     # the join is a new action: redo steps go, like any new step
            runtime._journal("TRUNC", {"hist": h.uid, "keep": at})
        if uid in runtime._STATES and h is not None:
            runtime.commit(o, label="Changes Outside History", mode=o.mode, full=True)

    # (2) uids, baselines and info for every member
    a_uid = runtime.obj_uid(A, create=True)
    runtime.ensure_state(A)
    a_before = meshio.read_snapshot(A.data)
    infos, snaps = [], {}
    for o in others:
        uid = runtime.obj_uid(o, create=True)
        runtime.ensure_state(o)
        infos.append({
            "uid": uid, "name": o.name, "matrix": _matrix_list(o),
            "collections": [c.name for c in o.users_collection],
            "materials": _mat_names(o.data), "mesh_name": o.data.name,
        })
        snaps[uid] = meshio.read_snapshot(o.data)
    a_mats_before = _mat_names(A.data)

    # (3) tag every vertex with its source member
    tagged = []
    for i, o in enumerate(members):
        me = o.data
        if me.name in tagged:
            continue
        _remove_map_attr(me)
        at = me.attributes.new(MAP_ATTR, "INT", "POINT")
        at.data.foreach_set("value", [i] * len(at.data))
        tagged.append(me.name)

    # (4) join
    a_name = A.name
    with runtime.suspended():
        ok = _plain_join(context)
    A = bpy.data.objects.get(a_name)
    if not ok or A is None:
        for n in tagged:
            _remove_map_attr(bpy.data.meshes.get(n))
        return False

    # (5) joined mesh
    after = meshio.read_snapshot(A.data)
    _remove_map_attr(A.data)
    _strip_map_attr(after)
    for n in tagged:
        me = bpy.data.meshes.get(n)
        if me is None or me == A.data:
            continue
        if me.users == 0:
            bpy.data.meshes.remove(me)     # absorbed mesh: recreated from the snapshot on unmerge
        else:
            _remove_map_attr(me)

    # (6) merge histories into one timeline
    hist = R.history_for(a_uid) or R.create(a_uid)
    absorbed = []
    for info in infos:
        hb = R.history_for(info["uid"])
        if hb is not None and hb is not hist and hb not in absorbed:
            absorbed.append(hb)
    all_members = list(hist.members)
    entries = list(hist.entries)
    for hb in absorbed:
        entries.extend(hb.entries)
        for m in hb.members:
            if m not in all_members:
                all_members.append(m)
    for info in infos:
        if info["uid"] not in all_members:
            all_members.append(info["uid"])
    if a_uid in all_members:
        all_members.remove(a_uid)
    all_members.insert(0, a_uid)
    rewrite = bool(absorbed and any(hb.entries for hb in absorbed))
    entries.sort(key=lambda e: e.seq)
    hist.entries = entries
    hist.cursor = len(entries)
    hist.members = all_members
    if not hist.enabled and any(hb.enabled for hb in absorbed):
        hist.enabled = True
    for hb in absorbed:
        R.histories.pop(hb.uid, None)
        hb.entries = []
        hb.cursor = 0
        runtime._journal("RESET", {"hist": hb.uid})
        runtime._journal("META", {"hist": hb.uid, "members": []})
        hb.members = []
    for m in all_members:
        R.attach(m, hist)
    runtime._journal("META", {"hist": hist.uid, "members": list(hist.members), "enabled": hist.enabled,
                              "morph_target": hist.morph_target})
    if rewrite:
        runtime._journal("RESET", {"hist": hist.uid})
        for i, e in enumerate(hist.entries):
            runtime._journal("STEP", {"hist": hist.uid, "at": i},
                             lambda e=e: e.to_bytes(SETTINGS.compression, 0))

    # (7) the JOIN step
    step = Step(seq=R.new_seq(), label="Join", mode="OBJECT", target=a_uid, time=time.time(),
                kind=JOIN, before=a_before, after=after,
                extra={"members": infos, "map_attr": MAP_ATTR,
                       "target_materials": a_mats_before,
                       "target_materials_after": _mat_names(A.data),
                       SNAPSHOTS_KEY: snaps})
    at = hist.cursor
    hist.push(step)
    entry = hist.entries[-1]
    runtime._journal("STEP", {"hist": hist.uid, "at": at}, lambda e=entry: e.to_bytes(SETTINGS.compression, 0))

    # (8) baselines
    runtime.ensure_state(A, refresh=True)
    for info in infos:
        runtime._STATES.pop(info["uid"], None)
        runtime._DIRTY.pop(info["uid"], None)
        runtime._UID_CACHE.pop(info["uid"], None)
    runtime._enforce()
    runtime.tag_redraw_all()
    return True


# ----------------------------------------------------------------------------- playback
def _link(ob, names) -> None:
    linked = False
    for n in names or ():
        coll = bpy.data.collections.get(n)
        if coll is None:
            for sc in bpy.data.scenes:
                if sc.collection.name == n:
                    coll = sc.collection
                    break
        if coll is not None and ob.name not in coll.objects:
            coll.objects.link(ob)
            linked = True
    if not linked:
        bpy.context.scene.collection.objects.link(ob)


def _recreate(info, snap):
    uid = info["uid"]
    ob = runtime.find_object(uid)
    if ob is not None:            # still there (e.g. restored by native undo): just reset its mesh
        if ob.mode == "OBJECT":
            meshio.write_snapshot(ob.data, snap)
        return ob
    me = bpy.data.meshes.new(info.get("mesh_name") or info.get("name") or "Mesh")
    meshio.write_snapshot(me, snap)
    for n in info.get("materials") or ():
        me.materials.append(bpy.data.materials.get(n) if n else None)
    ob = bpy.data.objects.new(info.get("name") or me.name, me)
    _link(ob, info.get("collections"))
    m = info.get("matrix")
    if m and len(m) == 16:
        ob.matrix_world = Matrix([m[0:4], m[4:8], m[8:12], m[12:16]])
    ob[runtime.UID_PROP] = uid
    runtime._UID_CACHE[uid] = ob.name
    return ob


def _remove(uid, target_uid) -> None:
    ob = runtime.find_object(uid)
    if ob is None:
        return
    vl = bpy.context.view_layer
    was_active = vl.objects.active == ob
    if was_active and ob.mode != "OBJECT":
        _set_mode("OBJECT")
    me = ob.data
    with runtime.suspended():
        bpy.data.objects.remove(ob, do_unlink=True)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
    runtime._UID_CACHE.pop(uid, None)
    runtime._DIRTY.pop(uid, None)
    if was_active:
        a = runtime.find_object(target_uid)
        if a is not None:
            try:
                vl.objects.active = a
                a.select_set(True)
            except Exception:
                pass


def join_hook(step, forward, states) -> dict:
    """History.goto hook for JOIN steps: unmerge (backward) / re-merge (forward)."""
    target = step.target
    extra = step.extra or {}
    infos = extra.get("members") or []
    snaps = extra.get(SNAPSHOTS_KEY) or {}
    a = runtime.find_object(target)
    st = states.get(target)
    if forward:
        with runtime.suspended():
            for info in infos:
                uid = info["uid"]
                _remove(uid, target)
                states.pop(uid, None)
                runtime._STATES.pop(uid, None)
        if st is not None and step.after is not None:
            st.set_snapshot(step.after)
        if a is not None:
            _set_materials(a.data, extra.get("target_materials_after"))
    else:
        if st is not None and step.before is not None:
            st.set_snapshot(step.before)
        if a is not None:
            _set_materials(a.data, extra.get("target_materials"))
        with runtime.suspended():
            for info in infos:
                uid = info["uid"]
                snap = snaps.get(uid)
                if snap is None:
                    continue
                _recreate(info, snap)
                c = snap.copy()
                ms = MeshState(c.counts, c.arrays)
                states[uid] = ms
                runtime._STATES[uid] = ms
    return {target: {TOPO_MARK}}


# ----------------------------------------------------------------------------- operator
class HINDSIGHT_OT_join(bpy.types.Operator):
    bl_idname = "hindsight.join"
    bl_label = "Join"
    bl_description = "Join the selection into the active object and merge their histories. Scrubbing back past the join separates them again"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.view_layer.objects.active is not None

    def invoke(self, context, event):
        _a, others = _members(context)
        if _any_history(list(context.selected_objects) + [context.view_layer.objects.active]):
            try:
                return context.window_manager.invoke_confirm(self, event, message=CONFIRM_MESSAGE)
            except TypeError:
                return context.window_manager.invoke_confirm(self, event)
        return bpy.ops.object.join()

    def execute(self, context):
        A, others = _members(context)
        if not others:
            return bpy.ops.object.join()
        if A is not None and A.type == "MESH" and _any_history([A] + others):
            return {'FINISHED'} if join_with_history(context) else {'CANCELLED'}
        return bpy.ops.object.join()


classes = (HINDSIGHT_OT_join,)
_keymaps = []


def _add_keymaps():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc is None:
        return
    km = kc.keymaps.new(name="Object Mode", space_type='EMPTY')
    for mods in ({"ctrl": True}, {"oskey": True}):
        k = km.keymap_items.new("hindsight.join", 'J', 'PRESS', **mods)
        _keymaps.append((km, k))


def register():
    for c in classes:
        bpy.utils.register_class(c)
    _add_keymaps()


def unregister():
    for km, k in _keymaps:
        try:
            km.keymap_items.remove(k)
        except Exception:
            pass
    _keymaps.clear()
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
