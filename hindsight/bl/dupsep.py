"""Separate, Mask Extract and Duplicate handling.

- Separate (P, Edit Mode) and Mask Extract (Sculpt Mode): Hindsight asks first when the object has a
  history; the new object starts its own history.
- Duplicate (Shift+D), Separate and Mask Extract copy the object's custom properties, including the
  Hindsight uid. `fix_uids()` keeps the uid on the original and gives every copy a fresh uid with no
  history (it starts its own on its first stroke). It runs from a depsgraph handler whenever the
  number of objects changes.
- Linked Duplicate (Alt+D): a warning explains the objects share mesh data; Shift+D gives an
  independent history.
"""

import bpy
from bpy.app.handlers import persistent

from ..core.registry import new_uid
from . import runtime, warnings

MSG_SEPARATE = "The separated part becomes a new object that starts its own, empty undo history"
MSG_EXTRACT = "The extracted mesh becomes a new object that starts its own, empty undo history"

_NAMES: dict = {}       # uid -> object name seen most recently for it (the original)
_LAST_COUNT = [-1]      # len(bpy.data.objects) at the last handler run
_WARN_TRIES = [0]


# ----------------------------------------------------------------------------- uid fixing
def _pick_original(uid: str, objs: list):
    names = {o.name: o for o in objs}
    for name in (_NAMES.get(uid), runtime._UID_CACHE.get(uid)):
        if name is not None and name in names:
            return names[name]
    return min(objs, key=lambda o: (len(o.name), o.name))


def fix_uids() -> list:
    """Give copies (duplicate / separate / extract) of a tracked object a fresh uid and no history.

    The uid stays on the original. Returns the names of the objects that got a new uid."""
    groups: dict = {}
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        try:
            u = ob.get(runtime.UID_PROP)
        except Exception:
            continue
        if u is not None:
            groups.setdefault(u, []).append(ob)
    changed = []
    names = {}
    for uid, objs in groups.items():
        keep = objs[0] if len(objs) == 1 else _pick_original(uid, objs)
        names[uid] = keep.name
        if len(objs) == 1:
            continue
        for ob in objs:
            if ob == keep or ob.library is not None:
                continue
            try:
                nu = new_uid()
                ob[runtime.UID_PROP] = nu
            except Exception as e:
                print("Hindsight: could not assign a new uid to", ob.name, e)
                continue
            names[nu] = ob.name
            changed.append(ob.name)
            if runtime._TRACK.get("name") == ob.name:
                runtime._TRACK["uid"] = nu
        if runtime._UID_CACHE.get(uid) != keep.name:
            runtime._UID_CACHE.pop(uid, None)
    _NAMES.clear()
    _NAMES.update(names)
    _LAST_COUNT[0] = len(bpy.data.objects)
    if changed:
        runtime.tag_redraw_all()
    return changed


@persistent
def _on_depsgraph(scene, depsgraph) -> None:
    try:
        n = len(bpy.data.objects)
        if n == _LAST_COUNT[0]:
            return
        _LAST_COUNT[0] = n
        fix_uids()
    except Exception as e:
        print("Hindsight: uid fix error:", e)


@persistent
def _on_load(*_a) -> None:
    _NAMES.clear()
    _LAST_COUNT[0] = -1


# ----------------------------------------------------------------------------- operators
def _has_history(context) -> bool:
    try:
        return runtime.history_of(context.active_object) is not None
    except Exception:
        return False


def _confirm(op, context, event, message):
    try:
        return context.window_manager.invoke_confirm(op, event, title=op.bl_label, message=message,
                                                     confirm_text=op.bl_label)
    except TypeError:  # older signature
        return context.window_manager.invoke_confirm(op, event)


def _result(r) -> set:
    return {"CANCELLED"} if r == {"CANCELLED"} else {"FINISHED"}


class HINDSIGHT_OT_separate(bpy.types.Operator):
    """Separate selected geometry into a new object (it starts its own undo history)"""
    bl_idname = "hindsight.separate"
    bl_label = "Separate"

    type: bpy.props.EnumProperty(
        name="Type",
        items=(("MENU", "Menu", "Show Blender's Separate menu"),
               ("SELECTED", "Selection", ""),
               ("MATERIAL", "By Material", ""),
               ("LOOSE", "By Loose Parts", "")),
        default="MENU", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH"

    def invoke(self, context, event):
        if _has_history(context):
            return _confirm(self, context, event, MSG_SEPARATE)
        return self.execute(context)

    def execute(self, context):
        if self.type == "MENU":
            r = bpy.ops.mesh.separate("INVOKE_DEFAULT")
        else:
            r = bpy.ops.mesh.separate(type=self.type)
        fix_uids()
        return _result(r)


class HINDSIGHT_OT_mask_extract(bpy.types.Operator):
    """Extract the masked area into a new object (it starts its own undo history)"""
    bl_idname = "hindsight.mask_extract"
    bl_label = "Mask Extract"

    @classmethod
    def poll(cls, context):
        return context.mode == "SCULPT"

    def invoke(self, context, event):
        if _has_history(context):
            return _confirm(self, context, event, MSG_EXTRACT)
        return self.execute(context)

    def execute(self, context):
        r = bpy.ops.mesh.paint_mask_extract("INVOKE_DEFAULT")
        fix_uids()
        return _result(r)


def _deferred_linked_warning():
    """Show the linked-duplicate popup once the grab that follows Alt+D has finished."""
    try:
        wm = bpy.context.window_manager
        if wm is None or not wm.windows:
            return None
        win = wm.windows[0]
        if _WARN_TRIES[0] < 600 and len(getattr(win, "modal_operators", ())) > 0:
            _WARN_TRIES[0] += 1
            return 0.1
        with bpy.context.temp_override(window=win, screen=win.screen):
            warnings.linked_duplicate_check(bpy.context)
    except Exception as e:
        print("Hindsight: linked duplicate warning failed:", e)
    return None


class HINDSIGHT_OT_duplicate_linked(bpy.types.Operator):
    """Duplicate selected objects, sharing their mesh data (history affects all of them)"""
    bl_idname = "hindsight.duplicate_linked"
    bl_label = "Duplicate Linked"

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def invoke(self, context, event):
        r = bpy.ops.object.duplicate_move_linked("INVOKE_DEFAULT")
        fix_uids()
        _WARN_TRIES[0] = 0
        if not bpy.app.timers.is_registered(_deferred_linked_warning):
            bpy.app.timers.register(_deferred_linked_warning, first_interval=0.1)
        return _result(r)

    def execute(self, context):
        r = bpy.ops.object.duplicate(linked=True)
        fix_uids()
        if not bpy.app.background and context.window is not None:
            warnings.linked_duplicate_check(context)
        return _result(r)


classes = (HINDSIGHT_OT_separate, HINDSIGHT_OT_mask_extract, HINDSIGHT_OT_duplicate_linked)

_keymaps = []


def _add_keymaps():
    if _keymaps:
        return
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc is None:
        return
    km = kc.keymaps.new(name="Mesh", space_type="EMPTY")
    _keymaps.append((km, km.keymap_items.new("hindsight.separate", "P", "PRESS")))
    km = kc.keymaps.new(name="Object Mode", space_type="EMPTY")
    _keymaps.append((km, km.keymap_items.new("hindsight.duplicate_linked", "D", "PRESS", alt=True)))


# ----------------------------------------------------------------------------- register
def register():
    for c in classes:
        try:
            bpy.utils.register_class(c)
        except ValueError:
            pass  # already registered
    _add_keymaps()
    hl = bpy.app.handlers.depsgraph_update_post
    if _on_depsgraph not in hl:
        hl.insert(0, _on_depsgraph)  # before the recorder, so copies never record into the original
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    _LAST_COUNT[0] = -1


def unregister():
    for km, k in _keymaps:
        try:
            km.keymap_items.remove(k)
        except Exception:
            pass
    _keymaps.clear()
    for hl, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                   (bpy.app.handlers.load_post, _on_load)):
        while fn in hl:
            hl.remove(fn)
    if bpy.app.timers.is_registered(_deferred_linked_warning):
        bpy.app.timers.unregister(_deferred_linked_warning)
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
    _NAMES.clear()
    _LAST_COUNT[0] = -1
