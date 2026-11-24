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


def test_skipped_test_prints_skip_not_pass():
    def skips():
        raise R.Skip("no hypothesis")

    out = io.StringIO()
    before = list(R.failures)
    with contextlib.redirect_stdout(out):
        R.run(skips)
    assert out.getvalue().splitlines() == ["SKIP skips: no hypothesis"], out.getvalue()
    assert R.failures == before


def test_hypothesis_tests_skip_without_hypothesis():
    import importlib
    saved = sys.modules.get("hypothesis")
    sys.modules["hypothesis"] = None   # makes `import hypothesis` raise ImportError
    try:
        for name, fn in (("tests.test_core_same_values", "test_hypothesis"),
                         ("tests.test_core_native_steps", "test_property")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                R.run(getattr(importlib.import_module(name), fn))
            assert "PASS" not in out.getvalue() and out.getvalue().startswith("SKIP " + fn + ":"), (name, out.getvalue())
    finally:
        if saved is None:
            del sys.modules["hypothesis"]
        else:
            sys.modules["hypothesis"] = saved


def test_needs_no_bpy():
    with open(R.__file__, encoding="utf-8") as f:
        assert "bpy" not in f.read().replace("without bpy", "")


def test_harness_re_exports_it():
    with open(os.path.join(os.path.dirname(__file__), "harness.py"), encoding="utf-8") as f:
        assert "run, finish = runner.run, runner.finish" in f.read()


if __name__ == "__main__":
    for t in (test_pass_and_fail_lines, test_skipped_test_prints_skip_not_pass, test_hypothesis_tests_skip_without_hypothesis,
              test_needs_no_bpy, test_harness_re_exports_it):
        R.run(t)
    R.finish()
