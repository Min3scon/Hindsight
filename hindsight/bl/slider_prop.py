"""The sidebar slider: a plain integer property bound to the active object's current step.

It needs no custom drawing, so it works even if the on-screen slider can't draw. Blender has no
per-object range for a property, so the range is redefined whenever the history length changes.
That happens from a timer, never while drawing.
"""
import bpy

from . import guard, log

PROP = "hindsight_step"
_RANGE = [None]


def _info():
    try:
        from . import runtime
        return runtime.scrub_info(bpy.context)
    except Exception:
        return None


def _get(self):
    info = _info()
    return int(info["pos"]) if info else 0


def _set(self, value):
    info = _info()
    if not info or info.get("locked"):
        return
    v = max(0, min(int(value), int(info["len"])))
    if v == int(info["pos"]):
        return
    try:
        from . import runtime
        runtime.goto(bpy.context, v, final=True)
    except Exception as exc:
        log.error_once(("sidebar_slider", str(exc)), f"sidebar slider could not move: {exc}", exc)


def _define(n):
    top = max(1, int(n))
    setattr(bpy.types.WindowManager, PROP, bpy.props.IntProperty(
        name="Step", description="Drag to move through the history",
        min=0, max=top, soft_min=0, soft_max=top, get=_get, set=_set))
    _RANGE[0] = int(n)


def length():
    info = _info()
    return int(info["len"]) if info else 0


def sync():
    """Match the slider range to the current history length."""
    n = length()
    if n != _RANGE[0]:
        _define(n)
        guard._redraw()


def _timer():
    try:
        sync()
    except Exception as exc:
        log.error_once(("slider_prop", str(exc)), f"sidebar slider range error: {exc}", exc)
    return 0.2


def register():
    _define(0)
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.2, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    try:
        delattr(bpy.types.WindowManager, PROP)
    except Exception:
        pass
    _RANGE[0] = None
