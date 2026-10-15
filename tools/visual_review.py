"""Record a look at a real-UI scenario's screenshots (gate item VISUAL).

    python tools/visual_review.py --scenario t1_gate --blender 5.1.2 --rows obj.transforms,obj.parenting \
        --verdict ok --note "sidebar, step list and popover: nothing cut off or overlapping" [--variant default]

The screenshots are the scenario's newest run (.tmp/ui/<scenario>/<blender>/shots); every one is
listed with the verdict, so the evidence says exactly which pictures were looked at. A verdict of
"defect" records a failing result (and the defect goes in DEFECTS.md). The result goes to
.tmp/results/tools/visual_review/<blender>/ for tools/make_evidence.py.
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--blender", required=True)
    ap.add_argument("--variant", default="")
    ap.add_argument("--rows", required=True)
    ap.add_argument("--verdict", choices=("ok", "defect"), required=True)
    ap.add_argument("--note", required=True)
    a = ap.parse_args(argv)
    folder = a.blender + (f"-{a.variant}" if a.variant else "")
    shots_dir = os.path.join(ROOT, ".tmp", "ui", a.scenario, folder, "shots")
    shots = sorted(f for f in os.listdir(shots_dir) if f.endswith(".png")) if os.path.isdir(shots_dir) else []
    if not shots:
        raise SystemExit(f"no screenshots in {shots_dir}: run the scenario first")
    rows = [r.strip() for r in a.rows.split(",") if r.strip()]
    unit = f"visual:{a.scenario}" + (f"[{a.variant}]" if a.variant else "")
    tests = [{"name": f"looked at {s}", "ok": a.verdict == "ok", "covers": [[r, "VISUAL"] for r in rows]}
             for s in shots]
    dst = os.path.join(ROOT, ".tmp", "results", "tools", "visual_review", a.blender)
    os.makedirs(dst, exist_ok=True)
    stem = (a.scenario + (f"_{a.variant}" if a.variant else "")).replace("/", "_")
    with open(os.path.join(dst, stem + ".log"), "w", encoding="utf-8") as fh:
        fh.write(f"{a.verdict}: {a.note}\n" + "\n".join(shots) + "\n")
    with open(os.path.join(dst, stem + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"unit": unit, "blender": a.blender, "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "tests": tests, "log": stem + ".log"}, fh, indent=1)
    print(f"visual_review: {len(shots)} screenshots, {a.verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
