"""Movie Mode: play the global history back from the Timeline (e.g. to render a timelapse).

Movie Mode only plays history back, it never changes it: no step is ever recorded from here.
"""
import bpy
from bpy.app.handlers import persistent

from . import guard, runtime

DATA_PATH = "hindsight.movie_step"
_BUSY = [False]   # re-entrancy guard (goto / keying write meshes and the Step value)


def _props(scene):
    return getattr(scene, "hindsight", None) if scene is not None else None


def _active(sh) -> bool:
    return bool(sh is not None and sh.movie_mode and sh.use_global)


class _Proxy:
    """Read-only view of a bpy object with some attributes overridden."""
    def __init__(self, base, **over):
        self.__dict__["_base"] = base
        self.__dict__.update(over)

    def __getattr__(self, name):
        return getattr(self.__dict__["_base"], name)


def _no_operator_context(context):
    """Context for goto() inside a frame-change handler: Preserve Modes off, because calling
    operators (mode switches) while the depsgraph is being evaluated crashes Blender."""
    sc = context.scene
    return _Proxy(context, scene=_Proxy(sc, hindsight=_Proxy(sc.hindsight, preserve_modes=False)))


def _apply(context, value) -> bool:
    """Move the global timeline to `value` if Movie Mode is active. True if it moved."""
    if _BUSY[0]:
        return False
    sh = _props(getattr(context, "scene", None))
    if not _active(sh):
        return False
    info = runtime.scrub_info(context)
    if info is None or not info.get("global"):
        return False
    target = max(0, min(int(round(value)), int(info["len"])))
    if target == info["pos"]:
        return False
    prev = getattr(runtime.goto, "_movie", False)
    _BUSY[0] = True
    runtime.goto._movie = True
    runtime.IN_HANDLER[0] = True
    try:
        with runtime.suspended():
            runtime.goto(context, target, final=True)
    finally:
        runtime.goto._movie = prev
        runtime.IN_HANDLER[0] = False
        _BUSY[0] = False
    return True


@guard.guarded("movie")
def on_step_changed(context) -> None:
    """The user changed the Step value (drag / typing): same as a frame change."""
    sh = _props(context.scene)
    if sh is not None:
        _apply(context, sh.movie_step)


@persistent
@guard.guarded("movie")
def on_frame_change(scene, depsgraph=None) -> None:
    sh = _props(scene)
    if not _active(sh):
        return  # off (or no global history): playback paused, keyframes stay
    value = sh.movie_step
    if depsgraph is not None:
        try:
            value = scene.evaluated_get(depsgraph).hindsight.movie_step
        except Exception:
            pass
    _apply(_no_operator_context(bpy.context), value)


def _step_fcurve(scene):
    ad = scene.animation_data
    act = ad.action if ad is not None else None
    if act is None:
        return None
    return action_fcurves(ad).find(DATA_PATH) if action_fcurves(ad) is not None else None


def action_fcurves(ad):
    """F-Curves of an AnimData's action: legacy Action.fcurves (<= 4.x) or the slot's channel bag (5.x)."""
    act = ad.action if ad is not None else None
    if act is None:
        return None
    fcs = getattr(act, "fcurves", None)
    if fcs is not None:
        return fcs
    try:
        from bpy_extras.anim_utils import action_get_channelbag_for_slot
        cb = action_get_channelbag_for_slot(act, ad.action_slot)
        return cb.fcurves if cb is not None else None
    except Exception:
        return None


class HINDSIGHT_OT_quick_auto_key(bpy.types.Operator):
    """Keyframe the first and last steps of the global history on the Timeline"""
    bl_idname = "hindsight.quick_auto_key"
    bl_label = "Quick Auto Key"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        sh = _props(context.scene)
        if sh is None:
            return False
        if not sh.use_global:
            cls.poll_message_set("Movie Mode needs Global Undo History")
            return False
        return True

    def execute(self, context):
        scene = context.scene
        sh = scene.hindsight
        info = runtime.scrub_info(context)
        n = int(info["len"]) if info is not None and info.get("global") else 0
        if n <= 0:
            self.report({'WARNING'}, "No global undo history to key")
            return {'CANCELLED'}
        f0 = scene.frame_start
        f1 = f0 + n * sh.frames_per_step
        _BUSY[0] = True
        try:
            fc = _step_fcurve(scene)
            if fc is not None:  # re-keying replaces the previous first/last keys
                fc.keyframe_points.clear()
            sh.movie_step = 0
            scene.keyframe_insert(data_path=DATA_PATH, frame=f0)
            sh.movie_step = n
            scene.keyframe_insert(data_path=DATA_PATH, frame=f1)
            fc = _step_fcurve(scene)
            if fc is not None:
                for kp in fc.keyframe_points:
                    kp.interpolation = 'LINEAR'
                fc.update()
            if scene.frame_end < f1:
                scene.frame_end = f1
            value = fc.evaluate(scene.frame_current) if fc is not None else sh.movie_step
            sh.movie_step = max(0, int(round(value)))
        finally:
            _BUSY[0] = False
        _apply(context, sh.movie_step)
        runtime.tag_redraw_all()
        self.report({'INFO'}, f"Keyed steps 0-{n} on frames {f0}-{f1}")
        return {'FINISHED'}


classes = (HINDSIGHT_OT_quick_auto_key,)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    if on_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(on_frame_change)


def unregister():
    hl = bpy.app.handlers.frame_change_post
    for fn in list(hl):
        if fn is on_frame_change or getattr(fn, "__name__", "") == "on_frame_change" and \
                getattr(fn, "__module__", "") == __name__:
            hl.remove(fn)
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
