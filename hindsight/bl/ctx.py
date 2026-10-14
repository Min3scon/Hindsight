"""Small context helpers shared by the UI and the runtime."""
import bpy


def active_object(context=None):
    """The active object, also when it is hidden in the viewport (Blender's `context.active_object`
    is None then, which would leave a hidden object's history out of reach)."""
    ctx = context if context is not None else bpy.context
    ob = getattr(ctx, "active_object", None)
    if ob is None:
        vl = getattr(ctx, "view_layer", None)
        try:
            ob = vl.objects.active if vl is not None else None
        except Exception:
            ob = None
    return ob
