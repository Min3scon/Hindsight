"""Tables for PERF_LOG.md: stored size per step, one technique added at a time, on real strokes.

    <blender python> bench/perf_log.py > .tmp/perf_tables.md

Uses the corpora in .tmp/corpus (tests_ui/scenarios/s_corpus.py) and the real encoder. Every
blob counted here is decoded again and checked against the recorded change (bench_format.check).
Steps:
  format 2      what beta 1 and the beta 2 work in progress stored: JSON header, XOR records, zstd 3
  container     format 3 step: binary header with its own checksum, XOR values as they are
  difference    values are new minus old (wrapping) instead of XOR, as they are
  prediction    zigzag mapping and a per-component predictor (order 0-3), zstd 3: what packing writes
  strong        the same step recompressed in the background (zstd 19), checked byte for byte
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import json
import statistics

from bench_codec import BUCKETS, CORPUS, frac, load_corpus
from bench_format import check, format2_blob, make_step
from hindsight.core import delta, step as stepmod
from hindsight.core.types import ADD, XOR

ORDER = ("100k", "100k_remesh", "100k_subsurf", "500k", "1m")
NAMES = {"100k": "100k, lat/long grid", "100k_remesh": "140k, voxel remesh", "100k_subsurf": "400k, subdivided",
         "500k": "500k, lat/long grid", "1m": "1M, lat/long grid"}
STAGES = ("format 2", "container", "difference", "prediction", "strong")


def sizes(s):
    x, a = make_step(s, XOR), make_step(s, ADD)
    out = {"format 2": len(format2_blob(x)),
           "container": len(stepmod.step_to_bytes(x, "zstd", delta.NORMAL)),
           "difference": len(stepmod.step_to_bytes(a, "zstd", delta.QUICK))}
    b = stepmod.step_to_bytes(a, "zstd", delta.NORMAL)
    out["prediction"] = len(b)
    strong = stepmod.recompress(b) or b
    out["strong"] = len(strong)
    check(s, stepmod.step_from_bytes(strong))
    out["k"] = sum(r.idx.size for r in s["recs"])
    return out


def main():
    tags = [t for t in ORDER if os.path.exists(os.path.join(CORPUS, t + ".npz"))]
    results = {}
    for tag in tags:
        steps = load_corpus(tag)
        rows = [(frac(s), sizes(s)) for s in steps]
        results[tag] = {"verts": steps[0]["n"], "rows": rows}
    print("### Stored size per step (bytes, mean), one technique added at a time\n")
    print("| Mesh | Stroke bucket | Steps | Mean touched | " + " | ".join(STAGES) + " | Change |")
    print("|---|---|---|---|" + "---|" * len(STAGES) + "---|")
    summary = {}
    for tag in tags:
        for name, lo, hi in BUCKETS[:3]:
            sel = [r for f, r in results[tag]["rows"] if lo <= f < hi]
            fr = [f for f, r in results[tag]["rows"] if lo <= f < hi]
            if not sel:
                continue
            m = {st: statistics.mean(r[st] for r in sel) for st in STAGES}
            summary[(tag, name)] = (m, len(sel), statistics.mean(r["k"] for r in sel))
            print(f"| {NAMES[tag]} | {name} | {len(sel)} | {100 * statistics.mean(fr):.1f}% | "
                  + " | ".join(f"{m[s]:,.0f}" for s in STAGES)
                  + f" | {100 * (m['strong'] / m['format 2'] - 1):+.0f}% |")
    print("\n### What each technique contributed (share of the format 2 size, mean over the 2% rows)\n")
    print("| Technique | " + " | ".join(NAMES[t] for t in tags) + " |")
    print("|---|" + "---|" * len(tags))
    for i in range(1, len(STAGES)):
        cells = []
        for tag in tags:
            m = summary.get((tag, "2%"))
            if m is None:
                cells.append("-")
                continue
            m = m[0]
            cells.append(f"{100 * (m[STAGES[i]] - m[STAGES[i - 1]]) / m['format 2']:+.1f}%")
        print(f"| {STAGES[i]} | " + " | ".join(cells) + " |")
    print("\n### Memory for a 6,000-step history of 2% strokes\n")
    print("| Mesh | Mean stroke (verts) | Old: 64 raw + 5,936 packed | New | Saved |")
    print("|---|---|---|---|---|")
    for tag in tags:
        s = summary.get((tag, "2%"))
        if s is None:
            continue
        m, _n, k = s
        raw = k * 16 + 64 * 0                          # index + 3 x 4 bytes per changed vertex
        old = 64 * raw + 5936 * m["format 2"]
        new = 64 * raw + 5936 * m["strong"]
        print(f"| {NAMES[tag]} | {k:,.0f} | {old / 2**20:,.1f} MB | {new / 2**20:,.1f} MB | {100 * (1 - new / old):.0f}% |")
    with open(os.path.join(os.path.dirname(__file__), "results_perf_log.json"), "w") as f:
        json.dump({f"{t}|{b}": {"n": v[1], "mean_changed_verts": v[2], **v[0]} for (t, b), v in summary.items()},
                  f, indent=1)


if __name__ == "__main__":
    main()
