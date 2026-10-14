import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""History menu operators: each calls the right runtime function, polls follow history/recording state.

Uses a FAKE runtime (the real one is not required) and a minimal scene PropertyGroup when the add-on's
own props are not registered.
"""
import types
import traceback

import bpy

import hindsight.bl as hb

# --- fake runtime -------------------------------------------------------------------------------
calls = []
state = {"history": False, "recording": True, "deleted": 3}


def _rec(name):
    def fn(*args):
        calls.append((name,) + args)
        if name == "delete_past":
            return state["deleted"]
        return None
    return fn


fake = types.ModuleType("hindsight.bl.runtime")
for _n in ("set_global", "set_preserve_modes", "set_morph_target", "delete_past", "reset_history",
           "turn_off", "turn_on", "reset_and_turn_off", "reset_all"):
    setattr(fake, _n, _rec(_n))
fake.history_of = lambda obj: (object() if state["history"] else None)
fake.is_recording = lambda obj: state["recording"]
sys.modules["hindsight.bl.runtime"] = fake
hb.runtime = fake

# --- minimal scene settings if the add-on's props are not registered -----------------------------
if "hindsight" not in bpy.types.Scene.bl_rna.properties:
    class HINDSIGHT_PG_test_scene(bpy.types.PropertyGroup):
        use_global: bpy.props.BoolProperty(default=False)
        preserve_modes: bpy.props.BoolProperty(default=True)

    bpy.utils.register_class(HINDSIGHT_PG_test_scene)
    bpy.types.Scene.hindsight = bpy.props.PointerProperty(type=HINDSIGHT_PG_test_scene)

from hindsight.bl import ui_menu  # noqa: E402

ui_menu.register()

for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
bpy.ops.mesh.primitive_cube_add()
OBJ = bpy.context.active_object
assert OBJ is not None and OBJ.type == 'MESH'

ops = bpy.ops.hindsight
HISTORY_OPS = ("set_morph_target", "delete_past", "reset_history", "turn_off", "reset_and_turn_off")


def last():
    assert calls, "no runtime call recorded"
    return calls[-1]


def test_registered():
    for name in HISTORY_OPS + ("toggle_global", "toggle_preserve_modes", "morph_brush", "turn_on",
                               "reset_all"):
        assert hasattr(bpy.types, "HINDSIGHT_OT_" + name), name
    assert bpy.types.HINDSIGHT_MT_history.bl_label == "History Menu"
    labels = {c.bl_idname: c.bl_label for c in ui_menu.classes if hasattr(c, "bl_idname")}
    assert labels["hindsight.toggle_preserve_modes"] == "Follow object and mode"
    assert labels["hindsight.set_morph_target"] == "Set morph target"
    assert labels["hindsight.morph_brush"] == "Use Morph History brush"
    assert labels["hindsight.delete_past"] == "Delete earlier steps"
    assert labels["hindsight.reset_history"] == "Clear history"
    assert labels["hindsight.turn_off"] == "Pause recording"
    assert labels["hindsight.reset_and_turn_off"] == "Clear and stop recording"
    assert labels["hindsight.turn_on"] == "Record this object"
    assert labels["hindsight.reset_all"] == "Clear all histories"
    for c in ui_menu.classes:
        if issubclass(c, bpy.types.Operator):
            assert 'UNDO' not in c.bl_options, c


def test_poll_without_history():
    state["history"] = False
    state["recording"] = True
    for name in HISTORY_OPS + ("morph_brush",):
        assert not getattr(ops, name).poll(), name
    assert not ops.turn_on.poll()           # recording is on
    assert ops.toggle_global.poll()
    assert ops.toggle_preserve_modes.poll()
    assert ops.reset_all.poll()


def test_poll_without_active_mesh():
    state["history"] = True
    vl = bpy.context.view_layer
    vl.objects.active = None
    try:
        for name in HISTORY_OPS + ("turn_on",):
            assert not getattr(ops, name).poll(), name
    finally:
        vl.objects.active = OBJ


def test_toggle_global():
    sc = bpy.context.scene
    sc.hindsight.use_global = False
    del calls[:]
    assert ops.toggle_global('EXEC_DEFAULT') == {'FINISHED'}
    assert last()[0] == "set_global" and last()[2] is True
    sc.hindsight.use_global = True
    assert ops.toggle_global('EXEC_DEFAULT') == {'FINISHED'}
    assert last()[0] == "set_global" and last()[2] is False
    sc.hindsight.use_global = False


def test_toggle_preserve_modes():
    sc = bpy.context.scene
    sc.hindsight.preserve_modes = True
    del calls[:]
    assert ops.toggle_preserve_modes('EXEC_DEFAULT') == {'FINISHED'}
    assert last()[0] == "set_preserve_modes" and last()[2] is False
    sc.hindsight.preserve_modes = False
    ops.toggle_preserve_modes('EXEC_DEFAULT')
    assert last()[0] == "set_preserve_modes" and last()[2] is True


def test_object_ops_call_runtime():
    state["history"] = True
    state["recording"] = True
    for name in HISTORY_OPS:
        assert getattr(ops, name).poll(), name
        del calls[:]
        assert getattr(ops, name)('EXEC_DEFAULT') == {'FINISHED'}, name
        assert len(calls) == 1, calls
        c = last()
        assert c[0] == name, c
        assert c[2] == OBJ if name != "set_morph_target" else len(c) == 2, c
    del calls[:]
    state["deleted"] = 0
    ops.delete_past('EXEC_DEFAULT')
    assert last()[0] == "delete_past"
    state["deleted"] = 3


def test_turn_on_off_polls():
    state["history"] = True
    state["recording"] = False
    assert not ops.turn_off.poll()
    assert ops.turn_on.poll()
    assert ops.reset_and_turn_off.poll()
    # Turn On works even when the history was removed (after Reset and Turn Off)
    state["history"] = False
    assert ops.turn_on.poll()
    del calls[:]
    assert ops.turn_on('EXEC_DEFAULT') == {'FINISHED'}
    assert last()[0] == "turn_on" and last()[2] == OBJ
    state["recording"] = True
    assert not ops.turn_on.poll()


def test_reset_all():
    del calls[:]
    assert ops.reset_all('EXEC_DEFAULT') == {'FINISHED'}
    assert len(calls) == 1 and last()[0] == "reset_all" and len(last()) == 2


class _Layout:
    def __init__(self):
        self.items = []

    def operator(self, idname, text=None, icon='NONE', **kw):
        self.items.append((idname, text, icon))

    def separator(self, **kw):
        pass


def _draw():
    lay = _Layout()
    ui_menu.HINDSIGHT_MT_history.draw(types.SimpleNamespace(layout=lay), bpy.context)
    return lay.items


def test_menu_draw():
    sc = bpy.context.scene
    state["history"] = True
    state["recording"] = True
    sc.hindsight.use_global = False
    sc.hindsight.preserve_modes = True
    items = _draw()
    ids = [i[0] for i in items]
    assert ids == ["hindsight.toggle_global", "hindsight.toggle_preserve_modes", "hindsight.set_morph_target",
                   "hindsight.morph_brush", "hindsight.delete_past", "hindsight.reset_history",
                   "hindsight.turn_off", "hindsight.reset_and_turn_off"], ids
    assert items[0][1] == "Use global history"
    assert items[1][2] == 'CHECKBOX_HLT'
    sc.hindsight.use_global = True
    sc.hindsight.preserve_modes = False
    state["recording"] = False
    items = _draw()
    assert items[0][1] == "Use per-object history"
    assert items[1][2] == 'CHECKBOX_DEHLT'
    assert "hindsight.turn_on" in [i[0] for i in items]
    assert "hindsight.turn_off" not in [i[0] for i in items]
    sc.hindsight.use_global = False
    state["recording"] = True


def test_morph_brush():
    state["history"] = True
    assert not ops.morph_brush.poll()        # object mode
    bpy.ops.object.mode_set(mode='SCULPT')
    try:
        assert ops.morph_brush.poll()
        try:
            res = ops.morph_brush('EXEC_DEFAULT')
            assert res in ({'FINISHED'}, {'CANCELLED'}), res
        except RuntimeError as e:   # error reports become exceptions headless
            assert "Morph History brush not available" in str(e), e
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    state["history"] = False
    bpy.ops.object.mode_set(mode='SCULPT')
    try:
        assert not ops.morph_brush.poll()
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


def test_unregister():
    ui_menu.unregister()
    assert not hasattr(bpy.types, "HINDSIGHT_MT_history")
    ui_menu.register()
    assert hasattr(bpy.types, "HINDSIGHT_MT_history")


ok = True
for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        try:
            _fn()
            print("PASS", _name)
        except Exception:
            ok = False
            print("FAIL", _name)
            traceback.print_exc()
sys.stdout.flush()
os._exit(0 if ok else 1)
