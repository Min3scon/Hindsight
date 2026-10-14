"""Self-check, Copy Diagnostics, the first-run notice and a few small operators.

Run Self-check works inside the live session: it looks at what is registered, whether the slider
has drawn recently, the shortcuts, the handlers, and does a record and scrub round trip on a
throwaway mesh that is never linked to the scene and never touches the user's history.
"""
import dataclasses
import os
import platform
import time

import bpy
import numpy as np

from . import guard, log

ISSUES_URL = "https://github.com/Min3scon/Hindsight/issues/new/choose"
LAST = {"time": None, "results": []}   # results: [(state, name, detail)], state True/False/None(info)


# ----------------------------------------------------------------------------- facts
def addon_version() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blender_manifest.toml")
    try:
        import tomllib
        with open(path, "rb") as fh:
            return str(tomllib.load(fh).get("version", "?"))
    except Exception:
        return "?"


def _package():
    return __package__.rsplit(".", 1)[0]


def _gpu_lines():
    try:
        import gpu
        p = gpu.platform
        backend = p.backend_type_get() if hasattr(p, "backend_type_get") else "?"
        return [f"GPU: {p.renderer_get()} ({p.vendor_get()})", f"GPU driver: {p.version_get()}",
                f"GPU backend: {backend}"]
    except Exception as exc:
        return [f"GPU: unknown ({exc})"]


def _settings_lines():
    from ..core.types import SETTINGS
    out = []
    try:
        for f in dataclasses.fields(SETTINGS):
            out.append(f"  {f.name} = {getattr(SETTINGS, f.name)!r}")
    except Exception as exc:
        out.append(f"  unreadable: {exc}")
    return out


def _history_lines():
    try:
        from . import runtime
        st = runtime.memory_stats()
        hs = runtime.reg().all()
        longest = max((len(h.entries) for h in hs), default=0)
        return [f"  objects with history: {st.get('objects', 0)}",
                f"  steps in total: {st.get('steps', 0)} (longest history: {longest})",
                f"  history size: {st.get('total_bytes', 0) / 2**20:.1f} MB "
                f"(in RAM with baselines: {st.get('ram_bytes', 0) / 2**20:.1f} MB)",
                f"  global history: {runtime.use_global()}"]
    except Exception as exc:
        return [f"  unreadable: {exc}"]


def diagnostics_text() -> str:
    lines = [f"Hindsight {addon_version()}",
             f"Blender {bpy.app.version_string} ({getattr(bpy.app, 'build_hash', b'?').decode(errors='replace')})",
             f"OS: {platform.platform()}"]
    lines += _gpu_lines()
    try:
        ui = bpy.context.preferences.system
        lines.append(f"UI scale: {ui.ui_scale:.2f}, region overlap: {bpy.context.preferences.system.use_region_overlap}")
    except Exception:
        pass
    lines.append("Settings:")
    lines += _settings_lines()
    lines.append("History:")
    lines += _history_lines()
    fails = guard.failed()
    lines.append("Switched-off features: " + (", ".join(f"{lbl} ({err})" for _, lbl, err, _ in fails) or "none"))
    try:
        import importlib
        top = importlib.import_module(_package())
        errs = getattr(top, "_errors", [])
        lines.append("Start-up errors: " + ("; ".join(f"{n}: {e}" for n, e, _ in errs) or "none"))
    except Exception:
        pass
    lines.append("Last self-check: " + (_summary() or "not run"))
    for state, name, detail in LAST["results"]:
        mark = {True: "ok  ", False: "FAIL", None: "info"}[state]
        lines.append(f"  {mark} {name}: {detail}")
    lines.append("Last 50 log lines:")
    lines += ["  " + ln for ln in log.tail(50)]
    return log.scrub("\n".join(lines))


# ----------------------------------------------------------------------------- checks
def _check_startup():
    import importlib
    top = importlib.import_module(_package())
    errs = getattr(top, "_errors", [])
    if errs:
        return False, "Start-up", "did not start: " + ", ".join(n for n, _, _ in errs)
    return True, "Start-up", f"{len(getattr(top, '_loaded', []))} parts registered"


def _check_panels():
    names = ["VIEW3D_PT_hindsight", "VIEW3D_PT_hindsight_history", "VIEW3D_PT_hindsight_help"]
    missing = [n for n in names if not hasattr(bpy.types, n)]
    if missing:
        return False, "Sidebar tab", "missing: " + ", ".join(missing)
    return True, "Sidebar tab", "Undo History tab is registered"


def _check_slider():
    from . import scrubber
    if not guard.ok("slider"):
        return False, "On-screen slider", "switched off after an error (see the log)"
    if not scrubber.draw_handler_installed():
        return False, "On-screen slider", "draw handler is not installed"
    t = guard.last_ok("slider")
    if t is None:
        return None, "On-screen slider", "installed, not drawn yet (no 3D Viewport has redrawn)"
    return True, "On-screen slider", f"installed, last drawn {time.time() - t:.1f} s ago"


def _check_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return None, "Shortcuts", "no add-on key configuration (background mode)"
    found = set()
    for km in kc.keymaps:
        for kmi in km.keymap_items:
            if kmi.idname in ("hindsight.step_back", "hindsight.step_forward") and kmi.active:
                found.add((km.name, kmi.idname))
    need = {(m, op) for m in ("Sculpt", "Vertex Paint") for op in ("hindsight.step_back", "hindsight.step_forward")}
    missing = need - found
    if missing:
        return False, "Shortcuts", "missing: " + ", ".join(f"{m} {op}" for m, op in sorted(missing))
    return True, "Shortcuts", "Ctrl+Z and Ctrl+Shift+Z in Sculpt and Vertex Paint Mode"


def _check_handlers():
    from . import runtime
    inst = runtime.handlers_installed()
    timer = bpy.app.timers.is_registered(runtime.tick)
    bad = [k for k, v in inst.items() if not v] + ([] if timer else ["timer"])
    for name in ("recorder", "undo_sync"):
        if not guard.ok(name):
            bad.append(guard.LABELS[name] + " switched off")
    if bad:
        return False, "Recorder", "problem: " + ", ".join(bad)
    return True, "Recorder", "handlers and timer installed"


def _check_persistence():
    try:
        from . import persistence
    except Exception as exc:
        return False, "Saving", f"not loaded: {exc}"
    missing = [n for n, fn in persistence._HANDLERS if fn not in getattr(bpy.app.handlers, n)]
    if missing:
        return False, "Saving", "missing handlers: " + ", ".join(missing)
    return True, "Saving", "save and load handlers installed"


def _positions(me):
    a = np.empty(len(me.vertices) * 3, np.float32)
    me.attributes["position"].data.foreach_get("vector", a)
    return a


def _check_round_trip():
    """Record two steps on a throwaway mesh, jump around, compare bit for bit."""
    from . import runtime
    from ..core.registry import Registry
    me = bpy.data.meshes.new("HindsightSelfCheck")
    n = 8
    verts = [(x * 0.1, y * 0.1, 0.0) for y in range(n) for x in range(n)]
    faces = [(y * n + x, y * n + x + 1, (y + 1) * n + x + 1, (y + 1) * n + x)
             for y in range(n - 1) for x in range(n - 1)]
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new("HindsightSelfCheck", me)
    saved = (runtime._REG, dict(runtime._STATES), dict(runtime._DIRTY), dict(runtime._UID_CACHE),
             dict(runtime._TRACK), runtime.journal_hook, runtime.is_excluded)
    t0 = time.perf_counter()
    try:
        runtime._REG = Registry()
        runtime._STATES.clear()
        runtime._DIRTY.clear()
        runtime._UID_CACHE.clear()
        runtime.journal_hook = None
        runtime.is_excluded = lambda obj: False
        runtime.ensure_state(ob)
        a0 = _positions(me)
        frames = [a0]
        for k in range(2):
            a = frames[-1].copy()
            a[3 * (5 + k * 9):3 * (5 + k * 9) + 12] += np.float32(0.0125 * (k + 1))
            me.attributes["position"].data.foreach_set("vector", a)
            me.update()
            if runtime.commit(ob, label="Self-check", mode="SCULPT") is None:
                return False, "Record and scrub", f"step {k + 1} was not recorded"
            frames.append(_positions(me))
        h = runtime._REG.history_for(runtime.obj_uid(ob))
        if h is None or len(h.entries) != 2:
            return False, "Record and scrub", "expected 2 steps"
        for pos in (0, 2, 1, 0, 2):
            runtime._goto_history(h, pos, True)
            if _positions(me).tobytes() != frames[pos].tobytes():
                return False, "Record and scrub", f"step {pos} did not match after a jump"
        ms = (time.perf_counter() - t0) * 1000
        return True, "Record and scrub", f"2 steps recorded, 5 jumps bit-exact ({ms:.0f} ms)"
    finally:
        (runtime._REG, states, dirty, cache, track, runtime.journal_hook, runtime.is_excluded) = saved
        runtime._STATES.clear()
        runtime._STATES.update(states)
        runtime._DIRTY.clear()
        runtime._DIRTY.update(dirty)
        runtime._UID_CACHE.clear()
        runtime._UID_CACHE.update(cache)
        runtime._TRACK.update(track)
        try:
            bpy.data.objects.remove(ob)
            bpy.data.meshes.remove(me)
        except Exception:
            pass


CHECKS = (_check_startup, _check_panels, _check_slider, _check_keymaps, _check_handlers,
          _check_persistence, _check_round_trip)


def run_checks(retry_failed=True):
    if retry_failed and guard.failed():
        names = guard.reset()
        try:
            from . import scrubber
            scrubber.ensure_draw_handler()
        except Exception:
            pass
        log.info("Self-check switched these back on: " + ", ".join(names))
    results = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as exc:
            log.error_once(("selfcheck", fn.__name__, str(exc)), f"self-check {fn.__name__} raised: {exc}", exc)
            results.append((False, fn.__name__.replace("_check_", "").replace("_", " ").capitalize(),
                            f"{type(exc).__name__}: {exc}"))
    LAST["time"] = time.time()
    LAST["results"] = results
    log.info("Self-check: " + _summary())
    return results


def _summary():
    if LAST["time"] is None:
        return ""
    res = LAST["results"]
    bad = sum(1 for s, _, _ in res if s is False)
    stamp = time.strftime("%H:%M", time.localtime(LAST["time"]))
    if bad:
        return f"{bad} of {len(res)} checks failed ({stamp})"
    return f"all {len(res)} checks passed ({stamp})"


def summary():
    return _summary()


# ----------------------------------------------------------------------------- operators
class HINDSIGHT_OT_self_check(bpy.types.Operator):
    bl_idname = "hindsight.self_check"
    bl_label = "Hindsight: Run Self-check"
    bl_description = "Check that every part of Hindsight is working and show the result"

    def execute(self, context):
        res = run_checks()
        bad = [r for r in res if r[0] is False]
        if bad:
            self.report({'WARNING'}, f"Self-check: {len(bad)} problem{'s' if len(bad) > 1 else ''}. "
                                     "Copy diagnostics and report a bug.")
        else:
            self.report({'INFO'}, "Self-check passed")
        return {'FINISHED'}

    def invoke(self, context, event):
        self.execute(context)
        return context.window_manager.invoke_popup(self, width=440)

    def draw(self, context):
        col = self.layout.column(align=True)
        col.label(text="Self-check: " + _summary())
        col.separator()
        for state, name, detail in LAST["results"]:
            icon = {True: 'CHECKMARK', False: 'ERROR', None: 'INFO'}[state]
            col.label(text=f"{name}: {detail}", icon=icon)
        if any(s is False for s, _, _ in LAST["results"]):
            col.separator()
            row = col.row(align=True)
            row.operator("hindsight.copy_diagnostics", icon='COPYDOWN')
            row.operator("wm.url_open", text="Report a bug", icon='URL').url = ISSUES_URL


class HINDSIGHT_OT_copy_diagnostics(bpy.types.Operator):
    bl_idname = "hindsight.copy_diagnostics"
    bl_label = "Copy Diagnostics"
    bl_description = "Copy versions, settings and recent log lines to the clipboard for a bug report"

    def execute(self, context):
        text = diagnostics_text()
        context.window_manager.clipboard = text
        self.report({'INFO'}, f"Diagnostics copied ({len(text.splitlines())} lines). Paste them into your bug report.")
        return {'FINISHED'}


class HINDSIGHT_OT_open_history_menu(bpy.types.Operator):
    bl_idname = "hindsight.open_history_menu"
    bl_label = "Hindsight: Open History Menu"
    bl_description = "Open the History Menu (global history, morph target, reset and more)"

    def execute(self, context):
        bpy.ops.wm.call_menu(name="HINDSIGHT_MT_history")
        return {'FINISHED'}


class HINDSIGHT_OT_notice(bpy.types.Operator):
    bl_idname = "hindsight.notice"
    bl_label = "Hindsight Notice"
    bl_options = {'INTERNAL'}
    message: bpy.props.StringProperty()

    def execute(self, context):
        self.report({'INFO'}, self.message)
        return {'FINISHED'}


WELCOME = "Hindsight is on. Press N and open the Undo History tab, or make a stroke in Sculpt Mode."


_STATUS_WS = []


def flash_status(text, seconds=12.0):
    """Show one line in the status bar for a few seconds (from any context, never raises)."""
    if bpy.app.background:
        return

    def _show():
        try:
            wm = bpy.context.window_manager
            win = wm.windows[0] if wm and wm.windows else None
            if win is None:
                return 1.0
            with bpy.context.temp_override(window=win, screen=win.screen):
                ws = win.workspace
                ws.status_text_set(text)
                _STATUS_WS.append(ws)
            if bpy.app.timers.is_registered(_clear_status):
                bpy.app.timers.unregister(_clear_status)
            bpy.app.timers.register(_clear_status, first_interval=seconds)
        except Exception as exc:
            log.error_once(("flash_status", str(exc)), f"status message failed: {exc}", exc)
        return None
    bpy.app.timers.register(_show, first_interval=0.05)


def _clear_status():
    for ws in _STATUS_WS:
        try:
            ws.status_text_set(None)
        except Exception:
            pass
    _STATUS_WS.clear()
    return None


def _welcome():
    """One line in the status bar the first time Hindsight is enabled, gone after 12 seconds."""
    try:
        from .prefs import get_prefs
        p = get_prefs()
        if p is None or getattr(p, "welcomed", True):
            return None
        wm = bpy.context.window_manager
        win = wm.windows[0] if wm and wm.windows else None
        if win is None:
            return 1.0
        p.welcomed = True
        try:
            bpy.context.preferences.is_dirty = True
        except Exception:
            pass
        with bpy.context.temp_override(window=win, screen=win.screen):
            ws = win.workspace
            ws.status_text_set(WELCOME)
            _STATUS_WS.append(ws)
        bpy.app.timers.register(_clear_status, first_interval=12.0)
        log.info(WELCOME)
    except Exception as exc:
        log.error_once("welcome", f"first-run notice failed: {exc}", exc)
    return None


def schedule_welcome():
    if bpy.app.background:
        return
    if not bpy.app.timers.is_registered(_welcome):
        bpy.app.timers.register(_welcome, first_interval=0.8)


classes = (HINDSIGHT_OT_self_check, HINDSIGHT_OT_copy_diagnostics, HINDSIGHT_OT_open_history_menu,
           HINDSIGHT_OT_notice)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    if bpy.app.timers.is_registered(_welcome):
        bpy.app.timers.unregister(_welcome)
    if bpy.app.timers.is_registered(_clear_status):
        bpy.app.timers.unregister(_clear_status)
    _clear_status()
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
