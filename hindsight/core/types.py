"""Shared data types for Hindsight's history engine.

Pure Python + numpy. No bpy imports anywhere under hindsight/core.

Channel keys
------------
A channel is one mesh attribute, named by a key string "name|DOMAIN|TYPE",
for example "position|POINT|FLOAT_VECTOR" or ".sculpt_mask|POINT|FLOAT".
Values are always held as a FLAT numpy array of the channel's native dtype
(see CHANNEL_TYPES) with length = element_count * width.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

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
    "FLOAT_COLOR": (np.float32, 4, "color"),
    "BYTE_COLOR": (np.float32, 4, "color_srgb"),
    "QUATERNION": (np.float32, 4, "value"),
}

# numpy unsigned "bits" dtype used for XOR, by native itemsize
BITS_DTYPE = {1: np.uint8, 2: np.uint16, 4: np.uint32, 8: np.uint64}

POSITION_KEY = "position|POINT|FLOAT_VECTOR"
MASK_KEY = ".sculpt_mask|POINT|FLOAT"
FACE_SET_KEY = ".sculpt_face_set|FACE|INT"
HIDE_VERT_KEY = ".hide_vert|POINT|BOOLEAN"
HIDE_EDGE_KEY = ".hide_edge|EDGE|BOOLEAN"
HIDE_POLY_KEY = ".hide_poly|FACE|BOOLEAN"
SHARP_FACE_KEY = "sharp_face|FACE|BOOLEAN"

# Topology arrays inside a Snapshot (not normal attributes)
EDGE_VERTS_KEY = ".edge_verts|EDGE|INT32_2D"
CORNER_VERT_KEY = ".corner_vert|CORNER|INT"
CORNER_EDGE_KEY = ".corner_edge|CORNER|INT"
FACE_OFFSETS_KEY = "face_offsets|FACE|INT"  # length = face_count + 1

DOMAINS = ("POINT", "EDGE", "FACE", "CORNER")


def make_key(name: str, domain: str, data_type: str) -> str:
    return f"{name}|{domain}|{data_type}"


def split_key(key: str) -> tuple[str, str, str]:
    name, domain, data_type = key.rsplit("|", 2)
    return name, domain, data_type


def key_dtype(key: str):
    """Native numpy dtype for a channel key."""
    return CHANNEL_TYPES[split_key(key)[2]][0]


def key_width(key: str) -> int:
    return CHANNEL_TYPES[split_key(key)[2]][1]


def bits_dtype(native_dtype) -> type:
    return BITS_DTYPE[np.dtype(native_dtype).itemsize]


@dataclass
class ChannelDelta:
    """Change of one channel between two states, stored as XOR of bit patterns.

    idx:  sorted, unique element indices (np.uint32), shape (k,)
    xor:  bit XOR (old ^ new) of the changed elements, shape (k, width),
          dtype = bits_dtype(native dtype of the channel) (uint8 for BOOLEAN,
          uint32 for float32/int32). Applying the same delta twice is a no-op,
          so one record works for both undo and redo.
    exists: (existed_before, exists_after). A missing attribute is treated as
          all-zeros; when stepping to a side where it does not exist the
          attribute is removed from the mesh.
    """
    key: str
    idx: np.ndarray
    xor: np.ndarray
    exists: tuple = (True, True)

    def nbytes(self) -> int:
        return int(self.idx.nbytes + self.xor.nbytes) + 64


@dataclass
class Snapshot:
    """Full mesh state, used only for topology-changing steps.

    counts: {"POINT": nv, "EDGE": ne, "FACE": nf, "CORNER": nl}
    arrays: channel key -> flat native array. Always contains POSITION_KEY,
            EDGE_VERTS_KEY, CORNER_VERT_KEY, CORNER_EDGE_KEY, FACE_OFFSETS_KEY,
            plus every recorded attribute channel present on the mesh.
    """
    counts: dict
    arrays: dict

    def nbytes(self) -> int:
        return int(sum(a.nbytes for a in self.arrays.values())) + 128

    def copy(self) -> "Snapshot":
        return Snapshot(dict(self.counts), {k: v.copy() for k, v in self.arrays.items()})


# Step kinds
DELTA = "DELTA"   # deltas only
TOPO = "TOPO"     # before/after snapshots
JOIN = "JOIN"     # join of several objects; extra holds join info; before/after per member


@dataclass
class Step:
    seq: int                      # global sequence number (monotonic across all objects)
    label: str                    # e.g. "Stroke", "Edit Mode", "Voxel Remesh"
    mode: str                     # object mode when recorded: SCULPT, EDIT, VERTEX_PAINT, OBJECT
    target: str                   # object uid the step applies to
    time: float = 0.0
    kind: str = DELTA
    deltas: list = field(default_factory=list)   # list[ChannelDelta]
    before: Optional[Snapshot] = None             # TOPO / JOIN
    after: Optional[Snapshot] = None              # TOPO / JOIN
    extra: dict = field(default_factory=dict)     # JSON-serialisable metadata

    def is_topo(self) -> bool:
        return self.kind in (TOPO, JOIN)

    def nbytes(self) -> int:
        n = 256 + sum(d.nbytes() for d in self.deltas)
        if self.before is not None:
            n += self.before.nbytes()
        if self.after is not None:
            n += self.after.nbytes()
        return n

    def meta(self) -> dict:
        return {"seq": self.seq, "label": self.label, "mode": self.mode,
                "target": self.target, "time": self.time, "kind": self.kind,
                "extra": self.extra}


class Entry:
    """One slot in a History. Holds the step raw (fast) or packed (small).

    Exactly one of .step / .blob is not None at any time (both may briefly
    coexist while packing). .meta is always available without unpacking.
    """
    __slots__ = ("meta", "step", "blob", "_nbytes")

    def __init__(self, step: Optional[Step] = None, blob: Optional[bytes] = None,
                 meta: Optional[dict] = None):
        self.step = step
        self.blob = blob
        self.meta = meta if meta is not None else (step.meta() if step else {})
        self._nbytes = None

    @property
    def seq(self) -> int:
        return self.meta["seq"]

    @property
    def target(self) -> str:
        return self.meta["target"]

    @property
    def kind(self) -> str:
        return self.meta.get("kind", DELTA)

    def is_packed(self) -> bool:
        return self.step is None

    def nbytes(self) -> int:
        if self.step is not None:
            return self.step.nbytes()
        return len(self.blob) + 128 if self.blob is not None else 128

    def load(self) -> Step:
        """Return the Step, unpacking (without caching) if packed."""
        if self.step is not None:
            return self.step
        from .step import step_from_bytes
        return step_from_bytes(self.blob)

    def pack(self, codec: str = "auto") -> None:
        if self.step is None:
            return
        from .step import step_to_bytes
        self.blob = step_to_bytes(self.step, codec)
        self.step = None

    def to_bytes(self, codec: str = "auto") -> bytes:
        if self.blob is not None:
            return self.blob
        from .step import step_to_bytes
        return step_to_bytes(self.step, codec)


@dataclass
class Settings:
    """Runtime settings (mirrored from add-on preferences)."""
    max_steps: int = 10000
    memory_limit_mb: int = 4096        # total history size (all objects); oldest evicted first
    raw_steps: int = 64                # most recent steps per history kept unpacked
    compression: str = "auto"          # auto | zstd | lz4 | zlib | none
    truncation_threshold: int = 10     # confirm before dropping >= this many future steps
    save_mode: str = "SIDECAR"         # SIDECAR | EMBED | BOTH
    crash_recovery: str = "LAST_SAVED" # LAST_SAVED | INTERRUPTED


SETTINGS = Settings()
