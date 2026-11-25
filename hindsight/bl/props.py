"""Scene-level Hindsight settings (saved in the .blend)."""
import contextlib
import bpy

from . import reg


def _upd_global(self, context):
    from . import runtime
    runtime.reg().use_global = self.use_global
    runtime.tag_redraw_all()


def _upd_preserve(self, context):
    from . import runtime
    runtime.reg().preserve_modes = self.preserve_modes


def _upd_movie_step(self, context):
    with contextlib.suppress(ImportError):
        from . import movie
        movie.on_step_changed(context)


def _upd_redraw(self, context):
    from . import runtime
    runtime.tag_redraw_all()


def _upd_movie_rig(self, context):
    """Movie Mode or one of its camera and lighting settings changed."""
    if self.movie_mode:
        with contextlib.suppress(ImportError):
            from . import movie
            movie.start()
    with contextlib.suppress(ImportError):
        from . import camrig
        camrig.on_settings_changed(context)
    _upd_redraw(self, context)


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
        name="Movie Mode", default=False, update=_upd_movie_rig,
        description="On while the time lapse plays on the timeline (Create time lapse switches it on). "
                    "Switch it off to change the history again: the time lapse's camera and lighting go with it")
    movie_step: bpy.props.IntProperty(
        name="Step", default=0, min=0, update=_upd_movie_step, options={'ANIMATABLE'},
        description="The global history step Movie Mode shows. Keyframe it to make a timelapse")
    frames_per_step: bpy.props.IntProperty(
        name="Frames per step", default=2, min=1, max=1000,
        description="How many frames each step lasts in the time lapse")
    movie_steps_per_frame: bpy.props.IntProperty(
        name="Steps per frame", default=1, min=1, max=10000,
        description="How many steps each frame of the time lapse moves on. "
                    "Above 1 the movie is shorter and renders sooner")
    movie_camera: bpy.props.BoolProperty(
        name="Camera", default=True, update=_upd_movie_rig,
        description="Create time lapse adds a camera that has the whole history in frame. "
                    "Your own camera isn't moved")
    movie_lighting: bpy.props.BoolProperty(
        name="Lighting", default=True, update=_upd_movie_rig,
        description="Create time lapse adds lighting when the scene has no light of its own")
    movie_view_angle: bpy.props.FloatProperty(
        name="Angle", subtype='ANGLE', default=0.5235987755982988, min=-3.141592653589793,
        max=3.141592653589793, update=_upd_movie_rig, options={'HIDDEN'},
        description="Where the camera stands, turned around the model from its front. Use this view sets it")
    movie_view_height: bpy.props.FloatProperty(
        name="Height", subtype='ANGLE', default=0.2617993877991494, min=-1.5533430342749532,
        max=1.5533430342749532, update=_upd_movie_rig, options={'HIDDEN'},
        description="How far above or below the model the camera stands. Use this view sets it")
    movie_user_camera: bpy.props.PointerProperty(
        type=bpy.types.Object, name="Camera before Movie Mode", options={'HIDDEN'},
        description="The scene camera Hindsight's camera stood in for. It is put back when Movie Mode is switched off")
    movie_prev_camera: bpy.props.StringProperty(
        name="Camera before Movie Mode (name)", default="", options={'HIDDEN'},
        description="Name of the camera to put back, in files saved by earlier builds. "
                    "Read only when the camera above is empty")
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
    reg.register_classes(classes)
    bpy.types.Scene.hindsight = bpy.props.PointerProperty(type=HINDSIGHT_PG_scene)


def unregister():
    with contextlib.suppress(AttributeError):
        del bpy.types.Scene.hindsight
    reg.unregister_classes(classes)
