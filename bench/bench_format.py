"""Stored size and speed of the step format on real recorded strokes: format 2 against format 3.

Run with Blender's own Python (numpy and zstandard):
    <blender python> bench/bench_format.py [tags]

Input: .tmp/corpus/<tag>.npz + .json from tests_ui/scenarios/s_corpus.py (real strokes, real Blender,
generated test models). Every format 3 blob is decoded and applied to the old state, and must give the
new state bit for bit before its size counts. Writes bench/results_format.json.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import json
import statistics
import time

import numpy as np

from bench_codec import BUCKETS, CORPUS, frac, load_corpus
from hindsight.core import codec, delta, step as stepmod
from hindsight.core.types import ADD, DELTA, XOR, ChannelDelta, Step

OUT = os.path.join(os.path.dirname(__file__), "results_format.json")


def make_step(s, op):
    ds = []
    for r in s["recs"]:
        vals = (r.new - r.old) if op == ADD else (r.old ^ r.new)
        ds.append(ChannelDelta(r.key, r.idx.astype(np.uint32), vals, r.exists, op))
    return Step(seq=123456, label="Stroke", mode="SCULPT", target="0123456789abcdef", time=1.76e9,
                kind=DELTA, deltas=ds)


def format2_blob(st):
    """The beta 1 / format 2 writer, reproduced: JSON header + "HD" XOR records + zstd 3."""
    import struct
    parts, heads = [], []
    for d in st.deltas:
        key_b = d.key.encode()
        idx = d.idx
        k = idx.size
        isz, w = d.vals.dtype.itemsize, d.vals.shape[1]
        if k > 1:
            dif = np.diff(idx)
            m = int(dif.max())
            dsz = 1 if m <= 0xFF else (2 if m <= 0xFFFF else 4)
            dif_b = dif.astype({1: np.uint8, 2: np.uint16, 4: np.uint32}[dsz]).tobytes()
        else:
            dsz, dif_b = 1, b""
        hdr = struct.pack("<2sBBBBHII", b"HD", 3, isz, w, dsz, len(key_b), k, int(idx[0]) if k else 0)
        planes = np.ascontiguousarray(d.vals).view(np.uint8).reshape(k, isz * w).T.tobytes() if k else b""
        p = hdr + key_b + dif_b + planes
        parts.append(p)
        heads.append({"key": d.key, "exists": [True, True], "k": int(k), "n": len(p)})
    meta = {"seq": st.seq, "label": st.label, "mode": st.mode, "target": st.target, "time": st.time,
            "kind": st.kind, "extra": {}}
    head = json.dumps({"v": 1, "meta": meta, "deltas": heads, "before": None, "after": None, "snaps": None},
                      separators=(",", ":")).encode()
    return b"HSS1" + struct.pack("<I", len(head)) + head + codec.compress(b"".join(parts), "zstd")


def check(s, st2):
    """Decoded deltas must turn the old values into the new ones and back, bit for bit."""
    for r, d in zip(s["recs"], st2.deltas):
        assert np.array_equal(d.idx, r.idx.astype(np.uint32)), "indices differ"
        got = (r.old ^ d.vals) if d.op == XOR else (r.old + d.vals)
        assert np.array_equal(got, r.new), "values differ"
        back = (got ^ d.vals) if d.op == XOR else (got - d.vals)
        assert np.array_equal(back, r.old), "backwards differs"


def timed(fn, reps):
    best = None
    for _ in range(reps):
        t = time.perf_counter()
        out = fn()
        dt = time.perf_counter() - t
        best = dt if best is None else min(best, dt)
    return out, best


def run(tag):
    steps = load_corpus(tag)
    rows = []
    for s in steps:
        f = frac(s)
        st3 = make_step(s, ADD)
        st2 = make_step(s, XOR)
        b2 = format2_blob(st2)
        reps = 5 if len(b2) < 200_000 else 2
        b3f, t_enc_fast = timed(lambda: stepmod.step_to_bytes(st3, "zstd", delta.QUICK), reps)
        b3, t_enc_best = timed(lambda: stepmod.step_to_bytes(st3, "zstd", delta.NORMAL), reps)
        b3s, t_strong = timed(lambda: stepmod.recompress(b3) or b3, 1)
        for blob in (b3f, b3, b3s):
            check(s, stepmod.step_from_bytes(blob))
        d2, t_dec2 = timed(lambda: stepmod.step_from_bytes(b2), reps)
        d3, t_dec3 = timed(lambda: stepmod.step_from_bytes(b3s), reps)
        check(s, d2)
        rows.append({"frac": f, "label": s["label"], "k": int(sum(r.idx.size for r in s["recs"])),
                     "f2": len(b2), "f3_fast": len(b3f), "f3": len(b3), "f3_strong": len(b3s),
                     "enc2_ms": None, "enc_fast_ms": t_enc_fast * 1e3, "enc_best_ms": t_enc_best * 1e3,
                     "strong_ms": t_strong * 1e3, "dec2_us": t_dec2 * 1e6, "dec3_us": t_dec3 * 1e6})
    return {"verts": steps[0]["n"], "steps": rows}


def summary(res):
    out = {}
    for name, lo, hi in BUCKETS:
        sel = [r for r in res["steps"] if lo <= r["frac"] < hi]
        if not sel:
            continue
        m = lambda k: statistics.mean(r[k] for r in sel)
        out[name] = {"n": len(sel), "f2": m("f2"), "f3_fast": m("f3_fast"), "f3": m("f3"), "f3_strong": m("f3_strong"),
                     "dec2_us": m("dec2_us"), "dec3_us": m("dec3_us"), "enc_fast_ms": m("enc_fast_ms"),
                     "enc_best_ms": m("enc_best_ms"), "strong_ms": m("strong_ms")}
    return out


def main():
    tags = [t[:-4] for t in sorted(os.listdir(CORPUS)) if t.endswith(".npz")]
    if len(sys.argv) > 1:
        tags = [t for t in tags if t in sys.argv[1:]]
    allres = {}
    for tag in tags:
        res = run(tag)
        sm = summary(res)
        allres[tag] = {"verts": res["verts"], "summary": sm, "steps": res["steps"]}
        print(f"\n=== {tag} ({res['verts']:,} verts)")
        print(f"{'bucket':6s} {'n':>3s} {'format 2':>9s} {'3 fast':>9s} {'3 best':>9s} {'3 strong':>9s} {'gain':>6s}"
              f" {'dec2 us':>8s} {'dec3 us':>8s} {'enc fast':>9s} {'enc best':>9s} {'strong':>8s}")
        for b, v in sm.items():
            print(f"{b:6s} {v['n']:3d} {v['f2']:9.0f} {v['f3_fast']:9.0f} {v['f3']:9.0f} {v['f3_strong']:9.0f}"
                  f" {100 * (1 - v['f3_strong'] / v['f2']):5.1f}% {v['dec2_us']:8.0f} {v['dec3_us']:8.0f}"
                  f" {v['enc_fast_ms']:8.2f}m {v['enc_best_ms']:8.2f}m {v['strong_ms']:7.1f}m")
    with open(OUT, "w") as f:
        json.dump(allres, f, indent=1)


if __name__ == "__main__":
    main()
