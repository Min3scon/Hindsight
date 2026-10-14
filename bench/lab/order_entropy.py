"""Compression lab (exploration, kept for PERF_LOG.md "What didn't help"): Residual size by prediction order along the record (log-magnitude bits per coordinate), 2% strokes.

Run with Blender's Python from the repo root after recording corpora (tests_ui/scenarios/s_corpus.py):
    <blender python> bench/lab/order_entropy.py <corpus tags>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
from bench_codec import load_corpus, frac, codec, idx_best, planes, zigzag
steps = load_corpus(sys.argv[1] if len(sys.argv) > 1 else "100k")
comp, _ = codec("zstd3")
def ent(r):
    # mean bits needed for |r| (log2) + sign
    a = np.abs(r.astype(np.int64))
    return float(np.mean(np.log2(a + 1) + 1))
tot = {}
for s in steps:
    f = frac(s)
    if not (0.01 <= f < 0.035):
        continue
    for r in s["recs"]:
        if not r.key.startswith("position"):
            continue
        D = (r.new.astype(np.int64).view(np.int64) if False else (r.new.view(np.int32).astype(np.int64) - r.old.view(np.int32).astype(np.int64)))
        same = np.concatenate(([False], np.diff(r.idx) == 1))
        res = {"o0": D}
        p1 = D.copy(); p1[1:] = D[1:] - D[:-1]; res["o1"] = p1
        p2 = p1.copy(); p2[2:] = D[2:] - 2*D[1:-1] + D[:-2]; res["o2"] = p2
        p3 = p2.copy(); p3[3:] = D[3:] - 3*D[2:-1] + 3*D[1:-2] - D[:-3]; res["o3"] = p3
        # float displacement magnitude
        oldf = r.old.view(np.float32); newf = r.new.view(np.float32)
        dl = (newf.astype(np.float64) - oldf.astype(np.float64))
        k = r.idx.size
        idx_b = len(comp(idx_best(r.idx)))
        tot.setdefault("n", 0); tot["n"] += k
        tot.setdefault("idx_bytes", 0); tot["idx_bytes"] += idx_b
        tot.setdefault("runs", 0); tot["runs"] += int((~same).sum())
        for name, v in res.items():
            tot.setdefault(name, 0.0); tot[name] += ent(v) * k
        tot.setdefault("absdisp", 0.0); tot["absdisp"] += float(np.mean(np.abs(dl))) * k
        # fraction of same exponent neighbours
        e = (r.old.view(np.uint32) >> 23) & 0xFF
        tot.setdefault("expchg", 0.0); tot["expchg"] += float(np.mean(e[1:] != e[:-1])) * k
        tot.setdefault("zero", 0.0); tot["zero"] += float(np.mean(D == 0)) * k
n = tot["n"]
print("verts", n, "avg run length", n / tot["runs"], "idx bytes/vert", tot["idx_bytes"] / n)
for kname in ("o0", "o1", "o2", "o3"):
    print(kname, "bits/component", tot[kname] / n)
print("mean |disp|", tot["absdisp"] / n, "exp change frac", tot["expchg"] / n, "zero D frac", tot["zero"] / n)
