"""Makes a corpus of real step bytes for tests/test_core_reference.py.

    python tests/reference/make_step_corpus.py ROOT OUT.zip [--ref]

ROOT is a checkout whose hindsight/core writes the steps (the baseline checkout of commit 66fcdf8
for the committed corpus, or this tree). With --ref the steps are written by the Python reference
(tests/reference, python zstandard and lz4) instead of the engine, which is what this tree's
step.py was before it moved to Rust. The file holds each blob as `<tag>_NNN_<what>.hss` and an
index.json with how it was written. Only core APIs that exist in both trees are used.
"""
import json
import struct
import sys
import zipfile
import zlib

import numpy as np

ROOT, OUT = sys.argv[1], sys.argv[2]
REF = "--ref" in sys.argv
sys.path.insert(0, ROOT)
if REF:
    sys.path.insert(0, ROOT + "/tests")

from hindsight.core import delta, step as stepmod                          # noqa: E402
from hindsight.core.types import (ADD, DATA, DELTA, JOIN, MISSING, TOPO, XOR, ChannelDelta, DataChange,  # noqa: E402
                                  Snapshot, Step)

if REF:
    from reference import step as refstep, pure                            # noqa: E402

    def write(*a):
        with pure():
            return refstep.step_to_bytes(*a)

    def recompress(b):
        with pure():
            return refstep.recompress(b)
    TAG = "ref"
else:
    pure = None
    write, recompress = stepmod.step_to_bytes, stepmod.recompress
    TAG = "base"

POS = "position|POINT|FLOAT_VECTOR"
MASK = ".sculpt_mask|POINT|FLOAT"
FSET = ".sculpt_face_set|FACE|INT"
HIDE = ".hide_vert|POINT|BOOLEAN"
COL = "Col|POINT|FLOAT_COLOR"
rng = np.random.default_rng(20261119)
NV = 6000


def brush(arr, w, centre, radius, amp):
    new = arr.copy().reshape(-1, w)
    lo, hi = max(0, centre - radius), min(NV, centre + radius)
    ix = np.sort(rng.choice(np.arange(lo, hi), size=max(1, (hi - lo) * 3 // 4), replace=False))
    fall = np.cos(np.linspace(0, np.pi / 2, ix.size))[:, None].astype(np.float32)
    new[ix] += (rng.normal(0, amp, (ix.size, w)).astype(np.float32) * fall)
    return new.reshape(-1)


def mesh():
    return {
        POS: rng.normal(0, 1, NV * 3).astype(np.float32),
        MASK: np.zeros(NV, np.float32),
        HIDE: np.zeros(NV, np.bool_),
        COL: rng.random(NV * 4).astype(np.float32),
        FSET: np.ones(NV * 2, np.int32),
    }


def width(key):
    return {POS: 3, COL: 4}.get(key, 1)


def strokes(n):
    arrs = mesh()
    out = []
    for i in range(n):
        ds = []
        c = int(rng.integers(500, NV - 500))
        new = brush(arrs[POS], 3, c, int(rng.integers(30, 400)), 0.02)
        ds.append(delta.diff(POS, arrs[POS], new))
        arrs[POS] = new
        if i % 3 == 0:
            m = arrs[MASK].copy()
            m[c - 50:c + 50] = np.float32(rng.random())
            ds.append(delta.diff(MASK, arrs[MASK], m))
            arrs[MASK] = m
        if i % 5 == 1:
            h = arrs[HIDE].copy()
            h[c:c + 700:2] = True
            ds.append(delta.diff(HIDE, arrs[HIDE], h))
            arrs[HIDE] = h
        if i % 7 == 2:
            f = arrs[FSET].copy()
            f[c:c + 90] = int(rng.integers(1, 20))
            ds.append(delta.diff(FSET, arrs[FSET], f))
            arrs[FSET] = f
        if i % 4 == 3:
            new = brush(arrs[COL], 4, c, 200, 0.05)
            ds.append(delta.diff(COL, arrs[COL], new))
            arrs[COL] = new
        out.append(Step(i + 1, "Stroke", "SCULPT", "obj-" + str(i % 2), 1000.5 + i, DELTA,
                        [d for d in ds if d is not None]))
    return out, arrs


def snap(arrs, nv=NV):
    return Snapshot({"POINT": nv, "EDGE": nv * 2, "FACE": nv, "CORNER": nv * 4},
                    {k: v.copy() for k, v in arrs.items()})


blobs = []


def add(what, step, codec="zstd", effort=delta.NORMAL, strong=False, recomp=False):
    b = write(step, codec, effort, strong)
    if recomp:
        b = recompress(b) or b
    blobs.append({"what": what, "blob": b, "codec": codec, "effort": effort, "strong": strong,
                  "recompressed": recomp})


steps, arrs = strokes(26)
combos = [("zstd", delta.NORMAL, False), ("zstd", delta.QUICK, False), ("zstd", delta.NORMAL, True),
          ("lz4", delta.NORMAL, False), ("zlib", delta.NORMAL, False), ("zlib", delta.NORMAL, True),
          ("none", delta.NORMAL, False), ("none", delta.QUICK, False)]
for i, st in enumerate(steps):
    c, e, s = combos[i % len(combos)]
    add(f"stroke{i}_{c}_{e}_{int(s)}", st, c, e, s)
for st in steps[:6]:
    add("stroke_recompressed", st, "zstd", delta.QUICK, recomp=True)
add("empty", Step(90, "Nothing", "OBJECT", "o", 0.0, DELTA, []), "zlib")
add("unicode", Step(91, "Pinsel é中文 \U0001f58c", "SCULPT", "obj-ü", 1.5, DELTA,
                    steps[1].deltas, extra={"note": "café", "f": 1e-5, "big": 1e22, "n": [1, 2.5]}), "zstd")
small = {k: v[:30 * width(k)].copy() for k, v in mesh().items()}
sm_after = {k: (v + 1 if v.dtype != np.bool_ else ~v) for k, v in small.items()}
add("topo", Step(92, "Voxel Remesh", "SCULPT", "obj-0", 7.0, TOPO, [], snap(small, 30), snap(sm_after, 30)), "zstd")
add("topo_zlib", Step(93, "Voxel Remesh", "SCULPT", "obj-0", 8.0, TOPO, [], snap(small, 30), snap(sm_after, 30)),
    "zlib", strong=True)
add("topo_with_deltas", Step(94, "Mixed", "EDIT", "obj-1", 9.0, TOPO, steps[3].deltas, snap(small, 30),
                             snap(sm_after, 30), extra={"why": "test"}), "lz4")
add("join", Step(95, "Join", "OBJECT", "obj-0", 10.0, JOIN, [], snap(small, 30), snap(sm_after, 30),
                 extra={"members": ["a", "b"], "_snapshots": {"a": snap(small, 30), "b": snap(sm_after, 30)}}),
    "zstd")
recs = [DataChange("xform", "obj-0", {"location": ([0.0, 1.0, 2.0], [0.5, 1.0, -2.25]),
                                      "scale": (MISSING, [1.0, 1.0, 1.0])}),
        DataChange("rna", "obj-1", {"hide_viewport": (False, True), "name": ("A", "Bé"),
                                    "d": ({"a": 1, "b": MISSING}, {"a": 2}),
                                    "nan": (float("nan"), float("inf"))})]
add("data", Step(96, "Move", "OBJECT", "obj-0", 11.0, DATA, [], extra={"k": 1}, records=recs), "zstd")
add("data_none", Step(97, "Move", "OBJECT", "obj-0", 12.0, DATA, [], records=recs), "none")
add("delta_and_data", Step(98, "Both", "SCULPT", "obj-0", 13.0, DELTA, steps[0].deltas, records=recs), "zlib")
add("long_label", Step(99, "L" * 60000, "SCULPT", "obj-0", 14.0, DELTA, steps[2].deltas), "zstd")

# format 1 and 2 steps, as old releases wrote them
for st in steps[:4]:
    recs1 = []
    for d in st.deltas:
        key = d.key.encode()
        k = int(d.idx.size)
        isz, w = d.vals.dtype.itemsize, d.vals.shape[1]
        flags = 3
        dif = np.diff(d.idx.astype(np.int64)).astype(np.uint32)
        planes = np.ascontiguousarray(d.vals).view(np.uint8).reshape(k, isz * w).T.tobytes()
        recs1.append(struct.pack("<2sBBBBHII", b"HD", flags, isz, w, 4, len(key), k, int(d.idx[0])) + key
                     + dif.tobytes() + planes)
    snaps_head = []
    head = json_head = json.dumps({"meta": st.meta(), "deltas": [{"n": len(r)} for r in recs1]}).encode()
    from hindsight.core import codec as _c
    body = b"".join(recs1)
    blob = b"HSS1" + struct.pack("<I", len(head)) + head + _c.compress(body, "zlib")
    blobs.append({"what": "format1_stroke", "blob": blob, "codec": "zlib", "effort": 1, "strong": False,
                  "recompressed": False, "legacy": True})

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    index = []
    for n, b in enumerate(blobs):
        name = f"{TAG}_{n:03d}_{b['what']}.hss"
        z.writestr(name, b["blob"])
        index.append({"name": name, **{k: v for k, v in b.items() if k != "blob"}})
    z.writestr("index.json", json.dumps(index, indent=0))
print(len(blobs), "steps,", sum(len(b["blob"]) for b in blobs), "bytes ->", OUT)
