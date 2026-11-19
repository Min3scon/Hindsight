"""crc32 for types, persist and journal: the engine's (native.crc32, same value as zlib.crc32, about 10x
faster), looked up on first use. Importing the engine at import time would stop the preferences and the
panel from loading when the engine is missing, and they must load to say so (test_bl_engine_missing)."""
from __future__ import annotations

_fn = None


def crc32(data, init: int = 0) -> int:
    global _fn
    if _fn is None:
        from .native import crc32 as f
        _fn = f
    return _fn(data, init)
