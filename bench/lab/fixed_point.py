"""Compression lab (exploration, kept for PERF_LOG.md "What didn't help"): Exponent-aware (fixed-point) differences against plain bit-pattern differences.

Run with Blender's Python from the repo root after recording corpora (tests_ui/scenarios/s_corpus.py):
    <blender python> bench/lab/fixed_point.py <corpus tags>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
from bench_codec import load_corpus, frac, CORPUS
def ent(r):
    return float(np.mean(np.log2(np.abs(r) + 1) + 1))
def run(tag, lo=0.01, hi=0.035):
    steps = load_corpus(tag)
    acc = {}
    def add(name, v, w):
        acc.setdefault(name, [0.0, 0]); acc[name][0] += v * w; acc[name][1] += w
    for s in steps:
        f = frac(s)
        if not (lo <= f < hi):
            continue
        for r in s["recs"]:
            if not r.key.startswith("position"):
                continue
            ob = r.old.view(np.uint32); nbits = r.new.view(np.uint32)
            D = r.new.view(np.int32).astype(np.int64) - r.old.view(np.int32).astype(np.int64)
            def ordm(b):
                b = b.astype(np.int64)
                return np.where(b & 0x80000000, -(b & 0x7FFFFFFF), b)
            Dm = ordm(nbits) - ordm(ob)
            of = r.old.view(np.float32).astype(np.float64); nf = r.new.view(np.float32).astype(np.float64)
            e_old = np.maximum(((ob >> 23) & 0xFF).astype(np.int64), 1)
            e_new = np.maximum(((nbits >> 23) & 0xFF).astype(np.int64), 1)
            sh = np.minimum(e_old, e_new)
            floor_e = int(sh.max()) - 20
            exc = sh < floor_e
            add("exceptions frac", float(exc.mean()), k := D.shape[0])
            sh = np.maximum(sh, floor_e)
            emin = floor_e
            of = np.where(exc, 0.0, of); nf = np.where(exc, 0.0, nf)
            dfix = np.round((nf - of) * 2.0 ** (150 - emin)).astype(np.int64)
            k = D.shape[0]
            def o2(x):
                p = x.copy(); p[1:] = x[1:] - x[:-1]; p[2:] = x[2:] - 2 * x[1:-1] + x[:-2]; return p
            def o1(x):
                p = x.copy(); p[1:] = x[1:] - x[:-1]; return p
            add("bitspace o1", ent(o1(D)), k); add("bitspace o2", ent(o2(D)), k)
            add("ordered-int o2", ent(o2(Dm)), k)
            for name, fn in (("o1", o1), ("o2", o2)):
                rr = fn(dfix)
                s0 = sh - emin
                m = s0.copy()
                m[1:] = np.minimum(m[1:], s0[:-1])
                if name == "o2":
                    m[2:] = np.minimum(m[2:], s0[:-2])
                q = rr >> m
                assert np.all((q << m) == rr)
                add(f"fixed-point {name} (shifted)", ent(q), k)
            add("emin range bits", float(np.mean(sh.max() - emin)), k)
            add("exp change old->new frac", float(np.mean(e_old != e_new)), k)
    print("==", tag)
    for k, (v, w) in acc.items():
        print(f"{k:40s} {v / w:.2f}")
for t in sys.argv[1:]:
    run(t)
