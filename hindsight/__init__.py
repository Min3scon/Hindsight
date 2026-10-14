# SPDX-License-Identifier: GPL-3.0-or-later
"""Hindsight: scrubbable per-object sculpt history for Blender."""
import importlib

# Modules registered in order. Optional modules (P3) are skipped if missing or broken.
_MODULES = (
    ("props", True), ("prefs", True), ("runtime", True), ("ops_core", True), ("filtering", True),
    ("warnings", True), ("ui_menu", True), ("ui_panel", True), ("scrubber", True),
    ("persistence", False), ("movie", False), ("morph", False), ("joinsep", False),
    ("dupsep", False),
)
_loaded = []


def register():
    _loaded.clear()
    for name, required in _MODULES:
        try:
            mod = importlib.import_module(f".bl.{name}", __package__)
        except Exception as e:
            if required:
                raise
            if not getattr(e, "name", "").endswith(name):
                print(f"Hindsight: optional module {name} unavailable: {e}")
            continue
        try:
            if name == "prefs":
                mod.register(__package__)
            else:
                mod.register()
        except Exception as e:
            if required:
                raise
            print(f"Hindsight: optional module {name} failed to register: {e}")
            continue
        _loaded.append(mod)
    try:
        from .bl import prefs
        p = prefs.get_prefs()
        if p is not None:
            prefs.sync_settings(p)
    except Exception:
        pass


def unregister():
    for mod in reversed(_loaded):
        try:
            mod.unregister()
        except Exception as e:
            print("Hindsight: unregister error:", e)
    _loaded.clear()
