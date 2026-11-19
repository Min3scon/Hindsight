"""R064 A/B: a saved file (100k vertices), 3,000 unsaved strokes (journal only), the file opened again with
Crash Recovery Interrupted. One JSON line: open ms, MB the process grew over the open (VmRSS), the
history's own size (Entry.nbytes) and how many of its steps are decoded (raw)."""
import json, os, sys, time, tempfile, gc
ROOT = sys.argv[1]; N = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
sys.path.insert(0, ROOT); sys.path.insert(1, os.path.join(ROOT, "bench", "scenarios"))
import bpy
import tests.harness as H
import _common as K
BL = K.load_bench_module("bench_bl")
from hindsight.bl import runtime, persistence
from hindsight.core.types import SETTINGS
def kb(tag):
    for line in open("/proc/self/status"):
        if line.startswith(tag):
            return int(line.split()[1])
H.fresh()
SETTINGS.save_mode = "SIDECAR"; SETTINGS.crash_recovery = "INTERRUPTED"; SETTINGS.max_steps = 100000
ob = BL.make_mesh(100_000, name="M")
tmp = tempfile.mkdtemp(prefix="hs_replay_")
path = os.path.join(tmp, "r.blend")
H.enter(ob, "SCULPT"); sk = BL.Stroker(ob); sk.stroke_commit(); H.enter(ob, "OBJECT")
bpy.ops.wm.save_as_mainfile(filepath=path)
ob = bpy.data.objects["M"]; H.enter(ob, "SCULPT"); sk = BL.Stroker(ob)
for i in range(N):
    sk.stroke_commit()
H.enter(ob, "OBJECT")
runtime.set_registry(type(runtime.reg())())     # drop the session's history before measuring the open
if runtime.packer is not None:
    runtime.packer.drain(60)
gc.collect()
rss0 = kb("VmRSS")
t = time.perf_counter()
bpy.ops.wm.open_mainfile(filepath=path)
dt = time.perf_counter() - t
gc.collect()
rss1 = kb("VmRSS")
h = runtime.history_of(bpy.data.objects["M"])
print("AB " + json.dumps({"open_ms": round(dt * 1000, 1), "rss_growth_mb": round((rss1 - rss0) / 1024, 1),
                          "history_mb": round(sum(e.nbytes() for e in h.entries) / 2**20, 1),
                          "raw_steps": sum(1 for e in h.entries if not e.is_packed()), "steps": len(h.entries)}), flush=True)
H.finish(0)
