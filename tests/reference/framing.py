"""Reference for the engine's `extents_into` (R085): the Movie Mode camera's extents in numpy.

`extents` here is what the engine computes, bit for bit: x, y, z = p @ b written out left to right in float64
with numpy's elementwise operations, rows with a non-finite x, y or z left out. The `old_*` functions are
hindsight/core/framing.py's numpy code before R085 (the product through numpy's matmul, so BLAS): the engine
equals them to the last bits only."""
import numpy as np

from hindsight.core import framing as F
from hindsight.core.history import MeshState
from hindsight.core.types import JOIN, POSITION_KEY


def extents(points, basis, tan_x, tan_y):
    """As the engine: (6,) float64 or None."""
    p = np.asarray(points)
    if p.dtype.kind != "f" or p.dtype.itemsize not in (4, 8):
        p = p.astype(np.float64)
    if p.size == 0 or p.size % 3:
        return None
    p = p.reshape(-1, 3).astype(np.float64)
    b = np.asarray(basis, np.float64).reshape(3, 3)
    with np.errstate(invalid="ignore", over="ignore"):
        x = p[:, 0] * b[0, 0] + p[:, 1] * b[1, 0] + p[:, 2] * b[2, 0]
        y = p[:, 0] * b[0, 1] + p[:, 1] * b[1, 1] + p[:, 2] * b[2, 1]
        z = p[:, 0] * b[0, 2] + p[:, 1] * b[1, 2] + p[:, 2] * b[2, 2]
        ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        x, y, z = x[ok], y[ok], z[ok]
        if not len(z):
            return None
        tx, ty = float(tan_x), float(tan_y)
        zx = z * tx
        zy = z * ty
        return np.array([np.max(x + zx), np.max(zx - x), np.max(y + zy), np.max(zy - y), np.min(z), np.max(z)],
                        np.float64)


def old_extents(points, basis, tan_x, tan_y, chunk: int = 65536):
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
        out = e if out is None else F.merge(out, e)
    return out



def old_extents_multi(points, bases, tans, chunk: int = 65536):
    """(k, 6) float64: `extents(points, bases[i], *tans[i])` for each of k bases, one chunk of the
    points at a time (each chunk converted once). None when there are no finite points. One 3x3
    product per basis: a wider product goes through the threaded BLAS, which was 20 to 50 times
    slower here on a busy 4-core machine."""
    try:
        p = np.asarray(points)
        if p.dtype.kind != "f":
            p = p.astype(np.float64)
    except (TypeError, ValueError):
        return None
    if p.size == 0 or p.size % 3:
        return None
    p = p.reshape(-1, 3)
    b = np.asarray(bases, np.float64).reshape(-1, 3, 3)
    t = np.asarray(tans, np.float64).reshape(len(b), 2)
    out = None
    for a in range(0, len(p), chunk):
        c = p[a:a + chunk].astype(np.float64, copy=False)
        e = [old_extents(c, b[i], t[i, 0], t[i, 1], chunk=len(c) + 1) for i in range(len(b))]
        if e[0] is None:
            continue
        e = np.stack(e)
        if out is None:
            out = e
        else:
            lo = np.minimum(out[:, 4], e[:, 4])
            out = np.maximum(out, e)
            out[:, 4] = lo
    return out



def old_history_extents_multi(h, state, target_uid: str, bases, tans, batch: int = 65536):
    """`history_extents` for k bases at once ((k,3,3) bases, k (tan_x, tan_y) pairs): (k, 6) or None.
    The moved vertices of many steps are measured together (up to `batch` of them at a time)."""
    try:
        pos0 = state.light.get(POSITION_KEY) if state is not None else None
        n = len(h.entries)
        cur = int(h.cursor)
        if pos0 is None or cur < 0 or cur > n:
            return None
    except Exception:
        return None
    out = [None]
    buf = []
    held = [0]

    def take(pts):
        e = old_extents_multi(pts, bases, tans)
        if e is not None:
            out[0] = e if out[0] is None else np.concatenate(
                [np.maximum(out[0][:, :4], e[:, :4]), np.minimum(out[0][:, 4:5], e[:, 4:5]),
                 np.maximum(out[0][:, 5:], e[:, 5:])], axis=1)

    def flush():
        if buf:
            take(buf[0] if len(buf) == 1 else np.concatenate(buf))
            buf.clear()
            held[0] = 0

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
                if not F._cross(ms, e.load(), forward, changed):
                    break
                arr = ms.arrays[POSITION_KEY].reshape(-1, 3)
                for idx in changed:
                    if idx is None:
                        take(arr)
                    else:
                        buf.append(arr[idx])                 # a copy: later steps don't change it
                        held[0] += len(idx)
                        if held[0] >= batch:
                            flush()
            except Exception:
                break
        flush()
    return out[0]


