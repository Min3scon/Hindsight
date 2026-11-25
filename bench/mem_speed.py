"""Speed check for the memory work: the scenarios PERF_LOG's speed table uses, timed in one process.

Run: <bpy python> bench/mem_speed.py [ROOT] [N]   (ROOT: a checkout whose `hindsight` package is timed,
default this one; N vertices, default 100,000). Prints one JSON line of medians (ms). Run it
alternately for the old and the new build and compare medians of 5 runs.
"""
import os, sys
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import shutil
import statistics
import tempfile
import time

import bpy
import tests.harness as H
from bench.bench_bl import Stroker, make_mesh, scrub
from bench.mem_profile import rebuild
from hindsight.bl import runtime
from hindsight.core.types import SETTINGS

assert os.path.abspath(runtime.__file__).startswith(ROOT), runtime.__file__
C = bpy.context
N = int(sys.argv[2]) if len(sys.argv) > 2 else 100_000


def main():
    H.fresh()
    SETTINGS.raw_steps = 64
    ob = make_mesh(N)
    H.enter(ob, "SCULPT")
    sk = Stroker(ob)
    commit = []
    for i in range(300):
        commit.append(sk.stroke_commit())
        if i % 64 == 63:
            runtime.packer.drain(60)
    runtime.packer.drain(60)
    h = runtime.history_of(ob)
    out = {"commit": statistics.median(commit)}
    out["jump1"], _ = scrub(h, [(300, 299), (299, 300), (150, 149), (149, 150), (10, 9), (9, 10)], 5)
    out["jump300"], _ = scrub(h, [(300, 0), (0, 300)], 3)
    tmp = tempfile.mkdtemp(prefix="hs_speed_")
    try:
        for mode in ("SIDECAR", "EMBED"):
            SETTINGS.save_mode = mode
            blend = os.path.join(tmp, f"s_{mode}.blend")
            ts, tl = [], []
            for _ in range(3):
                t0 = time.perf_counter()
                bpy.ops.wm.save_as_mainfile(filepath=blend)
                ts.append((time.perf_counter() - t0) * 1000)
                t0 = time.perf_counter()
                bpy.ops.wm.open_mainfile(filepath=blend)
                tl.append((time.perf_counter() - t0) * 1000)
            out[f"save_{mode}"] = statistics.median(ts)
            out[f"load_{mode}"] = statistics.median(tl)
        # a topology step (the mesh rebuilt with more vertices), Object Mode
        ob = bpy.data.objects["Obj"]
        H.enter(ob, "OBJECT")
        tt = []
        for k in range(3):
            rebuild(ob, N + 1000 * (k + 1))
            t0 = time.perf_counter()
            st = runtime.commit(ob, label="Remesh", mode="SCULPT", full=True)
            tt.append((time.perf_counter() - t0) * 1000)
            assert st is not None and st.kind == "TOPO"
        out["topo_commit"] = statistics.median(tt)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("RESULT " + json.dumps({k: round(v, 2) for k, v in out.items()}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        H.finish(1)
    H.finish(0)
