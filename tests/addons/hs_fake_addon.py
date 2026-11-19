"""A stand-in for a third-party add-on (tests only): property groups on Object, Scene and Material,
a collection, a pointer to another object, and an operator that edits them the way add-ons do."""
import bpy

_classes = []


def _reg(cls):
    _classes.append(cls)
    return cls


@_reg
class HSFAKE_PG_item(bpy.types.PropertyGroup):
    level: bpy.props.IntProperty(default=1)
    tag: bpy.props.StringProperty(default="")


@_reg
class HSFAKE_PG_settings(bpy.types.PropertyGroup):
    strength: bpy.props.FloatProperty(default=0.5)
    mode: bpy.props.EnumProperty(items=[("SOFT", "Soft", ""), ("HARD", "Hard", "")], default="SOFT")
    flags: bpy.props.BoolVectorProperty(size=3)
    sub: bpy.props.PointerProperty(type=HSFAKE_PG_item)
    items: bpy.props.CollectionProperty(type=HSFAKE_PG_item)
    target: bpy.props.PointerProperty(type=bpy.types.Object)


@_reg
class HSFAKE_OT_bump(bpy.types.Operator):
    bl_idname = "hsfake.bump"
    bl_label = "Bump"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.object.hsfake
        s.strength += 0.25
        s.items.add().level = len(s.items) * 10
        return {"FINISHED"}


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Object.hsfake = bpy.props.PointerProperty(type=HSFAKE_PG_settings)
    bpy.types.Scene.hsfake = bpy.props.PointerProperty(type=HSFAKE_PG_settings)
    bpy.types.Material.hsfake = bpy.props.PointerProperty(type=HSFAKE_PG_settings)


def unregister():
    for t in (bpy.types.Object, bpy.types.Scene, bpy.types.Material):
        del t.hsfake
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
