"""tests/runner.py is the one test loop: PASS / FAIL lines that run_tests.py counts, no bpy. harness.py re-exports it."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import contextlib
import io

import tests.runner as R


def test_pass_and_fail_lines():
    def passes():
        pass

    def fails():
        raise ValueError("boom")

    out, err = io.StringIO(), io.StringIO()
    before = list(R.failures)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            R.run(passes)
            R.run(fails)
        assert out.getvalue().splitlines() == ["PASS passes", "FAIL fails"], out.getvalue()
        assert "ValueError: boom" in err.getvalue()
        assert R.failures == before + ["fails"]
    finally:
        R.failures[:] = before


def test_needs_no_bpy():
    with open(R.__file__, encoding="utf-8") as f:
        assert "bpy" not in f.read().replace("without bpy", "")


def test_harness_re_exports_it():
    with open(os.path.join(os.path.dirname(__file__), "harness.py"), encoding="utf-8") as f:
        assert "run, finish = runner.run, runner.finish" in f.read()


if __name__ == "__main__":
    for t in (test_pass_and_fail_lines, test_needs_no_bpy, test_harness_re_exports_it):
        R.run(t)
    R.finish()
