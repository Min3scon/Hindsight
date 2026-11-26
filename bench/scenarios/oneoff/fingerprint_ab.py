"""R060 A/B: mesh fingerprints (blake2b-128 in Python before R060, xxh3-128 in the engine after).

    PYTHONPATH=<folder with the engine> <bpy python> bench/scenarios/oneoff/fingerprint_ab.py CODE_ROOT [K]

Run it alternating the two roots under test, each with its own engine first on the path. Prints one line
"AB {...}" (times in ms, medians of the reps after one warm-up):
  fp_100k, fp_1m      runtime.mesh_fingerprint of one mesh of 100k and 1M vertices (bench_bl.make_mesh)
  fp_1m_peak_mb       tracemalloc peak of one 1M fingerprint
  saved_prints        runtime.saved_prints() with K (3) recorded 1M meshes: what a save computes (D46)
  check, open         opening the saved file with those meshes: runtime.check_outside_changes alone, and
                      the whole bpy.ops.wm.open_mainfile
plus the engine's version and API, to show which one ran."""
import contextlib
import json, os, statistics, sys, tempfile, time, tracemalloc
ROOT = os.path.abspath(sys.argv[1])
K = int(sys.argv[2]) if len(sys.argv) > 2 else 3
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "bench", "scenarios"))
import bpy
import tests.harness as H
import _common as C
import hindsight_native
BL = C.load_bench_module("bench_bl")
from hindsight.bl import runtime
assert runtime.__file__.startswith(ROOT), runtime.__file__


def timed(fn, reps):
    fn()                                    # warm-up
    out = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return out


def med(xs):
    return round(statistics.median(xs), 2)


res = {"engine": getattr(hindsight_native, "__version__", None) or "?", "api": hindsight_native.API}
with contextlib.suppress(Exception):
    from importlib.metadata import version
    res["engine"] = version("hindsight_native")
H.fresh()
ob = BL.make_mesh(100_000, name="S")
res["fp_100k"] = med(timed(lambda: runtime.mesh_fingerprint(ob), 9))
bpy.data.objects.remove(ob)

obs = [BL.make_mesh(1_000_000, name=f"M{i}") for i in range(K)]
res["fp_1m"] = med(timed(lambda: runtime.mesh_fingerprint(obs[0]), 5))
tracemalloc.start()
runtime.mesh_fingerprint(obs[0])
res["fp_1m_peak_mb"] = round(tracemalloc.get_traced_memory()[1] / 2**20, 1)
tracemalloc.stop()
for o in obs:                               # a recorded mesh: one stroke each
    H.enter(o, "SCULPT")
    BL.Stroker(o).stroke_commit()
    H.enter(o, "OBJECT")
prints = runtime.saved_prints()
assert sum(1 for r in prints.values() if isinstance(r, dict) and "mesh" in r) == K, prints
res["saved_prints"] = med(timed(runtime.saved_prints, 3))

tmp = tempfile.mkdtemp(prefix="hs_fp_ab_")
path = os.path.join(tmp, "fp.blend")
bpy.ops.wm.save_as_mainfile(filepath=path)
real_check = runtime.check_outside_changes
checks = []


def check(saved):
    t = time.perf_counter()
    try:
        return real_check(saved)
    finally:
        checks.append((time.perf_counter() - t) * 1000)


runtime.check_outside_changes = check
opens = timed(lambda: bpy.ops.wm.open_mainfile(filepath=path), 3)
runtime.check_outside_changes = real_check
assert len(checks) == 4, checks
assert all(runtime.history_of(bpy.data.objects[f"M{i}"]) is not None for i in range(K)), "a history was left out"
assert not any("wasn't running" in t for _s, t in runtime.load_notes), runtime.load_notes
res["check"] = med(checks[1:])
res["open"] = med(opens)
print("AB " + json.dumps(res), flush=True)
H.finish(0)
