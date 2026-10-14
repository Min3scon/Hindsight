"""Hindsight runtime: recorder (stroke detection, capture), playback and the API used by the UI."""
from __future__ import annotations

import time

import bpy
import numpy as np

from ..core import globaltl
from ..core.history import MeshState
from ..core.registry import Registry, new_uid
from ..core.types import (
    SETTINGS, Step, DELTA, TOPO, CHANNEL_TYPES, split_key, key_width,
)
from . import meshio

UID_PROP = "_hindsight_uid"
RECORD_MODES = ("SCULPT", "VERTEX_PAINT")
TRACK_MODES = ("SCULPT", "VERTEX_PAINT", "EDIT")
DEBOUNCE = 0.15

_REG = Registry()
_STATES: dict = {}          # object uid -> MeshState (what the mesh shows at the cursor)
_DIRTY: dict = {}           # object uid -> time of last geometry update
_TRACK = {"name": None, "uid": None, "mode": None}
_SUSPEND = [0]
IN_HANDLER = [False]          # True while Movie Mode applies a step from a frame-change handler
_LAST_UNDO_MATCH = [0.0]
_UID_CACHE: dict = {}
_PENDING_TRUNCATE: dict = {}  # object uid -> number of future steps awaiting confirmation
missing_message = [None]      # "Undo History Not Found" text, set by persistence

journal_hook = None           # persistence sets: fn(rtype: str, header: dict, payload_fn or None)
packer = None


# ----------------------------------------------------------------------------- basics
def reg() -> Registry:
    return _REG


def set_registry(r: Registry) -> None:
    global _REG
    _REG = r
    _STATES.clear()
    _DIRTY.clear()
    _UID_CACHE.clear()
    _TRACK.update(name=None, uid=None, mode=None)


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
            print("Hindsight journal error:", e)


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
    return u


def find_object(uid: str):
    if uid is None:
        return None
    name = _UID_CACHE.get(uid)
    if name is not None:
        ob = bpy.data.objects.get(name)
        if ob is not None and ob.get(UID_PROP) == uid:
            return ob
    for ob in bpy.data.objects:
        if ob.type == "MESH" and ob.get(UID_PROP) == uid:
            _UID_CACHE[uid] = ob.name
            return ob
    return None


def is_excluded(obj) -> bool:
    try:
        from .filtering import is_excluded as _ex
        return bool(_ex(obj, bpy.context.scene))
    except Exception:
        return False


def _blocking(obj) -> bool:
    try:
        from .warnings import blocking
        return bool(blocking(bpy.context, obj))
    except Exception:
        return False


def history_of(obj):
    uid = obj_uid(obj)
    if uid is None or is_excluded(obj):
        return None
    return _REG.history_for(uid)


def is_recording(obj) -> bool:
    uid = obj_uid(obj)
    if uid is None or uid in _REG.disabled:
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


def ensure_state(obj, refresh: bool = False) -> MeshState:
    uid = obj_uid(obj, create=True)
    st = _STATES.get(uid)
    if obj.mode == "EDIT" and st is not None:
        return st  # mesh arrays are not readable in Edit Mode; keep the pre-edit baseline
    if st is None or refresh:
        snap = meshio.read_snapshot(obj.data)
        st = MeshState(snap.counts, snap.arrays)
        _STATES[uid] = st
    return st


def _visible_uids() -> set:
    out = set()
    for ob in bpy.data.objects:
        u = obj_uid(ob)
        if u is not None and not is_excluded(ob):
            out.add(u)
    return out


def prime_states() -> None:
    """Read a baseline for every object that has a history (after loading a file)."""
    for ob in bpy.data.objects:
        u = obj_uid(ob)
        if u is not None and _REG.history_for(u) is not None and ob.mode != "EDIT":
            ensure_state(ob, refresh=True)


# ----------------------------------------------------------------------------- recording
def _deltas_equal(a: Step, b: Step) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind == DELTA:
        if len(a.deltas) != len(b.deltas):
            return False
        bd = {d.key: d for d in b.deltas}
        for d in a.deltas:
            e = bd.get(d.key)
            if e is None or not np.array_equal(d.idx, e.idx) or not np.array_equal(d.xor, e.xor):
                return False
        return True
    return False


def _inverse_matches(new: Step, old: Step) -> bool:
    """True if `new` (observed change) is exactly `old` undone (or redone, XOR is symmetric)."""
    if new.kind == DELTA:
        return _deltas_equal(new, old)
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


def _observe(obj, st: MeshState, full: bool, label: str, mode: str):
    """Diff the mesh against the state. Returns an unsaved Step (seq=0) or None."""
    from ..core import delta as D
    me = obj.data
    uid = obj_uid(obj)
    c = meshio.counts(me)
    if c != st.counts or (full and not meshio.topology_equal(me, st.counts, st.arrays, deep=True)):
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


def commit(obj, label: str | None = None, mode: str | None = None, full: bool = False):
    """Record what changed on obj since the last commit as ONE step. Returns the Step or None."""
    if obj is None or obj.type != "MESH":
        return None
    uid = obj_uid(obj, create=True)
    _DIRTY.pop(uid, None)
    if obj.mode == "EDIT":
        return None  # Edit Mode changes are grouped and recorded when leaving Edit Mode
    if uid in _REG.disabled or is_excluded(obj):
        return None
    mode = mode or obj.mode
    st = _STATES.get(uid)
    if st is None:
        ensure_state(obj)
        return None
    if _blocking(obj):
        ensure_state(obj, refresh=True)
        return None
    h = _REG.history_for(uid)
    if h is not None and not h.enabled:
        return None
    if label is None:
        label = {"SCULPT": "Sculpt", "VERTEX_PAINT": "Vertex Paint", "EDIT": "Edit Mode"}.get(mode, "Change")
        try:
            ops = bpy.context.window_manager.operators
            if len(ops):
                label = ops[-1].bl_label or label
        except Exception:
            pass
    step = _observe(obj, st, full, label, mode)
    if step is None:
        return None
    if h is not None:
        # Native undo / redo / F9 redo moved the mesh onto a neighbouring recorded state.
        if h.cursor > 0 and _inverse_matches(step, h.entries[h.cursor - 1].load()):
            h.cursor -= 1
            _apply_to_state(st, step)
            _LAST_UNDO_MATCH[0] = time.time()
            _journal("CURSOR", {"hist": h.uid, "cursor": h.cursor})
            tag_redraw_all()
            return None
        if h.cursor < len(h.entries) and _inverse_matches(step, h.entries[h.cursor].load()):
            h.cursor += 1
            _apply_to_state(st, step)
            _journal("CURSOR", {"hist": h.uid, "cursor": h.cursor})
            tag_redraw_all()
            return None
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
    _journal("STEP", {"hist": h.uid, "at": at}, lambda e=entry: e.to_bytes(SETTINGS.compression))
    _enforce()
    tag_redraw_all()
    return step


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
        print("Hindsight: could not show truncation dialog:", e)


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


def _enforce() -> None:
    try:
        from ..core import store
        hs = _REG.all()
        store.enforce_caps(hs, SETTINGS)
        store.enforce_tiers(hs, SETTINGS, packer)
    except Exception as e:
        print("Hindsight: store error:", e)


# ----------------------------------------------------------------------------- mode tracking
def _active():
    try:
        return bpy.context.view_layer.objects.active
    except Exception:
        return None


def _enter(obj) -> None:
    uid = obj_uid(obj, create=True)
    h = _REG.history_for(uid)
    st = _STATES.get(uid)
    if obj.mode == "EDIT":
        if st is None:
            ensure_state(obj)
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
        commit(prev, label="Edit Mode", mode="EDIT", full=True)
    elif prev is not None and pmode in RECORD_MODES and obj_uid(prev) in _DIRTY:
        commit(prev, mode=pmode)
    if obj is not None and obj.type == "MESH" and mode in TRACK_MODES and not is_excluded(obj):
        _enter(obj)
        _TRACK["uid"] = obj_uid(obj)


def on_depsgraph(scene, depsgraph) -> None:
    if _SUSPEND[0]:
        return
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
                break
    check_mode_transition()


def tick() -> float:
    try:
        check_mode_transition()
        now = time.time()
        for uid, t in list(_DIRTY.items()):
            if now - t >= DEBOUNCE:
                ob = find_object(uid)
                if ob is None or ob.mode not in RECORD_MODES:
                    _DIRTY.pop(uid, None)
                    continue
                commit(ob)
    except Exception as e:
        print("Hindsight tick error:", e)
    return 0.1


def on_undo(*_a) -> None:
    """After native undo/redo: compare the mesh with our state (commit() detects the match)."""
    _UID_CACHE.clear()
    obj = _active()
    if obj is not None and obj.type == "MESH" and obj.mode in RECORD_MODES:
        if obj_uid(obj) in _STATES:
            commit(obj)


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


def _goto_history(h, pos: int, final: bool) -> dict:
    states = {}
    for m in list(h.members):
        ob = find_object(m)
        if ob is not None:
            if ob.mode == "EDIT":
                with suspended():
                    bpy.ops.object.mode_set(mode="OBJECT")
                commit(ob, label="Edit Mode", mode="EDIT", full=True)
                with suspended():
                    bpy.ops.object.mode_set(mode="EDIT")
            states[m] = ensure_state(ob)
    changes = h.goto(pos, states, join_hook=_join_hook)
    for target, keys in changes.items():
        ob = find_object(target)
        st = states.get(target) or _STATES.get(target)
        if ob is None or st is None:
            continue
        reenter_edit = ob.mode == "EDIT"
        if reenter_edit:
            with suspended():
                bpy.ops.object.mode_set(mode="OBJECT")
        _write_state(ob, st, keys, final)
        if reenter_edit:
            with suspended():
                bpy.ops.object.mode_set(mode="EDIT")
    _journal("CURSOR", {"hist": h.uid, "cursor": h.cursor})
    return changes


def goto(context, pos: int, final: bool = True) -> None:
    if _movie_locked(context) and not getattr(goto, "_movie", False):
        return
    if use_global(context):
        global_goto(context, pos, final)
    else:
        h = history_of(context.active_object)
        if h is None:
            return
        _goto_history(h, pos, final)
    tag_redraw_all()


def global_goto(context, g: int, final: bool = True) -> None:
    vis = _visible_uids()
    old = globaltl.global_position(_REG, vis)
    plan = globaltl.plan_global_goto(_REG, g, vis)
    new_g = sum(plan.values())
    for hid, cur in plan.items():
        h = _REG.histories.get(hid)
        if h is not None and h.cursor != cur:
            _goto_history(h, cur, final)
    p = _scene_props(context)
    preserve = bool(p.preserve_modes) if p is not None else _REG.preserve_modes
    if preserve and final and new_g != old:
        piv = globaltl.pivot_entry(_REG, old, new_g, vis)
        if piv is not None:
            h = _REG.histories.get(piv[0])
            e = h.entries[piv[1]] if h else None
            if e is not None:
                _switch_to(context, find_object(e.target), e.meta.get("mode", "SCULPT"))


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
            print("Hindsight: preserve modes switch failed:", e)
    _TRACK.update(name=ob.name, uid=obj_uid(ob), mode=ob.mode)
    if ob.mode in TRACK_MODES:
        ensure_state(ob)


def scrub_info(context):
    try:
        locked = _movie_locked(context)
        if use_global(context):
            vis = _visible_uids()
            order = globaltl.global_order(_REG, vis)
            if not order:
                return None
            return {"pos": globaltl.global_position(_REG, vis), "len": len(order),
                    "cap": SETTINGS.max_steps, "global": True, "locked": locked, "morph_target": None}
        obj = context.active_object
        h = history_of(obj)
        if h is None:
            return None
        return {"pos": h.cursor, "len": len(h.entries), "cap": SETTINGS.max_steps, "global": False,
                "locked": locked, "morph_target": h.morph_target}
    except Exception:
        return None


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
    h = history_of(context.active_object)
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
        ah = history_of(bpy.context.active_object)
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
    try:
        from .warnings import collect
        out.extend(collect(context, obj))
    except Exception:
        pass
    return out


# ----------------------------------------------------------------------------- register
def register() -> None:
    if on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(on_depsgraph)
    for hl in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if on_undo not in hl:
            hl.append(on_undo)
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
    global packer
    try:
        from ..core.store import Packer
        packer = Packer()
    except Exception:
        packer = None


def unregister() -> None:
    for hl in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.undo_post,
               bpy.app.handlers.redo_post):
        for fn in (on_depsgraph, on_undo):
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
