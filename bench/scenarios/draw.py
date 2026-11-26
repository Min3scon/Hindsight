"""Python cost of the redraw paths, headless: gpu and blf are stubbed (calls counted, nothing drawn),
so this is the add-on's own Python per redraw and nothing of Blender's drawing or of a GPU.

    <bpy python> bench/scenarios/draw.py CODE_ROOT [--quick] [--only a,b] [--reps N]

Scenarios (microseconds per call are reported in ms): 10,000 steps, 1 object or 50 objects, history
per object or global, and for 50 objects also with Collection Filtering (10 collections, 3 on the
blacklist):
  redraw_viewport_<cfg>  gizmo poll, the two POST_PIXEL handlers, the header button
  redraw_panel_<cfg>     sidebar main panel, slider getter, Warnings, Movie and Filter polls
  <cfg> = 1obj_object, 1obj_global, 50obj_object, 50obj_global, 50obj_object_filtered, 50obj_global_filtered
  drag_5000              one mouse move while dragging the bar over a 5,000-step history (the
                         gizmo modal plus the viewport redraw; runtime.goto replaced by a cursor move)
Extra fields: python_calls (cProfile, per call) and gpu (counted stub calls) on the viewport ones.
Not measured: the GPU, Blender's own drawing, a real window (headless: no region is ever drawn).
--quick: 500 steps (names unchanged).
"""
import cProfile
import os
import pstats
import sys
import types
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as K  # noqa: E402

opts = K.Opts()
K.use_root(opts.root)
STEPS = 10000 if not opts.quick else 500
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
import tests.harness as H  # noqa: E402
from hindsight.bl import runtime, scrubber, ui_panel, header, slider_prop, warnings as W  # noqa: E402
from hindsight.bl import prefs as P  # noqa: E402

K.import_checked(opts.root)


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
    if P.get_prefs() is None:                   # an add-on entry so get_prefs resolves like in Blender
        a = bpy.context.preferences.addons.new()
        a.module = P._base_package
    assert P.get_prefs() is not None
    obs = [H.make_mesh(40, name=f"O{i}", location=(i * 3.0, 0, 0)) for i in range(n_objs)]
    per = max(1, n_steps // n_objs)
    for ob in obs:
        H.enter(ob, "SCULPT")
        for k in range(per):
            H.fake_stroke(ob, k, n=3)
            runtime.commit(ob)
    active = obs[0]
    if active.mode != "SCULPT":
        H.enter(active, "SCULPT")
    pk = getattr(runtime, "packer", None)
    if pk is not None:                          # the background packer competes for the GIL
        pk.drain(120.0)
    return active, obs, runtime.history_of(active)


def calls(fn, n=10):
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(n):
        fn()
    pr.disable()
    return pstats.Stats(pr).total_calls / n


def gpu_counts(fn, n=5):
    COUNT.clear()
    for _ in range(n):
        fn()
    return {k: v / n for k, v in sorted(COUNT.items())}


def timed(fn, reps, inner):
    fn()
    return K.stats(K.time_it(fn, reps, inner=inner), python_calls=round(calls(fn), 1))


def redraw_cases(ctx, tag, results):
    hdr_self = SimpleNamespace(layout=_FL)
    pnl_self = SimpleNamespace(layout=_FL)

    def viewport_redraw():
        scrubber._scrub_poll(ctx)
        scrubber._draw(ctx)
        W.bpy = SimpleNamespace(context=ctx, types=bpy.types)
        W._draw_overlay()
        header.draw_header_button(hdr_self, ctx)

    def panel_redraw():
        ui_panel.VIEW3D_PT_hindsight.draw(pnl_self, ctx)
        slider_prop._get(None)
        if ui_panel.VIEW3D_PT_hindsight_warnings.poll(ctx):
            ui_panel.VIEW3D_PT_hindsight_warnings.draw(pnl_self, ctx)
        ui_panel.VIEW3D_PT_hindsight_movie.poll(ctx)
        ui_panel.VIEW3D_PT_hindsight_movie.draw_header(pnl_self, ctx)
        ui_panel.VIEW3D_PT_hindsight_filter.poll(ctx)

    reps = opts.n(9, 3)
    inner = 30 if STEPS >= 5000 else 100
    for kind, fn in (("viewport", viewport_redraw), ("panel", panel_redraw)):
        name = f"redraw_{kind}_{tag}"
        if opts.want(name):
            K.attempt(results, name, lambda fn=fn: timed(fn, reps, inner))
            if kind == "viewport" and "error" not in results[name]:
                results[name]["gpu"] = gpu_counts(fn)


def drag_case(results):
    name = "drag_5000"
    ob, obs, h = build(5000 if not opts.quick else 400, 1)
    ctx = make_ctx(ob)
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
            t[0] += 0.02
            ev = SimpleNamespace(mouse_region_x=xs[i[0] % 1000])
            i[0] += 1
            scrubber.HINDSIGHT_GT_scrubber.modal(giz, ctx, ev, None)
            scrubber._scrub_poll(ctx)
            scrubber._draw(ctx)
            W._draw_overlay()
            header.draw_header_button(hdr_self, ctx)
        results[name] = timed(move, opts.n(9, 3), 30)
        results[name]["gpu"] = gpu_counts(move)
    except Exception as e:
        import traceback
        traceback.print_exc()
        results[name] = {"error": f"{type(e).__name__}: {e}"[:300]}
    finally:
        runtime.goto, scrubber._clock = real_goto, real_clock


def set_filtering(obs):
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


results = {}
for objs in (1, 50):
    cfgs = [(f"{objs}obj_{g}", g == "global", False) for g in ("object", "global")]
    if objs == 50:
        cfgs += [(f"50obj_{g}_filtered", g == "global", True) for g in ("object", "global")]
    if not any(opts.want(f"redraw_{k}_{c[0]}") for k in ("viewport", "panel") for c in cfgs):
        continue
    try:
        ob, obs, h = build(STEPS, objs)
        ctx = make_ctx(ob)
    except Exception as e:
        import traceback
        traceback.print_exc()
        for c in cfgs:
            for k in ("viewport", "panel"):
                results[f"redraw_{k}_{c[0]}"] = {"error": f"build: {type(e).__name__}: {e}"[:300]}
        continue
    filtered = False
    for tag, glob, filt in cfgs:
        if filt and not filtered:
            set_filtering(obs)
            filtered = True
        bpy.context.scene.hindsight.use_global = glob
        redraw_cases(ctx, tag, results)
if opts.want("drag_5000"):
    drag_case(results)
K.emit(results, quick=opts.quick, steps=STEPS, blender=bpy.app.version_string, gpu="stubbed")
