import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R071: the engine's diff splits large arrays over threads; the result is byte for byte the one-thread one.

  chunks      every item size (1, 2, 4, 8) and width, row counts around the split points, changes on,
              next to and across the boundaries between the threads' runs of rows, threads 1 to 4: the
              indices and values equal one thread's and the numpy reference's (tests/reference/delta.py)
  automatic   arrays over 4 MB (two threads by the engine's own rule): delta.diff equals one thread and
              the reference, for a stroke, a scatter, everything changed and nothing changed
  record      the engine's record (diff, packing and codec in one call) of channels over 4 MB gives the
              blob of the one-thread diff written by step_encode, both delta packings (normal and quick)
  threads     a large diff runs with the GIL released (another thread runs meanwhile)"""
import threading
import time
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))         # tests/: the reference copies (Blender's --python adds no path)
from reference import delta as rdelta                 # noqa: E402
from hindsight.core import delta, step as stepmod     # noqa: E402
from hindsight.core.native import ENGINE              # noqa: E402
from hindsight.core.types import ADD, DELTA, Step, make_key   # noqa: E402

RNG = np.random.default_rng(71)
DT = {1: np.uint8, 2: np.uint16, 4: np.uint32, 8: np.uint64}


def engine(a, b, isz, w, t):
    r = ENGINE.diff(a, b, isz, w, t)
    return None if r is None else (bytes(r[0]), bytes(r[1]))


def reference(a, b, w):
    ob, nb = a.reshape(-1, w), b.reshape(-1, w)
    idx = np.flatnonzero((ob != nb).any(axis=1)).astype(np.uint32)
    if idx.size == 0:
        return None
    return idx.tobytes(), (nb[idx] - ob[idx]).tobytes()


def test_chunk_boundaries():
    n_cases = 0
    for isz, dt in DT.items():
        for w in (1, 2, 3, 4, 16):
            for n in (0, 1, 2, 3, 4, 5, 7, 31, 32, 33, 63, 64, 65, 129, 1000, 4099):
                a = RNG.integers(0, np.iinfo(dt).max, n * w, dtype=dt, endpoint=True)
                plans = [[], [0], [n - 1], list(range(n))]
                for t in (2, 3, 4):
                    per = -(-n // t) if n else 0
                    for c in range(1, t):
                        e = c * per
                        plans.append([r for r in (e - 1, e, e + 1) if 0 <= r < n])        # on and next to a boundary
                        plans.append([r for r in range(e - 40, e + 40) if 0 <= r < n])    # a run across it
                plans.append(sorted(set(int(x) for x in RNG.integers(0, max(n, 1), min(n, 50)))) if n else [])
                for rows in ([r for r in p if 0 <= r < n] for p in plans):
                    b = a.copy().reshape(-1, w) if n else a.copy().reshape(0, w)
                    for r in rows:
                        col = int(RNG.integers(0, w))
                        b[r, col] ^= dt(1 + int(RNG.integers(0, 200)))       # some bits flip: the row changed
                    b = b.reshape(-1)
                    want = reference(a, b, w)
                    one = engine(a, b, isz, w, 1)
                    assert one == want, (isz, w, n, rows[:5])
                    for t in (None, 2, 3, 4):
                        assert engine(a, b, isz, w, t) == one, (isz, w, n, t, rows[:5])
                    n_cases += 1
    print(f"PASS test_chunk_boundaries ({n_cases} arrays, threads 1 to 4 and automatic)")


def _big(key, nbytes):
    """A channel of about nbytes as recorded (float32 rows of the key's width)."""
    w = delta.key_width(key)
    n = nbytes // (4 * w)
    return RNG.random(n * w, dtype=np.float32).reshape(-1)


def _changes(old, w, how):
    new = old.copy().reshape(-1, w)
    n = new.shape[0]
    if how == "stroke":                       # a run of rows, across the middle (where two threads split)
        s = n // 2 - n // 100
        new[s:s + n // 50] *= np.float32(1.001)
    elif how == "scatter":
        idx = RNG.integers(0, n, n // 20)
        new[idx, 0] += np.float32(0.5)
    elif how == "all":
        new += np.float32(1.0)
    elif how == "edges":
        new[0, 0] = -new[0, 0]
        new[n - 1, w - 1] = np.float32(-0.0)
        new[n // 2 - 1: n // 2 + 1] = np.float32(np.nan)
    return new.reshape(-1)


def test_automatic_threads_equal_one_thread():
    keys = [make_key("position", "POINT", "FLOAT_VECTOR"), make_key("Col", "POINT", "FLOAT_COLOR")]
    n = 0
    try:
        for key in keys:
            w = delta.key_width(key)
            for size in (4 << 20, (6 << 20) + 4 * w * 7, 13 << 20):
                old = _big(key, size)
                for how in ("stroke", "scatter", "all", "edges", "same"):
                    new = old if how == "same" else _changes(old, w, how)
                    delta.DIFF_THREADS[0] = 1
                    one = delta.diff(key, old, new)
                    delta.DIFF_THREADS[0] = None
                    auto = delta.diff(key, old, new)
                    ref = rdelta.diff(key, old, new)
                    if how == "same":
                        assert one is None and auto is None and ref is None
                    else:
                        for d in (auto, ref):
                            assert d.idx.tobytes() == one.idx.tobytes() and d.vals.tobytes() == one.vals.tobytes(), (key, size, how)
                            assert d.vals.dtype == one.vals.dtype and d.vals.shape == one.vals.shape
                    n += 1
    finally:
        delta.DIFF_THREADS[0] = None
    print(f"PASS test_automatic_threads_equal_one_thread ({n} arrays of 4 to 13 MB)")


def test_record_of_large_channels():
    """record() diffs inside the engine with the automatic rule: same blob as the one-thread diff of
    every channel written by step_to_bytes, for the normal and the quick packing."""
    kp = make_key("position", "POINT", "FLOAT_VECTOR")
    kc = make_key("Col", "POINT", "FLOAT_COLOR")
    pos = _big(kp, 12 << 20)
    col = _big(kc, 5 << 20)
    cases = 0
    for how in ("stroke", "scatter", "edges"):
        npos, ncol = _changes(pos, 3, how), _changes(col, 4, "stroke")
        chans = [(kp, pos, npos, True, True), (kc, col, ncol, True, True)]
        delta.DIFF_THREADS[0] = 1
        try:
            deltas = [delta.diff(kp, pos, npos), delta.diff(kc, col, ncol)]
        finally:
            delta.DIFF_THREADS[0] = None
        for eff in (delta.NORMAL, delta.QUICK):
            got = stepmod.record_step("DELTA", 9, 2.5, "Stroke", "SCULPT", "obj", chans, None, "zstd", eff, False)
            st = Step(9, "Stroke", "SCULPT", "obj", 2.5, DELTA, deltas)
            want = stepmod.step_to_bytes(st, "zstd", eff, False)
            assert got == want, (how, eff, len(got), len(want))
            cases += 1
    print(f"PASS test_record_of_large_channels ({cases} blobs, 12 MB and 5 MB channels)")


def test_large_diff_releases_the_gil():
    a = RNG.random((16 << 20) // 4, dtype=np.float32)
    b = a.copy()
    b[1000:200000] += 1
    want = engine(a.view(np.uint32), b.view(np.uint32), 4, 1, 1)
    ticks, stop = [0], threading.Event()

    def spin():                     # gives the GIL back at every turn: it runs only while the engine lets it go
        while not stop.is_set():
            ticks[0] += 1
            time.sleep(0)
    old = sys.getswitchinterval()
    sys.setswitchinterval(5.0)      # no forced hand-over of the GIL during the test
    t = threading.Thread(target=spin)
    t.start()
    try:
        got = [engine(a.view(np.uint32), b.view(np.uint32), 4, 1, None) for _ in range(20)]
        ran = ticks[0]
    finally:
        stop.set()
        sys.setswitchinterval(old)
        t.join()
    assert all(g == want for g in got)
    assert ran > 20, ran
    print(f"PASS test_large_diff_releases_the_gil (a second thread ran {ran} turns during 20 diffs of 16 MB)")


def main():
    tests = [test_chunk_boundaries, test_automatic_threads_equal_one_thread, test_record_of_large_channels,
             test_large_diff_releases_the_gil]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
