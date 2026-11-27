"""The native engine (hindsight_native, Rust) against the Python code, on the hot paths of a stroke.

Run: <python with numpy, zstandard and hindsight_native> bench/bench_native.py
Writes bench/results_native.json (one entry per Python version). At 100k and 1M vertices, with a sculpt-like stroke over about 2%
of the vertices of a grid mesh (a brush disc: runs of vertices along each row, smooth falloff):
diff of the position channel, applying one step, packing and unpacking one step (the record alone
and the whole step with zstd), and a jump over 300 packed steps. Python path = the numpy code of tests/reference
in the same process. Times: median of repeated runs (ms). Memory: the peak of the operation
over what was held before it, as tracemalloc (Python allocations, numpy's included) and VmHWM
(the process peak, reset through /proc/self/clear_refs before each run; native allocations show
only here).

    ... bench/bench_native.py --jumps [--nv 100000] [--rounds 5] [--fixtures DIR]

History.goto over a 5,000-step history of packed zstd strokes (the newest 64 kept raw, as
SETTINGS.raw_steps does): jumps of 1, 30, 300 and 3,000 steps both ways, and a drag over the whole
history (100 slider events of 50 steps, each way). Three engines in the same process, in
alternating rounds: "steps" (apply_steps, a run of steps in one native call), "per step" (the
module's API 1 functions only, which is how goto ran before apply_steps) and "python" (no module).
Every engine must end on the same bytes. The history is built once and kept in --fixtures (default:
a folder in the system temp directory). Prints min and median ms per case, and the peak memory of
a 300 and a 3,000-step jump (tracemalloc, and VmHWM over VmRSS).

    ... bench/bench_native.py --container [--nv 100000] [--rounds 7]

The step container and codecs (API 3): one stroke step of about 2% of the vertices, written, read
and recompressed with the engine against the Python reference (tests/reference: the container and
codecs as they were before the Rust migration, python-zstandard, delta transforms through the
engine as they were). Alternating rounds in one process; min and median ms, then the peak memory
of one write and one read (tracemalloc, and VmHWM over VmRSS). Needs the zstandard and lz4 Python
packages for the reference side.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
import json
import platform
import statistics
import time
import tracemalloc

import numpy as np

from hindsight.core import codec, delta, native, step as stepmod
from hindsight.core.history import History, MeshState
from reference.history import History as RefHistory
from reference import pure
from hindsight.core.types import DELTA, POSITION_KEY, Entry, Step

LIB = native.ENGINE
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
        with pure():                      # tests/reference/delta.py: the numpy code the engine replaced
            p = measure(fn, reps)
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


# ------------------------------------------------------------------------------------- jumps
JUMP_STEPS = 5000
JUMP_CASES = [("1 fwd", 2500, 2501), ("1 back", 2501, 2500), ("30 fwd", 2500, 2530), ("30 back", 2530, 2500),
              ("300 fwd", 2000, 2300), ("300 back", 2300, 2000), ("3000 fwd", 1000, 4000),
              ("3000 back", 4000, 1000), ("30 back, raw steps", 5000, 4970)]


class _PerStep:
    """The module as API 1 had it: no apply_steps, so goto crosses one step at a time."""
    def __init__(self, m):
        for name in ("diff", "apply", "pack", "unpack"):
            setattr(self, name, getattr(m, name))


def _jump_fixture(nv, folder):
    path = os.path.join(folder, f"jumps_{nv}.npz")
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        side = int(round(nv ** 0.5))
        yy, xx = np.divmod(np.arange(side * side), side)
        pos = np.stack([xx / side, yy / side, np.zeros(side * side)], 1).astype(np.float32).reshape(-1)
        cur, blobs = pos, []
        for i in range(JUMP_STEPS):
            nxt = grid_stroke(side * side, cur)
            s = Step(i + 1, "Draw", "SCULPT", "obj", float(i), DELTA, [delta.diff(POSITION_KEY, cur, nxt)])
            blobs.append(stepmod.step_to_bytes(s, "zstd"))
            cur = nxt
        np.savez(path, start=pos, end=cur, lens=np.array([len(b) for b in blobs]),
                 data=np.frombuffer(b"".join(blobs), np.uint8))
    z = np.load(path)
    data = z["data"].tobytes()
    offs = np.concatenate(([0], np.cumsum(z["lens"])))
    h = History("obj")
    for i in range(JUMP_STEPS):
        b = data[offs[i]:offs[i + 1]]
        if i >= JUMP_STEPS - 64:
            h.entries.append(Entry(stepmod.step_from_bytes(b)))
        else:
            h.entries.append(Entry(blob=b, meta=stepmod.read_header(b)["meta"]))
    h.cursor = JUMP_STEPS
    return h, z["start"], z["end"]


def jumps(nv, rounds, folder):
    import hashlib
    h, start, end = _jump_fixture(nv, folder)
    engines = {"steps": History, "python": RefHistory}       # engine apply_steps / tests/reference/history.py
    states = {"obj": MeshState({"POINT": end.size // 3}, {POSITION_KEY: end.copy()})}
    arr = lambda: states["obj"].arrays[POSITION_KEY]     # noqa: E731
    times = {e: {} for e in engines}
    sums = {}
    for _ in range(rounds):
        for name, cls in engines.items():
            g = lambda p, cls=cls: cls.goto(h, p, states)           # noqa: E731
            if True:
                for case, a, b in JUMP_CASES:
                    reps = 20 if abs(b - a) == 1 else (1 if abs(b - a) >= 3000 else 3)
                    for _ in range(reps):
                        g(a)
                        t0 = time.perf_counter()
                        g(b)
                        times[name].setdefault(case, []).append((time.perf_counter() - t0) * 1000)
                    sums.setdefault(case, set()).add(hashlib.sha1(arr().tobytes()).hexdigest())
                for way, lo, hi, d in (("drag fwd", 0, JUMP_STEPS, 50), ("drag back", JUMP_STEPS, 0, -50)):
                    g(lo)
                    t0 = time.perf_counter()
                    for p in range(lo + d, hi + d, d):
                        g(p)
                    times[name].setdefault(way, []).append((time.perf_counter() - t0) * 1000)
                assert arr().tobytes() == start.tobytes()
                g(JUMP_STEPS)
                assert arr().tobytes() == end.tobytes()
    assert all(len(v) == 1 for v in sums.values()), "engines disagree"
    res = {"vertices": nv, "rounds": rounds, "load": os.getloadavg(), "ms": {}}
    print(f"\n{nv:,} vertices, {rounds} rounds (min / median ms)")
    print(f"  {'case':20s}" + "".join(f"{e:>22s}" for e in engines))
    for case in list(times["steps"]):
        cells = "".join(f"{min(times[e][case]):11.2f} /{statistics.median(times[e][case]):9.2f}" for e in engines)
        print(f"  {case:20s}{cells}", flush=True)
        res["ms"][case] = {e: [round(min(times[e][case]), 3), round(statistics.median(times[e][case]), 3)]
                           for e in engines}
    res["peak_MB"] = {}
    print("  peak memory of the jump, MB (tracemalloc / VmHWM over RSS)")
    for name, cls in engines.items():
        g = lambda p, cls=cls: cls.goto(h, p, states)                # noqa: E731
        if True:
            for case, a, b in (("300 fwd", 2000, 2300), ("3000 back", 4000, 1000)):
                g(a)
                hw = reset_hwm()
                rss0 = proc("VmRSS")
                tracemalloc.start()
                g(b)
                pk = tracemalloc.get_traced_memory()[1] / 1e6
                tracemalloc.stop()
                hwm = round((proc("VmHWM") - rss0) / 1e6, 3) if hw else None
                res["peak_MB"][f"{name}, {case}"] = [round(pk, 3), hwm]
                print(f"    {name:10s} {case:10s} {pk:8.3f} / {hwm}")
    h.goto(JUMP_STEPS, states)
    return res


def container(nv, rounds):
    """Step container and codecs: reference (Python) against the engine, alternating rounds."""
    from reference import step as rstep
    side = int(round(nv ** 0.5))
    nv = side * side
    yy, xx = np.divmod(np.arange(nv), side)
    pos = np.stack([xx / side, yy / side, np.zeros(nv)], 1).astype(np.float32).reshape(-1)
    d = delta.diff(POSITION_KEY, pos, grid_stroke(nv, pos))
    st = Step(1, "Draw", "SCULPT", "obj", 1.0, DELTA, [d])
    blob = stepmod.step_to_bytes(st, "zstd")
    assert rstep.step_to_bytes(st, "zstd") == blob, "bytes differ"
    quick = stepmod.step_to_bytes(st, "zstd", delta.QUICK)
    ops = {
        "write zstd": (lambda: rstep.step_to_bytes(st, "zstd"), lambda: stepmod.step_to_bytes(st, "zstd")),
        "write quick (journal)": (lambda: rstep.step_to_bytes(st, "zstd", delta.QUICK),
                                  lambda: stepmod.step_to_bytes(st, "zstd", delta.QUICK)),
        "read": (lambda: rstep.step_from_bytes(blob), lambda: stepmod.step_from_bytes(blob)),
        "read quick": (lambda: rstep.step_from_bytes(quick), lambda: stepmod.step_from_bytes(quick)),
        "recompress quick": (lambda: rstep.recompress(quick), lambda: stepmod.recompress(quick)),
    }
    res = {"vertices": nv, "changed": int(d.idx.size), "step_bytes": len(blob), "ops": {}}
    for name, (a, b) in ops.items():
        a(), b()
        ta, tb = [], []
        for _ in range(rounds):
            for fn, ts in ((a, ta), (b, tb)):
                t0 = time.perf_counter()
                fn()
                ts.append((time.perf_counter() - t0) * 1000)
        mem = []
        for fn in (a, b):
            tracemalloc.start()
            tracemalloc.reset_peak()
            fn()
            py = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            hwm = None
            if reset_hwm():
                rss0 = proc("VmRSS")
                fn()
                hwm = max(0, proc("VmHWM") - rss0)
            mem.append((py, hwm))
        res["ops"][name] = {"old_min_ms": round(min(ta), 3), "old_median_ms": round(statistics.median(ta), 3),
                            "new_min_ms": round(min(tb), 3), "new_median_ms": round(statistics.median(tb), 3),
                            "old_peak_py_bytes": mem[0][0], "new_peak_py_bytes": mem[1][0],
                            "old_peak_rss_bytes": mem[0][1], "new_peak_rss_bytes": mem[1][1]}
        r = res["ops"][name]
        print(f"  {nv:>9,} {name:24s} old {r['old_min_ms']:8.3f} / {r['old_median_ms']:8.3f} ms   "
              f"new {r['new_min_ms']:8.3f} / {r['new_median_ms']:8.3f} ms (min / median)   "
              f"peak py {mem[0][0] / 1e6:6.2f} -> {mem[1][0] / 1e6:6.2f} MB   "
              f"rss {(mem[0][1] or 0) / 1e6:6.2f} -> {(mem[1][1] or 0) / 1e6:6.2f} MB", flush=True)
    return res


def main():
    if "--container" in sys.argv:
        arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d     # noqa: E731
        out = [container(nv, int(arg("--rounds", 7)))
               for nv in ([int(arg("--nv", 0))] if "--nv" in sys.argv else (100_000, 1_000_000))]
        print(json.dumps({"python": sys.version.split()[0], "cpus": os.cpu_count(), "container": out}))
        return
    if "--jumps" in sys.argv:
        import tempfile
        arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d     # noqa: E731
        assert hasattr(LIB, "apply_steps")
        folder = arg("--fixtures", os.path.join(tempfile.gettempdir(), "hindsight_bench_jumps"))
        out = [jumps(nv, int(arg("--rounds", 5)), folder)
               for nv in ([int(arg("--nv", 0))] if "--nv" in sys.argv else (100_000, 1_000_000))]
        print(json.dumps({"python": sys.version.split()[0], "cpus": os.cpu_count(), "jumps": out}))
        return
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
