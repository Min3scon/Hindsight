"""Append-only crash journal: the engine's Journal (24.2a, #152; its Python is tests/reference/journal.py).

Record layout (little endian):

    MAGIC(4s) | type(u8) | header_len(u32) | payload_len(u64)
    | header JSON (utf-8) | payload | crc32(u32 over everything before it)

Reading stops silently at the first truncated or corrupt record, so a crash in the middle of an append never loses
the records written before it. On open, a trailing partial/corrupt record is cut off so new appends follow the last
valid record. atomic_write went with #53 (no add-on code writes a history folder); the reference keeps it.
"""
from __future__ import annotations

MAGIC = b"HSJ1"

RTYPES = {"STEP": 1, "CURSOR": 2, "TRUNC": 3, "DROP": 4, "RESET": 5, "META": 6, "SAVE": 7, "META_EVERYTHING": 8}
RTYPE_NAMES = {v: k for k, v in RTYPES.items()}

try:
    from .native import ENGINE as _N
except ImportError:     # EngineMissing: the names above still import, as types.py's do
    _N = None
if _N is not None:
    Journal = _N.Journal
