"""R072 A/B, end to end: the packer's idle recompression of a history as just opened (no Blender needed).

    PYTHONPATH=<engine folder> <python with numpy> bench/scenarios/oneoff/packer_level_ab.py CODE_ROOT

The steps, packed at the normal setting as a saved file holds them: the 1,000 stroke steps of a 100k mesh cached
by bench/saveload_bench.py (HS_BENCH_CACHE or <tmp>/hs_saveload_cache/steps_100000_1000_0001.bin) and 60 stroke
steps of a 1M mesh (bench_bl.make_mesh's sphere grid, 2% of the vertices per stroke, made here with numpy).
A store.Packer gets them with queue_recompress (what opening a file does) and works until every step was looked
at; the add-on's own pacing applies (5 s after the last activity, then at most a quarter of one core), except that
the 1 s idle wait between two steps (store.IDLE_S) is cut to 1 ms here so a run takes seconds, not 20 minutes: in the
add-on a step is recompressed about once a second, which doesn't change the work per step. Prints one
line "AB {...}": per set the packer thread's CPU inside recompress (thread_time; for the engine's packer, #155, the
process's CPU less the main thread's, which polls as Blender's tick does), the steps made smaller, the
bytes before and after, and the wall time until the last step was done (pacing included); plus the engine's
strong-level switch (0 = its rule)."""
import json, os, struct, sys, tempfile, threading, time
ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, ROOT)
import numpy as np
from hindsight.core import delta, step as stepmod, store
from hindsight.core.native import ENGINE
from hindsight.core.types import DELTA, POSITION_KEY, Entry, Step
assert store.__file__.startswith(ROOT), store.__file__

cache = os.path.join(os.environ.get("HS_BENCH_CACHE") or os.path.join(tempfile.gettempdir(), "hs_saveload_cache"),
                     "steps_100000_1000_0001.bin")
sets = {}
with open(cache, "rb") as fh:
    out = []
    while True:
        h = fh.read(8)
        if not h:
            break
        out.append(fh.read(struct.unpack("<Q", h)[0]))
sets["s100k"] = out

n = 1_000_000                          # bench_bl.make_mesh's positions
rows = int(np.sqrt(n / 2))
cols = n // rows
th = np.repeat(np.linspace(0.05, np.pi - 0.05, rows), cols)
ph = np.tile(np.linspace(0, 2 * np.pi, cols, endpoint=False), rows)
pos = np.stack([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)], 1).astype(np.float32).reshape(-1)
rng = np.random.default_rng(11)
nv = pos.size // 3
k = nv // 50
fall = np.sin(np.linspace(0, np.pi, k, dtype=np.float32)) ** 2
s1m = []
for i in range(60):
    new = pos.copy()
    s = int(rng.integers(0, nv - k))
    noise = 1.0 + 0.05 * rng.standard_normal(k).astype(np.float32)
    new.reshape(-1, 3)[s:s + k] *= (1.0 + 1e-3 * fall * noise)[:, None]
    s1m.append(stepmod.step_to_bytes(Step(seq=i + 2, label="Draw", mode="SCULPT", target="m1", time=1.7e9 + i,
                                          kind=DELTA, deltas=[delta.diff(POSITION_KEY, pos, new)]), "zstd"))
    pos = new
sets["s1m"] = s1m

store.IDLE_S = 0.001
res = {"engine_strong_switch": ENGINE._strong_level() if hasattr(ENGINE, "_strong_level") else None}
real = getattr(store, "recompress", None)
for name, blobs in sets.items():
    if real is None:                  # the engine's packer (#155): its thread counts what it looked at
        entries = [Entry(blob=b, meta={}) for b in blobs]
        p = store.Packer()
        c0, t0 = time.process_time() - time.thread_time(), time.monotonic()
        try:
            p.queue_recompress(entries)
            while p._tried < len(blobs) and time.monotonic() - t0 < 600:
                p.take_in()
                time.sleep(0.001)
        finally:
            p.stop()
        wall = time.monotonic() - t0
        cpu = time.process_time() - time.thread_time() - c0
        assert p._tried == len(blobs), (name, p._tried)
        res[name] = {"steps": len(blobs), "cpu_s": round(cpu, 3), "cpu_ms_per_step": round(cpu * 1000 / len(blobs), 3),
                     "recompressed": p.recompressed, "bytes_before": sum(len(b) for b in blobs),
                     "bytes_after": sum(len(e.blob) for e in entries), "wall_s": round(wall, 2)}
        continue
    cpu, looked, done = [0.0], [0], threading.Event()

    def timed(b):
        t = time.thread_time()
        try:
            return real(b)
        finally:
            cpu[0] += time.thread_time() - t
            looked[0] += 1
            if looked[0] == len(blobs):
                done.set()
    store.recompress = timed
    entries = [Entry(blob=b, meta={}) for b in blobs]
    p = store.Packer()
    t0 = time.monotonic()
    try:
        p.queue_recompress(entries)
        assert done.wait(600), (name, looked[0])
        wall = time.monotonic() - t0
    finally:
        p.stop()
        store.recompress = real
    res[name] = {"steps": len(blobs), "cpu_s": round(cpu[0], 3), "cpu_ms_per_step": round(cpu[0] * 1000 / len(blobs), 3),
                 "recompressed": p.recompressed, "bytes_before": sum(len(b) for b in blobs),
                 "bytes_after": sum(len(e.blob) for e in entries), "wall_s": round(wall, 2)}
res["load"] = open("/proc/loadavg").read().split()[:3] if os.path.exists("/proc/loadavg") else None
print("AB " + json.dumps(res), flush=True)
