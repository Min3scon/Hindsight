import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R081: the Morph History dab's box test (`morph.in_box`) tests one axis at a time on the vertices still in,
instead of all three axes of every vertex at once (34 of the 40 ms of a dab at 1M vertices). It must pick
exactly the vertices the old test picked, in the same order: the same float64 differences, the same bound,
hidden vertices left out, NaN never inside; and a whole Morph History stroke gives the same mesh as with the
old test.
R083 moved the dab into the engine: `in_box` is now tests/reference/morph_dab.py (the engine's oracle, checked
bit for bit in test_core_morph_dab), and the whole stroke here goes through the engine against the numpy dab
with the box test from before R081."""
import bpy
import numpy as np
from mathutils import Vector

import tests.harness as H
from hindsight.bl import meshio, runtime
from hindsight.bl import morph as BM
from hindsight.core import native
from hindsight.core.types import POSITION_KEY

sys.path.insert(0, os.path.dirname(__file__))
from reference import morph_dab as R  # noqa: E402

C = bpy.context
old_in_box = R.old_in_box


def check(co, c, r, hidden=None, what=""):
    got = R.in_box(co, c, r, hidden)
    ref = old_in_box(co, c, r, hidden)
    assert got.dtype == ref.dtype and np.array_equal(got, ref), (what, got[:10], ref[:10], got.size, ref.size)
    return ref.size


def test_same_vertices_as_before_random():
    rng = np.random.default_rng(81)
    inside = 0
    for k in range(300):
        n = int(rng.integers(0, 5000))
        co = (rng.normal(0, float(rng.choice([1e-3, 1.0, 50.0])), (n, 3))).astype(np.float32)
        if n and k % 7 == 0:
            co[rng.integers(0, n, 5)] = np.nan
        if n and k % 11 == 0:
            co[rng.integers(0, n, 5)] = rng.choice([np.inf, -np.inf, 3.4e38, -0.0], 5).astype(np.float32)[:, None]
        c = co[int(rng.integers(0, n))].astype(np.float64) if n and k % 3 else rng.normal(0, 1, 3)
        r = float(rng.choice([0.0, 1e-7, 0.01, 0.3, 2.0, 1e9]))
        hidden = rng.random(n) < 0.3 if k % 2 else None
        inside += check(co, c, r, hidden, f"case {k}")
    assert inside > 1000, inside


def test_same_vertices_at_the_bound():
    # vertices one float32 step either side of centre +- r on each axis, the centre and r not float32 numbers
    c = np.array([0.1, -0.7, 3.3], np.float64)
    r = 0.25
    pts = []
    for ax in range(3):
        for sign in (-1.0, 1.0):
            edge = np.float32(c[ax] + sign * r)
            for v in (np.nextafter(np.nextafter(edge, np.float32(-np.inf)), np.float32(-np.inf)),
                      np.nextafter(edge, np.float32(-np.inf)), edge, np.nextafter(edge, np.float32(np.inf)),
                      np.nextafter(np.nextafter(edge, np.float32(np.inf)), np.float32(np.inf))):
                p = c.astype(np.float32)
                p[ax] = v
                pts.append(p)
    co = np.array(pts, np.float32)
    n = check(co, c, r, None, "bound")
    assert 0 < n < len(co), n
    check(co, c, r, np.arange(len(co)) % 2 == 0, "bound hidden")
    # the bound exactly representable
    c2 = np.array([0.5, 0.5, 0.5])
    co2 = np.array([[0.75, 0.5, 0.5], [0.25, 0.5, 0.5], [0.7500001, 0.5, 0.5], [0.5, 0.5, 0.2499999]], np.float32)
    assert check(co2, c2, 0.25, None, "exact") == 2


def test_a_stroke_gives_the_same_mesh_as_before():
    """The same dabs through the engine and through the numpy dab with the box test from before R081: the same
    positions, bit for bit, and the same counts."""
    results = []
    real = native.ENGINE.morph_dab

    def numpy_dab(cur, start, target, acc, mask, hidden, cx, cy, cz, r, s):
        return R.morph_dab(cur, start, target, acc, mask, hidden, cx, cy, cz, r, s, box=old_in_box)
    for dab in (numpy_dab, None):
        H.fresh()
        ob = H.make_mesh(6000)
        H.enter(ob, "SCULPT")
        H.fake_stroke(ob, 1, n=200)
        assert H.commit(ob) is not None
        runtime.set_morph_target(C)
        for s in (2, 3, 4):
            H.fake_stroke(ob, s, n=100000)
            assert H.commit(ob) is not None
        co = meshio.read_channel(ob.data, POSITION_KEY).reshape(-1, 3).astype(np.float64)
        rng = np.random.default_rng(7)
        if dab is not None:
            native.ENGINE.morph_dab = dab
        try:
            assert BM.begin(ob), BM.last_message[0]
            counts = []
            for i in range(25):
                p = co[int(rng.integers(0, len(co)))]
                counts.append(BM.apply_morph_at(ob, Vector(p), float(rng.uniform(0.05, 0.6)), float(rng.uniform(0.1, 1))))
            BM.end(ob)
        finally:
            native.ENGINE.morph_dab = real
        results.append((counts, meshio.read_channel(ob.data, POSITION_KEY).copy()))
    (c0, p0), (c1, p1) = results
    assert c0 == c1 and sum(c0) > 0, (c0, c1)
    assert p0.view(np.uint32).tobytes() == p1.view(np.uint32).tobytes()


if __name__ == "__main__":
    for t in (test_same_vertices_as_before_random, test_same_vertices_at_the_bound,
              test_a_stroke_gives_the_same_mesh_as_before):
        H.run(t)
    H.finish()
