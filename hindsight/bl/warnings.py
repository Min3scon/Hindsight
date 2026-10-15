"""Warnings system: per-object compatibility checks and the viewport error overlay.

`collect()` lists (severity, text) pairs for the sidebar Warnings panel (via runtime.warnings_for),
`blocking()` tells the recorder to pause, and a POST_PIXEL draw handler shows ERROR texts in the
3D Viewport while the active object is in Sculpt, Vertex Paint or Edit mode.
No GPU or blf calls happen at import or register time, so this module is safe headless.
"""

import bpy

from . import guard
from .ctx import active_object as _ctx_active

ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"

TXT_DYNTOPO = "Dyntopo is on, so recording is paused on this object"
TXT_MULTIRES = "Multires modifier: recording is paused on this object"
TXT_GLOBAL_UNDO = "Global Undo is off. Turn it on in Preferences > System"
TXT_LINKED = ("This mesh is shared with other objects (a linked duplicate), so they share one history. "
              "Shift+D makes copies with their own history")
TXT_SHAPE_KEYS = "Shape keys aren't part of the history"
TXT_DEFORM = "Deform modifiers such as Armature and Displace can make older steps look different"
TXT_MODIFIERS = "Modifiers aren't part of the history"
TXT_ANIMATED = "This object is animated, so older steps can look different on other frames"
TXT_VGROUPS = "Vertex groups and weight paint aren't part of the history"
TXT_MATERIALS = "Materials aren't part of the history"
TXT_LARGE = ("Large mesh ({verts:.1f}M vertices). Steps that change most of it use a lot of memory; "
             "the memory limit is {limit} MB")
LARGE_VERTS = 1_500_000

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


def blocking_reason(context, obj):
    """Why recording is paused for obj, or None."""
    if obj is None:
        return None
    try:
        if _has_dyntopo(obj):
            return "Dyntopo is on"
        if _has_multires(obj):
            return "this object has a Multires modifier"
    except Exception:
        return None
    return None


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
            nv = len(getattr(data, "vertices", ()))
            if nv >= LARGE_VERTS:
                from ..core.types import SETTINGS
                out.append((WARNING, TXT_LARGE.format(verts=nv / 1e6, limit=int(SETTINGS.memory_limit_mb))))
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

        out.extend(everything_notes(context, obj))
    except ReferenceError:
        return []
    out.sort(key=lambda it: _SEV_ORDER.get(it[0], 3))
    return out


# What Record everything leaves out, said where it applies (COVERAGE.md lists the same limits).
TXT_EV_LINKED = "Linked from another file: it can't be changed here, so Hindsight doesn't record it"
TXT_EV_NOT_ON = ("Only adding and deleting this object is recorded. Switch on Record > Everything on this "
                 "object to record its changes too")
TXT_EV_ANIMATED = "Animated or driven, so not recorded (they follow the animation): {names}"
TXT_EV_LISTS = "Lists in constraint settings (such as Armature constraint targets) aren't recorded"
TXT_EV_EDIT_BIG = "Over 300,000 vertices: Edit Mode is recorded as one step per visit on this mesh"
TXT_EV_COMEBACK = ("A deleted {kind} comes back only while its data is still in this session "
                   "(meshes and empties always come back)")
TXT_EV_NEW_COLLECTIONS = ("Collections you make or delete are undone only when the scene is recorded "
                          "(Record > Objects added and deleted)")
TXT_EV_SHARED_MESH = ("Shares its mesh with {owner} (a linked duplicate): changes to the mesh are recorded "
                      "in {owner}'s history")
TXT_EV_MISSING_REF = ("This step refers to {names}, which is deleted now. This object's own history can't "
                      "bring it back; the global history can")
TXT_EV_PARENT_DEFORM = ("Parenting with deform added a modifier ({names}). Modifiers and vertex groups aren't "
                        "recorded yet, so going back past the parenting keeps them")
_DEFORM_MODIFIERS = {"ARMATURE", "LATTICE", "CURVE"}


def everything_notes(context, obj) -> list:
    """Limits of Record everything that apply to this object (only when Record is Everything)."""
    try:
        from . import adapters, datarec, runtime
        if not datarec.scope_everything():
            return []
    except Exception:
        return []
    out = []
    if datarec.is_linked(obj):
        return [(INFO, TXT_EV_LINKED)]
    tracked = datarec.is_tracked(obj)
    scene_on = datarec.is_tracked(context.scene)
    if scene_on and not tracked:
        out.append((INFO, TXT_EV_NOT_ON))
    if scene_on and getattr(obj, "type", "MESH") not in ("MESH", "EMPTY"):
        out.append((INFO, TXT_EV_COMEBACK.format(kind=obj.type.lower().replace("_", " "))))
    if not tracked:
        return out
    anim = adapters.animated_paths(obj)
    if anim:
        rows = {p for rows in adapters.ALLOWLIST.values() for p, _k, _c in rows}
        # 'location' stays; 'constraints["Copy Location"].influence' reads as 'influence'
        names = sorted({p if p in rows else p.rsplit(".", 1)[-1] for p in anim
                        if p in rows or p.startswith("constraints[")})
        if names:
            out.append((INFO, TXT_EV_ANIMATED.format(names=", ".join(n.replace("_", " ") for n in names[:6]))))
    for c in getattr(obj, "constraints", ()):
        if any(p.type == "COLLECTION" and len(getattr(c, p.identifier, ())) for p in c.bl_rna.properties
               if p.identifier != "rna_type"):
            out.append((INFO, TXT_EV_LISTS))
            break
    parent = getattr(obj, "parent", None)
    deform = [m.name for m in getattr(obj, "modifiers", ())
              if parent is not None and m.type in _DEFORM_MODIFIERS and getattr(m, "object", None) == parent]
    if deform:
        out.append((INFO, TXT_EV_PARENT_DEFORM.format(names=", ".join(deform[:3]))))
    if getattr(obj, "type", None) == "MESH" and len(obj.data.vertices) > runtime.EDIT_STEPS_MAX:
        out.append((INFO, TXT_EV_EDIT_BIG))
    if not scene_on:
        out.append((INFO, TXT_EV_NEW_COLLECTIONS))
    if datarec.mesh_owned_elsewhere(obj):
        out.append((INFO, TXT_EV_SHARED_MESH.format(owner=datarec.mesh_owner_name(obj) or "another object")))
    refs = adapters.missing_refs(obj)
    if refs:
        names = sorted({str((datarec._BODIES.get(r) or {}).get("name") or "an object") for _p, r in refs})
        out.append((WARNING, TXT_EV_MISSING_REF.format(names=", ".join(names[:3]))))
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
        obj = _ctx_active(context)
        if obj is None or not _shares_with_tracked(obj):
            return None

        def draw_fn(menu, _context):
            col = menu.layout.column()
            col.label(text="This mesh is shared with other objects, so they share one history.")
            col.label(text="Shift+D makes copies with their own history.")

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
        obj = _ctx_active(context)
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
