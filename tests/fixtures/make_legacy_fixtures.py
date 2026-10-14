"""Write a history file with an OLD build's own code, for the migration tests.

    python tests/fixtures/make_legacy_fixtures.py --core ROOT --out NAME [--codec zstd]

ROOT holds that build's `hindsight/core` (extract it with `git archive <commit> hindsight/core`).
Writes NAME.hsr (the registry exactly as that build saves it) and NAME.npz (every channel of every
object at every history position, to compare against after loading with the current build; the
data is the same for every build, so one copy is kept as legacy_expected.npz).

The fixtures in this folder were made with:
    --core <git archive 22bd252> --out beta1_format1 --codec zstd     (1.0.0-beta.1, format 1)
    --core <git archive d200842> --out beta2wip_format2 --codec zlib  (beta 2 before format 3)
The data is deterministic and includes awkward floats (NaN payloads, -0, denormals, infinities),
an attribute that appears, a boolean channel, a face-domain channel and a topology change.
Run it with Blender's own Python (numpy and zstandard).
"""
import argparse
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--core", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--codec", default="zstd")
A = ap.parse_args()
sys.path.insert(0, os.path.abspath(A.core))

import numpy as np  # noqa: E402

from hindsight.core import delta, persist  # noqa: E402
from hindsight.core.history import History  # noqa: E402
from hindsight.core.registry import Registry  # noqa: E402
from hindsight.core.types import DELTA, TOPO, Snapshot, Step  # noqa: E402

POS = "position|POINT|FLOAT_VECTOR"
MASK = ".sculpt_mask|POINT|FLOAT"
HIDE = ".hide_vert|POINT|BOOLEAN"
FSET = ".sculpt_face_set|FACE|INT"
COL = "Col|POINT|FLOAT_COLOR"
TOPO_KEYS = (".edge_verts|EDGE|INT32_2D", ".corner_vert|CORNER|INT", ".corner_edge|CORNER|INT",
             "face_offsets|FACE|INT")
rng = np.random.default_rng(20261014)


def mesh(nv, nf):
    st = {POS: rng.standard_normal(nv * 3).astype(np.float32),
          MASK: np.zeros(nv, np.float32),
          HIDE: np.zeros(nv, np.bool_),
          FSET: np.ones(nf, np.int32),
          ".edge_verts|EDGE|INT32_2D": rng.integers(0, nv, nf * 4 * 2).astype(np.int32),
          ".corner_vert|CORNER|INT": rng.integers(0, nv, nf * 4).astype(np.int32),
          ".corner_edge|CORNER|INT": rng.integers(0, nf * 4, nf * 4).astype(np.int32),
          "face_offsets|FACE|INT": (np.arange(nf + 1) * 4).astype(np.int32)}
    counts = {"POINT": nv, "EDGE": nf * 4, "FACE": nf, "CORNER": nf * 4}
    return counts, st


def weird_floats(n):
    pool = np.array([0.0, -0.0, np.inf, -np.inf, 1e-45, -1e-45, 1.17549435e-38, 3.4028235e38, 1.0, -1.0],
                    np.float32).view(np.uint32)
    nan_payloads = (np.uint32(0x7FC00000) | rng.integers(0, 1 << 22, 6).astype(np.uint32))
    nan_payloads = np.concatenate([nan_payloads, nan_payloads | np.uint32(0x80000000), [np.uint32(0x7F800001)]])
    bits = np.concatenate([pool, nan_payloads.astype(np.uint32)])
    return rng.choice(bits, n).view(np.float32)


def push_delta(h, seq, target, before, after, counts, label):
    ds = []
    for key in sorted(set(before) | set(after)):
        if key in TOPO_KEYS:
            continue
        dom = key.split("|")[1]
        width = {"FLOAT_VECTOR": 3, "FLOAT_COLOR": 4}.get(key.split("|")[2], 1)
        n = counts[dom] * width
        dt = after[key].dtype if key in after else before[key].dtype
        old = before.get(key, np.zeros(n, dt))
        new = after.get(key, np.zeros(n, dt))
        d = delta.diff(key, old, new, (key in before, key in after))
        if d is not None:
            ds.append(d)
    h.push(Step(seq, label, "SCULPT", target, float(seq), DELTA, ds))


def copy(st):
    return {k: v.copy() for k, v in st.items()}


reg = Registry()
expected = {}
seq = 0
for uid, (nv, nf) in (("objA", (300, 150)), ("objB", (120, 60))):
    h = History("H_" + uid, [uid])
    counts, st = mesh(nv, nf)
    states = [copy(st)]

    def step(label, fn, topo=None):
        global seq, counts, st
        seq += 1
        before, bcounts = copy(st), dict(counts)
        if topo is not None:
            counts, st = topo
            h.push(Step(seq, label, "SCULPT", uid, float(seq), TOPO, [],
                        Snapshot(bcounts, copy(before)), Snapshot(dict(counts), copy(st))))
        else:
            fn(st)
            push_delta(h, seq, uid, before, st, counts, label)
        states.append(copy(st))

    def stroke(s):
        idx = np.sort(rng.choice(counts["POINT"], counts["POINT"] // 6, replace=False))
        p = s[POS].reshape(-1, 3)
        p[idx] += (rng.standard_normal((idx.size, 3)) * 1e-3).astype(np.float32)

    step("Stroke", stroke)
    step("Mask", lambda s: s[MASK].__setitem__(slice(10, 90), weird_floats(80)))
    step("Hide", lambda s: s[HIDE].__setitem__(slice(0, counts["POINT"], 7), True))
    if uid == "objA":
        def add_col(s):
            s[COL] = weird_floats(counts["POINT"] * 4)
        step("Add colour", add_col)
        step("Face sets", lambda s: s[FSET].__setitem__(slice(0, counts["FACE"], 3), 7))
        step("Positions everywhere", lambda s: s.__setitem__(POS, weird_floats(counts["POINT"] * 3)))
        c2, s2 = mesh(nv + 40, nf + 20)
        s2[COL] = weird_floats((nv + 40) * 4)
        step("Remesh", None, topo=(c2, s2))
        step("Stroke after remesh", stroke)

        def drop_col(s):
            del s[COL]
        step("Remove colour", drop_col)
    else:
        step("Stroke 2", stroke)
    h.cursor = len(h.entries)
    reg.histories[h.uid] = h
    reg.by_object[uid] = h.uid
    for pos, s in enumerate(states):
        for k, v in s.items():
            expected[f"{h.uid}|{pos}|{k}"] = v
reg.next_seq = seq + 1
blob = persist.registry_to_bytes(reg, A.codec)
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, A.out + ".hsr"), "wb") as f:
    f.write(blob)
np.savez_compressed(os.path.join(here, A.out + ".npz"), **expected)
print(f"wrote {A.out}.hsr ({len(blob)} bytes), {len(expected)} expected arrays")
