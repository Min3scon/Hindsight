"""#129: bench/suite.py, ab.py and their scenarios run on Windows: no os.getloadavg, no /proc, a coarse process_time,
the win_amd64 wheel (#89's open point).
Run: <bpy python> tests/test_bench_suite.py"""
import importlib.util
import os
import sys
import tempfile

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
    p = subprocess.run([sys.executable, os.path.join(ROOT, "bench", "saveload_bench.py"), ROOT, "2000", "5", "1"],
                       capture_output=True, text=True, timeout=600, env=dict(os.environ, HS_BENCH_CACHE=tempfile.mkdtemp()))
    assert any(x.startswith("RESULT ") for x in p.stdout.splitlines()), (p.stderr or p.stdout)[-400:]


def test_idle_clock_sees_a_millisecond():
    """The idle group's CPU clock sees 1 ms of work: Windows' process_time moves in 15.6 ms ticks, so there
    idle_60s and depsgraph_handler read 0 or 15.62 ms."""
    import time
    spec = importlib.util.spec_from_file_location("bench_common", os.path.join(ROOT, "bench", "scenarios",
                                                                                "_common.py"))
    K = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(K)
    for _ in range(20):
        t0 = K.cpu()
        end = time.perf_counter() + 0.001
        while time.perf_counter() < end:
            pass
        assert K.cpu() > t0


def test_native_env_puts_the_roots_own_wheel_first():
    """Each root runs the engine its own hindsight/wheels ships (so an A/B compares engines too), on Windows as on
    Linux."""
    env = suite.native_env(ROOT, {"PATH": os.environ.get("PATH", "")})
    first = env.get("PYTHONPATH", "").split(os.pathsep)[0]
    assert first and os.path.isdir(os.path.join(first, "hindsight_native")), env
    assert first.startswith(tempfile.gettempdir()), first


def test_ab_runs_where_there_is_no_load_average():
    """bench/ab.py logs the load average after each group: None on Windows, which has none."""
    import subprocess
    out = os.path.join(tempfile.mkdtemp(), "ab.json")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "bench", "ab.py"), "--a", ROOT, "--b", ROOT,
                        "--rounds", "1", "--quick", "--only", "idle_60s_1obj", "--py", sys.executable,
                        "--out", out], capture_output=True, text=True, timeout=600)
    assert p.returncode == 0 and os.path.isfile(out), (p.stderr or p.stdout)[-400:]


H.run(test_loadavg_is_none_without_getloadavg)
H.run(test_native_env_puts_the_roots_own_wheel_first)
H.run(test_ab_runs_where_there_is_no_load_average)
H.run(test_idle_clock_sees_a_millisecond)
H.run(test_saveload_bench_runs_without_proc)
H.finish()
