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
# error_once stays bounded (S4-2). Keys often carry the error text, so an error whose text varies
# (an object name, a number) would be a new key on every timer tick. Per place and exception type
# only the first SAME_KIND_MAX different texts are logged; the rest is the same problem again.
# And at most SEEN_MAX keys are kept in all: past that, nothing new is logged (one line says so).
SAME_KIND_MAX = 3
SEEN_MAX = 1000
_KINDS = {}           # (place, exception type name) -> how many different keys were logged
_FULL = [False]       # SEEN_MAX reached and said so


def _kind(key, exc):
    """(place, exception type) of an error_once key: the key without its last part (the text) for
    tuple keys, when an exception is given. None when there's nothing to group by."""
    if exc is None or not isinstance(key, tuple) or len(key) < 2:
        return None
    return (key[:-1], type(exc).__name__)


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
    """Log `text` (plus the traceback of `exc`) once per key. Returns True if it was logged now.
    Bounded: see SAME_KIND_MAX and SEEN_MAX."""
    if key in _SEEN or _FULL[0]:
        return False
    kind = _kind(key, exc)
    if kind is not None and _KINDS.get(kind, 0) >= SAME_KIND_MAX:
        return False        # the same place failing the same way again, with another text
    if len(_SEEN) >= SEEN_MAX:
        _FULL[0] = True
        _emit("ERROR", f"more than {SEEN_MAX} different errors: no further ones are logged this session")
        return False
    _SEEN.add(key)
    if kind is not None:
        _KINDS[kind] = _KINDS.get(kind, 0) + 1
    _emit("ERROR", text)
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _emit("TRACE", tb.rstrip())
    return True


def forget(prefix=None):
    """Allow errors to be logged again (all, or keys starting with prefix)."""
    if prefix is None:
        _SEEN.clear()
        _KINDS.clear()
    else:
        for k in [k for k in _SEEN if str(k).startswith(prefix)]:
            _SEEN.discard(k)
        for k in [k for k in _KINDS if str(k[0]).startswith(prefix)]:
            del _KINDS[k]
    _FULL[0] = False


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
