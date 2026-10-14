"""Data adapters: what Hindsight records on a datablock besides mesh channels (see ADAPTERS.md).

An adapter reads a datablock's tracked values as a flat dict (path -> JSON value), and writes given
paths back. Diffing and playback are generic (core). The mesh recorder in runtime.py is adapter one
and keeps its own fast path; everything here is for other data.

The generic RNA adapter reads properties listed in ALLOWLIST by RNA path. Values are normalised:
floats stay Python floats (exact for Blender's float and double properties), vectors, colours,
eulers, quaternions and matrices become lists, enums their identifier, ID pointers the target's
Hindsight id (or None).
"""
from __future__ import annotations

import struct
import time

import bpy

from ..core.types import MISSING
from . import idmap, log

# ID type (bl_rna identifier) -> ((path, kind), ...). kind: "value" or "ref" (ID pointer).
# Each row is a property a user changes directly; rows are added tier by tier (see COVERAGE.md).
ALLOWLIST: dict = {
    "Object": (
        ("location", "value"), ("rotation_mode", "value"), ("rotation_euler", "value"),
        ("rotation_quaternion", "value"), ("rotation_axis_angle", "value"), ("scale", "value"),
        ("delta_location", "value"), ("delta_rotation_euler", "value"),
        ("delta_rotation_quaternion", "value"), ("delta_scale", "value"),
    ),
}


# Writing these paths makes Blender change others as a side effect (setting rotation_mode converts
# the rotation), so after writing one, its dependents are written again from the recorded state.
DEPENDENTS: dict = {
    "rotation_mode": ("rotation_euler", "rotation_quaternion", "rotation_axis_angle"),
}


def normalise(v):
    """A property value as a JSON value (exact)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, bpy.types.ID):
        return idmap.claim(v)
    if hasattr(v, "to_list") and hasattr(v, "col"):          # Matrix
        return [list(map(float, row)) for row in v]
    try:
        return [normalise(x) for x in v]                      # Vector, Euler, Quaternion, Color, arrays
    except TypeError:
        return str(v)


def same(a, b) -> bool:
    """Exact equality for normalised values: floats bit for bit (so -0.0 differs from 0.0 and a NaN
    equals the same NaN), lists element by element, everything else by type and value."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return _F64.pack(a) == _F64.pack(b)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


class Adapter:
    """Base class. Subclasses set `id` and `id_types` and implement capture() and write()."""
    id = ""
    id_types: tuple = ()
    budget_ms = 2.0          # capture() limit on the test models, per datablock (tests enforce it)

    def handles(self, idb) -> bool:
        return getattr(getattr(idb, "bl_rna", None), "identifier", None) in self.id_types

    def capture(self, idb) -> dict:
        raise NotImplementedError

    def write(self, idb, values: dict, recorded=None) -> None:
        """values: path -> value, or MISSING to remove (adapters that support removal).
        recorded(path) gives the recorded value of any other path (for side effects)."""
        raise NotImplementedError


class RNAAdapter(Adapter):
    """Properties from ALLOWLIST, read and written by RNA path."""
    id = "rna"

    @property
    def id_types(self):
        return tuple(ALLOWLIST)

    def rows(self, idb):
        return ALLOWLIST.get(idb.bl_rna.identifier, ())

    def capture(self, idb) -> dict:
        out = {}
        for path, _kind in self.rows(idb):
            try:
                out[path] = normalise(idb.path_resolve(path))
            except Exception:
                continue
        return out

    def write(self, idb, values: dict, recorded=None) -> None:
        # In allowlist order: modes before the values they convert (setting rotation_mode makes
        # Blender convert the rotation, so the recorded rotation values are written after it).
        values = dict(values)
        if recorded is not None:
            for path in list(values):
                for dep in DEPENDENTS.get(path, ()):
                    if dep not in values:
                        values[dep] = recorded(dep)
        for path, kind in self.rows(idb):
            if path not in values:
                continue
            v = values[path]
            if v is MISSING:
                continue
            try:
                owner, attr = _split(idb, path)
                if kind == "ref":
                    v = idmap.find(v) if v else None
                setattr(owner, attr, v)
            except Exception as e:
                log.error_once(("rna_write", path, str(e)), f"could not restore {path}: {e}", e)


def _split(idb, path: str):
    """(owner struct, attribute) for an RNA path such as 'location' or 'constraints["A"].influence'."""
    dot = path.rfind(".")
    if dot < 0 or path.rfind("]") > dot:
        return idb, path
    return idb.path_resolve(path[:dot]), path[dot + 1:]


ADAPTERS = {a.id: a for a in (RNAAdapter(),)}
_F64 = struct.Struct("<d")


def for_datablock(idb) -> list:
    return [a for a in ADAPTERS.values() if a.handles(idb)]


def timed_capture(adapter, idb) -> tuple:
    t = time.perf_counter()
    v = adapter.capture(idb)
    return v, (time.perf_counter() - t) * 1000.0
