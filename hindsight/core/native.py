"""The optional native engine: the Rust module `hindsight_native` (source in native/ at the
repository root, shipped as wheels in hindsight/wheels).

It does the number work of delta.py (diff, apply, and the transforms of packing and unpacking a
record) bit for bit like the Python code there, which stays as the reference and is used whenever
the module can't be loaded. Pure Python, no bpy.

HINDSIGHT_NATIVE=0        always use the Python code
HINDSIGHT_NATIVE=require  a missing or mismatched module is an error (tests)
"""
from __future__ import annotations

import os

API = 1                     # must match the module's API number (native/src/lib.rs)
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
        if getattr(m, "API", None) != API:
            raise ImportError(f"hindsight_native API {getattr(m, 'API', None)}, expected {API}")
    except Exception as e:          # ImportError, or a broken binary (OSError and the like)
        if MODE == "require":
            raise
        ERROR = str(e)
        return
    lib = m


_load()
AVAILABLE = lib is not None
