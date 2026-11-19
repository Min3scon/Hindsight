"""D111: with Record everything, material slots are recorded: adding and removing slots, which
material each holds, whether it is linked to the object or the mesh, and the active slot. Which faces
use which slot was already recorded (a mesh channel)."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def slots(ob):
    return ([(s.link, s.material.name if s.material else None) for s in ob.material_slots],
            ob.active_material_index, tuple(m.name if m else None for m in ob.data.materials))


def op(label, fn, states, ob):
    fn()
    L.tick_like(label)
    states.append(slots(ob))


def test_material_slots_come_back():
    L.fresh_scene()
    red = bpy.data.materials.new("Red")
    blue = bpy.data.materials.new("Blue")
    red.use_fake_user = blue.use_fake_user = True
    L.track_all()
    ob = bpy.data.objects["A"]
    states = [slots(ob)]
    op("add red", lambda: ob.data.materials.append(red), states, ob)
    op("add blue", lambda: ob.data.materials.append(blue), states, ob)
    op("active", lambda: setattr(ob, "active_material_index", 1), states, ob)

    def object_link():
        ob.material_slots[0].link = "OBJECT"
        ob.material_slots[0].material = blue
    op("object link", object_link, states, ob)
    op("swap", lambda: setattr(ob.material_slots[1], "material", red), states, ob)
    def remove_slot():
        ob.active_material_index = 1
        with C.temp_override(object=ob, active_object=ob):
            bpy.ops.object.material_slot_remove()
    op("remove", remove_slot, states, ob)
    h = runtime.history_of(ob)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert slots(ob) in states, (pos, slots(ob))
    runtime._goto_history(h, 0, True)
    assert slots(ob) == states[0], slots(ob)
    runtime._goto_history(h, len(h.entries), True)
    assert slots(ob) == states[-1], slots(ob)


if __name__ == "__main__":
    H.run(test_material_slots_come_back)
    H.finish()
