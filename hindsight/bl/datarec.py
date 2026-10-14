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
_OPS = {"marker": None}      # (count, pointer of the newest) of window_manager.operators
_JUMPS: list = []            # Hindsight jumps pushed onto Blender's undo stack (see on_undo)


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


def is_tracked(idb) -> bool:
    """True if this datablock's data is recorded right now."""
    if idb is None or not scope_everything() or not guard.ok("recorder"):
        return False
    uid = idmap.uid_of(idb)
    rt = _rt()
    return uid is not None and uid in rt.reg().everything and uid not in rt.reg().disabled


def set_tracked(idb, on: bool) -> None:
    """Switch "Record everything on this object" (also used by tests)."""
    rt = _rt()
    uid = idmap.claim(idb)
    if uid is None:
        return
    if on:
        rt.reg().everything.add(uid)
        baseline(idb, uid, refresh=True)
    else:
        rt.reg().everything.discard(uid)
    rt._journal("META_EVERYTHING", {"uid": uid, "on": bool(on)})


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
    """(adapter, path) -> value for every adapter that handles this datablock."""
    adapters.ENABLED_KINDS[0] = enabled_kinds()
    out = {}
    for a in adapters.for_datablock(idb):
        for path, v in a.capture(idb).items():
            out[(a.id, path)] = v
    return out


def baseline(idb, uid: str | None = None, refresh: bool = False) -> MeshState:
    """The recorded values of a datablock (read from Blender the first time, or on refresh)."""
    uid = uid or idmap.uid_of(idb, create=True)
    st = _state(uid)
    if refresh or not st.values:
        st.values = capture(idb)
    return st


# ----------------------------------------------------------------------------- recording
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
    for u in depsgraph.updates:
        idv = u.id
        orig = getattr(idv, "original", idv)
        uid = idmap.uid_of(orig)
        if uid in uids:
            _DIRTY[uid] = now


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


def tick() -> None:
    """Called from the runtime timer: commit when an operation has ended."""
    if not _DIRTY:
        _OPS["marker"] = _ops_marker()
        return
    rt = _rt()
    if rt._SUSPEND[0] or _operation_running():
        return
    marker = _ops_marker()
    finished = marker is not None and marker != _OPS["marker"]
    settled = time.time() - max(_DIRTY.values()) >= SETTLE
    if finished or settled:
        _OPS["marker"] = marker
        commit(label=_last_label() if finished else "Change")


def _diff(old: dict, new: dict) -> dict:
    """(adapter, path) -> (old, new) for every value that differs (MISSING on a side it lacks)."""
    out = {}
    for k in set(old) | set(new):
        a, b = old.get(k, MISSING), new.get(k, MISSING)
        if a is MISSING and b is MISSING:
            continue
        if b is MISSING and not adapters.kind_on(adapters.kind_of(*k) or ""):
            continue        # a kind switched off isn't read; that isn't a removal
        if a is MISSING or b is MISSING or not adapters.same(a, b):
            out[k] = (a, b)
    return out


@guard.guarded("recorder")
def commit(label: str = "Change", uids=None) -> list:
    """Record what changed on the dirty (or given) datablocks as one step per history."""
    rt = _rt()
    targets = list(uids) if uids is not None else list(_DIRTY)
    for u in targets:
        _DIRTY.pop(u, None)
    steps = []
    for uid in targets:
        if uid not in tracked_uids():
            continue
        idb = idmap.find(uid)
        if idb is None:
            continue
        st = _state(uid)
        if not st.values:
            st.values = capture(idb)          # first sight: just a baseline
            continue
        new = capture(idb)
        changes = _diff(st.values, new)
        if not changes:
            continue
        by_adapter: dict = {}
        for (a, path), ch in changes.items():
            by_adapter.setdefault(a, {})[path] = ch
        records = [DataChange(a, uid, ch) for a, ch in sorted(by_adapter.items())]
        h = rt.reg().history_for(uid)
        if h is not None and not h.enabled:
            continue
        if h is None:
            h = rt.reg().create(uid)
            rt._journal("META", {"hist": h.uid, "members": list(h.members), "enabled": True,
                                 "morph_target": None})
        mode = getattr(idb, "mode", "OBJECT") or "OBJECT"
        step = Step(seq=rt.reg().new_seq(), label=label, mode=mode, target=uid, time=time.time(),
                    kind=DATA, records=records)
        at = h.cursor
        removed = h.push(step)
        for r in records:
            st.apply_record(r, True)
        if removed:
            rt._journal("TRUNC", {"hist": h.uid, "keep": at})
        entry = h.entries[-1]
        rt._journal("STEP", {"hist": h.uid, "at": at}, lambda e=entry: e.to_bytes(rt.SETTINGS.compression, 0))
        rt._enforce(h)
        steps.append(step)
    if steps:
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
            if a is None:
                continue
            a.write(idb, {p: st.values.get((aid, p), MISSING) for p in paths},
                    lambda path, aid=aid: st.values.get((aid, path), MISSING))
    try:
        idb.update_tag()
    except Exception:
        pass
    _DIRTY.pop(idmap.uid_of(idb), None)


def push_undo(message: str = "Hindsight jump") -> bool:
    """Put the state after a jump on Blender's undo stack (Object Mode data only)."""
    if bpy.app.background:
        return False
    try:
        wm = bpy.context.window_manager
        win = wm.windows[0]
        with bpy.context.temp_override(window=win):
            bpy.ops.ed.undo_push(message=message)
        return True
    except Exception as e:
        log.error_once(("undo_push", str(e)), f"could not add the jump to Blender's undo: {e}", e)
        return False


def note_jump(h, before: int, after: int, values_before: dict, values_after: dict) -> None:
    """Remember a jump that was pushed onto Blender's undo stack (see on_undo)."""
    _JUMPS.append({"hist": h.uid, "before": before, "after": after,
                   "vb": values_before, "va": values_after, "undone": False})
    del _JUMPS[:-32]


def on_undo() -> None:
    """After Blender's undo or redo: re-read tracked datablocks. If they now match the state before
    (undo) or after (redo) a Hindsight jump, the cursor follows; if they match a neighbouring step,
    the cursor moves one step; anything else becomes the new baseline (nothing is recorded)."""
    rt = _rt()
    idmap.forget()
    seen = {}
    for uid in tracked_uids():
        idb = idmap.find(uid)
        if idb is not None:
            seen[uid] = capture(idb)
    if not seen:
        return
    for j in reversed(_JUMPS):
        h = rt.reg().histories.get(j["hist"])
        if h is None:
            continue
        want = j["vb"] if not j["undone"] else j["va"]
        if want and all(uid in seen and not _diff(vals, seen[uid]) for uid, vals in want.items()):
            h.cursor = j["before"] if not j["undone"] else j["after"]
            j["undone"] = not j["undone"]
            break
    for uid, vals in seen.items():
        st = _state(uid)
        h = rt.reg().history_for(uid)
        diff = _diff(st.values, vals)
        if diff and h is not None:
            for idx, inverse in ((h.cursor - 1, True), (h.cursor, False)):
                if 0 <= idx < len(h.entries) and _records_match(h.entries[idx].load(), uid, diff, inverse):
                    h.cursor += -1 if inverse else 1
                    break
        st.values = vals
    _DIRTY.clear()
    rt.tag_redraw_all()


def _records_match(step, uid, diff: dict, inverse: bool) -> bool:
    want = {}
    for r in step.records:
        if r.target != uid:
            continue
        for p, (o, n) in r.changes.items():
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
    for uid in tracked_uids():
        idb = idmap.find(uid)
        if idb is not None:
            baseline(idb, uid, refresh=True)


def forget() -> None:
    _DIRTY.clear()
    _JUMPS.clear()
    _OPS["marker"] = None
