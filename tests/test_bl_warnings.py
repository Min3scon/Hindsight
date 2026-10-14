import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import traceback
from types import SimpleNamespace

import bpy

from hindsight.bl import warnings as W

ok = True


def check(name, fn):
    global ok
    try:
        fn()
        print("PASS", name)
    except Exception:
        ok = False
        print("FAIL", name)
        traceback.print_exc()


def fresh_cube():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    return bpy.context.active_object


def texts(obj, ctx=None):
    return [t for _s, t in W.collect(ctx or bpy.context, obj)]


def has(obj, sev, text, ctx=None):
    return (sev, text) in W.collect(ctx or bpy.context, obj)


def t_clean():
    obj = fresh_cube()
    assert W.collect(bpy.context, obj) == [], W.collect(bpy.context, obj)
    assert not W.blocking(bpy.context, obj)
    assert W.collect(bpy.context, None) == [] and not W.blocking(bpy.context, None)


def t_multires():
    obj = fresh_cube()
    obj.modifiers.new("m", 'MULTIRES')
    assert has(obj, "ERROR", W.TXT_MULTIRES)
    assert W.blocking(bpy.context, obj)
    assert W.TXT_MODIFIERS not in texts(obj) and W.TXT_DEFORM not in texts(obj)
    assert W.collect(bpy.context, obj)[0][0] == "ERROR"


def t_dyntopo():
    fake = SimpleNamespace(mode='SCULPT', use_dynamic_topology_sculpting=True, modifiers=[], data=None,
                           type='MESH', animation_data=None, vertex_groups=[])
    assert ("ERROR", W.TXT_DYNTOPO) in W.collect(bpy.context, fake)
    assert W.blocking(bpy.context, fake)
    fake.mode = 'OBJECT'
    assert ("ERROR", W.TXT_DYNTOPO) not in W.collect(bpy.context, fake)
    assert not W.blocking(bpy.context, fake)
    obj = fresh_cube()
    assert not obj.use_dynamic_topology_sculpting
    assert W.TXT_DYNTOPO not in texts(obj)


def t_global_undo():
    obj = fresh_cube()
    edit = bpy.context.preferences.edit
    old = edit.use_global_undo
    try:
        edit.use_global_undo = False
        assert has(obj, "ERROR", W.TXT_GLOBAL_UNDO)
        assert not W.blocking(bpy.context, obj)
        edit.use_global_undo = True
        assert W.TXT_GLOBAL_UNDO not in texts(obj)
    finally:
        edit.use_global_undo = old


def t_armature_displace():
    obj = fresh_cube()
    obj.modifiers.new("a", 'ARMATURE')
    assert has(obj, "WARNING", W.TXT_DEFORM)
    assert W.TXT_MODIFIERS not in texts(obj)
    obj = fresh_cube()
    obj.modifiers.new("d", 'DISPLACE')
    assert has(obj, "WARNING", W.TXT_DEFORM)
    assert not W.blocking(bpy.context, obj)


def t_subsurf_info():
    obj = fresh_cube()
    obj.modifiers.new("s", 'SUBSURF')
    assert has(obj, "INFO", W.TXT_MODIFIERS)
    assert W.TXT_DEFORM not in texts(obj)
    obj.modifiers.new("d", 'DISPLACE')
    assert has(obj, "INFO", W.TXT_MODIFIERS) and has(obj, "WARNING", W.TXT_DEFORM)


def t_shape_keys():
    obj = fresh_cube()
    assert W.TXT_SHAPE_KEYS not in texts(obj)
    obj.shape_key_add(name="Basis")
    assert has(obj, "WARNING", W.TXT_SHAPE_KEYS)


def t_linked():
    obj = fresh_cube()
    assert W.TXT_LINKED not in texts(obj)
    b = bpy.data.objects.new("B", obj.data)
    bpy.context.scene.collection.objects.link(b)
    assert has(obj, "WARNING", W.TXT_LINKED) and has(b, "WARNING", W.TXT_LINKED)


def t_vertex_group():
    obj = fresh_cube()
    assert W.TXT_VGROUPS not in texts(obj)
    obj.vertex_groups.new(name="g")
    assert has(obj, "INFO", W.TXT_VGROUPS)


def t_transform():
    """History is stored in object space: moving, rotating or scaling an object is not a problem
    and must not produce a warning (beta 1 warned for every object away from the origin)."""
    obj = fresh_cube()
    obj.location = (1, 0, 0)
    obj.rotation_euler = (0, 0, 0.5)
    obj.scale = (2, 1, 0.5)
    bpy.context.view_layer.update()
    assert not texts(obj), texts(obj)


def t_animation():
    obj = fresh_cube()
    assert W.TXT_ANIMATED not in texts(obj)
    obj.keyframe_insert("location")
    assert has(obj, "WARNING", W.TXT_ANIMATED)


def t_linked_duplicate_check():
    calls = []

    class WM:
        def popup_menu(self, fn, title="", icon='NONE'):
            calls.append((fn, title, icon))

    obj = fresh_cube()
    ctx = SimpleNamespace(active_object=obj, window_manager=WM())
    W.linked_duplicate_check(ctx)
    assert calls == []
    b = bpy.data.objects.new("B", obj.data)
    bpy.context.scene.collection.objects.link(b)
    W.linked_duplicate_check(SimpleNamespace(active_object=b, window_manager=WM()))
    assert calls == [], "no uid on the other object yet"
    obj["_hindsight_uid"] = "abc"
    W.linked_duplicate_check(SimpleNamespace(active_object=b, window_manager=WM()))
    assert len(calls) == 1 and calls[0][1] == "Hindsight" and calls[0][2] == 'ERROR'
    labels = []
    menu = SimpleNamespace(layout=SimpleNamespace(column=lambda: SimpleNamespace(
        label=lambda text="": labels.append(text))))
    calls[0][0](menu, None)
    assert labels and any("Shift+D" in s for s in labels)
    W.linked_duplicate_check(SimpleNamespace(active_object=None, window_manager=WM()))
    W.linked_duplicate_check(bpy.context)   # real context headless: must not raise


def t_register():
    assert W.classes == () or isinstance(W.classes, tuple)
    W.register()
    assert W._handle is not None
    W.register()
    W.unregister()
    assert W._handle is None
    W.unregister()
    W._draw_overlay()   # no region in background: must be a silent no-op


for name, fn in [
    ("clean", t_clean), ("multires", t_multires), ("dyntopo", t_dyntopo), ("global_undo", t_global_undo),
    ("deform_modifiers", t_armature_displace), ("subsurf_info", t_subsurf_info), ("shape_keys", t_shape_keys),
    ("linked_data", t_linked), ("vertex_group", t_vertex_group), ("transform", t_transform),
    ("animation", t_animation), ("linked_duplicate_check", t_linked_duplicate_check),
    ("register", t_register),
]:
    check(name, fn)

print("ALL OK" if ok else "FAILURES")
sys.stdout.flush(); os._exit(0 if ok else 1)
