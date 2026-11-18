"""The native engine (hindsight_native, Rust) against the Python code, on the hot paths of a stroke.

Run: <python with numpy, zstandard and hindsight_native> bench/bench_native.py
Writes bench/results_native.json (one entry per Python version). At 100k and 1M vertices, with a sculpt-like stroke over about 2%
of the vertices of a grid mesh (a brush disc: runs of vertices along each row, smooth falloff):
diff of the position channel, applying one step, packing and unpacking one step (the record alone
and the whole step with zstd), and a jump over 300 packed steps. Python path = native.lib set to
None in the same process. Times: median of repeated runs (ms). Memory: the peak of the operation
over what was held before it, as tracemalloc (Python allocations, numpy's included) and VmHWM
(the process peak, reset through /proc/self/clear_refs before each run; native allocations show
only here).
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import json
import platform
import statistics
import time
import tracemalloc

import numpy as np

from hindsight.core import codec, delta, native, step as stepmod
from hindsight.core.history import History, MeshState
from hindsight.core.types import DELTA, POSITION_KEY, Entry, Step

LIB = native.lib
assert LIB is not None, f"hindsight_native not loaded: {native.ERROR}"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_native.json")
rng = np.random.default_rng(11)


def proc(field):
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith(field + ":"):
                return int(line.split()[1]) * 1024
    return 0


def reset_hwm():
    try:
        with open("/proc/self/clear_refs", "w") as fh:
            fh.write("5")
        return True
    except OSError:
        return False


def measure(fn, reps):
    """-> (median ms, python peak bytes, process peak bytes over RSS before)."""
    fn()                                   # warm up (and first-touch of reused buffers)
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    tracemalloc.start()
    tracemalloc.reset_peak()
    fn()
    py_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    hwm = None
    if reset_hwm():
        rss0 = proc("VmRSS")
        fn()
        hwm = max(0, proc("VmHWM") - rss0)
    return statistics.median(ts), py_peak, hwm


def grid_stroke(nv, pos, frac=0.02):
    """A new position array: a brush disc over about `frac` of a square grid's vertices."""
    side = int(round(nv ** 0.5))
    r = (frac * nv / np.pi) ** 0.5
    cx, cy = rng.uniform(r, side - r, 2)
    y0, y1 = int(cy - r), int(cy + r) + 1
    new = pos.copy()
    p = new.reshape(-1, 3)
    for y in range(max(0, y0), min(side, y1)):
        half = (r * r - (y - cy) ** 2)
        if half <= 0:
            continue
        half = half ** 0.5
        x0, x1 = max(0, int(cx - half)), min(side, int(cx + half) + 1)
        xs = np.arange(x0, x1)
        dist = np.hypot(xs - cx, y - cy) / r
        fall = np.clip(1 - dist, 0, 1) ** 2
        p[y * side + x0:y * side + x1, 2] += (fall * 0.01).astype(np.float32)
    return new


def case(nv):
    side = int(round(nv ** 0.5))
    nv = side * side
    yy, xx = np.divmod(np.arange(nv), side)
    pos = np.stack([xx / side, yy / side, np.zeros(nv)], 1).astype(np.float32).reshape(-1)
    new = grid_stroke(nv, pos)
    res = {"vertices": nv}
    reps = 30 if nv <= 200_000 else 10

    def both(name, fn, reps=reps):
        n = measure(fn, reps)
        native.lib = None
        try:
            p = measure(fn, reps)
        finally:
            native.lib = LIB
        res[name] = {"python_ms": round(p[0], 3), "native_ms": round(n[0], 3),
                     "python_peak_py_bytes": p[1], "native_peak_py_bytes": n[1],
                     "python_peak_rss_bytes": p[2], "native_peak_rss_bytes": n[2]}
        print(f"  {nv:>9,} {name:22s} python {p[0]:8.3f} ms  native {n[0]:8.3f} ms   "
              f"peak py {p[1] / 1e6:7.2f} / {n[1] / 1e6:7.2f} MB   rss {(p[2] or 0) / 1e6:7.2f} / "
              f"{(n[2] or 0) / 1e6:7.2f} MB", flush=True)

    d = delta.diff(POSITION_KEY, pos, new)
    res["changed"] = int(d.idx.size)
    both("diff", lambda: delta.diff(POSITION_KEY, pos, new))
    arr = pos.copy()
    state = {"fwd": True}

    def apply_one():
        delta.apply(arr, d, state["fwd"])
        state["fwd"] = not state["fwd"]
    both("apply_step", apply_one)
    rec = delta.pack_delta(d)
    both("pack_record", lambda: delta.pack_delta(d))
    both("unpack_record", lambda: delta.unpack_delta(rec))
    st = Step(1, "Draw", "SCULPT", "obj", 1.0, DELTA, [d])
    blob = stepmod.step_to_bytes(st, "zstd")
    res["step_bytes"] = len(blob)
    both("pack_step_zstd", lambda: stepmod.step_to_bytes(st, "zstd"))
    both("unpack_step_zstd", lambda: stepmod.step_from_bytes(blob))

    # 300 packed strokes, then a jump from the newest to the oldest and back
    h = History("obj")
    cur = pos.copy()
    for i in range(300):
        nxt = grid_stroke(nv, cur)
        s = Step(i + 1, "Draw", "SCULPT", "obj", float(i), DELTA, [delta.diff(POSITION_KEY, cur, nxt)])
        h.entries.append(Entry(blob=stepmod.step_to_bytes(s, "zstd"), meta=s.meta()))
        cur = nxt
    h.cursor = 300
    states = {"obj": MeshState({"POINT": nv}, {POSITION_KEY: cur.copy()})}

    def jump():
        h.goto(0 if h.cursor == 300 else 300, states)
    both("jump_300_steps", jump, reps=6 if nv <= 200_000 else 4)
    assert h.cursor in (0, 300)
    end = states["obj"].arrays[POSITION_KEY]
    assert end.tobytes() == (pos if h.cursor == 0 else cur).tobytes()
    return res


def main():
    out = {"date": time.strftime("%Y-%m-%d"), "machine": platform.platform(), "cpu": platform.processor(),
           "cpus": os.cpu_count(), "python": sys.version.split()[0], "numpy": np.__version__,
           "codecs": codec.available(), "native": LIB.__file__, "load": os.getloadavg(), "cases": []}
    try:
        with open("/proc/cpuinfo") as fh:
            out["cpu"] = next(l.split(":", 1)[1].strip() for l in fh if l.startswith("model name"))
    except (OSError, StopIteration):
        pass
    print(out["machine"], out["cpu"], "Python", out["python"], "numpy", out["numpy"])
    for nv in (100_000, 1_000_000):
        out["cases"].append(case(nv))
    out["load_after"] = os.getloadavg()
    try:                                   # one entry per Python version (3.11 and 3.13 runs side by side)
        with open(OUT) as fh:
            runs = json.load(fh).get("runs", {})
    except (OSError, ValueError, AttributeError):
        runs = {}
    runs[out["python"].rsplit(".", 1)[0]] = out
    with open(OUT, "w") as fh:
        json.dump({"runs": runs}, fh, indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
