"""Movie Mode camera framing and lighting math.

Pure Python + numpy. No bpy imports.

World axes are Blender's: Z up, the model's front faces -Y. Angles are radians.
A view basis is a (3,3) matrix whose COLUMNS are the camera's right (local X), up (local Y)
and back (local Z) in world space; the camera looks along -back.
"""
from __future__ import annotations

import contextlib
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


_START = (-np.inf, -np.inf, -np.inf, -np.inf, np.inf, -np.inf)     # extents of no point (merge identity)


def _engine():
    from .native import ENGINE        # here, not at import: the panel loads (and says so) without the engine
    return ENGINE


def _flat_points(points):
    """points as a flat C-contiguous float32 or float64 array of 3 per point, or None when there are none."""
    try:
        p = np.asarray(points)
        if p.dtype.kind != "f" or p.dtype.itemsize not in (4, 8):
            p = p.astype(np.float64)
    except (TypeError, ValueError):
        return None
    if p.size == 0 or p.size % 3:
        return None
    return np.ascontiguousarray(p).reshape(-1)


def _views(bases, tans):
    b = np.ascontiguousarray(np.asarray(bases, dtype=np.float64).reshape(-1, 3, 3))
    t = np.ascontiguousarray(np.asarray(tans, dtype=np.float64).reshape(len(b), 2))
    return b, t, np.tile(np.array(_START, np.float64), (len(b), 1))


def extents(points, basis, tan_x, tan_y, chunk: int = 65536):
    """[max(x+z*tx), max(-x+z*tx), max(y+z*ty), max(-y+z*ty), min z, max z] (float64) with
    x,y,z = points @ basis; None when there are no finite points. `basis` may be any 3x3 matrix
    (see `shifted`). R085: measured by the engine in one pass (`extents_into`), the product written
    out left to right in float64 (numpy's matmul went through BLAS: the last bits may differ from
    before); tests/reference/framing_extents.py has the numpy version it equals. `chunk` is unused (kept for
    callers): nothing is copied."""
    p = _flat_points(points)
    if p is None:
        return None
    b, t, out = _views(np.asarray(basis, dtype=np.float64)[None], [(float(tan_x), float(tan_y))])
    if not _engine().extents_into(p, None, b, t, out):
        return None
    return out[0]


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
    with contextlib.suppress(TypeError, ValueError):
        q = np.asarray(points)
        if q.dtype.kind == "f" and q.size and q.size % 3 == 0:
            q = q.reshape(-1, 3)
            lo, hi = q.min(axis=0), q.max(axis=0)          # no float64 copy unless something isn't finite
            if np.isfinite(lo).all() and np.isfinite(hi).all():
                return lo.astype(np.float64), hi.astype(np.float64)
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


def extents_multi(points, bases, tans, chunk: int = 65536):
    """(k, 6) float64: `extents(points, bases[i], *tans[i])` for each of k bases, in one pass of the
    engine (R085). None when no point is finite under the first basis (a later basis under which none
    is finite keeps the extents of no point: -inf and inf). `chunk` is unused (kept for callers)."""
    p = _flat_points(points)
    if p is None:
        return None
    b, t, out = _views(bases, tans)
    _engine().extents_into(p, None, b, t, out)
    return out if out[0, 4] < np.inf else None


def plus(a, b):
    """Extents of the sum set {p + q} (Minkowski sum) from the extents of each set (None-safe:
    None if either is None). Every one of the six numbers is a maximum or a minimum of a linear
    function, so they add."""
    if a is None or b is None:
        return None
    return np.asarray(a, np.float64) + np.asarray(b, np.float64)


def ball_extents(radius: float, basis, tan_x, tan_y):
    """Extents of a ball of `radius` around the origin, in the space `basis` maps from (any 3x3
    matrix: a ball in a mesh's own space becomes an ellipsoid in camera axes)."""
    r = max(float(radius), 0.0)
    b = np.asarray(basis, np.float64)
    tx, ty = float(tan_x), float(tan_y)
    a = np.array([[1, 0, tx], [-1, 0, tx], [0, 1, ty], [0, -1, ty], [0, 0, 1]], np.float64)
    n = np.linalg.norm(a @ b.T, axis=1) * r          # |b @ a_i|: the largest value of p @ b @ a_i over the ball
    return np.array([n[0], n[1], n[2], n[3], -n[4], n[4]], np.float64)


def box_from_extents(ext):
    """(lo, hi) xyz from extents taken with the identity basis and zero tangents."""
    e = np.asarray(ext, np.float64)
    return np.array([-e[1], -e[3], e[4]]), np.array([e[0], e[2], e[5]])


# ------------------------------------------------------------------ modifiers
# A modifier stack is described with plain values (camrig reads them from Blender):
#   ("MIRROR", (x, y, z), frame)  frame: 4x4 matrix of the mirror plane's space in the mesh's space
#                                 (identity: the object's own origin and axes; else the mirror object's)
#   ("ARRAY", count, constant xyz, relative xyz)  fixed count, no object offset, no caps
#   anything else is left to the measured reach (see `reach`).
# `envelope` turns it into groups (L, Z): the stack's output lies in the union over the groups of
# {L @ p + z : p a vertex of the base mesh, z in the convex hull of Z}. This is exact for Mirror
# (the reflected copies) and for Array (the shifted copies); every other modifier is covered by the
# reach measured on the shape as it is now.
MAX_GROUPS = 64
MAX_OFFSETS = 64


def _corners(lo, hi) -> np.ndarray:
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])],
                    np.float64)


def _group_box(L, Z, lo, hi):
    """Axis-aligned box around {L @ p + z} for p in the box lo..hi and z in Z."""
    c = _corners(lo, hi) @ np.asarray(L, np.float64).T
    return c.min(0) + Z.min(0), c.max(0) + Z.max(0)


def _thin(Z) -> np.ndarray:
    """Fewer offsets with the same or a larger hull: the corners of their box past MAX_OFFSETS."""
    Z = np.unique(np.round(Z, 12), axis=0)
    if len(Z) > MAX_OFFSETS:
        Z = _corners(Z.min(0), Z.max(0))
    return Z


def envelope(stack, base_lo, base_hi, exact: bool = True):
    """[(L 3x3, Z (m,3)), ...] for a modifier stack (see above) on a base mesh whose vertices lie
    in the box base_lo..base_hi (mesh space). With exact=True the box is the mesh's own (the shape
    now) and an Array's relative offset uses its size; with exact=False the box holds every shape
    of a history and the offset may be any size from zero up to the box's, so the result holds every
    one of them. The L of the groups (and their order) don't depend on the box or on `exact`.
    None when there are more than MAX_GROUPS different L."""
    groups = [(np.eye(3), np.zeros((1, 3)))]
    actual = True                   # the offsets are real copies (not a range of possible ones)
    lo0, hi0 = np.asarray(base_lo, np.float64), np.asarray(base_hi, np.float64)
    for mod in stack:
        kind = mod[0]
        if kind == "MIRROR":
            axes, fr = mod[1], np.asarray(mod[2], np.float64)
            try:
                inv = np.linalg.inv(fr)
            except np.linalg.LinAlgError:
                continue                                   # a flat mirror object: Blender mirrors nothing useful
            for ax in range(3):
                if not axes[ax]:
                    continue
                s = np.eye(4)
                s[ax, ax] = -1.0
                m = fr @ s @ inv
                R, c = m[:3, :3], m[:3, 3]
                groups = groups + [(R @ L, Z @ R.T + c) for L, Z in groups]
        elif kind == "ARRAY":
            count, const, rel = int(mod[1]), np.asarray(mod[2], np.float64), np.asarray(mod[3], np.float64)
            if count < 2:
                continue
            lo = np.full(3, np.inf)
            hi = np.full(3, -np.inf)
            for L, Z in groups:
                a, b = _group_box(L, Z, lo0, hi0)
                lo, hi = np.minimum(lo, a), np.maximum(hi, b)
            size = np.maximum(hi - lo, 0.0)
            # copy j is shifted by j * (const + rel * size of the input); its hull over j = 0 .. count-1
            # is spanned by j = 0 and j = count - 1. The size is known exactly when every copy so far
            # maps the base's box onto a box (axis-aligned mirrors); else it lies between the
            # unmirrored part's size and `size`; for a history, anywhere from 0 up to `size`.
            aligned = all(np.count_nonzero(np.abs(L) > 1e-9, axis=1).max() <= 1 for L, _Z in groups)
            if exact and aligned and actual:
                low = size
            elif exact and actual:
                a, b = _group_box(groups[0][0], groups[0][1], lo0, hi0)     # the identity group
                low = np.minimum(np.maximum(b - a, 0.0), size)
                actual = False
            else:
                low = np.zeros(3)
                actual = False
            shifts = (count - 1) * (const + _corners(rel * low, rel * size))
            groups = [(L, np.vstack([Z] + [Z + s for s in shifts])) for L, Z in groups]
        # anything else: left to the reach
        merged = {}
        for L, Z in groups:
            key = np.round(L, 9).tobytes()
            if key in merged:
                merged[key] = (merged[key][0], np.vstack([merged[key][1], Z]))
            else:
                merged[key] = (L, Z)
        if len(merged) > MAX_GROUPS:
            return None
        groups = [(L, _thin(Z)) for L, Z in merged.values()]
    return groups


def directions(extra=None) -> np.ndarray:
    """(3, m) unit directions: the 6 of a cube's faces and the 8 of its corners, plus `extra` (rows)."""
    d = np.array([(i, j, k) for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)
                  if abs(i) + abs(j) + abs(k) in (1, 3)], np.float64)
    if extra is not None:
        d = np.vstack([d, np.asarray(extra, np.float64).reshape(-1, 3)])
    n = np.linalg.norm(d, axis=1)
    d = d[n > 1e-12] / n[n > 1e-12, None]
    return d.T


def _finite32(points) -> np.ndarray:
    """(n, 3) float32 of the finite rows (no copy when the points are float32 and all finite)."""
    p = np.asarray(points).reshape(-1, 3)
    if p.dtype != np.float32:
        with np.errstate(over="ignore"):
            p = p.astype(np.float32)
    ok = np.isfinite(p).all(axis=1)
    return p if ok.all() else p[ok]


def _support(points, dirs, chunk: int = 262144):
    """(m,) max of points @ dirs over the finite points; -inf where there are none. float32 (a
    measure, not the framing itself), and plain element-wise products: see extents_multi."""
    p = _finite32(points)
    d = np.asarray(dirs, np.float64)
    out = np.full(d.shape[1], -np.inf)
    for a in range(0, len(p), chunk):
        c = p[a:a + chunk]
        x, y, z = (np.ascontiguousarray(c[:, i]) for i in range(3))
        with np.errstate(invalid="ignore", over="ignore"):
            for j in range(d.shape[1]):
                dx, dy, dz = (np.float32(v) for v in d[:, j])
                v = x * dx
                v += y * dy
                v += z * dz
                out[j] = max(out[j], float(v.max()))
    return out


def reach(evaluated, base, groups, dirs=None) -> float:
    """How far the evaluated shape (what renders now) reaches past what `envelope` says about the
    base mesh now: the largest amount, over the directions `dirs`, by which the evaluated shape goes
    further than the envelope. 0 when the envelope already holds it. Rounded up by a millionth of
    the shape's size, for the float32 products."""
    ev = np.asarray(evaluated).reshape(-1, 3)
    bs = np.asarray(base).reshape(-1, 3)
    if not len(ev) or not len(bs) or not groups:
        return 0.0
    U = directions() if dirs is None else np.asarray(dirs, np.float64)
    m = U.shape[1]
    s_ev = _support(ev, U)
    wide = np.concatenate([np.asarray(L, np.float64).T @ U for L, _Z in groups], axis=1)
    s_b = _support(bs, wide).reshape(len(groups), m)                 # one pass over the base mesh
    s_z = np.stack([(np.asarray(Z, np.float64) @ U).max(0) for _L, Z in groups])
    s_env = (s_b + s_z).max(0)
    ok = np.isfinite(s_ev) & np.isfinite(s_env)
    if not ok.any():
        return 0.0
    size = float(np.max(np.abs(s_ev[ok])))
    return float(max(0.0, np.max(s_ev[ok] - s_env[ok]) + 1e-6 * size))


def extents_radii(points, radii, basis, tan_x, tan_y, chunk: int = 65536):
    """Extents (as `extents`) of the union of balls: one around each point, of its own radius, in
    the space `basis` maps from (any 3x3 matrix). None when there are no finite points."""
    try:
        p = np.asarray(points).reshape(-1, 3)
        r = np.asarray(radii).reshape(-1)
    except (TypeError, ValueError):
        return None
    if not len(p) or len(r) != len(p):
        return None
    b = np.asarray(basis, np.float64)
    tx, ty = float(tan_x), float(tan_y)
    f = np.array([[1, 0, tx], [-1, 0, tx], [0, 1, ty], [0, -1, ty], [0, 0, 1]], np.float64)
    g = np.linalg.norm(f @ b.T, axis=1)               # |b @ f_i|: what a unit ball adds along f_i
    out = None
    for a in range(0, len(p), chunk):
        with np.errstate(invalid="ignore", over="ignore"):
            c = p[a:a + chunk].astype(np.float64) @ b
            rr = np.maximum(r[a:a + chunk].astype(np.float64), 0.0)
        ok = np.isfinite(c).all(axis=1) & np.isfinite(rr)
        if not ok.all():
            c, rr = c[ok], rr[ok]
        if not len(c):
            continue
        x, y, z = c[:, 0], c[:, 1], c[:, 2]
        e = np.array([np.max(x + z * tx + rr * g[0]), np.max(z * tx - x + rr * g[1]),
                      np.max(y + z * ty + rr * g[2]), np.max(z * ty - y + rr * g[3]),
                      np.min(z - rr * g[4]), np.max(z + rr * g[4])], np.float64)
        out = e if out is None else merge(out, e)
    return out


# ------------------------------------------------------------------ the history
def history_vertex_reach(h, state, target_uid: str):
    """(n,) float32: for each vertex of the mesh at the cursor (`state`), the farthest it is from
    its place there at any position of History `h`, or None when that can't be told (no state, or a
    reachable step changes the number of vertices, so they don't correspond any more). One walk
    over the history like `history_extents_multi`, on private copies; a direction stops where
    that one stops."""
    try:
        pos0 = state.light.get(POSITION_KEY) if state is not None else None
        n = len(h.entries)
        cur = int(h.cursor)
        if pos0 is None or cur < 0 or cur > n:
            return None
        ref = np.asarray(pos0, np.float32).reshape(-1, 3)
        nv = len(ref)
    except Exception:
        return None
    out = np.zeros(nv, np.float32)
    for forward, order in ((False, range(cur - 1, -1, -1)), (True, range(cur, n))):
        ms = MeshState(dict(state.counts), {POSITION_KEY: np.array(ref, dtype=np.float32, copy=True).reshape(-1)})
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
                if len(arr) != nv:
                    return None
                for idx in changed:
                    sel = slice(None) if idx is None else idx
                    with np.errstate(invalid="ignore", over="ignore"):
                        d = np.sqrt(((arr[sel] - ref[sel]).astype(np.float64) ** 2).sum(axis=1))
                    d[~np.isfinite(d)] = 0.0
                    out[sel] = np.maximum(out[sel], (d * (1.0 + 1e-6)).astype(np.float32))
            except Exception:
                break
    return out


def history_extents_multi(h, state, target_uid: str, bases, tans, batch: int = 65536):
    """Extents (as `extents` gives them) of the mesh of `target_uid` over every position of History
    `h` other than the cursor's, for k bases at once ((k,3,3) bases, k (tan_x, tan_y) pairs): (k, 6),
    or None when it has none.

    Merged with the extents of the mesh at the cursor, a camera fitted to the result has every step
    of the time lapse in frame, exactly. One walk over the history that looks only at the vertices
    each step moves, so it costs about as much as one scrub from end to end, whatever the mesh
    size. Works on private copies: the history, its entries and `state` are left unchanged; a
    direction stops at a JOIN or at a step that can't be applied.

    R085: the vertices each step moves are measured by the engine where they are, right after the
    step (`extents_into` with the step's indices), into one running result: no copies of them are
    gathered (`batch` is unused, kept for callers)."""
    try:
        pos0 = state.light.get(POSITION_KEY) if state is not None else None
        n = len(h.entries)
        cur = int(h.cursor)
        if pos0 is None or cur < 0 or cur > n:
            return None
    except Exception:
        return None
    measure = _engine().extents_into
    b, t, out = _views(bases, tans)
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
                arr = ms.arrays[POSITION_KEY]
                for idx in changed:
                    measure(arr, None if idx is None else np.ascontiguousarray(idx, dtype=np.uint32), b, t, out)
            except Exception:
                break
    return out if out[0, 4] < np.inf else None


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
