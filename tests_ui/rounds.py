#!/usr/bin/env python3
"""One full test pass: Rounds A-G on every Blender 4.2+ found, plus the headless suite.

    python tests_ui/rounds.py --pass 1 [--blender all] [--rounds ABCDEFG] [--big]

A registration and lifecycle       B UI in every mode, small/large/multi-object models
C real recording, bit-exact jumps  D awkward models and abuse
E the owner's files (copies)       F old versus new builds
G headless suite in every Blender (the real-UI rounds above already run on every Blender)

--big adds the 10,000-step session on the 100k model to Round C.
Writes .tmp/rounds/pass<N>/summary.json and summary.md; copies a few screenshots of generated
test models to test_rounds/pass<N>/ (WebP). Never copies anything from Round E (own files).
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hs_ui  # noqa: E402

ROOT = hs_ui.ROOT
GEOM = [40, 40, 1600, 900]

ROUNDS = {
    "A": [("install_gui", {}, "fresh-gui"), ("audit_register", {}, ""), ("lifecycle", {}, "fresh")],
    "B": [("ui_tour", {}, "fresh-gui"), ("round_b", {}, ""), ("regress_slider_visible", {}, "")],
    "C": [("round_c", {"model": "size_10k.blend", "obj": "Blob_10k", "jumps": "80"}, "", "10k"),
          ("round_c", {"model": "size_10k.blend", "obj": "Blob_10k", "ops": "0", "mouse": "4", "jumps": "20",
                       "session": "1000"}, "", "10k-1000"),
          ("round_c", {"model": "size_100k.blend", "obj": "Blob_100k", "jumps": "60"}, "", "100k"),
          ("round_c", {"model": "size_500k.blend", "obj": "Blob_500k", "jumps": "40", "ops": "24"}, "", "500k"),
          ("regress_stroke_split", {}, ""), ("regress_file_load", {}, ""), ("regress_native_undo", {}, "")],
    "D": [("round_d", {}, ""), ("fail_inject", {}, "")],
    "E": [("round_e", {"manifest": str(hs_ui.TMP / "own" / "manifest.json")}, "")],
}
# Screenshots worth keeping per scenario (generated test models only).
KEEP = {"ui_tour": ["C_sculpt_before_stroke", "D_sculpt_three_steps", "G_popover", "I_f3_search", "K_edit_mode",
                    "M_self_check", "N_preferences"],
        "round_b": ["size_1m_sculpt", "scene_many_sculpt"],
        "round_d": ["awkward_Multires", "dyntopo_warning", "damaged_history", "newer_format", "memory_limit_warning",
                    "large_mesh_2m"],
        "fail_inject": ["sidebar_reports_slider_off"]}


def recopy_own():
    lst = hs_ui.TMP / "own" / "list.txt"
    if not lst.is_file():
        return False
    dst = ROOT / "my_models_copies"
    if dst.exists():
        shutil.rmtree(dst)
    exe = hs_ui.find_blenders()[-1][1]
    subprocess.run([str(exe), "-b", "--factory-startup", "--python", str(HERE / "copy_own_models.py"), "--",
                    str(lst), str(dst), str(hs_ui.TMP / "own" / "manifest.json")],
                   capture_output=True, env=hs_ui.sandbox_env("models"))
    return True


def keep_shots(out, scenario, dest):
    for want in KEEP.get(scenario, []):
        for png in sorted((out / "shots").glob(f"*{want}*.png")):
            dest.mkdir(parents=True, exist_ok=True)
            webp = dest / (png.stem.split("_", 1)[1] + ".webp")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(png), "-quality", "80", str(webp)])
            break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="pass_no", type=int, required=True)
    ap.add_argument("--blender", default="all")
    ap.add_argument("--rounds", default="ABCDEFG")
    ap.add_argument("--big", action="store_true")
    a = ap.parse_args()
    blenders = hs_ui.select(hs_ui.find_blenders(), a.blender)
    hs_ui.build(hs_ui.find_blenders())
    pdir = hs_ui.TMP / "rounds" / f"pass{a.pass_no}"
    if pdir.exists():
        shutil.rmtree(pdir)
    pdir.mkdir(parents=True)
    shots_dest = ROOT / "test_rounds" / f"pass{a.pass_no}"
    results = []
    t0 = time.time()

    def record(rnd, name, tag, res, out=None, extra=None):
        checks = res.get("checks", [])
        fails = [c for c in checks if not c["ok"]]
        ok = bool(res.get("ok"))
        line = f"{'ok  ' if ok else 'FAIL'} {rnd} {name:26s} {tag:8s} {len(checks) - len(fails)}/{len(checks)}"
        print(line, flush=True)
        for c in fails:
            print(f"     x {c['name']}: {c.get('detail', '')[:300]}")
        if res.get("error"):
            print("     error:", str(res["error"])[-600:])
        results.append({"round": rnd, "run": name, "blender": tag, "ok": ok, "checks": len(checks),
                        "failed": [c["name"] + ": " + c.get("detail", "")[:200] for c in fails],
                        "error": (str(res.get("error"))[-400:] if res.get("error") else None),
                        "notes": extra if extra is not None else {}})

    big = list(ROUNDS["C"]) + ([("round_c", {"model": "size_100k.blend", "obj": "Blob_100k", "session": "10000",
                                               "jumps": "40", "ops": "0", "mouse": "4"}, "", "100k-10000")]
                                 if a.big else [])
    for rnd in a.rounds:
        if rnd in ROUNDS or rnd == "C":
            if rnd == "E" and not recopy_own():
                print("skip E: no own-file list")
                continue
            for item in (big if rnd == "C" else ROUNDS[rnd]):
                scen, args, mode = item[0], item[1], item[2]
                label = item[3] if len(item) > 3 else ""
                for tag, exe in blenders:
                    if "fresh" in mode:
                        hs_ui.wipe_profile(tag)
                    if "gui" not in mode:
                        hs_ui.cli_install(tag, exe, hs_ui.DEFAULT_ZIP)
                    out_tag = f"{tag}{'-' + label if label else ''}"
                    res, out = hs_ui.run_one(tag, exe, scen, dict(args), hs_ui.DEFAULT_ZIP, 3600, False, GEOM,
                                             out_tag=out_tag)
                    notes = res.get("notes", {})
                    keep = {k: v for k, v in notes.items() if k not in ("diagnostics", "abuse_log_tail", "console_tail",
                                                                     "own_files")}
                    if scen == "round_e":
                        # own files: numbers and measurements only, never names or paths
                        keep = {"own_files": [{k: v for k, v in r.items() if k not in ("warnings", "labels")}
                                              for r in notes.get("own_files", [])]}
                    record(rnd, scen + (f" ({label})" if label else ""), tag, res, out, keep)
                    if scen != "round_e" and tag == blenders[-1][0]:
                        keep_shots(out, scen, shots_dest)
        elif rnd == "F":
            r = subprocess.run([sys.executable, str(HERE / "round_f.py"), "--blender", a.blender],
                               capture_output=True, text=True)
            summ = json.loads((hs_ui.TMP / "roundf" / "summary.json").read_text()) if (
                hs_ui.TMP / "roundf" / "summary.json").is_file() else []
            for s in summ:
                res = {"ok": s["ok"], "checks": [{"name": s["run"], "ok": s["ok"], "detail": ""}] * max(1, s.get("checks", 1))}
                record("F", s["run"], s["blender"], res, extra={k: v for k, v in s.items()
                                                                if k in ("identical_positions", "positions")})
        elif rnd == "G":
            r = subprocess.run([sys.executable, str(ROOT / "tests" / "run_tests.py"), "--blender", a.blender],
                               capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if "files passed" in line:
                    tag = line.split("]")[0].strip("[")
                    ok = line.split()[1].split("/")[0] == line.split()[1].split("/")[1]
                    record("G", "headless suite", tag, {"ok": ok, "checks": [{"name": line, "ok": ok}]})
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print("   ", line)
    (pdir / "summary.json").write_text(json.dumps(results, indent=1))
    bad = [x for x in results if not x["ok"]]
    md = [f"Pass {a.pass_no}: {len(results) - len(bad)}/{len(results)} runs passed in {(time.time() - t0) / 60:.0f} min.", ""]
    for x in results:
        md.append(f"- {'ok' if x['ok'] else 'FAIL'} Round {x['round']} {x['run']} on {x['blender']}: "
                  f"{x['checks'] - len(x['failed'])}/{x['checks']} checks"
                  + (f". Failed: {'; '.join(x['failed'])[:400]}" if x["failed"] else "")
                  + (f". Error: {x['error'][:200]}" if x.get("error") else ""))
    (pdir / "summary.md").write_text("\n".join(md), encoding="utf-8")
    print(md[0])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
