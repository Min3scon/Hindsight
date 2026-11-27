"""Where Hindsight's memory goes, inside Blender (bpy as a Python module, headless).

Run: <bpy python> bench/mem_profile.py      (HS_MEM_QUICK=1: fewer strokes)
Prints a table and writes bench/results_mem.json.

Sessions: 100k vertices with 6,000 strokes and 1M vertices with 300 strokes, each touching about 2%
of the vertices (bench_bl.Stroker: contiguous, smooth falloff, 5% noise), newest 64 steps raw.
Process memory is RSS (VmRSS) and the peak (VmHWM, reset through /proc/self/clear_refs before each
measured operation). Python-side numbers come from tracemalloc (numpy reports its buffers to it) and
sys.getsizeof; Blender's own C allocations (the mesh, ID properties) show only in RSS.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gc
import json
import shutil
import tempfile
import time
import tracemalloc

import bpy
import numpy as np
import tests.harness as H
from bench.bench_bl import Stroker, environment, make_mesh
from hindsight.bl import meshio, runtime
from hindsight.core.types import SETTINGS

C = bpy.context
HERE = os.path.dirname(os.path.abspath(__file__))
QUICK = bool(os.environ.get("HS_MEM_QUICK"))
MB = 1 << 20


def proc(field):
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith(field + ":"):
                return int(line.split()[1]) * 1024
    return 0


def reset_peak():
    try:
        with open("/proc/self/clear_refs", "w") as fh:
            fh.write("5")
        return True
    except OSError:
        return False


class Peak:
    """tracemalloc peak above the level at start, and the RSS peak above the RSS at start."""

    def __enter__(self):
        gc.collect()
        self.rss0 = proc("VmRSS")
        self.hwm_ok = reset_peak()
        tracemalloc.start()
        tracemalloc.reset_peak()
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self.t0) * 1000
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.py_peak = peak            # tracked from start, so this is the transient extra
        self.py_kept = cur
        self.rss_peak = proc("VmHWM") - self.rss0 if self.hwm_ok else None
        self.rss_after = proc("VmRSS") - self.rss0
        return False

    def row(self):
        return {"ms": round(self.ms, 1), "py_peak_extra": self.py_peak, "py_kept": self.py_kept,
                "rss_peak_extra": self.rss_peak, "rss_after_extra": self.rss_after}


def drain():
    if runtime.packer is not None:
        runtime.packer.drain(300)


def entry_overhead(entries):
    """Bytes of the Python objects around the step data (Entry, meta dict, its values, extra dict),
    each object counted once even when shared between entries. Blob and raw steps excluded."""
    seen = set()
    total = 0

    def add(o):
        nonlocal total
        if id(o) in seen:
            return
        seen.add(id(o))
        total += sys.getsizeof(o)
        if isinstance(o, dict):
            for k, v in o.items():
                add(k)
                add(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                add(v)
    for e in entries:
        add(e)
        add(e.meta)
    # blobs: bytes object header beyond the data
    hdr = sum(sys.getsizeof(e.blob) - len(e.blob) for e in entries if e.blob is not None)
    return total, hdr


def breakdown(h):
    packed = [e for e in h.entries if e.step is None]
    raw = [e for e in h.entries if e.step is not None]
    blob_bytes = sum(len(e.blob) for e in packed)
    raw_arrays = sum(d.idx.nbytes + d.vals.nbytes for e in raw for d in e.step.deltas)
    raw_snaps = sum((e.step.before.nbytes() if e.step.before is not None else 0)
                    + (e.step.after.nbytes() if e.step.after is not None else 0) for e in raw)
    ovh, blob_hdr = entry_overhead(h.entries)
    # raw step Python objects (Step, ChannelDelta, ndarray headers)
    raw_obj = sum(sys.getsizeof(e.step) + sys.getsizeof(getattr(e.step, "__dict__", ()))
                  + sum(sys.getsizeof(d) + sys.getsizeof(getattr(d, "__dict__", ())) + sys.getsizeof(d.idx) - d.idx.nbytes
                        + sys.getsizeof(d.vals) - d.vals.nbytes for d in e.step.deltas) for e in raw)
    n = max(1, len(h.entries))
    return {"steps": len(h.entries), "packed_steps": len(packed), "raw_steps": len(raw),
            "packed_blob_bytes": blob_bytes, "packed_bytes_per_step": blob_bytes / max(1, len(packed)),
            "raw_array_bytes": raw_arrays, "raw_snapshot_bytes": raw_snaps, "raw_object_bytes": raw_obj,
            "entry_overhead_bytes": ovh + blob_hdr, "entry_overhead_per_1000": (ovh + blob_hdr) * 1000 / n}


def state_bytes():
    out = {}
    for uid, st in runtime._STATES.items():
        topo = sum(a.nbytes for k, a in st.arrays.items() if k in meshio.TOPO_KEYS)
        other = sum(a.nbytes for k, a in st.arrays.items() if k not in meshio.TOPO_KEYS)
        out[uid] = {"topology": topo, "channels": other}
    return out


def bufs_bytes():
    return sum(b.nbytes for b in meshio._bufs.values())


def entries_overhead_after_load(h):
    ovh, hdr = entry_overhead(h.entries)
    return (ovh + hdr) * 1000 / max(1, len(h.entries))


def session(n, strokes, tmp, two_objects=False):
    H.fresh()
    SETTINGS.max_steps = 10000
    SETTINGS.memory_limit_mb = 4096
    SETTINGS.raw_steps = 64
    SETTINGS.compression = "auto"
    gc.collect()
    r = {"verts_requested": n, "strokes": strokes, "rss_start": proc("VmRSS")}
    ob = make_mesh(n)
    H.enter(ob, "SCULPT")
    r["verts"] = len(ob.data.vertices)
    gc.collect()
    r["rss_mesh"] = proc("VmRSS")
    sk = Stroker(ob)
    r["stroke_verts"] = sk.k
    t0 = time.perf_counter()
    for i in range(strokes):
        sk.stroke_commit()
        if i % 64 == 63:
            drain()
    drain()
    r["record_s"] = time.perf_counter() - t0
    gc.collect()
    h = runtime.history_of(ob)
    r["rss_recorded"] = proc("VmRSS")
    r["history"] = breakdown(h)
    r["states"] = state_bytes()
    r["meshio_buffers"] = bufs_bytes()
    r["sculpt_stroker_copy"] = sk.co.nbytes      # the benchmark's own position copy (not Hindsight)
    print(f"\n== {r['verts']:,} verts, {strokes} strokes of {sk.k:,} verts, recorded in {r['record_s']:.0f} s")

    # one commit
    sk.stroke()
    with Peak() as p:
        runtime.commit(ob)
    r["commit"] = p.row()
    # one-step and long jumps
    with Peak() as p:
        runtime.goto(C, len(h.entries) - 1)
    r["jump_1"] = p.row()
    runtime.goto(C, len(h.entries))
    with Peak() as p:
        runtime.goto(C, 0)
    r["jump_all_back"] = p.row()
    with Peak() as p:
        runtime.goto(C, len(h.entries))
    r["jump_all_forward"] = p.row()
    drain()
    hist_bytes = sum(e.nbytes() for e in h.entries)
    r["history_bytes"] = hist_bytes

    # topology steps: three remesh-like changes at the end (counts change -> TOPO)
    if not QUICK or n <= 200_000:
        r["topology"] = topo_steps(ob, n)
        ob = bpy.data.objects["Obj"]
        h = runtime.history_of(ob)

    # save / load in each mode
    r["save"] = {}
    r["load"] = {}
    mode = "EMBED"  # #48: every save puts the history in the .blend (V1's name for that in the keys)
    drain()
    blend = os.path.join(tmp, f"m_{mode}.blend")
    with Peak() as p:
        bpy.ops.wm.save_as_mainfile(filepath=blend)
    r["save"][mode] = p.row()
    r["save"][mode]["blend_bytes"] = os.path.getsize(blend)
    with Peak() as p:
        bpy.ops.wm.open_mainfile(filepath=blend)
    r["load"][mode] = p.row()
    ob = bpy.data.objects["Obj"]
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == r["history"]["steps"] + len(r.get("topology", {}).get("steps", [])) + 1, \
        (mode, None if h is None else len(h.entries))
    r["load"][mode]["entry_overhead_per_1000"] = entries_overhead_after_load(h)
    r["history_bytes_at_save"] = sum(e.nbytes() for e in h.entries)
    gc.collect()
    r["rss_end"] = proc("VmRSS")
    r["rss_hwm"] = proc("VmHWM")
    if two_objects:
        r["two_objects"] = two(ob)
    return r


def rebuild(ob, n):
    """Give ob's mesh a new lat/long grid of about n vertices (Object Mode), as a remesh would."""
    tmp = make_mesh(n, name="T")
    me = ob.data
    src = tmp.data
    co = np.empty(len(src.vertices) * 3, np.float32)
    src.vertices.foreach_get("co", co)
    cv = np.empty(len(src.loops), np.int32)
    src.loops.foreach_get("vertex_index", cv)
    ls = np.empty(len(src.polygons), np.int32)
    src.polygons.foreach_get("loop_start", ls)
    bpy.data.objects.remove(tmp)
    bpy.data.meshes.remove(src)
    me.clear_geometry()
    me.vertices.add(co.size // 3)
    me.vertices.foreach_set("co", co)
    me.loops.add(cv.size)
    me.loops.foreach_set("vertex_index", cv)
    me.polygons.add(ls.size)
    me.polygons.foreach_set("loop_start", ls)
    me.update(calc_edges=True)
    H.enter(ob, "OBJECT")


def topo_steps(ob, n):
    """Rebuild the mesh with a slightly different grid in Object Mode, as a remesh would, and commit."""
    H.enter(ob, "OBJECT")
    out = {"steps": []}
    for k in range(3):
        rebuild(ob, n + 1000 * (k + 1))
        with Peak() as p:
            st = runtime.commit(ob, label="Remesh", mode="SCULPT", full=True)
        assert st is not None and st.kind == "TOPO", st
        row = p.row()
        row["before_bytes"] = st.before.nbytes()
        row["after_bytes"] = st.after.nbytes()
        out["steps"].append(row)
    drain()
    h = runtime.history_of(ob)
    out["raw_bytes_each"] = [e.nbytes() for e in h.entries[-3:]]
    blobs = [len(e.to_bytes(SETTINGS.compression)) for e in h.entries[-3:]]
    out["packed_bytes_each"] = blobs
    H.enter(ob, "SCULPT")
    return out


def two(ob):
    """A second object with a history: is the idle object's state kept, and how big is it."""
    H.enter(ob, "SCULPT")
    b = make_mesh(100_000, name="B")
    H.enter(b, "SCULPT")
    sk = Stroker(b, seed=3)
    for _ in range(20):
        sk.stroke_commit()
    H.enter(b, "OBJECT")
    H.enter(ob, "SCULPT")
    drain()
    return {"states": state_bytes(), "idle_kept": runtime.obj_uid(b) in runtime._STATES}


def fmt(b):
    return "-" if b is None else f"{b / MB:,.1f} MB"


def report(env, results):
    lines = []
    for r in results:
        hb = r["history"]
        st = sum(v["topology"] + v["channels"] for v in r["states"].values())
        stt = sum(v["topology"] for v in r["states"].values())
        lines.append(f"\n{r['verts']:,} vertices, {r['strokes']} strokes of {r['stroke_verts']:,} vertices")
        lines.append(f"  RSS: start {fmt(r['rss_start'])}, with mesh {fmt(r['rss_mesh'])}, recorded "
                     f"{fmt(r['rss_recorded'])}, end {fmt(r['rss_end'])}, peak {fmt(r['rss_hwm'])}")
        rows = [
            ("packed step data (blobs)", hb["packed_blob_bytes"]),
            (f"raw tier ({hb['raw_steps']} steps, arrays)", hb["raw_array_bytes"] + hb["raw_snapshot_bytes"]),
            ("raw tier Python objects", hb["raw_object_bytes"]),
            (f"Entry/meta objects ({hb['entry_overhead_per_1000']:,.0f} B per 1,000 steps... per step "
             f"{hb['entry_overhead_per_1000'] / 1000:,.0f} B)", hb["entry_overhead_bytes"]),
            (f"MeshState baseline (topology {fmt(stt)})", st),
            ("meshio read buffers", r["meshio_buffers"]),
        ]
        for name, b in rows:
            lines.append(f"  {name:<70} {fmt(b):>12}")
        lines.append(f"  packed bytes per step: {hb['packed_bytes_per_step']:,.0f}")
        lines.append(f"  Entry overhead per step after load: "
                     f"{r['load']['EMBED']['entry_overhead_per_1000'] / 1000:,.0f} B")
        for name in ("commit", "jump_1", "jump_all_back", "jump_all_forward"):
            p = r[name]
            lines.append(f"  transient {name:<18} python peak {fmt(p['py_peak_extra']):>10}, RSS peak "
                         f"{fmt(p['rss_peak_extra']):>10}, {p['ms']:.0f} ms")
        hs = r["history_bytes_at_save"]
        for kind in ("save", "load"):
            for mode, p in r[kind].items():
                lines.append(f"  {kind} {mode:<8} python peak {fmt(p['py_peak_extra']):>10} "
                             f"({p['py_peak_extra'] / hs:.2f}x history), RSS peak {fmt(p['rss_peak_extra']):>10}"
                             f" ({(p['rss_peak_extra'] or 0) / hs:.2f}x), {p['ms']:.0f} ms")
        if "topology" in r:
            for s in r["topology"]["steps"]:
                lines.append(f"  topology step: before {fmt(s['before_bytes'])} + after {fmt(s['after_bytes'])},"
                             f" commit python peak {fmt(s['py_peak_extra'])}, kept {fmt(s['py_kept'])}")
            lines.append(f"  topology steps packed: {', '.join(fmt(b) for b in r['topology']['packed_bytes_each'])}")
        if "two_objects" in r:
            t = r["two_objects"]
            lines.append(f"  two objects: idle object's state kept = {t['idle_kept']}; states "
                         + ", ".join(fmt(v['topology'] + v['channels']) for v in t["states"].values()))
    text = "\n".join(lines)
    print(f"\nBlender {env['bpy']}, Python {env['python']}, {env['cpu']}")
    print(text)
    return text


def main():
    env = environment()
    tmp = tempfile.mkdtemp(prefix="hs_mem_")
    plan = [(100_000, 600 if QUICK else 6000, True), (1_000_000, 60 if QUICK else 300, False)]
    results = []
    try:
        for n, s, two_ in plan:
            results.append(session(n, s, tmp, two_objects=two_))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    text = report(env, results)
    if not QUICK:
        with open(os.path.join(HERE, "results_mem.json"), "w") as fh:
            json.dump({"environment": env, "results": results, "table": text}, fh, indent=1, default=str)
        print("\nwrote bench/results_mem.json")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        H.finish(1)
    H.finish(0)
