"""Run every tests/test_*.py in its own subprocess.

    python tests/run_tests.py [substring]                       # $HINDSIGHT_PY or this interpreter (needs bpy)
    python tests/run_tests.py --blender PATH [substring]        # inside real Blender: blender -b --python
    python tests/run_tests.py --blender all [substring]         # every Blender 4.2+ found on this machine

With --blender the user's Blender settings are never read or written: BLENDER_USER_* point at a
throwaway folder under .tmp/ and --factory-startup is passed.
"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _sandbox_env(tag):
    env = dict(os.environ)
    base = os.path.join(ROOT, ".tmp", "profiles", "headless-" + tag)
    for key, sub in (("BLENDER_USER_RESOURCES", "resources"), ("BLENDER_USER_CONFIG", "config"),
                     ("BLENDER_USER_SCRIPTS", "scripts"), ("BLENDER_USER_EXTENSIONS", "extensions"),
                     ("BLENDER_USER_DATAFILES", "datafiles"), ("TEMP", "temp"), ("TMP", "temp")):
        p = os.path.join(base, sub)
        os.makedirs(p, exist_ok=True)
        env[key] = p
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_suite(cmd_for, env, files, label):
    failed = []
    t0 = time.time()
    for f in files:
        t = time.time()
        r = subprocess.run(cmd_for(f), capture_output=True, text=True, timeout=3600, env=env,
                           encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        npass = out.count("PASS ")
        nfail = out.count("FAIL ")
        ok = r.returncode == 0 and nfail == 0 and npass > 0
        print(f"{'ok  ' if ok else 'FAIL'} [{label}] {os.path.basename(f):32s} pass={npass} fail={nfail} "
              f"rc={r.returncode} {time.time() - t:.1f}s", flush=True)
        if not ok:
            failed.append(f)
            print("\n".join("    " + line for line in out.strip().splitlines()[-30:]))
    print(f"[{label}] {len(files) - len(failed)}/{len(files)} files passed in {time.time() - t0:.1f}s")
    return failed


def main():
    args = sys.argv[1:]
    blender = None
    if "--blender" in args:
        i = args.index("--blender")
        blender = args[i + 1]
        del args[i:i + 2]
    pat = args[0] if args else ""
    files = sorted(f for f in glob.glob(os.path.join(HERE, "test_*.py")) if pat in os.path.basename(f))

    failed = []
    if blender is None:
        py = os.environ.get("HINDSIGHT_PY") or sys.executable
        failed = run_suite(lambda f: [py, f], dict(os.environ), files, os.path.basename(py))
    else:
        if blender == "all":
            sys.path.insert(0, os.path.join(ROOT, "tests_ui"))
            import hs_ui
            targets = [(tag, str(exe)) for tag, exe in hs_ui.select(hs_ui.find_blenders(), "all")]
        else:
            targets = [(os.path.basename(os.path.dirname(blender)) or "blender", blender)]
        for tag, exe in targets:
            env = _sandbox_env(tag)
            failed += run_suite(lambda f: [exe, "-b", "--factory-startup", "--python-exit-code", "1",
                                           "--python", f], env, files, tag)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
