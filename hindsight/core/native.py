"""The optional native engine: the Rust module `hindsight_native` (source in native/ at the
repository root, shipped as wheels in hindsight/wheels).

It does the number work of delta.py (diff, apply, and the transforms of packing and unpacking a
record) bit for bit like the Python code there, which stays as the reference and is used whenever
the module can't be loaded. Pure Python, no bpy.

HINDSIGHT_NATIVE=0        always use the Python code
HINDSIGHT_NATIVE=require  a missing, older or mismatched module is an error (tests)

A module from an older release (API from MIN_API up) is still used: callers look a function up
with getattr and run their Python code when the module doesn't have it.
"""
from __future__ import annotations

import os

API = 2                     # the module's API number (native/src/lib.rs) this code is written for
MIN_API = 1                 # oldest one still used: functions it lacks run in Python, one by one
# API 1: diff, apply, pack, unpack.  API 2: + apply_steps (a run of packed steps, history.py)
MODE = os.environ.get("HINDSIGHT_NATIVE", "").strip().lower()

lib = None                  # the module, or None: delta.py reads this at every call
ERROR = None                # why it isn't used, for the log


def _load():
    global lib, ERROR
    if MODE == "0":
        ERROR = "turned off (HINDSIGHT_NATIVE=0)"
        return
    try:
        import hindsight_native as m
        api = getattr(m, "API", None)
        if not isinstance(api, int) or not MIN_API <= api <= API or (MODE == "require" and api != API):
            raise ImportError(f"hindsight_native API {api}, expected {API}")
    except Exception as e:          # ImportError, or a broken binary (OSError and the like)
        if MODE == "require":
            raise
        ERROR = str(e)
        return
    lib = m


_load()
AVAILABLE = lib is not None
