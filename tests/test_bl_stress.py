import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Stress test: 10,000 fake strokes on a ~100k-vertex mesh in Sculpt Mode, bit-exact long jumps,
and eviction of the oldest step at step 10,001."""
import time

import bpy
import numpy as np
import tests.harness as H
from hindsight.bl import runtime
from hindsight.core.types import SETTINGS

C = bpy.context
STEPS = 10_000
SNAP_EVERY = 1000
STROKE_VERTS = 300


def _read_pos(me):
    a = me.attributes["position"].data
    co = np.empty(len(a) * 3, np.float32)
    a.foreach_get("vector", co)
    return co


def _write_pos(me, co):
    me.attributes["position"].data.foreach_set("vector", co)
    me.update()


def _stroke(co, rng, nv):
    """~STROKE_VERTS contiguous-ish vertices (a window of 2x that size, every other vertex roughly),
    pushed along their direction from the origin with a smooth falloff plus a little noise."""
    win = STROKE_VERTS * 2
    s = int(rng.integers(0, nv - win))
    idx = s + np.sort(rng.choice(win, STROKE_VERTS, replace=False))
    p = co.reshape(-1, 3)
    fall = (np.sin(np.linspace(0, np.pi, STROKE_VERTS, dtype=np.float32)) ** 2)
    scale = 1.0 + 2e-3 * fall * (1.0 + 0.1 * rng.standard_normal(STROKE_VERTS).astype(np.float32))
    p[idx] *= scale[:, None].astype(np.float32)


def test_stress_10k_steps():
    H.fresh()
    SETTINGS.max_steps = 10000
    SETTINGS.memory_limit_mb = 4096
    SETTINGS.truncation_threshold = 10
    ob = H.make_mesh(100_000)
    me = ob.data
    H.enter(ob, "SCULPT")
    nv = len(me.vertices)
    print(f"mesh: {nv} verts, {len(me.polygons)} faces; {STEPS} strokes of {STROKE_VERTS} verts")
    rng = np.random.default_rng(1234)
    co = _read_pos(me)
    truth = {0: co.tobytes()}
    t0 = time.perf_counter()
    t_commit = 0.0
    for i in range(1, STEPS + 1):
        _stroke(co, rng, nv)
        _write_pos(me, co)
        tc = time.perf_counter()
        st = runtime.commit(ob)
        t_commit += time.perf_counter() - tc
        assert st is not None, f"stroke {i} not recorded"
        if i == 1 or i % SNAP_EVERY == 0:
            truth[i] = co.tobytes()
        if i % 2000 == 0:
            print(f"  {i} strokes, {time.perf_counter() - t0:.1f}s (commit total {t_commit:.1f}s)")
            sys.stdout.flush()
    t_record = time.perf_counter() - t0
    h = runtime.history_of(ob)
    assert len(h.entries) == STEPS, len(h.entries)
    assert h.cursor == STEPS
    if runtime.packer is not None:
        runtime.packer.drain(60)
    mem = runtime.memory_stats()

    jumps = [0, 10000, 5000, 4999, 1000, 9000, 0, 10000]
    jt = []
    for pos in jumps:
        tj = time.perf_counter()
        runtime.goto(C, pos)
        jt.append((time.perf_counter() - tj) * 1000)
        assert h.cursor == pos, (h.cursor, pos)
        if pos in truth:
            want = truth[pos]
        else:
            # 4999 is not a stored snapshot: derive it from 5000 by stepping the recorded stroke back is
            # what we test, so compare against an independent replay of the same RNG instead.
            want = truth_4999[0]
        assert _read_pos(me).tobytes() == want, f"positions differ after goto {pos}"
    # ---- step 10,001 evicts the oldest step
    _stroke(co, rng, nv)
    _write_pos(me, co)
    assert runtime.commit(ob) is not None
    assert len(h.entries) == STEPS, f"len after step 10001 = {len(h.entries)}"
    assert h.cursor == STEPS
    runtime.goto(C, 0)
    assert _read_pos(me).tobytes() == truth[1], "after eviction position 0 must be the state after stroke 1"
    runtime.goto(C, STEPS)
    assert _read_pos(me).tobytes() == co.tobytes(), "newest state after eviction"
    total = time.perf_counter() - t0
    print(f"record {STEPS} strokes: {t_record:.1f}s (commit {t_commit:.1f}s, "
          f"{t_commit / STEPS * 1000:.2f} ms/commit)")
    print("jumps ms:", ", ".join(f"{p}:{t:.0f}" for p, t in zip(jumps, jt)))
    print(f"total runtime {total:.1f}s")
    print("memory_stats:", mem, f"({mem['total_bytes'] / mem['steps']:.0f} B/step,"
          f" {mem['total_bytes'] / 2**20:.1f} MiB history, {mem['ram_bytes'] / 2**20:.1f} MiB incl. baseline)")


truth_4999 = [None]


def _precompute_4999():
    """Replay the stroke RNG on plain numpy to get the exact positions after stroke 4999."""
    ob = H.make_mesh(100_000, name="Ref")
    co = _read_pos(ob.data)
    nv = co.size // 3
    bpy.data.objects.remove(ob, do_unlink=True)
    rng = np.random.default_rng(1234)
    for _ in range(4999):
        _stroke(co, rng, nv)
    truth_4999[0] = co.tobytes()


def test_stress():
    H.fresh()
    _precompute_4999()
    test_stress_10k_steps()


if __name__ == "__main__":
    H.run(test_stress)
    H.finish()
