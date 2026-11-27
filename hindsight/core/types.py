"""Shared data types for Hindsight's history engine.

Pure Python + numpy. No bpy imports anywhere under hindsight/core.

Channel keys
------------
A channel is one mesh attribute, named by a key string "name|DOMAIN|TYPE",
for example "position|POINT|FLOAT_VECTOR" or ".sculpt_mask|POINT|FLOAT".
Values are always held as a FLAT numpy array of the channel's native dtype
(see CHANNEL_TYPES) with length = element_count * width.

The step's shapes (Step, ChannelDelta, Snapshot, DataChange, Slice, Entry), the key helpers and MISSING are the
Rust engine's (native/src/types.rs, #147); their Python is in tests/reference/types.py. Their values stay Python
objects, held by the engine. Settings stays a dataclass here (bl/prefs.py and bl/selfcheck.py read its fields).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# data_type -> (numpy native dtype, width per element, foreach property name)
CHANNEL_TYPES = {
    "FLOAT": (np.float32, 1, "value"),
    "INT": (np.int32, 1, "value"),
    "INT8": (np.int8, 1, "value"),
    "BOOLEAN": (np.bool_, 1, "value"),
    "FLOAT_VECTOR": (np.float32, 3, "vector"),
    "FLOAT2": (np.float32, 2, "vector"),
    "INT32_2D": (np.int32, 2, "value"),
    "INT16_2D": (np.int16, 2, "value"),      # custom_normal, Blender 5.0+
    "FLOAT_COLOR": (np.float32, 4, "color"),
    "BYTE_COLOR": (np.float32, 4, "color_srgb"),
    "QUATERNION": (np.float32, 4, "value"),
}

# numpy unsigned "bits" dtype used for deltas, by native itemsize
BITS_DTYPE = {1: np.uint8, 2: np.uint16, 4: np.uint32, 8: np.uint64}

POSITION_KEY = "position|POINT|FLOAT_VECTOR"
MASK_KEY = ".sculpt_mask|POINT|FLOAT"
FACE_SET_KEY = ".sculpt_face_set|FACE|INT"
HIDE_VERT_KEY = ".hide_vert|POINT|BOOLEAN"
HIDE_POLY_KEY = ".hide_poly|FACE|BOOLEAN"

# Topology arrays inside a Snapshot (not normal attributes)
EDGE_VERTS_KEY = ".edge_verts|EDGE|INT32_2D"
CORNER_VERT_KEY = ".corner_vert|CORNER|INT"
CORNER_EDGE_KEY = ".corner_edge|CORNER|INT"
FACE_OFFSETS_KEY = "face_offsets|FACE|INT"  # length = face_count + 1


# ChannelDelta.op: how vals relate the two states
XOR = 0   # vals = old ^ new (format 1 and 2 histories); applying twice is a no-op
ADD = 1   # vals = new - old, modular in the bit pattern; forward adds, backward subtracts


# Step kinds
DELTA = "DELTA"   # deltas only
TOPO = "TOPO"     # before/after snapshots
JOIN = "JOIN"     # join of several objects; extra holds join info; before/after per member
DATA = "DATA"     # data records only (adapters other than the mesh recorder)


try:
    from .native import ENGINE as _N
except ImportError:     # EngineMissing: Settings and the tables still import (prefs.py), so the add-on can say why
    _N = None
if _N is not None:
    make_key, split_key, key_dtype, key_width, bits_dtype = (_N.make_key, _N.split_key, _N.key_dtype,
                                                          _N.key_width, _N.bits_dtype)
    ChannelDelta, Snapshot, DataChange, Step, Slice, Entry = (_N.ChannelDelta, _N.Snapshot, _N.DataChange, _N.Step,
                                                              _N.Slice, _N.Entry)
    MISSING = _N.MISSING    # a path that doesn't exist on one side of a change (a constraint added, a key removed)
    _N.types_setup(CHANNEL_TYPES, BITS_DTYPE, __package__ + ".step")


@dataclass
class Settings:
    """Runtime settings (mirrored from add-on preferences)."""
    max_steps: int = 10000
    memory_limit_mb: int = 4096        # total history size (all objects); oldest evicted first
    raw_steps: int = 64                # most recent steps per history kept unpacked
    compression: str = "auto"          # auto | zstd | lz4 | zlib | none
    truncation_threshold: int = 10     # confirm before dropping >= this many future steps
    crash_recovery: str = "INTERRUPTED" # LAST_SAVED | INTERRUPTED (#128)
    edit_steps_max: int = 300_000      # vertices: Record everything records Edit Mode per operation up to
                                       # this, per visit above (about 20 to 27 ms per 100k per operation)


# The crash journal's step blobs are written on the main thread at every stroke: lz4 costs about
# 60% of zstd level 3 there and the journal is temporary. History, packer and save keep the user's codec.
JOURNAL_CODEC = "lz4"

SETTINGS = Settings()
