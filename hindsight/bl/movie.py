"""Movie Mode: play the global history back from the Timeline (e.g. to render a time lapse).

Movie Mode only plays history back, it never changes it: no step is ever recorded from here.
"""
import contextlib
import inspect
import math

import bpy

from . import reg
from bpy.app.handlers import persistent

from . import guard, runtime

DATA_PATH = "hindsight.movie_step"
_BUSY = [False]   # re-entrancy guard (goto / keying write meshes and the Step value)
_SHOWN = {}       # the Step value last applied, what that jump was planned from, the whole frame it was for,
                  # the next step number then


def _takes_vis(fn) -> bool:
    """True if runtime.goto takes the visible objects listed by its caller (vis=)."""
    try:
        return "vis" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


_GOTO_TAKES_VIS = _takes_vis(runtime.goto)


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


def _histories_key(vis) -> tuple:
    """Everything a jump depends on: what is visible (vis) and every history's cursor and length."""
    r = runtime.reg()
    return (id(r), vis, tuple((h.uid, h.cursor, len(h.entries)) for h in r.histories.values()))


def _histories_now() -> tuple:
    return _histories_key(frozenset(runtime._visible_uids()))


def _seq() -> tuple:
    """What a recorded step changes, and what Blender's undo / redo changes (a cursor, no new step number)."""
    r = runtime.reg()
    return (id(r), r.next_seq, runtime.UNDO_MOVES[0])


def _recorded_since(want) -> bool:
    """True if, in the same registry, steps were recorded or Blender's undo / redo moved a history since
    the Step value `want` was last applied."""
    then, now = _SHOWN.get("seq"), _seq()
    return want == _SHOWN.get("want") and then is not None and then[0] == now[0] and then[1:] != now[1:]


def _apply(context, value) -> bool:
    """Move the global timeline to `value` if Movie Mode is active. True if it moved.

    Listing the visible objects walks every object in the file, so it is done once per call and
    the list is used for the check, the timeline and the record of what is shown. The record keeps
    the list from before the jump: the jump was planned from it, so a later call that finds the
    same list, cursors and Step value would plan the same jump and find nothing to do."""
    if _BUSY[0]:
        return False
    sh = _props(getattr(context, "scene", None))
    if not _active(sh):
        return False
    want = int(round(value))
    try:
        vis = frozenset(runtime._visible_uids())
    except Exception:                  # as scrub_info: no timeline, nothing to play
        return False
    if _SHOWN.get("want") == want and _SHOWN.get("histories") == _histories_key(vis):
        return False      # the step already shown (Frames per step above 1, or a render's sub-frames)
    try:                               # what scrub_info reads, without listing the objects again
        groups = runtime._groups(vis)
        pos = runtime._position(vis, groups) if groups else 0
    except Exception:                  # as scrub_info: no timeline, nothing to play
        return False
    if not groups:
        return False
    target = max(0, min(want, len(groups)))
    if _recorded_since(want):
        # #107: a step recorded with Movie Mode on (a stroke at the end) stays shown and the Step value
        # follows it; jumping back would let the next change throw it away. An undo of it sticks the same way
        want = target = pos
        _BUSY[0] = True
        try:
            sh.id_data.hindsight.movie_step = pos      # sh is a read-only proxy in the frame handler
        finally:
            _BUSY[0] = False
    if target == pos:
        _SHOWN.update(want=want, histories=_histories_key(vis), seq=_seq())
        return False
    prev = getattr(runtime.goto, "_movie", False)
    _BUSY[0] = True
    runtime.goto._movie = True
    runtime.IN_HANDLER[0] = True
    try:
        with runtime.suspended():
            if _GOTO_TAKES_VIS:
                runtime.goto(context, target, final=True, vis=vis)
            else:
                runtime.goto(context, target, final=True)
        _lamps_follow(context.scene)   # #112: still in the handler, so placing them doesn't walk the history
    finally:
        runtime.goto._movie = prev
        runtime.IN_HANDLER[0] = False
        _BUSY[0] = False
    _SHOWN.update(want=want, histories=_histories_key(vis), seq=_seq())
    return True


def _lamps_follow(scene) -> None:
    """#104: a jump can bring the user's light back or take it away. Movie lighting's lamps follow in
    the same frame, in a render too (the timer waits for it to end); one lamp the user deleted is left
    to the timer."""
    try:
        from . import camrig
    except ImportError:
        return
    if not scene.hindsight.movie_lighting:
        return
    roles = set(camrig.LIGHT_ROLES)
    if bool(camrig.wanted(scene) & roles) != bool(set(camrig.rig(scene)) & roles):
        camrig.ensure(scene, reframe=False)


@guard.guarded("movie")
def on_step_changed(context) -> None:
    """The user changed the Step value (drag / typing): same as a frame change."""
    sh = _props(context.scene)
    if sh is not None:
        _apply(context, sh.movie_step)


@guard.guarded("movie")
def follow_new_steps() -> None:
    """#107: the timer moves the Step value onto steps recorded while Movie Mode is on, so it says what
    the scene shows (a frame change that comes first does the same)."""
    sh = _props(bpy.context.scene)
    if _active(sh) and _recorded_since(sh.movie_step) and not bpy.app.is_job_running('RENDER'):
        _apply(bpy.context, sh.movie_step)      # not while a render reads the scene: after it


@guard.guarded("movie")      # #113: not persistent, so loading a file takes it away (start() puts it back)
def on_frame_change(scene, depsgraph=None) -> None:
    sh = _props(scene)
    if not _active(sh):
        return  # off (or no global history): playback paused, keyframes stay
    frame = scene.frame_current
    if scene.frame_subframe and _SHOWN.get("frame") in (frame, frame + 1) and \
            _SHOWN.get("histories") == _histories_now():
        return  # motion blur samples around the frame being rendered: keep that frame's step
    fc = _step_fcurve(scene) if scene.frame_subframe else None
    if fc is not None:
        value = math.floor(fc.evaluate(frame) + 0.5)    # the step of the whole frame
    else:
        value = sh.movie_step
        if depsgraph is not None:
            with contextlib.suppress(Exception):
                value = scene.evaluated_get(depsgraph).hindsight.movie_step
    _apply(_no_operator_context(bpy.context), value)
    _SHOWN["frame"] = frame


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


def _movie_frames(n: int, sh) -> int:
    """Frames from the first key to the last for n steps: Frames per step for each step, or, with
    Steps per frame above 1, that many steps on each frame."""
    return math.ceil(n * sh.frames_per_step / max(1, sh.movie_steps_per_frame))


def movie_length(scene) -> dict | None:
    """The movie Create time lapse makes with the settings as they are now: steps, frames rendered
    (first and last key included) and seconds at the scene's frame rate. None without a global
    history."""
    sh = _props(scene)
    if sh is None or not sh.use_global:
        return None
    info = runtime.scrub_info(_Proxy(bpy.context, scene=scene))
    if info is None or not info.get("global"):
        return None
    n = int(info["len"])
    frames = _movie_frames(n, sh) + 1
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    return {"steps": n, "frames": frames, "seconds": frames / fps}


class HINDSIGHT_OT_create_time_lapse(bpy.types.Operator):
    """Key the global history on the timeline and add a camera that keeps all of it in frame, with lighting
    when the scene has none"""
    bl_idname = "hindsight.create_time_lapse"
    bl_label = "Create time lapse"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        sh = _props(context.scene)
        if sh is None:
            return False
        if not sh.use_global:
            cls.poll_message_set("Movie Mode needs global history. Turn it on in the History Menu")
            return False
        return True

    def execute(self, context):
        scene = context.scene
        sh = scene.hindsight
        info = runtime.scrub_info(context)
        n = int(info["len"]) if info is not None and info.get("global") else 0
        if n <= 0:
            self.report({'WARNING'}, "No global history to keyframe yet")
            return {'CANCELLED'}
        f0 = scene.frame_start
        f1 = f0 + _movie_frames(n, sh)
        from . import camrig
        if not sh.movie_mode:     # #113: on now; the camera and lights come after the keys, below
            camrig._HOLD[0] = True
            try:
                sh.movie_mode = True
            finally:
                camrig._HOLD[0] = False
        _BUSY[0] = True
        try:
            fc = _step_fcurve(scene)
            old_end = None
            if fc is not None:  # re-keying replaces the previous first/last keys
                old_end = max((kp.co[0] for kp in fc.keyframe_points), default=None)
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
            if scene.frame_end < f1 or old_end is not None and scene.frame_end == old_end:
                scene.frame_end = f1     # also shorter, when the range ended at the old last key
            value = fc.evaluate(scene.frame_current) if fc is not None else sh.movie_step
            sh.movie_step = max(0, int(round(value)))
        finally:
            _BUSY[0] = False
        camrig.on_settings_changed(context)     # placed for the whole history, before the jump (whose
                                                # lamps check then finds them made)
        _apply(context, sh.movie_step)
        runtime.tag_redraw_all()
        self.report({'INFO'}, f"Keyed steps 0-{n} on frames {f0}-{f1}")
        return {'FINISHED'}


classes = (HINDSIGHT_OT_create_time_lapse,)


def start() -> None:
    """#113: the frame handler and the camera and lighting's check start when Movie Mode goes on (Create
    time lapse, or a file saved with it on), never before. They stay until another file is loaded: both do
    nothing while Movie Mode is off, and undo can switch it back on without telling anyone."""
    if on_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(on_frame_change)
    with contextlib.suppress(ImportError):
        from . import camrig
        camrig.start()


def _movie_mode_somewhere() -> bool:
    from . import camrig
    return any(sc.library is None and camrig._has_settings(sc) and sc.hindsight.movie_mode
               for sc in bpy.data.scenes)


@persistent
def _on_load_post(*_args) -> None:
    if _movie_mode_somewhere():
        start()


def register():
    reg.register_classes(classes)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    with contextlib.suppress(Exception):     # bpy.data is restricted while Blender starts: load_post does it
        if _movie_mode_somewhere():
            start()


def unregister():
    while _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    hl = bpy.app.handlers.frame_change_post
    for fn in list(hl):
        if fn is on_frame_change or getattr(fn, "__name__", "") == "on_frame_change" and \
                getattr(fn, "__module__", "") == __name__:
            hl.remove(fn)
    reg.unregister_classes(classes)
