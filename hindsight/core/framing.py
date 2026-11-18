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


def extents(points, basis, tan_x, tan_y, chunk: int = 65536):
    """[max(x+z*tx), max(-x+z*tx), max(y+z*ty), max(-y+z*ty), min z, max z] (float64) with
    x,y,z = points @ basis; None when there are no finite points. `basis` may be any 3x3 matrix
    (see `shifted`). Worked through in chunks, so a big float32 mesh is never copied whole."""
    try:
        p = np.asarray(points)
        if p.dtype.kind != "f":
            p = p.astype(np.float64)
    except (TypeError, ValueError):
        return None
    if p.size == 0 or p.size % 3:
        return None
    p = p.reshape(-1, 3)
    b = np.asarray(basis, dtype=np.float64)
    tx, ty = float(tan_x), float(tan_y)
    out = None
    for a in range(0, len(p), chunk):
        with np.errstate(invalid="ignore", over="ignore"):
            c = p[a:a + chunk].astype(np.float64, copy=False) @ b
        if not np.isfinite(c).all():
            c = c[np.isfinite(c).all(axis=1)]
            if not len(c):
                continue
        x, y, z = c[:, 0], c[:, 1], c[:, 2]
        zx = z * tx
        zy = z * ty
        e = np.array([np.max(x + zx), np.max(zx - x), np.max(y + zy), np.max(zy - y),
                      np.min(z), np.max(z)], np.float64)
        out = e if out is None else merge(out, e)
    return out


def shifted(ext, offset, tan_x, tan_y):
    """Extents of points that were all moved by `offset` = (x, y, z) in camera axes. With it a mesh
    is measured in its own space: extents(local, matrix.T @ basis, ...) shifted by translation @ basis
    equals extents(world points, basis, ...), without making a world-space copy of the mesh."""
    if ext is None:
        return None
    ox, oy, oz = (float(v) for v in offset)
    tx, ty = float(tan_x), float(tan_y)
    return np.asarray(ext, np.float64) + np.array(
        [ox + oz * tx, -ox + oz * tx, oy + oz * ty, -oy + oz * ty, oz, oz], np.float64)


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


# ------------------------------------------------------------------ the history
def history_extents(h, state, target_uid: str, basis, tan_x, tan_y):
    """Extents (as `extents` gives them) of the mesh of `target_uid` over every position of History
    `h` other than the cursor's, or None when it has none.

    Merged with the extents of the mesh at the cursor, a camera fitted to the result has every step
    of the timelapse in frame, exactly. One walk over the history that looks only at the vertices
    each step moves, so it costs about as much as one scrub from end to end, whatever the mesh
    size. Works on private copies: the history, its entries and `state` are left unchanged; a
    direction stops at a JOIN or at a step that can't be applied.
    """
    try:
        pos0 = state.arrays.get(POSITION_KEY) if state is not None else None
        n = len(h.entries)
        cur = int(h.cursor)
        if pos0 is None or cur < 0 or cur > n:
            return None
    except Exception:
        return None
    out = None
    for forward, order in ((False, range(cur - 1, -1, -1)), (True, range(cur, n))):
        ms = MeshState(dict(state.counts), {POSITION_KEY: np.array(pos0, dtype=np.float32, copy=True).reshape(-1)})
        for i in order:
            try:
                e = h.entries[i]
                if e.kind == JOIN:
                    break
                if e.target != target_uid:
                    continue
                changed = []
                if not _cross(ms, e.load(), forward, changed):
                    break
                arr = ms.arrays[POSITION_KEY].reshape(-1, 3)
                for idx in changed:
                    out = merge(out, extents(arr if idx is None else arr[idx], basis, tan_x, tan_y))
            except Exception:
                break
    return out


def _cross(ms: MeshState, step, forward: bool, changed=None) -> bool:
    """Apply what History._mesh does to the positions for one step of this object. `changed`, if
    given, receives the vertex indices the step moved (None for every vertex)."""
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
            if changed is not None:
                changed.append(d.idx)
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
        if changed is not None:
            changed.append(None)
        return True
    return True     # DATA and other kinds don't touch the mesh
