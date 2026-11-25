import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R

import bpy

from hindsight.bl import reg


class HS_OT_reg_a(bpy.types.Operator):
    bl_idname = "hindsight.reg_a"
    bl_label = "A"

    def execute(self, context):
        return {'FINISHED'}


class HS_OT_reg_b(bpy.types.Operator):
    bl_idname = "hindsight.reg_b"
    bl_label = "B"

    def execute(self, context):
        return {'FINISHED'}


CLASSES = (HS_OT_reg_a, HS_OT_reg_b)


def t_classes():
    reg.register_classes(CLASSES)
    assert all(c.is_registered for c in CLASSES)
    reg.unregister_classes(CLASSES)
    reg.unregister_classes(CLASSES)          # tolerant: already gone is not an error
    assert not any(c.is_registered for c in CLASSES)


def t_already_registered():
    reg.register_classes(CLASSES)
    try:
        reg.register_classes(CLASSES)
        raise AssertionError("a second register must raise")
    except ValueError:
        pass
    reg.register_classes(CLASSES, again_ok=True)   # dupsep's case: already registered is fine
    reg.unregister_classes(CLASSES)


def t_keymaps():
    reg.register_classes(CLASSES)
    store = []
    reg.add_keymap(store, "Object Mode", "hindsight.reg_a", 'F9', ctrl=True)
    reg.add_keymap(store, "Object Mode", "hindsight.reg_b", 'F9', shift=True)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:                           # headless without an add-on key config: nothing to add
        assert store == []
    else:
        assert len(store) == 2
        km = kc.keymaps["Object Mode"]
        assert {k.idname for k in km.keymap_items} >= {"hindsight.reg_a", "hindsight.reg_b"}
        reg.remove_keymaps(store)
        assert store == []
        assert not {k.idname for k in km.keymap_items} & {"hindsight.reg_a", "hindsight.reg_b"}
    reg.remove_keymaps(store)                # twice is fine
    reg.unregister_classes(CLASSES)


for fn in (t_classes, t_already_registered, t_keymaps):
    R.run(fn)
R.finish()
