"""Benchmark of the Hindsight storage engine (core.delta / core.step / core.codec).

Run: /home/user/venvs/bpy42/bin/python bench/bench_core.py
Prints a markdown table and writes bench/results_core.json. All numbers measured (median of 5).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import json
import platform
import statistics
import time

import numpy as np

from hindsight.core import codec, delta, step as stepmod
from hindsight.core.types import DELTA, POSITION_KEY, Step

REPS = 5
RNG = np.random.default_rng(42)


def sphere(n):
    """~n vertices on a unit sphere, lat/long rows in row-major index order."""
    rows = int(np.sqrt(n / 2))
    cols = n // rows
    n = rows * cols
    th = np.repeat(np.linspace(0.01, np.pi - 0.01, rows), cols)
    ph = np.tile(np.linspace(0, 2 * np.pi, cols, endpoint=False), rows)
    p = np.stack([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)], 1).astype(np.float32)
    return p.reshape(-1)


def stroke(pos, frac=0.02, mag=1e-3):
    """Spatially contiguous index range (~frac of verts) pushed out radially by ~mag with falloff."""
    nv = pos.size // 3
    k = int(nv * frac)
    s = int(RNG.integers(0, nv - k))
    new = pos.copy()
    p = new.reshape(-1, 3)
    fall = np.sin(np.linspace(0, np.pi, k, dtype=np.float32)) ** 2
    p[s:s + k] *= (1.0 + mag * fall * (1.0 + 0.05 * RNG.standard_normal(k).astype(np.float32)))[:, None]
    return new


def med(fn, reps=REPS):
    ts = []
    out = None
    for _ in range(reps):
        t0 = time.perf_counter()
        out = fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(ts), out


def bench(n):
    base = sphere(n)
    nv = base.size // 3
    r = {"verts": nv}
    new = stroke(base)
    t, d = med(lambda: delta.diff(POSITION_KEY, base, new))
    r["changed_verts"] = int(d.idx.size)
    r["diff_ms"] = t
    st = Step(1, "Stroke", "SCULPT", "obj", 0.0, DELTA, [d])
    r["raw_bytes"] = int(d.idx.nbytes + d.xor.nbytes)
    r["raw_step_nbytes"] = st.nbytes()
    t, _ = med(lambda: delta.pack_delta(d))
    r["pack_delta_ms"] = t
    for name in [c for c in codec.available() if c != "none"]:
        t, blob = med(lambda: stepmod.step_to_bytes(st, name))
        r[f"{name}_bytes"] = len(blob)
        r[f"{name}_pack_ms"] = t
        buf = base.copy()

        def ua():
            s2 = stepmod.step_from_bytes(blob)
            for dd in s2.deltas:
                delta.apply(buf, dd)
        t, _ = med(ua)
        r[f"{name}_unpack_apply_ms"] = t
    buf = base.copy()
    t, _ = med(lambda: delta.apply(buf, d))
    r["apply_raw_ms"] = t
    # 100 sequential strokes, applied to one buffer
    cur = base.copy()
    deltas, blobs = [], []
    for i in range(100):
        nxt = stroke(cur)
        dd = delta.diff(POSITION_KEY, cur, nxt)
        deltas.append(dd)
        blobs.append(stepmod.step_to_bytes(Step(i, "Stroke", "SCULPT", "obj", 0.0, DELTA, [dd]), "auto"))
        cur = nxt
    final = cur

    def seq_raw():
        b = base.copy()
        for dd in deltas:
            delta.apply(b, dd)
        return b

    def seq_packed():
        b = base.copy()
        for bl in blobs:
            for dd in stepmod.step_from_bytes(bl).deltas:
                delta.apply(b, dd)
        return b

    def seq_undo():
        b = final.copy()
        for dd in reversed(deltas):
            delta.apply(b, dd)
        return b
    t, out = med(seq_raw)
    assert np.array_equal(out.view(np.uint32), final.view(np.uint32))
    r["apply100_raw_ms"] = t
    t, out = med(seq_packed)
    assert np.array_equal(out.view(np.uint32), final.view(np.uint32))
    r["apply100_packed_ms"] = t
    t, out = med(seq_undo)
    assert np.array_equal(out.view(np.uint32), base.view(np.uint32))
    r["undo100_raw_ms"] = t
    r["avg_packed_bytes_100"] = int(np.mean([len(b) for b in blobs]))
    r["full_positions_bytes"] = int(base.nbytes)
    return r


def main():
    res = {"python": platform.python_version(), "numpy": np.__version__, "codecs": codec.available(),
           "machine": platform.machine(), "reps": REPS, "results": [bench(n) for n in (100_000, 500_000)]}
    cods = [c for c in codec.available() if c != "none"]
    rows = [("verts", "verts", "{:,}"), ("changed verts", "changed_verts", "{:,}"),
            ("diff (ms)", "diff_ms", "{:.2f}"), ("pack_delta, uncompressed (ms)", "pack_delta_ms", "{:.3f}"),
            ("raw delta bytes (idx+xor)", "raw_bytes", "{:,}")]
    for c in cods:
        rows += [(f"{c}: packed bytes/step", f"{c}_bytes", "{:,}"), (f"{c}: pack time (ms)", f"{c}_pack_ms", "{:.3f}"),
                 (f"{c}: unpack+apply (ms)", f"{c}_unpack_apply_ms", "{:.3f}")]
    rows += [("apply raw delta (ms)", "apply_raw_ms", "{:.3f}"),
             ("100 steps apply, raw (ms)", "apply100_raw_ms", "{:.2f}"),
             ("100 steps undo, raw (ms)", "undo100_raw_ms", "{:.2f}"),
             ("100 steps unpack+apply, packed auto (ms)", "apply100_packed_ms", "{:.2f}"),
             ("mem/step raw (Step.nbytes)", "raw_step_nbytes", "{:,}"),
             ("mem/step packed (auto, avg of 100)", "avg_packed_bytes_100", "{:,}"),
             ("full positions array (bytes)", "full_positions_bytes", "{:,}")]
    R = res["results"]
    print(f"Python {res['python']}, numpy {res['numpy']}, codecs {res['codecs']}, median of {REPS}\n")
    print("| metric | " + " | ".join(f"{r['verts']:,} verts" for r in R) + " |")
    print("|---|" + "---|" * len(R))
    for label, k, fmt in rows:
        print(f"| {label} | " + " | ".join(fmt.format(r[k]) for r in R) + " |")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_core.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=1)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
