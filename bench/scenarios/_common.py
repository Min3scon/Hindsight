"""Shared helpers of the benchmark scenarios (bench/scenarios/*.py).

Every scenario script is run as

    <python with bpy> bench/scenarios/<name>.py CODE_ROOT [--quick] [--only a,b] [--reps N]

CODE_ROOT is the checkout whose `hindsight` package (and `tests/harness.py`) is measured. The
script prints ONE JSON object on its last line:

    {"results": {"<scenario>": {"min_ms": .., "median_ms": .., "n": .., "tracemalloc_mb": ..,
                                "hwm_mb": .., ...}},
     "notes": {...}}

A scenario that cannot run on that code (it lacks something) is {"error": "..."} and the suite
reports it as "not available", never as a number. Times are milliseconds unless "unit" says so.
"""
import contextlib
import importlib.util
import json
import os
import statistics
import sys
import time
import traceback

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKOUT = os.path.dirname(BENCH)          # the checkout this file lives in (not necessarily ROOT)


class Opts:
    def __init__(self, argv=None):
        argv = list(sys.argv[1:] if argv is None else argv)
        if not argv or argv[0].startswith("--"):
            raise SystemExit("usage: script.py CODE_ROOT [--quick] [--only a,b] [--reps N]")
        self.root = os.path.abspath(argv[0])
        rest = argv[1:]
        self.quick = "--quick" in rest
        self.only = []
        self.reps = None
        if "--only" in rest:
            self.only = [x for x in rest[rest.index("--only") + 1].split(",") if x]
        if "--reps" in rest:
            self.reps = int(rest[rest.index("--reps") + 1])
        self.rest = rest

    def want(self, *names):
        """True when no --only was given or any given pattern is a substring of one of the names."""
        return not self.only or any(p in n for p in self.only for n in names)

    def n(self, normal, quick):
        return self.reps or (quick if self.quick else normal)


def use_root(root):
    """Put the code under test first on sys.path (and make it the working directory)."""
    sys.path.insert(0, root)
    os.chdir(root)


def import_checked(root):
    """Import hindsight from root and fail loudly if another copy was picked up."""
    import hindsight
    assert os.path.abspath(hindsight.__file__).startswith(root), hindsight.__file__
    return hindsight


def load_bench_module(name):
    """Load bench/<name>.py of THIS checkout (the shared helpers: Stroker, make_mesh, ...) without
    letting it change the import path of the code under test."""
    path = os.path.join(BENCH, name + ".py")
    spec = importlib.util.spec_from_file_location("hsbench_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
    return mod


def stats(samples, unit="ms", **extra):
    out = {"min_ms": round(min(samples), 4), "median_ms": round(statistics.median(samples), 4),
           "n": len(samples)}
    if unit != "ms":
        out["unit"] = unit
    out.update(extra)
    return out


def proc(field):
    with contextlib.suppress(OSError):
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith(field + ":"):
                    return int(line.split()[1]) * 1024
    return 0


def reset_hwm():
    try:
        with open("/proc/self/clear_refs", "w") as fh:
            fh.write("5")
        return True
    except OSError:
        return False


class Peak:
    """Time and peak memory of a block: tracemalloc (Python and numpy allocations) and the process
    peak (VmHWM over the RSS before, reset through /proc/self/clear_refs, so native allocations
    show). tracemalloc slows code down: use `time_it` and `mem_of` separately when time matters."""

    def __enter__(self):
        import gc
        import tracemalloc
        gc.collect()
        self.rss0 = proc("VmRSS")
        reset_hwm()
        tracemalloc.start()
        tracemalloc.reset_peak()
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        import tracemalloc
        self.ms = (time.perf_counter() - self.t0) * 1000
        _cur, py = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.tracemalloc_mb = py / 2 ** 20
        self.hwm_mb = max(0, proc("VmHWM") - self.rss0) / 2 ** 20
        return False


def mem_of(fn, reps=3, prepare=None):
    """{tracemalloc_mb, hwm_mb}: the worst of `reps` calls of fn. Not timed."""
    tm, hw = 0.0, 0.0
    for _ in range(reps):
        if prepare:
            prepare()
        with Peak() as p:
            fn()
        tm, hw = max(tm, p.tracemalloc_mb), max(hw, p.hwm_mb)
    return {"tracemalloc_mb": round(tm, 3), "hwm_mb": round(hw, 3)}


def time_it(fn, reps, prepare=None, inner=1):
    """ms per call of fn, one sample per rep (`inner` calls per sample)."""
    out = []
    for _ in range(reps):
        if prepare:
            prepare()
        t = time.perf_counter()
        for _ in range(inner):
            fn()
        out.append((time.perf_counter() - t) * 1000 / inner)
    return out


def attempt(results, name, fn):
    """results[name] = fn(), or {"error": ...} when it raises (the old code may lack the feature)."""
    try:
        results[name] = fn()
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        results[name] = {"error": f"{type(e).__name__}: {e}"[:300]}


def native_loaded():
    try:
        from hindsight.core import native
        return native.ENGINE is not None
    except Exception:
        return False


def emit(results, **notes):
    notes.setdefault("native", native_loaded())
    sys.stdout.write("\n" + json.dumps({"results": results, "notes": notes}) + "\n")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
