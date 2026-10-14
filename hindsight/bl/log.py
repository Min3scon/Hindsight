"""Hindsight's console log: one prefix, a ring buffer for Copy Diagnostics, no repeated spam.

Everything the add-on prints goes through here. ``error_once`` prints the full traceback the first
time a given error happens and stays quiet after that.
"""
import os
import sys
import time
import traceback
from collections import deque

PREFIX = "Hindsight"
_LINES = deque(maxlen=400)
_SEEN = set()
_MAX_SEEN = 256


def _emit(level, text):
    stamp = time.strftime("%H:%M:%S")
    for line in str(text).splitlines() or [""]:
        _LINES.append(f"{stamp} {level:5s} {line}")
    try:
        print(f"{PREFIX}: {text}" if level != "TRACE" else text, file=sys.stdout)
    except Exception:
        pass


def info(text):
    _emit("INFO", text)


def warn(text):
    _emit("WARN", text)


def error(text):
    _emit("ERROR", text)


def error_once(key, text, exc=None):
    """Log `text` (plus the traceback of `exc`) once per key. Returns True if it was logged now."""
    if key in _SEEN:
        return False
    if len(_SEEN) < _MAX_SEEN:
        _SEEN.add(key)
    _emit("ERROR", text)
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _emit("TRACE", tb.rstrip())
    return True


def forget(prefix=None):
    """Allow errors to be logged again (all, or keys starting with prefix)."""
    if prefix is None:
        _SEEN.clear()
    else:
        for k in [k for k in _SEEN if str(k).startswith(prefix)]:
            _SEEN.discard(k)


def tail(n=50):
    return [scrub(line) for line in list(_LINES)[-n:]]


def scrub(text):
    """Remove personal paths and the user name from text meant for a bug report."""
    s = str(text)
    reps = []
    for var in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        v = os.environ.get(var)
        if v and len(v) > 3:
            reps.append((v, "<" + var.lower() + ">"))
    try:
        import bpy
        fp = bpy.data.filepath
        if fp:
            reps.append((os.path.dirname(fp), "<blend dir>"))
            reps.append((os.path.basename(fp), "<blend>"))
    except Exception:
        pass
    reps.sort(key=lambda r: -len(r[0]))
    for old, new in reps:
        s = s.replace(old, new).replace(old.replace("\\", "/"), new)
    for var in ("USERNAME", "USER"):
        v = os.environ.get(var)
        if v and len(v) > 2:
            s = s.replace(v, "<user>")
    return s
