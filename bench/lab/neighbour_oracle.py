"""Compression lab (exploration, kept for PERF_LOG.md "What didn't help"): Upper bound for prediction from mesh neighbours (uses the edges; not decodable as written).

Run with Blender's Python from the repo root after recording corpora (tests_ui/scenarios/s_corpus.py):
    <blender python> bench/lab/neighbour_oracle.py <corpus tags>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import numpy as np
from bench_codec import load_corpus, frac, CORPUS
tag = sys.argv[1]
z = np.load(os.path.join(CORPUS, tag + ".npz"))
ev = z["base|.edge_verts|EDGE|INT32_2D"].reshape(-1, 2).astype(np.int64)
steps = load_corpus(tag)
n = steps[0]["n"]
a = np.concatenate([ev[:, 0], ev[:, 1]]); b = np.concatenate([ev[:, 1], ev[:, 0]])
o = np.argsort(a, kind="stable"); a = a[o]; b = b[o]
indptr = np.zeros(n + 1, np.int64); np.add.at(indptr, a + 1, 1); indptr = np.cumsum(indptr)
deg = np.diff(indptr)
def ent(r):
    return float(np.mean(np.log2(np.abs(r) + 1) + 1))
rng = np.random.default_rng(0)
prio = rng.permutation(n)
acc = {}
def add(name, v, w):
    acc.setdefault(name, [0.0, 0]); acc[name][0] += v * w; acc[name][1] += w
for s in steps:
    f = frac(s)
    if not (0.01 <= f < 0.035):
        continue
    rr = [r for r in s["recs"] if r.key.startswith("position")]
    if not rr:
        continue
    r = rr[0]
    S = r.idx
    D = r.new.view(np.int32).astype(np.int64) - r.old.view(np.int32).astype(np.int64)
    Df = np.zeros((n, 3), np.int64); Df[S] = D
    inS = np.zeros(n, bool); inS[S] = True
    # neighbour sums for S
    starts, ends = indptr[S], indptr[S + 1]
    nb_counts = ends - starts
    nb = b[np.concatenate([np.arange(st, en) for st, en in zip(starts, ends)])]
    owner = np.repeat(np.arange(S.size), nb_counts)
    sums = np.zeros((S.size, 3), np.int64); np.add.at(sums, owner, Df[nb])
    lap = D - sums // nb_counts[:, None]
    add("o0", ent(D), S.size)
    oldf = r.old.view(np.float32).astype(np.float64); newf = r.new.view(np.float32).astype(np.float64)
    dl = newf - oldf
    ulp = 2.0 ** -23
    Ff = np.zeros((n, 3)); Ff[S] = dl / ulp
    sf = np.zeros((S.size, 3)); np.add.at(sf, owner, Ff[nb])
    lapf = dl / ulp - sf / nb_counts[:, None]
    add("float space o0 (ulp at 1.0)", ent(np.round(dl / ulp)), S.size)
    add("float space oracle laplacian", ent(np.round(lapf)), S.size)
    # per-vertex exponent-based ulp (true ulp)
    e = np.floor(np.log2(np.maximum(np.abs(oldf), 1e-30)))
    add("frac comps with |x|<0.125", float(np.mean(np.abs(oldf) < 0.125)), S.size)
    add("oracle laplacian (all nbrs)", ent(lap), S.size)
    # Luby MIS in S
    F = np.zeros(n, bool)
    cand = inS.copy()
    while cand.any():
        # vertex is local max of prio among candidate neighbours
        cv = np.flatnonzero(cand)
        st, en = indptr[cv], indptr[cv + 1]
        nbv = b[np.concatenate([np.arange(x, y) for x, y in zip(st, en)])]
        ow = np.repeat(np.arange(cv.size), en - st)
        p_nb = np.where(cand[nbv], prio[nbv], -1)
        mx = np.full(cv.size, -1); np.maximum.at(mx, ow, p_nb)
        win = prio[cv] > mx
        F[cv[win]] = True
        cand[cv[win]] = False
        # remove neighbours of winners
        wv = cv[win]
        st, en = indptr[wv], indptr[wv + 1]
        if wv.size:
            nbw = b[np.concatenate([np.arange(x, y) for x, y in zip(st, en)])]
            cand[nbw] = False
    fmask = F[S]
    add("MIS fine fraction", fmask.mean(), S.size)
    add("fine: all-neighbour mean", ent(lap[fmask]), fmask.sum())
    # coarse: predict from coarse+unchanged neighbours only (excluding fine), oracle-ish
    Dc = Df.copy(); Dc[F] = 0
    known = ~F  # coarse or unchanged
    kn = known[nb]
    sums_c = np.zeros((S.size, 3), np.int64); np.add.at(sums_c, owner, Dc[nb] * kn[:, None])
    cnt_c = np.zeros(S.size, np.int64); np.add.at(cnt_c, owner, kn.astype(np.int64))
    cm = ~fmask
    predc = np.where(cnt_c[:, None] > 0, sums_c // np.maximum(cnt_c, 1)[:, None], 0)
    add("coarse: mean of non-fine nbrs (not causal)", ent((D - predc)[cm]), cm.sum())
for k, (v, w) in acc.items():
    print(f"{k:45s} {v / w:.2f}")
