import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R077: a mesh's face offsets (where each face's corners start) come from Blender's triangles: a face of n
corners is n - 2 triangles, so counting each face's triangles gives its size, and the offsets are the running
sum. `me.polygons.foreach_get("loop_start")` reads them face by face through RNA (32 to 40 ms at 1M faces).
The counted sizes must add up to the corner count, or the old read is used; the result is the same array."""
import bpy
import bmesh
import numpy as np

import tests.harness as H
from hindsight.bl import meshio

C = bpy.context


def _loop_start(me):
    out = np.empty(len(me.polygons) + 1, np.int32)
    me.polygons.foreach_get("loop_start", out[:-1])
    out[-1] = len(me.loops)
    return out


def _check(me, what):
    c = meshio.counts(me)
    got = meshio._face_offsets(me, c["FACE"], c["CORNER"])
    want = _loop_start(me)
    assert got.dtype == np.int32 and np.array_equal(got, want), what


def test_offsets_equal_loop_start():
    H.fresh()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=40, y_subdivisions=30)
    _check(C.active_object.data, "quads")
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12)
    _check(C.active_object.data, "quads and triangle fans")
    bpy.ops.mesh.primitive_cylinder_add(vertices=17)                     # two 17-gons
    _check(C.active_object.data, "n-gons")
    bpy.ops.mesh.primitive_monkey_add()
    ob = C.active_object
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.poke(bm, faces=list(bm.faces)[::3])
    bmesh.ops.triangulate(bm, faces=list(bm.faces)[1::5])
    bm.to_mesh(ob.data)
    bm.free()
    _check(ob.data, "mixed")
    me = bpy.data.meshes.new("Empty")
    _check(me, "no faces")
    me2 = bpy.data.meshes.new("Edges")
    me2.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [(0, 1), (1, 2)], [])
    _check(me2, "loose edges only")


def test_offsets_fall_back_when_the_count_disagrees():
    H.fresh()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=10, y_subdivisions=10)
    me = C.active_object.data
    c = meshio.counts(me)
    got = meshio._face_offsets(me, c["FACE"], c["CORNER"] + 1)       # sizes can't add up: the RNA read
    assert np.array_equal(got[:-1], _loop_start(me)[:-1]) and got[-1] == c["CORNER"] + 1


if __name__ == "__main__":
    for t in (test_offsets_equal_loop_start, test_offsets_fall_back_when_the_count_disagrees):
        H.run(t)
    H.finish()
