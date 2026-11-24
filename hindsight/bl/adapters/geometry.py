"""Data adapters: curves, Grease Pencil, hair and point clouds (see ADAPTERS.md)."""
from __future__ import annotations

import contextlib
from .. import log
from .core import _UI_PROPS, set_struct_values, struct_values
from .keys import _GEOM_CACHE


_SPLINE_SKIP = frozenset(_UI_PROPS | {"hide"})
_BEZ_3 = ("co", "handle_left", "handle_right")
_BEZ_INT = ("handle_left_type", "handle_right_type")
_PT_1 = ("radius", "tilt", "weight_softbody")
_MB_SKIP = frozenset(_UI_PROPS | {"select", "hide"})


def _bulk(coll, field, n, width=1, kind=float):
    buf = [kind(0)] * (n * width)
    if n:
        coll.foreach_get(field, buf)
    return buf


# ---- Grease Pencil (D131): 4.3+ keeps strokes as curves with attributes in drawings; 4.2's legacy
# Grease Pencil keeps layers, frames, strokes and points
_ATTR_KEY = {"FLOAT": ("value", 1), "INT": ("value", 1), "BOOLEAN": ("value", 1), "INT8": ("value", 1),
             "FLOAT_VECTOR": ("vector", 3), "FLOAT2": ("vector", 2), "FLOAT_COLOR": ("color", 4),
             "BYTE_COLOR": ("color", 4), "INT32_2D": ("value", 2), "QUATERNION": ("value", 4),
             "FLOAT4X4": ("value", 16)}
_GPL_SKIP = frozenset(_UI_PROPS | {"name", "info", "select", "parent", "parent_bone", "matrix_inverse",
                                   "line_change", "annotation_opacity", "annotation_hide",
                                   "use_annotation_onion_skinning", "annotation_onion_before_range",
                                   "annotation_onion_after_range", "annotation_onion_before_color",
                                   "annotation_onion_after_color"})
_GPS_SKIP = frozenset(_UI_PROPS | {"select", "select_index"})
_GPP_FIELDS = (("co", 3), ("pressure", 1), ("strength", 1), ("vertex_color", 4), ("uv_factor", 1),
               ("uv_rotation", 1), ("uv_fill", 2))


def _is_legacy_gp(gp) -> bool:
    """4.2's Grease Pencil (layers of GPencilLayer with frames of strokes)."""
    try:
        return gp.bl_rna.properties["layers"].fixed_type.identifier == "GPencilLayer"
    except Exception:
        return False


def _stroke_sizes(d) -> list:
    n = len(d.strokes)
    if not n:
        return []                       # an empty drawing (its offsets can't be read in bulk)
    offs = [0] * (n + 1)
    d.curve_offsets.foreach_get("value", offs)
    return [b - a for a, b in zip(offs, offs[1:])]


def _drawing_record(d) -> dict:
    sizes = _stroke_sizes(d)
    return {"sizes": sizes, "attrs": _attrs_of(d)}


def _write_drawing(d, rec) -> None:
    sizes = rec.get("sizes") or []
    if _stroke_sizes(d) != sizes:
        if len(d.strokes):
            d.remove_strokes()
        if sizes:
            d.add_strokes(sizes)
    _write_attrs(d, rec.get("attrs") or {})
    with contextlib.suppress(Exception):
        d.tag_positions_changed()


def _gp_record(gp) -> dict:
    layers = []
    if _is_legacy_gp(gp):
        for l in gp.layers:
            frames = []
            for f in l.frames:
                strokes = []
                for st in f.strokes:
                    pts = st.points
                    n = len(pts)
                    r = {"values": struct_values(st, _GPS_SKIP), "n": n}
                    for fld, w in _GPP_FIELDS:
                        r[fld] = _bulk(pts, fld, n, w)
                    strokes.append(r)
                frames.append([int(f.frame_number), {"strokes": strokes}])
            layers.append({"name": l.info, "values": struct_values(l, _GPL_SKIP), "frames": frames})
        return {"legacy": True, "layers": layers}
    for l in gp.layers:
        frames = [[int(f.frame_number), _drawing_record(f.drawing)] for f in l.frames if f.drawing is not None]
        layers.append({"name": l.name, "values": struct_values(l, _GPL_SKIP), "frames": frames})
    return {"legacy": False, "layers": layers}


def _write_gp(gp, rec) -> None:
    want = rec.get("layers") or []
    names = [w["name"] for w in want]
    legacy = _is_legacy_gp(gp)
    lname = (lambda l: l.info) if legacy else (lambda l: l.name)
    for l in [l for l in gp.layers if lname(l) not in names]:
        gp.layers.remove(l)
    for w in want:
        l = next((x for x in gp.layers if lname(x) == w["name"]), None)
        if l is None:
            l = gp.layers.new(w["name"])
        set_struct_values(l, w.get("values") or {})
        wf = {fn: fr for fn, fr in w.get("frames") or ()}
        for f in [f for f in l.frames if int(f.frame_number) not in wf]:
            l.frames.remove(f) if legacy else l.frames.remove(int(f.frame_number))
        for fn, fr in w.get("frames") or ():
            f = next((x for x in l.frames if int(x.frame_number) == fn), None)
            if f is None:
                f = l.frames.new(fn)
            if legacy:
                _write_legacy_frame(f, fr)
            elif f.drawing is not None:
                _write_drawing(f.drawing, fr)


def _write_legacy_frame(f, fr) -> None:
    want = fr.get("strokes") or []
    if [len(st.points) for st in f.strokes] != [w["n"] for w in want]:
        for st in list(f.strokes):          # (legacy strokes have no clear())
            f.strokes.remove(st)
        for w in want:
            st = f.strokes.new()
            if w["n"]:
                st.points.add(w["n"])
    for st, w in zip(f.strokes, want):
        set_struct_values(st, w.get("values") or {})
        for fld, _w in _GPP_FIELDS:
            if fld in w and w["n"]:
                st.points.foreach_set(fld, w[fld])


# ---- hair curves and point clouds (D134): generic attributes
def _attrs_of(owner) -> dict:
    out = {}
    for a in owner.attributes:
        kw = _ATTR_KEY.get(a.data_type)
        if kw is None or a.name.startswith("."):
            continue
        key, width = kw
        kind = float if a.data_type in ("FLOAT", "FLOAT_VECTOR", "FLOAT2", "FLOAT_COLOR", "BYTE_COLOR",
                                        "QUATERNION", "FLOAT4X4") else int
        out[a.name] = [a.domain, a.data_type, _bulk(a.data, key, len(a.data), width, kind)]
    return out


def _write_attrs(owner, want: dict) -> None:
    for a in [a for a in owner.attributes if a.name not in want and not a.name.startswith(".")]:
        try:
            owner.attributes.remove(a)
        except Exception:
            pass
    for name, (domain, dtype, vals) in want.items():
        a = owner.attributes.get(name)
        if a is None or a.data_type != dtype or a.domain != domain:
            if a is not None:
                try:
                    owner.attributes.remove(a)
                except Exception:
                    pass
            a = owner.attributes.new(name, dtype, domain)
        key, width = _ATTR_KEY[dtype]
        if len(a.data) * width == len(vals):
            a.data.foreach_set(key, vals)


def _curve_sizes(c) -> list:
    n = len(c.curves)
    if not n:
        return []
    offs = [0] * (n + 1)
    c.curve_offset_data.foreach_get("value", offs)
    return [b - a for a, b in zip(offs, offs[1:])]


def _write_hair(c, g) -> None:
    sizes = g.get("sizes") or []
    if _curve_sizes(c) != sizes:
        if hasattr(c, "remove_curves"):
            if len(c.curves):
                c.remove_curves()
            if sizes:
                c.add_curves(sizes)
        else:                                    # Blender 4.2 can only add curves
            log.error_once(("hair", "resize"), "hair curves added or removed can't be put back on this Blender version")
            return
    _write_attrs(c, g.get("attrs") or {})
    c.update_tag()


def _write_points(pc, g) -> None:
    n = g.get("n", 0)
    if len(pc.points) != n:
        if hasattr(pc, "resize"):
            pc.resize(n)
        else:
            log.error_once(("points", "resize"), "points added or removed can't be put back on this Blender version")
            return
    _write_attrs(pc, g.get("attrs") or {})
    pc.update_tag()


def _geom_print(d, t):
    """Counts that change when points, strokes or elements are added or removed: some of those calls
    (a point cloud's resize) don't tell the depsgraph, so a kept record is checked against them."""
    if t == "Curves":
        return (len(d.curves), len(d.points), len(d.attributes))
    if t == "PointCloud":
        return (len(d.points), len(d.attributes))
    if t == "Lattice":
        return (len(d.points),)
    if t == "MetaBall":
        return (len(d.elements),)
    if t in ("Curve", "SurfaceCurve"):
        return tuple(len(sp.bezier_points) + len(sp.points) for sp in d.splines)
    return (len(d.layers),)


def geometry_of(d):
    """The geometry of curve, surface, lattice, metaball, Grease Pencil, hair curves and point cloud
    data, or None for other data."""
    t = d.bl_rna.identifier
    if t not in _GEOM_TYPES:
        return None
    k = d.session_uid
    fp = _geom_print(d, t)
    hit = _GEOM_CACHE.get(k)
    if hit is not None and hit[0] == fp:
        return hit[1]
    g = _read_geometry(d, t)
    _GEOM_CACHE[k] = (fp, g)
    return g


_GEOM_TYPES = frozenset({"Curves", "PointCloud", "GreasePencil", "GreasePencilv3", "Curve", "SurfaceCurve",
                         "Lattice", "MetaBall"})


def _read_geometry(d, t):
    if t == "Curves":
        return {"sizes": _curve_sizes(d), "attrs": _attrs_of(d)}
    if t == "PointCloud":
        return {"n": len(d.points), "attrs": _attrs_of(d)}
    if t in ("GreasePencil", "GreasePencilv3"):
        return _gp_record(d)
    if t == "Lattice":
        n = len(d.points)
        g = {"co_deform": _bulk(d.points, "co_deform", n, 3), "weight_softbody": _bulk(d.points, "weight_softbody", n)}
    elif t == "MetaBall":
        g = {"elements": [struct_values(e, _MB_SKIP) for e in d.elements]}
    else:
        rows = []
        for sp in d.splines:
            r = {"values": struct_values(sp, _SPLINE_SKIP)}
            if sp.type == "BEZIER":
                pts = sp.bezier_points
                n = len(pts)
                for f in _BEZ_3:
                    r[f] = _bulk(pts, f, n, 3)
                for f in _BEZ_INT:
                    r[f] = _bulk(pts, f, n, 1, int)
            else:
                pts = sp.points
                n = len(pts)
                r["co"] = _bulk(pts, "co", n, 4)
                r["weight"] = _bulk(pts, "weight", n)
            for f in _PT_1:
                r[f] = _bulk(pts, f, n)
            r["n"] = n
            rows.append(r)
        g = {"splines": rows}
    return g


def write_geometry(d, g) -> None:
    if not isinstance(g, dict):
        return
    _GEOM_CACHE.pop(d.session_uid, None)
    t = d.bl_rna.identifier
    try:
        if t in ("GreasePencil", "GreasePencilv3"):
            _write_gp(d, g)
            _GEOM_CACHE.pop(d.session_uid, None)
            return
        if t == "Curves":
            _write_hair(d, g)
            _GEOM_CACHE.pop(d.session_uid, None)
            return
        if t == "PointCloud":
            _write_points(d, g)
            _GEOM_CACHE.pop(d.session_uid, None)
            return
        if t == "Lattice":
            if len(d.points) * 3 == len(g.get("co_deform") or ()):
                d.points.foreach_set("co_deform", g["co_deform"])
                d.points.foreach_set("weight_softbody", g["weight_softbody"])
                d.update_tag()
        elif t == "MetaBall":
            want = g.get("elements") or []
            if [e.type for e in d.elements] != [w.get("type") for w in want]:
                d.elements.clear()
                for w in want:
                    d.elements.new(type=w.get("type", "BALL"))
            for e, w in zip(d.elements, want):
                set_struct_values(e, w)
        elif t in ("Curve", "SurfaceCurve"):
            want = g.get("splines") or []
            same = [(sp.type, len(sp.bezier_points) if sp.type == "BEZIER" else len(sp.points)) for sp in d.splines] \
                == [(w["values"].get("type"), w["n"]) for w in want]
            if not same:
                d.splines.clear()
                for w in want:
                    sp = d.splines.new(w["values"].get("type", "POLY"))
                    pts = sp.bezier_points if sp.type == "BEZIER" else sp.points
                    if w["n"] > len(pts):
                        pts.add(w["n"] - len(pts))
            for sp, w in zip(d.splines, want):
                set_struct_values(sp, {k: x for k, x in w["values"].items() if k != "type"})
                if sp.type == "BEZIER":
                    pts = sp.bezier_points
                    for f in _BEZ_INT:          # handle types first: setting one moves the handles
                        pts.foreach_set(f, w[f])
                    for f in _BEZ_3:
                        pts.foreach_set(f, w[f])
                else:
                    pts = sp.points
                    pts.foreach_set("co", w["co"])
                    pts.foreach_set("weight", w["weight"])
                for f in _PT_1:
                    pts.foreach_set(f, w[f])
            d.update_tag()
    except Exception as e:
        log.error_once(("geometry", str(e)), f"could not restore the shape of {d.name}: {e}", e)
