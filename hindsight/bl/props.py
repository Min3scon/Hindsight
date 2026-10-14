"""Scene-level Hindsight settings (saved in the .blend)."""
import bpy


def _upd_global(self, context):
    from . import runtime
    runtime.reg().use_global = self.use_global
    runtime.tag_redraw_all()


def _upd_preserve(self, context):
    from . import runtime
    runtime.reg().preserve_modes = self.preserve_modes


def _upd_movie_step(self, context):
    try:
        from . import movie
        movie.on_step_changed(context)
    except ImportError:
        pass


def _upd_redraw(self, context):
    from . import runtime
    runtime.tag_redraw_all()


class HINDSIGHT_PG_coll_item(bpy.types.PropertyGroup):
    collection: bpy.props.PointerProperty(type=bpy.types.Collection, name="Collection")


class HINDSIGHT_PG_scene(bpy.types.PropertyGroup):
    use_global: bpy.props.BoolProperty(
        name="Global history", default=False, update=_upd_global,
        description="Put every object's steps on one timeline instead of one history per object")
    preserve_modes: bpy.props.BoolProperty(
        name="Follow object and mode", default=True, update=_upd_preserve,
        description="When global history moves to another object's step, select that object and enter its mode")
    movie_mode: bpy.props.BoolProperty(
        name="Movie Mode", default=False, update=_upd_redraw,
        description="Play the global history on the timeline. The history itself can't change while this is on")
    movie_step: bpy.props.IntProperty(
        name="Step", default=0, min=0, update=_upd_movie_step, options={'ANIMATABLE'},
        description="The global history step Movie Mode shows. Keyframe it to make a timelapse")
    frames_per_step: bpy.props.IntProperty(
        name="Frames per step", default=2, min=1, max=1000,
        description="How many frames each step lasts when you use Quick auto key")
    filter_mode: bpy.props.EnumProperty(
        name="Collection Filtering",
        items=[("NONE", "Off", "Record every object"),
               ("BLACKLIST", "Blacklist", "Exclude objects in the listed collections"),
               ("WHITELIST", "Whitelist", "Record only objects in the listed collections")],
        default="NONE", update=_upd_redraw)
    filter_collections: bpy.props.CollectionProperty(type=HINDSIGHT_PG_coll_item)
    filter_index: bpy.props.IntProperty(default=0)


classes = (HINDSIGHT_PG_coll_item, HINDSIGHT_PG_scene)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.hindsight = bpy.props.PointerProperty(type=HINDSIGHT_PG_scene)


def unregister():
    try:
        del bpy.types.Scene.hindsight
    except AttributeError:
        pass
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
