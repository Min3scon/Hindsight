#!/usr/bin/env python3
"""Run the test suite (tests/run_tests.py) in real Blender or with a bpy module, keep a full log.

    python tools/ci/run_suite.py --blender ~/blender/blender-5.3.0-linux-x64/blender --log out/log.txt
    python tools/ci/run_suite.py --python /home/user/venvs/bpy53/bin/python core_codec
    python tools/ci/run_suite.py --python PY --known-fail test_bl_t1_migrate.py

A positional argument is a substring of test file names (what run_tests.py takes). HINDSIGHT_NATIVE and
the rest of the environment pass through. --known-fail NAME (repeatable) names a test file whose failure
is reported as "known" and does not fail the run; the file still runs and its log is kept. Use it only
for failures that come from the machine (a missing file or Blender binary), never to hide a bug.
Writes a Markdown summary to $GITHUB_STEP_SUMMARY when that is set. Exit 0 all good, 1 failures,
2 nothing ran.
"""
import argparse
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LINE = re.compile(r"^(ok  |FAIL) \[(?P<label>[^\]]*)\] (?P<file>\S+)\s+pass=(?P<p>\d+) fail=(?P<f>\d+) rc=(?P<rc>\S+) (?P<t>[\d.]+)s")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--blender", help="path of the blender binary (tests run inside it)")
    g.add_argument("--python", help="a Python that has bpy installed (default: this one)")
    ap.add_argument("filter", nargs="?", default="", help="substring of test file names")
    ap.add_argument("--skip", action="append", default=[], help="leave out files whose name has this")
    ap.add_argument("--known-fail", action="append", default=[], metavar="FILE")
    ap.add_argument("--log", help="write the complete output here too")
    a = ap.parse_args(argv)

    cmd = [sys.executable, "-u", os.path.join(ROOT, "tests", "run_tests.py")]
    env = dict(os.environ)
    if a.blender:
        cmd += ["--blender", os.path.abspath(a.blender)]
        mode = "blender " + os.path.basename(os.path.dirname(os.path.abspath(a.blender)))
    else:
        env["HINDSIGHT_PY"] = a.python or sys.executable
        mode = "module " + env["HINDSIGHT_PY"]
    for s in a.skip:
        cmd += ["--skip", s]
    if a.filter:
        cmd.append(a.filter)
    print("run:", " ".join(cmd), "| HINDSIGHT_NATIVE=" + env.get("HINDSIGHT_NATIVE", ""), flush=True)

    log = None
    if a.log:
        os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
        log = open(a.log, "w", encoding="utf-8", errors="replace")
    t0 = time.time()
    rows = []
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    for line in p.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        if log:
            log.write(line)
        m = LINE.match(line)
        if m:
            rows.append(m)
    p.wait()
    if log:
        log.close()

    known = set(a.known_fail)
    bad = [m for m in rows if m.group(1) == "FAIL" and m.group("file") not in known]
    kfail = [m for m in rows if m.group(1) == "FAIL" and m.group("file") in known]
    npass = sum(int(m.group("p")) for m in rows)
    secs = time.time() - t0
    md = [f"### {mode}", "",
          f"{len(rows) - len(bad) - len(kfail)}/{len(rows)} files ok, {npass} PASS lines, {secs:.0f} s, "
          f"HINDSIGHT_NATIVE={env.get('HINDSIGHT_NATIVE', '(unset)')}", ""]
    if bad:
        md += ["Failed files:"] + [f"- `{m.group('file')}` (pass={m.group('p')} fail={m.group('f')} rc={m.group('rc')})"
                                   for m in bad] + [""]
    if kfail:
        md += ["Known failures (the machine lacks something, see the workflow comment):"] + \
              [f"- `{m.group('file')}`" for m in kfail] + [""]
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")
    print("\n".join(md))
    if not rows:
        print("no test file ran (bad filter?)")
        return 2
    return 1 if bad or (p.returncode not in (0, 1)) else 0


if __name__ == "__main__":
    sys.exit(main())
