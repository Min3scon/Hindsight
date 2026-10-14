"""Warnings system: per-object compatibility checks and the viewport error overlay.

`collect()` lists (severity, text) pairs for the sidebar Warnings panel (via runtime.warnings_for),
`blocking()` tells the recorder to pause, and a POST_PIXEL draw handler shows ERROR texts in the
3D Viewport while the active object is in Sculpt, Vertex Paint or Edit mode.
No GPU or blf calls happen at import or register time, so this module is safe headless.
"""

import bpy

from . import guard

ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"

TXT_DYNTOPO = "Dyntopo is not supported: history is paused"
TXT_MULTIRES = "Multires is not supported: history is paused"
TXT_GLOBAL_UNDO = "Enable Global Undo in Preferences > System"
TXT_LINKED = ("Mesh data is shared with other objects (linked duplicate): history affects all of them. "
              "Use Shift+D for independent history")
TXT_SHAPE_KEYS = "Shape keys are not stored in history"
TXT_DEFORM = "Deform modifiers (Armature, Displace...) can make playback unpredictable"
TXT_MODIFIERS = "Modifiers are not stored in history"
TXT_ANIMATED = "Animated object: playback may differ from what was recorded"
TXT_VGROUPS = "Vertex groups and weight paint are not stored"
TXT_MATERIALS = "Materials are not stored in history"
TXT_TRANSFORM = ("Object transforms are not recorded: moving the object can make older steps "
                 "play back in the wrong place")

DEFORM_TYPES = frozenset({
    'ARMATURE', 'DISPLACE', 'LATTICE', 'CURVE', 'HOOK', 'MESH_DEFORM', 'SURFACE_DEFORM', 'CAST',
    'SIMPLE_DEFORM', 'SMOOTH', 'LAPLACIANSMOOTH', 'CORRECTIVE_SMOOTH', 'WARP', 'WAVE', 'SHRINKWRAP',
})

OVERLAY_MODES = frozenset({'SCULPT', 'VERTEX_PAINT', 'EDIT'})
OVERLAY_COLOR = (1.0, 0.42, 0.22, 1.0)   # red-orange
OVERLAY_TOP = 60                         # px from the region top (before ui_scale)
OVERLAY_SIZE = 13                        # font size (before ui_scale)

_SEV_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


def _prefs(context):
    prefs = getattr(context, "preferences", None)
    return prefs if prefs is not None else bpy.context.preferences


def _global_undo_on(context):
    try:
        return bool(_prefs(context).edit.use_global_undo)
    except Exception:
        return True


def _has_dyntopo(obj):
    return getattr(obj, "mode", None) == 'SCULPT' and bool(getattr(obj, "use_dynamic_topology_sculpting", False))


def _has_multires(obj):
    return any(m.type == 'MULTIRES' for m in getattr(obj, "modifiers", ()))


def _is_transformed(obj):
    try:
        if abs(obj.matrix_world.determinant() - 1.0) > 1e-6:
            return True
        if obj.location.length > 0:
            return True
        return any(abs(r) > 0 for r in obj.rotation_euler)
    except Exception:
        return False


def blocking(context, obj) -> bool:
    """True when recording must pause for obj (Dyntopo or Multires)."""
    if obj is None:
        return False
    try:
        return _has_dyntopo(obj) or _has_multires(obj)
    except Exception:
        return False


def collect(context, obj) -> list:
    """Return [(severity, text), ...] for obj, errors first, then warnings, then info."""
    out = []
    if obj is None:
        return out
    try:
        if _has_dyntopo(obj):
            out.append((ERROR, TXT_DYNTOPO))
        if _has_multires(obj):
            out.append((ERROR, TXT_MULTIRES))
        if not _global_undo_on(context):
            out.append((ERROR, TXT_GLOBAL_UNDO))

        data = getattr(obj, "data", None)
        if data is not None and getattr(obj, "type", 'MESH') == 'MESH':
            if getattr(data, "users", 1) > 1:
                out.append((WARNING, TXT_LINKED))
            if getattr(data, "shape_keys", None) is not None:
                out.append((WARNING, TXT_SHAPE_KEYS))

        mods = [m for m in getattr(obj, "modifiers", ()) if m.type != 'MULTIRES']
        if any(m.type in DEFORM_TYPES for m in mods):
            out.append((WARNING, TXT_DEFORM))
        if any(m.type not in DEFORM_TYPES for m in mods):
            out.append((INFO, TXT_MODIFIERS))

        ad = getattr(obj, "animation_data", None)
        if ad is not None and getattr(ad, "action", None) is not None:
            out.append((WARNING, TXT_ANIMATED))

        if len(getattr(obj, "vertex_groups", ())) > 0:
            out.append((INFO, TXT_VGROUPS))
        if any(getattr(sl, "material", None) is not None for sl in getattr(obj, "material_slots", ())):
            out.append((INFO, TXT_MATERIALS))

        if _is_transformed(obj):
            out.append((INFO, TXT_TRANSFORM))
    except ReferenceError:
        return []
    out.sort(key=lambda it: _SEV_ORDER.get(it[0], 3))
    return out


# --------------------------------------------------------------------------------------------
# Linked duplicate popup (called by the duplicate handler after Alt+D)

def _shares_with_tracked(obj):
    data = getattr(obj, "data", None)
    if data is None or getattr(obj, "type", None) != 'MESH' or getattr(data, "users", 1) < 2:
        return False
    for other in bpy.data.objects:
        if other == obj or other.data != data:
            continue
        if other.get("_hindsight_uid"):
            return True
    return False


def linked_duplicate_check(context) -> None:
    """Show a popup if the active object shares mesh data with an object that has Hindsight history."""
    try:
        obj = getattr(context, "active_object", None)
        if obj is None or not _shares_with_tracked(obj):
            return None

        def draw_fn(menu, _context):
            col = menu.layout.column()
            col.label(text="Mesh data is shared with other objects (linked duplicate).")
            col.label(text="History affects all of them.")
            col.label(text="Use Shift+D for independent history.")

        context.window_manager.popup_menu(draw_fn, title="Hindsight", icon='ERROR')
    except Exception as exc:
        from . import log
        log.error_once(("linked_popup", str(exc)), f"linked duplicate popup failed: {exc}", exc)
    return None


# --------------------------------------------------------------------------------------------
# Viewport overlay

_handle = None


def _overlay_x(context, region, scale):
    """Left offset clearing the toolbar when it overlaps the WINDOW region."""
    x = 12 * scale
    try:
        if context.preferences.system.use_region_overlap:
            for r in context.area.regions:
                if r.type == 'TOOLS' and r.width > 1 and r.x <= region.x + 1:
                    x += r.width
                    break
    except Exception:
        pass
    return x


def _draw_overlay():
    if not guard.ok("overlay"):
        return
    try:
        context = bpy.context
        region = context.region
        if region is None or region.type != 'WINDOW':
            return
        obj = context.active_object
        if obj is None or obj.mode not in OVERLAY_MODES:
            return
        errors = [text for sev, text in collect(context, obj) if sev == ERROR]
        if not errors:
            return
        import blf
        scale = context.preferences.system.ui_scale or 1.0
        font_id = 0
        size = OVERLAY_SIZE * scale
        blf.size(font_id, size)
        blf.color(font_id, *OVERLAY_COLOR)
        try:
            blf.enable(font_id, blf.SHADOW)
            blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.8)
            blf.shadow_offset(font_id, 1, -1)
        except Exception:
            pass
        x = _overlay_x(context, region, scale)
        y = region.height - OVERLAY_TOP * scale
        line = size * 1.5
        for text in errors:
            blf.position(font_id, x, y, 0)
            blf.draw(font_id, "Hindsight: " + text)
            y -= line
        try:
            blf.disable(font_id, blf.SHADOW)
        except Exception:
            pass
    except Exception as exc:
        guard.fail("overlay", exc, "draw")


def _remove_overlay():
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        except Exception:
            pass
        _handle = None


guard.on_fail("overlay", _remove_overlay)

classes = ()


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw_overlay, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        except Exception:
            pass
        _handle = None
