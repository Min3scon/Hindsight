"""Collection filtering: decides which objects are excluded from recording.

Excluded objects record nothing and their history is hidden (not deleted); the runtime
(`runtime.history_of` / `runtime.is_excluded`) asks `is_excluded(obj, scene)` here.

Modes (scene.hindsight.filter_mode):
  NONE      nothing is excluded
  BLACKLIST objects in any listed collection (or a collection nested in one) are excluded;
            an empty list excludes nothing
  WHITELIST only objects in a listed collection (or nested in one) are recorded;
            an empty list excludes everything
"""

import bpy
from bpy.props import StringProperty


def _scene_props(scene):
    return getattr(scene, "hindsight", None) if scene is not None else None


def listed_collections(scene) -> list:
    """The (non-empty) collections in the scene's filter list."""
    sh = _scene_props(scene)
    if sh is None:
        return []
    out = []
    try:
        for item in sh.filter_collections:
            coll = getattr(item, "collection", None)
            if coll is not None:
                out.append(coll)
    except Exception:
        return []
    return out


def object_in_collection(obj, coll) -> bool:
    """True if obj is in coll directly or in any collection nested inside it."""
    try:
        for uc in obj.users_collection:
            if uc == coll:
                return True
        objs = coll.all_objects
        found = objs.get(obj.name)
        if found is None:
            return False
        if found == obj:
            return True
        # name clash with a linked-library object: compare by identity
        return any(o == obj for o in objs)
    except (ReferenceError, AttributeError):
        return False


def is_excluded(obj, scene=None) -> bool:
    if obj is None:
        return False
    if scene is None:
        try:
            scene = bpy.context.scene
        except Exception:
            return False
    sh = _scene_props(scene)
    if sh is None:
        return False
    try:
        mode = sh.filter_mode
    except Exception:
        return False
    if mode == 'BLACKLIST':
        return any(object_in_collection(obj, c) for c in listed_collections(scene))
    if mode == 'WHITELIST':
        return not any(object_in_collection(obj, c) for c in listed_collections(scene))
    return False


def _after_change() -> None:
    try:
        from . import runtime
        runtime.tag_redraw_all()
    except Exception:
        pass


class HINDSIGHT_UL_collections(bpy.types.UIList):
    """Collections used by Hindsight's collection filtering."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname,
                  index=0, flt_flag=0):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.prop(item, "collection", text="", icon='OUTLINER_COLLECTION')
        else:
            layout.alignment = 'CENTER'
            layout.label(text="", icon='OUTLINER_COLLECTION')


class HINDSIGHT_OT_filter_add(bpy.types.Operator):
    bl_idname = "hindsight.filter_add"
    bl_label = "Add Collection"
    bl_description = "Add a collection to the filter list (the active collection by default)"
    bl_options = {'REGISTER', 'INTERNAL'}

    collection_name: StringProperty(
        name="Collection",
        description="Name of the collection to add (empty: the active collection)",
        default="", options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _scene_props(getattr(context, "scene", None)) is not None

    def execute(self, context):
        sh = _scene_props(context.scene)
        name = self.collection_name
        if not name:
            try:
                name = context.view_layer.active_layer_collection.collection.name
            except Exception:
                name = ""
        coll = bpy.data.collections.get(name) if name else None
        if coll is None and self.collection_name:
            self.report({'WARNING'}, f'No collection named "{self.collection_name}"')
            return {'CANCELLED'}
        if coll is not None:
            for i, item in enumerate(sh.filter_collections):
                if item.collection == coll:
                    sh.filter_index = i
                    self.report({'INFO'}, f'"{coll.name}" is already in the list')
                    return {'CANCELLED'}
        # coll None here = the scene's master collection is active: add an empty row to pick into
        item = sh.filter_collections.add()
        if coll is not None:
            item.collection = coll
        sh.filter_index = len(sh.filter_collections) - 1
        _after_change()
        return {'FINISHED'}


class HINDSIGHT_OT_filter_remove(bpy.types.Operator):
    bl_idname = "hindsight.filter_remove"
    bl_label = "Remove Collection"
    bl_description = "Remove the selected collection from the filter list"
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        sh = _scene_props(getattr(context, "scene", None))
        return sh is not None and len(sh.filter_collections) > 0

    def execute(self, context):
        sh = _scene_props(context.scene)
        idx = sh.filter_index
        n = len(sh.filter_collections)
        if not 0 <= idx < n:
            return {'CANCELLED'}
        sh.filter_collections.remove(idx)
        sh.filter_index = max(0, min(idx, n - 2))
        _after_change()
        return {'FINISHED'}


classes = (
    HINDSIGHT_UL_collections,
    HINDSIGHT_OT_filter_add,
    HINDSIGHT_OT_filter_remove,
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
