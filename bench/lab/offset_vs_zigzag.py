"""Compression lab (exploration, kept for PERF_LOG.md "What didn't help"): Offset (bias) mapping against zigzag for signed residuals, compressed size.

Run with Blender's Python from the repo root after recording corpora (tests_ui/scenarios/s_corpus.py):
    <blender python> bench/lab/offset_vs_zigzag.py <corpus tags>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import statistics
import numpy as np
from bench_codec import load_corpus, frac, codec
from hindsight.core import delta as D
comp, _ = codec("zstd3")


def resid(v, order):
    r = v.astype(np.int64)
    for _ in range(order):
        r = np.concatenate((r[:1], r[1:] - r[:-1]))
    return r


def zz32(r):
    r32 = r.astype(np.int64)
    return (((r32 << 1) ^ (r32 >> 63)) & 0xFFFFFFFF).astype(np.uint32)


def bias32(r):
    m = int(np.abs(r).max()).bit_length() + 1 if r.size else 1
    m = min(m, 32)
    return ((r + (1 << (m - 1))) & 0xFFFFFFFF).astype(np.uint32)


for tag in sys.argv[1:]:
    steps = load_corpus(tag)
    tot = {"zz": 0, "bias": 0}
    two = {"zz": [], "bias": []}
    for s in steps:
        parts = {"zz": [], "bias": []}
        for r in s["recs"]:
            if r.old.dtype != np.uint32:
                continue
            v = (r.new - r.old).view(np.int32).astype(np.int64)
            for c in range(v.shape[1]):
                best = None
                for o in range(4):
                    rr = resid(v[:, c], o)
                    e = D._bits(zz32(rr).reshape(-1, 1))[0]
                    if best is None or e < best[0] * 0.97:
                        best = (e, rr)
                rr = best[1]
                parts["zz"].append(D._planes(zz32(rr).reshape(-1, 1)))
                parts["bias"].append(D._planes(bias32(rr).reshape(-1, 1)))
        for kname in parts:
            n = len(comp(b"".join(parts[kname])))
            tot[kname] += n
            if 0.01 <= frac(s) < 0.035:
                two[kname].append(n)
    print(tag, {k: f"total {v / 1e6:.3f} MB, 2% {statistics.mean(two[k]):.0f} B" for k, v in tot.items()})
