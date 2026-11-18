"""meshio reads and writes topology through the mesh's own attributes (".edge_verts", ".corner_vert",
".corner_edge") instead of the legacy collections: the same values bit for bit, in both directions,
with the legacy collections as the fallback. Run: <bpy python> tests/test_bl_runtime_topo.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy  # noqa: E402
import bmesh  # noqa: E402
import numpy as np  # noqa: E402
import tests.harness as H  # noqa: E402
from hindsight.bl import meshio, runtime  # noqa: E402
from hindsight.core.types import (  # noqa: E402
    CORNER_EDGE_KEY, CORNER_VERT_KEY, EDGE_VERTS_KEY, FACE_OFFSETS_KEY,
)

C = bpy.context


def _meshes():
    """Meshes with quads, triangles, n-gons, loose edges and loose vertices."""
    H.fresh()
    out = [H.make_mesh(3000, name="Sphere")]
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=13, y_segments=9, size=2.0)
    bmesh.ops.triangulate(bm, faces=bm.faces[: len(bm.faces) // 3])
    v = [bm.verts.new((5 + i, 0, 0)) for i in range(4)]
    bm.edges.new((v[0], v[1]))
    bm.edges.new((v[1], v[2]))                      # loose edges, v[3] a loose vertex
    ng = [bm.verts.new((3 * np.cos(a), 3 * np.sin(a), 1)) for a in np.linspace(0, 6, 7, endpoint=False)]
    bm.faces.new(ng)                                # a 7-sided face
    me = bpy.data.meshes.new("Mixed")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("Mixed", me)
    C.scene.collection.objects.link(ob)
    out.append(ob)
    return out


def _legacy_topology(me):
    c = meshio.counts(me)
    ev = np.empty(c["EDGE"] * 2, np.int32)
    me.edges.foreach_get("vertices", ev)
    cv = np.empty(c["CORNER"], np.int32)
    me.loops.foreach_get("vertex_index", cv)
    ce = np.empty(c["CORNER"], np.int32)
    me.loops.foreach_get("edge_index", ce)
    fo = np.empty(c["FACE"] + 1, np.int32)
    me.polygons.foreach_get("loop_start", fo[:-1])
    fo[-1] = c["CORNER"]
    return {EDGE_VERTS_KEY: ev, CORNER_VERT_KEY: cv, CORNER_EDGE_KEY: ce, FACE_OFFSETS_KEY: fo}


def _same(a, b):
    return a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()


class _no_attrs:
    """Force the legacy collections (as if the mesh had no topology attributes)."""
    def __enter__(self):
        self.old = meshio._int_attr
        meshio._int_attr = lambda *a, **k: None

    def __exit__(self, *a):
        meshio._int_attr = self.old


def test_topology_read_matches_legacy():
    for ob in _meshes():
        me = ob.data
        want = _legacy_topology(me)
        got = meshio.read_topology(me)
        assert set(got) == set(want)
        for k in want:
            assert _same(got[k], want[k]), (ob.name, k)
        with _no_attrs():
            old = meshio.read_topology(me)
        for k in want:
            assert _same(old[k], want[k]), (ob.name, k, "fallback")
        c = meshio.counts(me)
        assert meshio.topology_equal(me, c, got, deep=True)
        bad = dict(got)
        bad[EDGE_VERTS_KEY] = got[EDGE_VERTS_KEY].copy()
        bad[EDGE_VERTS_KEY][-1] ^= 1
        assert not meshio.topology_equal(me, c, bad, deep=True), "a changed edge must be seen"
        assert meshio.topology_equal(me, c, bad, deep="corners")


def test_write_snapshot_matches_legacy():
    for ob in _meshes():
        snap = meshio.read_snapshot(ob.data)
        results = []
        for force_legacy in (False, True):
            me = bpy.data.meshes.new("W")
            if force_legacy:
                with _no_attrs():
                    meshio.write_snapshot(me, snap)
            else:
                meshio.write_snapshot(me, snap)
            assert not me.validate(verbose=False), "the written mesh needed fixing"
            back = meshio.read_snapshot(me)
            assert back.counts == snap.counts
            assert set(back.arrays) == set(snap.arrays), set(back.arrays) ^ set(snap.arrays)
            for k, a in snap.arrays.items():
                assert _same(back.arrays[k], a), (ob.name, k, force_legacy)
            leg = _legacy_topology(me)
            for k, a in leg.items():
                assert _same(a, snap.arrays[k]), (ob.name, k, force_legacy, "legacy read")
            results.append(meshio.fingerprint(me))
        assert results[0] == results[1]


def test_fingerprint_same_both_paths():
    for ob in _meshes():
        a = meshio.fingerprint(ob.data)
        with _no_attrs():
            b = meshio.fingerprint(ob.data)
        assert a == b, ob.name


def test_topology_step_round_trip():
    """A topology step recorded and played back both ways (the path these readers and writers serve)."""
    H.fresh()
    ob = H.make_mesh(2000)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    assert runtime.commit(ob) is not None
    before = H.snapshot(ob)
    H.enter(ob, "EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide()
    H.enter(ob, "SCULPT")
    after = H.snapshot(ob)
    h = runtime.history_of(ob)
    assert h.entries[-1].kind == "TOPO"
    for _ in range(2):
        runtime.goto(C, h.cursor - 1)
        H.assert_same(H.snapshot(ob), before, "back")
        runtime.goto(C, h.cursor + 1)
        H.assert_same(H.snapshot(ob), after, "forward")


for f in [test_topology_read_matches_legacy, test_write_snapshot_matches_legacy,
          test_fingerprint_same_both_paths, test_topology_step_round_trip]:
    H.run(f)
H.finish()
