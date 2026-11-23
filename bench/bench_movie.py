"""Movie Mode playback cost: time scene.frame_set(f) over every frame of a Quick auto key movie
(what a render does before drawing each frame), split by cProfile into where the time goes.

    <venv>/bin/python bench/bench_movie.py [--quick] [--render-only]

Writes bench/results_movie.json. Histories are built as tests/test_bl_movie.py does (meshes made
with numpy + foreach_set, strokes of about 2% of the vertices written to the mesh, then commit).

The 300-frame grid (100k and 1M vertices, Object and Sculpt Mode, Steps per frame 1 and 8, plus
500 other objects) and a Cycles CPU render of 30 frames at 320 x 180 with 1 sample:

    bench/bench_movie.py --grid                 writes bench/results_movie_grid.json
    bench/bench_movie.py --case N MODE SPF [--extra K] [--root PATH]    one case, one JSON line
    bench/bench_movie.py --render-case [--root PATH]                    the render, one JSON line

--root times another checkout's `hindsight` (an A/B against an older commit: run the two
alternately, at least five rounds, and compare min and median).
"""
import cProfile
import json
import math
import os
import pstats
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if "--root" in sys.argv:            # time another checkout's add-on (A/B runs)
    sys.path.insert(0, os.path.abspath(sys.argv[sys.argv.index("--root") + 1]))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import tests.harness as H  # noqa: E402
from hindsight.bl import runtime

C = bpy.context
HERE = os.path.dirname(os.path.abspath(__file__))
QUICK = "--quick" in sys.argv


def grid_mesh(n_verts, name, x=0.0):
    side = int(round(math.sqrt(n_verts)))
    ys, xs = np.mgrid[0:side, 0:side].astype(np.float32)
    co = np.stack([xs / side, ys / side, np.zeros_like(xs)], -1).reshape(-1).astype(np.float32)
    q = side - 1
    i = np.arange(q)
    a = (i[None, :] + side * i[:, None]).reshape(-1)
    loops = np.stack([a, a + 1, a + side + 1, a + side], -1).reshape(-1).astype(np.int32)
    me = bpy.data.meshes.new(name)
    me.vertices.add(side * side)
    me.vertices.foreach_set("co", co)
    me.loops.add(len(loops))
    me.loops.foreach_set("vertex_index", loops)
    me.polygons.add(q * q)
    me.polygons.foreach_set("loop_start", np.arange(0, len(loops), 4, dtype=np.int32))
    me.update(calc_edges=True)
    ob = bpy.data.objects.new(name, me)
    ob.location.x = x
    C.scene.collection.objects.link(ob)
    return ob


def stroke(ob, rng, share=0.02):
    """Push a round patch of about `share` of the vertices along z, as a brush would."""
    me = ob.data
    n = len(me.vertices)
    side = int(round(math.sqrt(n)))
    co = np.empty(n * 3, np.float32)
    me.attributes["position"].data.foreach_get("vector", co)
    co = co.reshape(-1, 3)
    r = math.sqrt(share * n / math.pi)
    cx, cy = rng.uniform(r, side - r, 2)
    ys, xs = np.divmod(np.arange(n), side)
    d = np.hypot(xs - cx, ys - cy)
    m = d < r
    co[m, 2] += (0.002 * (1 - d[m] / r)).astype(np.float32)
    me.attributes["position"].data.foreach_set("vector", co.reshape(-1))
    me.update()


def build(n_verts, n_steps, n_obj):
    H.fresh()
    objs = [grid_mesh(n_verts, f"M{k}", 1.2 * k) for k in range(n_obj)]
    rng = np.random.default_rng(1)
    t0 = time.perf_counter()
    k = 0
    while k < n_steps:
        ob = objs[(k // 10) % n_obj]           # ten strokes on one object, then the next
        H.enter(ob, "SCULPT")
        for _ in range(min(10, n_steps - k)):
            stroke(ob, rng)
            assert H.commit(ob) is not None
            k += 1
    runtime.set_global(C, True)
    info = runtime.scrub_info(C)
    assert info["len"] == n_steps, info
    return objs, time.perf_counter() - t0


def key_movie(fps_step=2, spf=1):
    sc = C.scene
    sh = sc.hindsight
    sh.movie_mode = True
    sc.frame_start = 1
    sh.frames_per_step = fps_step
    sh.movie_steps_per_frame = spf
    sc.frame_set(1)
    assert bpy.ops.hindsight.quick_auto_key() == {'FINISHED'}
    sc.frame_end = sc.frame_start + 1          # Quick auto key only makes the range longer
    assert bpy.ops.hindsight.quick_auto_key() == {'FINISHED'}
    sc.frame_set(sc.frame_end)            # start from the end of the movie, as after keying
    return sc.frame_start, sc.frame_end


CATS = ("handler bookkeeping", "applying steps", "writing the mesh", "journal and disk",
        "undo bookkeeping", "redraw tagging", "other add-on", "other")


def _cat(func):
    fn, _line, name = func
    fn = fn.replace("\\", "/")
    if fn == "~":
        return None
    if "/hindsight/" not in fn:
        return "other"
    mod = fn.rsplit("/", 1)[-1]
    if mod in ("globaltl.py", "movie.py", "filtering.py", "idmap.py", "guard.py", "adapters.py",
               "registry.py"):
        return "handler bookkeeping"
    if mod in ("history.py", "step.py", "codec.py", "delta.py", "store.py", "types.py"):
        return "applying steps"
    if mod == "meshio.py":
        return "writing the mesh"
    if mod in ("persistence.py", "journal.py", "persist.py"):
        return "journal and disk"
    if mod == "datarec.py":
        if name in ("note_jump", "push_undo", "can_push", "drop_undone_jumps"):
            return "undo bookkeeping"
        if name == "write":
            return "writing the mesh"
        return "handler bookkeeping"
    if mod == "runtime.py":
        if name in ("_write_state", "_clamp_state_weights", "mark_written", "_refresh_toggle"):
            return "writing the mesh"
        if name == "tag_redraw_all":
            return "redraw tagging"
        if name == "_journal":
            return "journal and disk"
        if name == "mesh_fingerprint":
            return "undo bookkeeping"
        if name in ("ensure_state", "read_mesh_snapshot"):
            return "applying steps"
        return "handler bookkeeping"
    return "other add-on"


def split(prof):
    """Own time per category. A builtin (numpy, bpy, file IO) is charged to whoever called it."""
    st = pstats.Stats(prof).stats
    out = dict.fromkeys(CATS, 0.0)
    for func, (_cc, _nc, tt, _ct, callers) in st.items():
        c = _cat(func)
        if c is not None:
            out[c] += tt
            continue
        tot = sum(v[2] for v in callers.values()) or 1.0
        for cf, v in callers.items():
            cc = _cat(cf) or "other"
            out[cc] += tt * v[2] / tot
    return out


def _count_goto():
    orig = runtime.goto
    calls = [0]

    def counted(*a, **k):
        calls[0] += 1
        return orig(*a, **k)
    counted.__dict__.update(orig.__dict__)

    def restore():
        orig.__dict__.update({k: v for k, v in counted.__dict__.items() if k == "_movie"})
        runtime.goto = orig
    runtime.goto = counted
    return calls, restore


def _timed_handlers(spent):
    """Wrap the add-on's frame_change_post and depsgraph_update_post handlers to time them."""
    swaps = []
    for hl in (bpy.app.handlers.frame_change_post, bpy.app.handlers.depsgraph_update_post):
        for i, fn in enumerate(list(hl)):
            if "hindsight" not in getattr(fn, "__module__", ""):
                continue

            def timed(*a, _fn=fn):
                t = time.perf_counter()
                try:
                    return _fn(*a)
                finally:
                    spent[0] += time.perf_counter() - t
            hl[i] = timed
            swaps.append((hl, i, fn))

    def restore():
        for hl, i, fn in swaps:
            hl[i] = fn
    return restore


def play(f0, f1, profile=False):
    """frame_set every frame. Returns (seconds per frame, add-on seconds per frame, step shown per
    frame, goto calls, profile)."""
    sc = C.scene
    times, addon, steps = [], [], []
    calls, restore = _count_goto()
    spent = [0.0]
    unwrap = _timed_handlers(spent) if not profile else (lambda: None)
    prof = cProfile.Profile() if profile else None
    try:
        for f in range(f0, f1 + 1):
            if prof:
                prof.enable()
            spent[0] = 0.0
            t = time.perf_counter()
            sc.frame_set(f)
            times.append(time.perf_counter() - t)
            addon.append(spent[0])
            if prof:
                prof.disable()
            steps.append(sc.hindsight.movie_step)
    finally:
        unwrap()
        restore()
    return times, addon, steps, calls[0], prof


def scene_run(label, n_verts, n_steps, n_obj):
    _objs, build_s = build(n_verts, n_steps, n_obj)
    sc = C.scene
    f0, f1 = key_movie(fps_step=2)
    times, addon, steps, gotos, _ = play(f0, f1)
    ms = np.array(times) * 1000
    am = np.array(addon) * 1000
    moved = np.array([True] + [a != b for a, b in zip(steps, steps[1:])])
    sc.frame_set(f1)
    _t, _a, _s, _g, prof = play(f0, f1, profile=True)
    parts = split(prof)
    tot = sum(parts.values()) or 1.0
    res = {"scene": label, "vertices": n_verts * n_obj, "objects": n_obj, "steps": n_steps,
           "build_s": round(build_s, 1), "frames": len(times), "frames_step_changed": int(moved.sum()),
           "goto_calls": gotos,
           "total_s": round(float(ms.sum()) / 1000, 3), "median_ms": round(float(np.median(ms)), 3),
           "worst_ms": round(float(ms.max()), 2),
           "addon_total_s": round(float(am.sum()) / 1000, 3), "addon_median_ms": round(float(np.median(am)), 3),
           "addon_worst_ms": round(float(am.max()), 2),
           "addon_median_step_changed_ms": round(float(np.median(am[moved])), 3),
           "addon_median_same_step_ms": round(float(np.median(am[~moved])), 4),
           "blender_median_ms": round(float(np.median(ms - am)), 3),
           "profile_share": {k: round(v / tot, 4) for k, v in parts.items()},
           "profile_ms_per_frame": {k: round(1000 * v / len(times), 3) for k, v in parts.items()}}
    f0, f1 = key_movie(fps_step=1, spf=4)     # the same movie with Steps per frame 4
    t4, _a, _s, _g, _p = play(f0, f1)
    res["spf4_frames"] = len(t4)
    res["spf4_total_s"] = round(sum(t4), 3)
    res["spf4_median_ms"] = round(1000 * float(np.median(t4)), 3)
    print_row(res)
    return res


def print_row(r):
    print(f"\n{r['scene']}: {r['frames']} frames ({r['frames_step_changed']} change the step), "
          f"frame_set total {r['total_s']} s, median {r['median_ms']} ms, worst {r['worst_ms']} ms; "
          f"add-on total {r['addon_total_s']} s, median {r['addon_median_ms']} ms "
          f"(step changed {r['addon_median_step_changed_ms']}, same step {r['addon_median_same_step_ms']}), "
          f"worst {r['addon_worst_ms']} ms; Blender median {r['blender_median_ms']} ms; "
          f"goto calls {r['goto_calls']}")
    print("    profile (own time, with profiler overhead):")
    for k, v in r["profile_ms_per_frame"].items():
        print(f"    {k:28s} {v:9.3f} ms/frame  {100 * r['profile_share'][k]:5.1f}%")
    print(f"    Steps per frame 4, Frames per step 1: {r['spf4_frames']} frames, "
          f"{r['spf4_total_s']} s, median {r['spf4_median_ms']} ms")


def render_run(motion_blur, engine="CYCLES", frames=10):
    """Render `frames` frames at 64x36 and count how often the handler runs and moves history."""
    sc = C.scene
    sc.render.engine = engine
    sc.render.resolution_x, sc.render.resolution_y = 64, 36
    sc.render.resolution_percentage = 100
    if engine == "CYCLES":
        sc.cycles.samples = 1
        sc.cycles.device = "CPU"
        sc.cycles.use_denoising = False
    sc.render.use_motion_blur = motion_blur
    if sc.camera is None:
        cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
        sc.collection.objects.link(cam)
        cam.location = (0.5, 0.5, 3.0)
        sc.camera = cam
    out = tempfile.mkdtemp(prefix="hs_movie_")
    sc.render.filepath = os.path.join(out, "f_")
    sc.render.image_settings.file_format = "PNG"
    end = sc.frame_end
    sc.frame_end = sc.frame_start + frames - 1
    seen = []

    def spy(scene, depsgraph=None):
        seen.append((scene.frame_current, round(scene.frame_subframe, 3)))
    bpy.app.handlers.frame_change_post.append(spy)
    calls, restore = _count_goto()
    t = time.perf_counter()
    try:
        bpy.ops.render.render(animation=True)
    finally:
        restore()
        bpy.app.handlers.frame_change_post.remove(spy)
        sc.frame_end = end
    dt = time.perf_counter() - t
    return {"engine": engine, "motion_blur": motion_blur, "frames": frames,
            "handler_calls": len(seen), "subframe_calls": sum(1 for _f, s in seen if s),
            "goto_calls": calls[0], "seconds": round(dt, 2), "calls": seen[:40]}


def renders():
    build(100_000 if not QUICK else 20_000, 20, 1)
    key_movie(fps_step=2)
    out = []
    # EEVEE and Workbench abort the process here (no GPU: "Couldn't open libEGL.so.1"), so only
    # Cycles on the CPU is rendered.
    for engine, mb in (("CYCLES", False), ("CYCLES", True)):
        try:
            r = render_run(mb, engine)
        except Exception as e:
            r = {"engine": engine, "motion_blur": mb, "error": str(e).splitlines()[0][:200]}
        print(" render", {k: v for k, v in r.items() if k != "calls"})
        out.append(r)
    return out


# ----------------------------------------------------------------------------- the 300-frame grid
def stroke_patch(ob, rng, share=0.02):
    """stroke(), computed on the patch only (the same kind of change, quick enough at 1M)."""
    me = ob.data
    n = len(me.vertices)
    side = int(round(math.sqrt(n)))
    co = np.empty(n * 3, np.float32)
    me.attributes["position"].data.foreach_get("vector", co)
    co = co.reshape(-1, 3)
    r = math.sqrt(share * n / math.pi)
    cx, cy = rng.uniform(r + 2, side - r - 2, 2)
    ys, xs = np.mgrid[int(cy - r) - 1:int(cy + r) + 2, int(cx - r) - 1:int(cx + r) + 2]
    d = np.hypot(xs - cx, ys - cy)
    m = d < r
    co[(ys * side + xs)[m], 2] += (0.002 * (1 - d[m] / r)).astype(np.float32)
    me.attributes["position"].data.foreach_set("vector", co.reshape(-1))
    me.update()


def _vmhwm_mb():
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


def _timed_all(spent):
    """Wrap every Hindsight handler that can run while a frame changes or renders; time each."""
    swaps = []
    for nm in ("frame_change_pre", "frame_change_post", "depsgraph_update_pre", "depsgraph_update_post",
               "render_init", "render_pre", "render_post", "render_complete"):
        hl = getattr(bpy.app.handlers, nm)
        for i, fn in enumerate(list(hl)):
            if "hindsight" not in getattr(fn, "__module__", ""):
                continue

            def timed(*a, _fn=fn, _k=f"{nm}:{fn.__name__}"):
                t = time.perf_counter()
                try:
                    return _fn(*a)
                finally:
                    v = spent.setdefault(_k, [0.0, 0])
                    v[0] += time.perf_counter() - t
                    v[1] += 1
            hl[i] = timed
            swaps.append((hl, i, fn))

    def restore():
        for hl, i, fn in swaps:
            hl[i] = fn
    return restore


def grid_build(n_verts, n_steps, mode="OBJECT", extra=0):
    H.fresh()
    ob = grid_mesh(n_verts, "M")
    rng = np.random.default_rng(1)
    H.enter(ob, "SCULPT")
    t0 = time.perf_counter()
    for _ in range(n_steps):
        stroke_patch(ob, rng)
        assert H.commit(ob) is not None
    if runtime.packer is not None:
        runtime.packer.drain(600)
    build_s = time.perf_counter() - t0
    runtime.set_global(C, True)
    for i in range(extra):                     # other objects in the file, never recorded
        e = bpy.data.objects.new(f"E{i}", bpy.data.meshes.new(f"E{i}"))
        C.scene.collection.objects.link(e)
    if mode == "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    return ob, build_s


def grid_case(n_verts, mode, spf, frames=300, extra=0):
    """frame_set over a movie of `frames` frames (Frames per step 1, Steps per frame spf), forward,
    then the jump back to the first frame (a loop), then backwards. Add-on = Hindsight's handlers."""
    ob, build_s = grid_build(n_verts, frames * spf, mode, extra)
    f0, f1 = key_movie(fps_step=1, spf=spf)
    sc = C.scene
    sc.frame_end = f1
    assert f1 - f0 == frames, (f0, f1)
    sc.frame_set(f0)
    spent = {}
    unwrap = _timed_all(spent)

    def run(seq):
        tot, ours = [], []
        for f in seq:
            b = sum(v[0] for v in spent.values())
            t = time.perf_counter()
            sc.frame_set(f)
            tot.append(time.perf_counter() - t)
            ours.append(sum(v[0] for v in spent.values()) - b)
        return np.array(tot) * 1000, np.array(ours) * 1000
    try:
        tracemalloc.start()
        tot, ours = run(range(f0 + 1, f1 + 1))
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert runtime.scrub_info(C)["pos"] == frames * spf
        lt, lo = run([f0])                         # the loop: from the last frame to the first
        run([f1])
        bt, bo = run(range(f1 - 1, f0 - 1, -1))
    finally:
        unwrap()
    med = lambda a: round(float(np.median(a)), 3)  # noqa: E731
    return {"vertices": n_verts, "mode": mode, "steps_per_frame": spf, "frames": frames, "steps": frames * spf,
            "extra_objects": extra, "build_s": round(build_s, 1),
            "frame_set_median_ms": med(tot), "frame_set_min_ms": round(float(tot.min()), 3),
            "addon_median_ms": med(ours), "addon_min_ms": round(float(ours.min()), 3),
            "blender_median_ms": med(tot - ours), "addon_total_s": round(float(ours.sum()) / 1000, 3),
            "frame_set_total_s": round(float(tot.sum()) / 1000, 3),
            "loop_jump_ms": round(float(lt[0]), 2), "loop_jump_addon_ms": round(float(lo[0]), 2),
            "backwards_frame_set_median_ms": med(bt), "backwards_addon_median_ms": med(bo),
            "handler_calls": {k: v[1] for k, v in spent.items()},
            "playback_tracemalloc_peak_mb": round(peak / 1e6, 2), "vmhwm_mb": _vmhwm_mb()}


def render_case(n_verts=100_000, steps=300, frames=30, size=(320, 180)):
    """Cycles on the CPU, 1 sample, an animation render of `frames` frames that each show a new step."""
    grid_build(n_verts, steps, "OBJECT")
    key_movie(fps_step=1, spf=1)
    sc = C.scene
    sc.render.engine = "CYCLES"
    sc.render.resolution_x, sc.render.resolution_y = size
    sc.render.resolution_percentage = 100
    sc.cycles.samples = 1
    sc.cycles.device = "CPU"
    sc.cycles.use_denoising = False
    if sc.camera is None:
        cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
        sc.collection.objects.link(cam)
        cam.location = (0.5, 0.5, 2.0)
        sc.camera = cam
    sc.render.filepath = os.path.join(tempfile.mkdtemp(prefix="hs_movie_"), "f_")
    sc.render.image_settings.file_format = "PNG"
    sc.frame_start, sc.frame_end = 1, frames
    sc.frame_set(1)
    stamps = []

    def post(*_a):
        stamps.append(time.perf_counter())
    bpy.app.handlers.render_post.append(post)
    spent = {}
    unwrap = _timed_all(spent)
    t = time.perf_counter()
    try:
        bpy.ops.render.render(animation=True)
    finally:
        unwrap()
        bpy.app.handlers.render_post.remove(post)
    dt = time.perf_counter() - t
    per = np.diff([t] + stamps) * 1000
    ours = sum(v[0] for v in spent.values()) * 1000
    return {"vertices": n_verts, "frames": frames, "size": list(size), "samples": 1, "total_s": round(dt, 3),
            "per_frame_median_ms": round(float(np.median(per)), 2), "addon_total_ms": round(ours, 2),
            "addon_per_frame_ms": round(ours / frames, 3),
            "handler_calls": {k: v[1] for k, v in spent.items()}, "vmhwm_mb": _vmhwm_mb()}


def grid_main():
    if "--case" in sys.argv:
        i = sys.argv.index("--case")
        n, mode, spf = int(sys.argv[i + 1]), sys.argv[i + 2], int(sys.argv[i + 3])
        extra = int(sys.argv[sys.argv.index("--extra") + 1]) if "--extra" in sys.argv else 0
        print("RESULT", json.dumps(grid_case(n, mode, spf, extra=extra)), flush=True)
        return
    if "--render-case" in sys.argv:
        print("RESULT", json.dumps(render_case()), flush=True)
        return
    res = {"blender": bpy.app.version_string, "cases": [], "render": None}
    for n in (100_000, 1_000_000):
        for mode in ("OBJECT", "SCULPT"):
            for spf in (1, 8):
                res["cases"].append(grid_case(n, mode, spf))
                print(res["cases"][-1], flush=True)
    res["cases"].append(grid_case(100_000, "OBJECT", 1, extra=500))
    res["render"] = render_case()
    path = os.path.join(HERE, "results_movie_grid.json")
    with open(path, "w") as fh:
        json.dump(res, fh, indent=1)
    print("wrote", path)


def main():
    if any(a in sys.argv for a in ("--grid", "--case", "--render-case")):
        grid_main()
        return
    res = {"blender": bpy.app.version_string, "scenes": [], "renders": [],
           "not_measured": "EEVEE and Workbench: Blender aborts without a GPU (libEGL.so.1 missing)"}
    if "--render-only" not in sys.argv:
        cases = ([("100k, 1,000 steps, 2 objects", 100_000, 1000, 2), ("100k, 5,000 steps", 100_000, 5000, 1),
                  ("500k, 300 steps", 500_000, 300, 1)]
                 if not QUICK else [("20k, 200 steps, 2 objects", 20_000, 200, 2)])
        for label, nv, ns, no in cases:
            res["scenes"].append(scene_run(label, nv, ns, no))
    res["renders"] = renders()
    name = "results_movie.json" if not QUICK else "results_movie_quick.json"
    if "--out" in sys.argv:
        name = sys.argv[sys.argv.index("--out") + 1]
    path = os.path.join(HERE, name)
    with open(path, "w") as fh:
        json.dump(res, fh, indent=1)
    print("\nwrote", path)


if __name__ == "__main__":
    main()
    H.finish(0)
