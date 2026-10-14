"""Independent oracle: snapshots of Blender state read WITHOUT any Hindsight code.

The gate (tools/gate_spec.py, item ORACLE) needs the tests to compare restored state through code
that shares nothing with the adapters' own capture, so a bug in an adapter can't hide itself. This
module imports only bpy, bmesh and numpy, and reads through different paths than hindsight/bl:
  - meshes: the legacy collections (vertices.co, edges.vertices, loops, polygons) plus every
    attribute through its own value property, shape keys, vertex group names; in Edit Mode through
    bmesh instead of the mesh;
  - everything else: a generic walk over RNA properties (editable ones, plus a few derived ones such
    as matrix_world), nested structs and collections, ID pointers by type and name.
Use it from headless tests (tests/) and real-UI scenarios (tests_ui/, via sys.path).
"""
import hashlib
import struct

import bpy
import numpy as np

# Selection and UI state that nothing in Hindsight promises to restore.
SKIP_ATTRS_PREFIX = (".select", ".uv_select", ".vs.", ".es.", ".pn.")
# The topology itself, which Blender lists among the attributes only after some operations (an
# Edit Mode round trip): read through the legacy collections instead (edges, loops), never skipped.
TOPOLOGY_ATTRS = {".edge_verts", ".corner_vert", ".corner_edge"}
_ATTR_PROP = {"FLOAT": ("value", np.float32, 1), "INT": ("value", np.int32, 1), "INT8": ("value", np.int8, 1),
              "BOOLEAN": ("value", np.bool_, 1), "FLOAT_VECTOR": ("vector", np.float32, 3),
              "FLOAT2": ("vector", np.float32, 2), "FLOAT_COLOR": ("color", np.float32, 4),
              "BYTE_COLOR": ("color_srgb", np.float32, 4), "INT32_2D": ("value", np.int32, 2),
              "QUATERNION": ("value", np.float32, 4), "FLOAT4X4": ("value", np.float32, 16),
              "INT16_2D": ("value", np.int16, 2)}


def _digest(arr) -> str:
    a = np.ascontiguousarray(arr)
    return f"{a.dtype.str}{list(a.shape)}:" + hashlib.blake2b(a.tobytes(), digest_size=16).hexdigest()


def _legacy(coll, prop, n, dtype):
    out = np.empty(n, dtype)
    if n:
        coll.foreach_get(prop, out)
    return out


def mesh_state(me) -> dict:
    """Every piece of mesh data, as digests of exact bytes (positions bit for bit)."""
    nv, ne, nf, nl = len(me.vertices), len(me.edges), len(me.polygons), len(me.loops)
    out = {"counts": (nv, ne, nf, nl),
           "co": _digest(_legacy(me.vertices, "co", nv * 3, np.float32)),
           "edges": _digest(_legacy(me.edges, "vertices", ne * 2, np.int32)),
           "loop_v": _digest(_legacy(me.loops, "vertex_index", nl, np.int32)),
           "loop_e": _digest(_legacy(me.loops, "edge_index", nl, np.int32)),
           "poly_start": _digest(_legacy(me.polygons, "loop_start", nf, np.int32)),
           "poly_total": _digest(_legacy(me.polygons, "loop_total", nf, np.int32))}
    for a in me.attributes:
        if a.name.startswith(SKIP_ATTRS_PREFIX) or a.name == "position" or a.name in TOPOLOGY_ATTRS:
            continue
        spec = _ATTR_PROP.get(a.data_type)
        key = f"attr:{a.name}|{a.domain}|{a.data_type}"
        if spec is None:
            out[key] = f"unreadable {a.data_type} x{len(a.data)}"
            continue
        prop, dt, w = spec
        out[key] = _digest(_legacy(a.data, prop, len(a.data) * w, dt))
    if me.shape_keys is not None:
        for kb in me.shape_keys.key_blocks:
            out[f"shape:{kb.name}"] = (_digest(_legacy(kb.data, "co", len(kb.data) * 3, np.float32)),
                                       repr(kb.value), kb.mute, kb.relative_key.name, kb.vertex_group)
    out["materials"] = tuple(m.name if m is not None else None for m in me.materials)
    return out


def canonical_mesh_state(me) -> dict:
    """The mesh up to element order where Blender itself doesn't keep one: Blender's own operators
    (Extrude, Fill, the UV sphere primitive) give edges, faces and corners in an order that depends
    on memory allocation history, so the same operations replayed don't match bit for bit. Positions
    and point values stay in order; edges and faces are compared as sets; other values as sorted
    lists. Used only by the replay oracle; restores are always compared bit for bit."""
    nv, ne, nf, nl = len(me.vertices), len(me.edges), len(me.polygons), len(me.loops)
    ev = _legacy(me.edges, "vertices", ne * 2, np.int32).reshape(-1, 2)
    ev.sort(axis=1)
    edges = ev[np.lexsort((ev[:, 1], ev[:, 0]))]
    lv = _legacy(me.loops, "vertex_index", nl, np.int32)
    starts = _legacy(me.polygons, "loop_start", nf, np.int32)
    totals = _legacy(me.polygons, "loop_total", nf, np.int32)
    faces = []
    for s, t in zip(starts.tolist(), totals.tolist()):
        cyc = lv[s:s + t].tolist()
        k = cyc.index(min(cyc))
        faces.append(tuple(cyc[k:] + cyc[:k]))
    out = {"counts": (nv, ne, nf, nl), "co": _digest(_legacy(me.vertices, "co", nv * 3, np.float32)),
           "edges": _digest(edges),
           "faces": hashlib.blake2b(repr(sorted(faces)).encode(), digest_size=16).hexdigest()}
    for a in me.attributes:
        if a.name.startswith(SKIP_ATTRS_PREFIX) or a.name == "position" or a.name.startswith("."):
            continue
        spec = _ATTR_PROP.get(a.data_type)
        if spec is None:
            continue
        prop, dt, w = spec
        vals = _legacy(a.data, prop, len(a.data) * w, dt)
        key = f"attr:{a.name}|{a.domain}|{a.data_type}"
        if a.domain == "POINT":
            out[key] = _digest(vals)
        else:
            rows = vals.reshape(len(a.data), w) if w > 1 else vals.reshape(-1, 1)
            out[key] = _digest(rows[np.lexsort(rows.T[::-1])] if len(rows) else rows)
    return out


def edit_mesh_state(ob) -> dict:
    """The same for an object in Edit Mode, read through bmesh (not the mesh datablock)."""
    import bmesh
    bm = bmesh.from_edit_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    co = np.array([c for v in bm.verts for c in v.co], np.float32)
    edges = np.array([v.index for e in bm.edges for v in e.verts], np.int32)
    faces = [tuple(v.index for v in f.verts) for f in bm.faces]
    out = {"counts": (len(bm.verts), len(bm.edges), len(bm.faces), sum(len(f) for f in faces)),
           "co": _digest(co), "edges": _digest(edges),
           "faces": hashlib.blake2b(repr(faces).encode(), digest_size=16).hexdigest(),
           "smooth": hashlib.blake2b(bytes(f.smooth for f in bm.faces), digest_size=16).hexdigest(),
           "mat": hashlib.blake2b(repr([f.material_index for f in bm.faces]).encode(), digest_size=16).hexdigest()}
    for kind, seq in (("v", bm.verts), ("e", bm.edges), ("f", bm.faces)):
        layers = seq.layers
        for lname in ("float", "int", "bool", "string", "float_vector", "float_color", "color"):
            coll = getattr(layers, lname, None)
            if coll is None:
                continue
            for name, layer in coll.items():
                if name.startswith(SKIP_ATTRS_PREFIX):
                    continue
                vals = [x[layer] for x in seq]
                out[f"layer:{kind}:{lname}:{name}"] = hashlib.blake2b(
                    repr([tuple(v) if hasattr(v, "__len__") and not isinstance(v, (str, bytes)) else v
                          for v in vals]).encode(), digest_size=16).hexdigest()
    return out


# ----------------------------------------------------------------------------- RNA walk
# Volatile or derived state that is not data the user made (selection, evaluation, counters).
SKIP_PROPS = {"rna_type", "select", "is_evaluated", "original", "users", "session_uid", "tag",
              "is_runtime_data", "is_missing", "is_library_indirect", "use_extra_user", "is_embedded_data",
              "id_type", "library_weak_reference", "preview", "bound_box", "dimensions", "is_editmode",
              "is_from_instancer", "is_from_set", "is_instancer", "mode", "active_material",
              "active_material_index", "active_shape_key", "active_shape_key_index", "is_active",
              "show_expanded", "is_override_data_editable", "is_override_data", "field", "collision",
              "soft_body", "particle_systems", "rigid_body", "rigid_body_constraint", "image_user",
              "motion_path", "animation_visualization", "pose", "shader_effects", "lightgroup",
              "is_shadow_catcher", "is_holdout", "light_linking", "id_data", "override_library",
              "library", "asset_data", "use_fake_user", "name_full", "is_valid", "error_location",
              "error_rotation", "persistent_uid", "execution_time", "is_override_data",
              "active",      # which constraint or modifier is highlighted in the panel: UI state
              "set_inverse_pending"}   # Child Of: a one-shot request consumed by evaluation, not state
DERIVED = {"matrix_world"}
# Object collections read on their own in object_state (by index and name, so a diff says which).
OBJECT_SKIP = SKIP_PROPS | {"constraints", "modifiers", "material_slots"}
UID_PROP = "_hindsight_uid"


def _norm(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return ("f", struct.pack("<d", v).hex())
    if isinstance(v, bpy.types.ID):
        return ("ID", type(v).__name__, v.name)
    if hasattr(v, "to_list") and hasattr(v, "col"):          # Matrix
        return tuple(tuple(_norm(float(x)) for x in row) for row in v)
    if isinstance(v, (set, frozenset)):
        return tuple(sorted(v))
    try:
        return tuple(_norm(x) for x in v)
    except TypeError:
        return repr(v)


def rna_dump(st, depth: int = 2, skip=SKIP_PROPS) -> dict:
    """Every editable property of an RNA struct (and DERIVED ones), recursing into nested structs
    and collections `depth` levels deep. ID pointers become (type, name)."""
    out = {}
    for prop in st.bl_rna.properties:
        ident = prop.identifier
        if ident in skip or ident.startswith("bl_"):
            continue
        try:
            v = getattr(st, ident)
        except Exception:
            continue
        if prop.type == "POINTER":
            if v is None or isinstance(v, bpy.types.ID):
                out[ident] = _norm(v)
            elif depth > 0:
                out[ident] = rna_dump(v, depth - 1, skip)
        elif prop.type == "COLLECTION":
            if depth > 0:
                out[ident] = tuple(_norm(x) if isinstance(x, bpy.types.ID) else
                                   tuple(sorted(rna_dump(x, depth - 1, skip).items())) for x in v)
        elif not prop.is_readonly or ident in DERIVED:
            out[ident] = _norm(v)
    return out


def id_props(idb) -> dict:
    """Custom properties (Geometry Nodes modifier inputs are stored this way). {} for a struct type
    that can't hold any."""
    try:
        keys = list(idb.keys())
    except TypeError:
        return {}
    return {k: _norm(idb[k].to_dict() if hasattr(idb[k], "to_dict") else
                     (idb[k].to_list() if hasattr(idb[k], "to_list") else idb[k]))
            for k in keys if k != UID_PROP}


def object_state(ob, mesh: bool = True, derived: bool = True) -> dict:
    """An object as the user made it: its RNA properties (transforms, parenting, visibility, display,
    ...), constraints and modifiers with all their settings and custom properties, the collections it
    is in, its view-layer hide state, material slots, and its mesh. In Edit Mode the edit mesh is
    written to the mesh first and read the same way. derived=False leaves out values that depend on
    other objects (matrix_world), for checks that move one object's history alone."""
    if derived:
        bpy.context.view_layer.update()       # derived matrices are evaluated values: evaluate first
    rna = rna_dump(ob, depth=1, skip=OBJECT_SKIP if derived else OBJECT_SKIP | DERIVED | {"matrix_local"})
    out = {"rna": rna, "custom": id_props(ob),
           "collections": tuple(sorted(c.name for c in ob.users_collection)),
           "constraints": {f"{i}:{c.name}": dict(rna_dump(c, 1), type=c.type) for i, c in enumerate(ob.constraints)},
           "modifiers": {f"{i}:{m.name}": dict(rna_dump(m, 1), type=m.type, custom=id_props(m))
                         for i, m in enumerate(ob.modifiers)},
           "slots": tuple((s.link, s.material.name if s.material else None) for s in ob.material_slots)}
    try:
        out["hide"] = ob.hide_get()
    except Exception:
        out["hide"] = None
    if mesh and ob.type == "MESH" and ob.data is not None:
        out["mesh"] = read_mesh(ob, mesh_state)
        out["vgroups"] = tuple(g.name for g in ob.vertex_groups)
    return out


def read_mesh(ob, reader):
    """reader(mesh) on the object's mesh. In Edit Mode mesh.attributes are the edit-mode layers (no
    readable values): the edit mesh is written back and a plain copy is read, so Edit and Object
    Mode readings compare. The mesh can be in Edit Mode through another object that shares it
    (a linked duplicate), so the mesh is asked, not the object."""
    me = ob.data
    if not me.is_editmode:
        return reader(me)
    editor = next((o for o in bpy.data.objects if o.data == me and o.mode == "EDIT"), ob)
    editor.update_from_editmode()
    tmp = me.copy()
    try:
        return reader(tmp)
    finally:
        bpy.data.meshes.remove(tmp)


def scene_state(scene=None) -> dict:
    """Which objects exist and how they hang together: names, types, data, parents, collections, the
    collection tree."""
    sc = scene or bpy.context.scene

    def tree(c):
        return (c.name, tuple(sorted(o.name for o in c.objects)), tuple(tree(ch) for ch in c.children))
    return {"objects": tuple(sorted((o.name, o.type, o.data.name if o.data else None,
                                     o.parent.name if o.parent else None) for o in sc.objects)),
            "tree": tree(sc.collection)}


def diff(a, b, path="") -> list:
    """Where two snapshots differ (paths), for failure messages."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'second' if k in b else 'first'}")
            else:
                out += diff(a[k], b[k], f"{path}/{k}")
    elif a != b:
        out.append(f"{path}: {str(a)[:80]} != {str(b)[:80]}")
    return out
