import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R072: the strong zstd level of idle recompression is 15 for data up to 1 MB (strokes) and stays 19 above
(topology steps, compressed with a window as large as the data).

  rule       a strong blob of data up to 1 MB is the level-15 blob, above 1 MB the level-19 blob (the engine's
             measurement switch `_strong_level` writes each level on its own to compare)
  old blobs  steps made strong at level 19 by earlier builds read back the same, are known as strong, and
             recompress leaves them alone (nothing is redone at the new level)
  steps      a recompressed stroke step and topology step hold the same step as before
  switch     `_strong_level` takes 0 (the rule) or 1 to 22 and nothing else"""
import traceback

import numpy as np

from hindsight.core import codec, delta, step as stepmod
from hindsight.core.native import ENGINE
from hindsight.core.types import DELTA, EDGE_VERTS_KEY, CORNER_EDGE_KEY, CORNER_VERT_KEY, FACE_OFFSETS_KEY, \
    POSITION_KEY, TOPO, Snapshot, Step

RNG = np.random.default_rng(72)


def at(level, fn):
    ENGINE._strong_level(level)
    try:
        return fn()
    finally:
        ENGINE._strong_level(0)


def data(n):
    """Compressible bytes like a step body: float32 values on a smooth curve with a little noise."""
    x = np.sin(np.linspace(0, 60, n // 4, dtype=np.float32)) + RNG.normal(0, 1e-3, n // 4).astype(np.float32)
    return x.tobytes() + b"\0" * (n % 4)


def test_level_by_size():
    assert ENGINE._strong_level() == 0, "the add-on runs with the rule"
    for n in (100, 64 << 10, (1 << 20) - 1, 1 << 20, (1 << 20) + 1, 3 << 20):
        d = data(n)
        got = codec.compress(d, "zstd", True)
        want = at(19 if n > (1 << 20) else 15, lambda: codec.compress(d, "zstd", True))
        assert got == want, n
        if n >= 64 << 10:
            other = at(15 if n > (1 << 20) else 19, lambda: codec.compress(d, "zstd", True))
            assert other != got, ("the other level would write other bytes", n)
        assert got[0] == codec.ZSTD_MAX and codec.is_strong(got) and codec.decompress(got, len(d)) == d
    print("PASS test_level_by_size")


def _strokes(n_steps=20, nv=100_000):
    pos = np.sin(np.arange(nv * 3, dtype=np.float32) * np.float32(0.0007)) * np.float32(2.0)   # smooth, like a mesh
    out = []
    for i in range(n_steps):
        new = pos.copy()
        s = int(RNG.integers(0, nv - 2000))
        new[3 * s:3 * (s + 2000)] *= np.float32(1.001)
        out.append(Step(seq=i + 2, label="Draw", mode="SCULPT", target="o", time=1.7e9 + i, kind=DELTA,
                        deltas=[delta.diff(POSITION_KEY, pos, new)]))
        pos = new
    return out


def _topo():
    nv, nf = 100_000, 100_000
    grid = np.arange(nf * 4, dtype=np.int32) % nv
    a = {POSITION_KEY: np.sin(np.arange(nv * 3, dtype=np.float32) * 0.001), EDGE_VERTS_KEY: grid[:nf * 2].copy(),
         CORNER_VERT_KEY: grid.copy(), CORNER_EDGE_KEY: (grid // 2).astype(np.int32),
         FACE_OFFSETS_KEY: (np.arange(nf + 1) * 4).astype(np.int32)}
    b = {k: v.copy() for k, v in a.items()}
    b[POSITION_KEY][:3000] += np.float32(0.5)

    def counts(x):
        return {"POINT": x[POSITION_KEY].size // 3, "EDGE": x[EDGE_VERTS_KEY].size // 2,
                "FACE": x[FACE_OFFSETS_KEY].size - 1, "CORNER": x[CORNER_VERT_KEY].size}
    return Step(seq=50, label="Subdivide", mode="EDIT", target="o", time=1.7e9, kind=TOPO,
                before=Snapshot(counts(a), a), after=Snapshot(counts(b), b))


def test_old_strong_blobs_stay_as_they_are():
    steps = _strokes() + [_topo()]
    n_small = 0
    for st in steps:
        normal = stepmod.step_to_bytes(st, "zstd")
        old = at(19, lambda: stepmod.recompress(normal))           # what builds before R072 wrote
        new = stepmod.recompress(normal)
        assert old is not None and new is not None
        for b in (old, new):
            assert stepmod.same_step(stepmod.step_from_bytes(b), st), st.kind
            assert stepmod.recompress(b) is None, "a strong step is never recompressed again"
            assert stepmod.read_header(b) == stepmod.read_header(normal)
        if st.kind == TOPO:
            assert new == old, "data over 1 MB keeps level 19"
        else:
            n_small += new != old
            assert len(new) <= len(old) * 1.05, (len(new), len(old))
    assert n_small > 10, n_small
    print(f"PASS test_old_strong_blobs_stay_as_they_are ({len(steps)} steps, {n_small} strokes written at level 15)")


def test_switch():
    assert ENGINE._strong_level() == 0
    for bad in (-1, 23, 100):
        try:
            ENGINE._strong_level(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert ENGINE._strong_level() == 0
    assert at(12, ENGINE._strong_level) == 12 and ENGINE._strong_level() == 0
    print("PASS test_switch")


def main():
    tests = [test_level_by_size, test_old_strong_blobs_stay_as_they_are, test_switch]
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
