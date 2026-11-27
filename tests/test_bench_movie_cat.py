"""#84: the movie benchmark files the split adapters package under "handler bookkeeping", not "other".
Run: <bpy python> tests/test_bench_movie_cat.py"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.argv = [sys.argv[0]]
import tests.harness as H  # noqa: E402

spec = importlib.util.spec_from_file_location("bench_movie", os.path.join(ROOT, "bench", "bench_movie.py"))
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)


def test_adapters_package_is_handler_bookkeeping():
    for mod in ("core.py", "objects.py", "modifiers.py"):
        fn = f"{ROOT}/hindsight/bl/adapters/{mod}"
        assert bm._cat((fn, 1, "f")) == "handler bookkeeping", mod


H.run(test_adapters_package_is_handler_bookkeeping)
H.finish()
