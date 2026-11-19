"""R069: a recorded mesh's baseline keeps its topology packed while the user is idle.

The baseline (MeshState) of a 1M-vertex mesh holds about 50 MB of edge and corner indices that are read
only when the topology changes, is compared, or a step needs the mesh as it was. Packed (each index minus
the one before it, then zstd), a real sculpt mesh's topology takes about a third (a voxel remesh of 1.5M
vertices: 75 MB to 24 MB). The packed form keeps an xxh3-128 checksum of every array, so a compare of the
topology never needs the arrays back.

The differences are taken modulo 2**32 (numpy integer arithmetic wraps) and the running sum gives the
array back exactly, whatever the values.
"""
from __future__ import annotations

import numpy as np

from . import codec
from .types import CORNER_EDGE_KEY, CORNER_VERT_KEY, EDGE_VERTS_KEY, FACE_OFFSETS_KEY

TOPO_KEYS = (EDGE_VERTS_KEY, CORNER_VERT_KEY, CORNER_EDGE_KEY, FACE_OFFSETS_KEY)
_CODEC = "zstd"


def hash_of(a) -> str:
    """xxh3-128 of the array's bytes (the engine's Hasher128)."""
    from .native import ENGINE
    h = ENGINE.Hasher128()
    h.update(np.ascontiguousarray(a).reshape(-1).view(np.uint8))
    return h.hexdigest()


def pack_array(a) -> tuple:
    """(packed bytes, dtype, shape, checksum, delta-coded) of one array; unpack_array gives it back."""
    a = np.ascontiguousarray(a)
    flat = a.reshape(-1)
    delta = flat.dtype.kind in "iu"
    if delta and len(flat):
        d = np.empty_like(flat)
        d[0] = flat[0]
        np.subtract(flat[1:], flat[:-1], out=d[1:])
    else:
        d = flat
    return codec.compress(d.view(np.uint8), _CODEC), a.dtype.str, a.shape, hash_of(a), delta


def unpack_array(p: tuple) -> np.ndarray:
    blob, dt, shape, _h, delta = p
    dtype = np.dtype(dt)
    n = int(np.prod(shape)) if len(shape) else 1
    raw = codec.decompress(blob, n * dtype.itemsize)
    flat = np.frombuffer(raw, dtype)
    out = np.cumsum(flat, dtype=dtype) if delta and len(flat) else flat.copy()
    return out.reshape(shape)


def pack_all(arrays: dict) -> dict:
    """{key: pack_array(a)} (run off the main thread: the codec works without the GIL)."""
    return {k: pack_array(a) for k, a in arrays.items()}


def packed_bytes(packed: dict) -> int:
    return sum(len(p[0]) for p in packed.values())
