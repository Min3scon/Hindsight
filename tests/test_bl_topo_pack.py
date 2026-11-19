import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R069: the baseline of a recorded mesh keeps its topology (edge and corner indices, about 50 MB at 1M
vertices) packed while the user is idle. Strokes and moves never unpack it; a compare of the topology
uses its checksum; anything that needs the arrays (a topology change's step, going back over one) gets
them back exactly.

A topology change made outside Sculpt Mode (bmesh) stands in for Voxel Remesh, which can't run without a
window on every version; Rotate Edge in Edit Mode changes the connectivity with the same element counts."""
import bpy
import bmesh
import numpy as np

import tests.harness as H
from hindsight.bl import meshio, runtime
from hindsight.core import topopack
from hindsight.core.history import MeshState

C = bpy.context


def _state(ob):
    return runtime._STATES[runtime.obj_uid(ob)]


def _topo(ob):
    s = meshio.read_snapshot(ob.data)
    return {k: s.arrays[k].copy() for k in meshio.TOPO_KEYS if k in s.arrays}


class _Unpacks:
    """Counts MeshState topology unpacks."""

    def __enter__(self):
        self.n = 0
        self._real = MeshState._unpack_topo

        def spy(st):
            self.n += 1
            return self._real(st)
        MeshState._unpack_topo = spy
        return self

    def __exit__(self, *_a):
        MeshState._unpack_topo = self._real
        return False


def setup(n=3000):
    H.fresh()
    runtime.TOPO_PACK_MIN[0] = 0          # small test meshes are packed too
    ob = H.make_mesh(n, name="Packed")
    H.enter(ob, "SCULPT")
    for s in range(2):
        H.fake_stroke(ob, s + 1)
        assert runtime.commit(ob) is not None
    return ob


def test_pack_round_trip_is_exact():
    rng = np.random.default_rng(7)
    cases = [np.zeros(0, np.int32), np.array([5], np.int32), np.arange(10_000, dtype=np.int32),
             rng.integers(-2 ** 31, 2 ** 31 - 1, 50_000, dtype=np.int64).astype(np.int32),
             np.array([2 ** 31 - 1, -2 ** 31, 0, 2 ** 31 - 1, -1], np.int32)]
    for a in cases:
        p = topopack.pack_array(a)
        b = topopack.unpack_array(p)
        assert b.dtype == a.dtype and b.shape == a.shape and np.array_equal(a, b), len(a)
        assert p[3] == topopack.hash_of(a) == topopack.hash_of(b)
    assert topopack.hash_of(cases[2]) != topopack.hash_of(cases[2][::-1].copy())


def test_idle_packs_the_topology_and_strokes_never_unpack():
    ob = setup()
    st = _state(ob)
    want = _topo(ob)
    full = sum(a.nbytes for a in st.arrays.values())
    assert runtime.pack_idle_states(wait=True) == 1
    assert st.is_packed() and not any(k in st.light for k in meshio.TOPO_KEYS)
    assert runtime.baseline_bytes() < full, (runtime.baseline_bytes(), full)
    with _Unpacks() as u:
        for s in range(3, 6):
            H.fake_stroke(ob, s)
            assert runtime.commit(ob) is not None
        runtime.goto(C, 1)
        runtime.goto(C, 5)
    assert u.n == 0, f"strokes and jumps unpacked the topology {u.n} times"
    assert st.is_packed()
    got = {k: st.arrays[k] for k in want}          # reading the arrays gives them back
    assert all(np.array_equal(got[k], want[k]) for k in want)
    assert not st.is_packed()


def test_topology_change_with_packed_baseline_goes_back_exactly():
    ob = setup()
    before_mesh = H.snapshot(ob)
    runtime.pack_idle_states(wait=True)
    assert _state(ob).is_packed()
    H.enter(ob, "OBJECT")
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:60], cuts=1)
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 9)
    assert runtime.commit(ob) is not None
    h = runtime.history_of(ob)
    after_mesh = H.snapshot(ob)
    runtime.pack_idle_states(wait=True)
    runtime.goto(C, 2)
    H.assert_same(H.snapshot(ob), before_mesh, "before the topology change")
    runtime.pack_idle_states(wait=True)
    runtime.goto(C, len(h.entries))
    H.assert_same(H.snapshot(ob), after_mesh, "after it")


def _edit_snapshot(ob):
    """The mesh as Edit Mode has it (the attributes of an edit mesh aren't all readable: a copy's are)."""
    ob.update_from_editmode()
    m = ob.data.copy()
    try:
        snap = meshio.read_snapshot(m, sculpt_only=False)
        return {k: v.copy() for k, v in snap.arrays.items()}
    finally:
        bpy.data.meshes.remove(m)


def _end_operator(idname, k):
    """The recorder's timer sees one operator end (headless Blender registers no operators run from
    Python: the same stand-in as tests/test_bl_datarec_perf_counts.py)."""
    from hindsight.bl import datarec
    real_marker, real_info = datarec._ops_marker, datarec._last_ops_info
    datarec._OPS["marker"] = (k, 1000 + k)
    datarec._ops_marker = lambda: (k + 1, 1001 + k)
    datarec._last_ops_info = lambda: (idname, 1000 + k, k + 1)
    try:
        datarec.tick()
    finally:
        datarec._ops_marker, datarec._last_ops_info = real_marker, real_info


def test_edit_mode_compares_packed_topology_by_checksum():
    """Edit Mode step by step (Record everything): an operator that keeps the counts is compared
    against the packed topology by checksum (no unpack); Rotate Edge, which changes the connectivity
    with the same counts, is still a topology step, and going back gives the edges back exactly."""
    from hindsight.bl import datarec
    H.fresh()
    runtime.TOPO_PACK_MIN[0] = 0
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    ob = H.make_mesh(20_000, name="E")
    datarec.set_tracked(ob, True)
    datarec.finish_operation("baseline")
    H.enter(ob, "EDIT")
    datarec.finish_operation("edit")
    st = _state(ob)
    runtime.pack_idle_states(wait=True)
    assert st.is_packed()
    bm = bmesh.from_edit_mesh(ob.data)
    for v in bm.verts:
        v.select = v.index % 11 == 0
    bmesh.update_edit_mesh(ob.data)
    h0 = runtime.history_of(ob)
    n0 = len(h0.entries) if h0 is not None else 0
    bpy.ops.mesh.vertices_smooth(factor=0.5)        # positions only: compared in full, topology by checksum
    with _Unpacks() as u:
        _end_operator("MESH_OT_vertices_smooth", 1)
    assert u.n == 0 and st.is_packed(), u.n
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == n0 + 1 and h.entries[-1].kind == "DELTA", (len(h.entries), h.entries[-1].kind)
    smoothed = _edit_snapshot(ob)
    bm = bmesh.from_edit_mesh(ob.data)
    for e in bm.edges:
        e.select = False
    for v in bm.verts:
        v.select = False
    inner = [e for e in bm.edges if len(e.link_faces) == 2][:25]
    for e in inner:
        e.select = True
    bmesh.update_edit_mesh(ob.data)
    bpy.ops.mesh.edge_rotate(use_ccw=False)
    _end_operator("MESH_OT_edge_rotate", 2)
    assert len(h.entries) == n0 + 2 and h.entries[-1].kind == "TOPO", (len(h.entries), h.entries[-1].kind)
    rotated = _edit_snapshot(ob)
    H.enter(ob, "OBJECT")
    runtime.goto(C, n0 + 1)
    H.assert_same(H.snapshot(ob), smoothed, "before Rotate Edge")
    runtime.goto(C, n0 + 2)
    H.assert_same(H.snapshot(ob), rotated, "after it")


def test_a_baseline_changed_while_packing_is_not_swapped():
    ob = setup()
    st = _state(ob)
    job = st.pack_job()
    assert job is not None
    packed = topopack.pack_all(job)
    st.arrays = {k: v.copy() for k, v in st.arrays.items()}   # a new baseline meanwhile
    assert not st.finish_pack(job, packed) and not st.is_packed()


if __name__ == "__main__":
    try:
        for t in (test_pack_round_trip_is_exact, test_idle_packs_the_topology_and_strokes_never_unpack,
                  test_topology_change_with_packed_baseline_goes_back_exactly,
                  test_edit_mode_compares_packed_topology_by_checksum,
                  test_a_baseline_changed_while_packing_is_not_swapped):
            H.run(t)
    finally:
        runtime.TOPO_PACK_MIN[0] = runtime.TOPO_PACK_DEFAULT
    H.finish()
