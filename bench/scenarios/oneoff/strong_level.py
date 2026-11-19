"""R072: idle recompression (Packer, step.recompress) at zstd levels 19, 17, 15 and 12: CPU time and size.

    PYTHONPATH=<engine folder with _strong_level> <bpy python> bench/scenarios/oneoff/strong_level.py CODE_ROOT [ROUNDS]

Real step blobs as the packer gets them (packed at the normal setting, zstd level 3):
  s100k   the 1,000 stroke steps of a 100k mesh cached by bench/saveload_bench.py (HS_BENCH_CACHE or
          <tmp>/hs_saveload_cache/steps_100000_1000_0001.bin; left out if missing)
  s1m     60 stroke steps of a 1M mesh (bench_bl.make_mesh positions, 2% of the vertices per stroke, as
          saveload_bench makes them)
  topo    3 topology steps of a 100k mesh (Blender's own subdivision of 1% of the edges): bodies over 1 MB,
          where the engine compresses with a window as large as the body and long distance matching
Each set is recompressed by the engine at each level (`_strong_level`), the levels in a new order every
round; CPU is the calling thread's time (the engine runs there). Prints one line "AB {...}": per set and
level the CPU ms per step (median of rounds), the bytes after (and before), and the time to read the
recompressed steps back (step_from_bytes, ms per step)."""
import json, os, statistics, struct, sys, tempfile, time
CACHE = os.path.join(os.environ.get("HS_BENCH_CACHE") or os.path.join(tempfile.gettempdir(), "hs_saveload_cache"),
                     "steps_100000_1000_0001.bin")      # before tests.harness moves the temporary folder
ROOT = sys.argv[1]
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "bench", "scenarios"))
import bpy
import bmesh
import numpy as np
import tests.harness as H
import _common as K
from hindsight.bl import meshio
from hindsight.core import delta, step as stepmod
from hindsight.core.native import ENGINE
from hindsight.core.types import DELTA, POSITION_KEY, TOPO, Step
BL = K.load_bench_module("bench_bl")
LEVELS = (19, 17, 15, 12)

sets = {}
cache = CACHE
if os.path.isfile(cache):
    out = []
    with open(cache, "rb") as fh:
        while True:
            h = fh.read(8)
            if not h:
                break
            out.append(fh.read(struct.unpack("<Q", h)[0]))
    sets["s100k"] = out

H.fresh()
ob = BL.make_mesh(1_000_000, name="M")
pos = np.empty(len(ob.data.vertices) * 3, np.float32)
ob.data.attributes["position"].data.foreach_get("vector", pos)
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
    d = delta.diff(POSITION_KEY, pos, new)
    s1m.append(stepmod.step_to_bytes(Step(seq=i + 2, label="Draw", mode="SCULPT", target="m1", time=1.7e9 + i,
                                          kind=DELTA, deltas=[d]), "zstd"))
    pos = new
sets["s1m"] = s1m
bpy.data.objects.remove(ob)

ob = BL.make_mesh(100_000, name="T")
topo = []
before = meshio.read_snapshot(ob.data)
for i in range(3):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.edges.ensure_lookup_table()
    sel = [bm.edges[j] for j in rng.integers(0, len(bm.edges), len(bm.edges) // 100)]
    bmesh.ops.subdivide_edges(bm, edges=list(set(sel)), cuts=1, use_grid_fill=True)
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()
    after = meshio.read_snapshot(ob.data)
    topo.append(stepmod.step_to_bytes(Step(seq=i + 2, label="Subdivide", mode="EDIT", target="t", time=1.7e9 + i,
                                           kind=TOPO, before=before, after=after), "zstd"))
    before = after
sets["topo"] = topo

res = {name: {lv: {"cpu": [], "size": None, "read": []} for lv in LEVELS} for name in sets}
order = list(LEVELS)
for r in range(ROUNDS):
    for lv in (order if r % 2 == 0 else list(reversed(order))):
        ENGINE._strong_level(lv)
        for name, blobs in sets.items():
            cpu, size, outs = 0.0, 0, []
            for b in blobs:
                t = time.thread_time()
                nb = stepmod.recompress(b)
                cpu += time.thread_time() - t
                outs.append(nb if nb is not None else b)
                size += len(outs[-1])
            t = time.thread_time()
            for nb in outs:
                stepmod.step_from_bytes(nb)
            rd = time.thread_time() - t
            d = res[name][lv]
            d["cpu"].append(cpu * 1000 / len(blobs))
            d["read"].append(rd * 1000 / len(blobs))
            assert d["size"] in (None, size), (name, lv, d["size"], size)      # the same bytes every round
            d["size"] = size
ENGINE._strong_level(19)
out = {"rounds": ROUNDS, "load": open("/proc/loadavg").read().split()[:3]}
for name, blobs in sets.items():
    o = {"steps": len(blobs), "bytes_before": sum(len(b) for b in blobs)}
    for lv in LEVELS:
        d = res[name][lv]
        o[f"L{lv}"] = {"cpu_ms_per_step": round(statistics.median(d["cpu"]), 3), "bytes": d["size"],
                       "read_ms_per_step": round(statistics.median(d["read"]), 3)}
    out[name] = o
print("AB " + json.dumps(out), flush=True)
H.finish(0)
