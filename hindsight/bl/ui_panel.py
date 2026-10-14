"""Sidebar tab "Undo History" (VIEW3D_PT_hindsight) and its sub-panels.

Built only from standard widgets, so it shows even if the on-screen slider can't draw. It appears
in every mode and with no history yet. The runtime is imported lazily and every call is guarded:
a failing runtime draws a short note instead of breaking the panel.

The drawing helpers (draw_main, draw_history_options, draw_help) are shared with the header
popover in header.py.
"""
import textwrap

import bpy

from . import guard
from .beta import ISSUES_URL
from .prefs import draw_scrubber_props, get_prefs

_FAIL = object()

NO_HISTORY = "Hindsight is on. Make a stroke in Sculpt Mode to start recording."
NO_HISTORY_EDIT = "Hindsight is on. Edits are recorded once this object has a history."
CATEGORY = "Undo History"


def _rt():
    try:
        from . import runtime
        return runtime
    except Exception:
        return None


def _call(name, *args):
    """runtime.<name>(*args), or _FAIL if the runtime is missing or the call raises."""
    rt = _rt()
    if rt is None:
        return _FAIL
    try:
        return getattr(rt, name)(*args)
    except Exception:
        return _FAIL


def _scene_props(context):
    scene = getattr(context, "scene", None)
    return getattr(scene, "hindsight", None) if scene is not None else None


def _mb(nbytes) -> str:
    try:
        return f"{float(nbytes) / 2**20:.1f} MB"
    except Exception:
        return "0.0 MB"


def _op_exists(idname: str) -> bool:
    try:
        mod, name = idname.split(".", 1)
        if hasattr(bpy.types, f"{mod.upper()}_OT_{name}"):
            return True
        getattr(getattr(bpy.ops, mod), name).get_rna_type()
        return True
    except Exception:
        return False


def _operator(layout, idname, text, icon='NONE', **props):
    if _op_exists(idname):
        op = layout.operator(idname, text=text, icon=icon)
        for k, v in props.items():
            try:
                setattr(op, k, v)
            except Exception:
                pass
        return op
    sub = layout.row()
    sub.enabled = False
    sub.label(text=text, icon=icon)
    return None


def wrap(layout, context, text, icon='NONE', units=None):
    """Labels don't wrap in Blender: split text to fit the panel width."""
    try:
        scale = float(context.preferences.system.ui_scale) or 1.0
    except Exception:
        scale = 1.0
    width = None
    if units is not None:
        width = units * 20 * scale
    else:
        region = getattr(context, "region", None)
        width = getattr(region, "width", None)
    if not isinstance(width, (int, float)) or width <= 0:
        width = 260 * scale
    chars = max(18, int((width - (44 if icon != 'NONE' else 24) * scale) / (6.3 * scale)))
    col = layout.column(align=True)
    for i, line in enumerate(textwrap.wrap(str(text), chars, break_on_hyphens=False) or [""]):
        col.label(text=line, icon=icon if i == 0 else ('BLANK1' if icon != 'NONE' else 'NONE'))
    return col


def _warnings(context):
    obj = getattr(context, "active_object", None)
    res = _call("warnings_for", context, obj)
    if res is _FAIL or not res:
        return []
    out = []
    try:
        for sev, text in res:
            out.append((str(sev), str(text)))
    except Exception:
        return []
    return out


def _startup_errors():
    try:
        import importlib
        top = importlib.import_module(__package__.rsplit(".", 1)[0])
        return [n for n, _, _ in getattr(top, "_errors", [])]
    except Exception:
        return []


def _draw_problems(layout, context, units=None):
    """A short box when part of the add-on is switched off or failed to start."""
    off = [lbl for name, lbl, _, _ in guard.failed() if name not in guard.SOFT]
    soft = [lbl for name, lbl, _, _ in guard.failed() if name in guard.SOFT and name != "registration"]
    start = _startup_errors()
    if not (off or soft or start):
        return
    box = layout.box()
    if start:
        wrap(box, context, "Some parts did not start: " + ", ".join(start) + ".", 'ERROR', units)
    if off:
        wrap(box, context, "Switched off after an error: " + ", ".join(off) + ".", 'ERROR', units)
    if soft:
        wrap(box, context, "Last error: " + ", ".join(soft) + ". Your history is safe in memory.", 'INFO', units)
    col = box.column(align=True)
    _operator(col, "hindsight.self_check", "Run self-check", 'CHECKMARK')
    _operator(col, "hindsight.copy_diagnostics", "Copy diagnostics", 'COPYDOWN')


def draw_main(layout, context, units=None):
    """Status, slider, step counter, back and forward. Shared by the sidebar and the popover."""
    _draw_problems(layout, context, units)
    obj = getattr(context, "active_object", None)
    if obj is None:
        wrap(layout, context, "Select a mesh object to see its history.", 'INFO', units)
        return
    if getattr(obj, "type", None) != 'MESH':
        wrap(layout, context, "Hindsight records mesh objects. Select a mesh.", 'INFO', units)
        return

    excluded = _call("is_excluded", obj)
    if excluded is not _FAIL and excluded:
        wrap(layout, context, "Not recorded: this object is excluded by Collection Filtering.", 'FILTER', units)
        return
    recording = _call("is_recording", obj)
    has_uid = _call("obj_uid", obj)
    if recording is False and has_uid not in (None, _FAIL):
        wrap(layout, context, "Recording is off for this object.", 'PAUSE', units)
        _operator(layout, "hindsight.turn_on", "Enable for this object", 'REC')

    info = _call("scrub_info", context)
    if info is _FAIL:
        wrap(layout, context, "History unavailable. Run self-check.", 'ERROR', units)
        return
    if not info or not info.get("len"):
        if recording is not False or has_uid in (None, _FAIL):
            text = NO_HISTORY_EDIT if getattr(obj, "mode", "") == 'EDIT' else NO_HISTORY
            wrap(layout, context, text, 'INFO', units)
        return

    pos, n = int(info.get("pos", 0)), int(info.get("len", 0))
    locked = bool(info.get("locked"))
    wm = context.window_manager
    row = layout.row(align=True)
    row.enabled = not locked
    back = row.row(align=True)
    back.enabled = pos > 0
    _operator(back, "hindsight.step_back", "", 'TRIA_LEFT', native_fallback=False)
    if hasattr(wm, "hindsight_step"):
        row.prop(wm, "hindsight_step", text="", slider=True)
    else:
        row.label(text=f"{pos} / {n}")
    fwd = row.row(align=True)
    fwd.enabled = pos < n
    _operator(fwd, "hindsight.step_forward", "", 'TRIA_RIGHT', native_fallback=False)

    where = "global history" if info.get("global") else "this object"
    line = f"Step {pos} of {n}, {where}"
    mt = info.get("morph_target")
    col = layout.column(align=True)
    col.label(text=line)
    if mt is not None:
        col.label(text=f"Morph target: step {mt}", icon='PINNED')
    if locked:
        wrap(layout, context, "Locked while Movie Mode is on.", 'LOCKED', units)


def draw_history_options(layout, context):
    """The History Menu items as buttons."""
    sh = _scene_props(context)
    col = layout.column(align=True)
    if sh is not None:
        col.prop(sh, "use_global", text="Global history (all objects)")
        sub = col.row()
        sub.active = bool(sh.use_global)
        sub.prop(sh, "preserve_modes", text="Preserve modes")
    col = layout.column(align=True)
    row = col.row(align=True)
    _operator(row, "hindsight.set_morph_target", "Set morph target", 'PINNED')
    _operator(row, "hindsight.morph_brush", "Morph brush", 'BRUSH_DATA')
    row = col.row(align=True)
    _operator(row, "hindsight.delete_past", "Delete past steps", 'TRASH')
    _operator(row, "hindsight.reset_history", "Reset history", 'FILE_REFRESH')
    row = col.row(align=True)
    obj = getattr(context, "active_object", None)
    recording = _call("is_recording", obj) if obj is not None else _FAIL
    if recording is False:
        _operator(row, "hindsight.turn_on", "Turn recording on", 'REC')
    else:
        _operator(row, "hindsight.turn_off", "Turn recording off", 'PAUSE')
    _operator(row, "hindsight.reset_and_turn_off", "Reset and turn off", 'CANCEL')
    _operator(layout, "hindsight.reset_all", "Reset all objects", 'TRASH')


def draw_help(layout, context):
    from .selfcheck import addon_version, summary
    col = layout.column(align=True)
    _operator(col, "hindsight.self_check", "Run self-check", 'CHECKMARK')
    _operator(col, "hindsight.copy_diagnostics", "Copy diagnostics", 'COPYDOWN')
    op = _operator(col, "wm.url_open", "Report a bug", 'URL')
    if op is not None:
        op.url = ISSUES_URL
    s = summary()
    sub = layout.column(align=True)
    sub.scale_y = 0.85
    if s:
        sub.label(text="Last self-check: " + s)
    sub.label(text=f"Hindsight {addon_version()}. Beta: back up your files.")


def _draw_memory_readout(layout, stats):
    if not isinstance(stats, dict):
        return
    try:
        layout.label(text=f"This object: {_mb(stats.get('active_bytes', 0))}, "
                          f"{int(stats.get('active_steps', 0))} steps")
        layout.label(text=f"All objects: {_mb(stats.get('total_bytes', 0))}, "
                          f"{int(stats.get('steps', 0))} steps")
    except Exception:
        pass


# ----------------------------------------------------------------------------- panels
class VIEW3D_PT_hindsight(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_label = "Undo History"
    bl_order = 0

    def draw_header_preset(self, context):
        from .beta import draw_header_tag
        draw_header_tag(self.layout)

    def draw(self, context):
        layout = self.layout
        draw_main(layout, context)
        _operator(layout, "hindsight.open_history_menu", "History Menu", 'COLLAPSEMENU')


class _SubPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_parent_id = "VIEW3D_PT_hindsight"


class VIEW3D_PT_hindsight_warnings(_SubPanel, bpy.types.Panel):
    bl_label = "Warnings"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return bool(_warnings(context))

    def draw(self, context):
        col = self.layout.column(align=True)
        for sev, text in _warnings(context):
            wrap(col, context, text, 'ERROR' if sev == "ERROR" else 'INFO')


class VIEW3D_PT_hindsight_history(_SubPanel, bpy.types.Panel):
    bl_label = "History options"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 2

    def draw(self, context):
        draw_history_options(self.layout, context)


class VIEW3D_PT_hindsight_movie(_SubPanel, bpy.types.Panel):
    bl_label = "Movie Mode"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 3

    @classmethod
    def poll(cls, context):
        return _scene_props(context) is not None

    def draw_header(self, context):
        sh = _scene_props(context)
        if sh is not None:
            self.layout.prop(sh, "movie_mode", text="")

    def draw(self, context):
        layout = self.layout
        sh = _scene_props(context)
        if sh is None:
            return
        if not sh.use_global:
            wrap(layout, context, "Movie Mode plays the global history. Turn on Global history first.", 'INFO')
        col = layout.column()
        col.active = bool(sh.movie_mode)
        col.use_property_split = True
        col.use_property_decorate = True
        col.prop(sh, "movie_step", text="Step")
        col.prop(sh, "frames_per_step", text="Frames per step")
        _operator(layout, "hindsight.quick_auto_key", "Quick auto key", 'KEY_HLT')


class VIEW3D_PT_hindsight_filter(_SubPanel, bpy.types.Panel):
    bl_label = "Collection Filtering"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 4

    @classmethod
    def poll(cls, context):
        return _scene_props(context) is not None

    def draw(self, context):
        layout = self.layout
        sh = _scene_props(context)
        if sh is None:
            return
        layout.row().prop(sh, "filter_mode", expand=True)
        mode = sh.filter_mode

        row = layout.row()
        row.active = mode != 'NONE'
        row.template_list("HINDSIGHT_UL_collections", "", sh, "filter_collections",
                          sh, "filter_index", rows=3)
        col = row.column(align=True)
        col.operator("hindsight.filter_add", text="", icon='ADD')
        col.operator("hindsight.filter_remove", text="", icon='REMOVE')

        if mode == 'BLACKLIST':
            wrap(layout, context, "Objects in these collections are not recorded.")
        elif mode == 'WHITELIST':
            wrap(layout, context, "Only objects in these collections are recorded.")
        else:
            wrap(layout, context, "Every mesh object is recorded.")


class VIEW3D_PT_hindsight_memory(_SubPanel, bpy.types.Panel):
    bl_label = "Memory"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 5

    def draw(self, context):
        layout = self.layout
        stats = _call("memory_stats")
        prefs = get_prefs(context)
        if isinstance(stats, dict):
            col = layout.column(align=True)
            _draw_memory_readout(col, stats)
            limit = getattr(prefs, "memory_limit_mb", None) if prefs is not None else None
            if limit:
                col.label(text=f"Limit: {int(limit)} MB, oldest steps go first")
        if prefs is not None:
            col = layout.column()
            col.use_property_split = True
            col.use_property_decorate = False
            col.prop(prefs, "memory_limit_mb")
            col.prop(prefs, "max_steps")
            col.prop(prefs, "raw_steps")
            col.prop(prefs, "compression")


class VIEW3D_PT_hindsight_scrubber(_SubPanel, bpy.types.Panel):
    bl_label = "On-screen slider"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 6

    def draw(self, context):
        layout = self.layout
        if not guard.ok("slider"):
            wrap(layout, context, "The on-screen slider hit an error and is off. The slider above "
                                  "still works. Run self-check to try again.", 'ERROR')
        prefs = get_prefs(context)
        if prefs is None:
            layout.label(text="Preferences unavailable", icon='INFO')
            return
        layout.use_property_split = True
        layout.use_property_decorate = False
        draw_scrubber_props(layout.column(), prefs)


class VIEW3D_PT_hindsight_help(_SubPanel, bpy.types.Panel):
    bl_label = "Help"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 7

    def draw(self, context):
        draw_help(self.layout, context)


classes = (
    VIEW3D_PT_hindsight,
    VIEW3D_PT_hindsight_warnings,
    VIEW3D_PT_hindsight_history,
    VIEW3D_PT_hindsight_movie,
    VIEW3D_PT_hindsight_filter,
    VIEW3D_PT_hindsight_memory,
    VIEW3D_PT_hindsight_scrubber,
    VIEW3D_PT_hindsight_help,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
