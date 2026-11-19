"""Hindsight runtime: recorder (stroke detection, capture), playback and the API used by the UI."""
from __future__ import annotations

import os
import time

import bpy
import numpy as np
from bpy.app.handlers import persistent

from ..core import globaltl
from ..core.history import MeshState
from ..core.registry import Registry, new_uid
from ..core.types import (
    JOURNAL_CODEC, SETTINGS, Snapshot, Step, DELTA, TOPO, CHANNEL_TYPES, split_key, key_width, key_dtype,
)
from . import filtering, guard, log, meshio, multires, rigmark
from . import warnings as _warn
from .ctx import active_object as _ctx_active

UID_PROP = "_hindsight_uid"
RECORD_MODES = ("SCULPT", "VERTEX_PAINT", "WEIGHT_PAINT", "TEXTURE_PAINT")
TRACK_MODES = ("SCULPT", "VERTEX_PAINT", "WEIGHT_PAINT", "TEXTURE_PAINT", "EDIT")
DEBOUNCE = 0.15
FORCE_TEXTURE = [None]      # tests: True or False when the preferences aren't registered


def texture_on() -> bool:
    """Record texture painting (preference, off by default)."""
    if FORCE_TEXTURE[0] is not None:
        return FORCE_TEXTURE[0]
    try:
        from .prefs import get_prefs
        p = get_prefs()
        return p is not None and bool(getattr(p, "record_texture_painting", False))
    except Exception:
        return False


FORCE_DYNTOPO = [None]     # tests: True or False when the preferences aren't registered
DYNTOPO_TRIS_MAX = 200_000  # D145: a Dyntopo stroke's step holds the whole mesh (LIMITS-A 1.5: about 250 ms
                            # per stroke at 100k triangles, 1.4 s and 32 to 64 MB at 1M)


def dyntopo_on() -> bool:
    """Record Dyntopo (preference, off by default)."""
    if FORCE_DYNTOPO[0] is not None:
        return FORCE_DYNTOPO[0]
    try:
        from .prefs import get_prefs
        p = get_prefs()
        return p is not None and bool(getattr(p, "record_dyntopo", False))
    except Exception:
        return False


def dyntopo_active(obj) -> bool:
    """obj is in Sculpt Mode with Dyntopo on (the flag reads False outside Sculpt Mode)."""
    return getattr(obj, "mode", None) == "SCULPT" and bool(getattr(obj, "use_dynamic_topology_sculpting", False))


def dyntopo_recorded(obj) -> bool:
    """D145: Dyntopo strokes are steps: Record Dyntopo is on and the mesh (as last written back) has at
    most DYNTOPO_TRIS_MAX faces. Otherwise Dyntopo pauses recording, as before."""
    return (dyntopo_active(obj) and dyntopo_on() and obj.data is not None
            and len(obj.data.polygons) <= DYNTOPO_TRIS_MAX)


_DYN_LAST: dict = {}     # uid -> Dyntopo was on when the object's state was last read or committed
_DYN_WANT: dict = {}     # uid -> Dyntopo on or off after the jump being written (_dyn_at)


def _dyn_at(h, pos: int) -> bool:
    """Whether Dyntopo was on at history position pos: each step committed with Dyntopo on (or just
    turned off) holds extra["dyn"] = [before, after] (D145); no record means off."""
    try:
        if pos > 0:
            d = (h.entries[pos - 1].meta.get("extra") or {}).get("dyn")
            return bool(d and d[1])
        if h.entries:
            d = (h.entries[0].meta.get("extra") or {}).get("dyn")
            return bool(d and d[0])
    except Exception:
        pass
    return False


def _flush_dyntopo(obj) -> bool:
    """Write Dyntopo's own mesh (a BMesh while sculpting) back to obj.data, so it can be read."""
    try:
        with suspended():
            bpy.ops.ed.flush_edits()
        return True
    except Exception as e:
        log.error_once(("dyntopo_flush", str(e)), f"could not read the Dyntopo mesh: {e}", e)
        return False


def _mode_recorded(mode) -> bool:
    return mode in RECORD_MODES and (mode != "TEXTURE_PAINT" or texture_on())

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
NATIVE_UNDO_EDIT_TEXT = ("Blender's own undo went to an Edit Mode state that isn't next to this object's "
                         "place in its history (with Global Undo off a history jump isn't on Blender's "
                         "undo), so it was reversed. Use the slider")


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
    _GEN[0] += 1
    if _CAPS_MEMO is not None:
        _CAPS_MEMO.clear()
    _STATES.clear()
    _DIRTY.clear()
    _STROKE_SEEN.clear()
    _WROTE.clear()
    _UID_CACHE.clear()
    _NATIVE_PENDING.clear()
    WEIGHTS_CLAMPED.clear()
    _TRACK.update(name=None, uid=None, mode=None)
    try:
        from . import datarec, idmap
        datarec.forget()
        idmap.forget(session=True)
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
    if u is not None:
        from . import idmap
        idmap.note(obj, u)
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
            idmap.unnote(ob)
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
    """Not recorded: excluded by Collection Filtering, or one of Hindsight's own Movie Mode objects."""
    try:                  # modules imported once: this runs for every object on every redraw
        if rigmark.is_rig(obj):
            return True
        return bool(filtering.is_excluded(obj, bpy.context.scene))
    except Exception:
        return False


def blocking_reason(obj):
    try:
        return _warn.blocking_reason(bpy.context, obj)
    except Exception:
        return None


def _blocking(obj) -> bool:
    try:
        return bool(_warn.blocking(bpy.context, obj))
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


def extras_on(obj) -> bool:
    """D43: vertex group weights and shape keys are part of the mesh state of objects recorded with
    Record everything."""
    try:
        from . import datarec
        return datarec.is_tracked(obj)
    except Exception:
        return False


EXTRAS_TOPO_MAX = 300_000    # D106: up to this many vertices, topology steps keep weights and shapes
_SHAPES_STALE: set = set()   # D106: uids whose kept shape keys may be older than the mesh's


def topo_extras_on(obj) -> bool:
    """D106: an object not recorded with Record everything whose mesh has vertex groups or shape keys:
    its topology steps (remesh, decimate, ...) carry the weights and shape keys from before and after,
    so going back over one gives them back. Strokes don't (they don't change them)."""
    if extras_on(obj):
        return False
    me = getattr(obj, "data", None)
    if me is None or getattr(obj, "type", None) != "MESH":
        return False
    if not (len(getattr(obj, "vertex_groups", ())) or getattr(me, "shape_keys", None) is not None):
        return False
    return len(me.vertices) <= EXTRAS_TOPO_MAX


def refresh_extras(obj, st=None, me=None, shapes_only: bool = False) -> None:
    """D106: the state's vertex group weights and shape key positions read again (they change outside
    recorded strokes: Weight Paint, Edit Mode, shape key edits). No step: the state follows the mesh.
    shapes_only: after a stroke, which can change a shape key but never a weight (reading weights
    goes through every vertex in Python)."""
    if not topo_extras_on(obj):
        return
    st = st or _STATES.get(obj_uid(obj))
    if st is None or not st.counts:
        return
    me = me or obj.data
    if len(me.vertices) != st.counts.get("POINT", -1):
        return
    if shapes_only:
        sk = getattr(me, "shape_keys", None)
        vals = {}
        if sk is not None:
            for i, kb in enumerate(sk.key_blocks):
                co = np.empty(len(kb.data) * 3, np.float32)
                kb.data.foreach_get("co", co)
                vals[meshio.sk_key(i, kb.name)] = co
        drop = [k for k in st.arrays if k.startswith(meshio.SK_PREFIX)]
    else:
        vals = meshio.read_extras(obj, me)
        drop = [k for k in st.arrays if meshio.is_extra(k) and not k.startswith(meshio.IMG_PREFIX)]
    for k in drop:
        del st.arrays[k]
    st.arrays.update(vals)
    _SHAPES_STALE.discard(obj_uid(obj))


def refresh_images(obj, st=None) -> None:
    """The state's image baselines (the pixels of the images a texture stroke can change), read now.
    No step: strokes are compared with this."""
    st = st or _STATES.get(obj_uid(obj))
    if st is None or not st.counts:
        return
    for k in [k for k in st.arrays if k.startswith(meshio.IMG_PREFIX)]:
        del st.arrays[k]
    for k, a in meshio.read_images(obj).items():
        st.arrays[k] = a.copy()


def _read_new_images(obj, st=None) -> None:
    """D142: an image the tool can paint now but that has no baseline yet (a canvas or texture node
    picked while in Texture Paint) is read as it is before it is painted. No step."""
    st = st or _STATES.get(obj_uid(obj))
    if st is None or not st.counts:
        return
    for img in meshio.paint_images(obj):
        k = meshio.img_key(img.name)
        if k not in st.arrays:
            a = meshio.read_images_named(obj, (img,)).get(k)
            if a is not None:
                st.arrays[k] = a.copy()


_PAINTING = [None]     # D142: uid of the object a texture painting stroke is running on


def _image_stroke_running() -> bool:
    try:
        for win in bpy.context.window_manager.windows:
            for op in getattr(win, "modal_operators", ()):
                if op.bl_idname == "PAINT_OT_image_paint":
                    return True
    except Exception:
        pass
    return False


def _image_editor_images() -> set:
    """Names of the images shown in an Image Editor in Paint mode (2D texture painting)."""
    out = set()
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "IMAGE_EDITOR":
                    sp = area.spaces.active
                    if getattr(sp, "mode", None) == "PAINT" and sp.image is not None:
                        out.add(sp.image.name)
    except Exception:
        pass
    return out


def _paint_target():
    """(object, in_mode): the mesh object a texture painting stroke changes, or (None, False). In
    Texture Paint, the active object; D144: in an Image Editor's Paint mode, the active mesh object
    in Object Mode when the image shown there is one of the images it paints."""
    if not texture_on():
        return None, False
    obj = _active()
    if obj is None or obj.type != "MESH" or is_excluded(obj):
        return None, False
    if obj.mode == "TEXTURE_PAINT":
        return obj, True
    if obj.mode == "OBJECT":
        shown = _image_editor_images()
        if shown and not shown.isdisjoint(i.name for i in meshio.paint_images(obj)):
            return obj, False
    return None, False


def _watch_image_paint() -> bool:
    """D142: painting an image sends no depsgraph update (seen in a real window), so the end of a
    texture painting stroke is seen from its operator, which the timer polls while there is a
    target (see _paint_target). True while there is one (the timer then runs faster)."""
    obj, in_mode = _paint_target()
    if obj is not None and _image_stroke_running():
        _PAINTING[0] = (obj_uid(obj, create=True), in_mode)
        return True
    held, _PAINTING[0] = _PAINTING[0], None
    if held is not None:
        uid, was_in_mode = held
        if was_in_mode:
            _DIRTY[uid] = time.time()
            _STROKE_SEEN.add(uid)      # a stroke ended: committed at once
        else:
            ob = find_object(uid)      # D144: painted in an Image Editor, the object stays in Object Mode
            if ob is not None and ob.mode == "OBJECT":
                commit(ob, label="Texture Paint", mode="TEXTURE_PAINT")
    elif obj is not None and obj_uid(obj) not in _DIRTY:
        if not in_mode:
            uid = obj_uid(obj, create=True)
            st = _STATES.get(uid)
            if st is None or not st.counts:
                ensure_state(obj, refresh=True)     # a baseline before the first stroke
        _read_new_images(obj)
    return obj is not None


def read_mesh_snapshot(obj):
    """The object's mesh as a Snapshot. In Edit Mode the mesh attributes are the edit-mode layers,
    so the edit mesh is written back first and a plain copy is read instead."""
    ext = extras_on(obj)
    if obj.mode != "EDIT":
        return meshio.read_snapshot(obj.data, ob=obj, extras=ext)
    with suspended():
        obj.update_from_editmode()
        tmp = obj.data.copy()
    try:
        return meshio.read_snapshot(tmp, ob=obj, extras=ext)
    finally:
        with suspended():
            bpy.data.meshes.remove(tmp)


def refresh_state_any_mode(obj) -> MeshState:
    """Read the object's mesh into its state again (in Edit Mode from a copy), keeping its values."""
    if obj.mode != "EDIT":
        return ensure_state(obj, refresh=True)
    uid = obj_uid(obj, create=True)
    old = _STATES.get(uid)
    snap = read_mesh_snapshot(obj)
    st = MeshState(snap.counts, snap.arrays, old.values if old is not None else None)
    _STATES[uid] = st
    return st


def ensure_state(obj, refresh: bool = False) -> MeshState:
    uid = obj_uid(obj, create=True)
    st = _STATES.get(uid)
    if obj.mode == "EDIT" and st is not None and st.counts:
        return st  # mesh arrays are not readable in Edit Mode; keep the pre-edit baseline
    if st is None or refresh or not st.counts:
        if dyntopo_active(obj) and not stroke_running():
            _flush_dyntopo(obj)          # D145: the mesh as Dyntopo has it, not as it was last written back
        snap = read_mesh_snapshot(obj)
        st = MeshState(snap.counts, snap.arrays, st.values if st is not None else None)
        _STATES[uid] = st
        _DYN_LAST[uid] = dyntopo_active(obj)
    return st


def _exclusion_test():
    """is_excluded for many objects in a row: the same answers, with what doesn't depend on the
    object looked up once (the slider asks for every object on every redraw in global mode)."""
    try:
        sh = filtering._scene_props(bpy.context.scene)
        if sh is None or sh.filter_mode not in ("BLACKLIST", "WHITELIST"):
            # Collection Filtering off: only Hindsight's own Movie Mode objects are left out
            def test(ob):
                try:
                    return rigmark.is_rig(ob)
                except Exception:
                    return False
            return test
    except Exception:
        pass
    return is_excluded


def _visible_uids() -> set:
    out = set()
    excluded = _exclusion_test()
    for ob in bpy.data.objects:
        u = any_uid(ob)
        if u is not None and not excluded(ob):
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
        from ..core.delta import view_bits

        def same(s1, s2):
            if s1.counts != s2.counts:
                return False
            for k in set(s1.arrays) | set(s2.arrays):
                if k not in s1.arrays or k not in s2.arrays:
                    return False
                a, b = s1.arrays[k], s2.arrays[k]      # bit for bit, without a bytes copy of each mesh
                if a.dtype != b.dtype or not np.array_equal(view_bits(a), view_bits(b)):
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
    ext = full and extras_on(obj)
    if c != st.counts or (full and not meshio.topology_equal(me, st.counts, st.arrays, deep=deep)):
        after = meshio.read_snapshot(me, ob=obj, extras=ext or topo_extras_on(obj))
        # `before` takes the state's arrays without copying them: every caller either drops the step
        # or calls _apply_to_state, whose set_snapshot gives the state fresh arrays (never in place)
        before = dict(st.arrays)
        if not ext and uid in _SHAPES_STALE:
            # D106: strokes since the shape keys were last read: they may be older than the mesh was.
            # No shapes rather than shapes that never existed (going back rebuilds them from positions)
            before = {k: v for k, v in before.items() if not k.startswith(meshio.SK_PREFIX)}
        return Step(seq=0, label=label or "Topology Change", mode=mode, target=uid, time=time.time(),
                    kind=TOPO, before=Snapshot(dict(st.counts), before), after=after)
    # D-f: a Weight Paint stroke is a step of the vertex group weights (every group, NaN where a
    # vertex is not a member); objects recorded with Record everything read them with the rest
    wp = not ext and mode == "WEIGHT_PAINT" and len(getattr(obj, "vertex_groups", ())) > 0
    extra_vals = meshio.read_extras(obj, me) if ext else (meshio.read_weights(obj, me) if wp else {})
    if mode == "TEXTURE_PAINT" and texture_on():
        extra_vals = dict(extra_vals)
        extra_vals.update(meshio.read_images(obj))
    keys = meshio.channel_keys(me, sculpt_only=not full) + list(extra_vals)
    present = set(keys)
    for k in list(st.arrays):
        if k in meshio.TOPO_KEYS or k in present or (meshio.is_extra(k) and not ext):
            continue
        name = split_key(k)[0]
        if full or name in meshio.SCULPT_NAMES or split_key(k)[2] in ("FLOAT_COLOR", "BYTE_COLOR"):
            keys.append(k)
    deltas = []
    for key in keys:
        new = extra_vals.get(key) if meshio.is_extra(key) else meshio.read_channel(me, key, reuse=True)
        old = st.arrays.get(key)
        if new is None and old is None:
            continue
        if key.startswith(meshio.IMG_PREFIX) and (old is None or new is None or old.size != new.size):
            # no baseline for this image yet, or it was resized: take it as it is, no step
            if new is not None:
                st.arrays[key] = new.copy()
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
    meshio.keep_buffers(keys)
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


WEIGHTS_CLAMPED: set = set()  # object uids whose last restore put back weights outside 0 to 1 clamped


def _clamp_state_weights(obj, st: MeshState) -> None:
    """D43: Blender only takes weights from 0 to 1: the state keeps what Blender now holds, so the
    clamping is never recorded as a change."""
    clamped = False
    for k, arr in st.arrays.items():
        if k.startswith(meshio.VG_PREFIX):
            clamped |= meshio.clamp_weights(arr)
    uid = obj_uid(obj)
    if clamped:
        WEIGHTS_CLAMPED.add(uid)
    else:
        WEIGHTS_CLAMPED.discard(uid)       # the note goes when a later restore needed no clamping


def _write_state(obj, st: MeshState, keys, final: bool = True) -> None:
    mark_written(obj)
    me = obj.data
    uid = obj_uid(obj)
    dyn_now = dyntopo_active(obj)
    want = _DYN_WANT.pop(uid, None) if uid is not None else None
    want = dyn_now if want is None else (want and obj.mode == "SCULPT")
    dyn = dyn_now or want
    with suspended():
        if "__TOPO__" in keys or dyn:
            # D145: while Dyntopo is on the mesh is a BMesh that the next write-back would overwrite:
            # leave Sculpt Mode, write, come back and turn Dyntopo on again (4.2 doesn't by itself)
            if dyn and "__TOPO__" not in keys:
                keys = set(keys) | {"__TOPO__"}
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
            if multires.MR_KEY in st.arrays:
                multires.restore(obj, st.arrays[multires.MR_KEY])
                me = obj.data
            else:
                meshio.write_snapshot(me, st.to_snapshot(), ob=obj)
            if mode != "OBJECT" and is_active:
                bpy.ops.object.mode_set(mode=mode)
                d = obj.data
                if (want and obj.mode == "SCULPT" and not obj.use_dynamic_topology_sculpting
                        and len(d.loops) == 3 * len(d.polygons)):
                    # Dyntopo was on at this position (and its meshes are all triangles, so turning
                    # it on changes nothing in the mesh); 4.2 and a rewritten mesh don't by themselves
                    bpy.ops.sculpt.dynamic_topology_toggle()
                if uid is not None:
                    _DYN_LAST[uid] = dyntopo_active(obj)
            if obj.mode == "SCULPT" and multires.active(obj):
                multires.begin_visit(obj)
        else:
            meshio.write_changes(me, st.arrays, keys, st.removed, ob=obj)
            if obj.mode == "SCULPT" and final and _refresh_toggle():
                _sculpt_refresh(obj)
        st.removed.clear()
        _clamp_state_weights(obj, st)
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


# L6: the Edit Mode cap is SETTINGS.edit_steps_max (preference "Edit Mode steps up to", default 300,000)


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
                and len(obj.data.vertices) <= SETTINGS.edit_steps_max and not datarec.mesh_owned_elsewhere(obj))
    except Exception:
        return False


def _shared_owner(obj, uid):
    """D104: the object whose history records obj's mesh when obj shares it with others (a linked
    duplicate, Alt+D): the one that already has a history for it. None if obj's mesh is its own, or
    no other user of it has a history yet."""
    me = getattr(obj, "data", None)
    if me is None or me.users < 2 or getattr(obj, "library", None) is not None:
        return None
    h = _REG.history_for(uid)
    if h is not None and h.members and h.members[0] != uid:
        o = find_object(h.members[0])
        if o is not None and o.data == me:
            return o, h.members[0]
    if h is not None and any(e.target == uid for e in h.entries):
        return None                       # obj records this mesh itself
    best = None
    for o in bpy.data.objects:
        if o == obj or o.type != "MESH" or o.data != me or o.library is not None:
            continue
        ou = obj_uid(o)
        ho = _REG.history_for(ou) if ou else None
        if ho is None or not ho.entries or ou in _REG.disabled:
            continue
        key = (o.name,)
        if best is None or key < best[0]:
            best = (key, o, ou)
    return (best[1], best[2]) if best else None


def _leave_shared(obj, uid) -> None:
    """D104: obj was in another object's history only through a shared mesh, and has its own mesh now
    (Make Single User, the owner's mesh replaced): it leaves that history and starts its own."""
    h = _REG.history_for(uid)
    if h is None or len(h.members) < 2 or h.members[0] == uid or getattr(obj, "data", None) is None:
        return
    if obj.data.users > 1 or any(e.target == uid for e in h.entries):
        return
    _REG.remove_object(uid)
    _journal("META", {"hist": h.uid, "members": list(h.members), "enabled": h.enabled,
                      "morph_target": h.morph_target})
    ensure_state(obj, refresh=obj.mode != "EDIT")


def commit(obj, label: str | None = None, mode: str | None = None, full: bool = False, _edit: bool = False,
           _mr_step=None):
    """Record what changed on obj since the last commit as ONE step. Returns the Step or None."""
    if obj is None or obj.type != "MESH":
        return None
    uid = obj_uid(obj, create=True)
    _DIRTY.pop(uid, None)
    _STROKE_SEEN.discard(uid)
    if _mr_step is None and multires.active(obj):
        return None      # a Multires object: its step is the visit's (multires.end_visit), never a stroke's
    if not _edit and obj.mode != "EDIT":
        _leave_shared(obj, uid)
        shared = _shared_owner(obj, uid)
        if shared is not None:
            # D104: one mesh, one history. The step is the owner's (it writes the one mesh); obj
            # joins that history, so its slider shows it too.
            o, ou = shared
            ho = _REG.history_for(ou)
            if _REG.history_for(uid) is not ho and uid not in _REG.disabled:
                _REG.attach(uid, ho)
                _journal("META", {"hist": ho.uid, "members": list(ho.members), "enabled": ho.enabled,
                                  "morph_target": ho.morph_target})
            if ou not in _STATES or not _STATES[ou].counts:
                ensure_state(o, refresh=True)
                _STATES[uid] = _STATES[ou]
                return None
            _STATES[uid] = _STATES[ou]
            return commit(o, label=label, mode=mode or obj.mode, full=full)
    if obj.mode == "EDIT" and not _edit:
        return None  # Edit Mode changes are grouped and recorded when leaving Edit Mode
    if (mode or obj.mode) == "TEXTURE_PAINT" and not texture_on():
        return None
    if uid in _REG.disabled or is_excluded(obj):
        return None
    mode = mode or obj.mode
    st = _STATES.get(uid)
    if st is not None and multires.MR_KEY in st.arrays and _mr_step is None:
        st = ensure_state(obj, refresh=True)      # the state held a Multires snapshot: read the mesh again
    if st is None or not st.counts:          # no mesh baseline yet (or data values only)
        ensure_state(obj, refresh=True)
        return None
    if _blocking(obj):
        ensure_state(obj, refresh=True)
        return None
    h = _REG.history_for(uid)
    if h is not None and not h.enabled:
        return None
    if h is None and mode not in RECORD_MODES and not _edit and not extras_on(obj):
        # Only a sculpt or paint stroke starts a history. Leaving Edit Mode on an object without
        # one (for example a file saved in Edit Mode) just takes a new baseline. An object recorded
        # with Record everything records the visit (D50).
        ensure_state(obj, refresh=True)
        return None
    if label is None:
        label = {"SCULPT": "Sculpt", "VERTEX_PAINT": "Vertex Paint", "WEIGHT_PAINT": "Weight Paint", "TEXTURE_PAINT": "Texture Paint",
                 "EDIT": "Edit Mode"}.get(mode, "Change")
        try:
            ops = bpy.context.window_manager.operators
            if len(ops):
                label = ops[-1].bl_label or label
        except Exception:
            pass
    dyn = dyntopo_active(obj)
    if dyn:
        if stroke_running():
            _DIRTY[uid] = time.time()        # never in the middle of a stroke: when it ends
            return None
        if not _flush_dyntopo(obj):
            return None
        full = True                          # Dyntopo changes the topology: compare all of it
    src = None
    if _edit and obj.mode == "EDIT":
        # In Edit Mode the mesh's attributes are the edit-mode layers ("position" isn't one of them):
        # read a plain copy of the mesh instead (update_from_editmode has made it current).
        with suspended():
            src = obj.data.copy()
    try:
        step = _mr_step if _mr_step is not None else _observe(obj, st, full, label, mode, me=src)
    finally:
        if src is not None:
            with suspended():
                bpy.data.meshes.remove(src)
    native = uid in _NATIVE_PENDING
    _NATIVE_PENDING.discard(uid)
    if step is None:
        return None
    was = bool(_DYN_LAST.get(uid))
    if dyn or was:                           # D145: going back and forward puts Dyntopo on or off with it
        step.extra = dict(step.extra or {})
        step.extra["dyn"] = [int(was), int(dyn)]
    _DYN_LAST[uid] = dyn
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
        if mode == "EDIT" and _edit:
            # D40: Blender's own Edit Mode undo landed somewhere that isn't next to the cursor (with
            # Global Undo off a jump isn't on Blender's undo). Blender's undo never records a step
            # or drops later ones: put the mesh back and say so.
            _put_back_after_native_undo(obj, st, step, NATIVE_UNDO_EDIT_TEXT)
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
    group = OP_GROUP[0]
    if group is not None:                    # D52: one operator's steps are one global position
        if group[0] is None:
            group[0] = step.seq
        else:
            step.extra = dict(step.extra or {}, op=group[0])
    at = h.cursor
    removed = h.push(step)
    _apply_to_state(st, step)
    if step.kind != DELTA and obj.mode == "TEXTURE_PAINT" and texture_on():
        refresh_images(obj, st)      # a rebuilt mesh dropped the image baselines
    if step.kind == DELTA and getattr(obj.data, "shape_keys", None) is not None:
        _SHAPES_STALE.add(uid)   # D106: a stroke can change a shape key; read again when leaving
    if removed:
        _journal("TRUNC", {"hist": h.uid, "keep": at})
    entry = h.entries[-1]
    _journal("STEP", {"hist": h.uid, "at": at}, lambda e=entry: e.to_bytes(JOURNAL_CODEC, 0))
    _enforce(h)
    try:
        from . import datarec
        datarec.drop_undone_jumps()
    except Exception:
        pass
    tag_redraw_all()
    return step


def _put_back_after_native_undo(obj, st: MeshState, step: Step, text: str = NATIVE_UNDO_TEXT) -> None:
    keys = {d.key for d in step.deltas} | set(st.removed) if step.kind == DELTA else {"__TOPO__"}
    if obj.mode == "EDIT":
        keys = {"__TOPO__"}       # in Edit Mode the edit mesh is written by leaving and entering it
    name = obj.name

    def _later():
        ob = bpy.data.objects.get(name)
        if ob is not None:
            _write_state(ob, st, keys)
            tag_redraw_all()
        return None
    # Never write meshes or switch modes inside Blender's undo handler: do it right after.
    if bpy.app.background:
        _later()                  # tests call the handler themselves, outside Blender's undo
    else:
        bpy.app.timers.register(_later, first_interval=0.0)
    log.warn(text)
    try:
        from .selfcheck import flash_status
        flash_status("Hindsight: " + text, 10.0)
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
_CAPS_MEMO = None             # store.CapsMemo, made on first use (core.store is imported lazily here)
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


def _journal_drop(h, n) -> None:
    """The step cap or the memory limit dropped the n oldest steps of h: the crash journal has to
    know, or recovery puts later steps in the wrong place."""
    if n:
        _journal("DROP", {"hist": h.uid, "n": int(n)})


def _after_caps(drops, stats) -> None:
    """The caps of a full pass have dropped steps (before any packing starts)."""
    for x, n in drops:
        _journal_drop(x, n)
    if stats.get("memory_dropped"):
        _note_eviction(int(stats["memory_dropped"]))


def _enforce(h=None) -> None:
    """Keep caps and tiers. Incremental per commit (O(1)); full rescans every 64 commits or when
    the memory estimate crosses the limit, so recording cost does not grow with history length."""
    try:
        from ..core import store
        global _CAPS_MEMO
        if _CAPS_MEMO is None:
            _CAPS_MEMO = store.CapsMemo()
        _ENFORCE_COUNT[0] += 1
        if h is not None and h.entries:
            MEMORY["estimate"] += h.entries[-1].nbytes()
        over_limit = MEMORY["estimate"] > int(SETTINGS.memory_limit_mb) * (1 << 20)
        if h is not None and _ENFORCE_COUNT[0] % 64 and not over_limit:
            excess = len(h.entries) - SETTINGS.max_steps
            if excess > 0:
                _journal_drop(h, h.drop_oldest(excess))
            k = len(h.entries) - max(0, SETTINGS.raw_steps) - 1
            if k >= 0 and not h.entries[k].is_packed():
                if packer is not None:
                    packer.submit(h.entries[k], SETTINGS.compression)
                else:
                    h.entries[k].pack(SETTINGS.compression)
            return
        stats = {}
        # S4-3: over the limit with nothing that may be dropped, a pass after one history changed
        # only looks at that history (store.CapsMemo); otherwise every history is checked
        store.enforce(_REG.all(), SETTINGS, packer, stats, _CAPS_MEMO, h, on_caps=_after_caps)
        MEMORY["estimate"] = stats["total"]
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
    if obj.mode == "SCULPT" and multires.active(obj):
        multires.begin_visit(obj)
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
    if (obj.mode == "WEIGHT_PAINT" and st is not None and st.counts and topo_extras_on(obj)
            and not any(k.startswith(meshio.VG_PREFIX) for k in st.arrays)):
        # D-f: a state read without weights (a file just loaded) takes them as they are now: the
        # weights of strokes are compared with this baseline, nothing outside a stroke is a step
        refresh_extras(obj, st)
    if h is not None and st is not None and h.enabled and uid not in _REG.disabled:
        # Anything changed while the object was not being tracked becomes one step.
        commit(obj, label="Changes Outside History", mode=obj.mode, full=True)
    else:
        ensure_state(obj, refresh=True)
    refresh_extras(obj)          # D106: weights and shapes as they are now (Weight Paint isn't a step)
    if obj.mode == "TEXTURE_PAINT" and texture_on():
        refresh_images(obj)


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
    if prev is not None and pmode == "SCULPT" and prev.mode != "SCULPT" and multires.active(prev):
        multires.leave(prev)              # a Multires visit is one step, taken (flush) once Sculpt Mode has ended
    if prev is not None and pmode == "EDIT" and prev.mode != "EDIT":
        if _mesh_owned_elsewhere(prev):
            ensure_state(prev, refresh=True)    # D39: the mesh's owner records this visit
        else:
            commit(prev, label="Edit Mode", mode="EDIT", full=True)
    elif prev is not None and pmode in RECORD_MODES and obj_uid(prev) in _DIRTY:
        commit(prev, mode=pmode)
    if prev is not None and pmode in RECORD_MODES and prev.mode != pmode and obj_uid(prev) in _SHAPES_STALE:
        refresh_extras(prev, shapes_only=True)
    if obj is not None and obj.type == "MESH" and mode in TRACK_MODES and (mode != "TEXTURE_PAINT" or texture_on()) \
            and not is_excluded(obj):
        if mode == "VERTEX_PAINT" and not len(obj.data.color_attributes):
            # Blender adds a colour layer on entering Vertex Paint; 5.3 does it after the mode's first
            # update. The mesh is read once it is there, so that layer is never taken for a change.
            _TRACK["mode"] = None
            return
        _enter(obj)
        _TRACK["uid"] = obj_uid(obj)


_GEN = [0]       # counts depsgraph updates, undo and file loads (scrub_info's cache for drawing)


@persistent
def on_depsgraph(scene, depsgraph=None) -> None:
    _GEN[0] += 1           # also while suspended or switched off: what is visible may have changed
    _on_depsgraph(scene, depsgraph)


@guard.guarded("recorder", where="on_depsgraph")
def _on_depsgraph(scene, depsgraph=None) -> None:
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
    if _mode_recorded(obj.mode):
        data = obj.data
        paint = {i.name for i in meshio.paint_images(obj)} if obj.mode == "TEXTURE_PAINT" else ()
        for u in depsgraph.updates:
            idv = u.id
            orig = getattr(idv, "original", idv)
            if orig == obj or orig == data or (paint and isinstance(orig, bpy.types.Image) and orig.name in paint):
                uid = obj_uid(obj)
                if uid is not None:
                    _DIRTY[uid] = time.time()
                    if stroke_running():
                        _STROKE_SEEN.add(uid)
                break
    check_mode_transition()


@guard.guarded("recorder")
def _tick_once() -> bool:
    check_mode_transition()
    painting = _watch_image_paint()
    multires.flush()
    ob_ = _active()
    if ob_ is not None and ob_.mode == "SCULPT" and multires.active(ob_) and not stroke_running():
        multires.tick(ob_)
    if _DIRTY and stroke_running():
        return painting   # never split a stroke, even if a slow modifier stack pauses its updates
    now = time.time()
    for uid, t in list(_DIRTY.items()):
        # A stroke that has finished is committed at once, so quick strokes stay separate steps.
        # Changes without a stroke operator (filters run from a menu, scripts) wait for the
        # updates to settle.
        if uid in _STROKE_SEEN or now - t >= DEBOUNCE:
            ob = find_object(uid)
            if ob is None or not _mode_recorded(ob.mode):
                _DIRTY.pop(uid, None)
                _STROKE_SEEN.discard(uid)
                continue
            commit(ob)
    return painting


def tick() -> float:
    """Commits each stroke as it ends (or once updates settle). Never raises."""
    busy = False
    try:
        busy = _tick_once()
        ensure_handlers()
    except Exception as e:
        log.error_once(("tick", str(e)), f"timer error: {e}", e)
    try:
        from . import datarec
        datarec.tick()
        busy = busy or bool(datarec._DIRTY)
    except Exception as e:
        log.error_once(("datarec_tick", str(e)), f"data recorder error: {e}", e)
    try:
        from . import camrig
        camrig.tick()
    except Exception as e:
        log.error_once(("camrig_tick", str(e)), f"Movie Mode camera error: {e}", e)
    return 0.02 if (_DIRTY or busy) else 0.1


OP_GROUP = [None]             # [first step number] while one operator's changes are recorded (D52)
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
    _GEN[0] += 1
    try:
        from . import idmap
        idmap.restore_after_undo()        # D42: ids set after Blender's undo push come back first
    except Exception as e:
        log.error_once(("restore_ids", str(e)), f"could not restore ids after undo: {e}", e)
    _UID_CACHE.clear()
    _NATIVE_PENDING.update(_STATES.keys())   # objects in other modes are checked on their next commit
    # D53: Hindsight's jumps on Blender's undo are matched first; an object whose history that moved
    # is where it belongs and isn't compared again (in Edit Mode that compare would undo the undo)
    moved = set()
    try:
        from . import datarec
        moved = datarec.on_undo(UNDO_DIRECTION[0]) or set()
    except Exception as e:
        log.error_once(("datarec_undo", str(e)), f"data recorder error after undo: {e}", e)
    obj = _active()
    h = history_of(obj) if obj is not None else None
    if h is not None and h.uid in moved:
        _NATIVE_PENDING.discard(obj_uid(obj))
        return
    if obj is not None and obj.type == "MESH" and obj.mode in RECORD_MODES:
        if obj_uid(obj) in _STATES:
            commit(obj)
    elif obj is not None and obj.type == "MESH" and obj.mode == "EDIT" and edit_steps_on(obj):
        if obj_uid(obj) in _STATES:
            commit_edit(obj, "Edit")


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
    _GEN[0] += 1
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


def _after_mesh_write(ob) -> None:
    try:
        from . import adapters
        adapters.after_mesh_write(ob)      # D108: UV map flags, once the UV maps exist again
    except Exception as e:
        log.error_once(("after_mesh_write", str(e)), f"could not finish restoring {ob.name}: {e}", e)


def _goto_history(h, pos: int, final: bool, acc: dict | None = None) -> dict:
    """Move one history to pos and write what changed to Blender. With acc (a global jump), the
    move is noted in acc instead of being pushed onto Blender's undo stack on its own."""
    from . import datarec
    multires.flush()
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
        # D44: steps that change which mesh the object uses: going back, its mesh is written before it
        # goes back to the earlier mesh; going forward, after it uses the later one
        relink_last = pos < before and "data" in data_keys.get("rna", ())
        if data_keys and not relink_last:
            datarec.write(ob, st, data_keys)
            wrote_data = True
        if not mesh_keys:
            if relink_last:
                datarec.write(ob, st, data_keys)
                wrote_data = True
            _after_mesh_write(ob)
            continue
        # the mesh can be in Edit Mode through the object itself or (D51) through a linked duplicate
        editor = ob if ob.mode == "EDIT" else _mesh_editor(ob)
        if editor is not None:
            with suspended(), bpy.context.temp_override(object=editor, active_object=editor):
                bpy.ops.object.mode_set(mode="OBJECT")
        fp = pushing and ob.mode == "OBJECT" and ob.type == "MESH" and target in datarec.tracked_uids()
        if fp and target not in meshes:
            meshes[target] = [mesh_fingerprint(ob), None]
        if ob.type == "MESH":
            _DYN_WANT[target] = _dyn_at(h, pos)
        _write_state(ob, st, mesh_keys, final)
        _after_mesh_write(ob)
        if fp:
            meshes[target][1] = mesh_fingerprint(ob)
        if editor is not None:
            with suspended(), bpy.context.temp_override(object=editor, active_object=editor):
                bpy.ops.object.mode_set(mode="EDIT")
        if relink_last:
            datarec.write(ob, st, data_keys)
            wrote_data = True
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
    "SCULPT_OT_cloth_filter", "SCULPT_OT_color_filter", "HINDSIGHT_OT_morph_stroke", "PAINT_OT_image_paint",
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


def _mesh_editor(ob):
    """The object through which ob's mesh is in Edit Mode right now (a linked duplicate), or None."""
    me = getattr(ob, "data", None)
    if me is None or not getattr(me, "is_editmode", False):
        return None
    for o in bpy.context.view_layer.objects:
        if o.data == me and o.mode == "EDIT":
            return o
    return None


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
    with_ob = ob if extras_on(ob) else None
    if ob.mode != "EDIT":
        return meshio.fingerprint(ob.data, with_ob)
    with suspended():
        ob.update_from_editmode()
        tmp = ob.data.copy()
    try:
        return meshio.fingerprint(tmp, with_ob)
    finally:
        with suspended():
            bpy.data.meshes.remove(tmp)


_GROUPS = {}   # the global timeline's operations and the position on it, kept until they change


def _hist_sig(h):
    return (tuple(h.members), len(h.entries), h.entries[0].seq if h.entries else 0,
            h.entries[-1].seq if h.entries else 0)


def _groups(vis) -> list:
    """globaltl.global_groups, kept between calls: the slider, the header, the sidebar and Movie
    Mode ask for it on every redraw and every frame, and building it walks and sorts every step.
    A step recorded at the end of one history (the usual case) extends the kept groups; anything else
    (a history gains, loses or replaces steps or objects, what is visible changes) builds them again."""
    vis = frozenset(vis)
    sigs = {h.uid: _hist_sig(h) for h in _REG.histories.values()}
    key = (id(_REG), _REG.next_seq, vis, tuple(sorted(sigs.items())))
    if _GROUPS.get("key") == key:
        return _GROUPS["groups"]
    groups = _extend_groups(vis, sigs)
    if groups is None:
        groups = globaltl.global_groups(_REG, vis)
    _GROUPS.clear()
    _GROUPS.update(key=key, groups=groups, vis=vis, reg=id(_REG), sigs=sigs,
                   ver=_GROUPS_VER[0] + 1)
    _GROUPS_VER[0] += 1
    return groups


_GROUPS_VER = [0]


def _extend_groups(vis, sigs):
    """The kept groups with the one step appended since they were built, or None to build again."""
    old = _GROUPS.get("sigs")
    if old is None or _GROUPS.get("vis") != vis or _GROUPS.get("reg") != id(_REG) or set(old) != set(sigs):
        return None
    grown = [u for u in sigs if sigs[u] != old[u]]
    if len(grown) != 1:
        return None
    u = grown[0]
    om, olen, ofirst, olast = old[u]
    m, n, first, last = sigs[u]
    if m != om or n != olen + 1 or (olen and first != ofirst) or last <= olast:
        return None
    h = _REG.histories[u]
    groups = list(_GROUPS["groups"])
    if vis is not None and not (set(h.members) & vis):
        return groups                                    # not on the visible timeline
    if groups and last <= groups[-1][-1][0]:
        return None                                      # not after every kept step: build again
    e = h.entries[-1]
    item = (e.seq, h.uid, n - 1)
    gk = globaltl._group_key(e)
    if gk != e.seq:                                      # D52: part of an operation already on it
        for gi in range(len(groups) - 1, -1, -1):
            g = groups[gi]
            if globaltl._group_key(_entry(g[0])) == gk:
                groups[gi] = sorted(g + [item])
                return groups
    groups.append([item])
    return groups


def _entry(t):
    _seq, hid, i = t
    return _REG.histories[hid].entries[i]


def _position(vis, groups) -> int:
    """globaltl.global_position, kept until a cursor moves."""
    key = (_GROUPS.get("ver"), id(groups), tuple(h.cursor for h in _REG.histories.values()))
    if _GROUPS.get("pos_key") != key:
        _GROUPS.update(pos_key=key, pos=globaltl.global_position(_REG, vis, groups))
    return _GROUPS["pos"]


def global_goto(context, g: int, final: bool = True) -> None:
    """Move the global timeline to position g. Steps are undone and redone across histories in
    step-number order (globaltl.transition), and the whole jump is one entry on Blender's undo."""
    from . import datarec
    vis = _visible_uids()
    groups = _groups(vis)
    old = globaltl.global_position(_REG, vis, groups)
    acc = {"hists": {}, "vb": {}, "va": {}, "meshes": {}, "wrote": False, "object_mode": True}
    held = _leave_edit_for_push(context, final) if g != old else None
    pre = datarec.recorded_values()          # the whole scene before the jump (objects it brings back
                                             # or removes must not count on the wrong side)
    for hid, cur in globaltl.transition(_REG, g, vis, groups):
        h = _REG.histories.get(hid)
        if h is not None and h.cursor != cur:
            _goto_history(h, cur, final, acc)
    datarec.resolve_missing_refs()          # D37: objects brought back get their references back
    p = _scene_props(context)
    preserve = bool(p.preserve_modes) if p is not None else _REG.preserve_modes
    new_g = globaltl.global_position(_REG, vis, groups) if preserve and final else old
    if acc["wrote"] and final and not IN_HANDLER[0] and acc["object_mode"]:
        if datarec.push_undo():
            datarec.note_jump(acc["hists"], pre, datarec.recorded_values(), acc["meshes"])
    _reenter_edit(context, held)
    if preserve and final and new_g != old:
        piv = globaltl.pivot_entry(_REG, old, new_g, vis, groups)
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
            if mode in ("SCULPT", "VERTEX_PAINT", "WEIGHT_PAINT", "TEXTURE_PAINT", "EDIT") and ob.mode != mode:
                bpy.ops.object.mode_set(mode=mode)
        except Exception as e:
            log.error_once(("preserve", str(e)), f"Follow object and mode could not switch objects: {e}", e)
    _TRACK.update(name=ob.name, uid=obj_uid(ob), mode=ob.mode)
    if ob.mode in TRACK_MODES:
        ensure_state(ob)


_SCRUB = {}       # scrub_info's last global answer for drawing: {"key": ..., "info": ...}


def _scrub_key(context):
    """Everything the global answer of scrub_info depends on, cheap to build: the histories (steps,
    cursors, members), the scene and its Hindsight settings (global, Movie Mode, Collection
    Filtering), the step cap, the recorded data's ids, the number of objects, and _GEN, which
    counts depsgraph updates (objects added, deleted, renamed, moved between collections, marked),
    undo and file loads."""
    sc = context.scene
    p = getattr(sc, "hindsight", None)
    filt = None
    if p is not None:
        filt = (p.filter_mode, tuple(it.collection.as_pointer() if it.collection is not None else 0
                                     for it in p.filter_collections), bool(p.movie_mode), bool(p.use_global))
    try:
        from . import datarec
        tracked = frozenset(datarec.tracked_uids())
    except Exception:
        tracked = None
    # rigmark.GEN: counts Movie Mode marks set or taken off, where rigmark has it (setting a custom
    # property sends no depsgraph update)
    return (id(_REG), _REG.next_seq, _REG.use_global, _GEN[0], len(bpy.data.objects), sc.as_pointer(),
            filt, int(SETTINGS.max_steps), tracked, tuple(getattr(rigmark, "GEN", ())),
            tuple((h.uid, h.cursor, len(h.entries), tuple(h.members)) for h in _REG.histories.values()))


def scrub_info(context, cached: bool = False):
    """Where the slider is: {"pos", "len", "cap", "global", "locked", "morph_target"} or None.
    cached (for drawing only, which asks several times per redraw): in global mode the answer is
    kept until _scrub_key changes. Operators that act on the answer leave it off."""
    if cached:
        try:
            if use_global(context):
                key = _scrub_key(context)
                if _SCRUB.get("key") != key:
                    _SCRUB.clear()
                    _SCRUB.update(key=key, info=scrub_info(context))
                info = _SCRUB["info"]
                return dict(info) if info is not None else None
        except Exception:
            _SCRUB.clear()
    try:
        locked = _movie_locked(context)
        if use_global(context):
            vis = _visible_uids()
            groups = _groups(vis)
            n = globaltl.global_length(_REG, vis, groups)
            if not n:
                return None
            return {"pos": _position(vis, groups), "len": n,
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
            groups = _groups(vis)
            cur = _position(vis, groups)
            n = len(groups)

            def label(k):                            # one position per operation
                _seq, hid, i = groups[k][0]
                h = _REG.histories.get(hid)
                return h.entries[i].meta.get("label", "Step") if h else "Step"
        else:
            h = history_of(_ctx_active(context))
            if h is None:
                return []
            cur = h.cursor
            entries = h.entries
            n = len(entries)

            def label(k):
                return entries[k].meta.get("label", "Step")
        lo, hi = max(0, cur - around), min(n, cur + around)
        for i in range(hi, lo, -1):                  # only the rows shown (was: every step's label)
            out.append((i, label(i - 1), i == cur))
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
    at = h.cursor
    h.reset()
    _journal("RESET", {"hist": h.uid, "at": at})
    if obj.mode == "EDIT":
        return
    ensure_state(obj, refresh=True)
    tag_redraw_all()


def reset_all(context) -> None:
    for ob in bpy.data.objects:
        if _REG.history_for(obj_uid(ob) or "") is not None:
            reset_history(context, ob)


OUTSIDE_LABEL = "Changes Outside Hindsight"
OUTSIDE_BRIDGED_TEXT = ("{name} was changed while Hindsight wasn't running. Its history goes on, with the "
                        "change as one step, read from the backup copy Blender keeps of the file")
OUTSIDE_MESH_TEXT = ("{name} was changed while Hindsight wasn't running, so its history no longer matches its "
                     "mesh and was left out")


def _override_key(idb):
    """[library file, name of the linked datablock] of a library override, else None."""
    ol = getattr(idb, "override_library", None)
    ref = getattr(ol, "reference", None) if ol is not None else None
    lib = getattr(ref, "library", None) if ref is not None else None
    if lib is None:
        return None
    from . import idmap
    return [type(idb).__name__, idmap.lib_path(lib), ref.name]


OVERRIDES_KEY = "~overrides"   # D109: in Registry.saved, {id: [type, library file, name]} of overrides


def override_ids() -> dict:
    """Every library override (objects and collections) that has a Hindsight id, by its key."""
    out = {}
    for col in (bpy.data.objects, bpy.data.collections):
        for idb in col:
            if getattr(idb, "override_library", None) is None:
                continue
            u = idb.get(UID_PROP)
            k = _override_key(idb) if u else None
            if k is not None:
                out[u] = k
    return out


def restamp_overrides(saved) -> int:
    """D109: Blender builds library overrides again from their library when a file is opened, and the
    id Hindsight gave one is gone. Each override saved with an id gets it back, found by its type,
    library file and the name of the datablock it overrides. Returns how many."""
    from . import idmap
    m = (saved or {}).get(OVERRIDES_KEY)
    if not isinstance(m, dict) or not m:
        return 0
    want = {}
    for u, k in m.items():
        if isinstance(k, list) and len(k) == 3 and idmap.find(u) is None:
            want.setdefault(tuple(k), []).append(u)
    n = 0
    for col in (bpy.data.objects, bpy.data.collections):
        for idb in col:
            k = _override_key(idb)
            if k is None or tuple(k) not in want or idb.get(UID_PROP) is not None:
                continue
            us = want[tuple(k)]
            if len(us) != 1:
                continue                  # two overrides of one datablock: can't tell them apart
            idb[UID_PROP] = us[0]
            idmap.note(idb, us[0])
            n += 1
    return n


def saved_prints() -> dict:
    """D46: every recorded object as it is saved (a fingerprint of its mesh, its recorded values), so
    opening the file can tell whether it was changed in a session without Hindsight."""
    from . import adapters, datarec, idmap
    tracked = datarec.tracked_uids()
    uids = set(tracked)
    for h in _REG.histories.values():
        if h.entries:
            uids.update(h.members)
    out = {}
    for u in sorted(uids):
        ob = idmap.find(u) or find_object(u)
        if ob is None:
            continue
        rec = {}
        try:
            if isinstance(ob, bpy.types.Object) and ob.type == "MESH" and ob.data is not None:
                rec["mesh"] = mesh_fingerprint(ob)
            if u in tracked:
                rec["values"] = [[a, p, v] for (a, p), v in sorted(datarec.capture(ob).items())]
                # D57: references still waiting for a deleted object to come back
                miss = [[path, ref, a, ap, adapters.WANTED[(u, a, ap)], datarec.ref_label(ref)]
                        for path, (ref, a, ap) in sorted((adapters.MISSING_REFS.get(u) or {}).items())
                        if (u, a, ap) in adapters.WANTED]
                if miss:
                    rec["missing"] = miss
        except Exception as e:
            log.error_once(("saved_prints", str(e)), f"could not note {getattr(ob, 'name', u)} for saving: {e}", e)
            continue
        out[u] = rec
    ov = override_ids()
    if ov:
        out[OVERRIDES_KEY] = ov               # D109: their ids, which Blender drops on load
    return out


def _bridge_from_backup(ob, uid, h, fp) -> bool:
    """D107: the mesh was changed in a session without Hindsight. Blender keeps the file as it was
    before that session's save in <file>.blend1 (Save Versions). When that copy's mesh is exactly the
    one Hindsight saved, the change becomes one topology step from it to the mesh now, and the history
    is kept. False when there is no such copy or it doesn't match (then the history is left out)."""
    path = bpy.data.filepath
    if not path or ob.mode == "EDIT" or not os.path.isfile(path + "1"):
        return False
    name = ob.data.name
    kinds = ("meshes", "materials", "images", "textures", "node_groups")
    before_ids = {k: {d.as_pointer() for d in getattr(bpy.data, k)} for k in kinds}
    loaded = None
    try:
        with suspended():
            with bpy.data.libraries.load(path + "1", link=False) as (src, dst):
                if name not in src.meshes:
                    return False
                dst.meshes = [name]
            loaded = dst.meshes[0] if dst.meshes else None
        if loaded is None:
            return False
        ext = extras_on(ob)
        if meshio.fingerprint(loaded, ob if ext else None) != fp:
            return False
        with_extras = ext or topo_extras_on(ob)
        before = meshio.read_snapshot(loaded, ob=ob, extras=with_extras)
        after = meshio.read_snapshot(ob.data, ob=ob, extras=with_extras)
        step = Step(seq=_REG.new_seq(), label=OUTSIDE_LABEL, mode="OBJECT", target=uid, time=time.time(),
                    kind=TOPO, before=before, after=after)
        at = h.cursor
        removed = h.push(step)
        if removed:
            _journal("TRUNC", {"hist": h.uid, "keep": at})
        entry = h.entries[-1]
        _journal("STEP", {"hist": h.uid, "at": at}, lambda e=entry: e.to_bytes(JOURNAL_CODEC, 0))
        return True
    except Exception as e:
        log.error_once(("blend1_bridge", str(e)), f"could not read the backup copy of the file: {e}", e)
        return False
    finally:
        # what loading the copy brought in goes again (the mesh and anything it pulled with it)
        with suspended():
            for k in kinds:
                col = getattr(bpy.data, k)
                for d in [d for d in col if d.as_pointer() not in before_ids[k] and d.users == 0]:
                    try:
                        col.remove(d)
                    except Exception:
                        pass


def check_outside_changes(saved: dict) -> None:
    """D46: after opening a file, compare every recorded object with how Hindsight saved it. Values
    changed in a session without Hindsight become one step; a changed mesh can't be rebuilt from
    the history, so that object's history is left out with a message (never played back onto it)."""
    from . import datarec, idmap
    outside = set()
    for u, rec in (saved or {}).items():
        ob = idmap.find(u) or find_object(u)
        if ob is None or not isinstance(rec, dict):
            continue
        h = _REG.history_for(u)
        fp = rec.get("mesh")
        if fp and isinstance(ob, bpy.types.Object) and ob.type == "MESH" and h is not None and h.entries:
            if (mesh_fingerprint(ob) != fp and h.cursor == len(h.entries) and len(h.members) == 1
                    and _bridge_from_backup(ob, u, h, fp)):
                # D107 (only with nothing recovered ahead of the saved position, and for one object):
                # the file's state is after the new step, for crash recovery as for D83
                load_notes.append(("INFO", OUTSIDE_BRIDGED_TEXT.format(name=ob.name)))
                ensure_state(ob, refresh=True)
                outside.add(u)
            elif mesh_fingerprint(ob) != fp:
                h.reset()
                # with the meshes as they are now: steps recorded from here on belong to them, and
                # crash recovery must not take this history for a stale one again (D82)
                prints = {}
                for m in h.members:
                    o = ob if m == u else (idmap.find(m) or find_object(m))
                    if isinstance(o, bpy.types.Object) and o.type == "MESH":
                        prints[m] = mesh_fingerprint(o)
                _journal("RESET", {"hist": h.uid, "mesh": prints})
                for m in list(h.members):
                    _REG.remove_object(m)
                load_notes.append(("ERROR", OUTSIDE_MESH_TEXT.format(name=ob.name)))
                ensure_state(ob, refresh=ob.mode != "EDIT")
                continue
        vals = rec.get("values")
        if isinstance(vals, list) and u in datarec.tracked_uids():
            st = datarec._state(u)
            st.values = {(a, p): v for a, p, v in vals}
            _restore_missing_refs(u, rec.get("missing"))
            if datarec.commit(label=OUTSIDE_LABEL, uids={u}, mesh=False):
                outside.add(u)
    if outside:
        # D83: the file already holds these changes, so for crash recovery its state is the position
        # after the step just made, and the values to compare with next time are today's
        prints = saved_prints()
        for u in sorted(outside):
            h = _REG.history_for(u)
            if h is not None:
                _journal("META", {"hist": h.uid, "file_at": h.cursor})
        _journal("META", {"saved": {u: prints[u] for u in sorted(outside) if u in prints}})


def _restore_missing_refs(u, miss) -> None:
    """D57: references that were waiting for a deleted object to come back when the file was saved
    wait again (the sidebar says so, the global history puts them in). One changed without Hindsight
    meanwhile stops waiting when that change is recorded, right after this."""
    from . import adapters
    for item in miss if isinstance(miss, list) else ():
        try:
            path, ref, aid, apath, value = item[:5]
        except (TypeError, ValueError):
            continue
        adapters.MISSING_REFS.setdefault(u, {})[path] = (ref, aid, apath)
        adapters.WANTED[(u, aid, apath)] = value
        if len(item) > 5 and isinstance(item[5], str):
            adapters.REF_NAMES[ref] = item[5]


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
        from . import datarec
        if datarec.is_tracked(obj) and obj.mode != "EDIT":
            # D46: values and mesh changed while off become one step together
            datarec.catch_up(obj, "Changes While Off")
        elif uid in _STATES and obj.mode != "EDIT":
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
        at = h.cursor
        h.reset()
        _journal("RESET", {"hist": h.uid, "at": at})
        _REG.remove_object(uid)
    _REG.disabled.add(uid)
    _STATES.pop(uid, None)
    _DIRTY.pop(uid, None)
    tag_redraw_all()


_HIST_BYTES: dict = {}    # history uid -> (signature, sizes key, bytes): memory_stats per history


def _history_bytes(h, gkey) -> int:
    """sum(e.nbytes() for e in h.entries), kept while the history's steps and cursor are the same
    (store._sig) and no step changed size: gkey holds the packer's counters (it packs and
    recompresses in the background) and _ENFORCE_COUNT (packing on the main thread only happens in
    _enforce)."""
    from ..core import store
    sig = store._sig(h)
    c = _HIST_BYTES.get(h.uid)
    if c is not None and c[0] == sig and c[1] == gkey:
        return c[2]
    b = sum(e.nbytes() for e in h.entries)
    _HIST_BYTES[h.uid] = (sig, gkey, b)
    return b


def memory_stats() -> dict:
    """The Memory panel's numbers (drawn on every redraw while it is open)."""
    from ..core import store
    total = 0
    steps = 0
    active_b = 0
    active_s = 0
    ah = None
    try:
        ah = history_of(_ctx_active(bpy.context))
    except Exception:
        pass
    gkey = (store._packer_key(packer), _ENFORCE_COUNT[0], id(_REG))   # read before any size
    hs = _REG.all()
    if len(_HIST_BYTES) > 2 * len(hs) + 16:
        _HIST_BYTES.clear()                          # histories gone: don't keep their sizes
    for h in hs:
        b = _history_bytes(h, gkey)
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
