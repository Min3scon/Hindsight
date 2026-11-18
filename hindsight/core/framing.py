"""Movie Mode camera framing and lighting math.

Pure Python + numpy. No bpy imports.

World axes are Blender's: Z up, the model's front faces -Y. Angles are radians.
A view basis is a (3,3) matrix whose COLUMNS are the camera's right (local X), up (local Y)
and back (local Z) in world space; the camera looks along -back.
"""
from __future__ import annotations

import math

import numpy as np

from .history import MeshState
from .types import DELTA, JOIN, TOPO, POSITION_KEY

# ------------------------------------------------------------------ light rig tuning
# Each lamp: azimuth from the view axis (toward the camera's right is positive), elevation above
# the camera's horizon, strength (W/m2, sun lamp), angular diameter, colour.
KEY_AZIMUTH = math.radians(-40.0)
KEY_ELEVATION = math.radians(35.0)
KEY_STRENGTH = 3.2
KEY_ANGLE = math.radians(15.0)
KEY_COLOR = (1.0, 0.96, 0.9)

FILL_AZIMUTH = math.radians(50.0)
FILL_ELEVATION = math.radians(5.0)
FILL_STRENGTH = 0.9
FILL_ANGLE = math.radians(40.0)
FILL_COLOR = (0.86, 0.92, 1.0)

RIM_AZIMUTH = math.radians(140.0)
RIM_ELEVATION = math.radians(30.0)
RIM_STRENGTH = 2.4
RIM_ANGLE = math.radians(10.0)
RIM_COLOR = (1.0, 1.0, 1.0)

LIGHT_DISTANCE = 3.0     # lamp location = center + (toward lamp) * LIGHT_DISTANCE * radius

_LIGHTS = (
    ("key", KEY_AZIMUTH, KEY_ELEVATION, KEY_STRENGTH, KEY_ANGLE, KEY_COLOR),
    ("fill", FILL_AZIMUTH, FILL_ELEVATION, FILL_STRENGTH, FILL_ANGLE, FILL_COLOR),
    ("rim", RIM_AZIMUTH, RIM_ELEVATION, RIM_STRENGTH, RIM_ANGLE, RIM_COLOR),
)

MAX_MARGIN = 0.9


# ------------------------------------------------------------------ camera
def view_basis(azimuth: float, elevation: float) -> np.ndarray:
    """(3,3) float64; columns = right, up, back (world). See module docstring."""
    az = float(azimuth)
    el = float(elevation)
    ca, sa = math.cos(az), math.sin(az)
    ce, se = math.cos(el), math.sin(el)
    back = np.array([sa * ce, -ca * ce, se], np.float64)
    right = np.array([ca, sa, 0.0], np.float64)
    up = np.cross(back, right)
    up /= np.linalg.norm(up)
    return np.column_stack((right, up, back))


def lens_tangents(lens_mm, sensor_width, sensor_height, sensor_fit, res_x, res_y,
                  aspect_x=1.0, aspect_y=1.0) -> tuple:
    """(tan_x, tan_y) of half the horizontal and vertical field of view, as Blender's perspective
    camera computes them."""
    lens = max(float(lens_mm), 1e-6)
    fx = max(float(res_x), 1.0) * max(float(aspect_x), 1e-6)
    fy = max(float(res_y), 1.0) * max(float(aspect_y), 1e-6)
    fit_ = str(sensor_fit).upper()
    if fit_ == "VERTICAL":
        horizontal, sensor = False, float(sensor_height)
    elif fit_ == "HORIZONTAL":
        horizontal, sensor = True, float(sensor_width)
    else:
        horizontal, sensor = fx >= fy, float(sensor_width)
    t = max(sensor, 1e-6) * 0.5 / lens
    if horizontal:
        return t, t * fy / fx
    return t * fx / fy, t


def shrink(tan_x, tan_y, margin: float) -> tuple:
    """Tangents scaled by (1 - margin), margin clamped to [0, 0.9]."""
    try:
        m = float(margin)
    except (TypeError, ValueError):
        m = 0.0
    if not math.isfinite(m):
        m = 0.0
    m = min(max(m, 0.0), MAX_MARGIN)
    return float(tan_x) * (1.0 - m), float(tan_y) * (1.0 - m)


def _points(points):
    """(N,3) float64 of the finite rows, or None."""
    if points is None:
        return None
    try:
        p = np.asarray(points, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if p.size == 0 or p.size % 3:
        return None
    p = p.reshape(-1, 3)
    ok = np.isfinite(p).all(axis=1)
    if not ok.all():
        p = p[ok]
    return p if len(p) else None


def extents(points, basis, tan_x, tan_y):
    """[max(x+z*tx), max(-x+z*tx), max(y+z*ty), max(-y+z*ty), min z, max z] (float64) with
    x,y,z = points @ basis; None when there are no finite points."""
    p = _points(points)
    if p is None:
        return None
    c = p @ np.asarray(basis, dtype=np.float64)
    x, y, z = c[:, 0], c[:, 1], c[:, 2]
    tx, ty = float(tan_x), float(tan_y)
    zx = z * tx
    zy = z * ty
    return np.array([np.max(x + zx), np.max(zx - x), np.max(y + zy), np.max(zy - y),
                     np.min(z), np.max(z)], np.float64)


def merge(a, b):
    """Union of two extents (None-safe)."""
    if a is None:
        return None if b is None else np.array(b, np.float64)
    if b is None:
        return np.array(a, np.float64)
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    out = np.maximum(a, b)
    out[4] = min(a[4], b[4])
    return out


def fit(ext, basis, tan_x, tan_y, min_distance: float = 1e-3):
    """Closest camera on the basis' viewing direction with every point of `ext` in frame."""
    if ext is None:
        return None
    e = np.asarray(ext, np.float64)
    tx, ty = float(tan_x), float(tan_y)
    if e.shape != (6,) or not np.isfinite(e).all() or not (tx > 0 and ty > 0):
        return None
    if not (math.isfinite(tx) and math.isfinite(ty)):
        return None
    md = float(min_distance)
    if not (md > 0 and math.isfinite(md)):
        md = 1e-3
    A, B, C, D, zmin, zmax = (float(v) for v in e)
    cx = (A - B) * 0.5
    cy = (C - D) * 0.5
    cz = max((A + B) / (2.0 * tx), (C + D) / (2.0 * ty), zmax + md)
    m = np.asarray(basis, np.float64)
    zmid = (zmin + zmax) * 0.5
    loc = m @ np.array([cx, cy, cz])
    ctr = m @ np.array([cx, cy, zmid])
    return {
        "location": tuple(float(v) for v in loc),
        "near": cz - zmax,
        "far": cz - zmin,
        "center": tuple(float(v) for v in ctr),
        "depth": cz - zmid,
    }


def bounds(points):
    """(world min xyz, world max xyz) of the finite points, or None."""
    p = _points(points)
    if p is None:
        return None
    return p.min(axis=0), p.max(axis=0)


def merge_bounds(a, b):
    """Union of two bounds (None-safe)."""
    if a is None:
        return None if b is None else (np.array(b[0], np.float64), np.array(b[1], np.float64))
    if b is None:
        return np.array(a[0], np.float64), np.array(a[1], np.float64)
    return (np.minimum(np.asarray(a[0], np.float64), np.asarray(b[0], np.float64)),
            np.maximum(np.asarray(a[1], np.float64), np.asarray(b[1], np.float64)))


# ------------------------------------------------------------------ lighting
def light_rig(basis, center, radius: float) -> list:
    """Three sun lamps (key, fill, rim) placed relative to the camera so the visible side is lit."""
    m = np.asarray(basis, np.float64)
    right, up, back = m[:, 0], m[:, 1], m[:, 2]
    c = np.asarray(center, np.float64).reshape(3)
    r = float(radius)
    if not (math.isfinite(r) and r > 0):
        r = 1.0
    out = []
    for role, az, el, strength, angle, color in _LIGHTS:
        # unit vector from the model toward the lamp; az 0 el 0 = toward the camera
        toward = math.cos(el) * (math.cos(az) * back + math.sin(az) * right) + math.sin(el) * up
        toward /= np.linalg.norm(toward)
        d = -toward
        out.append({
            "role": role,
            "direction": tuple(float(v) for v in d),
            "location": tuple(float(v) for v in (c - d * LIGHT_DISTANCE * r)),
            "strength": float(strength),
            "angle": float(angle),
            "color": tuple(float(v) for v in color),
        })
    return out


# ------------------------------------------------------------------ history sampling
def sample_positions(length: int, cursor: int, max_samples: int = 48) -> list:
    """Sorted unique positions in [0, length], always with 0, length and the (clamped) cursor,
    evenly spread, at most max(max_samples, 2) + 1 values."""
    n = max(0, int(length))
    cur = min(max(int(cursor), 0), n)
    k = max(2, int(max_samples))
    if n + 1 <= k:
        vals = set(range(n + 1))
    else:
        vals = {int(round(v)) for v in np.linspace(0.0, n, k)}
    vals.update((0, n, cur))
    return sorted(vals)


def positions_along(h, state, target_uid: str, wanted):
    """Yield (position, flat float32 positions) of `target_uid` at each reachable wanted position.

    `state` is the object's MeshState at ``h.cursor``. Works on private copies: the history,
    its entries and `state` are left unchanged. Walks backward from the cursor to the smallest
    wanted position, then forward to the largest, mirroring what History.goto does to the
    positions; a direction stops at a JOIN, at a step it can't apply, or on bad data.
    The yielded array is valid only until the next iteration.
    """
    try:
        if state is None:
            return
        pos0 = state.arrays.get(POSITION_KEY)
        if pos0 is None:
            return
        n = len(h.entries)
        cur = int(h.cursor)
        if cur < 0 or cur > n:
            return
        want = sorted({int(w) for w in wanted if 0 <= int(w) <= n})
        counts = dict(state.counts)
        base = np.array(pos0, dtype=np.float32, copy=True).reshape(-1)
    except Exception:
        return
    if not want:
        return
    if cur in want:
        yield cur, np.array(base, copy=True)
    back = [w for w in want if w < cur]
    if back:
        yield from _walk(h, counts, base, target_uid, cur, back, False)
    fwd = [w for w in want if w > cur]
    if fwd:
        yield from _walk(h, counts, base, target_uid, cur, fwd, True)


def _walk(h, counts, base, target_uid, cur, want, forward):
    """One direction of positions_along. `want` is sorted and all on that side of `cur`."""
    ms = MeshState(dict(counts), {POSITION_KEY: np.array(base, copy=True)})
    if forward:
        order = range(cur, want[-1])
        targets = set(want)
    else:
        order = range(cur - 1, want[0] - 1, -1)
        targets = set(want)
    for i in order:
        try:
            e = h.entries[i]
            if e.kind == JOIN:
                return
            if e.target != target_uid:
                ok = True
            else:
                ok = _cross(ms, e.load(), forward)
        except Exception:
            return
        if not ok:
            return
        p = i + 1 if forward else i
        if p in targets:
            arr = ms.arrays.get(POSITION_KEY)
            if arr is None or arr.size != int(ms.counts.get("POINT", -1)) * 3:
                return
            yield p, arr


def _cross(ms: MeshState, step, forward: bool) -> bool:
    """Apply what History._mesh does to the positions for one step of this object."""
    kind = step.kind
    if kind == JOIN:
        return False
    if kind == DELTA:
        nverts = int(ms.counts.get("POINT", 0))
        for d in step.deltas:
            if d.key != POSITION_KEY:
                continue
            if len(d.idx) and int(d.idx[-1]) >= nverts:
                return False
            ms.apply_delta(d, forward)
        return True
    if kind == TOPO:
        snap = step.after if forward else step.before
        if snap is None:
            return False
        p = snap.arrays.get(POSITION_KEY)
        nv = int(snap.counts.get("POINT", -1))
        if p is None or p.size != nv * 3:
            return False
        ms.counts = dict(snap.counts)
        ms.arrays = {POSITION_KEY: np.array(p, dtype=np.float32, copy=True).reshape(-1)}
        return True
    return True     # DATA and other kinds don't touch the mesh
