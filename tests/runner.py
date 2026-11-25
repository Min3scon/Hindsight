"""The shared test loop, without bpy: tests/run_tests.py counts its `PASS name` / `FAIL name` lines.

    import runner as R
    R.run(test_a); R.run(test_b)
    R.finish()
"""
import os
import sys
import traceback

failures = []
on_finish = []      # callables run by finish() before it exits (harness.py removes its temporary folders)


def run(fn):
    try:
        fn()
        print("PASS", fn.__name__)
    except Exception:
        traceback.print_exc()
        print("FAIL", fn.__name__)
        failures.append(fn.__name__)


def finish(code=None):
    if code is None:
        code = 1 if failures else 0
    if failures:
        print("FAILED:", ", ".join(failures))
    sys.stdout.flush()
    sys.stderr.flush()
    for f in on_finish:
        f()
    os._exit(code)
