"""Data recorder: one step per user operation for everything adapters record (see ADAPTERS.md).

Runs next to the mesh recorder in runtime.py and shares its registry, states (MeshState.values
holds the recorded values) and journal. Nothing is recorded unless the preference Record is set to
Everything and the object was switched on with "Record everything on this object".

A step ends when (1) no modal operator is running and the newest registered operator changed (an
operator finished), or (2) changes have settled for SETTLE seconds (property edits, scripts).
"""
from __future__ import annotations

import time

import bpy

from ..core.history import DATA_MARK, MeshState
from ..core.types import DATA, MISSING, DataChange, Step
from . import adapters, guard, idmap, log

SETTLE = 0.3
# Modal operators that don't change data: navigation and UI. Anything else running means an
# operation is still in progress (transform, knife, a sculpt stroke, ...).
_PASSIVE_PREFIXES = ("VIEW3D_OT_rotate", "VIEW3D_OT_move", "VIEW3D_OT_zoom", "VIEW3D_OT_navigate",
                     "VIEW3D_OT_view", "VIEW3D_OT_fly", "VIEW3D_OT_walk", "VIEW3D_OT_ndof",
                     "SCREEN_OT_", "WM_OT_", "UI_OT_", "IMAGE_OT_view", "NODE_OT_view",
                     "GRAPH_OT_view", "ACTION_OT_view", "ANIM_OT_change_frame")

_DIRTY: dict = {}            # uid -> time of the last update seen
_BODIES: dict = {}           # object uid -> its latest body (to bring it back if it gets deleted)
_BODY_DIRTY: set = set()     # object uids whose body must be read again
_OPS = {"marker": None}      # (count, pointer of the newest) of window_manager.operators
_JUMPS: list = []            # Hindsight jumps pushed onto Blender's undo stack (see on_undo)
_MESH_DIRTY: set = set()     # tracked mesh objects whose mesh got a geometry update in Object Mode
_MESH_USERS: dict = {}       # mesh pointer -> [the tracked object that records it (its owner)]
_MESH_OWNER: dict = {}       # mesh pointer -> owner uid (kept while it uses the mesh, D39)
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


def set_tracked(idb, on: bool) -> None:
    """Switch "Record everything on this object" (also used by tests). Linked data is refused."""
    rt = _rt()
    if on and is_linked(idb):
        log.warn(LINKED_TEXT)
        return
    uid = idmap.claim(idb)
    if uid is None:
        return
    if on:
        rt.reg().everything.add(uid)
        baseline(idb, uid, refresh=True)
    else:
        rt.reg().everything.discard(uid)
    rt._journal("META_EVERYTHING", {"uid": uid, "on": bool(on)})
    refresh_mesh_users()


def _track_new(uid: str) -> None:
    """D36: an object added or duplicated while the scene is recorded is recorded fully from the
    start. Otherwise its later changes were lost when the global history took it away and brought
    it back (it came back as it was when added)."""
    rt = _rt()
    ob = idmap.find(uid)
    if ob is None or is_linked(ob) or not isinstance(ob, bpy.types.Object):
        return
    if uid in rt.reg().everything or uid in rt.reg().disabled:
        return
    rt.reg().everything.add(uid)
    baseline(ob, uid, refresh=True)
    rt._journal("META_EVERYTHING", {"uid": uid, "on": True})
    refresh_mesh_users()


def tracked_uids() -> set:
    if not scope_everything():
        return set()
    rt = _rt()
    return set(rt.reg().everything) - set(rt.reg().disabled)


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
    try:
        from .prefs import get_prefs
        p = get_prefs()
        if p is not None and hasattr(p, "record_kinds"):
            return {k.lower() for k in p.record_kinds}
    except Exception:
        pass
    return None


FORCE_KINDS = [None]          # tests: a set of kind ids


def capture(idb) -> dict:
    """(adapter, path) -> value for every adapter that handles this datablock and is switched on."""
    adapters.ENABLED_KINDS[0] = enabled_kinds()
    out = {}
    for a in adapters.for_datablock(idb):
        if not adapter_ok(a.id):
            continue
        try:
            vals = a.capture(idb)
        except Exception as e:
            _adapter_failed(a.id, e, "capture")
            continue
        for path, v in vals.items():
            out[(a.id, path)] = v
    return out


def refresh_mesh_users() -> None:
    """Which tracked object records each mesh (to see Object Mode changes to it). D39: a mesh shared
    by linked duplicates (Alt+D) is recorded once, by its owner: the object that recorded it first
    (kept while it uses the mesh), otherwise the first by name. Any other user of the mesh records
    only its own object data, and an object that becomes the owner starts from its mesh as it is."""
    users = {}
    for uid in tracked_uids():
        ob = idmap.find(uid)
        if isinstance(ob, bpy.types.Object) and ob.type == "MESH" and ob.data is not None:
            users.setdefault(ob.data.as_pointer(), []).append((ob.name, uid, ob))
    owners = {}
    for ptr, us in users.items():
        keep = _MESH_OWNER.get(ptr)
        uid = keep if any(u == keep for _n, u, _o in us) else min(us)[1]
        if uid != keep and len(us) > 1:
            ob = next(o for _n, u, o in us if u == uid)
            if ob.mode != "EDIT":
                _rt().ensure_state(ob, refresh=True)
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


def baseline(idb, uid: str | None = None, refresh: bool = False) -> MeshState:
    """The recorded values of a datablock (read from Blender the first time, or on refresh). A mesh
    object also gets a baseline of its mesh (to record Object Mode changes such as Apply)."""
    uid = uid or idmap.uid_of(idb, create=True)
    if isinstance(idb, bpy.types.Object) and idb.type == "MESH" and idb.mode != "EDIT":
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
        return
    try:
        if bpy.context.screen is not None and bpy.context.screen.is_animation_playing:
            return            # playback changes animated values every frame; nothing to record
    except Exception:
        pass
    now = time.time()
    scenes = [u for u in uids if _is_scene_uid(u)]
    for u in depsgraph.updates:
        idv = u.id
        orig = getattr(idv, "original", idv)
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


_SCENE_UIDS: set = set()


def _is_scene_uid(uid: str) -> bool:
    if uid in _SCENE_UIDS:
        return True
    idb = idmap.find(uid)
    if isinstance(idb, bpy.types.Scene):
        _SCENE_UIDS.add(uid)
        return True
    return False


def refresh_bodies(scene, only=None) -> None:
    """Keep the bodies of the scene's objects current (all of them, or the given uids)."""
    for ob in scene.objects:
        u = idmap.claim(ob)
        if u and (only is None or u in only):
            try:
                _BODIES[u] = adapters.object_body(ob)
            except Exception as e:
                log.error_once(("body", str(e)), f"could not read an object's details: {e}", e)


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
    try:
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


def _last_label(default: str = "Change") -> str:
    try:
        ops = bpy.context.window_manager.operators
        if len(ops):
            return ops[-1].bl_label or default
    except Exception:
        pass
    return default


@guard.guarded("datarec")
def tick() -> None:
    """Called from the runtime timer: commit when an operation has ended. When an operator finishes,
    every tracked datablock is compared (some operators, such as hiding in the viewport, don't send
    the object an update); otherwise only those that changed, once changes settle."""
    rt = _rt()
    if rt._SUSPEND[0] or _operation_running():
        return
    marker = _ops_marker()
    finished = marker is not None and marker != _OPS["marker"] and _OPS["marker"] is not None
    if marker is not None and _OPS["marker"] is None:
        _OPS["marker"] = marker
    if not _DIRTY and not finished:
        return
    settled = bool(_DIRTY) and time.time() - max(_DIRTY.values()) >= SETTLE
    if finished or settled:
        _OPS["marker"] = marker
        label = _last_label() if finished else "Change"
        refresh_mesh_users()
        if finished:
            # D32: the timer can run before Blender has evaluated what the operator changed; evaluate
            # now, so its mesh update is seen in this step and not left for a later one
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
        commit(label=label, uids=(tracked_uids() | set(_DIRTY)) if finished else None, mesh=True)
        if finished:
            _edit_step(label)


def _edit_step(label: str) -> None:
    """Edit Mode step by step: the operation that just finished on a tracked object in Edit Mode."""
    rt = _rt()
    obj = rt._active()
    if obj is not None and getattr(obj, "mode", "") == "EDIT" and rt.edit_steps_on(obj) \
            and adapter_ok("edit_steps"):
        try:
            rt.commit_edit(obj, label)
        except Exception as e:
            _adapter_failed("edit_steps", e, "capture")


def _diff(old: dict, new: dict) -> dict:
    """(adapter, path) -> (old, new) for every value that differs (MISSING on a side it lacks). An
    adapter that is switched off (after an error) is left out: its values aren't read any more,
    which isn't a removal."""
    out = {}
    failed = set()
    for k in set(old) | set(new):
        aid = k[0]
        if aid in failed or not adapter_ok(aid):
            continue
        a, b = old.get(k, MISSING), new.get(k, MISSING)
        if a is MISSING and b is MISSING:
            continue
        if k[1].startswith("body:"):
            continue        # stored with creation and deletion steps, never read back from Blender
        if b is MISSING and not adapters.kind_on(adapters.kind_of(*k) or ""):
            continue        # a kind switched off isn't read; that isn't a removal
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
def commit(label: str = "Change", uids=None, mesh: bool = True) -> list:
    """Record what changed on the dirty (or given) datablocks as one step per history. With mesh,
    a tracked mesh object in Object Mode whose mesh got an update is compared too, and what changed
    goes into the same step."""
    rt = _rt()
    targets = list(uids) if uids is not None else list(_DIRTY)
    for u in targets:
        _DIRTY.pop(u, None)
    # the scene first: a collection or object it brings back gets the lower step number, so it
    # exists before an object's own step from the same operation is replayed (and is removed last)
    targets.sort(key=lambda u: (not _is_scene_uid(u), u))
    steps = []
    op_first = None          # number of this operation's first step (global timeline: one position)
    for uid in targets:
        if uid not in tracked_uids():
            continue
        idb = idmap.find(uid)
        if idb is None or is_linked(idb):
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
        changes = _diff(st.values, new)
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
            snaps = {}
            for ou in sorted(old_set ^ new_set):
                body = _BODIES.get(ou)
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
            step = Step(seq=rt.reg().new_seq(), label=label, mode=mode, target=uid, time=time.time(),
                        kind=DATA, records=records, extra=extra)
        if op_first is None:
            op_first = step.seq
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
        rt._journal("STEP", {"hist": h.uid, "at": at}, lambda e=entry: e.to_bytes(rt.SETTINGS.compression, 0))
        rt._enforce(h)
        steps.append(step)
    if steps:
        drop_undone_jumps()   # a new step: Blender can no longer redo the jumps it undid
        rt.tag_redraw_all()
    return steps


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
    try:
        idb.update_tag()
    except Exception:
        pass
    _DIRTY.pop(idmap.uid_of(idb), None)


def resolve_missing_refs() -> None:
    """D37: an object a restore couldn't refer to is back (the global history brought it back): put
    the recorded references in now, so Blender matches the history again."""
    if not adapters.MISSING_REFS:
        return
    for ou, d in list(adapters.MISSING_REFS.items()):
        todo = {}
        for _path, (ref, aid, apath) in list(d.items()):
            if idmap.find(ref) is not None:
                todo.setdefault(aid, set()).add(apath)
        owner = idmap.find(ou)
        if todo and owner is not None:
            write(owner, _state(ou), todo)
        elif owner is None:
            adapters.MISSING_REFS.pop(ou, None)


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
    del _JUMPS[:-JUMPS_MAX]


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
    if not all(uid in seen and not _diff(vals, seen[uid]) for uid, vals in want.items()):
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
    rt = _rt()
    idmap.forget()
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
        diff = _diff(st.values, vals)
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
    refresh_mesh_users()
    for uid in tracked_uids():
        idb = idmap.find(uid)
        if idb is not None:
            baseline(idb, uid, refresh=True)


def forget() -> None:
    _DIRTY.clear()
    _BODIES.clear()
    _BODY_DIRTY.clear()
    _SCENE_UIDS.clear()
    _JUMPS.clear()
    _MESH_DIRTY.clear()
    _MESH_USERS.clear()
    _MESH_OWNER.clear()
    adapters.MISSING_REFS.clear()
    _OPS["marker"] = None
