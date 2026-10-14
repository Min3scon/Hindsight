"""Morph History brush: paint brushed areas back toward the morph target step."""
from __future__ import annotations

import bpy
import numpy as np

from ..core import morph as M
from ..core.types import POSITION_KEY, MASK_KEY, HIDE_VERT_KEY
from . import guard, log, meshio, runtime

TOOL_ID = "hindsight.morph_history"
STROKE_LABEL = "Morph History"
MSG_REMESH = "Set a new Morph Target after remeshing"

_SESSION: dict = {}          # object uid -> stroke data (see begin)
last_message = [None]        # reason the last begin() refused
_tool_registered = [False]


# ----------------------------------------------------------------------------- core API
def unsupported_reason(obj):
    """Text if Morph History cannot work on obj (shape keys / dyntopo / multires), else None."""
    if obj is None or getattr(obj, "type", None) != "MESH":
        return "Morph History needs an active mesh object"
    if obj.data.shape_keys is not None:
        return "Morph History is not supported with Shape Keys"
    if getattr(obj, "use_dynamic_topology_sculpting", False):
        return "Morph History is not supported with Dyntopo"
    if any(m.type == 'MULTIRES' for m in obj.modifiers):
        return "Morph History is not supported with Multires"
    return None


def _refuse(msg: str) -> bool:
    last_message[0] = msg
    return False


def begin(obj) -> bool:
    """Start a stroke: validate, record pending changes, compute the target once.

    Returns False (reason in last_message[0]) if the brush cannot be used.
    """
    last_message[0] = None
    if obj is not None:
        end(obj, commit=False)
    reason = unsupported_reason(obj)
    if reason:
        return _refuse(reason)
    h = runtime.history_of(obj)
    if h is None or h.morph_target is None:
        return _refuse("No Morph Target set (History menu > Set Morph Target Here)")
    uid = runtime.obj_uid(obj)
    # Anything sculpted but not yet recorded becomes its own step first.
    runtime.commit(obj)
    h = runtime.history_of(obj)
    if h is None or h.morph_target is None:
        return _refuse("No Morph Target set (History menu > Set Morph Target Here)")
    st = runtime.ensure_state(obj)
    target = M.target_positions(h, st, uid)
    me = obj.data
    start = meshio.read_channel(me, POSITION_KEY)
    if target is None or start is None or target.size != start.size:
        return _refuse(MSG_REMESH)
    nv = start.size // 3
    mask = meshio.read_channel(me, MASK_KEY)
    if mask is not None and mask.size != nv:
        mask = None
    hidden = meshio.read_channel(me, HIDE_VERT_KEY)
    if hidden is not None and (hidden.size != nv or not hidden.any()):
        hidden = None
    susp = runtime.suspended()
    susp.__enter__()   # keep the recorder from splitting the stroke into several steps
    _SESSION[uid] = {"target": target, "start": start.copy(), "acc": np.zeros(nv, np.float64),
                     "mask": mask, "hidden": hidden, "susp": susp, "moved": False}
    return True


def apply_morph_at(obj, center_local, radius_local: float, strength: float) -> int:
    """One dab at center_local (object space). Returns the number of vertices moved.

    Within one stroke each vertex moves toward the target by the strongest
    weight it has received (relative to the stroke start), so one pass at
    strength s moves at most s of the way; more passes get closer.
    """
    s = _SESSION.get(runtime.obj_uid(obj))
    if s is None or radius_local is None or radius_local <= 0 or strength <= 0:
        return 0
    me = obj.data
    cur = meshio.read_channel(me, POSITION_KEY)
    if cur is None or cur.size != s["start"].size:
        return 0
    c = np.array(tuple(center_local)[:3], dtype=np.float64)
    co = cur.reshape(-1, 3)
    r = float(radius_local)
    # cheap box test first, then exact distances on the candidates
    near = np.all(np.abs(co - c) <= r, axis=1)
    if s["hidden"] is not None:
        near &= ~s["hidden"]
    cand = np.flatnonzero(near)
    if cand.size == 0:
        return 0
    d = np.sqrt(((co[cand].astype(np.float64) - c) ** 2).sum(axis=1))
    inside = d < r
    idx = cand[inside]
    if idx.size == 0:
        return 0
    w = M.falloff(d[inside], r) * float(strength)
    if s["mask"] is not None:
        w *= 1.0 - s["mask"][idx]
    acc = s["acc"]
    w = np.maximum(acc[idx], np.clip(w, 0.0, 1.0))
    acc[idx] = w
    before = co[idx].copy()
    co[idx] = s["start"].reshape(-1, 3)[idx]
    M.morph_blend(cur, s["target"], idx, w, 1.0)
    moved = int(np.count_nonzero(np.any(co[idx] != before, axis=1)))
    if moved:
        with runtime.suspended():
            meshio.write_channel(me, POSITION_KEY, cur)
            me.update()
            obj.update_tag()
        runtime.mark_written(obj)
        s["moved"] = True
    return moved


def end(obj, commit: bool = True):
    """Finish the stroke: record it as one normal step. Returns the Step or None."""
    uid = runtime.obj_uid(obj)
    s = _SESSION.pop(uid, None) if uid is not None else None
    if s is None:
        return None
    try:
        s["susp"].__exit__(None, None, None)
    except Exception:
        pass
    if not commit or not s["moved"]:
        return None
    step = runtime.commit(obj, label=STROKE_LABEL, mode=obj.mode)
    if step is not None and not bpy.app.background:
        try:
            bpy.ops.ed.undo_push(message=STROKE_LABEL)
        except Exception:
            pass
    runtime.tag_redraw_all()
    return step


def active() -> bool:
    return bool(_SESSION)


# ----------------------------------------------------------------------------- viewport helpers
def _brush_settings(context):
    ts = context.tool_settings
    ups = ts.unified_paint_settings
    brush = getattr(getattr(ts, "sculpt", None), "brush", None)

    def pick(unified_flag, name, default):
        if brush is None or getattr(ups, unified_flag, True):
            return getattr(ups, name, default)
        return getattr(brush, name, default)

    size = float(pick("use_unified_size", "size", 50))
    strength = float(pick("use_unified_strength", "strength", 0.5))
    locked = pick("use_unified_size", "use_locked_size", "VIEW")
    unproj = float(pick("use_unified_size", "unprojected_radius", 0.0))
    try:
        size *= context.preferences.system.ui_scale
    except Exception:
        pass
    return size, strength, (unproj if locked == "SCENE" and unproj > 0 else None)


def _hit(context, obj, coord):
    from bpy_extras import view3d_utils
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return None
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    mw = obj.matrix_world
    inv = mw.inverted_safe()
    o_l = inv @ origin
    d_l = (inv.to_3x3() @ direction).normalized()
    try:
        ok, loc, _nor, _idx = obj.ray_cast(o_l, d_l, depsgraph=context.evaluated_depsgraph_get())
    except Exception:
        try:
            ok, loc, _nor, _idx = obj.ray_cast(o_l, d_l)
        except Exception:
            return None
    return loc if ok else None


def _radius_local(context, obj, hit_local, radius_px, world_radius):
    from bpy_extras import view3d_utils
    mw = obj.matrix_world
    hit_w = mw @ hit_local
    rv3d = context.region_data
    right = rv3d.view_matrix.inverted().to_3x3().col[0].normalized()
    if world_radius is None:
        p0 = view3d_utils.location_3d_to_region_2d(context.region, rv3d, hit_w)
        p1 = view3d_utils.location_3d_to_region_2d(context.region, rv3d, hit_w + right)
        if p0 is None or p1 is None:
            return 0.0
        px_per_unit = (p1 - p0).length
        if px_per_unit <= 1e-9:
            return 0.0
        world_radius = radius_px / px_per_unit
    inv = mw.inverted_safe()
    return ((inv @ (hit_w + right * world_radius)) - hit_local).length


# ----------------------------------------------------------------------------- operator + tool
class HINDSIGHT_OT_morph_stroke(bpy.types.Operator):
    bl_idname = "hindsight.morph_stroke"
    bl_label = "Morph History"
    bl_description = "Paint the brushed area back toward the morph target"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return context.mode == 'SCULPT' and ob is not None and ob.type == 'MESH'

    def invoke(self, context, event):
        if not guard.ok("morph"):
            self.report({'WARNING'}, "Morph History is off after an error. Run self-check to try again.")
            return {'CANCELLED'}
        if context.area is None or context.area.type != 'VIEW_3D' or context.region_data is None:
            return {'PASS_THROUGH'}
        ob = context.active_object
        if not begin(ob):
            self.report({'WARNING'}, last_message[0] or "Morph History unavailable")
            return {'CANCELLED'}
        self._obj_name = ob.name
        self._last = None
        self._dab(context, ob, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _dab(self, context, ob, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        size, strength, world_r = _brush_settings(context)
        pts = [(x, y)]
        if self._last is not None:
            lx, ly = self._last
            dist = ((x - lx) ** 2 + (y - ly) ** 2) ** 0.5
            spacing = max(1.0, size * 0.15)
            n = int(dist // spacing)
            pts = [(lx + (x - lx) * (i / (n + 1)), ly + (y - ly) * (i / (n + 1)))
                   for i in range(1, n + 1)] + pts
        self._last = (x, y)
        for p in pts:
            hit = _hit(context, ob, p)
            if hit is None:
                continue
            r = _radius_local(context, ob, hit, size, world_r)
            if r > 0:
                apply_morph_at(ob, hit, r, strength)

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception as exc:
            guard.fail("morph", exc, "stroke")
            ob = bpy.data.objects.get(getattr(self, "_obj_name", ""))
            if ob is not None:
                try:
                    end(ob)   # keep what was painted so far as one step
                except Exception:
                    pass
            return {'CANCELLED'}

    def _modal(self, context, event):
        ob = bpy.data.objects.get(getattr(self, "_obj_name", ""))
        if ob is None or ob.mode != 'SCULPT':
            if ob is not None:
                end(ob, commit=False)
            return {'CANCELLED'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            if context.region_data is not None:
                self._dab(context, ob, event)
            return {'RUNNING_MODAL'}
        if (event.type == 'LEFTMOUSE' and event.value == 'RELEASE') or event.type in {'ESC', 'RIGHTMOUSE'}:
            end(ob)
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        ob = bpy.data.objects.get(getattr(self, "_obj_name", ""))
        if ob is not None:
            end(ob)


class HINDSIGHT_TOOL_morph_history(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'SCULPT'
    bl_idname = TOOL_ID
    bl_label = "Morph History"
    bl_description = ("Paint parts of the mesh back toward the morph target. Set the target "
                      "first (History Menu > Set morph target). Light strokes give a partial blend")
    bl_icon = "ops.sculpt.mesh_filter"
    bl_widget = None
    bl_keymap = (
        ("hindsight.morph_stroke", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
    )

    def draw_settings(context, layout, tool):
        ups = context.tool_settings.unified_paint_settings
        layout.prop(ups, "size", text="Radius")
        layout.prop(ups, "strength", text="Strength")


classes = (HINDSIGHT_OT_morph_stroke,)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    _tool_registered[0] = False
    try:
        bpy.utils.register_tool(HINDSIGHT_TOOL_morph_history, after={"builtin.annotate"},
                                separator=True, group=False)
        _tool_registered[0] = True
    except Exception as exc:
        log.error_once("register_tool", f"Morph History tool could not be added to the toolbar: {exc}", exc)


def unregister():
    for uid in list(_SESSION):
        s = _SESSION.pop(uid)
        try:
            s["susp"].__exit__(None, None, None)
        except Exception:
            pass
    if _tool_registered[0]:
        try:
            bpy.utils.unregister_tool(HINDSIGHT_TOOL_morph_history)
        except Exception:
            pass
        _tool_registered[0] = False
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
