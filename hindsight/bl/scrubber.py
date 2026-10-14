"""On-screen slider: a history slider along the bottom of every 3D Viewport.

Drawing is done by a POST_PIXEL draw handler; input goes through a 2D gizmo (gizmos get
events before the Sculpt keymap, which binds LEFTMOUSE to brush strokes). A fallback modal
operator ``hindsight.scrub`` implements the same drag for use from Python.

The slider sits above bottom-docked overlapping regions (the brush asset shelf in Sculpt Mode on
Blender 4.3+) and between the toolbar and the sidebar, so it is never drawn under them. In Sculpt
and Vertex Paint Mode it shows from the start, as an empty track, before any step exists.
The draw handler is guarded: if it ever raises, it is removed and the sidebar says so.

The layout helpers (``layout``, ``pos_from_x``, ``x_from_pos``, ``hit_test``, ``geometry``)
are pure and testable headless. Nothing touches ``gpu`` until the draw callback runs.
"""

import math
import time

import bpy

from . import guard

MENU_NAME = "HINDSIGHT_MT_history"
THROTTLE = 1.0 / 60.0      # min seconds between live (final=False) gotos during a drag
HIT_PAD = 10.0             # bar hit area extends this many px (x ui_scale) above/below the bar
FONT_SIZE = 11.0           # counter font size in px (x ui_scale)
MORPH_COLOR = (0.35, 0.85, 1.0, 1.0)
LOCKED_COLOR = (0.55, 0.55, 0.55, 0.7)

_clock = time.perf_counter  # patched by tests


class _Defaults:
    scrub_height = 6
    scrub_margin = 18
    scrub_width_pct = 60
    scrub_color = (1.0, 0.6, 0.24, 1.0)
    scrub_bg_color = (0.1, 0.1, 0.1, 0.6)
    scrub_show_counter = True


DEFAULTS = _Defaults()


# ---------------------------------------------------------------------------------------------
# Lazy access to sibling modules (written concurrently; tests may replace these functions)

def _runtime():
    from . import runtime
    return runtime


def _prefs(context=None):
    try:
        from .prefs import get_prefs
        p = get_prefs(context)
    except Exception:
        p = None
    return p if p is not None else DEFAULTS


def _pref(p, name):
    v = getattr(p, name, None) if p is not None else None
    return getattr(DEFAULTS, name) if v is None else v


def _ui_scale(context):
    try:
        s = float(context.preferences.system.ui_scale)
    except Exception:
        s = 1.0
    return s if s > 0.0 else 1.0


_reported = set()


def _report(where, exc):
    """Input errors (gizmo, modal) are logged once; they never switch the slider off."""
    key = (where, type(exc).__name__, str(exc))
    if key in _reported or len(_reported) > 32:
        return
    _reported.add(key)
    from . import log
    log.error_once(("slider", where, str(exc)), f"on-screen slider ({where}): {type(exc).__name__}: {exc}", exc)


# ---------------------------------------------------------------------------------------------
# Pure layout

def layout(region_w, region_h, ui_scale, prefs_like, insets=(0.0, 0.0, 0.0)):
    """Pixel layout of the scrubber in region space (origin bottom-left).

    insets = (left, right, bottom) pixels covered by overlapping regions (toolbar, sidebar,
    asset shelf); the slider is centred in what is left and sits above the bottom inset.
    """
    s = float(ui_scale) if ui_scale and ui_scale > 0 else 1.0
    left, right, bottom = (max(0.0, float(v)) for v in insets)
    h = max(1.0, float(_pref(prefs_like, "scrub_height"))) * s
    m = max(0.0, float(_pref(prefs_like, "scrub_margin"))) * s + bottom
    pct = min(100.0, max(1.0, float(_pref(prefs_like, "scrub_width_pct"))))
    avail = max(40.0 * s, float(region_w) - left - right)
    w = avail * pct / 100.0
    x0 = left + (avail - w) / 2.0
    x1 = x0 + w
    y0 = float(round(m))
    y1 = y0 + float(round(h))
    cy = (y0 + y1) / 2.0
    cap = h / 2.0                       # rounded caps extend past x0 / x1
    r = max(5.0, float(_pref(prefs_like, "scrub_height")) / 2.0 + 3.0) * s
    gap = 8.0 * s
    dot_cx = x0 - cap - gap - r
    font = FONT_SIZE * s
    return {
        "bar": (x0, y0, x1, y1),
        "dot": (dot_cx, cy, r),
        "counter": (x1 + cap + gap, cy - font * 0.35),
        "hint": ((x0 + x1) / 2.0, y1 + 7.0 * s),
        "global": (dot_cx - r * 0.45, cy + r + 4.0 * s),
        "font_size": font,
        "scale": s,
        "hit_pad": HIT_PAD * s,
        "hit_pad_x": cap + 2.0 * s,
        "dot_pad": 3.0 * s,
        "handle_r": cap + 3.0 * s,
        "region": (int(region_w), int(region_h)),
    }


def _layout_key(lay):
    return (lay["bar"], lay["dot"], lay["scale"])


def pos_from_x(x, lay, length):
    """Region x -> history position, clamped to 0..length, rounded to the nearest step."""
    length = int(length)
    x0, _, x1, _ = lay["bar"]
    if length <= 0 or x1 <= x0:
        return 0
    t = min(1.0, max(0.0, (float(x) - x0) / (x1 - x0)))
    return int(math.floor(t * length + 0.5))


def x_from_pos(pos, lay, length):
    x0, _, x1, _ = lay["bar"]
    if int(length) <= 0:
        return x0
    t = min(1.0, max(0.0, float(pos) / float(length)))
    return x0 + (x1 - x0) * t


def hit_test(x, y, lay):
    """"dot", "bar" or None for a region-space point."""
    cx, cy, r = lay["dot"]
    rr = r + lay.get("dot_pad", 0.0)
    if (x - cx) ** 2 + (y - cy) ** 2 <= rr * rr:
        return "dot"
    x0, y0, x1, y1 = lay["bar"]
    pad = lay.get("hit_pad", HIT_PAD)
    padx = lay.get("hit_pad_x", 0.0)
    if x0 - padx <= x <= x1 + padx and y0 - pad <= y <= y1 + pad:
        return "bar"
    return None


# ---------------------------------------------------------------------------------------------
# Pure geometry (triangle lists)

def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]


def _disc(cx, cy, r, seg=24, a0=0.0, a1=2.0 * math.pi):
    pts = []
    for i in range(seg):
        t0 = a0 + (a1 - a0) * i / seg
        t1 = a0 + (a1 - a0) * (i + 1) / seg
        pts += [(cx, cy),
                (cx + r * math.cos(t0), cy + r * math.sin(t0)),
                (cx + r * math.cos(t1), cy + r * math.sin(t1))]
    return pts


def _capsule(x0, y0, x1, y1):
    """Rect x0..x1 with non-overlapping half-disc caps outside it (safe for alpha blending)."""
    r = (y1 - y0) / 2.0
    cy = (y0 + y1) / 2.0
    pts = _rect(x0, y0, x1, y1) if x1 > x0 else []
    pts += _disc(x0, cy, r, 10, 0.5 * math.pi, 1.5 * math.pi)
    pts += _disc(max(x0, x1), cy, r, 10, -0.5 * math.pi, 0.5 * math.pi)
    return pts


def _annulus(cx, cy, r0, r1, seg=32, a0=0.0, a1=2.0 * math.pi):
    """Ring between radius r0 and r1 (also used as a soft edge, Blender's flat shader has no AA)."""
    pts = []
    for i in range(seg):
        t0 = a0 + (a1 - a0) * i / seg
        t1 = a0 + (a1 - a0) * (i + 1) / seg
        c0, s0, c1, s1 = math.cos(t0), math.sin(t0), math.cos(t1), math.sin(t1)
        p0, p1 = (cx + r0 * c0, cy + r0 * s0), (cx + r1 * c0, cy + r1 * s0)
        p2, p3 = (cx + r1 * c1, cy + r1 * s1), (cx + r0 * c1, cy + r0 * s1)
        pts += [p0, p1, p2, p0, p2, p3]
    return pts


def _capsule_edge(x0, y0, x1, y1, w):
    """Soft edge around the two round caps of a capsule."""
    r = (y1 - y0) / 2.0
    cy = (y0 + y1) / 2.0
    return (_annulus(x0, cy, r, r + w, 10, 0.5 * math.pi, 1.5 * math.pi)
            + _annulus(max(x0, x1), cy, r, r + w, 10, -0.5 * math.pi, 0.5 * math.pi))


def geometry(lay, pos, length, morph_target=None, hover=None):
    """{name: [(x, y), ...]} triangle lists. Drawn in DRAW_ORDER; *_aa are one-pixel soft edges."""
    x0, y0, x1, y1 = lay["bar"]
    s = lay["scale"]
    aa = 0.9 * s
    cy = (y0 + y1) / 2.0
    xp = x_from_pos(pos, lay, length)
    hr = lay["handle_r"] + (1.5 * s if hover == "bar" else 0.0)
    cx, dcy, r = lay["dot"]
    ring = max(1.5, 1.6 * s)
    g = {
        "bg": _capsule(x0, y0, x1, y1),
        "bg_aa": _capsule_edge(x0, y0, x1, y1, aa),
        "fill": _capsule(x0, y0, xp, y1),
        "fill_aa": _capsule_edge(x0, y0, xp, y1, aa),
        "handle_shadow": _disc(xp, cy - 0.8 * s, hr + 1.6 * s),
        "handle": _disc(xp, cy, hr),
        "handle_aa": _annulus(xp, cy, hr, hr + aa),
        "dot_bg": _disc(cx, dcy, r),
        "dot": _annulus(cx, dcy, r - ring, r),
        "dot_aa": _annulus(cx, dcy, r, r + aa),
        "dot_core": _disc(cx, dcy, r * (0.55 if hover == "dot" else 0.32)),
    }
    if morph_target is not None and length > 0:
        xm = x_from_pos(morph_target, lay, length)
        g["tick"] = _rect(xm - 1.0 * s, y0 - 5.0 * s, xm + 1.0 * s, y1 + 5.0 * s)
    return g


# ---------------------------------------------------------------------------------------------
# Drag logic shared by the gizmo and the fallback operator

class _Drag:
    def __init__(self, lay, info):
        self.lay = lay
        self.length = int(info.get("len") or 0)
        self.start_pos = int(info.get("pos") or 0)
        self.applied = self.start_pos     # last position sent to runtime.goto
        self.target = self.start_pos      # latest position under the mouse
        self.last_t = -1.0e9
        self.timer = None

    def begin(self, context, x):
        self._add_timer(context)
        self.target = pos_from_x(x, self.lay, self.length)
        self._apply(context, force=True)

    def move(self, context, x):
        self.target = pos_from_x(x, self.lay, self.length)
        self._apply(context)

    def flush(self, context):
        self._apply(context)

    def _apply(self, context, force=False):
        if self.target == self.applied:
            return
        now = _clock()
        if not force and now - self.last_t < THROTTLE:
            return
        rt = _runtime()
        rt.goto(context, self.target, final=False)
        self.applied = self.target
        self.last_t = now
        rt.tag_redraw_all()

    def end(self, context, cancel=False):
        self._remove_timer(context)
        pos = self.start_pos if cancel else self.target
        rt = _runtime()
        rt.goto(context, pos, final=True)
        self.applied = pos
        rt.tag_redraw_all()
        return pos

    def _add_timer(self, context):
        try:
            win = getattr(context, "window", None)
            if win is not None:
                self.timer = context.window_manager.event_timer_add(THROTTLE, window=win)
        except Exception:
            self.timer = None

    def _remove_timer(self, context):
        if self.timer is None:
            return
        try:
            context.window_manager.event_timer_remove(self.timer)
        except Exception:
            pass
        self.timer = None


_OVERLAP_BOTTOM = {"ASSET_SHELF", "ASSET_SHELF_HEADER"}
_OVERLAP_SIDE = {"TOOLS", "UI", "TOOL_PROPS"}


def region_insets(area, region):
    """(left, right, bottom) pixels of `region` covered by other regions of the same area.

    With Region Overlap on (Blender's default) the toolbar, the sidebar and the asset shelf are
    drawn on top of the viewport region. Hidden regions report a width or height of 1.
    """
    left = right = bottom = 0.0
    if area is None or region is None:
        return left, right, bottom
    try:
        for r in area.regions:
            if r == region or r.width <= 1 or r.height <= 1:
                continue
            align = getattr(r, "alignment", "")
            if r.type in _OVERLAP_BOTTOM and align == "BOTTOM":
                bottom = max(bottom, float(r.y + r.height - region.y))
            elif r.type in _OVERLAP_SIDE and align == "LEFT":
                left = max(left, float(r.x + r.width - region.x))
            elif r.type in _OVERLAP_SIDE and align == "RIGHT":
                right = max(right, float(region.x + region.width - r.x))
    except Exception:
        return 0.0, 0.0, 0.0
    if left + right > region.width * 0.8:   # tiny viewport: ignore side panels rather than vanish
        left = right = 0.0
    if bottom > region.height * 0.7:
        bottom = 0.0
    return left, right, bottom


def _layout_for(context, region=None):
    region = region if region is not None else context.region
    area = getattr(context, "area", None)
    return layout(region.width, region.height, _ui_scale(context), _prefs(context),
                  region_insets(area, region))


def slider_info(context):
    """What the slider shows: runtime.scrub_info, or an empty track in Sculpt / Vertex Paint Mode
    on an object that will record its first stroke. None = draw nothing."""
    rt = _runtime()
    info = rt.scrub_info(context)
    if info and info.get("len"):
        return info
    if rt.use_global(context):
        return None
    obj = getattr(context, "active_object", None)
    if obj is None or getattr(obj, "type", None) != "MESH" or obj.mode not in rt.RECORD_MODES:
        return None
    if not rt.can_record(obj):
        return None
    return {"pos": 0, "len": 0, "cap": rt.SETTINGS.max_steps, "global": False, "locked": False,
            "morph_target": None, "empty": True}


def _open_menu(context):
    try:
        bpy.ops.wm.call_menu(name=MENU_NAME)
    except Exception as exc:
        _report("menu", exc)


# ---------------------------------------------------------------------------------------------
# Drawing

_draw_handle = None
_shader = None
_batch_cache = {}
_hover = (0, None)   # (region pointer, "dot" | "bar" | None)


def _hover_for(region):
    try:
        ptr = region.as_pointer()
    except Exception:
        return None
    return _hover[1] if _hover[0] == ptr else None


def _set_hover(region, part):
    global _hover
    try:
        _hover = (region.as_pointer(), part)
    except Exception:
        pass


def _batches(lay, pos, length, morph_target, hover):
    global _shader
    key = (_layout_key(lay), pos, length, morph_target, hover)
    b = _batch_cache.get(key)
    if b is not None:
        return _shader, b
    import gpu
    from gpu_extras.batch import batch_for_shader
    if _shader is None:
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    if len(_batch_cache) > 16:
        _batch_cache.clear()
    b = {name: batch_for_shader(_shader, 'TRIS', {"pos": pts})
         for name, pts in geometry(lay, pos, length, morph_target, hover).items() if pts}
    _batch_cache[key] = b
    return _shader, b


def _lighten(c, k=0.5):
    return (c[0] + (1.0 - c[0]) * k, c[1] + (1.0 - c[1]) * k, c[2] + (1.0 - c[2]) * k, 1.0)


def _draw(context):
    region = context.region
    if region is None or region.type != 'WINDOW':
        return
    info = slider_info(context)
    guard.mark_ok("slider")
    if not info:
        return
    prefs = _prefs(context)
    lay = layout(region.width, region.height, _ui_scale(context), prefs,
                 region_insets(context.area, region))
    length = int(info.get("len") or 0)
    pos = int(info.get("pos") or 0)
    morph = info.get("morph_target")
    locked = bool(info.get("locked"))
    hover = None if locked else _hover_for(region)

    color = tuple(_pref(prefs, "scrub_color"))[:4]
    bg = tuple(_pref(prefs, "scrub_bg_color"))[:4]
    if locked:
        color = LOCKED_COLOR
    handle_col = _lighten(color, 0.6 if hover == "bar" else 0.35)
    dot_col = _lighten(color, 0.4) if hover == "dot" else color

    import gpu
    shader, batches = _batches(lay, pos, length, morph, hover)
    gpu.state.blend_set('ALPHA')
    shader.bind()
    soft = lambda c, k=0.4: (c[0], c[1], c[2], c[3] * k)
    for name, col in (("bg_aa", soft(bg)), ("bg", bg), ("fill_aa", soft(color)), ("fill", color),
                      ("tick", MORPH_COLOR), ("handle_shadow", (0.0, 0.0, 0.0, 0.28)),
                      ("handle_aa", soft(handle_col)), ("handle", handle_col),
                      ("dot_bg", (0.06, 0.06, 0.06, 0.72)), ("dot_aa", soft(dot_col)), ("dot", dot_col),
                      ("dot_core", dot_col)):
        b = batches.get(name)
        if b is not None:
            shader.uniform_float("color", col)
            b.draw(shader)
    gpu.state.blend_set('NONE')

    show_counter = bool(_pref(prefs, "scrub_show_counter"))
    is_global = bool(info.get("global"))
    empty = bool(info.get("empty"))
    if not (show_counter or is_global or empty):
        return
    import blf
    font = 0
    blf.size(font, lay["font_size"])
    shadow = getattr(blf, "SHADOW", None)
    if shadow is not None:
        try:
            blf.enable(font, shadow)
            blf.shadow(font, 3, 0.0, 0.0, 0.0, 0.7)
            blf.shadow_offset(font, 1, -1)
        except Exception:
            shadow = None
    text_col = (0.6, 0.6, 0.6, 0.8) if locked else (1.0, 1.0, 1.0, 0.9)
    if empty:
        hint = "Make a stroke to start recording"
        tw, _ = blf.dimensions(font, hint)
        hx, hy = lay["hint"]
        blf.color(font, 1.0, 1.0, 1.0, 0.6)
        blf.position(font, hx - tw / 2.0, hy, 0)
        blf.draw(font, hint)
    elif show_counter:
        x, y = lay["counter"]
        blf.color(font, *text_col)
        blf.position(font, x, y, 0)
        blf.draw(font, "%d / %d" % (pos, length))
    if is_global:
        x, y = lay["global"]
        blf.color(font, *color)
        blf.position(font, x, y, 0)
        blf.draw(font, "G")
    if shadow is not None:
        blf.disable(font, shadow)


def draw_callback():
    try:
        _draw(bpy.context)
    except Exception as exc:
        try:
            import gpu
            gpu.state.blend_set('NONE')
        except Exception:
            pass
        guard.fail("slider", exc, "draw")


def _remove_draw_handler():
    """Called once if drawing fails: only the slider goes away, nothing else."""
    global _draw_handle
    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            pass
        _draw_handle = None


guard.on_fail("slider", _remove_draw_handler)


def draw_handler_installed():
    return _draw_handle is not None


# ---------------------------------------------------------------------------------------------
# Input: 2D gizmo (takes priority over the Sculpt keymap)

def _scrub_poll(context):
    if not guard.ok("slider"):
        return False
    try:
        return slider_info(context) is not None
    except Exception:
        return False


class HINDSIGHT_GT_scrubber(bpy.types.Gizmo):
    bl_idname = "HINDSIGHT_GT_scrubber"
    __slots__ = ("_drag", "_mode")

    def setup(self):
        self._drag = None
        self._mode = None

    def draw(self, context):
        pass  # the draw handler draws the scrubber

    def draw_select(self, context, select_id):
        pass

    def test_select(self, context, location):
        region = context.region
        try:
            hit = hit_test(location[0], location[1], _layout_for(context, region))
        except Exception as exc:
            _report("test_select", exc)
            hit = None
        _set_hover(region, hit)
        return 0 if hit else -1

    def invoke(self, context, event):
        self._mode = None
        self._drag = None
        try:
            lay = _layout_for(context)
            x, y = event.mouse_region_x, event.mouse_region_y
            hit = hit_test(x, y, lay)
            if hit == "dot":
                # Open the menu on release (exit): returning FINISHED here would pass the
                # press through to the sculpt brush.
                self._mode = "dot"
                return {'RUNNING_MODAL'}
            if hit == "bar":
                info = _runtime().scrub_info(context)
                if info and not info.get("locked") and info.get("len"):
                    drag = _Drag(lay, info)
                    self._drag = drag
                    self._mode = "bar"
                    drag.begin(context, x)
                return {'RUNNING_MODAL'}  # locked: swallow the click, ignore the drag
        except Exception as exc:
            _report("invoke", exc)
            if self._mode == "bar":
                return {'RUNNING_MODAL'}
        return {'CANCELLED'}

    def modal(self, context, event, tweak):
        drag = getattr(self, "_drag", None)
        if getattr(self, "_mode", None) == "bar" and drag is not None:
            try:
                drag.move(context, event.mouse_region_x)
            except Exception as exc:
                _report("modal", exc)
        return {'RUNNING_MODAL'}

    def exit(self, context, cancel):
        mode = getattr(self, "_mode", None)
        drag = getattr(self, "_drag", None)
        self._mode = None
        self._drag = None
        try:
            if mode == "bar" and drag is not None:
                drag.end(context, cancel)
            elif mode == "dot" and not cancel:
                _open_menu(context)
        except Exception as exc:
            _report("exit", exc)


class HINDSIGHT_GGT_scrubber(bpy.types.GizmoGroup):
    bl_idname = "HINDSIGHT_GGT_scrubber"
    bl_label = "Hindsight Scrubber"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'PERSISTENT', 'SHOW_MODAL_ALL'}

    @classmethod
    def poll(cls, context):
        return _scrub_poll(context)

    def setup(self, context):
        self.scrubber = self.gizmos.new(HINDSIGHT_GT_scrubber.bl_idname)


# ---------------------------------------------------------------------------------------------
# Fallback modal operator (same drag, driven by event.mouse_region_x)

class HINDSIGHT_OT_scrub(bpy.types.Operator):
    bl_idname = "hindsight.scrub"
    bl_label = "Scrub History"
    bl_description = "Drag along the undo history scrubber"

    @classmethod
    def poll(cls, context):
        return _scrub_poll(context)

    def invoke(self, context, event):
        region = context.region
        if region is None:
            return {'CANCELLED'}
        info = _runtime().scrub_info(context)
        if not info or info.get("locked"):
            return {'CANCELLED'}
        self._drag = _Drag(_layout_for(context, region), info)
        self._drag.begin(context, event.mouse_region_x)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        drag = self._drag
        if (event.type == 'ESC' and event.value == 'PRESS') or \
                (event.type == 'RIGHTMOUSE' and event.value == 'PRESS'):
            drag.end(context, cancel=True)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            drag.target = pos_from_x(event.mouse_region_x, drag.lay, drag.length)
            drag.end(context, cancel=False)
            return {'FINISHED'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'TIMER'}:
            drag.move(context, event.mouse_region_x)
        return {'RUNNING_MODAL'}


# ---------------------------------------------------------------------------------------------

classes = (HINDSIGHT_GT_scrubber, HINDSIGHT_GGT_scrubber, HINDSIGHT_OT_scrub)


def register():
    global _draw_handle
    for cls in classes:
        if "bl_rna" not in cls.__dict__:
            bpy.utils.register_class(cls)
    ensure_draw_handler()


def ensure_draw_handler():
    global _draw_handle
    if _draw_handle is None and guard.ok("slider"):
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _draw_handle, _shader
    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            pass
        _draw_handle = None
    for cls in reversed(classes):
        if "bl_rna" in cls.__dict__:
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
    _batch_cache.clear()
    _shader = None
