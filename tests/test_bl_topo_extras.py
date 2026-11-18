"""D106: going back over a topology change gives back vertex group weights and shape keys, also for
objects recorded for sculpting only (before, only with Record everything: the weights came back empty
and the shapes were rebuilt from the mesh positions). A topology change made outside Sculpt Mode is
the stand-in for Voxel Remesh, which can't run without a window on every version.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import bmesh
import numpy as np
import tests.harness as H
from hindsight.bl import runtime

C = bpy.context


def weights(ob, name):
    g = ob.vertex_groups[name]
    out = np.full(len(ob.data.vertices), -1.0, np.float32)
    for v in ob.data.vertices:
        for x in v.groups:
            if x.group == g.index:
                out[v.index] = x.weight
    return out


def key_co(ob, i):
    kb = ob.data.shape_keys.key_blocks[i]
    co = np.empty(len(kb.data) * 3, np.float32)
    kb.data.foreach_get("co", co)
    return co


def subdivide(ob):
    H.enter(ob, "OBJECT")
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:100], cuts=1)
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()


def test_weights_and_shapes_come_back_over_a_topology_change():
    H.fresh()
    ob = H.make_mesh(500, name="Body")
    g = ob.vertex_groups.new(name="Arm")
    g.add(list(range(0, len(ob.data.vertices), 3)), 0.6, "REPLACE")
    ob.shape_key_add(name="Basis")
    sk = ob.shape_key_add(name="Smile")
    co = key_co(ob, 1)
    co[:30] += 0.2
    sk.data.foreach_set("co", co)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    assert runtime.commit(ob) is not None
    w1, k1, n1 = weights(ob, "Arm"), key_co(ob, 1), len(ob.data.vertices)
    # weights changed in Weight Paint (not a step), then the topology change
    H.enter(ob, "OBJECT")
    g.add([1, 2], 0.9, "REPLACE")
    H.enter(ob, "SCULPT")
    w2 = weights(ob, "Arm")
    subdivide(ob)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 2)
    assert runtime.commit(ob) is not None
    h = runtime.history_of(ob)
    assert len(ob.data.vertices) != n1
    n_end = len(ob.data.vertices)
    runtime.goto(C, 1)                                   # before the topology change
    assert len(ob.data.vertices) == n1
    assert np.array_equal(weights(ob, "Arm"), w2), "weights as they were right before the change"
    assert np.array_equal(key_co(ob, 1), k1), "the shape key as it was"
    runtime.goto(C, len(h.entries))
    assert len(ob.data.vertices) == n_end


if __name__ == "__main__":
    H.run(test_weights_and_shapes_come_back_over_a_topology_change)
    H.finish()
