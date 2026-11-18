import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""The on-screen slider draws exactly what it drew before its batches were kept between redraws.

gpu and blf are replaced by recorders (there is no GPU here). Every gpu and blf call of the draw,
with every vertex, colour, position and text, is compared with a copy of the slider's drawing code
as it was before the caching (commit 66fcdf8), over many states and in sequences that hit and
miss the caches. Also: S4-1, the digit counts kept per region are bounded and forgotten on load.
"""

import math
import traceback
import types
from types import SimpleNamespace

import bpy

from hindsight.bl import scrubber as S

ok = True


def run(fn):
    global ok
    try:
        fn()
        print("PASS", fn.__name__)
    except Exception:
        ok = False
        print("FAIL", fn.__name__)
        traceback.print_exc()


# ----------------------------------------------------------------------------- gpu / blf recorders
LOG = []


class _Shader:
    def bind(self):
        LOG.append(("bind",))

    def uniform_float(self, name, v):
        LOG.append(("uniform", name, tuple(v)))


class _Batch:
    def __init__(self, pts):
        self.pts = [tuple(p) for p in pts]

    def draw(self, shader):
        LOG.append(("draw", self.pts))


BUILT = [0]


def _batch_for_shader(shader, kind, data):
    assert kind == 'TRIS'
    BUILT[0] += 1
    return _Batch(data["pos"])


def _install_fakes():
    gpu = types.ModuleType("gpu")
    gpu.shader = SimpleNamespace(from_builtin=lambda name: _Shader())
    gpu.state = SimpleNamespace(blend_set=lambda m: LOG.append(("blend", m)))
    gx = types.ModuleType("gpu_extras")
    gxb = types.ModuleType("gpu_extras.batch")
    gxb.batch_for_shader = _batch_for_shader
    gx.batch = gxb
    blf = types.ModuleType("blf")
    blf.SHADOW = 4
    for n in ("size", "enable", "disable", "shadow", "shadow_offset", "color", "position", "draw"):
        setattr(blf, n, (lambda n: (lambda *a: LOG.append(("blf." + n,) + a)))(n))
    blf.dimensions = lambda f, t: (LOG.append(("blf.dimensions", f, t)), (len(t) * 6.5, 11.0))[1]
    saved = {k: sys.modules.get(k) for k in ("gpu", "gpu_extras", "gpu_extras.batch", "blf")}
    sys.modules.update({"gpu": gpu, "gpu_extras": gx, "gpu_extras.batch": gxb, "blf": blf})
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


# ----------------------------------------------------------------------------- reference (66fcdf8)
def _ref_rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]


def _ref_disc(cx, cy, r, seg=24, a0=0.0, a1=2.0 * math.pi):
    pts = []
    for i in range(seg):
        t0 = a0 + (a1 - a0) * i / seg
        t1 = a0 + (a1 - a0) * (i + 1) / seg
        pts += [(cx, cy),
                (cx + r * math.cos(t0), cy + r * math.sin(t0)),
                (cx + r * math.cos(t1), cy + r * math.sin(t1))]
    return pts


def _ref_capsule(x0, y0, x1, y1):
    r = (y1 - y0) / 2.0
    cy = (y0 + y1) / 2.0
    pts = _ref_rect(x0, y0, x1, y1) if x1 > x0 else []
    pts += _ref_disc(x0, cy, r, 10, 0.5 * math.pi, 1.5 * math.pi)
    pts += _ref_disc(max(x0, x1), cy, r, 10, -0.5 * math.pi, 0.5 * math.pi)
    return pts


def _ref_annulus(cx, cy, r0, r1, seg=32, a0=0.0, a1=2.0 * math.pi):
    pts = []
    for i in range(seg):
        t0 = a0 + (a1 - a0) * i / seg
        t1 = a0 + (a1 - a0) * (i + 1) / seg
        c0, s0, c1, s1 = math.cos(t0), math.sin(t0), math.cos(t1), math.sin(t1)
        p0, p1 = (cx + r0 * c0, cy + r0 * s0), (cx + r1 * c0, cy + r1 * s0)
        p2, p3 = (cx + r1 * c1, cy + r1 * s1), (cx + r0 * c1, cy + r0 * s1)
        pts += [p0, p1, p2, p0, p2, p3]
    return pts


def _ref_capsule_edge(x0, y0, x1, y1, w):
    r = (y1 - y0) / 2.0
    cy = (y0 + y1) / 2.0
    return (_ref_annulus(x0, cy, r, r + w, 10, 0.5 * math.pi, 1.5 * math.pi)
            + _ref_annulus(max(x0, x1), cy, r, r + w, 10, -0.5 * math.pi, 0.5 * math.pi))


def _ref_geometry(lay, pos, length, morph_target=None, hover=None):
    x0, y0, x1, y1 = lay["bar"]
    s = lay["scale"]
    aa = 0.9 * s
    cy = (y0 + y1) / 2.0
    xp = S.x_from_pos(pos, lay, length)
    hr = lay["handle_r"] + (1.5 * s if hover == "bar" else 0.0)
    cx, dcy, r = lay["dot"]
    ring = max(1.5, 1.6 * s)
    g = {
        "bg": _ref_capsule(x0, y0, x1, y1),
        "bg_aa": _ref_capsule_edge(x0, y0, x1, y1, aa),
        "fill": _ref_capsule(x0, y0, xp, y1),
        "fill_aa": _ref_capsule_edge(x0, y0, xp, y1, aa),
        "handle_shadow": _ref_disc(xp, cy - 0.8 * s, hr + 1.6 * s),
        "handle": _ref_disc(xp, cy, hr),
        "handle_aa": _ref_annulus(xp, cy, hr, hr + aa),
        "dot_bg": _ref_disc(cx, dcy, r),
        "dot": _ref_annulus(cx, dcy, r - ring, r),
        "dot_aa": _ref_annulus(cx, dcy, r, r + aa),
        "dot_core": _ref_disc(cx, dcy, r * (0.55 if hover == "dot" else 0.32)),
    }
    if morph_target is not None and length > 0:
        xm = S.x_from_pos(morph_target, lay, length)
        g["tick"] = _ref_rect(xm - 1.0 * s, y0 - 5.0 * s, xm + 1.0 * s, y1 + 5.0 * s)
    return g


def _ref_lighten(c, k=0.5):
    return (c[0] + (1.0 - c[0]) * k, c[1] + (1.0 - c[1]) * k, c[2] + (1.0 - c[2]) * k, 1.0)


def _ref_draw(info, prefs, region_w, region_h, ui_scale, insets, hover):
    """The drawing part of scrubber._draw at 66fcdf8, writing the same log as the recorders."""
    log = []
    length = int(info.get("len") or 0)
    lay = S.layout(region_w, region_h, ui_scale, prefs, insets, S.counter_digits(length))
    pos = int(info.get("pos") or 0)
    morph = info.get("morph_target")
    locked = bool(info.get("locked"))
    hover = None if locked else hover
    color = tuple(S._pref(prefs, "scrub_color"))[:4]
    bg = tuple(S._pref(prefs, "scrub_bg_color"))[:4]
    if locked:
        color = S.LOCKED_COLOR
    handle_col = _ref_lighten(color, 0.6 if hover == "bar" else 0.35)
    dot_col = _ref_lighten(color, 0.4) if hover == "dot" else color
    batches = {name: [tuple(p) for p in pts]
               for name, pts in _ref_geometry(lay, pos, length, morph, hover).items() if pts}
    log.append(("blend", 'ALPHA'))
    log.append(("bind",))
    soft = lambda c, k=0.4: (c[0], c[1], c[2], c[3] * k)
    for name, col in (("bg_aa", soft(bg)), ("bg", bg), ("fill_aa", soft(color)), ("fill", color),
                      ("tick", S.MORPH_COLOR), ("handle_shadow", (0.0, 0.0, 0.0, 0.28)),
                      ("handle_aa", soft(handle_col)), ("handle", handle_col),
                      ("dot_bg", (0.06, 0.06, 0.06, 0.72)), ("dot_aa", soft(dot_col)), ("dot", dot_col),
                      ("dot_core", dot_col)):
        b = batches.get(name)
        if b is not None:
            log.append(("uniform", "color", tuple(col)))
            log.append(("draw", b))
    log.append(("blend", 'NONE'))
    show_counter = bool(S._pref(prefs, "scrub_show_counter"))
    is_global = bool(info.get("global"))
    empty = bool(info.get("empty"))
    if not (show_counter or is_global or empty):
        return log
    font = 0
    log.append(("blf.size", font, lay["font_size"]))
    log.append(("blf.enable", font, 4))
    log.append(("blf.shadow", font, 3, 0.0, 0.0, 0.0, 0.7))
    log.append(("blf.shadow_offset", font, 1, -1))
    text_col = (0.6, 0.6, 0.6, 0.8) if locked else (1.0, 1.0, 1.0, 0.9)
    if empty:
        hint = "Make a stroke to start recording"
        log.append(("blf.dimensions", font, hint))
        tw = len(hint) * 6.5
        hx, hy = lay["hint"]
        log.append(("blf.color", font, 1.0, 1.0, 1.0, 0.6))
        log.append(("blf.position", font, hx - tw / 2.0, hy, 0))
        log.append(("blf.draw", font, hint))
    elif show_counter:
        x, y = lay["counter"]
        log.append(("blf.color", font) + tuple(text_col))
        log.append(("blf.position", font, x, y, 0))
        log.append(("blf.draw", font, "%d / %d" % (pos, length)))
    if is_global:
        x, y = lay["global"]
        log.append(("blf.color", font) + tuple(color))
        log.append(("blf.position", font, x, y, 0))
        log.append(("blf.draw", font, "G"))
    log.append(("blf.disable", font, 4))
    return log


# ----------------------------------------------------------------------------- driving the real _draw
class Prefs:
    def __init__(self, **kw):
        self.scrub_height = 6
        self.scrub_margin = 18
        self.scrub_width_pct = 60
        self.scrub_color = (1.0, 0.6, 0.24, 1.0)
        self.scrub_bg_color = (0.1, 0.1, 0.1, 0.6)
        self.scrub_show_counter = True
        self.__dict__.update(kw)


class Region:
    def __init__(self, w, h, ptr, typ='WINDOW', x=0, y=0, align="NONE"):
        self.width, self.height, self._ptr, self.type = w, h, ptr, typ
        self.x, self.y, self.alignment = x, y, align

    def as_pointer(self):
        return self._ptr


def _ctx(region, ui_scale, left=0.0, right=0.0):
    regions = [region]
    if left:
        regions.append(Region(int(left), region.height, region._ptr + 1, 'TOOLS', 0, 0, "LEFT"))
    if right:
        regions.append(Region(int(right), region.height, region._ptr + 2, 'UI',
                              region.width - int(right), 0, "RIGHT"))
    return SimpleNamespace(region=region, area=SimpleNamespace(regions=regions),
                           preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=ui_scale)))


def _new_draw(ctx, info, prefs, hover):
    S._prefs = lambda context=None: prefs
    S.slider_info = lambda context: info
    S._hover = (ctx.region.as_pointer(), hover)
    del LOG[:]
    S._draw(ctx)
    return list(LOG)


def _info(pos, n, **kw):
    d = {"pos": pos, "len": n, "cap": 10000, "global": False, "locked": False, "morph_target": None}
    d.update(kw)
    return d


def _states():
    P = Prefs
    for n in (0, 1, 7, 214, 999, 1000, 10000):
        for pos in sorted({0, 1, n // 3, n - 1, n} & set(range(0, n + 1))):
            yield _info(pos, n), P(), None
    yield _info(5, 10, morph_target=3), P(), None
    yield _info(5, 10, morph_target=0), P(), "bar"
    yield _info(5, 10, morph_target=None), P(), "dot"
    yield _info(5, 10, locked=True), P(), "bar"                  # locked: no hover, grey
    yield _info(5, 10, **{"global": True}), P(), None            # "G"
    yield _info(5, 10, **{"global": True}), P(scrub_show_counter=False), None
    yield _info(5, 10), P(scrub_show_counter=False), None        # no text at all
    yield {"pos": 0, "len": 0, "cap": 10000, "global": False, "locked": False, "morph_target": None,
           "empty": True}, P(), None                             # the empty track and the hint
    yield _info(3, 10), P(scrub_color=(0.2, 0.9, 0.3, 0.8), scrub_bg_color=(0.3, 0.3, 0.3, 1.0)), "bar"
    yield _info(3, 10), P(scrub_height=20, scrub_margin=0, scrub_width_pct=100), "dot"
    yield _info(3, 10), P(scrub_height=2, scrub_width_pct=5), None


def _compare_all(order):
    """Draw every state at several sizes, scales and panel layouts in the given order."""
    n = 0
    for (w, h, scale, left, right) in order:
        for info, prefs, hover in _states():
            ctx = _ctx(Region(w, h, 7000), scale, left, right)
            got = _new_draw(ctx, info, prefs, hover)
            want = _ref_draw(info, prefs, w, h, scale, S.region_insets(ctx.area, ctx.region), hover)
            assert got == want, (w, h, scale, left, right, info, vars(prefs), hover,
                                 next((i, a, b) for i, (a, b) in enumerate(zip(got, want)) if a != b)
                                 if len(got) == len(want) else (len(got), len(want)))
            n += 1
    return n


_SIZES = [(1574, 954, 1.0, 56.0, 0.0), (1574, 954, 1.0, 56.0, 320.0), (800, 500, 2.0, 112.0, 0.0),
          (420, 300, 1.0, 56.0, 250.0), (1000, 600, 1.5, 0.0, 0.0)]


def _with_fakes(fn):
    def wrapper():
        saved = _install_fakes()
        orig = (S._prefs, S.slider_info, S._hover, S._shader)
        S._shader = None
        S._forget_cache()
        try:
            fn()
        finally:
            S._prefs, S.slider_info, S._hover, S._shader = orig
            S._forget_cache()
            _restore(saved)
    wrapper.__name__ = fn.__name__
    return wrapper


@_with_fakes
def test_draw_same_as_before():
    n = _compare_all(_SIZES)
    # again, now from warm caches, and in another order (hits and misses mixed)
    n += _compare_all(list(reversed(_SIZES)) + _SIZES)
    assert n > 300, n


@_with_fakes
def test_geometry_same_as_before():
    for w, h, scale, left, right in _SIZES:
        lay = S.layout(w, h, scale, Prefs(), (left, right, 0.0), 4)
        for pos, n, mt, hover in ((0, 0, None, None), (5, 10, 3, "bar"), (10, 10, 0, "dot"),
                                  (1234, 10000, 9999, None), (1, 3, None, "bar")):
            assert S.geometry(lay, pos, n, mt, hover) == _ref_geometry(lay, pos, n, mt, hover)


@_with_fakes
def test_redraw_builds_nothing_new():
    """A redraw with nothing changed builds no batch; a stroke at the end of the history
    (pos == len, so the handle stays at the right end) builds none either; moving the handle
    rebuilds only the fill and the handle (5 parts), not all 11."""
    prefs = Prefs()
    ctx = _ctx(Region(1574, 954, 9100), 1.0, 56.0)
    BUILT[0] = 0
    _new_draw(ctx, _info(40, 80), prefs, None)
    assert BUILT[0] == 11, BUILT[0]
    for _ in range(5):
        _new_draw(ctx, _info(40, 80), prefs, None)
    assert BUILT[0] == 11, BUILT[0]
    BUILT[0] = 0
    _new_draw(ctx, _info(80, 80), prefs, None)            # handle to the end
    assert BUILT[0] == 5, BUILT[0]
    BUILT[0] = 0
    _new_draw(ctx, _info(81, 81), prefs, None)            # one more stroke: same place on screen
    _new_draw(ctx, _info(82, 82), prefs, None)
    assert BUILT[0] == 0, BUILT[0]
    BUILT[0] = 0
    _new_draw(ctx, _info(20, 82), prefs, "bar")           # drag: fill + handle only
    assert BUILT[0] == 5, BUILT[0]
    # every cache stays small whatever happens
    for i in range(200):
        _new_draw(ctx, _info(i, 200, morph_target=i // 2), prefs, ("bar", "dot", None)[i % 3])
    for cache in list(S._GROUPS.values()) + [S._PLANS, S._LAYOUTS]:
        assert len(cache) <= S.CACHE_MAX, len(cache)


@_with_fakes
def test_cache_follows_changes():
    """Colour, size, UI scale, panels and digits all change what is drawn on the next redraw."""
    ctx = _ctx(Region(1574, 954, 9200), 1.0, 56.0)
    prefs = Prefs()
    info = _info(3, 10)
    a = _new_draw(ctx, info, prefs, None)
    prefs.scrub_color = (0.0, 0.0, 1.0, 1.0)
    b = _new_draw(ctx, info, prefs, None)
    assert a != b and b == _ref_draw(info, prefs, 1574, 954, 1.0, (56.0, 0.0, 0.0), None)
    prefs.scrub_height = 12
    c = _new_draw(ctx, info, prefs, None)
    assert c == _ref_draw(info, prefs, 1574, 954, 1.0, (56.0, 0.0, 0.0), None)
    ctx.preferences.system.ui_scale = 2.0
    d = _new_draw(ctx, info, prefs, None)
    assert d == _ref_draw(info, prefs, 1574, 954, 2.0, (56.0, 0.0, 0.0), None)
    ctx.region.width = 900
    e = _new_draw(ctx, info, prefs, None)
    assert e == _ref_draw(info, prefs, 900, 954, 2.0, (56.0, 0.0, 0.0), None)
    info = _info(3, 12345)
    f = _new_draw(ctx, info, prefs, None)
    assert f == _ref_draw(info, prefs, 900, 954, 2.0, (56.0, 0.0, 0.0), None)
    # the hit test sees the same layout as the draw
    assert S._layout_for(ctx) is S._layout_cached(900, 954, 2.0, prefs, (56.0, 0.0, 0.0), 5)


@_with_fakes
def test_digits_bounded_and_forgotten_on_load():
    """S4-1: _DIGITS (region pointer -> digit count) was never pruned. Regions come and go and
    their pointers are reused, so a stale count could shift the hit test by a few pixels. It is
    bounded now and emptied when a file is loaded."""
    prefs = Prefs()
    for ptr in range(10_000, 10_100):
        _new_draw(_ctx(Region(1000, 600, ptr), 1.0), _info(5, 12345), prefs, None)
    assert len(S._DIGITS) <= S.DIGITS_MAX, len(S._DIGITS)
    assert S._DIGITS.get(10_099) == 5              # the newest regions are kept
    # a region drawn with a 5-digit history, then a file load: a new region at the same address
    # must hit-test like a fresh region (no digits kept), not like the old one
    ctx = _ctx(Region(1000, 600, 10_099), 1.0)
    S._prefs = lambda context=None: prefs
    stale = S._layout_for(ctx)
    fresh = S.layout(1000, 600, 1.0, prefs, (0.0, 0.0, 0.0), S.MIN_DIGITS)
    assert stale["bar"] != fresh["bar"]            # 5 digits really move the bar
    S.register()
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    finally:
        S.unregister()
    assert not S._DIGITS, S._DIGITS
    S._prefs = lambda context=None: prefs
    assert S._layout_for(ctx)["bar"] == fresh["bar"]


for f in (test_draw_same_as_before, test_geometry_same_as_before, test_redraw_builds_nothing_new,
          test_cache_follows_changes, test_digits_bounded_and_forgotten_on_load):
    run(f)

sys.stdout.flush()
os._exit(0 if ok else 1)
