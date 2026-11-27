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

from .crc import crc32

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


# ChannelDelta.op: how vals relate the two states
XOR = 0   # vals = old ^ new (format 1 and 2 histories); applying twice is a no-op
ADD = 1   # vals = new - old, modular in the bit pattern; forward adds, backward subtracts


@dataclass
class ChannelDelta:
    """Change of one channel between two states, exact to the bit.

    idx:  sorted, unique element indices (np.uint32), shape (k,)
    vals: per changed element, shape (k, width), dtype = bits_dtype(native dtype
          of the channel) (uint8 for BOOLEAN, uint32 for float32/int32).
          op ADD: new - old of the bit patterns, wrapping (what new steps store).
          op XOR: old ^ new (steps recorded by format 1 and 2 histories).
          Both are exact for every bit pattern (NaN, -0, denormals included).
    exists: (existed_before, exists_after). A missing attribute is treated as
          all-zeros; when stepping to a side where it does not exist the
          attribute is removed from the mesh.
    """
    key: str
    idx: np.ndarray
    vals: np.ndarray
    exists: tuple = (True, True)
    op: int = XOR

    def nbytes(self) -> int:
        return int(self.idx.nbytes + self.vals.nbytes) + 64


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
DATA = "DATA"     # data records only (adapters other than the mesh recorder)


class _Missing:
    """A path that doesn't exist on one side of a change (a constraint added, a key removed)."""
    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"

    def __reduce__(self):
        return "MISSING"


MISSING = _Missing()


@dataclass
class DataChange:
    """Change of recorded values on one datablock, made by one adapter.

    adapter: adapter id ("xform", "rna", ...)
    target:  uid of the datablock (its `_hindsight_uid`)
    changes: path -> (old, new). Values are JSON values (numbers, strings, booleans, None, lists of
             those); MISSING marks a side where the path doesn't exist. Floats are exact (Blender's
             float32 and double properties round-trip through Python floats and JSON unchanged).
    """
    adapter: str
    target: str
    changes: dict

    def nbytes(self) -> int:
        return 96 + 48 * len(self.changes) + sum(_value_size(o) + _value_size(n) for o, n in self.changes.values())


def _value_size(v) -> int:
    if isinstance(v, (list, tuple)):
        return 16 + sum(_value_size(x) for x in v)
    if isinstance(v, str):
        return 16 + len(v)
    return 16


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
    records: list = field(default_factory=list)  # list[DataChange] (any kind; DATA steps have only these)

    def nbytes(self) -> int:
        n = 256 + sum(d.nbytes() for d in self.deltas) + sum(r.nbytes() for r in self.records)
        if self.before is not None:
            n += self.before.nbytes()
        if self.after is not None:
            n += self.after.nbytes()
        return n

    def meta(self) -> dict:
        return {"seq": self.seq, "label": self.label, "mode": self.mode,
                "target": self.target, "time": self.time, "kind": self.kind,
                "extra": self.extra}


_NO_BLOB = (None, None)


class Slice:
    """A saved step's bytes inside its step part (#125): read out of `part.bytes` by offset, so the history is held
    once. `part.bytes` is None while Blender holds the step part, during a save."""
    __slots__ = ("part", "off", "n")

    def __init__(self, part, off: int, n: int):
        self.part, self.off, self.n = part, int(off), int(n)

    def __len__(self) -> int:
        return self.n

    def __getitem__(self, i):
        """A byte, or a copy of a run of bytes (step 1), as the step's own bytes would give."""
        if type(i) is slice:
            a, z, _ = i.indices(self.n)
            return self.part.bytes[self.off + a:self.off + max(a, z)]
        if i < 0:
            i += self.n
        if not 0 <= i < self.n:
            raise IndexError(i)
        return self.part.bytes[self.off + i]

    def copy(self):
        """The step's bytes, copied out (None while Blender holds the step part)."""
        b = self.part.bytes
        return None if b is None else b[self.off:self.off + self.n]

    def view(self):
        return memoryview(self.part.bytes)[self.off:self.off + self.n]


class Entry:
    """One slot in a History. Holds the step raw (fast) or packed (small).

    Exactly one of .step / .blob is not None at any time (both may briefly
    coexist while packing). .meta is always available without unpacking.

    R046: the packed bytes are kept together with their CRC-32, made when the bytes are (packing,
    the packer's recompression, loading, where the file's checksum was just verified), so a save
    doesn't read every packed step again. One tuple, replaced whole: the packer thread swaps it while
    the main thread may be saving, and a reader always gets bytes and the checksum of those bytes.
    """
    __slots__ = ("meta", "step", "_bc", "_nbytes", "__weakref__")

    def __init__(self, step: Optional[Step] = None, blob: Optional[bytes] = None,
                 meta: Optional[dict] = None, crc: Optional[int] = None):
        self.step = step
        if blob is None:
            self._bc = _NO_BLOB
        else:
            self._bc = (blob, crc if crc is not None else crc32(blob) & 0xFFFFFFFF)
        self.meta = meta if meta is not None else (step.meta() if step else {})
        self._nbytes = None

    @property
    def blob(self) -> Optional[bytes]:
        b = self._bc[0]
        return b.copy() if type(b) is Slice else b

    @blob.setter
    def blob(self, b: Optional[bytes]) -> None:
        self._bc = _NO_BLOB if b is None else (b, crc32(b) & 0xFFFFFFFF)

    def blob_crc(self):
        """(packed bytes, their CRC-32), read together; (None, None) while the step is raw. `_bc` is the same pair
        as held, a Slice in place of bytes for a step read out of its step part (#125)."""
        bc = self._bc
        return (bc[0].copy(), bc[1]) if type(bc[0]) is Slice else bc

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
        b = self._bc[0]                 # a Slice gives its length without a copy
        return len(b) + 128 if b is not None else 128

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

    def to_bytes(self, codec: str = "auto", effort: int = 1) -> bytes:
        """Packed form. effort 0 (fast) for writes on the main thread; 1 (best) otherwise."""
        b = self.blob
        if b is not None:
            return b
        from .step import step_to_bytes
        return step_to_bytes(self.step, codec, effort)


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
