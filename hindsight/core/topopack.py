"""R069: a recorded mesh's baseline keeps its topology packed while the user is idle.

The baseline (MeshState) of a 1M-vertex mesh holds about 50 MB of edge and corner indices that are read
only when the topology changes, is compared, or a step needs the mesh as it was. Packed (each index minus
the one before it, then zstd), a real sculpt mesh's topology takes about a third (a voxel remesh of 1.5M
vertices: 75 MB to 24 MB). The packed form keeps an xxh3-128 checksum of every array, so a compare of the
topology never needs the arrays back.

The packing runs in the Rust engine (native/src/topo.rs, #148), the delta coding, codec and checksum without the
GIL (pack_all runs off the main thread); the Python is in tests/reference/topopack.py.

hash_of(a)          xxh3-128 of the array's bytes, 32 hex digits
pack_array(a)       (packed bytes, dtype, shape, checksum, delta-coded); unpack_array gives the array back
pack_all(arrays)    {key: pack_array(a)}
packed_bytes(p)     the packed bytes of a pack_all result
"""
from __future__ import annotations

from .native import ENGINE as _N
from .types import CORNER_EDGE_KEY, CORNER_VERT_KEY, EDGE_VERTS_KEY, FACE_OFFSETS_KEY

TOPO_KEYS = (EDGE_VERTS_KEY, CORNER_VERT_KEY, CORNER_EDGE_KEY, FACE_OFFSETS_KEY)

hash_of, pack_array, unpack_array, pack_all, packed_bytes = (
    _N.topo_hash, _N.topo_pack, _N.topo_unpack, _N.topo_pack_all, _N.topo_packed_bytes)
