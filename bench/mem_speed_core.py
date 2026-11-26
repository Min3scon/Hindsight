"""Speed of the code paths the memory work changed, without Blender (less noise than whole saves).

Run: <python with numpy + zstandard> bench/mem_speed_core.py [ROOT]   (ROOT: checkout to time)
Prints one JSON line of medians (ms) over repeated runs: writing a stroke step (quick form, as the
journal does), a topology step of a 100k-vertex mesh, the history file of 6,000 strokes (save and
load), and the embedded form (base64 out and in, as persistence.py does it in that build).
"""
import os, sys
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import base64
import binascii
import json
import shutil
import statistics
import tempfile
import time

import numpy as np
from hindsight.core import delta, persist, step as stepmod
from hindsight.core.history import History
from hindsight.core.registry import Registry
from hindsight.core.types import (CORNER_EDGE_KEY, CORNER_VERT_KEY, DELTA, EDGE_VERTS_KEY, FACE_OFFSETS_KEY,
                                  POSITION_KEY, TOPO, Entry, Snapshot, Step)

assert stepmod.__file__.startswith(ROOT), stepmod.__file__
NEW = hasattr(persist, "registry_parts")
rng = np.random.default_rng(7)
NV = 100_000


def med(fn, reps):
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def stroke_step(seq, pos):
    k = NV // 50
    s = int(rng.integers(0, NV - k))
    new = pos.copy()
    fall = np.sin(np.linspace(0, np.pi, k, dtype=np.float32)) ** 2
    new.reshape(-1, 3)[s:s + k] *= (1 + 1e-3 * fall * (1 + 0.05 * rng.standard_normal(k).astype(np.float32)))[:, None]
    d = delta.diff(POSITION_KEY, pos, new)
    return Step(seq=seq, label="Draw", mode="SCULPT", target="obj", time=float(seq), kind=DELTA, deltas=[d]), new


def snapshot(nv):
    nf = nv
    th = np.linspace(0, 3, nv * 3, dtype=np.float32)
    return Snapshot({"POINT": nv, "EDGE": 2 * nf, "FACE": nf, "CORNER": 4 * nf},
                    {POSITION_KEY: np.sin(th), EDGE_VERTS_KEY: (np.arange(4 * nf, dtype=np.int32) // 2) % nv,
                     CORNER_VERT_KEY: np.arange(4 * nf, dtype=np.int32) % nv,
                     CORNER_EDGE_KEY: np.arange(4 * nf, dtype=np.int32) % (2 * nf),
                     FACE_OFFSETS_KEY: np.arange(nf + 1, dtype=np.int32) * 4})


def main():
    pos = np.sin(np.linspace(0, 50, NV * 3, dtype=np.float32))
    st, pos = stroke_step(1, pos)
    out = {"stroke_step_quick": med(lambda: stepmod.step_to_bytes(st, "auto", 0), 200)}
    topo = Step(seq=2, label="Remesh", mode="SCULPT", target="obj", kind=TOPO, before=snapshot(NV),
                after=snapshot(NV + 1000))
    out["topo_step_quick"] = med(lambda: stepmod.step_to_bytes(topo, "auto", 0), 15)
    # a 6,000-step history (blobs packed once, reused for every entry to keep set-up short)
    blobs = []
    for i in range(50):
        s, pos = stroke_step(i + 1, pos)
        blobs.append(stepmod.step_to_bytes(s))
    h = History("H", ["obj"])
    h.entries = [Entry(blob=blobs[i % 50], meta={"seq": i + 1, "label": "Draw", "mode": "SCULPT", "target": "obj",
                                                 "time": float(i), "kind": "DELTA", "extra": {}}) for i in range(6000)]
    h.cursor = 6000
    reg = Registry()
    reg.histories = {"H": h}
    reg.by_object = {"obj": "H"}
    tmp = tempfile.mkdtemp(prefix="hs_speed_core_")
    try:
        blend = os.path.join(tmp, "a.blend")
        if hasattr(persist, "save_sidecar"):     # builds before #53; V2 writes no history folder, only reads V1's
            out["save_sidecar"] = med(lambda: persist.save_sidecar(reg, blend), 9)
        else:
            os.makedirs(persist.sidecar_dir(blend))
            with open(persist.sidecar_file(blend), "wb") as fh:
                fh.write(persist.registry_to_bytes(reg))
        out["load_sidecar"] = med(lambda: persist.load_sidecar(blend), 9)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if NEW:
        enc = lambda: base64.b64encode(persist.registry_to_bytes(reg)).decode("ascii")
    else:
        def enc():
            data = persist.registry_to_bytes(reg)
            return base64.b64encode(data).decode("ascii")
    out["embed_encode"] = med(enc, 9)
    s = enc()
    dec = (lambda: persist.registry_from_bytes(binascii.a2b_base64(s))) if NEW else \
        (lambda: persist.registry_from_bytes(base64.b64decode(s)))
    out["embed_decode"] = med(dec, 9)
    print("RESULT " + json.dumps({k: round(v, 3) for k, v in out.items()}))


main()
