"""Compression lab (exploration, kept for PERF_LOG.md "What didn't help"): Rice-style split (low bits stored raw, high bits compressed) against zstd on byte planes.

Run with Blender's Python from the repo root after recording corpora (tests_ui/scenarios/s_corpus.py):
    <blender python> bench/lab/split_coding.py <corpus tags>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import time
import numpy as np
from bench_codec import load_corpus, frac, codec, idx_best, body
comp, _ = codec("zstd3")
def o(x, order):
    p = x.copy()
    if order >= 1: p[1:] = x[1:] - x[:-1]
    if order >= 2: p[2:] = x[2:] - 2 * x[1:-1] + x[:-2]
    if order >= 3: p[3:] = x[3:] - 3 * x[2:-1] + 3 * x[1:-2] - x[:-3]
    return p
def zz(r):
    return ((r << 1) ^ (r >> 63)).astype(np.uint64)
def pack_var(z, kk):
    """pack low kk[i] bits of z[i] (variable width), return bytes length"""
    tot = int(kk.sum())
    return (tot + 7) // 8
def cost_component(r, B):
    z = zz(r)
    n = z.size
    nb = (n + B - 1) // B
    pad = nb * B - n
    zp = np.concatenate([z, np.zeros(pad, np.uint64)]).reshape(nb, B)
    m = zp.mean(axis=1)
    best = None
    for off in (0, 1, 2):
        kb = np.maximum(0, np.floor(np.log2(m + 1)).astype(np.int64) - off)
        low_bytes = int((kb.sum() * B + 7) // 8)
        high = (zp >> kb[:, None].astype(np.uint64))
        hm = int(high.max())
        hdt = np.uint8 if hm < 256 else np.uint16 if hm < 65536 else np.uint32
        hb = high.astype(hdt).reshape(-1)[:n]
        hbytes = np.ascontiguousarray(hb.reshape(-1, 1)).view(np.uint8).reshape(n, -1).T.tobytes()
        c = low_bytes + len(comp(hbytes + kb.astype(np.uint8).tobytes()))
        if best is None or c < best:
            best = c
    return best
def run(tag, lo=0.01, hi=0.035):
    steps = load_corpus(tag)
    acc = {}
    def add(name, v, w):
        acc.setdefault(name, [0.0, 0]); acc[name][0] += v; acc[name][1] += w
    for s in steps:
        if not (lo <= frac(s) < hi):
            continue
        add("baseline bytes", s_b := len(comp(body(s))), 1)
        add("diff/prev planes bytes", len(comp(body(s, "diff", "prev", "planes", "best"))), 1)
        for B in (16, 32, 64):
            for order in (1, 2, 3, "best"):
                tot = 0
                for r in s["recs"]:
                    D = (r.new.astype(np.int64) - r.old.astype(np.int64))
                    if r.new.dtype == np.uint32:
                        D = r.new.view(np.int32).astype(np.int64) - r.old.view(np.int32).astype(np.int64)
                    tot += len(comp(idx_best(r.idx)))
                    for c in range(D.shape[1]):
                        if order == "best":
                            tot += min(cost_component(o(D[:, c], q), B) for q in (1, 2, 3))
                        else:
                            tot += cost_component(o(D[:, c], order), B)
                add(f"split-k B={B} order={order}", tot, 1)
    print("==", tag)
    for k, (v, w) in acc.items():
        print(f"{k:40s} {v / w:8.0f}")
for t in sys.argv[1:]:
    run(t)
