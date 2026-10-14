"""Runs inside real Blender (with a window). Loads a scenario module and steps it on a timer.

A scenario is a module with ``def run(d):`` written as a generator. ``yield <seconds>`` hands
control back to Blender for that long (events are processed, the UI redraws), so the scenario
reads top to bottom like a user session:

    def run(d):
        yield 1.0
        d.shot("start")
        d.check("panel registered", hasattr(bpy.types, "VIEW3D_PT_hindsight"))

Results go to <out>/result.json; screenshots to <out>/shots/. Blender quits at the end.
"""
import bpy
import importlib.util
import io
import json
import os
import sys
import time
import traceback

SPEC = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
OUT = SPEC["out"]


class _Tee(io.TextIOBase):
    """Copies everything Blender/Python prints so scenarios can assert on it."""

    def __init__(self, real, sink):
        self.real = real
        self.sink = sink

    def write(self, s):
        try:
            self.real.write(s)
            self.real.flush()
        except Exception:
            pass
        self.sink.append(s)
        return len(s)

    def flush(self):
        try:
            self.real.flush()
        except Exception:
            pass


CAPTURED = []
sys.stdout = _Tee(sys.__stdout__, CAPTURED)
sys.stderr = _Tee(sys.__stderr__, CAPTURED)


class Driver:
    def __init__(self):
        self.spec = SPEC
        self.args = SPEC.get("args", {})
        self.out = OUT
        self.shots_dir = os.path.join(OUT, "shots")
        os.makedirs(self.shots_dir, exist_ok=True)
        self.checks = []
        self.notes = {}
        self.t0 = time.time()
        self.shot_n = 0

    # ------------------------------------------------------------------ reporting
    def log(self, *a):
        print("[driver %.1fs]" % (time.time() - self.t0), *a)

    def check(self, name, ok, detail=""):
        ok = bool(ok)
        self.checks.append({"name": name, "ok": ok, "detail": str(detail)[:2000]})
        self.log(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail)[:300] if detail != "" else ""))
        return ok

    def note(self, key, value):
        self.notes[key] = value

    def console(self):
        return "".join(CAPTURED)

    def console_mark(self):
        return len(CAPTURED)

    def console_since(self, mark):
        return "".join(CAPTURED[mark:])

    # ------------------------------------------------------------------ context
    @property
    def window(self):
        return bpy.context.window_manager.windows[0]

    def view3d(self, window=None):
        """(window, area, region WINDOW, region UI, space) of the largest 3D viewport."""
        win = window or self.window
        areas = [a for a in win.screen.areas if a.type == "VIEW_3D"]
        if not areas:
            return win, None, None, None, None
        area = max(areas, key=lambda a: a.width * a.height)
        reg = next(r for r in area.regions if r.type == "WINDOW")
        ui = next((r for r in area.regions if r.type == "UI"), None)
        return win, area, reg, ui, area.spaces.active

    def override(self, area_type="VIEW_3D", region_type="WINDOW", **extra):
        win, area, reg, ui, space = self.view3d()
        if area_type != "VIEW_3D":
            area = next((a for a in win.screen.areas if a.type == area_type), area)
        region = next((r for r in area.regions if r.type == region_type), reg)
        kw = dict(window=win, screen=win.screen, area=area, region=region)
        kw.update(extra)
        return bpy.context.temp_override(**kw)

    def redraw(self):
        try:
            with self.override():
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        except Exception as e:
            self.log("redraw failed", e)

    # ------------------------------------------------------------------ screenshots
    def shot(self, name, area=None, full=True):
        """Window screenshot (or one area). Returns the file path."""
        self.shot_n += 1
        fn = os.path.join(self.shots_dir, "%02d_%s.png" % (self.shot_n, name))
        self.redraw()
        try:
            win = self.window
            if area is None:
                with bpy.context.temp_override(window=win, screen=win.screen):
                    bpy.ops.screen.screenshot(filepath=fn, check_existing=False)
            else:
                region = next(r for r in area.regions if r.type == "WINDOW")
                with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
                    bpy.ops.screen.screenshot_area(filepath=fn, check_existing=False)
        except Exception as e:
            self.log("screenshot failed", name, e)
            return None
        self.log("shot", fn)
        return fn

    # ------------------------------------------------------------------ simulated input
    def ev(self, type, value="NOTHING", x=None, y=None, **kw):
        win = self.window
        if x is None:
            win.event_simulate(type=type, value=value, **kw)
        else:
            win.event_simulate(type=type, value=value, x=int(x), y=int(y), **kw)

    def region_to_win(self, region, rx, ry):
        return region.x + rx, region.y + ry

    def drag(self, region, pts, button="LEFTMOUSE", step_wait=0.02, ctrl=False, shift=False):
        """Generator: press at pts[0], move through pts, release at pts[-1] (region coords)."""
        x, y = self.region_to_win(region, *pts[0])
        self.ev("MOUSEMOVE", x=x, y=y)
        yield step_wait
        self.ev(button, "PRESS", x=x, y=y, ctrl=ctrl, shift=shift)
        yield step_wait
        for p in pts[1:]:
            x, y = self.region_to_win(region, *p)
            self.ev("MOUSEMOVE", x=x, y=y)
            yield step_wait
        self.ev(button, "RELEASE", x=x, y=y)
        yield step_wait

    def click(self, region, rx, ry, button="LEFTMOUSE"):
        x, y = self.region_to_win(region, rx, ry)
        self.ev("MOUSEMOVE", x=x, y=y)
        yield 0.05
        self.ev(button, "PRESS", x=x, y=y)
        yield 0.05
        self.ev(button, "RELEASE", x=x, y=y)
        yield 0.1

    def key(self, key, ctrl=False, shift=False, alt=False):
        self.ev(key, "PRESS", ctrl=ctrl, shift=shift, alt=alt)
        self.ev(key, "RELEASE", ctrl=ctrl, shift=shift, alt=alt)

    # ------------------------------------------------------------------ sculpt
    def brush_stroke(self, pts, pressure=1.0, size=None, mode="NORMAL"):
        """One real sculpt stroke through bpy.ops.sculpt.brush_stroke (region coords)."""
        win, area, reg, ui, space = self.view3d()
        names = {p.identifier for p in bpy.types.OperatorStrokeElement.bl_rna.properties}
        stroke = []
        t = 0.0
        for i, (x, y) in enumerate(pts):
            e = {"name": "", "mouse": (x, y), "pressure": pressure, "time": t, "is_start": i == 0}
            if "mouse_event" in names:
                e["mouse_event"] = (x, y)
            if "location" in names:
                e["location"] = (0.0, 0.0, 0.0)
            if "size" in names and size is not None:
                e["size"] = size
            if "pen_flip" in names:
                e["pen_flip"] = False
            if "x_tilt" in names:
                e["x_tilt"] = 0.0
                e["y_tilt"] = 0.0
            stroke.append(e)
            t += 0.01
        with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=reg):
            return bpy.ops.sculpt.brush_stroke(stroke=stroke, mode=mode)


D = Driver()
_gen = None
_finished = [False]


def _finish(error=None):
    if _finished[0]:
        return
    _finished[0] = True
    res = {"ok": error is None and all(c["ok"] for c in D.checks), "checks": D.checks,
           "notes": D.notes, "error": error, "blender": bpy.app.version_string,
           "seconds_in_blender": round(time.time() - D.t0, 1)}
    with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    D.log("finished ok=%s" % res["ok"])
    if D.args.get("keep_open") == "1":
        return

    def _quit():
        try:
            bpy.context.preferences.view.use_save_prompt = False
        except Exception:
            pass
        try:
            win = bpy.context.window_manager.windows[0]
            with bpy.context.temp_override(window=win):
                bpy.ops.wm.quit_blender()
        except Exception:
            os._exit(0)
        return None
    bpy.app.timers.register(_quit, first_interval=0.2)


def _step():
    global _gen
    try:
        if _gen is None:
            spec = importlib.util.spec_from_file_location("hs_scenario", SPEC["scenario"])
            mod = importlib.util.module_from_spec(spec)
            sys.modules["hs_scenario"] = mod
            spec.loader.exec_module(mod)
            _gen = _flatten(mod.run(D))
        wait = next(_gen)
        return max(0.01, float(wait or 0.01))
    except StopIteration:
        _finish()
        return None
    except Exception:
        tb = traceback.format_exc()
        print(tb)
        _finish(error=tb)
        return None


def _flatten(gen):
    """Lets scenarios `yield from d.drag(...)` or yield nested generators."""
    for item in gen:
        if hasattr(item, "__next__"):
            yield from _flatten(item)
        else:
            yield item


if bpy.app.background:
    # Background runs: no timers fire before exit, so step synchronously.
    while True:
        r = _step()
        if r is None:
            break
        time.sleep(min(r, 0.05))
else:
    bpy.app.timers.register(_step, first_interval=float(D.args.get("start_delay", 1.5)), persistent=True)
