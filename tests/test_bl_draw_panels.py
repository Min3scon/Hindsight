import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Redraw paths outside the slider draw the same thing for less: the viewport warning overlay checks
only what gives an error (not every warning), and wrapped sidebar texts are kept between redraws."""

import textwrap
import traceback
import types
from types import SimpleNamespace

import bpy

from hindsight.bl import ui_panel, warnings as W

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


def _cube():
    bpy.ops.mesh.primitive_cube_add()
    return bpy.context.active_object


def _errors_of_collect(obj, ctx=None):
    return [t for s, t in W.collect(ctx or bpy.context, obj) if s == W.ERROR]


def test_overlay_errors_same_as_collect():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    edit = bpy.context.preferences.edit
    old = edit.use_global_undo
    objs = []
    try:
        plain = _cube()
        objs.append(plain)
        multi = _cube()
        multi.modifiers.new("m", 'MULTIRES')
        multi.modifiers.new("a", 'ARMATURE')
        multi.shape_key_add(name="Basis")
        multi.vertex_groups.new(name="g")
        objs.append(multi)
        sub = _cube()
        sub.modifiers.new("s", 'SUBSURF')
        sub.keyframe_insert("location")
        objs.append(sub)
        dyn = SimpleNamespace(mode='SCULPT', use_dynamic_topology_sculpting=True, modifiers=[], data=None,
                              type='MESH', animation_data=None, vertex_groups=[])
        dyn_multi = SimpleNamespace(mode='SCULPT', use_dynamic_topology_sculpting=True,
                                    modifiers=[SimpleNamespace(type='MULTIRES')], data=None, type='MESH',
                                    animation_data=None, vertex_groups=[])
        objs += [dyn, dyn_multi]
        for undo in (True, False):
            edit.use_global_undo = undo
            for ob in objs:
                assert W.error_texts(bpy.context, ob) == _errors_of_collect(ob), (ob, undo)
        edit.use_global_undo = False
        assert W.error_texts(bpy.context, dyn_multi) == [W.TXT_DYNTOPO, W.TXT_MULTIRES, W.TXT_GLOBAL_UNDO]
        assert W.error_texts(bpy.context, None) == [] == _errors_of_collect(None)
    finally:
        edit.use_global_undo = old


class _Region:
    type = 'WINDOW'
    height = 800
    x = 0


def test_overlay_draws_errors_without_other_checks():
    """The overlay runs on every viewport redraw in Sculpt, Vertex Paint and Edit Mode. It draws
    only errors, so it must not pay for the warning and Record everything checks."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ob = _cube()
    ob.modifiers.new("m", 'MULTIRES')
    ob.vertex_groups.new(name="g")
    log = []
    blf = types.ModuleType("blf")
    blf.SHADOW = 4
    for n in ("size", "color", "enable", "disable", "shadow", "shadow_offset", "position", "draw"):
        setattr(blf, n, (lambda n: (lambda *a: log.append((n,) + a)))(n))
    saved_blf, saved_bpy = sys.modules.get("blf"), W.bpy
    sys.modules["blf"] = blf
    fake_ob = SimpleNamespace(mode='SCULPT', use_dynamic_topology_sculpting=False, modifiers=ob.modifiers,
                              data=ob.data, type='MESH', animation_data=None, vertex_groups=ob.vertex_groups,
                              material_slots=())
    ctx = SimpleNamespace(region=_Region(), active_object=fake_ob, area=SimpleNamespace(regions=[]),
                          preferences=bpy.context.preferences, scene=bpy.context.scene)
    W.bpy = SimpleNamespace(context=ctx, types=bpy.types)
    calls = []
    orig = W.everything_notes
    W.everything_notes = lambda c, o: (calls.append(o), [])[1]
    try:
        W._draw_overlay()
        assert calls == [], "the overlay ran the Record everything checks"
        drawn = [a[2] for a in log if a[0] == "draw"]
        assert drawn == ["Hindsight: " + W.TXT_MULTIRES], drawn
        assert drawn == ["Hindsight: " + t for t in _errors_of_collect(fake_ob)]
    finally:
        W.everything_notes = orig
        W.bpy = saved_bpy
        if saved_blf is None:
            sys.modules.pop("blf", None)
        else:
            sys.modules["blf"] = saved_blf


class _Layout:
    def __init__(self, log):
        self.log = log

    def column(self, align=False):
        return self

    def label(self, text="", icon='NONE'):
        self.log.append((text, icon))


def test_wrap_same_lines_kept():
    texts = [ui_panel.NO_HISTORY, ui_panel.OBJECTS_OFF, ui_panel.NO_HISTORY_OTHER, "", "short",
             "Recording is paused: Dyntopo is on. Hindsight doesn't support that yet."]
    for scale in (1.0, 1.5, 2.0):
        for width in (None, 120, 260, 400, 1200):
            ctx = SimpleNamespace(preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=scale)),
                                  region=SimpleNamespace(width=width) if width else None)
            for icon in ('NONE', 'INFO'):
                for units in (None, 13):
                    for text in texts:
                        for _ in range(2):            # the second time from the kept lines
                            log = []
                            ui_panel.wrap(_Layout(log), ctx, text, icon, units)
                            w = units * 20 * scale if units is not None else (width or 260 * scale)
                            chars = max(18, int((w - (44 if icon != 'NONE' else 24) * scale) / (6.3 * scale)))
                            want = textwrap.wrap(text, chars, break_on_hyphens=False) or [""]
                            assert [t for t, _i in log] == want, (text, chars, log)
                            assert [i for _t, i in log] == [icon] + [
                                'BLANK1' if icon != 'NONE' else 'NONE'] * (len(want) - 1)
    assert len(ui_panel._WRAPPED) <= 64


class _AnyLayout:
    def __init__(self, log):
        self.log = log

    def __getattr__(self, name):
        def fn(*a, **kw):
            self.log.append((name, kw.get("text"), kw.get("icon")))
            return self
        return fn


def test_warnings_panel_checks_once_per_redraw():
    """The Warnings sub-panel's poll ran every check and its draw ran them all again. The draw now
    takes the list its poll just made, once, for the same object and region."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    a, b = _cube(), _cube()
    calls = []

    class RT:
        def warnings_for(self, context, obj):
            calls.append(obj.name)
            return [("ERROR", "Bad " + obj.name), ("INFO", "Note")]
    P = ui_panel.VIEW3D_PT_hindsight_warnings
    orig = ui_panel._rt
    ui_panel._rt = lambda: RT()
    try:
        ctx = SimpleNamespace(active_object=a, region=None, preferences=bpy.context.preferences)

        def draw():
            log = []
            P.draw(SimpleNamespace(layout=_AnyLayout(log)), ctx)
            return [(t, i) for n, t, i in log if n == "label"]
        assert P.poll(ctx) is True and calls == [a.name]
        assert draw() == [("Bad " + a.name, 'ERROR'), ("Note", 'INFO')] and calls == [a.name]
        assert draw() == [("Bad " + a.name, 'ERROR'), ("Note", 'INFO')] and calls == [a.name] * 2  # taken once
        P.poll(ctx)
        ctx.active_object = b                       # another object than the poll saw: checked again
        assert draw() == [("Bad " + b.name, 'ERROR'), ("Note", 'INFO')] and calls == [a.name] * 3 + [b.name]
        ui_panel._rt = lambda: None                 # no runtime: the poll says no, draw shows nothing
        assert P.poll(ctx) is False and draw() == []
    finally:
        ui_panel._rt = orig
        P._polled[0] = None


for f in (test_overlay_errors_same_as_collect, test_overlay_draws_errors_without_other_checks,
          test_wrap_same_lines_kept, test_warnings_panel_checks_once_per_redraw):
    run(f)

sys.stdout.flush()
os._exit(0 if ok else 1)
