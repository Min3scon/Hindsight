"""Data recorder: one step per user operation for everything adapters record (see ADAPTERS.md).

Runs next to the mesh recorder in runtime.py and shares its registry, states (MeshState.values
holds the recorded values) and journal. Nothing is recorded unless the preference Record is set to
Everything and the object was switched on with "Record everything on this object".

A step ends when (1) no modal operator is running and the newest registered operator changed (an
operator finished), or (2) changes have settled for SETTLE seconds (property edits, scripts).
"""
from __future__ import annotations

import contextlib
import marshal
import time

import bpy

from ..core.history import DATA_MARK, MeshState
from ..core.native import ENGINE as _ENGINE
from ..core.types import DATA, MISSING, DataChange, Entry, Step
from . import adapters, guard, idmap, log, rigmark

SETTLE = 0.3
CONTINUE = 1.5               # a "Change" step is continued, not followed by another, if the next update comes this soon
_CLOCK = {"last": None, "gap": 0.0, "batch": None}    # time of the last update that made something dirty, the
                                                      # gap before it, and when the changes now waiting began
_RECENT: dict = {}           # uid -> (seq, time) of its newest settle-committed "Change" step


def _now() -> float:
    return time.time()


def _note_update(now: float) -> None:
    """D147: a UI drag (a slider in a panel) sends an update per redraw, slow redraws far apart. The gap
    between the last two updates (only gaps under CONTINUE count) is how long the next one is awaited."""
    last = _CLOCK["last"]
    if _CLOCK["batch"] is None:
        _CLOCK["batch"] = now
    if last is not None and now > last:
        _CLOCK["gap"] = now - last if now - last < CONTINUE else 0.0
    _CLOCK["last"] = now


def _settle_time() -> float:
    return max(SETTLE, 1.5 * _CLOCK["gap"])
# Modal operators that don't change data: navigation and UI. Anything else running means an
# operation is still in progress (transform, knife, a sculpt stroke, ...).
_PASSIVE_PREFIXES = ("VIEW3D_OT_rotate", "VIEW3D_OT_move", "VIEW3D_OT_zoom", "VIEW3D_OT_navigate",
                     "VIEW3D_OT_view", "VIEW3D_OT_fly", "VIEW3D_OT_walk", "VIEW3D_OT_ndof",
                     "SCREEN_OT_", "WM_OT_", "UI_OT_", "IMAGE_OT_view", "NODE_OT_view",
                     "GRAPH_OT_view", "ACTION_OT_view", "ANIM_OT_change_frame")

_DIRTY: dict = {}            # uid -> time of the last update seen
_BODIES: dict = {}           # object uid -> its latest body, packed (see _body): to bring it back if deleted
_BODY_DIRTY: set = set()     # object uids whose body must be read again
_OPS = {"marker": None}      # (count, pointer of the newest) of window_manager.operators
_JUMPS: list = []            # Hindsight jumps pushed onto Blender's undo stack (see on_undo)
_MESH_DIRTY: set = set()     # tracked mesh objects whose mesh got a geometry update in Object Mode
_MESH_USERS: dict = {}       # mesh pointer -> [the tracked object that records it (its owner)]
_MESH_OWNER: dict = {}       # mesh pointer -> owner uid (kept while it uses the mesh, D39)
_MESH_OF: dict = {}          # owner uid -> pointer of the mesh its state was read from (D44)
OBJECT_DEEP_MAX = 1_000_000  # vertices; above this an Object Mode check compares counts and values only


FORCE_SCOPE = [None]         # tests: "EVERYTHING" or "SCULPT" when the preferences aren't registered


def scope_everything() -> bool:
    if FORCE_SCOPE[0] is not None:
        return FORCE_SCOPE[0] == "EVERYTHING"
    try:
        from .prefs import get_prefs
        p = get_prefs()
        return p is not None and getattr(p, "record_scope", "SCULPT") == "EVERYTHING"
    except Exception:
        return False


def _rt():
    from . import runtime
    return runtime


def is_linked(idb) -> bool:
    """Linked from another file (not a local copy or an override): it can't be changed here, so
    Hindsight could never put recorded values back. Such data is never recorded."""
    return idb is not None and getattr(idb, "library", None) is not None


LINKED_TEXT = "Linked from another file: it can't be changed here, so Hindsight doesn't record it."
RIG_TEXT = "Not recorded: this is Hindsight's own Movie Mode object."


def is_tracked(idb) -> bool:
    """True if this datablock's data is recorded right now."""
    if idb is None or not scope_everything() or not guard.ok("datarec") or is_linked(idb):
        return False
    uid = idmap.uid_of(idb)
    rt = _rt()
    return uid is not None and uid in rt.reg().everything and uid not in rt.reg().disabled


def adapter_ok(aid: str) -> bool:
    return guard.ok("adapter:" + aid)


def _adapter_failed(aid: str, exc, where: str) -> None:
    """An adapter raised: switch off that adapter only (one log line, a message in the sidebar).
    Everything it recorded stays in the history; sculpting and the other adapters carry on."""
    guard.fail("adapter:" + aid, exc, where)


def file_candidates() -> list:
    """What "Everything in this file" records: every local scene and object, except Hindsight's own
    Movie Mode objects."""
    out = [sc for sc in bpy.data.scenes if sc.library is None]
    out += [ob for ob in bpy.data.objects if ob.library is None and not rigmark.is_rig(ob)]
    return out


def file_tracked() -> bool:
    """True when every local scene and object is recorded (stops at the first that isn't)."""
    if not scope_everything():
        return False
    try:
        return all(is_tracked(x) for x in file_candidates())
    except Exception:
        return False


def set_file_tracked(on: bool) -> int:
    """Switch Record everything on (or off) for every local scene and object. Returns how many
    objects that is. Objects added later are recorded while a scene is (D36)."""
    n = 0
    with idmap.frame():
        for x in file_candidates():
            if is_tracked(x) != on:
                set_tracked(x, on, _refresh=False)
            n += isinstance(x, bpy.types.Object)
        refresh_mesh_users()            # once for all (per object it was one walk each)
    return n


def set_tracked(idb, on: bool, _refresh: bool = True) -> None:
    """Switch "Record everything on this object" (also used by tests). Linked data is refused."""
    rt = _rt()
    if on and is_linked(idb):
        log.warn(LINKED_TEXT)
        return
    if on and rigmark.is_rig(idb):
        log.warn(RIG_TEXT)
        return
    uid = idmap.claim(idb)
    if uid is None:
        return
    if on:
        was_on = uid in rt.reg().everything
        rt.reg().everything.add(uid)
        h = rt.reg().history_for(uid)
        if not was_on and h is not None and h.entries and _state(uid).values:
            # D46: switched on again: what changed while it was off becomes one step, so every
            # position of its history is a state that existed
            refresh_mesh_users()
            if isinstance(idb, bpy.types.Object) and idb.type == "MESH" and idb.mode == "OBJECT":
                _MESH_DIRTY.add(uid)
            commit(label=CHANGES_WHILE_OFF, uids={uid}, mesh=True)
        baseline(idb, uid, refresh=True)
    else:
        rt.reg().everything.discard(uid)
    rt._journal("META_EVERYTHING", {"uid": uid, "on": bool(on)})
    if _refresh:
        refresh_mesh_users()


def _track_new(uid: str) -> None:
    """D36: an object added or duplicated while the scene is recorded is recorded fully from the
    start. Otherwise its later changes were lost when the global history took it away and brought
    it back (it came back as it was when added)."""
    rt = _rt()
    ob = idmap.find(uid)
    if ob is None or is_linked(ob) or not isinstance(ob, bpy.types.Object):
        return
    if rigmark.is_rig(ob):
        return
    if uid in rt.reg().everything or uid in rt.reg().disabled:
        return
    rt.reg().everything.add(uid)
    baseline(ob, uid, refresh=True)
    rt._journal("META_EVERYTHING", {"uid": uid, "on": True})
    refresh_mesh_users()


CHANGES_WHILE_OFF = "Changes While Not Recorded"
CHANGES_WHILE_EXCLUDED = "Changes While Excluded"
_EXCLUDED: set = set()       # D54: tracked objects excluded by Collection Filtering at the last check


def check_exclusion() -> None:
    """D54: objects excluded by Collection Filtering record nothing; when one stops being excluded,
    what changed meanwhile becomes one step, so its history stays a line of real states."""
    rt = _rt()
    sh = getattr(getattr(bpy.context, "scene", None), "hindsight", None)
    if not _EXCLUDED and (sh is None or sh.filter_mode == 'NONE'):
        return      # nothing is or was excluded: don't walk every recorded object on every tick
    with idmap.frame():
        _check_exclusion(rt)


def _check_exclusion(rt) -> None:
    for uid in tracked_uids():
        ob = idmap.find(uid)
        if not isinstance(ob, bpy.types.Object):
            continue
        if rt.is_excluded(ob):
            _EXCLUDED.add(uid)
        elif uid in _EXCLUDED:
            _EXCLUDED.discard(uid)
            catch_up(ob, CHANGES_WHILE_EXCLUDED)


def catch_up(idb, label: str = CHANGES_WHILE_OFF) -> list:
    """D46: record what changed on a recorded datablock while its history was paused, as one step
    (values and mesh)."""
    uid = idmap.uid_of(idb)
    if uid is None or uid not in tracked_uids() or not _state(uid).values:
        return []
    refresh_mesh_users()
    if isinstance(idb, bpy.types.Object) and idb.type == "MESH" and idb.mode == "OBJECT":
        _MESH_DIRTY.add(uid)
    return commit(label=label, uids={uid}, mesh=True)


def tracked_uids() -> set:
    """A new set every call (callers may keep it)."""
    if not scope_everything():
        return set()
    r = _rt().reg()
    return r.everything - r.disabled


def any_tracked() -> bool:
    """bool(tracked_uids()), without building the set."""
    if not scope_everything():
        return False
    r = _rt().reg()
    dis = r.disabled
    for u in r.everything:
        if u not in dis:
            return True
    return False


def _state(uid: str) -> MeshState:
    rt = _rt()
    st = rt._STATES.get(uid)
    if st is None:
        st = MeshState({}, {})
        rt._STATES[uid] = st
    return st


def enabled_kinds():
    """Data kinds switched on in the preferences (None: all)."""
    if FORCE_KINDS[0] is not None:
        return set(FORCE_KINDS[0])
    with contextlib.suppress(Exception):
        from .prefs import get_prefs
        p = get_prefs()
        if p is not None and hasattr(p, "record_kinds"):
            return {k.lower() for k in p.record_kinds}
    return None


FORCE_KINDS = [None]          # tests: a set of kind ids


def capture(idb) -> dict:
    """(adapter, path) -> value for every adapter that handles this datablock and is switched on."""
    fc = idmap.frame_cache()
    if fc is None:
        adapters.ENABLED_KINDS[0] = enabled_kinds()
        ads = adapters.for_datablock(idb)
    else:
        # in a frame the preferences and the adapters for a type can't change: read them once
        if "kinds" not in fc:
            fc["kinds"] = enabled_kinds()
        adapters.ENABLED_KINDS[0] = fc["kinds"]
        key = ("adapters", getattr(getattr(idb, "bl_rna", None), "identifier", None))
        ads = fc.get(key)
        if ads is None:
            ads = fc[key] = adapters.for_datablock(idb)
    out = {}
    for a in ads:
        if not adapter_ok(a.id):
            continue
        try:
            vals = a.capture(idb)
        except Exception as e:
            _adapter_failed(a.id, e, "capture")
            continue
        keys = _KEYS.get(a.id)
        if keys is None:
            keys = _KEYS[a.id] = {}
        for path, v in vals.items():
            k = keys.get(path)
            if k is None:
                k = keys[path] = (a.id, path)
            out[k] = v
    return out


# adapter -> path -> the (adapter, path) key: every object's recorded values share the same key
# tuples instead of each holding its own (about a quarter of what an object's values take)
_KEYS: dict = {}


def refresh_mesh_users() -> None:
    """Which tracked object records each mesh (to see Object Mode changes to it). D39: a mesh shared
    by linked duplicates (Alt+D) is recorded once, by its owner: the object that recorded it first
    (kept while it uses the mesh), otherwise the first by name. Any other user of the mesh records
    only its own object data, and an object that becomes the owner starts from its mesh as it is."""
    with idmap.frame():
        _refresh_mesh_users()


def _refresh_mesh_users(read=None) -> None:
    """read: a set that gets the uid of every object whose mesh state this read again."""
    users = {}
    for uid in tracked_uids():
        ob = idmap.find(uid)
        if isinstance(ob, bpy.types.Object) and ob.type == "MESH" and ob.data is not None:
            users.setdefault(ob.data.as_pointer(), []).append((ob.name, uid, ob))
    owners = {}
    rt = _rt()

    def records_mesh(u):
        h = rt.reg().history_for(u)
        return h is not None and any(e.meta.get("kind") != DATA for e in h.entries)
    for ptr, us in users.items():
        keep = _MESH_OWNER.get(ptr)
        if any(u == keep for _n, u, _o in us):
            uid = keep
        else:
            # D45: the object whose history already holds this mesh's steps, then the first by name
            uid = min((0 if records_mesh(u) else 1, n, u) for n, u, _o in us)[2]
        ob = next(o for _n, u, o in us if u == uid)
        if _MESH_OF.get(uid) != ptr and ob.mode != "EDIT":
            # D44: it records a mesh it didn't before (Make Single User, the owner deleted, a duplicate
            # that was never an owner): its state starts from that mesh as it is
            rt.ensure_state(ob, refresh=True)
            _MESH_OF[uid] = ptr
            if read is not None:
                read.add(uid)
        owners[ptr] = uid
    _MESH_OWNER.clear()
    _MESH_OWNER.update(owners)
    _MESH_USERS.clear()
    _MESH_USERS.update({ptr: [uid] for ptr, uid in owners.items()})


def mesh_owned_elsewhere(ob) -> bool:
    """True for a tracked object whose mesh is recorded by another object (a linked duplicate)."""
    me = getattr(ob, "data", None)
    if getattr(ob, "type", None) != "MESH" or me is None:
        return False
    owner = _MESH_OWNER.get(me.as_pointer())
    return owner is not None and owner != idmap.uid_of(ob)


def mesh_owner_name(ob) -> str | None:
    me = getattr(ob, "data", None)
    owner = _MESH_OWNER.get(me.as_pointer()) if me is not None else None
    o = idmap.find(owner) if owner else None
    return o.name if o is not None else None


def _mesh_busy(ob) -> bool:
    """Another object is editing, sculpting or painting this object's mesh right now: its mesh is
    checked when that ends (D39; reading it in the middle of an Edit Mode visit reads edit layers)."""
    me = ob.data
    if getattr(me, "is_editmode", False):
        return True
    try:
        return any(o is not ob and o.data == me and o.mode != "OBJECT" for o in bpy.context.view_layer.objects)
    except Exception:
        return False


def baseline(idb, uid: str | None = None, refresh: bool = False, _mesh_read: bool = False) -> MeshState:
    """The recorded values of a datablock (read from Blender the first time, or on refresh). A mesh
    object also gets a baseline of its mesh (to record Object Mode changes such as Apply).
    _mesh_read: its mesh state was read a moment ago with nothing changed since (prime)."""
    uid = uid or idmap.uid_of(idb, create=True)
    if not _mesh_read and isinstance(idb, bpy.types.Object) and idb.type == "MESH" and idb.mode != "EDIT":
        cur = _rt()._STATES.get(uid)
        if refresh or cur is None or not cur.counts:
            _rt().ensure_state(idb, refresh=True)
    st = _state(uid)
    if refresh or not st.values:
        st.values = capture(idb)
        if isinstance(idb, bpy.types.Scene):
            refresh_bodies(idb)
    return st


# ----------------------------------------------------------------------------- recording
@guard.guarded("datarec")
def on_update(depsgraph) -> None:
    """depsgraph_update_post: remember which tracked datablocks changed."""
    uids = tracked_uids()
    if not uids:
        adapters.forget_materials()
        return
    with contextlib.suppress(Exception):
        if bpy.context.screen is not None and bpy.context.screen.is_animation_playing:
            adapters.forget_materials()
            return            # playback changes animated values every frame; nothing to record
    now = _now()
    scenes = _scene_uids_in(uids)
    mats = (bpy.types.Material, bpy.types.NodeTree, bpy.types.World)
    for u in depsgraph.updates:
        idv = u.id
        orig = getattr(idv, "original", idv)
        adapters.value_changed(orig)          # D117: its kept settings are read again
        if isinstance(orig, mats):
            ou = adapters.material_changed(orig)   # D112: its object records it
            if ou in uids:
                _DIRTY[ou] = now
            continue
        uid = idmap.uid_of(orig)
        if uid in uids:
            _DIRTY[uid] = now
        if isinstance(orig, bpy.types.Mesh) and u.is_updated_geometry:
            for ou in _MESH_USERS.get(orig.as_pointer(), ()):
                if ou in uids:
                    _MESH_DIRTY.add(ou)
                    _DIRTY[ou] = now
        if scenes and isinstance(orig, (bpy.types.Object, bpy.types.Collection, bpy.types.Scene)):
            for su in scenes:
                _DIRTY[su] = now
            if isinstance(orig, bpy.types.Object) and uid is not None:
                _BODY_DIRTY.add(uid)
    if _DIRTY and max(_DIRTY.values()) == now:
        _note_update(now)


_SCENE_UIDS: set = set()


def _local_scene_ids() -> set:
    out = set()
    try:
        for sc in bpy.data.scenes:
            if sc.library is None:
                u = sc.get(idmap.UID_PROP)
                if isinstance(u, str):
                    out.add(u)
    except Exception:
        return None
    return out


def _scene_uids_in(uids) -> list:
    """[u for u in uids if _is_scene_uid(u)], reading the scenes once."""
    local = _local_scene_ids()
    if local is None:
        return [u for u in uids if _is_scene_uid(u)]
    return [u for u in uids if u in _SCENE_UIDS or (u in local and _is_scene_uid(u))]


def _is_scene_uid(uid: str) -> bool:
    if uid in _SCENE_UIDS:
        return True
    # find() only returns a local datablock that carries this id: if no local scene does, it can't
    # be a scene (a few scenes to look at instead of a search of every object, mesh, ...)
    with contextlib.suppress(Exception):
        if not any(sc.library is None and sc.get(idmap.UID_PROP) == uid for sc in bpy.data.scenes):
            return False
    idb = idmap.find(uid)
    if isinstance(idb, bpy.types.Scene):
        _SCENE_UIDS.add(uid)
        return True
    return False


def refresh_bodies(scene, only=None) -> None:
    """Keep the bodies of the scene's objects current (all of them, or the given uids)."""
    for ob in scene.objects:
        if rigmark.is_rig(ob):
            continue          # Hindsight's own Movie Mode objects: no id, no body
        u = idmap.claim(ob)
        if u and (only is None or u in only):
            try:
                _BODIES[u] = _pack_body(adapters.object_body(ob))
            except Exception as e:
                log.error_once(("body", str(e)), f"could not read an object's details: {e}", e)


def _pack_body(body):
    """A body is kept as marshal bytes (less than half the memory of the dict; it's read back only
    when its object is deleted or added). Values that marshal can't take keep the dict."""
    try:
        return marshal.dumps(body, 2)
    except Exception:
        return body


def _body(uid):
    """The body kept for uid (a new dict, equal to the one read from Blender), or None."""
    b = _BODIES.get(uid)
    if isinstance(b, bytes):
        return marshal.loads(b)
    return b


def _mesh_snapshot(body):
    """A snapshot of the mesh behind a body, while the mesh datablock still exists."""
    if not isinstance(body, dict) or body.get("type") != "MESH" or not body.get("data"):
        return None
    me = idmap.find(body["data"])
    if me is None:
        return None
    from . import meshio
    return meshio.read_snapshot(me)


def _operation_running() -> bool:
    try:  # runs every timer tick: a try costs nothing, suppress costs a context manager
        for win in bpy.context.window_manager.windows:
            for op in getattr(win, "modal_operators", ()):
                if not op.bl_idname.startswith(_PASSIVE_PREFIXES):
                    return True
    except Exception:
        pass
    return False


def _ops_marker():
    try:
        ops = bpy.context.window_manager.operators
        n = len(ops)
        return (n, ops[-1].as_pointer() if n else 0)
    except Exception:
        return None


# R048: Blender's own operators that change only which objects or elements are selected (and which is
# active). The recorder doesn't record selection, so when one of these ends, the objects it can have
# touched (selected, active, updated) are read instead of every recorded object, and Edit Mode reads
# no mesh (Blender's Edit Mode mesh is converted for that, 44 ms at 300,000 vertices). Each name is
# a built-in selection operator; one that can also change data (Select Mode changes the tool
# settings, Shortest Path can mark seams) is not here.
_SELECT_ONLY = frozenset({
    "VIEW3D_OT_select", "VIEW3D_OT_select_box", "VIEW3D_OT_select_circle", "VIEW3D_OT_select_lasso",
    "OBJECT_OT_select_all", "OBJECT_OT_select_random", "OBJECT_OT_select_by_type", "OBJECT_OT_select_grouped",
    "OBJECT_OT_select_linked", "OBJECT_OT_select_mirror", "OBJECT_OT_select_more", "OBJECT_OT_select_less",
    "OBJECT_OT_select_pattern", "OBJECT_OT_select_camera", "OBJECT_OT_select_hierarchy",
    "MESH_OT_select_all", "MESH_OT_select_more", "MESH_OT_select_less", "MESH_OT_select_linked",
    "MESH_OT_select_linked_pick", "MESH_OT_select_random", "MESH_OT_loop_select", "MESH_OT_edgering_select",
    "MESH_OT_select_similar", "MESH_OT_select_similar_region", "MESH_OT_select_nth",
    "MESH_OT_select_non_manifold", "MESH_OT_select_loose", "MESH_OT_select_interior_faces",
    "MESH_OT_select_face_by_sides", "MESH_OT_select_axis", "MESH_OT_select_mirror", "MESH_OT_region_to_loop",
    "MESH_OT_loop_to_region", "MESH_OT_select_ungrouped", "MESH_OT_loop_multi_select",
    "MESH_OT_select_by_attribute", "MESH_OT_select_by_pole_count",
})


def _last_op_kind(prev):
    """"select" (R048: one of _SELECT_ONLY), "transform" (R050: a TRANSFORM_OT_ operator: move, rotate,
    scale, slide... They change topology only through Auto Merge, which always changes the element
    counts) or None, when exactly one operator ended since the marker `prev`. Two or more, or a list
    that can't be told apart, are None: everything is read, as for any operator."""
    if prev is None:
        return None
    info = _last_ops_info()
    if info is None:
        return None
    idname, before_last, n = info
    if prev[0] == 0:
        if n != 1:
            return None
    elif not before_last or before_last != prev[1]:
        return None
    if idname in _SELECT_ONLY:
        return "select"
    if isinstance(idname, str) and idname.startswith("TRANSFORM_OT_"):
        return "transform"
    return None


def _selected_uids() -> set:
    """The recorded objects that are selected or active (all recorded ones if that can't be read)."""
    tracked = tracked_uids()
    try:
        vl = bpy.context.view_layer
        obs = [o for o in vl.objects if o.select_get()]
        act = vl.objects.active
        if act is not None:
            obs.append(act)
    except Exception:
        return tracked
    out = set()
    for o in obs:
        u = idmap.uid_of(o)
        if u in tracked:
            out.add(u)
    return out


def _last_ops_info():
    """(idname of the operator that ended last, pointer of the one before it or 0, how many are
    listed), or None."""
    try:
        ops = bpy.context.window_manager.operators
        n = len(ops)
        if not n:
            return None
        return (ops[-1].bl_idname, ops[-2].as_pointer() if n > 1 else 0, n)
    except Exception:
        return None


def _last_label(default: str = "Change") -> str:
    with contextlib.suppress(Exception):
        ops = bpy.context.window_manager.operators
        if len(ops):
            return ops[-1].bl_label or default
    return default


@guard.guarded("datarec")
def tick() -> None:
    """Called from the runtime timer: commit when an operation has ended. When an operator finishes,
    every tracked datablock is compared (some operators, such as hiding in the viewport, don't send
    the object an update); otherwise only those that changed, once changes settle."""
    rt = _rt()
    if rt._SUSPEND[0] or _operation_running():
        return
    idle = not any_tracked()
    if not idle:
        check_exclusion()
    marker = _ops_marker()
    # an operator ending adds itself to the list; a list that only got emptied is the undo step a UI
    # drag or click pushed (Blender clears the list then): no operation ended, the settle path records it
    finished = (marker is not None and marker != _OPS["marker"] and _OPS["marker"] is not None
                and marker[0] > 0)
    if marker is not None and _OPS["marker"] is None:
        _OPS["marker"] = marker
    if not _DIRTY and not finished:
        return
    settled = bool(_DIRTY) and _now() - max(_DIRTY.values()) >= _settle_time()
    if finished or settled:
        prev = _OPS["marker"]
        _OPS["marker"] = marker
        if idle:
            # nothing is recorded: what finish_operation or commit would leave behind, without
            # reading anything (no evaluation, no walk over the objects)
            _DIRTY.clear()
            _MESH_OWNER.clear()
            _MESH_USERS.clear()
            return
        if finished:
            kind = _last_op_kind(prev)
            finish_operation(_last_label(), select_only=kind == "select", topology=kind != "transform")
        else:
            refresh_mesh_users()
            commit(label="Change", uids=None, mesh=True, settled=True)


def finish_operation(label: str, select_only: bool = False, topology: bool = True) -> list:
    """What happens when an operator has finished: everything it changed is recorded as one
    operation (the scene, object data, meshes, every object in Edit Mode: one position on the
    global timeline, D52 and D58). Tests call this for what the timer does.

    select_only (R048): the operator only changed the selection. The selected, active
    and updated datablocks are compared (a setting that doesn't tell the depsgraph is read again on
    the selected and active objects, as for any operator, D117); the rest, and Edit Mode's mesh, wait
    for the next operator, which reads everything.

    topology False (R050, a move, rotate or scale): Edit Mode compares the element counts but doesn't
    read the edges and corners again."""
    rt = _rt()
    check_exclusion()
    refresh_mesh_users()
    adapters.forget_selected_values()     # D117
    adapters.forget_loose_groups()        # D133
    adapters.forget_loose_data()          # D151
    # D32: the timer can run before Blender has evaluated what the operator changed; evaluate now,
    # so its mesh update is seen in this step and not left for a later one
    with contextlib.suppress(Exception):
        bpy.context.view_layer.update()
    rt.OP_GROUP[0] = [None]
    try:
        uids = (_selected_uids() if select_only else tracked_uids()) | set(_DIRTY)
        steps = commit(label=label, uids=uids, mesh=True)
        if not select_only:
            _edit_step(label, deep=topology)
    finally:
        rt.OP_GROUP[0] = None
    return steps


def _edit_step(label: str, deep: bool = True) -> None:
    """Edit Mode step by step: the operation that just finished, on every recorded object in Edit Mode
    (D52: with several objects in Edit Mode, each gets its step, as one operation)."""
    rt = _rt()
    active = rt._active()
    try:
        editing = [o for o in bpy.context.view_layer.objects if o.mode == "EDIT" and o.type == "MESH"]
    except Exception:
        editing = [active] if active is not None else []
    editing.sort(key=lambda o: o != active)          # the active object first
    for obj in editing:
        if rt.edit_steps_on(obj) and adapter_ok("edit_steps"):
            try:
                rt.commit_edit(obj, label, deep=deep)
            except Exception as e:
                _adapter_failed("edit_steps", e, "capture")
                break


_STRICT_SAME = _ENGINE.same_values    # R070: exact types, floats by bits, dicts in the same key order


def _same_values(old: dict, new: dict) -> bool:
    """True only if the two value dicts are identical: same keys, same types, floats bit for bit (marshal writes a float's 8 bytes; version 2 never shares objects). Then
    _diff finds nothing. False says nothing: _diff decides."""
    if len(old) != len(new):
        return False
    try:
        # R070: the engine's strict comparison walks both dicts at once (a value that is the same
        # object on both sides is equal at once). True means marshal would find them equal too;
        # False (another key order, a type it doesn't know, a real change) says nothing, and the
        # marshal compare below decides as before
        if _STRICT_SAME(old, new):
            return True
        if old.keys() != new.keys():
            return False
        # a value that is the same object on both sides (kept records: a material's until the
        # material changes, D112; settings until their datablock changes, D117) is the same value:
        # only the others are compared
        ks = [k for k, v in new.items() if old[k] is not v]
        return marshal.dumps([old[k] for k in ks], 2) == marshal.dumps([new[k] for k in ks], 2)
    except Exception:
        return False


def _fast_diff_ok() -> bool:
    """_same_values may stand in for _diff only while every adapter compares exactly (the base
    Adapter.changed); an adapter with its own comparison always goes through _diff."""
    base = adapters.Adapter.changed
    for a in adapters.ADAPTERS.values():
        fn = getattr(a.changed, "__func__", None)
        if fn is not base and not getattr(fn, "equal_means_unchanged", False):
            return False
    return True


def _diff(old: dict, new: dict) -> dict:
    """(adapter, path) -> (old, new) for every value that differs (MISSING on a side it lacks). An
    adapter that is switched off (after an error) is left out: its values aren't read any more,
    which isn't a removal."""
    out = {}
    failed = set()
    exact = _fast_diff_ok()
    dumps = marshal.dumps
    for k in set(old) | set(new):
        aid = k[0]
        if aid in failed or not adapter_ok(aid):
            continue
        a, b = old.get(k, MISSING), new.get(k, MISSING)
        if a is MISSING and b is MISSING:
            continue
        if k[1].startswith("body:"):
            continue        # stored with creation and deletion steps, never read back from Blender
        if b is MISSING:
            kd = adapters.kind_of(*k)
            if kd is not None and not adapters.kind_on(kd):
                continue    # a kind switched off isn't read; that isn't a removal (a path of no kind is always read: D152)
        if a is b:
            continue        # the same object: unchanged
        if exact and a is not MISSING and b is not MISSING:
            try:
                if dumps(a, 2) == dumps(b, 2):
                    continue    # identical (see _same_values): the adapter would find no change
            except Exception:
                pass
        try:
            changed = a is MISSING or b is MISSING or adapters.ADAPTERS[aid].changed(k[1], a, b)
        except Exception as e:
            _adapter_failed(aid, e, "diff")
            failed.add(aid)
            continue
        if changed:
            out[k] = (a, b)
    if failed:
        out = {k: v for k, v in out.items() if k[0] not in failed}
    return out


def _data_diff(old: dict, new: dict) -> dict:
    """_diff without the values that only come and go with ownership (see _owner_moves): after
    Blender's undo another object can own a shared mesh than when the history was made."""
    out = _diff(old, new)
    for k in _moves_only(out):
        del out[k]
    return out


def _moves_only(changes: dict) -> list:
    """The keys of changes that only come and go with ownership. An action that appears or goes
    while the object starts or stops playing one (the first keyframe, D120) is a change of its own."""
    assigned = any(k[0] == "object" and adapters.assignment_path(k[1]) for k in changes)
    return [k for k, (a, b) in changes.items()
            if (a is MISSING or b is MISSING) and adapters.owned_path(k[0], k[1])
            and not (assigned and k[1].startswith(adapters.ACT_PREFIX))]


def _owner_moves(st, changes: dict) -> None:
    """Values an object records for data it owns (its mesh's properties, the materials it records)
    come and go with the ownership: another user deleted, a material put in a slot. That isn't a change
    of the data, so it only becomes the new baseline (no step)."""
    for k in _moves_only(changes):
        _a, b = changes.pop(k)
        if b is MISSING:
            st.values.pop(k, None)
        else:
            st.values[k] = b


def _mesh_step(ob, uid: str, label: str):
    """What an Object Mode operation (Apply, Shade Smooth, Set Origin, ...) changed on a tracked
    object's mesh, as an unsaved Step, or None."""
    rt = _rt()
    st = rt._STATES.get(uid)
    if st is None or not st.counts or rt.is_excluded(ob) or rt._blocking(ob):
        rt.ensure_state(ob, refresh=True)                 # Multires and Dyntopo pause recording
        return None
    deep = "corners" if len(ob.data.vertices) <= OBJECT_DEEP_MAX else False
    return rt._observe(ob, st, True, label, "OBJECT", deep=deep)


@guard.guarded("datarec")
def commit(label: str = "Change", uids=None, mesh: bool = True, settled: bool = False) -> list:
    """Record what changed on the dirty (or given) datablocks as one step per history. With mesh,
    a tracked mesh object in Object Mode whose mesh got an update is compared too, and what changed
    goes into the same step."""
    with idmap.frame():
        return _commit(label, uids, mesh, settled)


def _commit(label: str, uids, mesh: bool, settled: bool = False) -> list:
    rt = _rt()
    targets = list(uids) if uids is not None else list(_DIRTY)
    for u in targets:
        _DIRTY.pop(u, None)
    began = _CLOCK["batch"]          # when the changes recorded now began (D147)
    _CLOCK["batch"] = None
    # the scene first: a collection or object it brings back gets the lower step number, so it
    # exists before an object's own step from the same operation is replayed (and is removed last)
    scenes = set(_scene_uids_in(targets))
    targets.sort(key=lambda u: (u not in scenes, u))
    steps = []
    group = rt.OP_GROUP[0]   # set while one operator's changes are recorded (shared with Edit Mode steps)
    op_first = group[0] if group else None   # this operation's first step (global timeline: one position)
    tracked = tracked_uids()  # read again after _track_new adds objects
    fast = _fast_diff_ok()
    for uid in targets:
        if uid not in tracked:
            continue
        idb = idmap.find(uid)
        if idb is None or is_linked(idb):
            continue
        if isinstance(idb, bpy.types.Object) and rt.is_excluded(idb):
            _EXCLUDED.add(uid)       # D54: nothing is recorded for it (caught up when it's back)
            continue
        mesh_step = None
        if mesh and uid in _MESH_DIRTY and not (isinstance(idb, bpy.types.Object) and idb.type == "MESH"
                                                and idb.mode == "OBJECT" and _mesh_busy(idb)):
            _MESH_DIRTY.discard(uid)
            if isinstance(idb, bpy.types.Object) and idb.type == "MESH" and idb.mode == "OBJECT":
                if adapter_ok("object_mesh"):
                    try:
                        mesh_step = _mesh_step(idb, uid, label)
                    except Exception as e:
                        _adapter_failed("object_mesh", e, "capture")
        st = _state(uid)
        is_scene = isinstance(idb, bpy.types.Scene)
        if is_scene and _BODY_DIRTY:
            refresh_bodies(idb, only=set(_BODY_DIRTY))
        if not st.values:
            st.values = capture(idb)          # first sight: just a baseline
            if is_scene:
                refresh_bodies(idb)
            if mesh_step is not None:
                rt._apply_to_state(st, mesh_step)
            continue
        new = capture(idb)
        changes = {} if fast and _same_values(st.values, new) else _diff(st.values, new)
        if changes:
            _owner_moves(st, changes)
        if changes:
            adapters.drop_changed_refs(uid, changes)
        if changes and uid in _BODIES and isinstance(idb, bpy.types.Object):
            # D149: what changed is also what brings the object back if it's deleted later. Some changes
            # (hiding) send no depsgraph update, so the kept body is read again here, while it exists
            try:
                _BODIES[uid] = _pack_body(adapters.object_body(idb))
            except Exception as e:
                log.error_once(("body", str(e)), f"could not read an object's details: {e}", e)
        if not changes and mesh_step is None:
            if is_scene:
                _BODY_DIRTY.clear()
            continue
        by_adapter: dict = {}
        for (a, path), ch in changes.items():
            by_adapter.setdefault(a, {})[path] = ch
        extra = {}
        if is_scene and "scene" in by_adapter and "objects" in by_adapter["scene"]:
            old_objs, new_objs = by_adapter["scene"]["objects"]
            old_set = set(old_objs) if old_objs is not MISSING else set()
            new_set = set(new_objs) if new_objs is not MISSING else set()
            refresh_bodies(idb, only=new_set - old_set)
            for ou in sorted(new_set - old_set):
                _track_new(ou)
            tracked = tracked_uids()
            snaps = {}
            for ou in sorted(old_set ^ new_set):
                body = _body(ou)
                if ou in new_set:
                    by_adapter["scene"][f"body:{ou}"] = (MISSING, body if body is not None else MISSING)
                else:
                    by_adapter["scene"][f"body:{ou}"] = (body if body is not None else MISSING, MISSING)
                snap = _mesh_snapshot(body)
                if snap is not None:
                    snaps[ou] = snap
            if snaps:
                extra["_snapshots"] = snaps
            _BODY_DIRTY.clear()
        records = [DataChange(a, uid, ch) for a, ch in sorted(by_adapter.items())]
        h = rt.reg().history_for(uid)
        if h is not None and not h.enabled:
            continue
        if h is None:
            h = rt.reg().create(uid)
            rt._journal("META", {"hist": h.uid, "members": list(h.members), "enabled": True,
                                 "morph_target": None})
        if mesh_step is not None:
            step = mesh_step
            step.seq = rt.reg().new_seq()
            step.records = records
            if extra:
                step.extra = dict(step.extra or {}, **extra)
        else:
            mode = getattr(idb, "mode", "OBJECT") or "OBJECT"
            if settled and label == "Change" and not extra and group is None:
                cont = _continue(h, uid, records, began)
                if cont is not None:
                    _apply_continued(h, st, uid, cont, records)
                    steps.append(cont)
                    continue
            step = Step(seq=rt.reg().new_seq(), label=label, mode=mode, target=uid, time=_now(),
                        kind=DATA, records=records, extra=extra)
            if settled and label == "Change" and not extra and group is None:
                _RECENT[uid] = (step.seq, _now())
        if op_first is None:
            op_first = step.seq
            if group is not None:
                group[0] = op_first
        else:
            step.extra = dict(step.extra or {}, op=op_first)
        at = h.cursor
        removed = h.push(step)
        if mesh_step is not None:
            rt._apply_to_state(st, mesh_step)
        for r in records:
            st.apply_record(r, True)
        st.snaps.update(extra.get("_snapshots") or {})
        if removed:
            rt._journal("TRUNC", {"hist": h.uid, "keep": at})
        entry = h.entries[-1]
        rt._journal("STEP", {"hist": h.uid, "at": at}, rt.step_payload(entry))
        rt._enforce(h)
        steps.append(step)
    if steps:
        drop_undone_jumps()   # a new step: Blender can no longer redo the jumps it undid
        rt.tag_redraw_all()
    return steps


def _same_value(a, b) -> bool:
    if a is b:
        return True
    if a is MISSING or b is MISSING:
        return False
    try:
        return marshal.dumps(a, 2) == marshal.dumps(b, 2)
    except Exception:
        return False


def _continue(h, uid, records, began=None):
    """D147: the step this change continues, merged with it: a "Change" step committed by the settle
    timer less than CONTINUE seconds ago, still the newest step of the history (a UI drag whose redraws
    were slow enough for the timer to commit in the middle of it). The merged step goes from the older
    step's old values to the new values; None when this change is not a continuation."""
    rec = _RECENT.get(uid)
    if rec is None or (began if began is not None else _now()) - rec[1] > CONTINUE or not h.entries \
            or h.cursor != len(h.entries):
        return None
    if h.entries[-1].seq != rec[0]:
        return None
    try:
        prev = h.entries[-1].load()
    except Exception:
        return None
    if prev.kind != DATA or prev.label != "Change" or prev.extra or prev.target != uid:
        return None
    by = {}
    for r in prev.records:
        by.setdefault(r.adapter, {}).update(r.changes)
    if any(path not in by.get(r.adapter, ()) for r in records for path in r.changes):
        return None          # it changes something the step before did not: a separate edit, a step of its own
    for r in records:
        d = by.setdefault(r.adapter, {})
        for path, (old, new) in r.changes.items():
            d[path] = (d[path][0], new) if path in d else (old, new)
    out = []
    for a, d in sorted(by.items()):
        d = {p: (o, n) for p, (o, n) in d.items() if not _same_value(o, n)}
        if d:
            out.append(DataChange(a, uid, d))
    return Step(seq=prev.seq, label="Change", mode=prev.mode, target=uid, time=_now(), kind=DATA, records=out)


def _apply_continued(h, st, uid, step, records) -> None:
    """Put the merged step where the step it continues was (or take it away: nothing changed in the end)."""
    rt = _rt()
    at = len(h.entries) - 1
    for r in records:
        st.apply_record(r, True)
    rt._journal("TRUNC", {"hist": h.uid, "keep": at})
    if step.records:
        h.entries[-1] = Entry(step)
        entry = h.entries[-1]
        rt._journal("STEP", {"hist": h.uid, "at": at}, rt.step_payload(entry))
        _RECENT[uid] = (step.seq, _now())
        rt._enforce(h)
    else:
        h.entries.pop()
        h.cursor = len(h.entries)
        _RECENT.pop(uid, None)
    rt.tag_redraw_all()


# ----------------------------------------------------------------------------- playback
def split_keys(keys) -> tuple:
    """(mesh keys, {adapter: {paths}}) from the changed keys History.goto returns."""
    mesh, data = set(), {}
    for k in keys:
        if isinstance(k, tuple) and k and k[0] == DATA_MARK:
            data.setdefault(k[1], set()).add(k[2])
        else:
            mesh.add(k)
    return mesh, data


def write(idb, st: MeshState, data_keys: dict) -> None:
    """Write the recorded values of the given paths back to the datablock."""
    rt = _rt()
    with rt.suspended():
        for aid, paths in data_keys.items():
            a = adapters.ADAPTERS.get(aid)
            if a is None or not adapter_ok(aid):
                continue
            try:
                a.write(idb, {p: st.values.get((aid, p), MISSING) for p in paths},
                        lambda path, aid=aid: _recorded(st, aid, path))
            except Exception as e:
                _adapter_failed(aid, e, "apply")
    with contextlib.suppress(Exception):
        idb.update_tag()
    sync_taken_names()
    sync_missing_refs(idb, st, data_keys)
    _DIRTY.pop(idmap.uid_of(idb), None)


def find_deleted(uid):
    """(scene, body, mesh snapshot) of the newest recorded deletion of uid at or before a scene
    history's cursor, or None."""
    rt = _rt()
    key = f"body:{uid}"
    for sc in bpy.data.scenes:
        su = idmap.uid_of(sc)
        h = rt.reg().history_for(su) if su else None
        if h is None:
            continue
        for e in reversed(h.entries[:h.cursor]):
            step = e.load()
            for r in step.records:
                if r.adapter != "scene":
                    continue
                ch = r.changes.get(key)
                if ch and ch[0] is not MISSING and ch[1] is MISSING:
                    snap = ((step.extra or {}).get("_snapshots") or {}).get(uid)
                    return sc, ch[0], snap
    return None


BROUGHT_BACK = "Bring Back"


def bring_back(uid: str):
    """L12: make a deleted object a step's reference waits for exist again, as a new step of the
    scene's history (its own history can't, the global history could only by going back). Returns
    the object, or None."""
    if not uid or idmap.find(uid) is not None:
        return None
    hit = find_deleted(uid)
    if hit is None:
        body = _body(uid)
        if body is None:
            return None
        hit = (bpy.context.scene, body, None)
    sc, body, snap = hit
    ob = adapters._recreate_object(sc, uid, body, snap)
    if ob is None:
        return None
    if isinstance(body, dict) and body.get("parent"):
        par = idmap.find(body["parent"])
        if par is not None:
            ob.parent = par
            ob.matrix_parent_inverse = adapters.to_rna(body["parent_inverse"])
    idmap.forget()
    finish_operation(BROUGHT_BACK)            # the scene records it coming back; references resolve
    resolve_missing_refs()
    return ob


def ref_label(ref) -> str:
    """How the sidebar names a deleted object or collection a reference waits for."""
    if ref in adapters.COLL_NAMES:
        return f"the collection {adapters.COLL_NAMES[ref]}"
    name = (_body(ref) or {}).get("name")
    if name:
        return str(name)
    return adapters.REF_NAMES.get(ref) or "an object"


def sync_missing_refs(idb, st: MeshState, data_keys: dict) -> None:
    """D60: a reference that couldn't be put back (the object it points to is deleted now): the
    recorded value waits with the note (resolve_missing_refs puts it in when that object is back) and
    the state is what Blender has, so the next operation doesn't record the difference as a change."""
    ou = idmap.uid_of(idb)
    d = adapters.MISSING_REFS.get(ou)
    if not d:
        return
    cap = None
    for _path, (_ref, aid, apath) in list(d.items()):
        if apath not in data_keys.get(aid, ()) or (aid, apath) not in st.values:
            continue
        if cap is None:
            cap = capture(idb)
        if (aid, apath) in cap:
            adapters.WANTED[(ou, aid, apath)] = st.values[(aid, apath)]
            st.values[(aid, apath)] = cap[(aid, apath)]


def sync_taken_names() -> None:
    """D47: a datablock that couldn't get its recorded name (another one holds it) is recorded as
    the name Blender gave it, so the next operation doesn't record the difference as a change."""
    for u, wanted in list(adapters.NAME_TAKEN.items()):
        idb = idmap.find(u)
        if idb is None:
            continue
        st = _state(u)
        if st.values.get(("rna", "name")) == wanted and idb.name != wanted:
            st.values[("rna", "name")] = idb.name


_ID_DATA = {"OBJECT": "objects", "MESH": "meshes", "MATERIAL": "materials", "COLLECTION": "collections",
            "NODETREE": "node_groups", "WORLD": "worlds", "SCENE": "scenes", "CAMERA": "cameras",
            "LIGHT": "lights", "IMAGE": "images", "TEXT": "texts", "ACTION": "actions"}


def resolve_taken_names() -> int:
    """D105: after a jump, a datablock that couldn't get its recorded name because another one held
    it gets it now if that one has moved on (two objects that swapped names, a chain of renames).
    A name another datablock still holds is left alone: renaming a datablock the user didn't scrub is
    never done. Returns how many were renamed."""
    done = 0
    for _ in range(8):                                    # chains: each pass frees names for the next
        moved = 0
        for u, wanted in list(adapters.NAME_TAKEN.items()):
            idb = idmap.find(u)
            if idb is None or not isinstance(wanted, str) or idb.name == wanted:
                adapters.NAME_TAKEN.pop(u, None)
                continue
            col = getattr(bpy.data, _ID_DATA.get(getattr(idb, "id_type", ""), ""), None)
            if col is None:
                continue
            holder = col.get(wanted)
            if holder is not None and holder != idb:
                continue
            try:
                idb.name = wanted
            except Exception:
                continue
            adapters.note_name(idb, wanted)
            if idb.name == wanted:
                st = _rt()._STATES.get(u)
                if st is not None and ("rna", "name") in st.values:
                    st.values[("rna", "name")] = wanted
                moved += 1
        done += moved
        if not moved:
            break
    return done


def resolve_missing_refs() -> None:
    """D37: an object a restore couldn't refer to is back (the global history brought it back): put
    the recorded references in now, so Blender matches the history again. D55: then collections
    Hindsight made again that the jump took out and left empty are removed."""
    resolve_taken_names()
    adapters.flush_loose_removals()           # D152: datablocks the jump went back over the making of
    if not adapters.MISSING_REFS:
        adapters.drop_recreated_orphans()
        return
    for ou, d in list(adapters.MISSING_REFS.items()):
        todo = {}
        for _path, (ref, aid, apath) in list(d.items()):
            if idmap.find(ref) is not None:
                todo.setdefault(aid, set()).add(apath)
        owner = idmap.find(ou)
        if todo and owner is not None:
            st = _state(ou)
            for aid, paths in todo.items():
                for p in paths:
                    w = adapters.WANTED.pop((ou, aid, p), MISSING)
                    if w is not MISSING:
                        st.values[(aid, p)] = w
            write(owner, st, todo)
        elif owner is None:
            adapters.MISSING_REFS.pop(ou, None)
    adapters.drop_recreated_orphans()


def _recorded(st: MeshState, aid: str, path: str):
    if path.startswith("snapshot:"):
        return st.snaps.get(path[9:])
    return st.values.get((aid, path), MISSING)


PUSH_IN_BACKGROUND = [False]   # tests: act as if each jump went onto Blender's undo stack
JUMPS_MAX = 512                # jumps remembered (Blender keeps at most 256 undo steps)


def can_push() -> bool:
    return not bpy.app.background or PUSH_IN_BACKGROUND[0]


def push_undo(message: str = "Hindsight jump") -> bool:
    """Put the state after a jump on Blender's undo stack (Object Mode only)."""
    if bpy.app.background:
        return PUSH_IN_BACKGROUND[0]
    try:
        wm = bpy.context.window_manager
        win = wm.windows[0]
        with bpy.context.temp_override(window=win):
            bpy.ops.ed.undo_push(message=message)
        return True
    except Exception as e:
        log.error_once(("undo_push", str(e)), f"could not add the jump to Blender's undo: {e}", e)
        return False


def note_jump(hists: dict, values_before: dict, values_after: dict, meshes: dict | None = None) -> None:
    """Remember a jump that was pushed onto Blender's undo stack (see on_undo). hists: history uid
    -> [cursor before, cursor after, length] for every history the jump moved; meshes: object uid ->
    [mesh fingerprint before, after] for every recorded mesh it wrote. Jumps undone in Blender and
    not redone are dropped: a new jump starts a new line of redo."""
    drop_undone_jumps()
    _JUMPS.append({"hists": {k: list(v) for k, v in hists.items()}, "vb": values_before,
                   "va": values_after, "meshes": dict(meshes or {}), "undone": False})
    del _JUMPS[:-_jumps_max()]


def _jumps_max() -> int:
    """Jumps worth remembering: each one is a Blender undo step, and Blender keeps only its own
    number of those (Preferences > System > Undo Steps; 0 means no limit there)."""
    try:
        n = int(bpy.context.preferences.edit.undo_steps)
    except Exception:
        n = 0
    return n if n > 0 else JUMPS_MAX


def recorded_values() -> dict:
    """uid -> recorded values of every tracked datablock that exists now (a global jump notes them
    before and after, see _jump_matches)."""
    out = {}
    for uid in tracked_uids():
        if idmap.find(uid) is not None:
            out[uid] = dict(_state(uid).values)
    return out


def drop_undone_jumps() -> None:
    """A new step or jump: Blender can no longer redo the jumps it undid."""
    _JUMPS[:] = [j for j in _JUMPS if not j["undone"]]


def _jump_candidate(direction: str):
    """The jump Blender's undo (the newest not undone) or redo (the oldest undone) would reach."""
    if direction == "redo":
        return next((j for j in _JUMPS if j["undone"]), None)
    return next((j for j in reversed(_JUMPS) if not j["undone"]), None)


def _jump_matches(j, seen: dict) -> bool:
    """Blender's data is exactly the state on the other side of jump j: every history it moved is
    where j left it, the recorded values and every mesh it wrote match."""
    rt = _rt()
    back = not j["undone"]
    for hid, (cb, ca, n) in j["hists"].items():
        h = rt.reg().histories.get(hid)
        if h is None or len(h.entries) != n or h.cursor != (ca if back else cb):
            return False
    want = j["vb"] if back else j["va"]
    if not want and not j["meshes"]:
        return False
    if not all(uid in seen and not _data_diff(vals, seen[uid]) for uid, vals in want.items()):
        return False
    # a datablock that exists on the other side of the jump only (brought back or removed by it)
    # must not exist now
    if any(uid in seen for uid in set(j["va"] if back else j["vb"]) - set(want)):
        return False
    for uid, (fb, fa) in j["meshes"].items():
        ob = rt.find_object(uid)
        if ob is None or ob.type != "MESH" or rt.mesh_fingerprint(ob) != (fb if back else fa):
            return False
    return True


@guard.guarded("datarec")
def on_undo(direction: str = "undo") -> None:
    """After Blender's undo or redo: re-read tracked datablocks. If they now match the state before
    (undo) or after (redo) a Hindsight jump, the cursor follows; if they match a neighbouring step,
    the cursor moves one step; anything else becomes the new baseline (nothing is recorded)."""
    idmap.forget()
    adapters.forget_materials()
    with idmap.frame():
        return _on_undo(direction)


def _on_undo(direction: str):
    rt = _rt()
    refresh_mesh_users()
    seen = {}
    for uid in tracked_uids():
        idb = idmap.find(uid)
        if idb is not None:
            seen[uid] = capture(idb)
    if not seen:
        return set()
    moved = set()
    # Blender's undo reaches the newest jump not undone yet, its redo the oldest undone one; it
    # counts only while the histories are where that jump left them and the data (values and
    # meshes) is exactly the state on its other side.
    j = _jump_candidate(direction)
    if j is not None and _jump_matches(j, seen):
        for hid, (cb, ca, _n) in j["hists"].items():
            h = rt.reg().histories[hid]
            h.cursor = cb if not j["undone"] else ca
            moved.add(hid)
            for m in h.members:                     # Blender put the meshes back too
                ob = rt.find_object(m)
                if ob is not None:
                    rt.refresh_state_any_mode(ob)
        j["undone"] = not j["undone"]
    for uid, vals in seen.items():
        st = _state(uid)
        h = rt.reg().history_for(uid)
        idb = idmap.find(uid)
        mesh_step = None
        if isinstance(idb, bpy.types.Object) and idb.type == "MESH" and idb.mode == "OBJECT" and st.counts \
                and not mesh_owned_elsewhere(idb):
            # deep: an undone Edit Mode step can change the edges alone (Sort Mesh Elements)
            mesh_step = rt._observe(idb, st, True, "Undo", "OBJECT", deep=True)
            rt._NATIVE_PENDING.discard(uid)
        diff = _data_diff(st.values, vals)
        if (diff or mesh_step is not None) and h is not None and h.uid not in moved:
            # the direction Blender went first: after a step that returned to an earlier state,
            # both neighbours can match
            order = ((h.cursor, False), (h.cursor - 1, True)) if direction == "redo" else \
                ((h.cursor - 1, True), (h.cursor, False))
            for idx, inverse in order:
                if 0 <= idx < len(h.entries) and _step_matches(h.entries[idx].load(), uid, diff, mesh_step,
                                                               st, inverse):
                    h.cursor += -1 if inverse else 1
                    moved.add(h.uid)
                    break
        st.values = vals
        if mesh_step is not None:
            rt._apply_to_state(st, mesh_step)
    _DIRTY.clear()
    _MESH_DIRTY.clear()
    rt.tag_redraw_all()
    return moved


def _step_matches(step, uid, diff: dict, mesh_step, st, inverse: bool) -> bool:
    """True if what Blender's undo or redo changed is exactly this recorded step (or its undoing):
    the data records and, for a step with a mesh part, the mesh."""
    has_mesh = step.kind != DATA and step.target == uid
    if has_mesh != (mesh_step is not None):
        return False
    if has_mesh and not _rt()._inverse_matches(mesh_step, step, st, inverse):
        return False
    return _records_match(step, uid, diff, inverse)


def _records_match(step, uid, diff: dict, inverse: bool) -> bool:
    want = {}
    for r in step.records:
        if r.target != uid:
            continue
        for p, (o, n) in r.changes.items():
            if p.startswith("body:"):
                continue        # stored with the step, never read back from Blender (see _diff)
            want[(r.adapter, p)] = (n, o) if inverse else (o, n)
    if set(want) != set(diff):
        return False
    for k, (o, n) in want.items():
        do, dn = diff[k]
        for x, y in ((o, do), (n, dn)):
            if (x is MISSING) != (y is MISSING) or (x is not MISSING and not adapters.same(x, y)):
                return False
    return True


def rebaseline() -> None:
    """Re-read every tracked datablock without recording (after the data kinds change)."""
    for uid in tracked_uids():
        idb = idmap.find(uid)
        if idb is not None:
            st = _state(uid)
            st.values.update(capture(idb))
    _DIRTY.clear()


def prime() -> None:
    """Read baselines for every tracked datablock (after a file is loaded)."""
    with idmap.frame():
        read = set()
        _refresh_mesh_users(read)     # reads the mesh state of each mesh's owner
        for uid in tracked_uids():
            idb = idmap.find(uid)
            if idb is not None:
                # nothing changes in between: an owner's mesh state just read isn't read twice
                baseline(idb, uid, refresh=True, _mesh_read=uid in read)


def forget() -> None:
    _DIRTY.clear()
    _RECENT.clear()
    _CLOCK.update(last=None, gap=0.0, batch=None)
    adapters.forget_materials()
    _BODIES.clear()
    _BODY_DIRTY.clear()
    _SCENE_UIDS.clear()
    _JUMPS.clear()
    _MESH_DIRTY.clear()
    _MESH_USERS.clear()
    _MESH_OWNER.clear()
    _MESH_OF.clear()
    _EXCLUDED.clear()
    adapters.MISSING_REFS.clear()
    adapters.WANTED.clear()
    adapters.REF_NAMES.clear()
    adapters.NAME_TAKEN.clear()
    adapters.COLL_NAMES.clear()
    adapters._RECREATED.clear()
    _OPS["marker"] = None
