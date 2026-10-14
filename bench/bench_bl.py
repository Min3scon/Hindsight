"""Benchmark of Hindsight inside real Blender (bpy 4.2 as a Python module, headless).

Run: /home/user/venvs/bpy42/bin/python bench/bench_bl.py
Writes bench/results_bl.json and bench/RESULTS.md. All numbers are measured on this machine.
Strokes are simulated (positions written with foreach_set, then runtime.commit), because sculpt
brush operators cannot run headless in bpy-module mode.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import importlib.util
import json
import platform
import shutil
import statistics
import tempfile
import time

import bpy
import numpy as np
import tests.harness as H
from hindsight.bl import runtime
from hindsight.core import codec, persist, store
from hindsight.core.types import SETTINGS

C = bpy.context
HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = (100_000, 500_000)
FRAC = 0.02
LONG_STEPS = 5000
LONG_BUDGET_S = 120.0


def read_pos(me):
    a = me.attributes["position"].data
    co = np.empty(len(a) * 3, np.float32)
    a.foreach_get("vector", co)
    return co


class Stroker:
    """Spatially contiguous index range (~2% of verts) pushed out radially with a smooth falloff."""

    def __init__(self, ob, seed=42):
        self.ob = ob
        self.me = ob.data
        self.co = read_pos(self.me)
        self.nv = self.co.size // 3
        self.k = int(self.nv * FRAC)
        self.rng = np.random.default_rng(seed)
        self.fall = np.sin(np.linspace(0, np.pi, self.k, dtype=np.float32)) ** 2

    def stroke(self):
        s = int(self.rng.integers(0, self.nv - self.k))
        p = self.co.reshape(-1, 3)
        noise = 1.0 + 0.05 * self.rng.standard_normal(self.k).astype(np.float32)
        p[s:s + self.k] *= (1.0 + 1e-3 * self.fall * noise)[:, None]
        self.me.attributes["position"].data.foreach_set("vector", self.co)
        self.me.update()

    def stroke_commit(self):
        self.stroke()
        t0 = time.perf_counter()
        st = runtime.commit(self.ob)
        dt = (time.perf_counter() - t0) * 1000.0
        assert st is not None
        return dt


def make_mesh(n, name="Obj"):
    """Closed-longitude lat/long grid on a sphere with ~n vertices, built with foreach_set (fast).
    (primitive_uv_sphere_add is super-linear in bmesh and takes minutes at 500k vertices.)"""
    rows = int(np.sqrt(n / 2))
    cols = n // rows
    th = np.repeat(np.linspace(0.05, np.pi - 0.05, rows), cols)
    ph = np.tile(np.linspace(0, 2 * np.pi, cols, endpoint=False), rows)
    co = np.stack([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)], 1).astype(np.float32)
    r = np.arange(rows - 1)[:, None]
    c = np.arange(cols)[None, :]
    a = r * cols + c
    b = r * cols + (c + 1) % cols
    quads = np.stack([a, b, b + cols, a + cols], -1).reshape(-1, 4).astype(np.int32)
    me = bpy.data.meshes.new(name)
    me.vertices.add(rows * cols)
    me.vertices.foreach_set("co", co.reshape(-1))
    me.loops.add(quads.size)
    me.loops.foreach_set("vertex_index", quads.reshape(-1))
    me.polygons.add(len(quads))
    me.polygons.foreach_set("loop_start", np.arange(0, quads.size, 4, dtype=np.int32))
    me.update(calc_edges=True)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob


def drain():
    if runtime.packer is not None:
        runtime.packer.drain(120)


def scrub(h, pairs, reps):
    """Median ms of runtime.goto for each (from, to) jump; goes to `from` first (untimed)."""
    ts = []
    for _ in range(reps):
        for a, b in pairs:
            if h.cursor != a:
                runtime.goto(C, a)
            t0 = time.perf_counter()
            runtime.goto(C, b)
            ts.append((time.perf_counter() - t0) * 1000.0)
            assert h.cursor == b
    return statistics.median(ts), len(ts)


def save_load(tmp, ob, h):
    """Returns dict with save/load timings and the method used."""
    blend = os.path.join(tmp, "bench.blend")
    out = {}
    if importlib.util.find_spec("hindsight.bl.persistence") is not None:
        try:
            SETTINGS.save_mode = "SIDECAR"
            want_len, want_cur = len(h.entries), h.cursor
            want_pos = read_pos(ob.data).tobytes()
            t0 = time.perf_counter()
            bpy.ops.wm.save_as_mainfile(filepath=blend)
            out["save_ms"] = (time.perf_counter() - t0) * 1000.0
            sc = os.path.join(persist.sidecar_dir(blend), persist.HSR_NAME)
            out["sidecar_bytes"] = os.path.getsize(sc) if os.path.isfile(sc) else None
            out["blend_bytes"] = os.path.getsize(blend)
            t0 = time.perf_counter()
            bpy.ops.wm.open_mainfile(filepath=blend)
            out["load_ms"] = (time.perf_counter() - t0) * 1000.0
            ob2 = bpy.data.objects.get("Obj")
            h2 = runtime.history_of(ob2) if ob2 is not None else None
            ok = h2 is not None and len(h2.entries) == want_len and h2.cursor == want_cur
            out["roundtrip_ok"] = bool(ok and read_pos(ob2.data).tobytes() == want_pos)
            out["method"] = ("bpy.ops.wm.save_as_mainfile + bpy.ops.wm.open_mainfile, save_mode=SIDECAR "
                             "(includes writing/reading the .blend itself)")
            return out
        except Exception as e:  # fall through to the direct measurement
            out = {"persistence_error": repr(e)}
    t0 = time.perf_counter()
    persist.save_sidecar(runtime.reg(), blend)
    out["save_ms"] = (time.perf_counter() - t0) * 1000.0
    out["sidecar_bytes"] = os.path.getsize(os.path.join(persist.sidecar_dir(blend), persist.HSR_NAME))
    t0 = time.perf_counter()
    reg2 = persist.load_sidecar(blend)
    out["load_ms"] = (time.perf_counter() - t0) * 1000.0
    h2 = reg2.histories[h.uid]
    out["roundtrip_ok"] = len(h2.entries) == len(h.entries) and h2.cursor == h.cursor
    out["method"] = ("hindsight.bl.persistence not present: core.persist.save_sidecar / load_sidecar "
                     "timed directly (history file only, no .blend I/O)")
    return out


def bench(n, tmp):
    H.fresh()
    SETTINGS.max_steps = 10000
    SETTINGS.memory_limit_mb = 4096
    SETTINGS.raw_steps = 64
    SETTINGS.compression = "auto"
    t0 = time.perf_counter()
    ob = make_mesh(n)
    assert not ob.data.validate(), "generated mesh invalid"
    r_build = (time.perf_counter() - t0) * 1000.0
    H.enter(ob, "SCULPT")
    me = ob.data
    r = {"verts_requested": n, "verts": len(me.vertices), "faces": len(me.polygons), "mesh_build_ms": r_build}
    sk = Stroker(ob)
    r["stroke_verts"] = sk.k
    print(f"\n== {r['verts']} verts, stroke = {sk.k} verts ({FRAC:.0%})")

    # ---- memory per step after 50 strokes (raw: newest 64 stay unpacked, so all 50 are raw)
    commit_ms = [sk.stroke_commit() for _ in range(50)]
    h = runtime.history_of(ob)
    drain()
    ms = runtime.memory_stats()
    r["mem_raw_bytes_per_step"] = ms["total_bytes"] / ms["steps"]
    r["baseline_bytes"] = ms["ram_bytes"] - ms["total_bytes"]
    SETTINGS.raw_steps = 0
    t0 = time.perf_counter()
    store.enforce_tiers(runtime.reg().all(), SETTINGS)       # synchronous packing
    r["pack_50_ms"] = (time.perf_counter() - t0) * 1000.0
    ms = runtime.memory_stats()
    r["mem_packed_bytes_per_step"] = ms["total_bytes"] / ms["steps"]
    SETTINGS.raw_steps = 64
    r["full_positions_bytes"] = r["verts"] * 12
    print(f"memory/step raw {r['mem_raw_bytes_per_step']:.0f} B, packed {r['mem_packed_bytes_per_step']:.0f} B")

    # ---- record to 300 strokes; recording overhead = median commit ms over all of them
    commit_ms += [sk.stroke_commit() for _ in range(250)]
    r["commit_ms_median"] = statistics.median(commit_ms)
    r["commit_ms_p90"] = float(np.percentile(commit_ms, 90))
    r["commit_samples"] = len(commit_ms)
    drain()
    assert len(h.entries) == 300 and h.cursor == 300
    print(f"commit median {r['commit_ms_median']:.2f} ms (p90 {r['commit_ms_p90']:.2f})")

    # ---- scrub latency (newest 64 raw, older packed)
    one = [(300, 299), (299, 300), (150, 149), (149, 150), (10, 9), (9, 10)]
    r["goto1_ms"], r["goto1_samples"] = scrub(h, one, 5)
    hund = [(300, 200), (200, 300), (200, 100), (100, 200), (100, 0), (0, 100)]
    r["goto100_ms"], r["goto100_samples"] = scrub(h, hund, 3)
    full = [(300, 0), (0, 300)]
    r["goto300_ms"], r["goto300_samples"] = scrub(h, full, 4)
    print(f"goto 1: {r['goto1_ms']:.2f} ms, 100: {r['goto100_ms']:.1f} ms, 300: {r['goto300_ms']:.1f} ms")
    runtime.goto(C, 300)

    # ---- save / load with 300 steps
    sl = save_load(tmp, ob, h)
    r["save_load_300"] = sl
    print(f"save {sl['save_ms']:.1f} ms, load {sl['load_ms']:.1f} ms ({sl.get('sidecar_bytes')} B) - {sl['method']}")
    ob = bpy.data.objects.get("Obj") or ob
    h = runtime.history_of(ob)

    # ---- 5000-step jumps (100k only, if recordable within the budget)
    r["goto5000_ms"] = "not measured"
    if n == 100_000 and h is not None and len(h.entries) == 300:
        if ob.mode != "SCULPT":
            H.enter(ob, "SCULPT")
        runtime.goto(C, len(h.entries))
        sk = Stroker(ob, seed=7)
        t0 = time.perf_counter()
        while len(h.entries) < LONG_STEPS and time.perf_counter() - t0 < LONG_BUDGET_S:
            commit_ms.append(sk.stroke_commit())
        rec_s = time.perf_counter() - t0
        r["record_to_5000_s"] = rec_s
        if len(h.entries) >= LONG_STEPS:
            drain()
            r["goto5000_ms"], r["goto5000_samples"] = scrub(h, [(LONG_STEPS, 0), (0, LONG_STEPS)], 2)
            ms = runtime.memory_stats()
            r["mem_5000_total_bytes"] = ms["total_bytes"]
            r["mem_5000_bytes_per_step"] = ms["total_bytes"] / ms["steps"]
            r["commit_ms_median_5000"] = statistics.median(commit_ms)
            print(f"goto 5000: {r['goto5000_ms']:.0f} ms (recorded in {rec_s:.0f}s)")
            sl = save_load(tmp, ob, h)
            r["save_load_5000"] = sl
            print(f"save(5000) {sl['save_ms']:.1f} ms, load {sl['load_ms']:.1f} ms ({sl.get('sidecar_bytes')} B)")
        else:
            r["goto5000_note"] = f"only {len(h.entries)} strokes recorded in {LONG_BUDGET_S:.0f}s"
    return r


def environment():
    cpu = "unknown"
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    return {
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "machine": platform.machine(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "bpy": bpy.app.version_string,
        "numpy": np.__version__,
        "codecs": codec.available(),
        "codec_used": codec.resolve("auto"),
        "mode": "headless bpy module (pip bpy), background, no GPU/viewport; simulated strokes",
    }


def fmt(v, nd=1):
    return v if isinstance(v, str) else f"{v:.{nd}f}"


def write_md(env, results):
    lines = ["# Hindsight benchmark results", "",
             "Measured with `bench/bench_core.py` (pure numpy storage engine) and `bench/bench_bl.py` "
             "(real Blender). **The Blender numbers are from the headless bpy module "
             f"(bpy {env['bpy']}, `import bpy` in Python {env['python']}), not the Blender GUI**: no viewport "
             "redraw, no sculpt PBVH rebuild cost, strokes simulated by writing vertex positions and calling "
             "`runtime.commit`. Expect the GUI to add its own redraw time on top of scrub latency. Test mesh: a "
             "lat/long quad grid on a sphere built with `foreach_set`, in Sculpt Mode.", "",
             "## Environment", "",
             f"- CPU: {env['cpu']} ({env['logical_cpus']} logical CPUs), {env['machine']}",
             f"- OS: {env['os']}",
             f"- Python {env['python']}, bpy {env['bpy']}, numpy {env['numpy']}",
             f"- Codecs available: {', '.join(env['codecs'])} (used: {env['codec_used']})", "",
             "## Blender (bpy module), one object, strokes of ~2% of the vertices", "",
             "| Metric | " + " | ".join(f"{r['verts']:,} verts" for r in results) + " |",
             "|---|" + "---|" * len(results)]

    def row(name, f):
        lines.append(f"| {name} | " + " | ".join(f(r) for r in results) + " |")
    row("Stroke size (verts)", lambda r: f"{r['stroke_verts']:,}")
    row("Memory per step, raw (bytes)", lambda r: f"{r['mem_raw_bytes_per_step']:,.0f}")
    row("Memory per step, packed (bytes)", lambda r: f"{r['mem_packed_bytes_per_step']:,.0f}")
    row("Full position array (bytes, for scale)", lambda r: f"{r['full_positions_bytes']:,}")
    row("Recording overhead: `runtime.commit` median (ms)", lambda r: fmt(r["commit_ms_median"], 2))
    row("Recording overhead: p90 (ms)", lambda r: fmt(r["commit_ms_p90"], 2))
    row("Scrub 1 step: `runtime.goto` median (ms)", lambda r: fmt(r["goto1_ms"], 2))
    row("Scrub 100 steps (ms)", lambda r: fmt(r["goto100_ms"]))
    row("Scrub 300 steps = whole history (ms)", lambda r: fmt(r["goto300_ms"]))
    row("Scrub 5,000 steps (ms)", lambda r: fmt(r["goto5000_ms"]))
    row("Save, 300 steps (ms)", lambda r: fmt(r["save_load_300"]["save_ms"]))
    row("Load, 300 steps (ms)", lambda r: fmt(r["save_load_300"]["load_ms"]))
    row("History file, 300 steps (bytes)", lambda r: f"{r['save_load_300'].get('sidecar_bytes') or 0:,}")
    lines.append("")
    for r in results:
        if "save_load_5000" in r:
            s = r["save_load_5000"]
            lines.append(f"- {r['verts']:,} verts, 5,000 steps: history {r['mem_5000_total_bytes'] / 2**20:.1f} MiB "
                         f"({r['mem_5000_bytes_per_step']:,.0f} B/step, newest 64 raw), save {s['save_ms']:.0f} ms, "
                         f"load {s['load_ms']:.0f} ms, file {s.get('sidecar_bytes', 0):,} B; recording 4,700 more "
                         f"strokes took {r['record_to_5000_s']:.0f} s including the simulated stroke writes.")
        elif r["goto5000_ms"] == "not measured":
            lines.append(f"- {r['verts']:,} verts: 5,000-step jump not measured (only attempted at 100k)"
                         + (f" ({r['goto5000_note']})." if "goto5000_note" in r else "."))
    lines.append(f"- Save/load method: {results[0]['save_load_300']['method']}.")
    lines += ["", "Definitions:", "",
              "- Memory per step = `runtime.memory_stats()['total_bytes'] / steps` after 50 strokes. Raw = deltas "
              "held as numpy arrays (the newest 64 steps stay raw by default); packed = after "
              "`core.store.enforce_tiers` with `raw_steps=0` (compressed).",
              "- Recording overhead = time spent inside `runtime.commit(obj)` after a stroke (read the sculpt "
              "channels, diff, push), median over all recorded strokes. It excludes the stroke itself.",
              "- Scrub latency = time of `runtime.goto(context, pos)` including writing the mesh attributes; "
              "the newest 64 steps are raw, older ones are packed and decompressed on the way.",
              "- Storage-engine numbers (pure numpy, no Blender) are in `bench/results_core.json`.", ""]
    with open(os.path.join(HERE, "RESULTS.md"), "w") as fh:
        fh.write("\n".join(lines))


def main():
    env = environment()
    results = []
    tmp = tempfile.mkdtemp(prefix="hs_bench_")
    try:
        for n in SIZES:
            results.append(bench(n, tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with open(os.path.join(HERE, "results_bl.json"), "w") as fh:
        json.dump({"environment": env, "results": results}, fh, indent=1)
    write_md(env, results)
    print("\nwrote bench/results_bl.json and bench/RESULTS.md")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        H.finish(1)
    H.finish(0)
