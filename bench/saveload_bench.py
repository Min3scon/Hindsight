"""Save and load of a large history inside Blender, history folder and embedded, time and memory.

Run: <bpy python> bench/saveload_bench.py ROOT N STEPS [REPS]
  ROOT   checkout whose `hindsight` package is measured (run old and new alternately)
  N      vertices of the mesh (the steps touch about 2% of them, as bench_bl.Stroker does)
  STEPS  steps in the history (the newest 64 kept raw, the rest packed, as while sculpting)
Prints one line "RESULT {json}": per mode the median and min of save and load time (ms), the
tracemalloc peak above the level before the operation and the process peak (VmHWM, reset through
/proc/self/clear_refs) above the RSS before it.

The steps are made once with the core alone (no Blender, so recording 6,000 strokes takes seconds
instead of minutes) and cached in HS_BENCH_CACHE (default: a folder next to this file's checkout in
the system temp folder): every build is measured on the same bytes. One real stroke is recorded
first so the object, its history and the baseline are set up by the add-on itself.
"""
import os, sys
ROOT = os.path.abspath(sys.argv[1])
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(HERE, "bench", "scenarios"))
import gc
import json
import shutil
import statistics
import struct
import tempfile
import time
import tracemalloc

import bpy
import numpy as np
import tests.harness as H
import _common as K   # bench/scenarios/_common.py: the helpers always come from THIS checkout, not from ROOT
_bl = K.load_bench_module("bench_bl")
Stroker, make_mesh = _bl.Stroker, _bl.make_mesh
from hindsight.bl import runtime
from hindsight.core import delta, step as stepmod
from hindsight.core.types import DELTA, POSITION_KEY, SETTINGS, Entry, Step

assert os.path.abspath(runtime.__file__).startswith(ROOT), runtime.__file__
N = int(sys.argv[2])
STEPS = int(sys.argv[3])
REPS = int(sys.argv[4]) if len(sys.argv) > 4 else 3
UID = "benchsaveload0000000000000000001"
CACHE = os.environ.get("HS_BENCH_CACHE") or os.path.join(tempfile.gettempdir(), "hs_saveload_cache")
_LEN = struct.Struct("<Q")


proc = K.proc  # 0 where there is no /proc (Windows)


class Peak:
    def __enter__(self):
        gc.collect()
        self.rss0 = proc("VmRSS")
        K.reset_hwm()
        tracemalloc.start()
        tracemalloc.reset_peak()
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self.t0) * 1000
        _cur, self.py = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.rss = proc("VmHWM") - self.rss0
        return False


def blobs(co0):
    """STEPS step blobs of strokes on a copy of the mesh positions, cached by (N, STEPS)."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"steps_{N}_{STEPS}_{UID[-4:]}.bin")
    if os.path.isfile(path):
        out = []
        with open(path, "rb") as fh:
            while True:
                h = fh.read(8)
                if not h:
                    return out
                out.append(fh.read(_LEN.unpack(h)[0]))
    rng = np.random.default_rng(11)
    pos = co0.copy()
    nv = pos.size // 3
    k = max(1, nv // 50)
    fall = np.sin(np.linspace(0, np.pi, k, dtype=np.float32)) ** 2
    out = []
    for i in range(STEPS):
        new = pos.copy()
        s = int(rng.integers(0, nv - k))
        noise = 1.0 + 0.05 * rng.standard_normal(k).astype(np.float32)
        new.reshape(-1, 3)[s:s + k] *= (1.0 + 1e-3 * fall * noise)[:, None]
        d = delta.diff(POSITION_KEY, pos, new)
        st = Step(seq=i + 2, label="Draw", mode="SCULPT", target=UID, time=1.7e9 + i, kind=DELTA, deltas=[d])
        out.append(stepmod.step_to_bytes(st, "zstd"))
        pos = new
    with open(path + ".tmp", "wb") as fh:
        for b in out:
            fh.write(_LEN.pack(len(b)))
            fh.write(b)
    os.replace(path + ".tmp", path)
    return out


def setup():
    H.fresh()
    SETTINGS.raw_steps = 64
    SETTINGS.compression = "auto"
    SETTINGS.max_steps = 100000
    SETTINGS.memory_limit_mb = 8192
    ob = make_mesh(N)
    ob[runtime.UID_PROP] = UID
    H.enter(ob, "SCULPT")
    sk = Stroker(ob)
    co0 = sk.co.copy()
    sk.stroke_commit()                 # the add-on sets up the history and the baseline
    runtime.packer.drain(60)
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 1 and h.entries[0].target == UID
    bs = blobs(co0)
    ents = []
    for i, b in enumerate(bs):
        meta = stepmod.read_header(b)["meta"]
        if i >= len(bs) - SETTINGS.raw_steps:
            ents.append(Entry(step=stepmod.step_from_bytes(b)))
        else:
            ents.append(Entry(blob=b, meta=meta))
    h.entries = h.entries + ents
    h.cursor = len(h.entries)
    runtime.reg().next_seq = len(h.entries) + 2
    del bs, ents
    gc.collect()
    return len(h.entries)


def main():
    n_entries = setup()
    hist_bytes = sum(e.nbytes() for h in runtime.reg().histories.values() for e in h.entries)
    out = {"n": N, "steps": n_entries, "history_mb": round(hist_bytes / 2 ** 20, 2)}
    tmp = tempfile.mkdtemp(prefix="hs_saveload_")
    try:
        mode = "EMBED"  # #48: every save puts the history in the .blend (V1's name for that in the keys)
        blend = os.path.join(tmp, f"s_{mode}.blend")
        rows = {"save": [], "load": []}
        for _ in range(REPS):
            if runtime.packer is not None:
                runtime.packer.drain(60)
            with Peak() as p:
                bpy.ops.wm.save_as_mainfile(filepath=blend)
            rows["save"].append(p)
            with Peak() as p:
                bpy.ops.wm.open_mainfile(filepath=blend)
            rows["load"].append(p)
            got = sum(len(x.entries) for x in runtime.reg().histories.values())
            assert got == n_entries, (mode, got, n_entries)   # (nothing here keeps the registry)
        for k, ps in rows.items():
            out[f"{k}_{mode}"] = {
                "ms_med": round(statistics.median(p.ms for p in ps), 1),
                "ms_min": round(min(p.ms for p in ps), 1),
                "py_mb": round(max(p.py for p in ps) / 2 ** 20, 2),
                "rss_mb": round(statistics.median(p.rss for p in ps) / 2 ** 20, 2)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("RESULT " + json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        H.finish(1)
    H.finish(0)
