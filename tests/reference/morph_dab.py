"""Reference for the engine's `morph_dab` (R083): one dab of the Morph History brush as hindsight/bl/morph.py
did it in numpy up to R081/R082 (the box test `in_box`, then the distances, `falloff`, the mask, the stroke's
strongest weight and `morph_blend` from the stroke's start; those two were in hindsight/core/morph.py). The engine
must give the same co, acc and count, bit for bit (NaN aside: any NaN)."""
import numpy as np


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


def in_box(co, c, r: float, hidden=None):
    """Indices (ascending) of the vertices of co (N x 3, float32) within r of c on every axis, hidden ones left
    out: one axis at a time, each on the vertices still in (R081); float64 differences."""
    cand = np.flatnonzero(np.abs(np.subtract(co[:, 0], c[0], dtype=np.float64)) <= r)
    for ax in (1, 2):
        if cand.size == 0:
            return cand
        cand = cand[np.abs(np.subtract(co[cand, ax], c[ax], dtype=np.float64)) <= r]
    if hidden is not None and cand.size:
        cand = cand[~hidden[cand]]
    return cand


def old_in_box(co, c, r: float, hidden=None):
    """The box test before R081 (all axes of every vertex at once)."""
    near = np.all(np.abs(co - c) <= r, axis=1)
    if hidden is not None:
        near &= ~hidden
    return np.flatnonzero(near)


def morph_dab(cur, start, target, acc, mask, hidden, cx, cy, cz, r, strength, box=in_box) -> int:
    """In place on cur (float32, 3 per vertex) and acc (float64 per vertex); returns the vertices moved."""
    c = np.array((cx, cy, cz), dtype=np.float64)
    co = cur.reshape(-1, 3)
    r = float(r)
    cand = box(co, c, r, hidden)
    if cand.size == 0:
        return 0
    d = np.sqrt(((co[cand].astype(np.float64) - c) ** 2).sum(axis=1))
    inside = d < r
    idx = cand[inside]
    if idx.size == 0:
        return 0
    w = falloff(d[inside], r) * float(strength)
    if mask is not None:
        w *= 1.0 - mask[idx]
    w = np.maximum(acc[idx], np.clip(w, 0.0, 1.0))
    acc[idx] = w
    before = co[idx].copy()
    co[idx] = start.reshape(-1, 3)[idx]
    morph_blend(cur, target, idx, w, 1.0)
    return int(np.count_nonzero(np.any(co[idx] != before, axis=1)))
