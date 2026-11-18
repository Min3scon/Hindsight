"""summarize.py OUTDIR: min and median over rounds of each metric, old vs new."""
import glob
import json
import os
import statistics
import sys

d = sys.argv[1]
runs = {"old": [], "new": []}
for p in sorted(glob.glob(os.path.join(d, "*.json"))):
    tag = os.path.basename(p).split("_")[0]
    runs[tag].append(json.load(open(p))["results"])
scen = list(runs["new"][0])
print(f"rounds: old {len(runs['old'])}, new {len(runs['new'])}; us per call: min (median of per-run mins); calls per call")
for sc in scen:
    if "us_min" in runs["new"][0][sc]:
        for r in runs["old"] + runs["new"]:
            r[sc] = {"mouse_move": r[sc]}
    for metric in runs["new"][0][sc]:
        o = [r[sc][metric] for r in runs["old"]]
        n = [r[sc][metric] for r in runs["new"]]
        if metric == "timer":
            om = min(x["cpu_ms_per_60s"] for x in o)
            nm = min(x["cpu_ms_per_60s"] for x in n)
            print(f"{sc:28s} {'timer cpu ms/60s':22s} {om:8.2f} -> {nm:8.2f}")
            continue
        if not isinstance(o[0], dict) or "us_min" not in o[0]:
            continue
        omin, nmin = min(x["us_min"] for x in o), min(x["us_min"] for x in n)
        omed, nmed = statistics.median(x["us_min"] for x in o), statistics.median(x["us_min"] for x in n)
        oc, nc = o[0]["calls"], n[0]["calls"]
        extra = ""
        if "gpu" in o[0]:
            extra = f"  batches built {o[0]['gpu'].get('batch_for_shader', 0):.1f} -> {n[0]['gpu'].get('batch_for_shader', 0):.1f}"
        print(f"{sc:28s} {metric:22s} {omin:8.1f} ({omed:7.1f}) -> {nmin:8.1f} ({nmed:7.1f}) us "
              f"{100 * (nmed / omed - 1):+5.0f}%   calls {oc:7.0f} -> {nc:7.0f}{extra}")
