"""#129: the equivalence check of the Rust moves (#24, dev/RUST_PLAN.md rule 2): the reference (tests/reference/) and
the engine run on the same input, and must give the same result, write the same bytes and raise the same exception
(class and message), bit for bit.

    equiv.check(ref.registry_from_bytes, persist.registry_from_bytes, data, key=equiv.registry)
    equiv.check(ref.atomic_write, journal.atomic_write, equiv.Out("h.bin"), data)

Each side gets its own deep copy of the arguments; an `Out(name)` argument becomes a path in a fresh empty folder
of its own. Afterwards the two results (through `key`, if given), every argument (what each side wrote in place)
and the files in the two folders must be the same. Floats compare by their bits (-0.0 is not 0.0, a NaN is
itself), numpy arrays by dtype, shape and bytes, other objects by type and attributes.
"""
import copy
import json
import os
import shutil
import struct
import tempfile

import numpy as np


class Out:
    """An output path argument: `name` in a fresh empty folder, one per side."""

    def __init__(self, name):
        self.name = name


def check(ref, eng, *args, key=None, **kw):
    """Run ref and eng on the same input and assert they agree (see the module docstring). Returns the reference's
    result, or the exception both raised."""
    sides = [_run(f, args, kw) for f in (ref, eng)]
    try:
        (r, ra, rkw, rfiles, _), (e, ea, ekw, efiles, _) = sides
        if isinstance(r, BaseException) or isinstance(e, BaseException):
            assert _exc(r) == _exc(e), f"exception: reference {_exc(r)}, engine {_exc(e)}"
        else:
            rk, ek = (key(r), key(e)) if key else (r, e)
            where = _diff(rk, ek)
            assert where is None, f"result differs at {where}"
        for i, (x, y) in enumerate(zip(ra, ea)):
            where = _diff(x, y)
            assert where is None, f"argument {i} differs after the call at {where}"
        for k in rkw:
            where = _diff(rkw[k], ekw[k])
            assert where is None, f"argument {k} differs after the call at {where}"
        assert rfiles.keys() == efiles.keys(), f"files written: reference {sorted(rfiles)}, engine {sorted(efiles)}"
        for name in rfiles:
            assert rfiles[name] == efiles[name], f"bytes written to {name} differ"
    finally:
        for s in sides:
            for d in s[4]:
                shutil.rmtree(d, ignore_errors=True)
    return r


def _run(f, args, kw):
    dirs = []

    def real(a):
        if isinstance(a, Out):
            dirs.append(tempfile.mkdtemp(prefix="hs_equiv_"))
            return os.path.join(dirs[-1], a.name)
        if isinstance(a, memoryview):       # deepcopy can't copy one. shortcut: bytes only, read-only; keep format if needed
            return memoryview(bytes(a))
        return copy.deepcopy(a)

    rargs = [real(a) for a in args]
    rkw = {k: real(v) for k, v in kw.items()}
    try:
        out = f(*rargs, **rkw)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:      # noqa: BLE001 - the exception is what is compared (an engine panic too)
        out = exc
    files = {}
    for i, d in enumerate(dirs):
        for top, _sub, names in os.walk(d):
            for n in names:
                p = os.path.join(top, n)
                with open(p, "rb") as fh:
                    files[f"{i}/" * bool(i) + os.path.relpath(p, d).replace(os.sep, "/")] = fh.read()
    # an Out argument is compared as the files in its folder, not as its path
    rargs = [o.name if isinstance(o, Out) else a for a, o in zip(rargs, args)]
    rkw = {k: kw[k].name if isinstance(kw[k], Out) else v for k, v in rkw.items()}
    return out, rargs, rkw, files, dirs


def _exc(x):
    return None if not isinstance(x, BaseException) else (type(x).__qualname__, str(x))


def _diff(a, b, at="top", seen=None):
    """None when a and b are the same bit for bit, else where they first differ."""
    seen = set() if seen is None else seen
    if (id(a), id(b)) in seen:                 # a cycle: already being compared
        return None
    seen.add((id(a), id(b)))
    if type(a) is not type(b):
        return f"{at} (type {type(a).__name__} / {type(b).__name__})"
    if isinstance(a, float):
        return None if struct.pack("<d", a) == struct.pack("<d", b) else at
    if isinstance(a, np.ndarray):
        same = a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()
        return None if same else f"{at} (array {a.dtype}{a.shape} / {b.dtype}{b.shape})"
    if isinstance(a, np.generic):
        return None if a.dtype == b.dtype and a.tobytes() == b.tobytes() else at
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return f"{at} (length {len(a)} / {len(b)})"
        return next((w for i, (x, y) in enumerate(zip(a, b)) if (w := _diff(x, y, f"{at}[{i}]", seen))), None)
    if isinstance(a, dict):
        if list(a) != list(b):
            return f"{at} (keys)"
        return next((w for k in a if (w := _diff(a[k], b[k], f"{at}[{k!r}]", seen))), None)
    if isinstance(a, (set, frozenset)):
        return None if a == b else at
    if hasattr(a, "__dict__") or hasattr(a, "__slots__"):
        na, nb = _names(a), _names(b)                # both sides' names: one only one side has is a difference
        if na != nb:
            return f"{at} (attributes {sorted(na ^ nb)} on one side only)"
        w = next((w for n in sorted(na) if (w := _diff(getattr(a, n), getattr(b, n), f"{at}.{n}", seen))), None)
        if w is not None or na:
            return w                                 # nothing set on it: only its == can tell (below)
    return None if a == b else at


def _names(o):
    """The attributes set on an object: its __dict__ and every __slots__ slot that holds a value."""
    out = set(getattr(o, "__dict__", None) or ())
    for c in type(o).__mro__:
        sl = getattr(c, "__slots__", ())
        out.update(n for n in ((sl,) if isinstance(sl, str) else sl) if n not in ("__dict__", "__weakref__")
                   and hasattr(o, n))
    return out


def _meta(m):
    """A step's meta as the file keeps it, through plain json: the extra's snapshots left out."""
    m = dict(m or {})
    if isinstance(m.get("extra"), dict):
        m["extra"] = {k: v for k, v in m["extra"].items() if k != "_snapshots"}
    return json.loads(json.dumps(m))


def registry(reg):
    """The key for a registry a move reads (equiv.check(..., key=equiv.registry)): its load report (format, damaged,
    mended), its settings, and each history's members, cursor, morph target and steps as (bytes, CRC-32, meta).
    It runs no shipped code, so the reference's result is never read through a move's engine (rule 1)."""
    out = {"load_report": reg.load_report,
           "settings": (reg.next_seq, reg.use_global, reg.preserve_modes, dict(reg.by_object), set(reg.disabled),
                        set(reg.everything))}
    for uid, h in reg.histories.items():
        out[uid] = (list(h.members), h.cursor, h.morph_target, [(*e.blob_crc(), _meta(e.meta)) for e in h.entries])
    return out
