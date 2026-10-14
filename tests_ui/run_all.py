#!/usr/bin/env python3
"""Run every real-UI scenario on every Blender 4.2+ found. Needs a desktop session (a window).

    python tests_ui/run_all.py [--blender all|5.1.2] [--only name,name] [--skip-build]

Skips (exit 0) when no Blender is installed or HINDSIGHT_NO_WINDOW is set (CI, SSH sessions).
Writes .tmp/ui/summary.json.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hs_ui  # noqa: E402

# (scenario, fresh profile, install through Blender's GUI installer instead of the CLI)
SUITE = [
    ("install_gui", True, True),
    ("audit_register", False, False),
    ("ui_tour", True, True),
    ("regress_slider_visible", False, False),
    ("regress_file_load", False, False),
    ("fail_inject", False, False),
    ("regress_stroke_split", False, False),
    ("regress_native_undo", False, False),
    ("adapters", False, False),
    ("tier1", False, False),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blender", default="all")
    ap.add_argument("--only", default="")
    ap.add_argument("--skip-build", action="store_true")
    a = ap.parse_args()
    if os.environ.get("HINDSIGHT_NO_WINDOW"):
        print("SKIP real-UI suite: HINDSIGHT_NO_WINDOW is set")
        return 0
    blenders = hs_ui.select(hs_ui.find_blenders(), a.blender) if hs_ui.find_blenders() else []
    if not blenders:
        print("SKIP real-UI suite: no Blender 4.2+ found")
        return 0
    if not a.skip_build:
        hs_ui.build(hs_ui.find_blenders())
    only = set(filter(None, a.only.split(",")))
    results = []
    t0 = time.time()
    for name, fresh, gui_install in SUITE:
        if only and name not in only:
            continue
        for tag, exe in blenders:
            if fresh:
                hs_ui.wipe_profile(tag)
            if not gui_install:
                hs_ui.cli_install(tag, exe, hs_ui.DEFAULT_ZIP)
            res, out = hs_ui.run_one(tag, exe, name, {}, hs_ui.DEFAULT_ZIP, 600, False, [40, 40, 1600, 900])
            checks = res.get("checks", [])
            fails = [c for c in checks if not c["ok"]]
            ok = bool(res.get("ok"))
            results.append({"scenario": name, "blender": tag, "ok": ok, "checks": len(checks),
                            "failed": [c["name"] for c in fails], "error": res.get("error"),
                            "seconds": res.get("seconds")})
            print(f"{'ok  ' if ok else 'FAIL'} {name:24s} {tag:8s} {len(checks) - len(fails)}/{len(checks)}"
                  f" checks {res.get('seconds')} s", flush=True)
            for c in fails:
                print(f"     x {c['name']}: {c.get('detail', '')[:300]}")
            if res.get("error"):
                print("     error:", str(res["error"])[-800:])
    bad = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(bad)}/{len(results)} scenario runs passed in {time.time() - t0:.0f} s")
    (hs_ui.TMP / "ui").mkdir(parents=True, exist_ok=True)
    (hs_ui.TMP / "ui" / "summary.json").write_text(json.dumps(results, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
