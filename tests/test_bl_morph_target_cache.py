import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R084: a Morph History stroke keeps the mesh at the morph target for the next stroke instead of walking back to
it again (every step since the target, Morph History strokes included). It must give exactly what the walk gives
whenever it is used, and the walk again whenever anything it depends on changed: another target, the history
replaced (a file opened), the oldest steps dropped, a topology step or a Join between the cursor and the target,
another vertex count."""
import bpy
import numpy as np
from mathutils import Vector

import tests.harness as H
from hindsight.bl import meshio, runtime
from hindsight.bl import morph as BM
from hindsight.core import morph as CM
from hindsight.core.types import POSITION_KEY

C = bpy.context


def setup(n=3000):
    H.fresh()
    BM._TARGETS.clear()
    ob = H.make_mesh(n)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1, n=200)
    assert H.commit(ob) is not None
    runtime.set_morph_target(C)
    for s in (2, 3, 4):
        H.fake_stroke(ob, s, n=100000)
        assert H.commit(ob) is not None
    return ob


def walk(ob):
    h = runtime.history_of(ob)
    return CM.target_positions(h, runtime.ensure_state(ob), runtime.obj_uid(ob))


def stroke(ob, seed, dabs=6):
    co = meshio.read_channel(ob.data, POSITION_KEY).reshape(-1, 3).astype(np.float64)
    rng = np.random.default_rng(seed)
    assert BM.begin(ob), BM.last_message[0]
    target = BM._SESSION[runtime.obj_uid(ob)]["target"]
    for _ in range(dabs):
        BM.apply_morph_at(ob, Vector(co[int(rng.integers(0, len(co)))]), 0.5, 0.7)
    BM.end(ob)
    return target


def same(a, b):
    return a is not None and b is not None and a.view(np.uint32).tobytes() == b.view(np.uint32).tobytes()


def test_kept_between_strokes_and_equal_to_the_walk():
    ob = setup()
    calls = []
    real = CM.target_positions

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)
    CM.target_positions = spy
    try:
        t1 = stroke(ob, 1)
        t2 = stroke(ob, 2)
        t3 = stroke(ob, 3)
    finally:
        CM.target_positions = real
    assert len(calls) == 1, "the walk ran again for a later stroke"
    assert t2 is t1 and t3 is t1 and not t1.flags.writeable
    assert same(t3, walk(ob)), "the kept target differs from the walk"
    h = runtime.history_of(ob)
    assert h.cursor == 7 and h.morph_target == 1
    # scrub back before the target: the walk goes forward from there, the kept one is still that mesh
    runtime.goto(C, 0)
    assert same(BM._target_positions(h, runtime.ensure_state(ob), runtime.obj_uid(ob)), walk(ob))
    runtime.goto(C, 7)


def test_walks_again_when_anything_changed():
    ob = setup()
    uid = runtime.obj_uid(ob)
    h = runtime.history_of(ob)
    t1 = stroke(ob, 1)
    # another target
    runtime.goto(C, 3)
    runtime.set_morph_target(C)
    runtime.goto(C, len(h.entries))
    t2 = stroke(ob, 2)
    assert t2 is not t1 and same(t2, walk(ob)) and not same(t2, t1)
    # the oldest step dropped (as the step cap does): the target index moves with it
    runtime.goto(C, len(h.entries))
    mt = h.morph_target
    h.entries = h.entries[1:]
    h.cursor -= 1
    h.morph_target = mt - 1
    st = runtime.ensure_state(ob)
    got = BM._target_positions(h, st, uid)
    assert got is not t2 and same(got, CM.target_positions(h, st, uid))
    # the History replaced (a file opened): never the old one's array
    BM._TARGETS[uid] = (BM._TARGETS[uid][0], BM._TARGETS[uid][1], np.zeros_like(got))     # poison the kept one
    import copy
    h2 = copy.copy(h)
    got2 = BM._target_positions(h2, st, uid)
    assert same(got2, CM.target_positions(h2, st, uid))


def test_topology_step_between_refuses_as_the_walk_does():
    ob = setup(800)
    stroke(ob, 1)
    assert runtime.obj_uid(ob) in BM._TARGETS
    H.enter(ob, "OBJECT")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide()
    bpy.ops.object.mode_set(mode="OBJECT")
    H.enter(ob, "SCULPT")
    h = runtime.history_of(ob)
    assert h.entries[-1].kind == "TOPO"
    assert walk(ob) is None
    assert not BM.begin(ob) and BM.last_message[0] == BM.MSG_REMESH
    # back before the remesh, the target is reachable again and the same as the walk
    runtime.goto(C, len(h.entries) - 1)
    assert BM.begin(ob), BM.last_message[0]
    t = BM._SESSION[runtime.obj_uid(ob)]["target"]
    BM.end(ob, commit=False)
    assert same(t, walk(ob))


def test_topology_step_with_the_same_vertex_count_refuses():
    """Rotate Edge changes the topology and keeps every vertex: only the topology step itself tells."""
    import bmesh
    ob = setup(800)
    stroke(ob, 1)
    n_verts = len(ob.data.vertices)
    H.enter(ob, "OBJECT")
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(ob.data)
    for e in bm.edges:
        e.select = False
    for v in bm.verts:
        v.select = False
    for e in [e for e in bm.edges if len(e.link_faces) == 2][:25]:
        e.select = True
    bmesh.update_edit_mesh(ob.data)
    bpy.ops.mesh.edge_rotate(use_ccw=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    H.enter(ob, "SCULPT")
    h = runtime.history_of(ob)
    assert len(ob.data.vertices) == n_verts and h.entries[-1].kind == "TOPO", [e.kind for e in h.entries]
    assert walk(ob) is None
    assert not BM.begin(ob) and BM.last_message[0] == BM.MSG_REMESH


def test_a_stroke_is_the_same_as_with_the_walk():
    """Three strokes with the kept target and three with a walk before each: the same mesh, bit for bit."""
    meshes = []
    for keep in (True, False):
        ob = setup()
        for s in (1, 2, 3):
            if not keep:
                BM._TARGETS.clear()
            stroke(ob, 10 + s)
        meshes.append(meshio.read_channel(ob.data, POSITION_KEY).copy())
    assert meshes[0].view(np.uint32).tobytes() == meshes[1].view(np.uint32).tobytes()


if __name__ == "__main__":
    for t in (test_kept_between_strokes_and_equal_to_the_walk, test_walks_again_when_anything_changed,
              test_topology_step_between_refuses_as_the_walk_does, test_topology_step_with_the_same_vertex_count_refuses,
              test_a_stroke_is_the_same_as_with_the_walk):
        H.run(t)
    H.finish()
