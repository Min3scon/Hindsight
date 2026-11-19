"""D134: with Record everything, hair curves and point clouds are recorded through their attributes:
moved points, attribute values, added attributes, and (Blender 5 and later; 4.2 has no way to remove
curves or resize a point cloud) curves and points added or removed."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context
RESIZE = "resize" in bpy.types.PointCloud.bl_rna.functions   # PointCloud.resize came with Blender 5.1 (not 5.0)


def pos_sum(owner):
    a = owner.attributes.get("position")
    if a is None:                  # an empty point cloud has no position attribute on Blender 5.0
        return 0.0
    buf = [0.0] * (3 * len(a.data))
    a.data.foreach_get("vector", buf)
    return round(sum(buf), 4)


def walk(h, states, read):
    assert all(x != y for x, y in zip(states, states[1:])), states
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert read() in states, (pos, read())
    runtime._goto_history(h, 0, True)
    assert read() == states[0], (read(), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


def test_hair_and_points():
    L.fresh_scene()
    hc = bpy.data.hair_curves.new("Hair")
    hc.add_curves([3, 4])
    hob = bpy.data.objects.new("Hair", hc)
    C.scene.collection.objects.link(hob)
    pc = bpy.data.pointclouds.new("Pts")
    pob = bpy.data.objects.new("Pts", pc)
    C.scene.collection.objects.link(pob)
    if RESIZE:
        pc.resize(5)
    L.track_all()
    L.tick_like("baseline")
    read = lambda: (len(hc.curves), len(hc.points), pos_sum(hc), len(pc.points), pos_sum(pc),
                    tuple(sorted(a.name for a in pc.attributes if not a.name.startswith("."))))
    states = [read()]

    def move_hair():
        a = hc.attributes["position"]
        a.data.foreach_set("vector", [0.5] * (3 * len(a.data)))
        hc.update_tag()        # as brushes do (4.2 doesn't tell the depsgraph about a bulk write)

    def move_points():
        a = pc.attributes["position"]
        a.data.foreach_set("vector", [0.25] * (3 * len(a.data)))

    def add_attr():
        pc.attributes.new("heat", "FLOAT", "POINT").data.foreach_set("value", [1.0] * len(pc.points))
    ops = [("move hair", move_hair)]
    if RESIZE:
        ops += [("move points", move_points), ("attribute", add_attr)]
    else:
        print("  SKIP point cloud part: this Blender (4.2, 5.0) can't give a point cloud points from Python")
    if RESIZE:
        ops += [("more hair", lambda: hc.add_curves([2])), ("fewer points", lambda: pc.resize(3))]
    for label, fn in ops:
        fn()
        L.tick_like(label)
        states.append(read())
    assert all(x != y for x, y in zip(states, states[1:])), states
    hists = [h for h in (runtime.history_of(hob), runtime.history_of(pob)) if h is not None]
    for h in hists:
        while h.cursor > 0:
            runtime._goto_history(h, h.cursor - 1, True)
    assert read() == states[0], (read(), states[0])
    for h in hists:
        runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


if __name__ == "__main__":
    H.run(test_hair_and_points)
    H.finish()
