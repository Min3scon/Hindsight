"""Morph History brush math: target positions from the history and the blend.

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
    pos = state.arrays.get(POSITION_KEY)
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


def falloff(dist, radius) -> np.ndarray:
    """Smooth brush falloff: (1 - (d/r)^2)^2, 1 at the centre, 0 at and beyond r."""
    d = np.asarray(dist, dtype=np.float64)
    if radius is None or radius <= 0:
        return np.zeros(d.shape, np.float64)
    w = 1.0 - (d / float(radius)) ** 2
    np.clip(w, 0.0, 1.0, out=w)
    return w * w


def morph_blend(cur: np.ndarray, target: np.ndarray, idx, weights, strength: float, mask=None) -> None:
    """In place: move vertices `idx` of `cur` toward `target`.

    cur/target: flat float32 arrays (N*3). idx: vertex indices. weights: one per idx.
    Factor f = weights * strength * (1 - mask[idx]) clipped to [0, 1]; the lerp is
    written as cur*(1-f) + target*f so f == 1 lands exactly on the target and
    f == 0 leaves the vertex bit-identical.
    """
    idx = np.asarray(idx, dtype=np.int64)
    if idx.size == 0:
        return
    f = np.asarray(weights, dtype=np.float64) * float(strength)
    if mask is not None:
        f = f * (1.0 - np.asarray(mask, dtype=np.float64)[idx])
    f = np.clip(f, 0.0, 1.0)[:, None]
    c = cur.reshape(-1, 3)
    t = target.reshape(-1, 3)
    a = c[idx].astype(np.float64)
    b = t[idx].astype(np.float64)
    c[idx] = (a * (1.0 - f) + b * f).astype(cur.dtype)
