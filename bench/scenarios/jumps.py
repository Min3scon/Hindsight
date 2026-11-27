"""Jumps in a long history, core only (History.goto): 1, 30, 300 and 3,000 steps forward and back,
and a drag across the whole history (slider events of 50 steps, each way). 100k and 1M vertices.

    <python with numpy> bench/scenarios/jumps.py CODE_ROOT [--quick] [--only a,b] [--reps N]

Scenarios: jump_{1,30,300,3000}_{fwd,back}_{100k,1m}, drag_5000_{fwd,back}_{100k,1m}; memory
(tracemalloc and VmHWM) is reported on jump_300_fwd_* and jump_3000_back_*.
The history is 5,000 packed zstd strokes (2% brush discs on a grid, the newest 64 kept raw as
SETTINGS.raw_steps does). It is built once and cached as an .npz in HS_BENCH_FIXTURES (default
<tmp>/hs_bench_fixtures), so every code root and every session reads the same bytes. Building it
needs the code under test (diff and the step format); a root whose format cannot read the fixture
fails here ("not available"). Every case checks the end state against the fixture's bytes.
--quick: 20k vertices, 600 steps, jumps of 1, 30, 300 and 550 (named 3000 all the same).
"""
import hashlib
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as K  # noqa: E402

opts = K.Opts()
K.use_root(opts.root)
import numpy as np  # noqa: E402
from hindsight.core import delta, step as stepmod  # noqa: E402
from hindsight.core.history import History, MeshState  # noqa: E402
from hindsight.core.types import DELTA, POSITION_KEY, Entry, Step  # noqa: E402

K.import_checked(opts.root)
FIXDIR = os.environ.get("HS_BENCH_FIXTURES") or os.path.join(tempfile.gettempdir(), "hs_bench_fixtures")
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


def fixture(nv, n):
    path = os.path.join(FIXDIR, f"fix_{nv}_{n}.npz")
    if os.path.isfile(path):
        return path
    os.makedirs(FIXDIR, exist_ok=True)
    rng = np.random.default_rng(7)
    side = int(round(nv ** 0.5))
    nv = side * side
    yy, xx = np.divmod(np.arange(nv), side)
    pos = np.stack([xx / side, yy / side, np.zeros(nv)], 1).astype(np.float32).reshape(-1)
    cur = pos
    blobs = []
    for i in range(n):
        nxt = grid_stroke(rng, nv, cur)
        s = Step(i + 1, "Draw", "SCULPT", "obj", float(i), DELTA, [delta.diff(POSITION_KEY, cur, nxt)])
        blobs.append(stepmod.step_to_bytes(s, "zstd"))
        cur = nxt
    lens = np.array([len(b) for b in blobs], np.int64)
    tmp = path + f".{os.getpid()}.tmp.npz"
    np.savez(tmp, start=pos, end=cur, lens=lens, data=np.frombuffer(b"".join(blobs), np.uint8))
    os.replace(tmp, path)
    return path


def load(path, n):
    z = np.load(path)
    data = z["data"].tobytes()
    offs = np.concatenate(([0], np.cumsum(z["lens"])))
    h = History("obj")
    for i in range(n):
        b = data[offs[i]:offs[i + 1]]
        meta = stepmod.read_header(b)["meta"]
        if i >= n - RAW:
            h.entries.append(Entry(stepmod.step_from_bytes(b)))
        else:
            h.entries.append(Entry(blob=b, meta=meta))
    h.cursor = n
    states = {"obj": MeshState({"POINT": z["end"].size // 3}, {POSITION_KEY: z["end"].copy()})}
    return h, states, z["start"], z["end"]


def case(nv, n, tag):
    h, states, start, end = load(fixture(nv, n), n)
    arr = lambda: states["obj"].arrays[POSITION_KEY]
    big = nv >= 500_000
    big_jump = 3000 if n >= 5000 else n - 50
    mid = n // 2
    cases = []                       # (scenario, from, to)
    for j in (1, 30, 300, 3000):
        jj = min(j, big_jump)
        a = mid - jj // 2 if jj < 3000 else (n - jj) // 2
        cases += [(f"jump_{j}_fwd_{tag}", a, a + jj), (f"jump_{j}_back_{tag}", a + jj, a)]
    out = {}
    sha_at = {}
    for name, a, b in cases:
        if not opts.want(name):
            continue
        j = abs(b - a)
        reps = opts.n(5 if (big and j >= 3000) else (8 if j >= 30 else 60), 3 if j >= 30 else 10)
        ts = []
        for _ in range(reps):
            h.goto(a, states)
            t0 = time.perf_counter()
            h.goto(b, states)
            ts.append((time.perf_counter() - t0) * 1000)
        sha_at[name] = hashlib.sha1(arr().tobytes()).hexdigest()[:12]
        out[name] = K.stats(ts, steps=j, vertices=nv, end_sha=sha_at[name])
    # memory of the two long jumps
    span = {nm: (a, b) for nm, a, b in cases}
    for name in (f"jump_300_fwd_{tag}", f"jump_3000_back_{tag}"):
        if name in out:
            a, b = span[name]
            out[name].update(K.mem_of(lambda b=b: h.goto(b, states), reps=2,
                                      prepare=lambda a=a: h.goto(a, states)))
    # the drag: slider events of 50 steps, forward across the whole history and back
    if opts.want(f"drag_5000_fwd_{tag}", f"drag_5000_back_{tag}"):
        fw, bk = [], []
        for _ in range(opts.n(max(2, 4 if not big else 3), 2)):
            h.goto(0, states)
            t0 = time.perf_counter()
            for p in range(50, n + 1, 50):
                h.goto(p, states)
            t1 = time.perf_counter()
            for p in range(n - 50, -1, -50):
                h.goto(p, states)
            t2 = time.perf_counter()
            fw.append((t1 - t0) * 1000)
            bk.append((t2 - t1) * 1000)
        out[f"drag_5000_fwd_{tag}"] = K.stats(fw, events=n // 50, vertices=nv)
        out[f"drag_5000_back_{tag}"] = K.stats(bk, events=n // 50, vertices=nv)
    assert arr().tobytes() == start.tobytes(), "start state differs"
    h.goto(n, states)
    assert arr().tobytes() == end.tobytes(), "end state differs"
    return out


results = {}
sizes = ((100_000, 5000, "100k"), (1_000_000, 5000, "1m")) if not opts.quick else ((20_000, 600, "100k"),
                                                                                  (40_000, 600, "1m"))
for nv, n, tag in sizes:
    # the size runs when --only names any of its scenarios (a pattern such as "jump_300_" is not a
    # substring of the size's tag, so asking for the tag alone skipped every named jump)
    names = [f"{k}_{d}_{tag}" for k in ("jump_1", "jump_30", "jump_300", "jump_3000", "drag_5000")
             for d in ("fwd", "back")]
    if not opts.want(*names):
        continue
    try:
        results.update(case(nv, n, tag))
    except Exception as e:
        import traceback
        traceback.print_exc()
        results[f"jump_300_fwd_{tag}"] = {"error": f"{type(e).__name__}: {e}"[:300]}
K.emit(results, quick=opts.quick, fixtures=FIXDIR)
