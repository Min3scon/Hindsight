# SPDX-License-Identifier: GPL-3.0-or-later
"""Hindsight: scrubbable per-object sculpt history for Blender."""
import importlib
import traceback

# Registered in this order, unregistered in reverse. A module that fails to register is reported
# loudly (full traceback in the console, one popup, a note in the sidebar) and the rest still load,
# so the Undo History tab is always there to say what went wrong.
_MODULES = (
    "props", "prefs", "runtime", "ops_core", "filtering", "warnings", "ui_menu", "slider_prop",
    "selfcheck", "ui_panel", "header", "scrubber", "persistence", "movie", "morph", "joinsep", "dupsep",
)
_loaded = []
_errors = []       # [(module name, short error, full traceback)]


def _report_failures():
    """One popup per session listing the modules that failed to start."""
    import bpy
    if bpy.app.background or not _errors:
        return None
    names = ", ".join(n for n, _, _ in _errors)

    def draw(menu, context):
        col = menu.layout.column()
        col.label(text=f"These parts did not start: {names}.")
        col.label(text="The rest of Hindsight works. Details are in the system console.")
        col.operator("hindsight.copy_diagnostics", text="Copy diagnostics", icon='COPYDOWN')
        col.operator("wm.url_open", text="Report a bug", icon='URL').url = \
            "https://github.com/Min3scon/Hindsight/issues/new/choose"
    try:
        wm = bpy.context.window_manager
        win = wm.windows[0] if wm and wm.windows else None
        if win is None:
            return 1.0  # no window yet (start-up): try again shortly
        with bpy.context.temp_override(window=win, screen=win.screen):
            wm.popup_menu(draw, title="Hindsight could not fully start", icon='ERROR')
    except Exception:
        pass
    return None


def register():
    from .bl import guard, log
    _loaded.clear()
    _errors.clear()
    for name in _MODULES:
        try:
            mod = importlib.import_module(f".bl.{name}", __package__)
            if name == "prefs":
                mod.register(__package__)
            else:
                mod.register()
        except Exception as exc:
            tb = traceback.format_exc()
            _errors.append((name, f"{type(exc).__name__}: {exc}", tb))
            log.error(f"module '{name}' failed to register: {type(exc).__name__}: {exc}")
            log.error(tb.rstrip())
            guard.fail("registration", exc, name)
            continue
        _loaded.append(mod)
    try:
        from .bl import prefs
        prefs.sync_settings(prefs.get_prefs())
    except Exception as exc:
        log.error_once("sync_settings", f"could not read preferences: {exc}", exc)
    if _errors:
        import bpy
        if not bpy.app.background:
            bpy.app.timers.register(_report_failures, first_interval=0.5)
    else:
        try:
            from .bl import selfcheck
            selfcheck.schedule_welcome()
        except Exception:
            pass


def unregister():
    from .bl import log
    for mod in reversed(_loaded):
        try:
            mod.unregister()
        except Exception as exc:
            log.error_once(("unregister", mod.__name__), f"unregister error in {mod.__name__}: {exc}", exc)
    _loaded.clear()
    try:
        import bpy
        if bpy.app.timers.is_registered(_report_failures):
            bpy.app.timers.unregister(_report_failures)
    except Exception:
        pass
