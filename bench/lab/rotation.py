"""Compression lab (exploration, kept for PERF_LOG.md "What didn't help"): Rotating each block of changes into its main direction before predicting.

Run with Blender's Python from the repo root after recording corpora (tests_ui/scenarios/s_corpus.py):
    <blender python> bench/lab/rotation.py <corpus tags>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
from bench_codec import load_corpus, frac
def ent(r):
    return float(np.mean(np.log2(np.abs(r) + 1) + 1)) if r.size else 0.0
def o2(x):
    p = x.copy(); p[1:] = x[1:] - x[:-1]; p[2:] = x[2:] - 2 * x[1:-1] + x[:-2]; return p
def o3(x):
    p = o2(x); p[3:] = x[3:] - 3 * x[2:-1] + 3 * x[1:-2] - x[:-3]; return p
def run(tag, lo=0.01, hi=0.035, B=64):
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
            D = (r.new.view(np.int32).astype(np.int64) - r.old.view(np.int32).astype(np.int64)).astype(np.float64)
            k = D.shape[0]
            add("per-comp o2", np.mean([ent(o2(D[:, c])) for c in range(3)]), k)
            add("per-comp o3", np.mean([ent(o3(D[:, c])) for c in range(3)]), k)
            # global rank-1 direction per delta
            for name, bs in (("delta", k), ("block64", 64), ("block16", 16)):
                outs = []
                for b0 in range(0, k, bs):
                    blk = D[b0:b0 + bs]
                    u = np.linalg.svd(blk, full_matrices=True)[2] if blk.shape[0] >= 3 else np.eye(3)
                    outs.append(blk @ u.T)
                R = np.round(np.concatenate(outs))
                add(f"rotated ({name}) o2", np.mean([ent(o2(R[:, c])) for c in range(3)]), k)
                add(f"   rotated ({name}) comp0/1/2 o2", ent(o2(R[:, 0])) * 0 + 0, k)
                for c in range(3):
                    add(f"   rotated ({name}) comp{c} o2", ent(o2(R[:, c])), k)
    print("==", tag)
    for k, (v, w) in acc.items():
        if "comp0/1/2" in k: continue
        print(f"{k:40s} {v / w:.2f}")
for t in sys.argv[1:]:
    run(t)
