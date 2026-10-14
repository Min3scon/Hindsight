"""Compression lab (exploration, kept for PERF_LOG.md "What didn't help"): Prediction cost at the start of each run of consecutive indices against inside runs.

Run with Blender's Python from the repo root after recording corpora (tests_ui/scenarios/s_corpus.py):
    <blender python> bench/lab/run_heads.py <corpus tags>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
from bench_codec import load_corpus, frac
def ent(r):
    return float(np.mean(np.log2(np.abs(r) + 1) + 1)) if r.size else 0.0
def run(tag, lo=0.01, hi=0.035):
    steps = load_corpus(tag)
    acc = {}
    def add(name, v, w):
        acc.setdefault(name, [0.0, 0]); acc[name][0] += v * w; acc[name][1] += w
    for s in steps:
        if not (lo <= frac(s) < hi):
            continue
        for r in s["recs"]:
            if not r.key.startswith("position"):
                continue
            D = r.new.view(np.int32).astype(np.int64) - r.old.view(np.int32).astype(np.int64)
            k = D.shape[0]
            idx = r.idx
            brk = np.concatenate(([True], np.diff(idx) != 1))
            runid = np.cumsum(brk) - 1
            starts = np.flatnonzero(brk)
            pos = np.arange(k) - starts[runid]
            def diffs(x, order):
                p = x.copy()
                for _ in range(order):
                    p = np.concatenate((p[:1], p[1:] - p[:-1])) if False else p
                return p
            # run-aware residuals of order o: residual uses min(o, pos) order
            def res(o):
                out = np.empty_like(D)
                from math import comb
                for t in range(k):
                    pass
                # vectorised: for each order j compute j-th difference (with run-local availability)
                R = []
                cur = D.copy()
                Rj = [D.copy()]
                for j in range(1, o + 1):
                    nxt = cur.copy()
                    nxt[1:] = cur[1:] - cur[:-1]
                    Rj.append(nxt)
                    cur = nxt
                ordr = np.minimum(pos, o)
                out = np.zeros_like(D)
                for j in range(o + 1):
                    m = ordr == j
                    out[m] = Rj[j][m]
                return out
            for o in (1, 2, 3, 4):
                rr = res(o)
                add(f"run-aware o{o}", ent(rr), k)
                add(f"   o{o} in-run part (pos>={o})", ent(rr[pos >= o]), int((pos >= o).sum()))
                add(f"   o{o} head part (pos<{o})", ent(rr[pos < o]), int((pos < o).sum()))
            add("frac pos<3", float(np.mean(pos < 3)), k)
    print("==", tag)
    for k, (v, w) in acc.items():
        print(f"{k:40s} {v / w:.2f}")
for t in sys.argv[1:]:
    run(t)
