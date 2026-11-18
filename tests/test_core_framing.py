import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Tests for hindsight.core.framing (Movie Mode camera and lighting math). Plain script, no bpy."""
import copy
import math
import traceback

import numpy as np

from hindsight.core import delta as _delta
from hindsight.core import framing as F
from hindsight.core.history import History, MeshState
from hindsight.core.types import (
    POSITION_KEY, MASK_KEY, EDGE_VERTS_KEY, ChannelDelta, Snapshot, Step, DELTA, TOPO, JOIN, DATA,
)

A, B = "objA", "objB"


def close(a, b, tol=1e-9):
    return np.allclose(np.asarray(a, np.float64), np.asarray(b, np.float64), atol=tol, rtol=0)


# ---------------------------------------------------------------- camera
def test_view_basis():
    for az, el in [(0.0, 0.0), (math.pi / 2, 0.0), (0.0, math.pi / 4), (1.3, -0.7), (-2.0, 1.5)]:
        m = F.view_basis(az, el)
        assert m.shape == (3, 3) and m.dtype == np.float64
        assert close(m.T @ m, np.eye(3), 1e-12), (az, el)
        assert abs(np.linalg.det(m) - 1.0) < 1e-12
        back = (math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))
        assert close(m[:, 2], back, 1e-12)
    m = F.view_basis(0, 0)
    assert close(m[:, 0], (1, 0, 0)) and close(m[:, 1], (0, 0, 1)) and close(m[:, 2], (0, -1, 0))
    m = F.view_basis(math.pi / 2, 0)        # camera on +X looking toward -X
    assert close(m[:, 0], (0, 1, 0), 1e-12) and close(m[:, 1], (0, 0, 1), 1e-12)
    assert close(m[:, 2], (1, 0, 0), 1e-12)
    m = F.view_basis(0, math.pi / 4)        # in front, above, looking down
    s = math.sqrt(0.5)
    assert close(m[:, 0], (1, 0, 0), 1e-12) and close(m[:, 1], (0, s, s), 1e-12)
    assert close(m[:, 2], (0, -s, s), 1e-12)


def test_lens_tangents():
    t = 18.0 / 50.0
    tx, ty = F.lens_tangents(50, 36, 24, "AUTO", 1920, 1080)
    assert abs(tx - t) < 1e-12 and abs(ty - t * 1080 / 1920) < 1e-12
    tx, ty = F.lens_tangents(50, 36, 24, "AUTO", 1080, 1920)     # portrait: width fits vertically
    assert abs(ty - t) < 1e-12 and abs(tx - t * 1080 / 1920) < 1e-12
    tx, ty = F.lens_tangents(50, 36, 24, "HORIZONTAL", 1080, 1920)
    assert abs(tx - t) < 1e-12 and abs(ty - t * 1920 / 1080) < 1e-12
    tv = 12.0 / 50.0
    tx, ty = F.lens_tangents(50, 36, 24, "VERTICAL", 1920, 1080)
    assert abs(ty - tv) < 1e-12 and abs(tx - tv * 1920 / 1080) < 1e-12
    # non-square pixels: 1000x1000 at aspect 2:1 is a 2000x1000 frame
    tx, ty = F.lens_tangents(50, 36, 24, "AUTO", 1000, 1000, 2.0, 1.0)
    assert abs(tx - t) < 1e-12 and abs(ty - t * 0.5) < 1e-12
    tx, ty = F.lens_tangents(50, 36, 24, "AUTO", 1000, 1000, 1.0, 2.0)
    assert abs(ty - t) < 1e-12 and abs(tx - t * 0.5) < 1e-12
    tx, ty = F.lens_tangents(35, 36, 24, "AUTO", 1000, 1000)     # square: horizontal
    assert abs(tx - 18 / 35) < 1e-12 and abs(ty - 18 / 35) < 1e-12


def random_cloud(rng):
    n = int(rng.integers(1, 400))
    p = rng.standard_normal((n, 3)) * rng.uniform(0.01, 20, 3)
    rot = np.linalg.qr(rng.standard_normal((3, 3)))[0]
    p = p @ rot + rng.uniform(-1e4, 1e4, 3)
    return p.astype(np.float32 if rng.random() < 0.5 else np.float64)


def check_inside(p, basis, f, tx, ty):
    c = np.asarray(p, np.float64).reshape(-1, 3) @ basis
    cam = np.asarray(f["location"]) @ basis
    d = cam[2] - c[:, 2]
    scale = 1e-9 * (1.0 + np.abs(cam).max() + np.abs(c).max())
    assert (d >= f["near"] - scale * 10).all()
    sx = d * tx - np.abs(c[:, 0] - cam[0])
    sy = d * ty - np.abs(c[:, 1] - cam[1])
    assert (sx >= -scale * 10).all() and (sy >= -scale * 10).all(), (sx.min(), sy.min())
    return d, sx, sy


def test_fit_invariant():
    rng = np.random.default_rng(7)
    for _ in range(200):
        p = random_cloud(rng)
        if len(p) < 2:
            p = np.vstack([p, p + 1]).astype(p.dtype)
        basis = F.view_basis(rng.uniform(-math.pi, math.pi), rng.uniform(-1.5, 1.5))
        tx, ty = rng.uniform(0.05, 2.0), rng.uniform(0.05, 2.0)
        flat = rng.random() < 0.3
        e = F.extents(p.reshape(-1) if flat else p, basis, tx, ty)
        f = F.fit(e, basis, tx, ty)
        assert f is not None
        d, sx, sy = check_inside(p, basis, f, tx, ty)
        # tight in the limiting axis: some point touches a frame edge
        zx, zy, zm = (e[0] + e[1]) / (2 * tx), (e[2] + e[3]) / (2 * ty), e[5] + 1e-3
        if zm < max(zx, zy):        # (otherwise the nearest point limits, at min_distance)
            s, t = (sx, tx) if zx >= zy else (sy, ty)
            assert (s / (d * t)).min() <= 1e-4, (s / (d * t)).min()
        assert abs(f["near"] - d.min()) <= 1e-6 * (1 + abs(d).max())
        assert abs(f["far"] - d.max()) <= 1e-6 * (1 + abs(d).max())
        assert f["near"] >= 1e-3 - 1e-12
        # center on the view axis at mid depth
        loc = np.asarray(f["location"])
        ctr = np.asarray(f["center"])
        assert close(loc - ctr, basis[:, 2] * f["depth"], 1e-6 * (1 + np.abs(loc).max()))
        assert abs(f["depth"] - (f["near"] + f["far"]) / 2) < 1e-6 * (1 + f["far"])


def test_merge():
    rng = np.random.default_rng(3)
    for _ in range(50):
        p, q = random_cloud(rng), random_cloud(rng)
        basis = F.view_basis(rng.uniform(-3, 3), rng.uniform(-1, 1))
        tx, ty = rng.uniform(0.1, 1), rng.uniform(0.1, 1)
        m = F.merge(F.extents(p, basis, tx, ty), F.extents(q, basis, tx, ty))
        both = F.extents(np.vstack([p.astype(np.float64), q.astype(np.float64)]), basis, tx, ty)
        assert close(m, both, 0)
        bb = F.merge_bounds(F.bounds(p), F.bounds(q))
        allp = np.vstack([p.astype(np.float64), q.astype(np.float64)])
        assert close(bb[0], allp.min(0), 0) and close(bb[1], allp.max(0), 0)
    e = F.extents(p, basis, tx, ty)
    assert F.merge(None, None) is None and close(F.merge(e, None), e) and close(F.merge(None, e), e)
    b = F.bounds(p)
    assert F.merge_bounds(None, None) is None
    assert close(F.merge_bounds(b, None)[0], b[0]) and close(F.merge_bounds(None, b)[1], b[1])


def test_margin():
    rng = np.random.default_rng(11)
    for margin in (0.0, 0.1, 0.25, 0.5):
        for _ in range(30):
            p = random_cloud(rng).astype(np.float64)
            basis = F.view_basis(rng.uniform(-3, 3), rng.uniform(-1, 1))
            tx, ty = rng.uniform(0.1, 1), rng.uniform(0.1, 1)
            sx_, sy_ = F.shrink(tx, ty, margin)
            assert abs(sx_ - tx * (1 - margin)) < 1e-15 and abs(sy_ - ty * (1 - margin)) < 1e-15
            f = F.fit(F.extents(p, basis, sx_, sy_), basis, sx_, sy_)
            c = p @ basis
            cam = np.asarray(f["location"]) @ basis
            d = cam[2] - c[:, 2]
            # projected (normalised) image coordinates stay inside 1 - margin of the full frame
            u = np.abs(c[:, 0] - cam[0]) / (d * tx)
            v = np.abs(c[:, 1] - cam[1]) / (d * ty)
            assert u.max() <= 1 - margin + 1e-7 and v.max() <= 1 - margin + 1e-7
            assert max(u.max(), v.max()) >= 1 - margin - 1e-4
    assert F.shrink(1, 1, 5) == (1 - F.MAX_MARGIN, 1 - F.MAX_MARGIN)
    assert F.shrink(1, 1, -1) == (1.0, 1.0)
    assert F.shrink(1, 1, float("nan")) == (1.0, 1.0)


def test_degenerate():
    basis = F.view_basis(0.3, 0.2)
    tx, ty = 0.36, 0.2
    for pts in ([(1.0, 2.0, 3.0)], np.full((10, 3), 5.0, np.float32), np.zeros((0, 3)), [],
                np.array([[np.nan, 0, 0], [np.inf, 1, 1]]), np.zeros(7), None):
        e = F.extents(pts, basis, tx, ty)
        f = F.fit(e, basis, tx, ty)
        b = F.bounds(pts)
        if e is None:
            assert f is None and b is None
        else:
            assert f is not None and f["near"] >= 1e-3 - 1e-12
            check_inside(pts, basis, f, tx, ty)
    # NaN rows ignored
    p = np.array([[0, 0, 0], [np.nan, 5, 5], [1, 1, 1], [2, np.inf, 0]], np.float32)
    assert close(F.extents(p, basis, tx, ty), F.extents(p[[0, 2]], basis, tx, ty), 0)
    assert close(F.bounds(p)[0], (0, 0, 0)) and close(F.bounds(p)[1], (1, 1, 1))
    assert F.fit(None, basis, tx, ty) is None
    assert F.fit(np.zeros(6), basis, 0.0, ty) is None
    assert F.fit(np.full(6, np.nan), basis, tx, ty) is None


# ---------------------------------------------------------------- lighting
def test_light_rig():
    rng = np.random.default_rng(5)
    for _ in range(20):
        az, el = rng.uniform(-3, 3), rng.uniform(-1.2, 1.2)
        basis = F.view_basis(az, el)
        center = rng.uniform(-100, 100, 3)
        radius = rng.uniform(0.01, 50)
        rig = F.light_rig(basis, center, radius)
        assert [l["role"] for l in rig] == ["key", "fill", "rim"]
        view = -basis[:, 2]
        by = {l["role"]: l for l in rig}
        for l in rig:
            d = np.asarray(l["direction"])
            assert abs(np.linalg.norm(d) - 1) < 1e-12
            assert close(l["location"], center - d * 3 * radius, 1e-9 * (1 + radius + 100))
            assert l["strength"] > 0 and 0 < l["angle"] < math.pi and len(l["color"]) == 3
        assert np.dot(by["key"]["direction"], view) > 0
        assert np.dot(by["fill"]["direction"], view) > 0
        assert np.dot(by["rim"]["direction"], view) < 0
        # key from the upper left, fill from the right, rim on the fill side
        right, up = basis[:, 0], basis[:, 1]
        assert np.dot(by["key"]["direction"], right) > 0 and np.dot(by["key"]["direction"], up) < 0
        assert np.dot(by["fill"]["direction"], right) < 0
        assert np.dot(by["rim"]["direction"], right) < 0
        # local directions are the same for every basis: the rig turns with the camera
        ref = F.light_rig(F.view_basis(0, 0), (0, 0, 0), 1.0)
        b0 = F.view_basis(0, 0)
        rot = basis @ b0.T
        for l, r in zip(rig, ref):
            assert close(l["direction"], rot @ np.asarray(r["direction"]), 1e-12)
            assert l["strength"] == r["strength"] and l["color"] == r["color"]
    # strengths independent of size
    s1 = [l["strength"] for l in F.light_rig(F.view_basis(0, 0), (0, 0, 0), 0.01)]
    s2 = [l["strength"] for l in F.light_rig(F.view_basis(0, 0), (0, 0, 0), 1000)]
    assert s1 == s2 == [F.KEY_STRENGTH, F.FILL_STRENGTH, F.RIM_STRENGTH]


# ---------------------------------------------------------------- sampling
def test_shifted_equals_world_extents():
    """A mesh measured in its own space, then shifted, gives the extents of its world positions."""
    rng = np.random.default_rng(11)
    for _ in range(50):
        local = random_cloud(rng).astype(np.float32)
        m = rng.normal(0, 1, (3, 3)) * rng.uniform(0.2, 4)          # rotation, scale and shear
        t = rng.normal(0, 30, 3)
        basis = F.view_basis(rng.uniform(-3, 3), rng.uniform(-1, 1))
        tx, ty = rng.uniform(0.1, 1), rng.uniform(0.1, 1)
        world = local.astype(np.float64) @ m.T + t
        want = F.extents(world, basis, tx, ty)
        got = F.shifted(F.extents(local, m.T @ basis, tx, ty), t @ basis, tx, ty)
        assert close(got, want, 1e-4 * (1 + np.abs(want).max())), (got, want)
    assert F.shifted(None, (0, 0, 0), 1, 1) is None


def _all_extents(arrays, basis, tx, ty):
    return F.extents(np.vstack([a.reshape(-1, 3) for a in arrays]), basis, tx, ty)


def test_history_extents_covers_every_step():
    """Merged with the mesh at the cursor it equals the extents of every position of the history;
    the history and the state are left as they were."""
    for seed in (1, 2, 3):
        h, end = make_case(seed)                      # delta steps, a topology step, a second object
        n = len(h.entries)
        assert any(e.kind == TOPO for e in h.entries) and any(e.target == B for e in h.entries)
        truth = truth_at(h, end, range(n + 1))
        assert len({a.size for a in truth.values()}) == 2, "the vertex count changes at the topology step"
        rng = np.random.default_rng(4)
        topo = next(i for i, e in enumerate(h.entries) if e.kind == TOPO)
        for cursor in (n, 0, n // 2, topo, topo + 1):
            hc = copy.deepcopy(h)
            st = copy.deepcopy(end)
            hc.goto(cursor, {A: st})
            before = fingerprint(hc, st)
            for _ in range(8):
                basis = F.view_basis(rng.uniform(-3, 3), rng.uniform(-1.2, 1.2))
                tx, ty = rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)
                past = F.history_extents(hc, st, A, basis, tx, ty)
                assert past is not None
                got = F.merge(F.extents(st.arrays[POSITION_KEY], basis, tx, ty), past)
                want = _all_extents(truth.values(), basis, tx, ty)
                assert close(got, want, 1e-9 * (1 + np.abs(want).max())), (cursor, got, want)
            assert fingerprint(hc, st) == before, "history_extents changed the history or the state"
        assert F.history_extents(h, None, A, basis, tx, ty) is None
        assert F.history_extents(h, end, "nobody", basis, tx, ty) is None


def test_history_extents_stops():
    """A JOIN, a missing snapshot or a bad delta ends the walk on that side without raising: only
    the positions before it count."""
    basis, tx, ty = F.view_basis(0.3, 0.2), 0.5, 0.4

    def check(h, end, truth, reachable):
        got = F.merge(F.extents(end.arrays[POSITION_KEY], basis, tx, ty),
                      F.history_extents(h, end, A, basis, tx, ty))
        want = _all_extents([truth[p] for p in reachable], basis, tx, ty)
        assert close(got, want, 1e-9 * (1 + np.abs(want).max())), (got, want)

    h, end = make_case(4, pack=False)
    n = len(h.entries)
    truth = truth_at(h, end, range(n + 1))
    j = n // 2
    h.entries[j].meta = dict(h.entries[j].meta, kind=JOIN)
    check(h, end, truth, range(j + 1, n + 1))
    h, end = make_case(5, pack=False)
    n = len(h.entries)
    truth = truth_at(h, end, range(n + 1))
    t = next(i for i, e in enumerate(h.entries) if e.kind == TOPO)
    h.entries[t].step.before = None
    check(h, end, truth, range(t + 1, n + 1))
    h, end = make_case(6, pack=False)
    n = len(h.entries)
    truth = truth_at(h, end, range(n + 1))
    k = next(i for i in range(n - 1, -1, -1)
             if h.entries[i].target == A and h.entries[i].kind == DELTA and h.entries[i].step.deltas)
    d = h.entries[k].step.deltas[-1]
    h.entries[k].step.deltas[-1] = ChannelDelta(d.key, np.array([10 ** 6], np.uint32),
                                                d.vals[:1], d.exists, d.op)
    check(h, end, truth, range(k + 1, n + 1))


def snap_of(rng, nv):
    return Snapshot({"POINT": nv, "EDGE": nv, "FACE": 0, "CORNER": 0},
                    {POSITION_KEY: rng.standard_normal(nv * 3).astype(np.float32),
                     EDGE_VERTS_KEY: rng.integers(0, nv, nv * 2).astype(np.int32)})


def build_history(seed=1, n=60, topo_at=30, pack=True):
    """History of A (deltas, one TOPO, mask deltas, DATA steps) interleaved with steps of B.
    Returns (h with its cursor at the end, A's state at position 0)."""
    rng = np.random.default_rng(seed)
    nv = 200
    pa = rng.standard_normal(nv * 3).astype(np.float32)
    mask = np.zeros(nv, np.float32)
    pb = rng.standard_normal(90).astype(np.float32)
    start = MeshState({"POINT": nv, "EDGE": nv, "FACE": 0, "CORNER": 0},
                      {POSITION_KEY: pa.copy(), MASK_KEY: mask.copy()})
    h = History(A, members=[A, B])
    seq = 0
    for i in range(n):
        seq += 1
        if i != topo_at and rng.random() < 0.35:      # a step of B
            nb = pb.copy()
            nb[rng.integers(0, 90, 10)] += 1
            h.push(Step(seq, "Stroke", "SCULPT", B, deltas=[_delta.diff(POSITION_KEY, pb, nb)]))
            pb = nb
            continue
        if i == topo_at:
            before = Snapshot(dict(start.counts), {POSITION_KEY: pa.copy()})
            nv = 333
            after = snap_of(rng, nv)
            h.push(Step(seq, "Remesh", "SCULPT", A, kind=TOPO, before=before, after=after))
            pa = after.arrays[POSITION_KEY].copy()
            mask = np.zeros(nv, np.float32)
            continue
        if i % 7 == 3:
            h.push(Step(seq, "Prop", "OBJECT", A, kind=DATA))
            continue
        na = pa.copy()
        sel = rng.choice(nv, int(rng.integers(1, nv)), replace=False)
        na[(sel[:, None] * 3 + np.arange(3)).ravel()] += rng.standard_normal(len(sel) * 3).astype(np.float32)
        nm = mask.copy()
        nm[rng.integers(0, nv, 5)] = rng.random(5).astype(np.float32)
        ds = [d for d in (_delta.diff(MASK_KEY, mask, nm), _delta.diff(POSITION_KEY, pa, na)) if d]
        h.push(Step(seq, "Stroke", "SCULPT", A, deltas=ds))
        pa, mask = na, nm
    if pack:
        for j, e in enumerate(h.entries):
            if j % 2 == 0:
                e.pack("zlib")
    return h, start


def fingerprint(h, st):
    return (h.cursor, len(h.entries), [(id(e), e.is_packed(), e.blob, id(e.step), dict(e.meta)) for e in h.entries],
            dict(st.counts), {k: v.tobytes() for k, v in st.arrays.items()}, set(st.removed))


def truth_at(h, end_state, positions):
    """{position: A's positions there}, from real gotos on deep copies."""
    out = {}
    for p in positions:
        hr = copy.deepcopy(h)
        sr = copy.deepcopy(end_state)
        hr.goto(p, {A: sr})
        out[p] = sr.arrays[POSITION_KEY].copy()
    return out


def make_case(seed, **kw):
    """(history at its end, A's state at the end), from replaying a copy from the start."""
    h, start = build_history(seed, **kw)
    hs = copy.deepcopy(h)
    hs.cursor = 0
    hs.goto(len(hs.entries), {A: start})
    return h, start


# ---------------------------------------------------------------- modifiers (review S2-1)
def _rand_view(rng):
    return F.view_basis(rng.uniform(-3, 3), rng.uniform(-1.2, 1.2)), rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)


def test_extents_multi_and_batched_history():
    """extents_multi equals extents basis by basis; history_extents_multi (moved vertices measured
    many steps at a time) equals history_extents basis by basis, with any batch size."""
    rng = np.random.default_rng(21)
    for _ in range(30):
        pts = random_cloud(rng)
        views = [_rand_view(rng) for _ in range(3)]
        got = F.extents_multi(pts, [v[0] for v in views], [(v[1], v[2]) for v in views], chunk=37)
        for i, (b, tx, ty) in enumerate(views):
            want = F.extents(pts, b, tx, ty)
            assert close(got[i], want, 1e-9 * (1 + np.abs(want).max())), (got[i], want)
    assert F.extents_multi(np.zeros((0, 3)), [np.eye(3)], [(1, 1)]) is None
    h, end = make_case(7)
    views = [_rand_view(rng) for _ in range(4)]
    bases, tans = np.stack([v[0] for v in views]), [(v[1], v[2]) for v in views]
    for batch in (1, 50, 65536):
        got = F.history_extents_multi(h, end, A, bases, tans, batch=batch)
        for i, (b, tx, ty) in enumerate(views):
            want = F.history_extents(h, end, A, b, tx, ty)
            assert close(got[i], want, 1e-9 * (1 + np.abs(want).max())), (batch, i)


def test_plus_and_ball():
    """plus is the Minkowski sum; ball_extents is a ball's extents under any matrix."""
    rng = np.random.default_rng(22)
    for _ in range(30):
        a, b = random_cloud(rng).astype(np.float64)[:40], random_cloud(rng).astype(np.float64)[:40]
        m = rng.normal(0, 1, (3, 3))
        tx, ty = rng.uniform(0.2, 0.8, 2)
        sums = (a[:, None, :] + b[None, :, :]).reshape(-1, 3)
        want = F.extents(sums, m, tx, ty)
        got = F.plus(F.extents(a, m, tx, ty), F.extents(b, m, tx, ty))
        assert close(got, want, 1e-7 * (1 + np.abs(want).max())), (got, want)
        r = rng.uniform(0.1, 5)
        dirs = rng.normal(size=(20000, 3))
        sphere = dirs / np.linalg.norm(dirs, axis=1)[:, None] * r
        ball = F.ball_extents(r, m, tx, ty)
        samp = F.extents(sphere, m, tx, ty)
        assert (samp[[0, 1, 2, 3, 5]] <= ball[[0, 1, 2, 3, 5]] + 1e-9).all() and samp[4] >= ball[4] - 1e-9
        assert close(samp, ball, 0.02 * r * (1 + np.abs(m).max() * 3)), (samp, ball)
    assert F.plus(None, np.zeros(6)) is None


def _mirror_points(p, axes, fr):
    out = [p]
    inv = np.linalg.inv(fr)
    for ax in range(3):
        if axes[ax]:
            s = np.eye(4)
            s[ax, ax] = -1
            m = fr @ s @ inv
            out = out + [q @ m[:3, :3].T + m[:3, 3] for q in out]
    return np.vstack(out)


def _envelope_points(p, groups):
    return np.vstack([(p @ np.asarray(L).T)[:, None, :] + np.asarray(Z)[None, :, :] for L, Z in groups]).reshape(-1, 3)


def test_envelope_mirror_and_array_exact():
    """For the shape now the envelope is exactly the mirrored and arrayed copies."""
    rng = np.random.default_rng(23)
    for _ in range(25):
        p = random_cloud(rng).astype(np.float64)[:60] * 1e-3
        axes = tuple(bool(v) for v in rng.integers(0, 2, 3))
        rot = np.linalg.qr(rng.standard_normal((3, 3)))[0]
        fr = np.eye(4)
        if rng.random() < 0.5:
            fr[:3, :3] = rot * rng.uniform(0.5, 2)
            fr[:3, 3] = rng.normal(0, 3, 3)
        stack = [("MIRROR", axes, fr), ("OTHER", "SUBSURF")]
        count = int(rng.integers(1, 5))
        const, rel = rng.normal(0, 2, 3), rng.normal(0, 1, 3)
        stack.append(("ARRAY", count, const, rel))
        lo, hi = p.min(0), p.max(0)
        groups = F.envelope(stack, lo, hi, exact=True)
        mir = _mirror_points(p, axes, fr)
        size = mir.max(0) - mir.min(0)
        truth = np.vstack([mir + j * (const + rel * size) for j in range(count)])
        b, tx, ty = _rand_view(rng)
        got = F.extents(_envelope_points(p, groups), b, tx, ty)
        want = F.extents(truth, b, tx, ty)
        tol = 1e-6 * (1 + np.abs(want).max())
        # exact when the mirror frame is a pure rotation and scale (its box of the mesh's box is then
        # what Blender's array measures only if axis aligned): check it holds, and is exact without
        # the mirror object
        assert (got[[0, 1, 2, 3, 5]] >= want[[0, 1, 2, 3, 5]] - tol).all() and got[4] <= want[4] + tol
        if np.allclose(fr, np.eye(4)):
            assert close(got, want, tol), (got, want)
        # a history: every size from 0 up to the box's is held
        small = p * 0.3 + rng.normal(0, 0.01, 3)
        wide = F.envelope(stack, np.minimum(lo, small.min(0)), np.maximum(hi, small.max(0)), exact=False)
        assert [np.round(L, 9).tobytes() for L, _ in wide] == [np.round(L, 9).tobytes() for L, _ in groups]
        mir_s = _mirror_points(small, axes, fr)
        truth_s = np.vstack([mir_s + j * (const + rel * (mir_s.max(0) - mir_s.min(0))) for j in range(count)])
        got_s = F.extents(_envelope_points(small, wide), b, tx, ty)
        want_s = F.extents(truth_s, b, tx, ty)
        tol = 1e-6 * (1 + np.abs(want_s).max())
        assert (got_s[[0, 1, 2, 3, 5]] >= want_s[[0, 1, 2, 3, 5]] - tol).all() and got_s[4] <= want_s[4] + tol


def test_reach():
    """reach: 0 when the envelope holds the evaluated shape; the push past it otherwise."""
    rng = np.random.default_rng(24)
    p = rng.normal(size=(500, 3))
    groups = F.envelope([], p.min(0), p.max(0))
    assert F.reach(p * 0.9, p, groups) == 0.0
    n = p / np.linalg.norm(p, axis=1)[:, None]
    r = F.reach(p + n * 0.25, p, groups)
    assert 0.1 < r <= 0.25 + 1e-5, r
    mg = F.envelope([("MIRROR", (True, False, False), np.eye(4))], p.min(0), p.max(0))
    mirrored = np.vstack([p, p * [-1, 1, 1]])
    assert F.reach(mirrored, p, mg) < 1e-4
    assert F.reach(mirrored, p, groups) > 0.0 or np.allclose(p.min(0)[0], -p.max(0)[0])
    assert F.reach(np.zeros((0, 3)), p, groups) == 0.0
    many = []
    for i in range(4):
        fr = np.eye(4)
        fr[:3, :3] = np.linalg.qr(rng.standard_normal((3, 3)))[0]
        many.append(("MIRROR", (True, True, True), fr))
    assert F.envelope(many, p.min(0), p.max(0)) is None, "more than MAX_GROUPS reflections"


TESTS = [test_view_basis, test_lens_tangents, test_fit_invariant, test_merge, test_margin,
         test_degenerate, test_light_rig, test_shifted_equals_world_extents,
         test_history_extents_covers_every_step, test_history_extents_stops,
         test_extents_multi_and_batched_history, test_plus_and_ball, test_envelope_mirror_and_array_exact,
         test_reach]


if __name__ == "__main__":
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    sys.stdout.flush()
    sys.exit(1 if failed else 0)
