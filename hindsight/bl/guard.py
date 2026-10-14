"""Feature guards.

Every handler, draw callback, timer and modal operator runs inside a guard. If it raises, the
traceback is logged once, that one feature is switched off, and everything else keeps working.
Sculpting, Blender's own undo and the existing history are never touched by a failure.
The sidebar lists switched-off features; Run Self-check can switch them back on.
"""
import functools
import time

from . import log

# name -> label shown to the user
LABELS = {
    "registration": "Start-up",
    "recorder": "Recording",
    "undo_sync": "Blender undo sync",
    "slider": "On-screen slider",
    "overlay": "Viewport warnings",
    "header": "Header button",
    "movie": "Movie Mode playback",
    "saving": "Saving history",
    "loading": "Loading history",
    "morph": "Morph History brush",
    "dupsep": "Duplicate and separate tracking",
}

# Features that are reported but never switched off (each call is independent, e.g. one file save).
SOFT = {"saving", "loading", "registration"}

_FAILED = {}      # name -> (time, short error text)
_ON_FAIL = {}     # name -> callable run once when the feature is switched off
_LAST_OK = {}     # name -> time of the last successful call (only for features that ask for it)


def on_fail(name, fn):
    _ON_FAIL[name] = fn


def ok(name):
    return name not in _FAILED


def failed():
    """[(name, label, error text, time)] for every feature that failed."""
    return [(n, LABELS.get(n, n), e, t) for n, (t, e) in sorted(_FAILED.items())]


def mark_ok(name):
    _LAST_OK[name] = time.time()


def last_ok(name):
    return _LAST_OK.get(name)


def fail(name, exc, where=""):
    text = f"{type(exc).__name__}: {exc}"
    label = LABELS.get(name, name)
    log.error_once((name, where, text), f"{label} failed{(' in ' + where) if where else ''}: {text}", exc)
    if name in SOFT:
        _FAILED[name] = (time.time(), text)
        return
    if name in _FAILED:
        return
    _FAILED[name] = (time.time(), text)
    log.warn(f"{label} is switched off for this session. Run Self-check in the Undo History tab to retry.")
    cb = _ON_FAIL.get(name)
    if cb is not None:
        try:
            cb()
        except Exception as e2:  # the cleanup itself must never raise
            log.error_once((name, "on_fail"), f"cleanup after {label} failure failed: {e2}", e2)
    _redraw()


def reset(name=None):
    """Switch failed features back on (all of them, or one)."""
    names = list(_FAILED) if name is None else [name]
    for n in names:
        _FAILED.pop(n, None)
    log.forget()
    _redraw()
    return names


def guarded(name, default=None, where=None):
    """Decorator: run fn only while the feature is on; on an exception switch the feature off."""
    def deco(fn):
        w = where or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if name in _FAILED and name not in SOFT:
                return default
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                fail(name, exc, w)
                return default
        wrapper.__wrapped__ = fn
        return wrapper
    return deco


def _redraw():
    try:
        import bpy
        wm = bpy.context.window_manager
        if wm is None:
            return
        for win in wm.windows:
            for area in win.screen.areas:
                if area.type in ("VIEW_3D", "PREFERENCES"):
                    area.tag_redraw()
    except Exception:
        pass
