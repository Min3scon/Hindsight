"""Morph History brush math: target positions from the history (the blend is the engine's
`morph_dab`, R083; its numpy oracle is tests/reference/morph_dab.py).

Pure Python + numpy. No bpy imports.
"""
from __future__ import annotations

import numpy as np

from .history import MeshState
from .types import JOIN, TOPO, POSITION_KEY


def target_positions(h, state: MeshState, target_uid: str):
    """Flat float32 positions of `target_uid` at ``h.morph_target``.

    Works on a private copy of the position array of `state` (the mesh at
    ``h.cursor``): walks the entries between the cursor and the morph target
    and applies only their position deltas. The history and `state` are not
    modified. Returns None when there is no target, when the walk crosses a
    topology change (TOPO for this object, or any JOIN), or on bad data.
    """
    mt = getattr(h, "morph_target", None)
    if mt is None or state is None:
        return None
    pos = state.light.get(POSITION_KEY)
    if pos is None:
        return None
    n = len(h.entries)
    mt = int(mt)
    if mt < 0 or mt > n:
        return None
    ms = MeshState(dict(state.counts), {POSITION_KEY: np.array(pos, dtype=np.float32, copy=True)})
    cur = int(h.cursor)
    if mt < cur:
        order, forward = range(cur - 1, mt - 1, -1), False
    else:
        order, forward = range(cur, mt), True
    nverts = ms.arrays[POSITION_KEY].size // 3
    try:
        for i in order:
            e = h.entries[i]
            kind = e.kind
            if kind == JOIN:
                return None
            if e.target != target_uid:
                continue
            if kind == TOPO:
                return None
            step = e.load()
            if step.kind != "DELTA":
                return None
            for d in step.deltas:
                if d.key != POSITION_KEY:
                    continue
                if len(d.idx) and int(d.idx[-1]) >= nverts:
                    return None
                ms.apply_delta(d, forward)
    except (IndexError, ValueError, KeyError):
        return None
    out = ms.arrays.get(POSITION_KEY)
    if out is None or out.size != nverts * 3:
        return None
    return out
