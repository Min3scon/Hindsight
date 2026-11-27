import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R085: the Movie Mode camera's extents are measured by the engine (`extents_into`): bit for bit the numpy
reference (tests/reference/framing.py `extents`, the product written out left to right) on random points of both
float types with NaN, infinite and overflowing rows, any 3x3 basis, a subset by index; equal to the numpy code
from before R085 (BLAS) to the last bits; the history walk equal to the old walk the same way; and its refusals."""
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from hindsight.core import framing as F  # noqa: E402
from hindsight.core.native import ENGINE  # noqa: E402
from reference import framing as R  # noqa: E402


def same(a, b):
    if a is None or b is None:
        return a is None and b is None
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    return a.shape == b.shape and a.view(np.uint64).tobytes() == b.view(np.uint64).tobytes()


def close(a, b, rel=1e-12):
    if a is None or b is None:
        return a is None and b is None
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    fin = np.isfinite(a) & np.isfinite(b)
    if not np.array_equal(np.isfinite(a), np.isfinite(b)) or not np.array_equal(a[~fin], b[~fin]):
        return False
    return bool(np.all(np.abs(a[fin] - b[fin]) <= rel * (1 + np.abs(b[fin]))))


def points(rng, n, dtype):
    p = rng.normal(0, float(rng.choice([1e-3, 1.0, 1e4])), (n, 3))
    if n and rng.random() < 0.3:
        p[rng.integers(0, n, 3)] = rng.choice([np.nan, np.inf, -np.inf, 1e308], (3, 3))
    return p.astype(dtype)


def basis(rng):
    k = rng.integers(0, 4)
    if k == 0:
        return F.view_basis(float(rng.uniform(-3, 3)), float(rng.uniform(-1.4, 1.4)))
    if k == 1:
        return np.eye(3)
    return rng.normal(0, float(rng.choice([1e-3, 1.0, 1e3])), (3, 3))


def test_extents_bit_for_bit():
    rng = np.random.default_rng(85)
    n_some = 0
    for k in range(400):
        dtype = np.float32 if k % 2 else np.float64
        p = points(rng, int(rng.integers(0, 3000)), dtype)
        b = basis(rng)
        tx, ty = float(rng.uniform(0, 2)), float(rng.uniform(0, 2))
        got = F.extents(p, b, tx, ty)
        assert same(got, R.extents(p, b, tx, ty)), k
        old = R.old_extents(p, b, tx, ty)
        if np.isfinite(p).all() and np.abs(p).max(initial=0) < 1e300:
            assert close(got, old), (k, got, old)
        n_some += got is not None
    assert n_some > 300, n_some
    # other input forms, as before
    assert same(F.extents([[1, 2, 3], [4, 5, 6]], np.eye(3), 0.5, 0.5), R.extents(np.array([[1., 2, 3], [4, 5, 6]]), np.eye(3), 0.5, 0.5))
    assert same(F.extents(np.arange(9, dtype=np.float16), np.eye(3), 1, 1), R.extents(np.arange(9, dtype=np.float64), np.eye(3), 1, 1))
    assert F.extents(np.zeros(0), np.eye(3), 1, 1) is None and F.extents(np.zeros(4), np.eye(3), 1, 1) is None
    assert F.extents(np.full((5, 3), np.nan), np.eye(3), 1, 1) is None
    print("PASS test_extents_bit_for_bit")


def test_multi_and_subsets():
    rng = np.random.default_rng(86)
    for k in range(120):
        p = points(rng, int(rng.integers(1, 2000)), np.float32 if k % 2 else np.float64)
        nb = int(rng.integers(1, 5))
        bs = np.stack([basis(rng) for _ in range(nb)])
        ts = rng.uniform(0, 2, (nb, 2))
        got = F.extents_multi(p, bs, ts)
        want = [R.extents(p, bs[i], *ts[i]) for i in range(nb)]
        if want[0] is None:
            assert got is None, k
            continue
        for i in range(nb):
            if want[i] is None:
                assert np.array_equal(got[i], np.array(F._START)), (k, i)
            else:
                assert same(got[i], want[i]), (k, i)
        # a subset by index, merged into earlier extents
        idx = np.unique(rng.integers(0, len(p), int(rng.integers(1, len(p) + 1)))).astype(np.uint32)
        out = np.tile(np.array(F._START), (nb, 1))
        ENGINE.extents_into(np.ascontiguousarray(p).reshape(-1), idx, bs, ts, out)
        for i in range(nb):
            w = R.extents(p[idx], bs[i], *ts[i])
            assert same(out[i], w) if w is not None else np.array_equal(out[i], np.array(F._START)), (k, i)
    print("PASS test_multi_and_subsets")


def test_history_walk_as_before():
    """The walk against the old one (moved vertices gathered, then numpy over them): equal to the last bits, on
    the histories test_core_framing builds (deltas, a topology step, mask deltas, data steps, another object's
    steps, half of them packed), from cursors at the start, inside and at the end."""
    import copy
    import test_core_framing as T
    checked = 0
    bases = np.stack([F.view_basis(0.3, 0.2), np.eye(3), F.view_basis(-2.0, -0.5)])
    tans = np.array([(0.4, 0.3), (0.0, 0.0), (1.2, 0.7)])
    for seed in range(1, 7):
        h, st0 = T.build_history(seed)
        n = len(h.entries)
        for c in sorted({0, n // 3, n // 2, (2 * n) // 3, n}):
            hc, sc = copy.deepcopy(h), copy.deepcopy(st0)
            hc.cursor = 0
            hc.goto(c, {T.A: sc})
            got = F.history_extents_multi(hc, sc, T.A, bases, tans)
            want = R.old_history_extents_multi(hc, sc, T.A, bases, tans)
            assert got is not None and close(got, want), (seed, c, got, want)
            checked += 1
    assert checked >= 24, checked
    print(f"PASS test_history_walk_as_before ({checked} walks)")


def test_refusals():
    p = np.zeros(30, np.float32)
    b, t, o = np.eye(3)[None].copy(), np.array([[1.0, 1.0]]), np.tile(np.array(F._START), (1, 1))

    def bad(*a):
        try:
            ENGINE.extents_into(*a)
        except ValueError:
            return True
        return False
    assert bad(np.zeros(31, np.float32), None, b, t, o)
    assert bad(np.zeros(30, np.int32), None, b, t, o)
    assert bad(p, np.array([10], np.uint32), b, t, o)                 # out of range
    assert bad(p, np.array([1], np.int64), b, t, o)                   # not uint32
    assert bad(p, None, np.eye(3).astype(np.float32)[None], t, o)
    assert bad(p, None, b, np.array([[1.0, 1.0, 1.0]]), o)
    assert bad(p, None, b, t, np.zeros((2, 6)))
    shared = np.zeros(6 + 30)
    assert bad(shared[3:33], None, b, t, shared[:6].reshape(1, 6))    # out shares memory with the points
    assert ENGINE.extents_into(p, np.zeros(0, np.uint32), b, t, o) == 0 and np.array_equal(o, np.tile(np.array(F._START), (1, 1)))
    print("PASS test_refusals")


if __name__ == "__main__":
    import traceback
    failed = []
    tests = (test_extents_bit_for_bit, test_multi_and_subsets, test_history_walk_as_before, test_refusals)
    for t in tests:
        try:
            t()
        except Exception:
            failed.append(t.__name__)
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"{len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        sys.exit(1)
