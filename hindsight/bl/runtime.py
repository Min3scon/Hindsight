"""Hindsight runtime: recorder (stroke detection, capture), playback and the API used by the UI."""
from __future__ import annotations

import time

import bpy
import numpy as np
from bpy.app.handlers import persistent

from ..core import globaltl
from ..core.history import MeshState
from ..core.registry import Registry, new_uid
from ..core.types import (
    SETTINGS, Step, DELTA, TOPO, CHANNEL_TYPES, split_key, key_width, key_dtype,
)
from . import guard, log, meshio
from .ctx import active_object as _ctx_active

UID_PROP = "_hindsight_uid"
RECORD_MODES = ("SCULPT", "VERTEX_PAINT")
TRACK_MODES = ("SCULPT", "VERTEX_PAINT", "EDIT")
DEBOUNCE = 0.15

_REG = Registry()
_STATES: dict = {}          # object uid -> MeshState (what the mesh shows at the cursor)
_DIRTY: dict = {}           # object uid -> time of last geometry update
_STROKE_SEEN: set = set()   # object uids changed while a stroke operator was running
_TRACK = {"name": None, "uid": None, "mode": None}
_SUSPEND = [0]
IN_HANDLER = [False]          # True while Movie Mode applies a step from a frame-change handler
_LAST_UNDO_MATCH = [0.0]
# Objects whose next commit may be a Blender undo/redo landing on a recorded neighbour. Set by the
# undo_post/redo_post handler. Without it, a change that happens to reverse the previous step
# (fill a mask, then clear it) would be mistaken for an undo and not recorded.
_NATIVE_PENDING: set = set()
# Objects whose mesh Hindsight has written since they entered their current mode. Blender's own
# sculpt undo doesn't know about those writes: undoing past one re-applies stale stroke data on
# top of the jumped-to mesh. For these objects Ctrl+Z never falls back to Blender's undo, and a
# Blender undo that lands outside the history is put back (see commit()).
_WROTE: set = set()
NATIVE_UNDO_TEXT = ("Blender's own undo can't go back past a history jump in Sculpt or Vertex Paint "
                    "Mode, so it was reversed. Use Ctrl+Z or the slider")


def mark_written(obj) -> None:
    uid = obj_uid(obj)
    if uid is not None:
        _WROTE.add(uid)


def wrote(obj) -> bool:
    return obj_uid(obj) in _WROTE
_UID_CACHE: dict = {}
_PENDING_TRUNCATE: dict = {}  # object uid -> number of future steps awaiting confirmation
missing_message = [None]      # history folder missing for this file, set by persistence
load_notes = []               # [(severity, text)] problems found while loading this file

journal_hook = None           # persistence sets: fn(rtype: str, header: dict, payload_fn or None)
packer = None


# ----------------------------------------------------------------------------- basics
def reg() -> Registry:
    return _REG


def set_registry(r: Registry) -> None:
    global _REG
    _REG = r
    MEMORY.update(estimate=0, evicted=0, warned=False)
    _STATES.clear()
    _DIRTY.clear()
    _STROKE_SEEN.clear()
    _WROTE.clear()
    _UID_CACHE.clear()
    _NATIVE_PENDING.clear()
    _TRACK.update(name=None, uid=None, mode=None)
    try:
        from . import datarec, idmap
        datarec.forget()
        idmap.forget()
    except Exception:
        pass


def settings():
    return SETTINGS


class suspended:
    """Context manager: ignore our own depsgraph/mode events while we write meshes."""
    def __enter__(self):
        _SUSPEND[0] += 1

    def __exit__(self, *a):
        _SUSPEND[0] -= 1


def _journal(rtype, header, payload_fn=None):
    if journal_hook is not None:
        try:
            journal_hook(rtype, header, payload_fn)
        except Exception as e:  # never break recording because of IO
            log.error_once(("journal", str(e)), f"crash-recovery journal error: {e}", e)


_FRESH_UIDS: set = set()     # ids given out for recording that may never happen (see drop_unused_uids)


def obj_uid(obj, create: bool = False):
    if obj is None or getattr(obj, "type", None) != "MESH":
        return None
    try:
        u = obj.get(UID_PROP)
    except Exception:
        return None
    if u is None and create:
        u = new_uid()
        obj[UID_PROP] = u
        _FRESH_UIDS.add(u)
    return u


def drop_unused_uids() -> int:
    """D30: entering Sculpt, Vertex Paint or Edit Mode gives an object an id so a first stroke can be
    recorded. If nothing was ever recorded with it (no history, not switched on, not referred to by
    recorded data), take it off again, so saving a file without recording adds nothing to it."""
    if not _FRESH_UIDS:
        return 0
    from . import idmap
    used = set(_REG.everything) | set(_REG.disabled) | set(idmap._owner)
    for h in _REG.histories.values():
        used.update(h.members)
    n = 0
    for ob in bpy.data.objects:
        u = ob.get(UID_PROP) if ob.library is None else None
        if u in _FRESH_UIDS and u not in used:
            del ob[UID_PROP]
            _STATES.pop(u, None)
            n += 1
    _FRESH_UIDS.intersection_update(used)
    _UID_CACHE.clear()
    return n


def find_object(uid: str):
    if uid is None:
        return None
    name = _UID_CACHE.get(uid)
    if name is not None:
        ob = bpy.data.objects.get(name)
        if ob is not None and ob.library is None and ob.get(UID_PROP) == uid:
            return ob
    for ob in bpy.data.objects:
        if ob.type == "MESH" and ob.library is None and ob.get(UID_PROP) == uid:   # never linked data
            _UID_CACHE[uid] = ob.name
            return ob
    return None


def is_excluded(obj) -> bool:
    try:
        from .filtering import is_excluded as _ex
        return bool(_ex(obj, bpy.context.scene))
    except Exception:
        return False


def blocking_reason(obj):
    try:
        from .warnings import blocking_reason as _br
        return _br(bpy.context, obj)
    except Exception:
        return None


def _blocking(obj) -> bool:
    try:
        from .warnings import blocking
        return bool(blocking(bpy.context, obj))
    except Exception:
        return False


def any_uid(obj):
    """The Hindsight id of any object (obj_uid is for meshes, which the sculpt recorder handles).
    Empties and other object types have one once Record everything has recorded them."""
    u = obj_uid(obj)
    if u is None and obj is not None:
        from . import idmap
        u = idmap.uid_of(obj)
    return u


def history_of(obj):
    uid = any_uid(obj)
    if uid is None or is_excluded(obj) or getattr(obj, "library", None) is not None:
        return None                       # linked data never has a history here (D28)
    return _REG.history_for(uid)


def is_recording(obj) -> bool:
    uid = obj_uid(obj)
    if uid is None or uid in _REG.disabled:
        return False
    h = _REG.history_for(uid)
    return h is None or h.enabled


def can_record(obj) -> bool:
    """True if the next stroke on obj would be recorded. Safe to call while drawing."""
    if obj is None or getattr(obj, "type", None) != "MESH" or not guard.ok("recorder"):
        return False
    if is_excluded(obj) or _blocking(obj):
        return False
    uid = obj_uid(obj)
    if uid is None:
        return True
    if uid in _REG.disabled:
        return False
    h = _REG.history_for(uid)
    return h is None or h.enabled


def _scene_props(context=None):
    sc = (context or bpy.context).scene
    return getattr(sc, "hindsight", None)


def use_global(context=None) -> bool:
    p = _scene_props(context)
    return bool(p.use_global) if p is not None else _REG.use_global


def _movie_locked(context=None) -> bool:
    p = _scene_props(context)
    return bool(p is not None and p.movie_mode and p.use_global)


def tag_redraw_all() -> None:
    wm = bpy.context.window_manager
    if wm is None:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type in ("VIEW_3D", "PROPERTIES"):
                area.tag_redraw()


# ----------------------------------------------------------------------------- state
def _sync_edit(obj) -> None:
    if obj.mode == "EDIT":
        try:
            obj.update_from_editmode()
        except Exception:
            pass


def read_mesh_snapshot(obj):
    """The object's mesh as a Snapshot. In Edit Mode the mesh attributes are the edit-mode layers,
    so the edit mesh is written back first and a plain copy is read instead."""
    if obj.mode != "EDIT":
        return meshio.read_snapshot(obj.data)
    with suspended():
        obj.update_from_editmode()
        tmp = obj.data.copy()
    try:
        return meshio.read_snapshot(tmp)
    finally:
        with suspended():
            bpy.data.meshes.remove(tmp)


def ensure_state(obj, refresh: bool = False) -> MeshState:
    uid = obj_uid(obj, create=True)
    st = _STATES.get(uid)
    if obj.mode == "EDIT" and st is not None and st.counts:
        return st  # mesh arrays are not readable in Edit Mode; keep the pre-edit baseline
    if st is None or refresh or not st.counts:
        snap = read_mesh_snapshot(obj)
        st = MeshState(snap.counts, snap.arrays, st.values if st is not None else None)
        _STATES[uid] = st
    return st


def _visible_uids() -> set:
    out = set()
    for ob in bpy.data.objects:
        u = any_uid(ob)
        if u is not None and not is_excluded(ob):
            out.add(u)
    try:
        from . import datarec
        out |= {u for u in datarec.tracked_uids() if datarec._is_scene_uid(u)}
        if any(datarec._is_scene_uid(u) for u in datarec.tracked_uids()):
            # with the scene recorded, a deleted object can come back: its history stays on the
            # global timeline (so the timeline doesn't change length when objects come and go)
            from . import idmap
            for h in _REG.histories.values():
                for m in h.members:
                    if m not in out and idmap.find(m) is None:
                        out.add(m)
    except Exception:
        pass
    return out


def prime_states() -> None:
    """Read a baseline for every object that has a history (after loading a file)."""
    for ob in bpy.data.objects:
        u = obj_uid(ob)
        if u is not None and _REG.history_for(u) is not None and ob.mode != "EDIT":
            ensure_state(ob, refresh=True)


# ----------------------------------------------------------------------------- recording
def _deltas_match(seen: Step, rec: Step, st: MeshState, inverse: bool) -> bool:
    """True if the change `seen` (diffed from `st`) is exactly `rec` undone (inverse) or redone."""
    from ..core import delta as D
    if seen.kind != DELTA or rec.kind != DELTA or len(seen.deltas) != len(rec.deltas):
        return False
    by_key = {d.key: d for d in rec.deltas}
    for d in seen.deltas:
        e = by_key.get(d.key)
        if e is None:
            return False
        before = st.arrays.get(d.key)
        if before is None:
            before = np.zeros(int(st.counts[split_key(d.key)[1]]) * key_width(d.key), dtype=key_dtype(d.key))
        if not D.same_change(d, e, before, inverse):
            return False
    return True


def _inverse_matches(new: Step, old: Step, st: MeshState, inverse: bool) -> bool:
    """True if `new` (observed change, diffed from `st`) is exactly `old` undone (inverse) or redone."""
    if new.kind == DELTA:
        return _deltas_match(new, old, st, inverse)
    if new.kind == TOPO and old.kind == TOPO and old.before is not None:
        def same(s1, s2):
            if s1.counts != s2.counts:
                return False
            for k in set(s1.arrays) | set(s2.arrays):
                if k not in s1.arrays or k not in s2.arrays:
                    return False
                if s1.arrays[k].tobytes() != s2.arrays[k].tobytes():
                    return False
            return True
        return (same(new.after, old.before) and same(new.before, old.after)) or \
               (same(new.after, old.after) and same(new.before, old.before))
    return False


def _observe(obj, st: MeshState, full: bool, label: str, mode: str, me=None, deep=True):
    """Diff the mesh (or `me`, a copy of it) against the state. Returns an unsaved Step (seq=0) or None.
    With full, every attribute is compared, and the topology too (deep: see meshio.topology_equal)."""
    from ..core import delta as D
    me = me if me is not None else obj.data
    uid = obj_uid(obj)
    c = meshio.counts(me)
    if c != st.counts or (full and not meshio.topology_equal(me, st.counts, st.arrays, deep=deep)):
        after = meshio.read_snapshot(me)
        return Step(seq=0, label=label or "Topology Change", mode=mode, target=uid, time=time.time(),
                    kind=TOPO, before=st.to_snapshot(), after=after)
    keys = meshio.channel_keys(me, sculpt_only=not full)
    present = set(keys)
    for k in list(st.arrays):
        if k in meshio.TOPO_KEYS or k in present:
            continue
        name = split_key(k)[0]
        if full or name in meshio.SCULPT_NAMES or split_key(k)[2] in ("FLOAT_COLOR", "BYTE_COLOR"):
            keys.append(k)
    deltas = []
    for key in keys:
        new = meshio.read_channel(me, key, reuse=True)
        old = st.arrays.get(key)
        if new is None and old is None:
            continue
        name, domain, dtn = split_key(key)
        n = st.counts[domain] * CHANNEL_TYPES[dtn][1]
        native = CHANNEL_TYPES[dtn][0]
        if old is None:
            d = D.diff(key, np.zeros(n, native), new, (False, True))
        elif new is None:
            d = D.diff(key, old, np.zeros(n, native), (True, False))
        else:
            if old.size != new.size:
                continue
            d = D.diff(key, old, new, (True, True))
        if d is not None:
            deltas.append(d)
    if not deltas:
        return None
    return Step(seq=0, label=label, mode=mode, target=uid, time=time.time(), kind=DELTA, deltas=deltas)


def _apply_to_state(st: MeshState, step: Step) -> None:
    if step.kind == DELTA:
        for d in step.deltas:
            st.apply_delta(d, True)
        st.removed.clear()
    else:
        st.set_snapshot(step.after)
        st.removed.clear()


def _write_state(obj, st: MeshState, keys, final: bool = True) -> None:
    mark_written(obj)
    me = obj.data
    with suspended():
        if "__TOPO__" in keys:
            mode = obj.mode
            is_active = bpy.context.view_layer.objects.active == obj
            if mode != "OBJECT" and is_active and IN_HANDLER[0]:
                # Mode switches inside a frame/depsgraph handler crash Blender: defer to a timer.
                name = obj.name
                snap = st.to_snapshot()

                def _later():
                    ob = bpy.data.objects.get(name)
                    if ob is not None:
                        tmp = MeshState(snap.counts, snap.arrays)
                        _write_state(ob, tmp, {"__TOPO__"}, final)
                    return None
                bpy.app.timers.register(_later, first_interval=0.0)
                st.removed.clear()
                return
            if mode != "OBJECT" and is_active:
                bpy.ops.object.mode_set(mode="OBJECT")
            meshio.write_snapshot(me, st.to_snapshot())
            if mode != "OBJECT" and is_active:
                bpy.ops.object.mode_set(mode=mode)
        else:
            meshio.write_changes(me, st.arrays, keys, st.removed)
            if obj.mode == "SCULPT" and final and _refresh_toggle():
                _sculpt_refresh(obj)
        st.removed.clear()
        obj.update_tag()


def _refresh_toggle() -> bool:
    try:
        from .prefs import get_prefs
        p = get_prefs()
        return bool(getattr(p, "sculpt_refresh_toggle", False)) if p else False
    except Exception:
        return False


def _sculpt_refresh(obj) -> None:
    if bpy.app.background:
        return
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.mode_set(mode="SCULPT")
    except Exception:
        pass


def commit_edit(obj, label: str = "Edit"):
    """Edit Mode step by step (Record everything on this object): record what the operation that
    just finished changed, as one step. Without it, Edit Mode is one step per visit."""
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        return None
    st = _STATES.get(obj_uid(obj))
    if st is None or not st.counts:
        return None
    with suspended():
        obj.update_from_editmode()
    return commit(obj, label=label, mode="EDIT", full=True, _edit=True)


EDIT_STEPS_MAX = 300_000   # vertices; above this an Edit Mode step (about 55 ms per 100k) is too slow


def _mesh_owned_elsewhere(obj) -> bool:
    try:
        from . import datarec
        return datarec.mesh_owned_elsewhere(obj)
    except Exception:
        return False


def edit_steps_on(obj) -> bool:
    try:
        from . import adapters, datarec
        return (datarec.is_tracked(obj) and adapters.kind_on("edit_steps")
                and len(obj.data.vertices) <= EDIT_STEPS_MAX and not datarec.mesh_owned_elsewhere(obj))
    except Exception:
        return False


def commit(obj, label: str | None = None, mode: str | None = None, full: bool = False, _edit: bool = False):
    """Record what changed on obj since the last commit as ONE step. Returns the Step or None."""
    if obj is None or obj.type != "MESH":
        return None
    uid = obj_uid(obj, create=True)
    _DIRTY.pop(uid, None)
    _STROKE_SEEN.discard(uid)
    if obj.mode == "EDIT" and not _edit:
        return None  # Edit Mode changes are grouped and recorded when leaving Edit Mode
    if uid in _REG.disabled or is_excluded(obj):
        return None
    mode = mode or obj.mode
    st = _STATES.get(uid)
    if st is None or not st.counts:          # no mesh baseline yet (or data values only)
        ensure_state(obj, refresh=True)
        return None
    if _blocking(obj):
        ensure_state(obj, refresh=True)
        return None
    h = _REG.history_for(uid)
    if h is not None and not h.enabled:
        return None
    if h is None and mode not in RECORD_MODES and not _edit:
        # Only a sculpt or paint stroke starts a history. Leaving Edit Mode on an object without
        # one (for example a file saved in Edit Mode) just takes a new baseline.
        ensure_state(obj, refresh=True)
        return None
    if label is None:
        label = {"SCULPT": "Sculpt", "VERTEX_PAINT": "Vertex Paint", "EDIT": "Edit Mode"}.get(mode, "Change")
        try:
            ops = bpy.context.window_manager.operators
            if len(ops):
                label = ops[-1].bl_label or label
        except Exception:
            pass
    src = None
    if _edit and obj.mode == "EDIT":
        # In Edit Mode the mesh's attributes are the edit-mode layers ("position" isn't one of them):
        # read a plain copy of the mesh instead (update_from_editmode has made it current).
        with suspended():
            src = obj.data.copy()
    try:
        step = _observe(obj, st, full, label, mode, me=src)
    finally:
        if src is not None:
            with suspended():
                bpy.data.meshes.remove(src)
    native = uid in _NATIVE_PENDING
    _NATIVE_PENDING.discard(uid)
    if step is None:
        return None
    if h is not None and native:
        # Native undo / redo / F9 redo moved the mesh onto a neighbouring recorded state. The
        # direction Blender went is tried first: after a step that returned to an earlier state,
        # both neighbours can match.
        back = (h.cursor > 0, h.cursor - 1, True, -1)
        fwd = (h.cursor < len(h.entries), h.cursor, False, 1)
        for ok_, idx, inverse, move in ((fwd, back) if UNDO_DIRECTION[0] == "redo" else (back, fwd)):
            if ok_ and _inverse_matches(step, h.entries[idx].load(), st, inverse):
                h.cursor += move
                _apply_to_state(st, step)
                if inverse:
                    _LAST_UNDO_MATCH[0] = time.time()
                _journal("CURSOR", {"hist": h.uid, "cursor": h.cursor})
                tag_redraw_all()
                return None
        if uid in _WROTE and mode in RECORD_MODES:
            # Blender's undo replayed stroke data recorded before a Hindsight jump: the result is
            # not a real state. Put the mesh back (never record it, never drop later steps).
            _put_back_after_native_undo(obj, st, step)
            return None
    if h is not None:
        fut = h.future_count()
        if fut > 0:
            replace = fut == 1 and time.time() - _LAST_UNDO_MATCH[0] < 3.0
            thr = SETTINGS.truncation_threshold
            if not replace and thr > 0 and fut >= thr:
                # Discard this stroke, ask first; the user repeats the stroke after confirming.
                _revert(obj, st, step)
                _PENDING_TRUNCATE[uid] = fut
                _ask_truncate(fut)
                return None
    else:
        h = _REG.create(uid)
        _journal("META", {"hist": h.uid, "members": list(h.members), "enabled": True,
                          "morph_target": None})
    step.seq = _REG.new_seq()
    at = h.cursor
    removed = h.push(step)
    _apply_to_state(st, step)
    if removed:
        _journal("TRUNC", {"hist": h.uid, "keep": at})
    entry = h.entries[-1]
    _journal("STEP", {"hist": h.uid, "at": at}, lambda e=entry: e.to_bytes(SETTINGS.compression, 0))
    _enforce(h)
    try:
        from . import datarec
        datarec.drop_undone_jumps()
    except Exception:
        pass
    tag_redraw_all()
    return step


def _put_back_after_native_undo(obj, st: MeshState, step: Step) -> None:
    keys = {d.key for d in step.deltas} | set(st.removed) if step.kind == DELTA else {"__TOPO__"}
    name = obj.name

    def _later():
        ob = bpy.data.objects.get(name)
        if ob is not None:
            _write_state(ob, st, keys)
            tag_redraw_all()
        return None
    # Never write meshes or switch modes inside Blender's undo handler: do it right after.
    bpy.app.timers.register(_later, first_interval=0.0)
    log.warn(NATIVE_UNDO_TEXT)
    try:
        from .selfcheck import flash_status
        flash_status("Hindsight: " + NATIVE_UNDO_TEXT, 10.0)
    except Exception:
        pass


def _revert(obj, st: MeshState, step: Step) -> None:
    if step.kind == DELTA:
        _write_state(obj, st, {d.key for d in step.deltas} | set(st.removed))
    else:
        _write_state(obj, st, {"__TOPO__"})


def _ask_truncate(n: int) -> None:
    if bpy.app.background:
        return
    try:
        wm = bpy.context.window_manager
        win = wm.windows[0]
        area = next((a for a in win.screen.areas if a.type == "VIEW_3D"), None)
        region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.ops.hindsight.confirm_truncate("INVOKE_DEFAULT", count=n)
    except Exception as e:
        log.error_once(("truncate_dialog", str(e)), f"could not show the new-branch dialog: {e}", e)


def confirm_truncate(obj) -> int:
    """User confirmed: drop the future steps. They then repeat their stroke."""
    h = history_of(obj)
    uid = obj_uid(obj)
    _PENDING_TRUNCATE.pop(uid, None)
    if h is None:
        return 0
    at = h.cursor
    n = len(h.truncate_future())
    if n:
        _journal("TRUNC", {"hist": h.uid, "keep": at})
    tag_redraw_all()
    return n


_ENFORCE_COUNT = [0]
# Memory accounting: "estimate" is an upper bound of the history size (new steps are added at their
# unpacked size), so the exact (slower) check only runs when the limit might be crossed.
MEMORY = {"estimate": 0, "evicted": 0, "warned": False}
MEMORY_TEXT = ("Memory limit reached: the oldest steps are being dropped to stay under {limit} MB. "
               "Raise the limit in Preferences if you want to keep more")


def _note_eviction(n: int) -> None:
    MEMORY["evicted"] += n
    if not MEMORY["warned"]:
        MEMORY["warned"] = True
        text = MEMORY_TEXT.format(limit=int(SETTINGS.memory_limit_mb))
        log.warn(text)
        try:
            from .selfcheck import flash_status
            flash_status("Hindsight: " + text, 15.0)
        except Exception:
            pass


def _enforce(h=None) -> None:
    """Keep caps and tiers. Incremental per commit (O(1)); full rescans every 64 commits or when
    the memory estimate crosses the limit, so recording cost does not grow with history length."""
    try:
        from ..core import store
        _ENFORCE_COUNT[0] += 1
        if h is not None and h.entries:
            MEMORY["estimate"] += h.entries[-1].nbytes()
        over_limit = MEMORY["estimate"] > int(SETTINGS.memory_limit_mb) * (1 << 20)
        if h is not None and _ENFORCE_COUNT[0] % 64 and not over_limit:
            excess = len(h.entries) - SETTINGS.max_steps
            if excess > 0:
                h.drop_oldest(excess)
            k = len(h.entries) - max(0, SETTINGS.raw_steps) - 1
            if k >= 0 and not h.entries[k].is_packed():
                if packer is not None:
                    packer.submit(h.entries[k], SETTINGS.compression)
                else:
                    h.entries[k].pack(SETTINGS.compression)
            return
        hs = _REG.all()
        stats = {}
        store.enforce_caps(hs, SETTINGS, stats)
        if stats.get("memory_dropped"):
            _note_eviction(int(stats["memory_dropped"]))
        store.enforce_tiers(hs, SETTINGS, packer)
        MEMORY["estimate"] = store.total_bytes(hs)
    except Exception as e:
        log.error_once(("store", str(e)), f"history store error: {e}", e)


# ----------------------------------------------------------------------------- mode tracking
def _active():
    try:
        return bpy.context.view_layer.objects.active
    except Exception:
        return None


def _enter(obj) -> None:
    uid = obj_uid(obj, create=True)
    _WROTE.discard(uid)      # entering a mode starts a fresh Blender undo context for it
    h = _REG.history_for(uid)
    st = _STATES.get(uid)
    if _mesh_owned_elsewhere(obj):
        # D39: a linked duplicate's mesh is recorded by its owner; this object starts from the mesh
        # as it is (nothing done through the owner becomes a step here)
        ensure_state(obj, refresh=obj.mode != "EDIT")
        return
    if obj.mode == "EDIT":
        if st is None or not st.counts:
            ensure_state(obj)
        elif h is not None and h.enabled and edit_steps_on(obj):
            # Edit Mode step by step: changes made outside the history are their own step first,
            # so the first Edit Mode step holds only that operation.
            commit(obj, label="Changes Outside History", mode="OBJECT", full=True, _edit=True)
        return
    if h is not None and st is not None and h.enabled and uid not in _REG.disabled:
        # Anything changed while the object was not being tracked becomes one step.
        commit(obj, label="Changes Outside History", mode=obj.mode, full=True)
    else:
        ensure_state(obj, refresh=True)


def check_mode_transition() -> None:
    if _SUSPEND[0]:
        return
    obj = _active()
    name = obj.name if obj is not None else None
    mode = obj.mode if obj is not None else None
    if _TRACK["name"] == name and _TRACK["mode"] == mode:
        return
    prev = find_object(_TRACK["uid"]) if _TRACK["uid"] else None
    pmode = _TRACK["mode"]
    _TRACK.update(name=name, uid=obj_uid(obj) if obj is not None else None, mode=mode)
    if prev is not None and pmode == "EDIT" and prev.mode != "EDIT":
        if _mesh_owned_elsewhere(prev):
            ensure_state(prev, refresh=True)    # D39: the mesh's owner records this visit
        else:
            commit(prev, label="Edit Mode", mode="EDIT", full=True)
    elif prev is not None and pmode in RECORD_MODES and obj_uid(prev) in _DIRTY:
        commit(prev, mode=pmode)
    if obj is not None and obj.type == "MESH" and mode in TRACK_MODES and not is_excluded(obj):
        _enter(obj)
        _TRACK["uid"] = obj_uid(obj)


@persistent
@guard.guarded("recorder")
def on_depsgraph(scene, depsgraph=None) -> None:
    if _SUSPEND[0] or depsgraph is None:
        return
    guard.mark_ok("recorder")
    try:
        from . import datarec
        datarec.on_update(depsgraph)
    except Exception as e:
        log.error_once(("datarec_update", str(e)), f"data recorder error: {e}", e)
    obj = _active()
    if obj is None or obj.type != "MESH":
        check_mode_transition()
        return
    if obj.mode in RECORD_MODES:
        data = obj.data
        for u in depsgraph.updates:
            idv = u.id
            orig = getattr(idv, "original", idv)
            if orig == obj or orig == data:
                uid = obj_uid(obj)
                if uid is not None:
                    _DIRTY[uid] = time.time()
                    if stroke_running():
                        _STROKE_SEEN.add(uid)
                break
    check_mode_transition()


@guard.guarded("recorder")
def _tick_once() -> None:
    check_mode_transition()
    if _DIRTY and stroke_running():
        return   # never split a stroke, even if a slow modifier stack pauses its updates
    now = time.time()
    for uid, t in list(_DIRTY.items()):
        # A stroke that has finished is committed at once, so quick strokes stay separate steps.
        # Changes without a stroke operator (filters run from a menu, scripts) wait for the
        # updates to settle.
        if uid in _STROKE_SEEN or now - t >= DEBOUNCE:
            ob = find_object(uid)
            if ob is None or ob.mode not in RECORD_MODES:
                _DIRTY.pop(uid, None)
                _STROKE_SEEN.discard(uid)
                continue
            commit(ob)


def tick() -> float:
    """Commits each stroke as it ends (or once updates settle). Never raises."""
    busy = False
    try:
        _tick_once()
        ensure_handlers()
    except Exception as e:
        log.error_once(("tick", str(e)), f"timer error: {e}", e)
    try:
        from . import datarec
        datarec.tick()
        busy = bool(datarec._DIRTY)
    except Exception as e:
        log.error_once(("datarec_tick", str(e)), f"data recorder error: {e}", e)
    return 0.02 if (_DIRTY or busy) else 0.1


UNDO_DIRECTION = ["undo"]      # which way Blender's last undo or redo went (see commit, datarec.on_undo)


@persistent
@guard.guarded("undo_sync")
def on_undo(*_a) -> None:
    """After Blender's undo."""
    UNDO_DIRECTION[0] = "undo"
    _after_native_undo()


@persistent
@guard.guarded("undo_sync")
def on_redo(*_a) -> None:
    """After Blender's redo."""
    UNDO_DIRECTION[0] = "redo"
    _after_native_undo()


def _after_native_undo() -> None:
    """Compare the mesh with our state (commit() detects the match)."""
    _UID_CACHE.clear()
    _NATIVE_PENDING.update(_STATES.keys())   # objects in other modes are checked on their next commit
    obj = _active()
    if obj is not None and obj.type == "MESH" and obj.mode in RECORD_MODES:
        if obj_uid(obj) in _STATES:
            commit(obj)
    elif obj is not None and obj.type == "MESH" and obj.mode == "EDIT" and edit_steps_on(obj):
        if obj_uid(obj) in _STATES:
            commit_edit(obj, "Edit")
    try:
        from . import datarec
        datarec.on_undo(UNDO_DIRECTION[0])
    except Exception as e:
        log.error_once(("datarec_undo", str(e)), f"data recorder error after undo: {e}", e)


_HANDLERS = (("depsgraph_update_post", on_depsgraph), ("undo_post", on_undo), ("redo_post", on_redo))


def ensure_handlers() -> list:
    """Re-install any recorder handler that went missing (another add-on or script removed it).
    Returns the names that had to be re-added."""
    added = []
    for name, fn in _HANDLERS:
        hl = getattr(bpy.app.handlers, name)
        if fn not in hl:
            hl.append(fn)
            added.append(name)
    return added


def handlers_installed() -> dict:
    return {name: fn in getattr(bpy.app.handlers, name) for name, fn in _HANDLERS}


@persistent
def on_load_post(*_a) -> None:
    added = ensure_handlers()
    if added:
        log.warn("recorder handlers were missing after loading a file and were re-installed: "
                 + ", ".join(added))


# ----------------------------------------------------------------------------- playback
def _join_hook(step, forward, states):
    try:
        from . import joinsep
        return joinsep.join_hook(step, forward, states)
    except ImportError:
        st = states.get(step.target)
        if st is not None:
            st.set_snapshot(step.after if forward else step.before)
        return None


def _find_any(uid):
    """An object by uid, or any other recorded datablock (a scene, ...)."""
    ob = find_object(uid)
    if ob is not None:
        return ob
    try:
        from . import idmap
        return idmap.find(uid)
    except Exception:
        return None


def _goto_history(h, pos: int, final: bool, acc: dict | None = None) -> dict:
    """Move one history to pos and write what changed to Blender. With acc (a global jump), the
    move is noted in acc instead of being pushed onto Blender's undo stack on its own."""
    from . import datarec
    states = {}
    for m in list(h.members):
        ob = find_object(m)
        if ob is None:
            other = _find_any(m)
            if other is not None:
                states[m] = datarec.baseline(other, m)
            continue
        if ob is not None:
            if ob.mode == "EDIT":
                with suspended():
                    bpy.ops.object.mode_set(mode="OBJECT")
                commit(ob, label="Edit Mode", mode="EDIT", full=True)
                with suspended():
                    bpy.ops.object.mode_set(mode="EDIT")
            states[m] = ensure_state(ob)
    tracked = [m for m in h.members if m in datarec.tracked_uids()]
    vb = {m: dict(states[m].values) for m in tracked if m in states}
    before = h.cursor
    changes = h.goto(pos, states, join_hook=_join_hook)
    wrote_data = False
    # A jump that may go onto Blender's undo stack: note each recorded mesh before it's written, so
    # Blender's undo of the jump can be told apart from anything else (datarec.on_undo)
    pushing = final and not IN_HANDLER[0] and datarec.can_push()
    meshes = acc["meshes"] if acc is not None else {}
    for target, keys in changes.items():
        ob = _find_any(target)
        st = states.get(target) or _STATES.get(target)
        if ob is None or st is None:
            continue
        mesh_keys, data_keys = datarec.split_keys(keys)
        if not isinstance(ob, bpy.types.Object):
            if data_keys:
                datarec.write(ob, st, data_keys)
                wrote_data = True
            continue
        if data_keys:
            datarec.write(ob, st, data_keys)
            wrote_data = True
        if not mesh_keys:
            continue
        reenter_edit = ob.mode == "EDIT"
        if reenter_edit:
            with suspended():
                bpy.ops.object.mode_set(mode="OBJECT")
        fp = pushing and ob.mode == "OBJECT" and ob.type == "MESH" and target in datarec.tracked_uids()
        if fp and target not in meshes:
            meshes[target] = [mesh_fingerprint(ob), None]
        _write_state(ob, st, mesh_keys, final)
        if fp:
            meshes[target][1] = mesh_fingerprint(ob)
        if reenter_edit:
            with suspended():
                bpy.ops.object.mode_set(mode="EDIT")
    ob = find_object(h.members[0]) if h.members else None
    object_mode = ob is None or ob.mode == "OBJECT"
    va = {m: dict(states[m].values) for m in tracked if m in states}
    if acc is not None:
        entry = acc["hists"].setdefault(h.uid, [before, h.cursor, len(h.entries)])
        entry[1] = h.cursor
        for k, v in vb.items():
            acc["vb"].setdefault(k, v)
        acc["va"].update(va)
        acc["wrote"] = acc["wrote"] or wrote_data or bool(meshes)
        acc["object_mode"] = acc["object_mode"] and object_mode
    elif (wrote_data or meshes) and final and not IN_HANDLER[0] and object_mode:
        if datarec.push_undo():
            datarec.note_jump({h.uid: [before, h.cursor, len(h.entries)]}, vb, va, meshes)
    if acc is None:
        datarec.resolve_missing_refs()
    _journal("CURSOR", {"hist": h.uid, "cursor": h.cursor})
    return changes


# Modal operators that edit the mesh. A jump (which may switch modes for topology steps) must
# never run while one of these is active: Blender 5.1 crashes in SculptPaintStroke::done if the
# object leaves Sculpt Mode mid-stroke (reproduced without Hindsight, see DECISIONS.md).
_STROKE_OPS = frozenset({
    "SCULPT_OT_brush_stroke", "PAINT_OT_vertex_paint", "PAINT_OT_weight_paint", "SCULPT_OT_mesh_filter",
    "SCULPT_OT_cloth_filter", "SCULPT_OT_color_filter", "HINDSIGHT_OT_morph_stroke",
})


def stroke_running() -> bool:
    try:
        for win in bpy.context.window_manager.windows:
            for op in getattr(win, "modal_operators", ()):
                if op.bl_idname in _STROKE_OPS:
                    return True
    except Exception:
        pass
    return False


def goto(context, pos: int, final: bool = True) -> None:
    if _movie_locked(context) and not getattr(goto, "_movie", False):
        return
    if stroke_running():
        log.warn("history jump skipped: a stroke is still in progress")
        return
    if use_global(context):
        global_goto(context, pos, final)
    else:
        h = history_of(_ctx_active(context))
        if h is None:
            return
        held = _leave_edit_for_push(context, final)
        _goto_history(h, pos, final)
        _reenter_edit(context, held)
    tag_redraw_all()


def _leave_edit_for_push(context, final: bool):
    """A jump that goes onto Blender's undo stack is pushed in Object Mode: in Edit Mode the push
    would keep only the edit mesh, and Blender's undo would later put back stale data for every
    other object. The edit is kept as a step first (as when a jump leaves Edit Mode). Returns the
    object to put back into Edit Mode after the push, or None."""
    from . import datarec
    ob = _ctx_active(context)
    if not final or IN_HANDLER[0] or not datarec.can_push() or ob is None or ob.mode != "EDIT":
        return None
    if not any(datarec.is_tracked(o) for o in context.view_layer.objects if o.mode == "EDIT"):
        return None
    with suspended():
        bpy.ops.object.mode_set(mode="OBJECT")
    commit(ob, label="Edit Mode", mode="EDIT", full=True)
    return ob.name


def _reenter_edit(context, name) -> None:
    if name is None:
        return
    ob = bpy.data.objects.get(name)
    if ob is None or ob.type != "MESH" or ob.mode != "OBJECT" or context.view_layer.objects.active != ob:
        return
    with suspended():
        try:
            bpy.ops.object.mode_set(mode="EDIT")
        except Exception as e:
            log.error_once(("reenter_edit", str(e)), f"could not go back to Edit Mode after a jump: {e}", e)


def mesh_fingerprint(ob) -> str:
    """meshio.fingerprint of an object's mesh, also in Edit Mode (read from a copy)."""
    if ob.mode != "EDIT":
        return meshio.fingerprint(ob.data)
    with suspended():
        ob.update_from_editmode()
        tmp = ob.data.copy()
    try:
        return meshio.fingerprint(tmp)
    finally:
        with suspended():
            bpy.data.meshes.remove(tmp)


def global_goto(context, g: int, final: bool = True) -> None:
    """Move the global timeline to position g. Steps are undone and redone across histories in
    step-number order (globaltl.transition), and the whole jump is one entry on Blender's undo."""
    from . import datarec
    vis = _visible_uids()
    old = globaltl.global_position(_REG, vis)
    acc = {"hists": {}, "vb": {}, "va": {}, "meshes": {}, "wrote": False, "object_mode": True}
    held = _leave_edit_for_push(context, final) if g != old else None
    for hid, cur in globaltl.transition(_REG, g, vis):
        h = _REG.histories.get(hid)
        if h is not None and h.cursor != cur:
            _goto_history(h, cur, final, acc)
    datarec.resolve_missing_refs()          # D37: objects brought back get their references back
    new_g = globaltl.global_position(_REG, vis)
    if acc["wrote"] and final and not IN_HANDLER[0] and acc["object_mode"]:
        if datarec.push_undo():
            datarec.note_jump(acc["hists"], acc["vb"], acc["va"], acc["meshes"])
    _reenter_edit(context, held)
    p = _scene_props(context)
    preserve = bool(p.preserve_modes) if p is not None else _REG.preserve_modes
    if preserve and final and new_g != old:
        piv = globaltl.pivot_entry(_REG, old, new_g, vis)
        if piv is not None:
            h = _REG.histories.get(piv[0])
            e = h.entries[piv[1]] if h else None
            if e is not None:
                tgt = find_object(e.target) or _find_any(e.target)
                if isinstance(tgt, bpy.types.Object):
                    _switch_to(context, tgt, e.meta.get("mode", "SCULPT"))


def _switch_to(context, ob, mode: str) -> None:
    if ob is None:
        return
    vl = context.view_layer
    cur = vl.objects.active
    with suspended():
        try:
            if cur is not None and cur != ob and cur.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            if cur != ob:
                for o in context.selected_objects:
                    o.select_set(False)
                vl.objects.active = ob
                ob.select_set(True)
            if mode in ("SCULPT", "VERTEX_PAINT", "EDIT") and ob.mode != mode:
                bpy.ops.object.mode_set(mode=mode)
        except Exception as e:
            log.error_once(("preserve", str(e)), f"Follow object and mode could not switch objects: {e}", e)
    _TRACK.update(name=ob.name, uid=obj_uid(ob), mode=ob.mode)
    if ob.mode in TRACK_MODES:
        ensure_state(ob)


def scrub_info(context):
    try:
        locked = _movie_locked(context)
        if use_global(context):
            vis = _visible_uids()
            n = globaltl.global_length(_REG, vis)
            if not n:
                return None
            return {"pos": globaltl.global_position(_REG, vis), "len": n,
                    "cap": SETTINGS.max_steps, "global": True, "locked": locked, "morph_target": None}
        obj = _ctx_active(context)
        h = history_of(obj)
        if h is None:
            return None
        return {"pos": h.cursor, "len": len(h.entries), "cap": SETTINGS.max_steps, "global": False,
                "locked": locked, "morph_target": h.morph_target}
    except Exception:
        return None


def step_list(context, around: int = 12) -> list:
    """Steps near the cursor for the step list: [(position after the step, label, current)], newest
    first, plus the start (position 0). Uses the global timeline when global history is on."""
    out = []
    try:
        if use_global(context):
            vis = _visible_uids()
            groups = globaltl.global_groups(_REG, vis)
            cur = globaltl.global_position(_REG, vis)
            labels = []
            for grp in groups:                       # one position per operation
                _seq, hid, i = grp[0]
                h = _REG.histories.get(hid)
                labels.append(h.entries[i].meta.get("label", "Step") if h else "Step")
        else:
            h = history_of(_ctx_active(context))
            if h is None:
                return []
            cur = h.cursor
            labels = [e.meta.get("label", "Step") for e in h.entries]
        n = len(labels)
        lo, hi = max(0, cur - around), min(n, cur + around)
        for i in range(hi, lo, -1):
            out.append((i, labels[i - 1], i == cur))
        if lo == 0:
            out.append((0, "Start", cur == 0))
    except Exception:
        return []
    return out


def step(context, delta: int) -> bool:
    info = scrub_info(context)
    if info is None or info["locked"]:
        return False
    new = info["pos"] + delta
    if new < 0 or new > info["len"]:
        return False
    goto(context, new, final=True)
    return True


# ----------------------------------------------------------------------------- menu actions
def set_global(context, on: bool) -> None:
    p = _scene_props(context)
    if p is not None:
        p.use_global = on
    _REG.use_global = on
    tag_redraw_all()


def set_preserve_modes(context, on: bool) -> None:
    p = _scene_props(context)
    if p is not None:
        p.preserve_modes = on
    _REG.preserve_modes = on


def set_morph_target(context) -> None:
    h = history_of(_ctx_active(context))
    if h is not None:
        h.morph_target = h.cursor
        _journal("META", {"hist": h.uid, "members": list(h.members), "enabled": h.enabled,
                          "morph_target": h.morph_target})
    tag_redraw_all()


def delete_past(context, obj) -> int:
    h = history_of(obj)
    if h is None:
        return 0
    n = h.delete_past()
    if n:
        _journal("DROP", {"hist": h.uid, "n": n})
    tag_redraw_all()
    return n


def reset_history(context, obj) -> None:
    h = history_of(obj)
    if h is None:
        return
    h.reset()
    _journal("RESET", {"hist": h.uid})
    if obj.mode == "EDIT":
        return
    ensure_state(obj, refresh=True)
    tag_redraw_all()


def reset_all(context) -> None:
    for ob in bpy.data.objects:
        if _REG.history_for(obj_uid(ob) or "") is not None:
            reset_history(context, ob)


def turn_off(context, obj) -> None:
    uid = obj_uid(obj, create=True)
    if uid in _STATES and obj.mode in RECORD_MODES and uid in _DIRTY:
        commit(obj)
    h = _REG.history_for(uid)
    if h is None:
        h = _REG.create(uid)
    h.enabled = False
    _journal("META", {"hist": h.uid, "members": list(h.members), "enabled": False,
                      "morph_target": h.morph_target})
    tag_redraw_all()


def turn_on(context, obj) -> None:
    uid = obj_uid(obj, create=True)
    _REG.disabled.discard(uid)
    h = _REG.history_for(uid)
    if h is not None:
        h.enabled = True
        _journal("META", {"hist": h.uid, "members": list(h.members), "enabled": True,
                          "morph_target": h.morph_target})
        if uid in _STATES and obj.mode != "EDIT":
            # Changes made while off become the new starting point: one step bridging them.
            commit(obj, label="Changes While Off", full=True)
        else:
            ensure_state(obj, refresh=obj.mode != "EDIT")
    else:
        if obj.mode != "EDIT":
            ensure_state(obj, refresh=True)
    tag_redraw_all()


def reset_and_turn_off(context, obj) -> None:
    uid = obj_uid(obj, create=True)
    h = _REG.history_for(uid)
    if h is not None:
        h.reset()
        _journal("RESET", {"hist": h.uid})
        _REG.remove_object(uid)
    _REG.disabled.add(uid)
    _STATES.pop(uid, None)
    _DIRTY.pop(uid, None)
    tag_redraw_all()


def memory_stats() -> dict:
    total = 0
    steps = 0
    active_b = 0
    active_s = 0
    ah = None
    try:
        ah = history_of(_ctx_active(bpy.context))
    except Exception:
        pass
    for h in _REG.all():
        b = sum(e.nbytes() for e in h.entries)
        total += b
        steps += len(h.entries)
        if h is ah:
            active_b, active_s = b, len(h.entries)
    base = sum(sum(a.nbytes for a in st.arrays.values()) for st in _STATES.values())
    return {"total_bytes": total, "ram_bytes": total + base, "steps": steps,
            "objects": len(_REG.by_object), "active_bytes": active_b, "active_steps": active_s}


def warnings_for(context, obj) -> list:
    out = []
    if missing_message[0]:
        out.append(("ERROR", missing_message[0]))
    out.extend(load_notes)
    if MEMORY["evicted"]:
        out.append(("WARNING", MEMORY_TEXT.format(limit=int(SETTINGS.memory_limit_mb))
                    + f". Dropped so far: {MEMORY['evicted']} steps"))
    try:
        from .warnings import collect
        out.extend(collect(context, obj))
    except Exception:
        pass
    return out


# ----------------------------------------------------------------------------- register
def register() -> None:
    ensure_handlers()
    if on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load_post)
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
    global packer
    try:
        from ..core.store import Packer
        packer = Packer()
    except Exception as e:
        log.warn(f"background packing unavailable, packing inline: {e}")
        packer = None


def unregister() -> None:
    for name, fn in _HANDLERS + (("load_post", on_load_post),):
        hl = getattr(bpy.app.handlers, name)
        while fn in hl:
            hl.remove(fn)
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    global packer
    if packer is not None:
        try:
            packer.stop()
        except Exception:
            pass
        packer = None
