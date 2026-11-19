import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R075: shape key positions are read and written through ShapeKey.points (Blender 4.1+, one copy) instead of
ShapeKey.data (one RNA call per point, about 200 times slower: 2 ms against 0.01 ms for 40k points). The values
are exactly the same: read_extras gives what kb.data gives, write_extras puts back exactly what it is given,
also when the mesh is rebuilt, and going back over a shape key change made with Record everything is exact."""
import bpy
import numpy as np

import tests.harness as H
from hindsight.bl import datarec, meshio, runtime

C = bpy.context


def _co_data(kb):
    a = np.empty(len(kb.data) * 3, np.float32)
    kb.data.foreach_get("co", a)
    return a


def _mesh_with_keys(n=3000, k=6, seed=3):
    H.fresh()
    ob = H.make_mesh(n, name="Keys")
    ob.shape_key_add(name="Basis")
    rng = np.random.default_rng(seed)
    nv = len(ob.data.vertices)
    for i in range(k):
        kb = ob.shape_key_add(name=f"K{i}")
        co = _co_data(kb)
        co += rng.normal(0, 0.05, co.size).astype(np.float32)
        co[rng.integers(0, co.size, 20)] = rng.choice([-0.0, 1e-38, 3.4e38, -1e-45], 20).astype(np.float32)
        kb.data.foreach_set("co", co)
    return ob, nv


def test_points_read_what_data_reads():
    ob, nv = _mesh_with_keys()
    assert meshio.sk_points(ob.data.shape_keys.key_blocks[1]) is not ob.data.shape_keys.key_blocks[1].data
    ext = meshio.read_extras(ob, ob.data)
    for i, kb in enumerate(ob.data.shape_keys.key_blocks):
        got = ext[meshio.sk_key(i, kb.name)]
        assert got.view(np.uint32).tobytes() == _co_data(kb).view(np.uint32).tobytes(), kb.name


def test_points_write_exactly_also_when_rebuilt():
    ob, nv = _mesh_with_keys()
    rng = np.random.default_rng(9)
    blocks = ob.data.shape_keys.key_blocks
    arrays = {}
    for i, kb in enumerate(blocks):
        arrays[meshio.sk_key(i, kb.name)] = rng.normal(0, 1, nv * 3).astype(np.float32)
    meshio.write_extras(ob, ob.data, arrays)
    for i, kb in enumerate(ob.data.shape_keys.key_blocks):
        assert np.array_equal(_co_data(kb), arrays[meshio.sk_key(i, kb.name)]), kb.name
    meshio.write_extras(ob, ob.data, arrays, rebuilt=True)
    for i, kb in enumerate(ob.data.shape_keys.key_blocks):
        assert np.array_equal(_co_data(kb), arrays[meshio.sk_key(i, kb.name)]), ("rebuilt", kb.name)


def test_going_back_over_a_shape_key_edit_is_exact():
    ob, nv = _mesh_with_keys()
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    datarec.set_tracked(ob, True)
    datarec.finish_operation("baseline")
    kb = ob.data.shape_keys.key_blocks[2]
    before = _co_data(kb)
    co = before.copy()
    co[:300] += 0.25
    kb.data.foreach_set("co", co)
    ob.data.update()
    C.view_layer.update()
    datarec.finish_operation("Edit Shape")
    h = runtime.history_of(ob)
    assert h is not None and h.entries, "the shape key edit made no step"
    runtime.goto(C, len(h.entries) - 1)
    assert np.array_equal(_co_data(ob.data.shape_keys.key_blocks[2]), before)
    runtime.goto(C, len(h.entries))
    assert np.array_equal(_co_data(ob.data.shape_keys.key_blocks[2]), co)


if __name__ == "__main__":
    for t in (test_points_read_what_data_reads, test_points_write_exactly_also_when_rebuilt,
              test_going_back_over_a_shape_key_edit_is_exact):
        H.run(t)
    H.finish()
