import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R083: the engine's `morph_dab` (one dab of the Morph History brush in one pass) against the numpy code it
replaced (tests/reference/morph_dab.py): the same positions, the same strongest weights and the same count, bit
for bit, on random meshes and strokes (masks out of range and NaN, hidden vertices, vertices one float32 step
either side of the box and of the sphere), dab after dab on the same arrays; and its refusals."""
import struct

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from hindsight.core.native import ENGINE  # noqa: E402
from reference import morph_dab as R  # noqa: E402


def same_bits(a, b):
    """Equal bit for bit, except that any NaN equals any NaN (numpy and the engine needn't agree on its payload)."""
    if a.shape != b.shape or a.dtype != b.dtype:
        return False
    na, nb = np.isnan(a), np.isnan(b)
    if not np.array_equal(na, nb):
        return False
    u = np.uint32 if a.dtype == np.float32 else np.uint64
    return np.array_equal(a[~na].view(u), b[~nb].view(u))


def run_both(co, start, target, acc, mask, hidden, c, r, s, what):
    co1, acc1 = co.copy(), acc.copy()
    co2, acc2 = co.copy(), acc.copy()
    m1 = R.morph_dab(co1, start, target, acc1, mask, hidden, c[0], c[1], c[2], r, s)
    m2 = ENGINE.morph_dab(co2, start, target, acc2, mask, hidden, c[0], c[1], c[2], r, s)
    assert m1 == m2, (what, m1, m2)
    assert same_bits(co1, co2), (what, np.flatnonzero(co1.view(np.uint32) != co2.view(np.uint32))[:5])
    assert same_bits(acc1, acc2), (what, np.flatnonzero(acc1.view(np.uint64) != acc2.view(np.uint64))[:5])
    return co2, acc2, m2


def mesh(rng, n, scale):
    co = (rng.normal(0, scale, n * 3)).astype(np.float32)
    start = (co + rng.normal(0, scale * 0.05, co.size)).astype(np.float32)
    target = (co + rng.normal(0, scale * 0.2, co.size)).astype(np.float32)
    return co, start, target


def test_random_strokes():
    rng = np.random.default_rng(83)
    total = 0
    for k in range(160):
        n = int(rng.integers(0, 4000))
        scale = float(rng.choice([1e-3, 1.0, 40.0]))
        co, start, target = mesh(rng, n, scale)
        if n and k % 9 == 0:
            co[rng.integers(0, co.size, 6)] = rng.choice([np.nan, np.inf, -np.inf, -0.0], 6).astype(np.float32)
        acc = np.zeros(n, np.float64)
        mask = None
        if k % 3 == 1:
            mask = rng.uniform(-0.5, 1.5, n).astype(np.float32)
            if n and k % 2:
                mask[rng.integers(0, n, 3)] = np.nan
        elif k % 3 == 2:
            mask = rng.uniform(0, 1, n).astype(np.float32)
        hidden = (rng.random(n) < 0.25) if k % 4 == 1 else None
        for dab in range(6):                          # dab after dab on the same arrays, as in a stroke
            if n:
                c = co.reshape(-1, 3)[int(rng.integers(0, n))].astype(np.float64)
                if not np.isfinite(c).all():
                    c = rng.normal(0, scale, 3)
            else:
                c = rng.normal(0, scale, 3)
            r = float(scale * rng.choice([0.0, 1e-6, 0.05, 0.4, 2.0, 1e6]))
            s = float(rng.choice([1e-3, 0.3, 1.0, 1.7]))
            co, acc, m = run_both(co, start, target, acc, mask, hidden, c, r, s, f"stroke {k} dab {dab}")
            total += m
    assert total > 20000, total
    print(f"PASS test_random_strokes (160 strokes of 6 dabs, {total} vertices moved)")


def _near(v, k=6):
    out = [np.float32(v)]
    up = dn = np.float32(v)
    for _ in range(k):
        up = np.nextafter(up, np.float32(np.inf))
        dn = np.nextafter(dn, np.float32(-np.inf))
        out += [up, dn]
    return out


def test_at_the_edges():
    """Vertices one float32 step either side of the box on each axis, and of the sphere along random directions."""
    rng = np.random.default_rng(84)
    for c, r in ((np.array([0.1, -0.7, 3.3]), 0.25), (np.array([1234.5, 0.0, -2.0]), 0.001),
                 (np.array([0.0, 0.0, 0.0]), 1.0), (np.array([-3e5, 7.0, 1e-3]), 17.0)):
        pts = []
        for ax in range(3):
            for sign in (-1.0, 1.0):
                for v in _near(c[ax] + sign * r):
                    p = c.astype(np.float32)
                    p[ax] = v
                    pts.append(p)
        dirs = rng.normal(0, 1, (300, 3))
        dirs /= np.linalg.norm(dirs, axis=1)[:, None]
        for d in dirs:
            base = c + d * r
            p = base.astype(np.float32)
            for ax in range(3):
                for v in _near(base[ax], 2):
                    q = p.copy()
                    q[ax] = v
                    pts.append(q)
        co = np.array(pts, np.float32).reshape(-1)
        n = len(pts)
        start = (co * np.float32(0.999)).astype(np.float32)
        target = (co * np.float32(1.01) + np.float32(0.003)).astype(np.float32)
        for mask in (None, rng.uniform(0, 1, n).astype(np.float32)):
            for hidden in (None, rng.random(n) < 0.3):
                for s in (0.4, 1.0):
                    _co, _acc, m = run_both(co.copy(), start, target, np.zeros(n), mask, hidden, c, r, s, f"edges {c} {r}")
    print("PASS test_at_the_edges")


def test_the_same_as_before_r081():
    """The engine against the box test from before R081 (all axes at once): the same."""
    rng = np.random.default_rng(85)
    for k in range(40):
        n = int(rng.integers(1, 3000))
        co, start, target = mesh(rng, n, 1.0)
        acc = np.zeros(n)
        hidden = (rng.random(n) < 0.2) if k % 2 else None
        for dab in range(4):
            c = co.reshape(-1, 3)[int(rng.integers(0, n))].astype(np.float64)
            r = float(rng.uniform(0.05, 1.0))
            co1, acc1 = co.copy(), acc.copy()
            m1 = R.morph_dab(co1, start, target, acc1, None, hidden, c[0], c[1], c[2], r, 0.8, box=R.old_in_box)
            m2 = ENGINE.morph_dab(co, start, target, acc, None, hidden, c[0], c[1], c[2], r, 0.8)
            assert m1 == m2 and same_bits(co1, co) and same_bits(acc1, acc), (k, dab)
    print("PASS test_the_same_as_before_r081")


def test_refusals():
    n = 10
    co = np.zeros(n * 3, np.float32)
    ok = dict(start=np.zeros(n * 3, np.float32), target=np.zeros(n * 3, np.float32), acc=np.zeros(n))

    def bad(**kw):
        a = dict(ok, **kw)
        try:
            ENGINE.morph_dab(kw.get("co", co), a["start"], a["target"], a["acc"], kw.get("mask"), kw.get("hidden"),
                             0.0, 0.0, 0.0, 1.0, 1.0)
        except ValueError:
            return True
        return False
    assert bad(start=np.zeros(n * 3 - 3, np.float32))
    assert bad(target=np.zeros(n * 3, np.float64))
    assert bad(acc=np.zeros(n, np.float32))
    assert bad(acc=np.zeros(n + 1))
    assert bad(mask=np.zeros(n + 1, np.float32))
    assert bad(hidden=np.zeros(n + 1, bool))
    assert bad(co=np.zeros(n * 3 + 1, np.float32))
    assert bad(start=np.zeros(n * 3 // 2, np.float64))                       # the same bytes, another type
    assert bad(co=np.zeros(n * 3, np.int32))
    assert bad(acc=np.zeros(n, np.int64))
    assert bad(mask=np.zeros(n, np.int32))
    assert bad(hidden=np.zeros(n, np.uint8))
    shared = np.zeros(n * 3 + n * 2, np.float32)
    assert bad(co=shared[:n * 3], acc=shared[n:].view(np.float64)[:n])         # co and acc share memory
    try:
        ENGINE.morph_dab(bytes(n * 12), ok["start"], ok["target"], ok["acc"], None, None, 0.0, 0.0, 0.0, 1.0, 1.0)
        raise AssertionError("a read-only co was taken")
    except (BufferError, TypeError, ValueError):
        pass
    assert ENGINE.morph_dab(co, ok["start"], ok["target"], ok["acc"], None, None, 0.0, 0.0, 0.0, 1.0, 1.0) == 0
    e = np.zeros(0, np.float32)
    assert ENGINE.morph_dab(e, e, e, np.zeros(0), None, None, 0.0, 0.0, 0.0, 1.0, 1.0) == 0
    print("PASS test_refusals")


if __name__ == "__main__":
    import traceback
    failed = []
    for t in (test_random_strokes, test_at_the_edges, test_the_same_as_before_r081, test_refusals):
        try:
            t()
        except Exception:
            failed.append(t.__name__)
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"{4 - len(failed)}/4 passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        sys.exit(1)
