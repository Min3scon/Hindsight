"""#129: bench/suite.py runs on Windows, which has no os.getloadavg (#89's open point).
Run: <bpy python> tests/test_bench_suite.py"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.argv = [sys.argv[0]]
import tests.harness as H  # noqa: E402

spec = importlib.util.spec_from_file_location("bench_suite", os.path.join(ROOT, "bench", "suite.py"))
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


def test_loadavg_is_none_without_getloadavg():
    saved = getattr(os, "getloadavg", None)
    if saved is not None:
        del os.getloadavg
    try:
        assert suite.loadavg() is None
    finally:
        if saved is not None:
            os.getloadavg = saved


def test_saveload_bench_runs_without_proc():
    """bench/saveload_bench.py (the saveload group) reports a result where /proc does not exist (Windows)."""
    import subprocess
    import tempfile
    p = subprocess.run([sys.executable, os.path.join(ROOT, "bench", "saveload_bench.py"), ROOT, "2000", "5", "1"],
                       capture_output=True, text=True, timeout=600, env=dict(os.environ, HS_BENCH_CACHE=tempfile.mkdtemp()))
    assert any(x.startswith("RESULT ") for x in p.stdout.splitlines()), (p.stderr or p.stdout)[-400:]


H.run(test_loadavg_is_none_without_getloadavg)
H.run(test_saveload_bench_runs_without_proc)
H.finish()
