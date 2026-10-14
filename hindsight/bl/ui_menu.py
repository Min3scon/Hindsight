"""History menu (HINDSIGHT_MT_history) and its operators.

Every operator calls only the runtime API (``bl/runtime.py``, INTERFACES.md section 6), imported
lazily so this module can be registered before (or without) the runtime being importable.
None of these operators carries 'UNDO': they must never create native undo steps.
"""

import bpy

MORPH_TOOL_ID = "hindsight.morph_history"


def _rt():
    from . import runtime
    return runtime


def _settings(context):
    scene = getattr(context, "scene", None)
    return getattr(scene, "hindsight", None) if scene is not None else None


def _active_mesh(context):
    obj = getattr(context, "active_object", None)
    if obj is not None and obj.type == 'MESH':
        return obj
    return None


def _history(obj):
    if obj is None:
        return None
    try:
        return _rt().history_of(obj)
    except Exception:
        return None


def _recording(obj):
    if obj is None:
        return False
    try:
        return bool(_rt().is_recording(obj))
    except Exception:
        return False


def _confirm(op, context, event, message):
    wm = context.window_manager
    try:
        return wm.invoke_confirm(op, event, message=message)
    except TypeError:  # older signature without title/message keywords
        return wm.invoke_confirm(op, event)


class _ObjectHistoryOp:
    """Mixin: needs an active mesh object that has a history."""

    @classmethod
    def poll(cls, context):
        return _history(_active_mesh(context)) is not None


class HINDSIGHT_OT_toggle_global(bpy.types.Operator):
    bl_idname = "hindsight.toggle_global"
    bl_label = "Use Global Undo History"
    bl_description = "Switch between one object's history and a single timeline for all objects"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _settings(context) is not None

    @classmethod
    def description(cls, context, properties):
        s = _settings(context)
        if s is not None and s.use_global:
            return "Use the active object's own undo history instead of the single timeline for all objects"
        return "Use a single undo timeline for all objects instead of the active object's own history"

    def execute(self, context):
        on = not bool(_settings(context).use_global)
        _rt().set_global(context, on)
        self.report({'INFO'}, "Global undo history" if on else "Active object undo history")
        return {'FINISHED'}


class HINDSIGHT_OT_toggle_preserve_modes(bpy.types.Operator):
    bl_idname = "hindsight.toggle_preserve_modes"
    bl_label = "Preserve Modes"
    bl_description = "Keep each object's mode when Global history switches objects"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _settings(context) is not None

    def execute(self, context):
        on = not bool(_settings(context).preserve_modes)
        _rt().set_preserve_modes(context, on)
        self.report({'INFO'}, "Preserve Modes on" if on else "Preserve Modes off")
        return {'FINISHED'}


class HINDSIGHT_OT_set_morph_target(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.set_morph_target"
    bl_label = "Set Morph Target Here"
    bl_description = "Mark the current step as the morph target"
    bl_options = {'REGISTER'}

    def execute(self, context):
        _rt().set_morph_target(context)
        self.report({'INFO'}, "Morph target set")
        return {'FINISHED'}


class HINDSIGHT_OT_morph_brush(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.morph_brush"
    bl_label = "Morph History Brush"
    bl_description = "Activate the Morph History tool (Sculpt Mode)"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'SCULPT' and super().poll(context)

    def execute(self, context):
        try:
            result = bpy.ops.wm.tool_set_by_id(name=MORPH_TOOL_ID, space_type='VIEW_3D')
        except Exception:
            result = None
        if not result or 'FINISHED' not in result:
            self.report({'ERROR'}, "Morph History tool not available")
            return {'CANCELLED'}
        return {'FINISHED'}


class HINDSIGHT_OT_delete_past(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.delete_past"
    bl_label = "Delete Past Undo Steps"
    bl_description = ("Remove older steps to free memory and reduce saved size; "
                      "later steps stay")
    bl_options = {'REGISTER'}

    def execute(self, context):
        n = int(_rt().delete_past(context, _active_mesh(context)) or 0)
        if n:
            self.report({'INFO'}, "Deleted %d past step%s" % (n, "" if n == 1 else "s"))
        else:
            self.report({'INFO'}, "No past steps to delete")
        return {'FINISHED'}


class HINDSIGHT_OT_reset_history(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.reset_history"
    bl_label = "Reset Undo History"
    bl_description = "Clear this object's history; the current mesh becomes the new starting point"
    bl_options = {'REGISTER'}

    def execute(self, context):
        _rt().reset_history(context, _active_mesh(context))
        self.report({'INFO'}, "Undo history reset")
        return {'FINISHED'}


class HINDSIGHT_OT_turn_off(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.turn_off"
    bl_label = "Turn Off Undo History"
    bl_description = ("Pause recording and keep existing steps. Changes made while off become "
                      "the new start when turned back on")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and _recording(_active_mesh(context))

    def execute(self, context):
        _rt().turn_off(context, _active_mesh(context))
        self.report({'INFO'}, "Undo history turned off")
        return {'FINISHED'}


class HINDSIGHT_OT_reset_and_turn_off(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.reset_and_turn_off"
    bl_label = "Reset and Turn Off"
    bl_description = "Remove the history entirely and stop recording. This cannot be undone"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return _confirm(self, context, event, "Remove this object's undo history? This cannot be undone.")

    def execute(self, context):
        _rt().reset_and_turn_off(context, _active_mesh(context))
        self.report({'INFO'}, "Undo history removed and turned off")
        return {'FINISHED'}


class HINDSIGHT_OT_turn_on(bpy.types.Operator):
    bl_idname = "hindsight.turn_on"
    bl_label = "Turn On Undo History"
    bl_description = "Start recording again on an object where it was turned off"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        if obj is None:
            return False
        try:
            return not _rt().is_recording(obj)
        except Exception:
            return False

    def execute(self, context):
        _rt().turn_on(context, _active_mesh(context))
        self.report({'INFO'}, "Undo history turned on")
        return {'FINISHED'}


class HINDSIGHT_OT_reset_all(bpy.types.Operator):
    bl_idname = "hindsight.reset_all"
    bl_label = "Reset All Objects"
    bl_description = "Clear the undo history of every object. This cannot be undone"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return getattr(context, "scene", None) is not None

    def invoke(self, context, event):
        return _confirm(self, context, event, "Reset the undo history of all objects? This cannot be undone.")

    def execute(self, context):
        _rt().reset_all(context)
        self.report({'INFO'}, "Undo history reset for all objects")
        return {'FINISHED'}


class HINDSIGHT_MT_history(bpy.types.Menu):
    bl_idname = "HINDSIGHT_MT_history"
    bl_label = "History"

    def draw(self, context):
        layout = self.layout
        s = _settings(context)
        use_global = bool(s.use_global) if s is not None else False
        preserve = bool(s.preserve_modes) if s is not None else False

        layout.operator(
            HINDSIGHT_OT_toggle_global.bl_idname,
            text="Use Active Object Undo History" if use_global else "Use Global Undo History",
            icon='WORLD' if not use_global else 'OBJECT_DATA',
        )
        layout.operator(
            HINDSIGHT_OT_toggle_preserve_modes.bl_idname,
            text="Preserve Modes",
            icon='CHECKBOX_HLT' if preserve else 'CHECKBOX_DEHLT',
        )
        layout.separator()
        layout.operator(HINDSIGHT_OT_set_morph_target.bl_idname, icon='PINNED')
        layout.operator(HINDSIGHT_OT_morph_brush.bl_idname, icon='BRUSH_DATA')
        layout.separator()
        layout.operator(HINDSIGHT_OT_delete_past.bl_idname, icon='TRASH')
        layout.operator(HINDSIGHT_OT_reset_history.bl_idname, icon='FILE_REFRESH')
        obj = _active_mesh(context)
        if obj is not None and not _recording(obj):
            layout.operator(HINDSIGHT_OT_turn_on.bl_idname, icon='PLAY')
        else:
            layout.operator(HINDSIGHT_OT_turn_off.bl_idname, icon='PAUSE')
        layout.operator(HINDSIGHT_OT_reset_and_turn_off.bl_idname, icon='CANCEL')


classes = (
    HINDSIGHT_OT_toggle_global,
    HINDSIGHT_OT_toggle_preserve_modes,
    HINDSIGHT_OT_set_morph_target,
    HINDSIGHT_OT_morph_brush,
    HINDSIGHT_OT_delete_past,
    HINDSIGHT_OT_reset_history,
    HINDSIGHT_OT_turn_off,
    HINDSIGHT_OT_reset_and_turn_off,
    HINDSIGHT_OT_turn_on,
    HINDSIGHT_OT_reset_all,
    HINDSIGHT_MT_history,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
