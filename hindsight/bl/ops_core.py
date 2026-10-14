"""Core operators: step back/forward (Ctrl+Z / Ctrl+Shift+Z), goto, truncation confirmation."""
import bpy

from . import runtime


class HINDSIGHT_OT_step_back(bpy.types.Operator):
    bl_idname = "hindsight.step_back"
    bl_label = "Step Back"
    bl_description = "Step back one step in the undo history (falls back to Blender's undo)"

    def execute(self, context):
        if not runtime.step(context, -1):
            try:
                return bpy.ops.ed.undo()
            except RuntimeError:
                return {'CANCELLED'}
        return {'FINISHED'}


class HINDSIGHT_OT_step_forward(bpy.types.Operator):
    bl_idname = "hindsight.step_forward"
    bl_label = "Step Forward"
    bl_description = "Step forward one step in the undo history (falls back to Blender's redo)"

    def execute(self, context):
        if not runtime.step(context, +1):
            try:
                return bpy.ops.ed.redo()
            except RuntimeError:
                return {'CANCELLED'}
        return {'FINISHED'}


class HINDSIGHT_OT_goto(bpy.types.Operator):
    bl_idname = "hindsight.goto"
    bl_label = "Go To Step"
    bl_description = "Jump to a step of the undo history"
    position: bpy.props.IntProperty(name="Position", default=0, min=0)

    def execute(self, context):
        runtime.goto(context, self.position, final=True)
        return {'FINISHED'}


class HINDSIGHT_OT_confirm_truncate(bpy.types.Operator):
    bl_idname = "hindsight.confirm_truncate"
    bl_label = "Start a New Branch?"
    bl_description = "Drop the steps after the current one so you can sculpt from here"
    count: bpy.props.IntProperty(default=0)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        col = self.layout.column()
        col.label(text=f"Sculpting here drops {self.count} later steps.", icon='ERROR')
        col.label(text="Your stroke was not applied. Click OK, then repeat it.")

    def execute(self, context):
        n = runtime.confirm_truncate(context.active_object)
        self.report({'INFO'}, f"Dropped {n} steps. Repeat your stroke to continue.")
        return {'FINISHED'}


classes = (HINDSIGHT_OT_step_back, HINDSIGHT_OT_step_forward, HINDSIGHT_OT_goto,
           HINDSIGHT_OT_confirm_truncate)

_keymaps = []


def _add_keymaps():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc is None:
        return
    for km_name in ("Sculpt", "Vertex Paint"):
        km = kc.keymaps.new(name=km_name, space_type='EMPTY')
        for mods in ({"ctrl": True}, {"oskey": True}):
            k = km.keymap_items.new("hindsight.step_back", 'Z', 'PRESS', **mods)
            _keymaps.append((km, k))
            k = km.keymap_items.new("hindsight.step_forward", 'Z', 'PRESS', shift=True, **mods)
            _keymaps.append((km, k))


def register():
    for c in classes:
        bpy.utils.register_class(c)
    _add_keymaps()


def unregister():
    for km, k in _keymaps:
        try:
            km.keymap_items.remove(k)
        except Exception:
            pass
    _keymaps.clear()
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
