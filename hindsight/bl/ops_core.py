"""Core operators: step back/forward (Ctrl+Z / Ctrl+Shift+Z), goto, truncation confirmation."""
import bpy

from . import runtime
from .ctx import active_object as _ctx_active


def _fallback(op, context, native, edge_text):
    """At either end of the history. Blender's own undo/redo runs only when it is safe: when the
    object has no history, or Hindsight hasn't written its mesh since it entered this mode."""
    obj = _ctx_active(context)
    info = runtime.scrub_info(context)
    if op.native_fallback and (info is None or not info.get("len") or obj is None or not runtime.wrote(obj)):
        try:
            return native()
        except RuntimeError:
            return {'CANCELLED'}
    if info is not None and info.get("len"):
        op.report({'INFO'}, edge_text)
    return {'CANCELLED'}


class HINDSIGHT_OT_step_back(bpy.types.Operator):
    bl_idname = "hindsight.step_back"
    bl_label = "Hindsight: Step Back"
    bl_description = "Go back one step in the history"

    native_fallback: bpy.props.BoolProperty(
        name="Use Blender undo at the start", default=True, options={'SKIP_SAVE', 'HIDDEN'},
        description="When there is no earlier step, run Blender's own undo instead")

    def execute(self, context):
        if runtime.step(context, -1):
            return {'FINISHED'}
        return _fallback(self, context, bpy.ops.ed.undo, "Start of the history")


class HINDSIGHT_OT_step_forward(bpy.types.Operator):
    bl_idname = "hindsight.step_forward"
    bl_label = "Hindsight: Step Forward"
    bl_description = "Go forward one step in the history"

    native_fallback: bpy.props.BoolProperty(
        name="Use Blender redo at the end", default=True, options={'SKIP_SAVE', 'HIDDEN'},
        description="When there is no later step, run Blender's own redo instead")

    def execute(self, context):
        if runtime.step(context, +1):
            return {'FINISHED'}
        return _fallback(self, context, bpy.ops.ed.redo, "End of the history")


class HINDSIGHT_OT_goto(bpy.types.Operator):
    bl_idname = "hindsight.goto"
    bl_label = "Go to Step"
    bl_description = "Jump to a step in the history"
    position: bpy.props.IntProperty(name="Position", default=0, min=0)

    def execute(self, context):
        runtime.goto(context, self.position, final=True)
        return {'FINISHED'}


class HINDSIGHT_OT_confirm_truncate(bpy.types.Operator):
    bl_idname = "hindsight.confirm_truncate"
    bl_label = "Start a new branch"
    bl_description = "Drop the steps after the current one so you can sculpt from here"
    count: bpy.props.IntProperty(default=0)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        col = self.layout.column()
        col.label(text=f"Sculpting here drops {self.count} later steps.", icon='ERROR')
        col.label(text="Your stroke was not applied. Click OK, then repeat it.")

    def execute(self, context):
        n = runtime.confirm_truncate(_ctx_active(context))
        self.report({'INFO'}, f"Dropped {n} steps. Repeat your stroke to continue.")
        return {'FINISHED'}


class HINDSIGHT_OT_record_everything(bpy.types.Operator):
    bl_idname = "hindsight.record_everything"
    bl_label = "Record Everything on This Object"
    bl_description = ("Also record changes other than sculpting on this object, such as moving it. "
                      "Needs Record set to Everything in the preferences")
    bl_options = {'REGISTER'}

    on: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _ctx_active(context) is not None

    def execute(self, context):
        from . import datarec
        ob = _ctx_active(context)
        if self.on and datarec.is_linked(ob):
            self.report({'WARNING'}, datarec.LINKED_TEXT)
            return {'CANCELLED'}
        datarec.set_tracked(ob, self.on)
        from . import runtime
        runtime.tag_redraw_all()
        return {'FINISHED'}


class HINDSIGHT_OT_record_scene(bpy.types.Operator):
    bl_idname = "hindsight.record_scene"
    bl_label = "Record Adding and Deleting Objects"
    bl_description = ("Record objects being added, duplicated and deleted in this scene, so scrubbing the "
                      "global history brings them back or removes them. Needs Record set to Everything")
    bl_options = {'REGISTER'}

    on: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})

    def execute(self, context):
        from . import datarec, runtime
        datarec.set_tracked(context.scene, self.on)
        runtime.tag_redraw_all()
        return {'FINISHED'}


classes = (HINDSIGHT_OT_step_back, HINDSIGHT_OT_step_forward, HINDSIGHT_OT_goto, HINDSIGHT_OT_record_everything,
           HINDSIGHT_OT_record_scene,
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
