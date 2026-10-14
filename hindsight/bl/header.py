"""Header button with a popover, and a Hindsight menu so the main actions are found with F3.

The popover holds the same controls as the sidebar tab, so the tab doesn't have to be open.
The menu is added to the Object, Sculpt, Paint (Vertex Paint) and Mesh (Edit Mode) menus;
Blender's F3 search finds anything that is in a menu.
"""
import bpy

from . import guard
from .ui_panel import _FAIL, _call, _operator, draw_help, draw_history_options, draw_main, draw_step_list
from .ctx import active_object as _ctx_active

UNITS = 13


class VIEW3D_PT_hindsight_popover(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Undo History"
    bl_ui_units_x = UNITS

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.label(text="Undo History", icon='RECOVER_LAST')
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.label(text="BETA")
        draw_main(layout, context, units=UNITS)
        box = layout.column()
        if draw_step_list(box, context, around=6):
            layout.separator()
        layout.separator()
        draw_history_options(layout, context)
        layout.separator()
        row = layout.row(align=True)
        _operator(row, "hindsight.open_history_menu", "History Menu", 'COLLAPSEMENU')
        _operator(row, "hindsight.self_check", "Self-check", 'CHECKMARK')
        _operator(row, "hindsight.copy_diagnostics", "", 'COPYDOWN')


@guard.guarded("header")
def draw_header_button(self, context):
    obj = _ctx_active(context)
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return
    info = _call("scrub_info", context)
    text = f"{info['pos']} / {info['len']}" if info not in (None, _FAIL) and info.get("len") else ""
    self.layout.popover(panel="VIEW3D_PT_hindsight_popover", text=text, icon='RECOVER_LAST')


class VIEW3D_MT_hindsight(bpy.types.Menu):
    bl_idname = "VIEW3D_MT_hindsight"
    bl_label = "Hindsight"

    def draw(self, context):
        layout = self.layout
        op = layout.operator("hindsight.step_back", text="Step Back", icon='TRIA_LEFT')
        op.native_fallback = False
        op = layout.operator("hindsight.step_forward", text="Step Forward", icon='TRIA_RIGHT')
        op.native_fallback = False
        layout.operator("hindsight.open_history_menu", text="Open History Menu", icon='COLLAPSEMENU')
        layout.separator()
        layout.operator("hindsight.self_check", text="Run Self-check", icon='CHECKMARK')
        layout.operator("hindsight.copy_diagnostics", text="Copy Diagnostics", icon='COPYDOWN')


def menu_entry(self, context):
    self.layout.separator()
    self.layout.menu(VIEW3D_MT_hindsight.bl_idname, icon='RECOVER_LAST')


_MENUS = ("VIEW3D_MT_object", "VIEW3D_MT_sculpt", "VIEW3D_MT_paint_vertex", "VIEW3D_MT_edit_mesh")
classes = (VIEW3D_PT_hindsight_popover, VIEW3D_MT_hindsight)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    # After the mode menus (View, Sculpt, Mask...): always on screen, unlike the far right end
    # of the header, which is cut off at common window widths.
    bpy.types.VIEW3D_MT_editor_menus.append(draw_header_button)
    for name in _MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            menu.append(menu_entry)


def unregister():
    for name in _MENUS:
        menu = getattr(bpy.types, name, None)
        if menu is not None:
            try:
                menu.remove(menu_entry)
            except Exception:
                pass
    try:
        bpy.types.VIEW3D_MT_editor_menus.remove(draw_header_button)
    except Exception:
        pass
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
