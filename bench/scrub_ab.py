"""Run bench/scrub_compare.py for two builds alternately and compare (least disturbed run of each).

    python bench/scrub_ab.py OLD_ROOT NEW_ROOT [--rounds 3] tag [tag ...]

Use Blender's own Python (it is passed on to the child runs).
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("old")
ap.add_argument("new")
ap.add_argument("--rounds", type=int, default=3)
ap.add_argument("tags", nargs="+")
A = ap.parse_args()

best = {"old": {}, "new": {}}
for _ in range(A.rounds):
    for which in ("old", "new"):
        root = A.old if which == "old" else A.new
        r = subprocess.run([sys.executable, os.path.join(HERE, "scrub_compare.py"), "--core", root] + A.tags,
                           capture_output=True, text=True, check=True)
        res = json.loads(r.stdout.strip().splitlines()[-1])["results"]
        for tag, v in res.items():
            b = best[which].setdefault(tag, dict(v))
            for k in ("all_back", "all_fwd", "single"):
                b[k] = min(b[k], v[k])
worst = 0.0
for tag in A.tags:
    o, n = best["old"][tag], best["new"][tag]
    parts = []
    for k, name in (("all_back", "whole history back"), ("all_fwd", "forward"), ("single", "one step")):
        pct = 100 * (n[k] / o[k] - 1)
        worst = max(worst, pct)
        parts.append(f"{name} {o[k]:.2f} -> {n[k]:.2f} ms ({pct:+.0f}%)")
    print(f"{tag:14s} " + ", ".join(parts) + f", stored {o['bytes'] / 1e6:.2f} -> {n['bytes'] / 1e6:.2f} MB")
print(f"worst slowdown {worst:+.1f}%")
