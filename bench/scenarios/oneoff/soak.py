"""Long session: 100k mesh, N strokes, a jump back and forward every 50, a save every 500; every 500 strokes the
process's memory (VmRSS) next to what Hindsight's history and baselines hold. Growth beyond those is a leak."""
import os, sys, time, gc, tempfile, json
ROOT = sys.argv[1]; N = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
sys.path.insert(0, ROOT); sys.path.insert(1, os.path.join(ROOT, "bench", "scenarios"))
import bpy
import tests.harness as H
import _common as K
BL = K.load_bench_module("bench_bl")
from hindsight.bl import runtime
from hindsight.core.types import SETTINGS
SETTINGS.max_steps = 100000; SETTINGS.memory_limit_mb = 16384; SETTINGS.save_mode = "SIDECAR"
C = bpy.context
NOREC = "norec" in sys.argv
def kb(tag):
    for line in open("/proc/self/status"):
        if line.startswith(tag):
            return int(line.split()[1])
H.fresh()
ob = BL.make_mesh(100_000, name="Soak")
path = os.path.join(tempfile.mkdtemp(prefix="hs_soak_"), "soak.blend")
H.enter(ob, "SCULPT"); sk = BL.Stroker(ob)
rows = []
t0 = time.time()
for i in range(1, N + 1):
    sk.stroke() if NOREC else sk.stroke_commit()
    if i % 50 == 0 and not NOREC:
        h = runtime.history_of(ob)
        runtime.goto(C, max(0, h.cursor - 10)); runtime.goto(C, len(h.entries))
    if i % 500 == 0:
        if runtime.packer is not None:
            runtime.packer.drain(120)
        H.enter(ob, "OBJECT"); bpy.ops.wm.save_as_mainfile(filepath=path); ob = bpy.data.objects["Soak"]; H.enter(ob, "SCULPT"); sk.ob = ob if hasattr(sk, "ob") else None
        gc.collect()
        st = runtime.memory_stats()
        rows.append({"strokes": i, "rss_mb": round(kb("VmRSS") / 1024, 1), "history_mb": round(st["total_bytes"] / 2**20, 1),
                     "ram_mb": round(st["ram_bytes"] / 2**20, 1), "steps": st["steps"], "py_objects": len(gc.get_objects()),
                     "s": round(time.time() - t0)})
        print("SOAK " + json.dumps(rows[-1]), flush=True)
H.finish(0)
