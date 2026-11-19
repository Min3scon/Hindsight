"""R061 A/B: Save mode Embed, 100k-vertex mesh, 6,000 steps (setup from bench/saveload_bench.py).
Prints one JSON line: median save and open times of reps 1..4 (rep 0 warms up), and the .blend size."""
import json, os, statistics, sys, time, tempfile, shutil
ROOT = os.path.abspath(sys.argv[1])
sys.argv = [sys.argv[0], ROOT, "100000", "6000", "1"]
sys.path.insert(0, os.path.join(ROOT, "bench"))
import saveload_bench as SB
import bpy
from hindsight.core.types import SETTINGS
from hindsight.bl import runtime
SB.setup()
SETTINGS.save_mode = "EMBED"
tmp = tempfile.mkdtemp(prefix="hs_ab_")
blend = os.path.join(tmp, "p.blend")
saves, opens, peaks = [], [], []
def _kb(tag):
    for line in open("/proc/self/status"):
        if line.startswith(tag):
            return int(line.split()[1])

for rep in range(5):
    if runtime.packer is not None:
        runtime.packer.drain(60)
    with open("/proc/self/clear_refs", "w") as fh:
        fh.write("5")
    rss0 = _kb("VmRSS")
    t0 = time.perf_counter(); bpy.ops.wm.save_as_mainfile(filepath=blend); ts = time.perf_counter() - t0
    peak = (_kb("VmHWM") - rss0) / 1024
    size = os.path.getsize(blend)
    t0 = time.perf_counter(); bpy.ops.wm.open_mainfile(filepath=blend); tl = time.perf_counter() - t0
    h = runtime.history_of(bpy.data.objects[0]) if len(bpy.data.objects) else None
    if rep:
        saves.append(ts); opens.append(tl); peaks.append(peak)
n = sum(len(x.entries) for x in runtime.reg().histories.values())
print("AB " + json.dumps({"save_ms": round(statistics.median(saves) * 1000, 1),
                          "open_ms": round(statistics.median(opens) * 1000, 1), "blend_mb": round(size / 2**20, 2), "save_peak_mb": round(statistics.median(peaks), 1),
                          "steps": n}), flush=True)
shutil.rmtree(tmp, ignore_errors=True)
import tests.harness as H
H.finish(0)
