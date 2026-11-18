"""Python cost of every Hindsight redraw path, headless (gpu/blf stubbed and counted).

    bpy53/bin/python draw_bench.py ROOT [--sizes 10,1000,10000] [--objs 1,50] [--json out]

Prints one JSON line with per-scenario numbers: microseconds per call (min and median of batches)
and Python function calls per call (cProfile).
"""
import cProfile
import json
import os
import pstats
import statistics
import sys
import time
import types
from types import SimpleNamespace

ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
args = sys.argv[2:]


def _opt(name, default):
    if name in args:
        return args[args.index(name) + 1]
    return default


SIZES = [int(x) for x in _opt("--sizes", "10,1000,10000").split(",")]
OBJS = [int(x) for x in _opt("--objs", "1,50").split(",")]
OUT = _opt("--json", None)

# ------------------------------------------------------------------ gpu / blf stubs (count calls)
COUNT = {}


def _c(name):
    COUNT[name] = COUNT.get(name, 0) + 1


class _Shader:
    def bind(self):
        _c("shader.bind")

    def uniform_float(self, n, v):
        _c("shader.uniform_float")


class _Batch:
    def __init__(self, data):
        self.n = len(data["pos"])

    def draw(self, sh):
        _c("batch.draw")


gpu = types.ModuleType("gpu")
gpu.shader = SimpleNamespace(from_builtin=lambda n: (_c("shader.from_builtin"), _Shader())[1])
gpu.state = SimpleNamespace(blend_set=lambda m: _c("state.blend_set"))


class _Mat:
    def push(self): _c("matrix.push")
    def pop(self): _c("matrix.pop")
    def translate(self, v): _c("matrix.translate")


gpu.matrix = _Mat()
gx = types.ModuleType("gpu_extras")
gxb = types.ModuleType("gpu_extras.batch")


def batch_for_shader(sh, kind, data):
    _c("batch_for_shader")
    return _Batch(data)


gxb.batch_for_shader = batch_for_shader
gx.batch = gxb
blf = types.ModuleType("blf")
blf.SHADOW = 4
for _n in ("size", "enable", "disable", "shadow", "shadow_offset", "color", "position", "draw"):
    setattr(blf, _n, (lambda n: (lambda *a: _c("blf." + n)))(_n))
blf.dimensions = lambda f, t: (_c("blf.dimensions"), (len(t) * 6.0, 10.0))[1]
import bpy  # noqa: E402
sys.modules.update({"gpu": gpu, "gpu_extras": gx, "gpu_extras.batch": gxb, "blf": blf})
import harness as H  # noqa: E402
from hindsight.bl import runtime, scrubber, ui_panel, header, slider_prop, warnings as W  # noqa: E402
from hindsight.bl import prefs as P  # noqa: E402


class FakeRegion:
    def __init__(self, t, x, y, w, h, align="NONE", ptr=1):
        self.type, self.x, self.y, self.width, self.height, self.alignment = t, x, y, w, h, align
        self._p = ptr

    def as_pointer(self):
        return self._p


class FakeLayout:
    def __getattr__(self, name):
        return _fake_fn

    def __setattr__(self, k, v):
        pass


_FL = FakeLayout()


def _fake_fn(*a, **kw):
    return _FL


def make_ctx(obj):
    win = FakeRegion("WINDOW", 0, 0, 1574, 954, ptr=1001)
    regions = [FakeRegion("HEADER", 0, 954, 1574, 26, "TOP", 1002),
               FakeRegion("TOOLS", 0, 0, 56, 954, "LEFT", 1003),
               FakeRegion("UI", 1254, 0, 320, 954, "RIGHT", 1004),
               FakeRegion("ASSET_SHELF", 56, 0, 1198, 1, "BOTTOM", 1005), win]
    area = SimpleNamespace(regions=regions, type="VIEW_3D")
    c = bpy.context
    return SimpleNamespace(region=win, area=area, scene=c.scene, view_layer=c.view_layer,
                           active_object=obj, preferences=c.preferences, window_manager=c.window_manager,
                           mode=getattr(obj, "mode", "OBJECT"), window=None)


def build(n_steps, n_objs):
    H.fresh()
    # an add-on entry so get_prefs resolves like in Blender
    if P.get_prefs() is None:
        a = bpy.context.preferences.addons.new()
        a.module = P._base_package
    assert P.get_prefs() is not None
    obs = []
    for i in range(n_objs):
        ob = H.make_mesh(40, name=f"O{i}", location=(i * 3.0, 0, 0))
        obs.append(ob)
    per = max(1, n_steps // n_objs)
    active = obs[0]
    for ob in obs:
        H.enter(ob, "SCULPT")
        for k in range(per):
            H.fake_stroke(ob, k, n=3)
            runtime.commit(ob)
    if active.mode != "SCULPT":
        H.enter(active, "SCULPT")
    h = runtime.history_of(active)
    pk = getattr(runtime, "packer", None)
    if pk is not None:          # the background packer competes for the GIL: let it finish first
        pk.drain(60.0)
    return active, obs, h


def timeit(fn, n, batches=7):
    out = []
    for _ in range(batches):
        t = time.perf_counter()
        for _ in range(n):
            fn()
        out.append((time.perf_counter() - t) / n * 1e6)
    return min(out), statistics.median(out)


def calls(fn, n=20):
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(n):
        fn()
    pr.disable()
    st = pstats.Stats(pr)
    return st.total_calls / n


def gpu_counts(fn, n=10):
    COUNT.clear()
    for _ in range(n):
        fn()
    return {k: v / n for k, v in sorted(COUNT.items())}


def scenario(ctx, h, n):
    hdr_self = SimpleNamespace(layout=_FL)
    pnl_self = SimpleNamespace(layout=_FL)

    def viewport_redraw():
        # what Blender runs per 3D Viewport redraw: gizmo group poll, the two POST_PIXEL
        # handlers, the header button
        scrubber._scrub_poll(ctx)
        scrubber._draw(ctx)
        W.bpy = SimpleNamespace(context=ctx, types=bpy.types)
        W._draw_overlay()
        header.draw_header_button(hdr_self, ctx)

    def panel_redraw():
        # sidebar: main panel (with the IntProperty getter Blender calls for row.prop), the
        # Warnings poll, the Steps and Memory sub-panels opened
        ui_panel.VIEW3D_PT_hindsight.draw(pnl_self, ctx)
        slider_prop._get(None)
        if ui_panel.VIEW3D_PT_hindsight_warnings.poll(ctx):
            ui_panel.VIEW3D_PT_hindsight_warnings.draw(pnl_self, ctx)
        ui_panel.VIEW3D_PT_hindsight_movie.poll(ctx)
        ui_panel.VIEW3D_PT_hindsight_movie.draw_header(pnl_self, ctx)
        ui_panel.VIEW3D_PT_hindsight_filter.poll(ctx)

    def steps_panel():
        ui_panel.VIEW3D_PT_hindsight_steps.draw(pnl_self, ctx)

    def memory_panel():
        ui_panel.VIEW3D_PT_hindsight_memory.draw(pnl_self, ctx)

    def popover():
        header.VIEW3D_PT_hindsight_popover.draw(pnl_self, ctx)

    lay = scrubber._layout_for(ctx)
    x0, y0, x1, y1 = lay["bar"]
    ymid = (y0 + y1) / 2
    hover_pts = [(x0 + (x1 - x0) * i / 97.0, ymid + (i % 5) * 7 - 14) for i in range(97)]
    hi = [0]

    class _Giz:
        pass
    giz = _Giz()

    def hover():
        x, y = hover_pts[hi[0] % 97]
        hi[0] += 1
        scrubber.HINDSIGHT_GT_scrubber.test_select(giz, ctx, (x, y))

    res = {}
    reps = 200 if n < 5000 else 60
    for name, fn, r in (("viewport_redraw", viewport_redraw, reps), ("panel_redraw", panel_redraw, reps),
                        ("steps_panel", steps_panel, reps), ("memory_panel", memory_panel, 20),
                        ("popover", popover, reps // 2), ("hover_test_select", hover, reps * 3)):
        fn()
        mn, md = timeit(fn, r)
        if os.environ.get("PROF") == name:
            pr = cProfile.Profile(); pr.enable()
            for _ in range(200):
                fn()
            pr.disable()
            print("=== PROFILE", name, n, file=sys.stderr)
            pstats.Stats(pr, stream=sys.stderr).sort_stats("tottime").print_stats(25)
        res[name] = {"us_min": round(mn, 2), "us_med": round(md, 2), "calls": round(calls(fn), 1)}
    res["viewport_redraw"]["gpu"] = gpu_counts(viewport_redraw)
    return res


def drag_bench(ctx, h):
    """UI share of one mouse move while dragging the bar: the gizmo modal (pos from x, throttle,
    goto, tag redraw) and the viewport redraw that follows. runtime.goto is replaced by a cursor
    move, so only the UI is timed."""
    real_goto, real_clock = runtime.goto, scrubber._clock
    t = [0.0]

    def fake_goto(context, pos, final=True):
        h.cursor = pos
    runtime.goto = fake_goto
    scrubber._clock = lambda: t[0]
    hdr_self = SimpleNamespace(layout=_FL)
    try:
        W.bpy = SimpleNamespace(context=ctx, types=bpy.types)
        info = runtime.scrub_info(ctx)
        lay = scrubber._layout_for(ctx)
        x0, _, x1, _ = lay["bar"]
        d = scrubber._Drag(lay, info)
        giz = SimpleNamespace(_mode="bar", _drag=d)
        xs = [x0 + (x1 - x0) * ((i * 37) % 1000) / 1000.0 for i in range(1000)]
        i = [0]

        def move():
            t[0] += 0.02          # past the throttle: every move applies
            ev = SimpleNamespace(mouse_region_x=xs[i[0] % 1000])
            i[0] += 1
            scrubber.HINDSIGHT_GT_scrubber.modal(giz, ctx, ev, None)
            scrubber._scrub_poll(ctx)
            scrubber._draw(ctx)
            W._draw_overlay()
            header.draw_header_button(hdr_self, ctx)
        move()
        mn, md = timeit(move, 100)
        res = {"us_min": round(mn, 2), "us_med": round(md, 2), "calls": round(calls(move), 1),
               "gpu": gpu_counts(move)}
    finally:
        runtime.goto, scrubber._clock = real_goto, real_clock
    return res


def timer_bench():
    """CPU of slider_prop's timer over 60 simulated seconds (0.2 s interval = 300 calls),
    nothing changing."""
    nxt = slider_prop._timer()
    n = int(60.0 / nxt)
    t = time.process_time()
    for _ in range(n):
        slider_prop._timer()
    cpu = time.process_time() - t
    return {"calls_per_60s": n, "cpu_ms_per_60s": round(cpu * 1e3, 3)}


def main():
    out = {}
    for objs in OBJS:
        for n in SIZES:
            if objs > n:
                continue
            ob, obs, h = build(n, objs)
            ctx = make_ctx(ob)
            for glob in (False, True):
                bpy.context.scene.hindsight.use_global = glob
                tag = f"{n}steps_{objs}obj_{'global' if glob else 'object'}"
                r = scenario(ctx, h, n)
                if n >= 1000 and not glob:
                    pass
                r["timer"] = timer_bench()
                out[tag] = r
                print(tag, json.dumps({k: (v["us_min"] if isinstance(v, dict) and "us_min" in v else v)
                                       for k, v in r.items()}), file=sys.stderr)
            bpy.context.scene.hindsight.use_global = False
            if objs >= 10 and n == max(SIZES):
                # Collection Filtering on: the objects in 10 collections, 3 of them blacklisted
                sc = bpy.context.scene
                colls = []
                for i in range(10):
                    c = bpy.data.collections.new(f"K{i}")
                    sc.collection.children.link(c)
                    colls.append(c)
                for i, o in enumerate(obs):
                    colls[i % 10].objects.link(o)
                    sc.collection.objects.unlink(o)
                for c in colls[7:]:
                    sc.hindsight.filter_collections.add().collection = c
                sc.hindsight.filter_mode = 'BLACKLIST'
                for glob in (False, True):
                    sc.hindsight.use_global = glob
                    tag = f"{n}steps_{objs}obj_{'global' if glob else 'object'}_filtered"
                    r = scenario(ctx, h, n)
                    r["timer"] = timer_bench()
                    out[tag] = r
                    print(tag, json.dumps({k: (v["us_min"] if isinstance(v, dict) and "us_min" in v else v)
                                           for k, v in r.items()}), file=sys.stderr)
                sc.hindsight.use_global = False
                sc.hindsight.filter_mode = 'NONE'
    # the drag over a 5,000-step history
    ob, obs, h = build(5000, 1)
    ctx = make_ctx(ob)
    out["drag_5000"] = drag_bench(ctx, h)
    print("drag", json.dumps(out["drag_5000"]), file=sys.stderr)
    s = json.dumps({"root": ROOT, "results": out})
    if OUT:
        with open(OUT, "w") as f:
            f.write(s)
    print(s)


main()
sys.stdout.flush()
os._exit(0)
