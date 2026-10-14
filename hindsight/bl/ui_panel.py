"""Sidebar panel "Undo History" (VIEW3D_PT_hindsight) and its sub-panels.

Only the runtime API (bl/runtime.py) is used, imported lazily; every runtime call is guarded
so a failing or missing runtime simply draws nothing for that part.
"""

import bpy

from .prefs import draw_scrubber_props, get_prefs

_FAIL = object()


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


def _operator(layout, idname, text, icon='NONE'):
    if _op_exists(idname):
        return layout.operator(idname, text=text, icon=icon)
    sub = layout.row()
    sub.enabled = False
    sub.label(text=text, icon=icon)
    return None


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


class VIEW3D_PT_hindsight(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Undo History"
    bl_label = "Undo History"

    def draw(self, context):
        layout = self.layout
        obj = getattr(context, "active_object", None)

        col = layout.column(align=True)
        if obj is None:
            col.label(text="No active object", icon='INFO')
        else:
            h = _call("history_of", obj)
            if h is None:
                excluded = _call("is_excluded", obj)
                if excluded is not _FAIL and excluded:
                    col.label(text="Excluded by collection filtering", icon='FILTER')
                else:
                    col.label(text="Steps: 0 / 0")
            elif h is not _FAIL:
                try:
                    col.label(text=f"Steps: {int(h.cursor)} / {len(h.entries)}")
                except Exception:
                    pass

        stats = _call("memory_stats")
        if stats is not _FAIL:
            _draw_memory_readout(layout.column(align=True), stats)

        row = layout.row(align=True)
        _operator(row, "hindsight.reset_history", "Reset This Object", 'TRASH')
        _operator(row, "hindsight.reset_all", "Reset All Objects", 'TRASH')

        if hasattr(bpy.types, "HINDSIGHT_MT_history"):
            layout.menu("HINDSIGHT_MT_history", text="History Menu", icon='COLLAPSEMENU')


class _SubPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Undo History"
    bl_parent_id = "VIEW3D_PT_hindsight"


class VIEW3D_PT_hindsight_movie(_SubPanel, bpy.types.Panel):
    bl_label = "Movie Mode"
    bl_options = {'DEFAULT_CLOSED'}

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
            layout.label(text="Movie Mode needs Global Undo History", icon='INFO')
        col = layout.column()
        col.active = bool(sh.movie_mode)
        col.use_property_split = True
        col.use_property_decorate = True
        col.prop(sh, "movie_step", text="Step")
        col.prop(sh, "frames_per_step", text="Frames per Step")
        if hasattr(bpy.types, "HINDSIGHT_OT_quick_auto_key") or _op_exists("hindsight.quick_auto_key"):
            layout.operator("hindsight.quick_auto_key", text="Quick Auto Key", icon='KEY_HLT')
        else:
            row = layout.row()
            row.enabled = False
            row.label(text="Quick Auto Key", icon='KEY_HLT')


class VIEW3D_PT_hindsight_scrubber(_SubPanel, bpy.types.Panel):
    bl_label = "Scrubber"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        prefs = get_prefs(context)
        if prefs is None:
            layout.label(text="Preferences unavailable", icon='INFO')
            return
        layout.use_property_split = True
        layout.use_property_decorate = False
        draw_scrubber_props(layout.column(), prefs)


class VIEW3D_PT_hindsight_memory(_SubPanel, bpy.types.Panel):
    bl_label = "Memory"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        prefs = get_prefs(context)
        if prefs is not None:
            col = layout.column()
            col.use_property_split = True
            col.use_property_decorate = False
            col.prop(prefs, "memory_limit_mb")
            col.prop(prefs, "max_steps")
            col.prop(prefs, "raw_steps")
            col.prop(prefs, "compression")
        stats = _call("memory_stats")
        if stats is _FAIL or not isinstance(stats, dict):
            return
        col = layout.column(align=True)
        _draw_memory_readout(col, stats)
        try:
            limit = getattr(prefs, "memory_limit_mb", None) if prefs is not None else None
            used = _mb(stats.get("total_bytes", 0))
            if limit:
                col.label(text=f"Used: {used} of {int(limit)} MB")
            col.label(text=f"In RAM (uncompressed): {_mb(stats.get('ram_bytes', 0))}")
            col.label(text=f"Objects with history: {int(stats.get('objects', 0))}")
        except Exception:
            pass


class VIEW3D_PT_hindsight_filter(_SubPanel, bpy.types.Panel):
    bl_label = "Collection Filtering"
    bl_options = {'DEFAULT_CLOSED'}

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

        help_col = layout.column(align=True)
        if mode in {'NONE', 'BLACKLIST'}:
            help_col.label(text="Blacklist: exclude objects in these collections")
        if mode in {'NONE', 'WHITELIST'}:
            help_col.label(text="Whitelist: record only objects in these collections")
        help_col.active = mode != 'NONE'


class VIEW3D_PT_hindsight_warnings(_SubPanel, bpy.types.Panel):
    bl_label = "Warnings"

    @classmethod
    def poll(cls, context):
        return bool(_warnings(context))

    def draw(self, context):
        col = self.layout.column(align=True)
        for sev, text in _warnings(context):
            col.label(text=text, icon='ERROR' if sev == "ERROR" else 'INFO')


classes = (
    VIEW3D_PT_hindsight,
    VIEW3D_PT_hindsight_movie,
    VIEW3D_PT_hindsight_scrubber,
    VIEW3D_PT_hindsight_memory,
    VIEW3D_PT_hindsight_filter,
    VIEW3D_PT_hindsight_warnings,
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
