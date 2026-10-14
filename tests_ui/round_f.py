#!/usr/bin/env python3
"""Round F: old versus new builds, on every Blender found.

    python tests_ui/round_f.py [--blender all]

Builds 1.0.0 and 1.0.0-beta.1 from git, records the same session with each build and with the
current one (all three save modes, one file left mid-rewind), opens the old files with the new
build, opens and saves the new files with beta 1 and checks the new build still reads them (going
back), and compares the sessions step by step.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hs_ui  # noqa: E402

OLD = {"b1": "e683e01", "v100": "1c1acf7"}


def run(tag, exe, profile, zip_path, scenario, args, results, name):
    hs_ui.wipe_profile(profile)
    hs_ui.cli_install(profile, exe, zip_path)
    res, out = hs_ui.run_one(profile, exe, scenario, args, zip_path, 600, False, [40, 40, 1600, 900],
                             out_tag=f"{tag}-{name}")
    fails = [c for c in res.get("checks", []) if not c["ok"]]
    ok = bool(res.get("ok"))
    print(f"{'ok  ' if ok else 'FAIL'} {name:28s} {tag:8s} {len(res.get('checks', [])) - len(fails)}/"
          f"{len(res.get('checks', []))}", flush=True)
    for c in fails:
        print(f"     x {c['name']}: {c.get('detail', '')[:300]}")
    if res.get("error"):
        print("     error:", str(res["error"])[-600:])
    results.append({"blender": tag, "run": name, "ok": ok, "checks": len(res.get("checks", [])),
                    "failed": [c["name"] for c in fails]})
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blender", default="all")
    a = ap.parse_args()
    blenders = hs_ui.find_blenders()
    hs_ui.build(blenders)
    zips = {"new": hs_ui.DEFAULT_ZIP}
    for label, commit in OLD.items():
        zips[label] = hs_ui.build_ref(blenders, commit, hs_ui.TMP / "old" / f"{label}.zip")
    results = []
    for tag, exe in hs_ui.select(blenders, a.blender):
        d = hs_ui.TMP / "roundf" / tag
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        for label in ("b1", "v100", "new"):
            run(tag, exe, f"{tag}-{label}", zips[label], "round_f_make", {"label": label, "out": str(d)},
                results, f"make with {label}")
        for label in ("b1", "v100"):
            run(tag, exe, f"{tag}-new", zips["new"], "round_f_check", {"src": str(d), "label": label},
                results, f"new opens {label} files")
        # going back to beta 1: it can't play format 3 steps, but opening and saving must not
        # destroy them (the new build then checks every position of the copies beta 1 saved)
        down = d / "down"
        run(tag, exe, f"{tag}-b1", zips["b1"], "round_f_downgrade", {"src": str(d), "out": str(down)},
            results, "b1 opens and saves new files")
        run(tag, exe, f"{tag}-new", zips["new"], "round_f_check", {"src": str(down), "label": "new"},
            results, "new reads them back")
        # same scripted session on every build, compared step by step
        sessions = {}
        for label in ("b1", "v100", "new"):
            p = d / f"{label}.json"
            if p.is_file():
                # The scripted session (seeded edits). Mouse strokes are not compared across
                # processes: their dabs depend on event timing, so meshes differ run to run.
                sessions[label] = json.loads(p.read_text()).get("scripted", [])
        same_len = len({len(h) for h in sessions.values()}) == 1
        per_step = [len({h[i] for h in sessions.values()}) == 1 for i in range(min(map(len, sessions.values())))] \
            if sessions else []
        ok = same_len and len(sessions) == 3 and all(per_step)
        print(f"{'ok  ' if ok else 'FAIL'} {'same session, 3 builds':28s} {tag:8s} steps={[len(h) - 1 for h in sessions.values()]} "
              f"identical meshes at {sum(per_step)}/{len(per_step)} positions", flush=True)
        results.append({"blender": tag, "run": "same session compare", "ok": ok, "identical_positions": sum(per_step),
                        "positions": len(per_step)})
    (hs_ui.TMP / "roundf" / "summary.json").write_text(json.dumps(results, indent=1))
    bad = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(bad)}/{len(results)} Round F runs passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
