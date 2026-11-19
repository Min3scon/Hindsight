"""Registry load (persist.registry_from_bytes) of a STEPS-step history of real 100k strokes: from the history
file (load_sidecar) and from bytes (the Embed copy). Core only, no Blender. Usage: python -I regload_ab.py ROOT
ENGINE_DIR STEPS. Prints AB {json}: min and median of 15 loads each."""
import os, sys, time, struct, statistics, json, tempfile, shutil, gc
ROOT, ENG, STEPS = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, ENG); sys.path.insert(0, ROOT)
from hindsight.core import persist, native, step as stepmod
from hindsight.core.history import History
from hindsight.core.registry import Registry
from hindsight.core.types import Entry
L = struct.Struct("<Q")
blobs = []
with open("/tmp/hs_saveload_cache/steps_100000_6000_0001.bin", "rb") as fh:
    while len(blobs) < STEPS:
        h = fh.read(8)
        if not h: break
        blobs.append(fh.read(L.unpack(h)[0]))
reg = Registry(); h = History("H0", ["a"])
for b in blobs:
    h.entries.append(Entry(blob=b, meta=stepmod.read_header(b)["meta"]))
h.cursor = len(h.entries); reg.histories["H0"] = h; reg.by_object["a"] = "H0"; reg.next_seq = len(blobs) + 2
data = persist.registry_to_bytes(reg)
tmp = tempfile.mkdtemp(prefix="hs_regload_")
blend = os.path.join(tmp, "f.blend")
persist.save_sidecar(reg, blend)
del reg, h
out = {"engine": native.ENGINE.__file__.split("/")[-3] if hasattr(native.ENGINE, "__file__") else "?", "steps": len(blobs), "mb": round(len(data) / 2**20, 1)}
for name, fn in (("file", lambda: persist.load_sidecar(blend)), ("bytes", lambda: persist.registry_from_bytes(data))):
    ts = []
    for _ in range(16):
        gc.collect()
        t = time.perf_counter(); r = fn(); ts.append((time.perf_counter() - t) * 1000)
        assert len(r.histories["H0"].entries) == len(blobs)
        del r
    ts = ts[1:]
    out[name + "_min"] = round(min(ts), 2); out[name + "_med"] = round(statistics.median(ts), 2)
shutil.rmtree(tmp, ignore_errors=True)
print("AB " + json.dumps(out))
