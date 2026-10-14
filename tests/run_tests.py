"""Run every tests/test_*.py in its own subprocess. Usage: python tests/run_tests.py [substring]

Uses $HINDSIGHT_PY if set, else /home/user/venvs/bpy42/bin/python if it exists, else the current
interpreter (bpy tests need Blender's Python module `bpy`; or run them with
`blender -b --python tests/test_x.py`).
"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else ""
    py = os.environ.get("HINDSIGHT_PY")
    if not py:
        cand = "/home/user/venvs/bpy42/bin/python"
        py = cand if os.path.exists(cand) else sys.executable
    files = sorted(f for f in glob.glob(os.path.join(HERE, "test_*.py")) if pat in os.path.basename(f))
    failed = []
    t0 = time.time()
    for f in files:
        t = time.time()
        r = subprocess.run([py, f], capture_output=True, text=True, timeout=1800)
        out = r.stdout + r.stderr
        npass = out.count("PASS ")
        nfail = out.count("FAIL ")
        ok = r.returncode == 0 and nfail == 0
        print(f"{'ok  ' if ok else 'FAIL'} {os.path.basename(f):32s} pass={npass} fail={nfail} "
              f"rc={r.returncode} {time.time() - t:.1f}s")
        if not ok:
            failed.append(f)
            print("\n".join("    " + line for line in out.strip().splitlines()[-25:]))
    print(f"{len(files) - len(failed)}/{len(files)} files passed in {time.time() - t0:.1f}s")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
