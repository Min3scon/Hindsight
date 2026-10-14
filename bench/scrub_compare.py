"""Scrub speed of one build of the core on real recorded strokes, for comparing builds side by side.

    python bench/scrub_compare.py --core ROOT [--reps N] tag [tag ...]

ROOT is a folder holding a `hindsight/core` package (the working tree, or an older commit extracted
with `git archive`). Steps are packed by that build's own writer, as the add-on would store them,
then History.goto runs across the whole history and back, and one step at a time over the newest
40 steps. Every jump is checked bit for bit. Prints one JSON line.
Run it with Blender's own Python. `bench/scrub_ab.py` runs two builds alternately and compares.
"""
import argparse
import json
import os
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--core", required=True)
ap.add_argument("--reps", type=int, default=7)
ap.add_argument("tags", nargs="+")
A = ap.parse_args()
sys.path.insert(0, os.path.abspath(A.core))

import numpy as np  # noqa: E402

from hindsight.core import step as stepmod  # noqa: E402
from hindsight.core import types as T  # noqa: E402
from hindsight.core.history import History, MeshState  # noqa: E402

CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".tmp", "corpus")
NEW = hasattr(T, "ADD")
TOPO_KEYS = (".edge_verts", ".corner_vert", ".corner_edge", "face_offsets")


def load(tag):
    meta = json.load(open(os.path.join(CORPUS, tag + ".json")))
    z = np.load(os.path.join(CORPUS, tag + ".npz"))
    base = {n[5:]: z[n].copy() for n in z.files if n.startswith("base|") and not n[5:].startswith(TOPO_KEYS)}
    state = {k: np.ascontiguousarray(v).view(T.bits_dtype(v.dtype)).reshape(-1, T.key_width(k)).copy()
             for k, v in base.items()}
    steps = []
    for i, sm in enumerate(meta["steps"]):
        recs = []
        for j, dm in enumerate(sm["deltas"]):
            key = dm["key"]
            w = T.key_width(key)
            bd = T.bits_dtype(T.key_dtype(key))
            idx = z[f"s{i}_{j}_idx"].astype(np.int64)
            v = z[f"s{i}_{j}_xor"].view(bd).reshape(-1, w)
            if key not in state:
                state[key] = np.zeros((meta["counts"][T.split_key(key)[1]], w), dtype=bd)
            old = state[key][idx].copy()
            new = (old + v) if dm.get("op", 0) == 1 else (old ^ v)
            state[key][idx] = new
            recs.append((key, idx.astype(np.uint32), old, new, tuple(dm["exists"])))
        steps.append(recs)
    final = {k: v.reshape(-1).view(T.key_dtype(k)).copy() for k, v in state.items()}
    return meta["counts"], base, final, steps


def build(steps):
    h = History("bench")
    for i, recs in enumerate(steps):
        ds = []
        for key, idx, old, new, exists in recs:
            if NEW:
                ds.append(T.ChannelDelta(key, idx, new - old, exists, T.ADD))
            else:
                ds.append(T.ChannelDelta(key, idx, old ^ new, exists))
        st = T.Step(seq=i + 1, label="Stroke", mode="SCULPT", target="bench", time=1.76e9, kind=T.DELTA, deltas=ds)
        blob = stepmod.step_to_bytes(st, "zstd", 0) if NEW else stepmod.step_to_bytes(st, "zstd")
        h.entries.append(T.Entry(blob=blob, meta=st.meta()))
    h.cursor = len(h.entries)
    return h


def same(a, b):
    return np.array_equal(a.view(np.uint32), b.view(np.uint32))


def run(tag):
    counts, base, final, steps = load(tag)
    h = build(steps)
    n = len(h.entries)
    pk = T.POSITION_KEY
    res = {"all_back": [], "all_fwd": [], "single": []}
    for _ in range(A.reps):
        states = {"bench": MeshState(counts, {k: v.copy() for k, v in final.items()})}
        h.cursor = n
        t = time.perf_counter()
        h.goto(0, states)
        res["all_back"].append((time.perf_counter() - t) * 1e3)
        assert same(states["bench"].arrays[pk], base[pk])
        t = time.perf_counter()
        h.goto(n, states)
        res["all_fwd"].append((time.perf_counter() - t) * 1e3)
        assert same(states["bench"].arrays[pk], final[pk])
        t = time.perf_counter()
        for p in range(n - 1, n - 41, -1):
            h.goto(p, states)
        res["single"].append((time.perf_counter() - t) * 1e3 / 40)
        h.goto(n, states)
    out = {k: min(v) for k, v in res.items()}
    out["bytes"] = sum(len(e.blob) for e in h.entries)
    out["steps"] = n
    return out


print(json.dumps({"new": NEW, "results": {t: run(t) for t in A.tags}}))
