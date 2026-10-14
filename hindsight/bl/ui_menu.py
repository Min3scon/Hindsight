"""History menu (HINDSIGHT_MT_history) and its operators.

Every operator calls only the runtime API (``bl/runtime.py``, INTERFACES.md section 6), imported
lazily so this module can be registered before (or without) the runtime being importable.
None of these operators carries 'UNDO': they must never create native undo steps.
"""

import bpy
from .ctx import active_object as _ctx_active

MORPH_TOOL_ID = "hindsight.morph_history"


def _rt():
    from . import runtime
    return runtime


def _settings(context):
    scene = getattr(context, "scene", None)
    return getattr(scene, "hindsight", None) if scene is not None else None


def _active_mesh(context):
    obj = _ctx_active(context)
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
    bl_label = "Use global history"
    bl_description = "Switch between one history per object and one timeline for every object"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _settings(context) is not None

    @classmethod
    def description(cls, context, properties):
        s = _settings(context)
        if s is not None and s.use_global:
            return "Go back to one history per object"
        return "Put every object's steps on one timeline, in the order you made them"

    def execute(self, context):
        on = not bool(_settings(context).use_global)
        _rt().set_global(context, on)
        self.report({'INFO'}, "Global history on" if on else "Per-object history on")
        return {'FINISHED'}


class HINDSIGHT_OT_toggle_preserve_modes(bpy.types.Operator):
    bl_idname = "hindsight.toggle_preserve_modes"
    bl_label = "Follow object and mode"
    bl_description = "When global history moves to another object's step, select that object and enter its mode"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _settings(context) is not None

    def execute(self, context):
        on = not bool(_settings(context).preserve_modes)
        _rt().set_preserve_modes(context, on)
        self.report({'INFO'}, "Follow object and mode on" if on else "Follow object and mode off")
        return {'FINISHED'}


class HINDSIGHT_OT_set_morph_target(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.set_morph_target"
    bl_label = "Set morph target"
    bl_description = "Use the current step as the shape the Morph History brush paints back to"
    bl_options = {'REGISTER'}

    def execute(self, context):
        _rt().set_morph_target(context)
        self.report({'INFO'}, "Morph target set")
        return {'FINISHED'}


class HINDSIGHT_OT_morph_brush(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.morph_brush"
    bl_label = "Use Morph History brush"
    bl_description = "Switch to the Morph History brush (Sculpt Mode)"
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
            self.report({'ERROR'}, "Morph History brush not available")
            return {'CANCELLED'}
        return {'FINISHED'}


class HINDSIGHT_OT_delete_past(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.delete_past"
    bl_label = "Delete earlier steps"
    bl_description = "Delete the steps before the current one to free memory. Later steps stay"
    bl_options = {'REGISTER'}

    def execute(self, context):
        n = int(_rt().delete_past(context, _active_mesh(context)) or 0)
        if n:
            self.report({'INFO'}, "Deleted %d earlier step%s" % (n, "" if n == 1 else "s"))
        else:
            self.report({'INFO'}, "No earlier steps to delete")
        return {'FINISHED'}


class HINDSIGHT_OT_reset_history(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.reset_history"
    bl_label = "Clear history"
    bl_description = "Delete this object's history. The mesh as it is now becomes the new start"
    bl_options = {'REGISTER'}

    def execute(self, context):
        _rt().reset_history(context, _active_mesh(context))
        self.report({'INFO'}, "History cleared")
        return {'FINISHED'}


class HINDSIGHT_OT_turn_off(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.turn_off"
    bl_label = "Pause recording"
    bl_description = "Stop recording this object and keep its steps. Changes made while paused become one step when you resume"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and _recording(_active_mesh(context))

    def execute(self, context):
        _rt().turn_off(context, _active_mesh(context))
        self.report({'INFO'}, "Recording paused")
        return {'FINISHED'}


class HINDSIGHT_OT_reset_and_turn_off(_ObjectHistoryOp, bpy.types.Operator):
    bl_idname = "hindsight.reset_and_turn_off"
    bl_label = "Clear and stop recording"
    bl_description = "Delete this object's history and stop recording it. Can't be undone"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return _confirm(self, context, event, "Delete this object's history and stop recording it? This can't be undone.")

    def execute(self, context):
        _rt().reset_and_turn_off(context, _active_mesh(context))
        self.report({'INFO'}, "History deleted. Recording stopped")
        return {'FINISHED'}


class HINDSIGHT_OT_turn_on(bpy.types.Operator):
    bl_idname = "hindsight.turn_on"
    bl_label = "Record this object"
    bl_description = "Start recording this object again"
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
        self.report({'INFO'}, "Recording")
        return {'FINISHED'}


class HINDSIGHT_OT_reset_all(bpy.types.Operator):
    bl_idname = "hindsight.reset_all"
    bl_label = "Clear all histories"
    bl_description = "Delete the history of every object. Can't be undone"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return getattr(context, "scene", None) is not None

    def invoke(self, context, event):
        return _confirm(self, context, event, "Delete the history of every object? This can't be undone.")

    def execute(self, context):
        _rt().reset_all(context)
        self.report({'INFO'}, "All histories cleared")
        return {'FINISHED'}


class HINDSIGHT_MT_history(bpy.types.Menu):
    bl_idname = "HINDSIGHT_MT_history"
    bl_label = "History Menu"

    def draw(self, context):
        layout = self.layout
        s = _settings(context)
        use_global = bool(s.use_global) if s is not None else False
        preserve = bool(s.preserve_modes) if s is not None else False

        layout.operator(
            HINDSIGHT_OT_toggle_global.bl_idname,
            text="Use per-object history" if use_global else "Use global history",
            icon='WORLD' if not use_global else 'OBJECT_DATA',
        )
        layout.operator(
            HINDSIGHT_OT_toggle_preserve_modes.bl_idname,
            text="Follow object and mode",
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
