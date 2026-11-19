"""L2: "Everything in this file" switches Record everything on for the scene and every local object
in one click (objects added later follow, D36), shows as on only when all of them are, and switches
them all off again. Hindsight's own Movie Mode objects are never switched on."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.bl import datarec, runtime

C = bpy.context


def test_one_switch_for_the_file():
    L.fresh_scene()
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    assert not datarec.file_tracked()
    bpy.ops.hindsight.record_file(on=True)
    assert datarec.file_tracked()
    assert datarec.is_tracked(C.scene)
    assert all(datarec.is_tracked(o) for o in bpy.data.objects)
    T.add_primitive("cube", (0, -5, 0)).run()
    L.tick_like("add")
    assert datarec.is_tracked(bpy.data.objects["Cube"]), "added later: recorded too (D36)"
    assert datarec.file_tracked()
    datarec.set_tracked(bpy.data.objects["B"], False)
    assert not datarec.file_tracked(), "one object off: the switch shows off"
    bpy.ops.hindsight.record_file(on=False)
    assert not any(datarec.is_tracked(o) for o in bpy.data.objects) and not datarec.is_tracked(C.scene)
    a = bpy.data.objects["A"]
    bpy.ops.hindsight.record_file(on=True)
    a.location.x += 1
    L.tick_like("move")
    assert runtime.history_of(a) is not None and runtime.history_of(a).entries


def test_switch_is_quick_with_many_objects():
    H.fresh()
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    me = bpy.data.meshes.new("M")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    for i in range(300):
        ob = bpy.data.objects.new(f"O{i}", me.copy())
        C.scene.collection.objects.link(ob)
    t = time.perf_counter()
    datarec.set_file_tracked(True)
    dt = time.perf_counter() - t
    assert datarec.file_tracked()
    assert dt < 3.0, dt                 # one walk over the objects, not one per object
    print(f"  300 objects switched on in {dt * 1000:.0f} ms")


if __name__ == "__main__":
    H.run(test_one_switch_for_the_file)
    H.run(test_switch_is_quick_with_many_objects)
    H.finish()
