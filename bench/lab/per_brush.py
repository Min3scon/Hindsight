"""Compression lab (exploration, kept for PERF_LOG.md "What didn't help"): Residual size and stored bytes per vertex for each brush.

Run with Blender's Python from the repo root after recording corpora (tests_ui/scenarios/s_corpus.py):
    <blender python> bench/lab/per_brush.py <corpus tags>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
from bench_codec import load_corpus, frac, codec, body
comp, _ = codec("zstd3")
def ent(r):
    return float(np.mean(np.log2(np.abs(r) + 1) + 1)) if r.size else 0.0
steps = load_corpus(sys.argv[1])
rows = {}
for s in steps:
    for r in s["recs"]:
        if not r.key.startswith("position"):
            continue
        D = r.new.view(np.int32).astype(np.int64) - r.old.view(np.int32).astype(np.int64)
        p = D.copy(); p[1:] = D[1:] - D[:-1]; p[2:] = D[2:] - 2 * D[1:-1] + D[:-2]
        p3 = p.copy(); p3[3:] = D[3:] - 3 * D[2:-1] + 3 * D[1:-2] - D[:-3]
        b = s["label"].rsplit(" ", 2)[0]
        size = len(comp(body(s, "diff", "prev", "planes", "best")))
        rows.setdefault(b, []).append((r.idx.size, ent(D), ent(p), ent(p3), size / r.idx.size))
for b, v in sorted(rows.items()):
    a = np.array(v)
    w = a[:, 0]
    print(f"{b:18s} n={len(v):2d} verts={int(w.sum()):7d}  o0 {np.average(a[:,1],weights=w):5.2f}  o2 {np.average(a[:,2],weights=w):5.2f}  o3 {np.average(a[:,3],weights=w):5.2f}  bytes/vert {np.average(a[:,4],weights=w):5.2f}")
