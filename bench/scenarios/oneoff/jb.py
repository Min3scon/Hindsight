"""Jump benchmark: python jb.py ROOT NV [--gen] [--reps R] [--mem] [--prof]

Fixture S/agents/NATIVE/fix_NV.npz: 5,000 packed zstd stroke steps (2% brush discs on a grid),
start and end position arrays. The newest 64 steps are kept raw (as SETTINGS.raw_steps does).
Prints one JSON line: per case min/median ms, plus sha of the array at each visited position.
"""
import hashlib, json, os, statistics, sys, time
ROOT = sys.argv[1]
NV = int(sys.argv[2])
sys.path.insert(0, ROOT)
import numpy as np
from hindsight.core import native, delta, step as stepmod
from hindsight.core.history import History, MeshState
from hindsight.core.types import DELTA, POSITION_KEY, Entry, Step

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, f"fix_{NV}.npz")
N = 5000
RAW = 64


def grid_stroke(rng, nv, pos, frac=0.02):
    side = int(round(nv ** 0.5))
    r = (frac * nv / np.pi) ** 0.5
    cx, cy = rng.uniform(r, side - r, 2)
    new = pos.copy()
    p = new.reshape(-1, 3)
    for y in range(max(0, int(cy - r)), min(side, int(cy + r) + 1)):
        half = (r * r - (y - cy) ** 2)
        if half <= 0:
            continue
        half = half ** 0.5
        x0, x1 = max(0, int(cx - half)), min(side, int(cx + half) + 1)
        xs = np.arange(x0, x1)
        fall = np.clip(1 - np.hypot(xs - cx, y - cy) / r, 0, 1) ** 2
        p[y * side + x0:y * side + x1, 2] += (fall * 0.01).astype(np.float32)
    return new


def gen():
    rng = np.random.default_rng(7)
    side = int(round(NV ** 0.5))
    nv = side * side
    yy, xx = np.divmod(np.arange(nv), side)
    pos = np.stack([xx / side, yy / side, np.zeros(nv)], 1).astype(np.float32).reshape(-1)
    cur = pos
    blobs = []
    for i in range(N):
        nxt = grid_stroke(rng, nv, cur)
        s = Step(i + 1, "Draw", "SCULPT", "obj", float(i), DELTA, [delta.diff(POSITION_KEY, cur, nxt)])
        blobs.append(stepmod.step_to_bytes(s, "zstd"))
        cur = nxt
    lens = np.array([len(b) for b in blobs], np.int64)
    np.savez(FIX, start=pos, end=cur, lens=lens, data=np.frombuffer(b"".join(blobs), np.uint8))
    print("wrote", FIX, nv, lens.sum() / 1e6, "MB")


def load():
    z = np.load(FIX)
    data = z["data"].tobytes()
    offs = np.concatenate(([0], np.cumsum(z["lens"])))
    h = History("obj")
    for i in range(N):
        b = data[offs[i]:offs[i + 1]]
        meta = stepmod.read_header(b)["meta"]
        if i >= N - RAW:
            h.entries.append(Entry(stepmod.step_from_bytes(b)))
        else:
            h.entries.append(Entry(blob=b, meta=meta))
    h.cursor = N
    states = {"obj": MeshState({"POINT": z["end"].size // 3}, {POSITION_KEY: z["end"].copy()})}
    return h, states, z["start"], z["end"]


def sha(states):
    return hashlib.sha1(states["obj"].arrays[POSITION_KEY].tobytes()).hexdigest()[:12]


# (name, start position, target position)
CASES = [
    ("j1_fwd", 2500, 2501), ("j1_back", 2501, 2500),
    ("j30_fwd", 2500, 2530), ("j30_back", 2530, 2500),
    ("j300_fwd", 2000, 2300), ("j300_back", 2300, 2000),
    ("j3000_fwd", 1000, 4000), ("j3000_back", 4000, 1000),
    ("j1_raw_back", 5000, 4999), ("j30_raw_back", 5000, 4970),
]


def main():
    if "--gen" in sys.argv:
        gen()
        return
    reps = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else 5
    h, states, start, end = load()
    out = {"native": native.lib is not None, "cases": {}, "sha": {}}
    big = NV >= 500_000
    for name, a, b in CASES:
        if big and name.startswith("j3000") and reps > 3:
            r = 3
        else:
            r = reps if abs(b - a) >= 30 else reps * 20
        ts = []
        for _ in range(r):
            h.goto(a, states)
            t0 = time.perf_counter()
            h.goto(b, states)
            ts.append((time.perf_counter() - t0) * 1000)
        out["cases"][name] = [round(min(ts), 3), round(statistics.median(ts), 3)]
        out["sha"][f"{b}"] = sha(states)
    # drag across the whole history: 100 slider events of 50 steps each way
    dr = []
    for _ in range(max(1, reps // 2)):
        h.goto(0, states)
        t0 = time.perf_counter()
        for p in range(50, N + 1, 50):
            h.goto(p, states)
        t1 = time.perf_counter()
        for p in range(N - 50, -1, -50):
            h.goto(p, states)
        t2 = time.perf_counter()
        dr.append(((t1 - t0) * 1000, (t2 - t1) * 1000))
    out["cases"]["drag_fwd"] = [round(min(d[0] for d in dr), 1), round(statistics.median(d[0] for d in dr), 1)]
    out["cases"]["drag_back"] = [round(min(d[1] for d in dr), 1), round(statistics.median(d[1] for d in dr), 1)]
    assert states["obj"].arrays[POSITION_KEY].tobytes() == start.tobytes()
    h.goto(N, states)
    assert states["obj"].arrays[POSITION_KEY].tobytes() == end.tobytes()
    if "--mem" in sys.argv:
        import tracemalloc
        mem = {}
        for name, a, b in [("j300_fwd", 2000, 2300), ("j3000_back", 4000, 1000), ("j3000_fwd", 1000, 4000)]:
            h.goto(a, states)
            with open("/proc/self/clear_refs", "w") as fh:
                fh.write("5")
            rss0 = _proc("VmRSS")
            tracemalloc.start()
            h.goto(b, states)
            pk = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            mem[name] = {"tracemalloc_MB": round(pk / 1e6, 3), "hwm_over_rss_MB": round((_proc("VmHWM") - rss0) / 1e6, 3)}
        out["mem"] = mem
    if "--prof" in sys.argv:
        import cProfile, pstats
        h.goto(2000, states)
        pr = cProfile.Profile()
        pr.enable()
        for _ in range(5):
            h.goto(2300, states)
            h.goto(2000, states)
        pr.disable()
        pstats.Stats(pr, stream=sys.stderr).sort_stats("tottime").print_stats(18)
    print(json.dumps(out))


def _proc(field):
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith(field + ":"):
                return int(line.split()[1]) * 1024
    return 0


if __name__ == "__main__":
    main()
