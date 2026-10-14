"""Tier 1 operations through real operators (bpy.ops), for the gate tests and Hypothesis.

Every Op runs one user operation on a deterministic scene (build_scene) and says which histories
should get exactly one step from it: object names, and "scene" for the scene history (objects added
or deleted). Selection is set first, as a user would by clicking; selecting is not a step.
Operators that need a real viewport (H, loop cut, knife) are in the real-UI scenarios instead.
"""
import math

import bpy

C = bpy.context
NAMES = ("A", "B", "C", "Pivot", "E")


def build_scene():
    """A cube, a cylinder, an icosphere, an empty and a grid (E, for Edit Mode), plus an empty
    collection "Props". (Not a UV
    sphere: Blender 5 builds its edges in a different order after each file reset, which a replay
    can't match.)"""
    bpy.ops.mesh.primitive_cube_add(location=(2, 0, 0))
    C.active_object.name = "A"
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, location=(0, 2, 0))
    C.active_object.name = "B"
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, location=(0, 0, 2))
    C.active_object.name = "C"
    bpy.ops.object.empty_add(location=(3, 3, 0))
    C.active_object.name = "Pivot"
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=2, location=(-3, 0, 0))
    C.active_object.name = "E"
    props = bpy.data.collections.new("Props")
    C.scene.collection.children.link(props)
    select()


def select(*names, active=None):
    if C.object is not None and C.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    obs = [bpy.data.objects[n] for n in names]
    for o in C.view_layer.objects:
        o.select_set(o in obs)
    C.view_layer.objects.active = bpy.data.objects[active] if active else (obs[0] if obs else None)


class Op:
    """unset_corners: Blender leaves the corner values (UVs) of the faces this operation makes unset,
    so they differ from run to run (seen without Hindsight); a replay can't compare them."""

    def __init__(self, label, fn, steps, edit=False, unset_corners=False):
        self.label, self.fn, self.steps, self.edit = label, fn, tuple(steps), edit
        self.unset_corners = unset_corners

    def run(self):
        r = self.fn()
        if isinstance(r, set) and "FINISHED" not in r:
            raise RuntimeError(f"{self.label}: {r}")

    def __repr__(self):
        return f"Op({self.label})"


def _sel_then(names, fn, active=None):
    def run():
        select(*names, active=active)
        return fn()
    return run


# ----------------------------------------------------------------------------- object operations
def translate(name, v):
    return Op(f"move {name} {v}", _sel_then([name], lambda: bpy.ops.transform.translate(value=v)), [name])


def translate_multi(names, v):
    return Op(f"move {'+'.join(names)}", _sel_then(names, lambda: bpy.ops.transform.translate(value=v)), names)


def rotate(name, angle, axis="Z"):
    return Op(f"rotate {name}", _sel_then([name], lambda: bpy.ops.transform.rotate(value=angle, orient_axis=axis)),
              [name])


def resize(name, s):
    return Op(f"scale {name}", _sel_then([name], lambda: bpy.ops.transform.resize(value=(s, s * 0.5, s))), [name])


def translate_snapped(name, v):
    return Op(f"snap-move {name}", _sel_then([name], lambda: bpy.ops.transform.translate(
        value=v, snap=True, snap_elements={'INCREMENT'})), [name])


def rotate_about_cursor(names, angle):
    def fn():
        ts = C.scene.tool_settings
        old = ts.transform_pivot_point
        ts.transform_pivot_point = "CURSOR"
        try:
            return bpy.ops.transform.rotate(value=angle, orient_axis="X")
        finally:
            ts.transform_pivot_point = old
    return Op(f"rotate {'+'.join(names)} about the cursor", _sel_then(names, fn), names)


def to_deltas(name):
    return Op(f"transforms to deltas {name}", _sel_then([name], lambda: bpy.ops.object.transforms_to_deltas(
        mode="ALL")), [name])


def apply_transform(name, what):
    kw = {"location": what == "location", "rotation": what == "rotation", "scale": what == "scale"}
    return Op(f"apply {what} {name}", _sel_then([name], lambda: bpy.ops.object.transform_apply(**kw)), [name])


def origin_to_geometry(name):
    return Op(f"origin to geometry {name}", _sel_then([name], lambda: bpy.ops.object.origin_set(
        type="ORIGIN_GEOMETRY")), [name])


def shade(name, smooth):
    fn = (lambda: bpy.ops.object.shade_smooth()) if smooth else (lambda: bpy.ops.object.shade_flat())
    return Op(f"shade {'smooth' if smooth else 'flat'} {name}", _sel_then([name], fn), [name])


def parent(child, par, keep):
    return Op(f"parent {child} to {par}", _sel_then([child, par], lambda: bpy.ops.object.parent_set(
        type="OBJECT", keep_transform=keep), active=par), [child])


def unparent(name, how):
    return Op(f"clear parent {name} ({how})", _sel_then([name], lambda: bpy.ops.object.parent_clear(type=how)),
              [name])


def add_constraint(name, ctype):
    return Op(f"add {ctype} to {name}", _sel_then([name], lambda: bpy.ops.object.constraint_add(type=ctype)),
              [name])


def delete_constraint(name, index=0):
    def fn():
        c = bpy.data.objects[name].constraints[index]
        return bpy.ops.constraint.delete(constraint=c.name, owner="OBJECT")
    return Op(f"delete constraint {index} of {name}", _sel_then([name], fn), [name])


def move_constraint(name, index, to):
    def fn():
        c = bpy.data.objects[name].constraints[index]
        return bpy.ops.constraint.move_to_index(constraint=c.name, owner="OBJECT", index=to)
    return Op(f"move constraint {index} of {name} to {to}", _sel_then([name], fn), [name])


def copy_constraint(name, index=0):
    def fn():
        c = bpy.data.objects[name].constraints[index]
        return bpy.ops.constraint.copy(constraint=c.name, owner="OBJECT")
    return Op(f"duplicate constraint {index} of {name}", _sel_then([name], fn), [name])


def move_to_collection(name, coll, new=False):
    def fn():
        if new:
            return bpy.ops.object.move_to_collection(collection_uid=C.scene.collection.session_uid, is_new=True,
                                                     new_collection_name=coll)
        return bpy.ops.object.move_to_collection(collection_uid=bpy.data.collections[coll].session_uid)
    return Op(f"move {name} to {coll}", _sel_then([name], fn), [name, "scene"] if new else [name])


def link_to_collection(name, coll):
    return Op(f"link {name} to {coll}", _sel_then([name], lambda: bpy.ops.object.link_to_collection(
        collection_uid=bpy.data.collections[coll].session_uid)), [name])


def remove_from_collection(name, coll):
    return Op(f"remove {name} from {coll}", _sel_then([name], lambda: bpy.ops.collection.objects_remove(
        collection=coll)), [name])


PRIMITIVES = ("cube", "uv_sphere", "ico_sphere", "cylinder", "cone", "torus", "plane", "circle", "grid", "monkey")


def add_primitive(kind, loc):
    def fn():
        select()
        if kind == "empty":
            return bpy.ops.object.empty_add(location=loc)
        return getattr(bpy.ops.mesh, f"primitive_{kind}_add")(location=loc)
    return Op(f"add {kind}", fn, ["scene"])


def delete(names):
    return Op(f"delete {'+'.join(names)}", _sel_then(names, lambda: bpy.ops.object.delete()), ["scene"])


def duplicate(name, linked=False):
    return Op(f"duplicate {name}{' linked' if linked else ''}", _sel_then(
        [name], lambda: bpy.ops.object.duplicate(linked=linked)), ["scene"])


# ----------------------------------------------------------------------------- Edit Mode
def edit(name, label, fn, seed=None, ratio=0.3):
    """One Edit Mode operation on `name` (enters Edit Mode first if needed; entering is not a step)."""
    def run():
        ob = bpy.data.objects[name]
        if C.view_layer.objects.active != ob or ob.mode != "EDIT":
            select(name)
            bpy.ops.object.mode_set(mode="EDIT")
        if seed is not None:
            bpy.ops.mesh.select_all(action="DESELECT")
            bpy.ops.mesh.select_random(ratio=ratio, seed=seed)
            _at_least_one_selected(ob)
        else:
            bpy.ops.mesh.select_all(action="SELECT")
        return fn()
    return Op(f"edit {name}: {label}", run, [name], edit=True)


def _at_least_one_selected(ob):
    import bmesh
    bm = bmesh.from_edit_mesh(ob.data)
    if not any(v.select for v in bm.verts) and bm.verts:
        bm.verts.ensure_lookup_table()
        bm.verts[len(bm.verts) // 2].select = True
        bm.select_flush(True)
        bmesh.update_edit_mesh(ob.data)


def edit_ops(name, seed):
    s = seed
    return [
        edit(name, "grab", lambda: bpy.ops.transform.translate(value=(0, 0, 0.25)), s),
        edit(name, "rotate", lambda: bpy.ops.transform.rotate(value=0.3, orient_axis="Z"), s + 1),
        edit(name, "scale", lambda: bpy.ops.transform.resize(value=(1.2, 1.2, 1.2)), s + 2),
        edit_select(name, "bridge the two outer rows", _select_outer_rows, lambda: bpy.ops.mesh.bridge_edge_loops()),
        edit(name, "extrude", lambda: bpy.ops.mesh.extrude_region_move(
            TRANSFORM_OT_translate={"value": (0, 0, 0.3)}), s + 3, 0.1),
        edit(name, "bevel", lambda: bpy.ops.mesh.bevel(offset=0.05, segments=2, affect="VERTICES"), s + 4, 0.1),
        edit_select(name, "inset", _select_faces(5), lambda: bpy.ops.mesh.inset(thickness=0.02), False),
        edit_select(name, "subdivide", _select_faces(7), lambda: bpy.ops.mesh.subdivide(number_cuts=1), False),
        edit_select(name, "merge", _select_verts(3), lambda: bpy.ops.mesh.merge(type="CENTER"), False),
        edit(name, "dissolve", lambda: bpy.ops.mesh.dissolve_verts(), s + 8, 0.05),
        edit(name, "delete", lambda: bpy.ops.mesh.delete(type="VERT"), s + 9, 0.05),
        edit_select(name, "fill the outline", _select_outline, lambda: bpy.ops.mesh.fill()),
        edit_select(name, "sort the edges", _select_all, lambda: bpy.ops.mesh.sort_elements(
            type="RANDOMIZE", elements={"EDGE"}, seed=s), False),
    ]


def _select_all(ob):
    bpy.ops.mesh.select_all(action="SELECT")


def edit_select(name, label, select_fn, fn, unset_corners=True):
    """An Edit Mode operation on a selection made by select_fn(ob) (deterministic). Fill and Bridge
    make faces whose UVs Blender leaves unset."""
    def run():
        ob = bpy.data.objects[name]
        if C.view_layer.objects.active != ob or ob.mode != "EDIT":
            select(name)
            bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        select_fn(ob)
        return fn()
    return Op(f"edit {name}: {label}", run, [name], edit=True, unset_corners=unset_corners)


def _select_faces(every):
    """Every n-th face in order of position (face indices aren't stable: Blender's operators make
    faces in an order that depends on memory allocation)."""
    def sel(ob):
        import bmesh
        bm = bmesh.from_edit_mesh(ob.data)
        faces = sorted(bm.faces, key=lambda f: tuple(round(c, 5) for c in f.calc_center_median()))
        for i, f in enumerate(faces):
            f.select = i % every == 1
        bm.select_flush(True)
        bmesh.update_edit_mesh(ob.data)
    return sel


def _select_verts(n):
    """n vertices spread out in order of position."""
    def sel(ob):
        import bmesh
        bm = bmesh.from_edit_mesh(ob.data)
        verts = sorted(bm.verts, key=lambda v: tuple(round(c, 5) for c in v.co))
        step = max(1, len(verts) // (n + 1))
        for k in range(1, n + 1):
            verts[min(k * step, len(verts) - 1)].select = True
        bm.select_flush(True)
        bmesh.update_edit_mesh(ob.data)
    return sel


def _select_outline(ob):
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.region_to_loop()


def _select_outer_rows(ob):
    import bmesh
    bm = bmesh.from_edit_mesh(ob.data)
    n = int(round(len(bm.verts) ** 0.5))          # the grid's first and last rows, by vertex index
    assert n * n == len(bm.verts), "bridge runs on the untouched grid topology"
    rows = set(range(n)) | set(range(n * n - n, n * n))
    for e in bm.edges:
        if all(v.index in rows for v in e.verts):
            e.select = True
    bm.select_flush(True)
    bmesh.update_edit_mesh(ob.data)


def leave_edit(name):
    """Leaving Edit Mode: never a step."""
    def run():
        if bpy.data.objects[name].mode == "EDIT":
            return bpy.ops.object.mode_set(mode="OBJECT")
        return {"FINISHED"}
    return Op(f"leave Edit Mode on {name}", run, [])


def select_only_in_edit(name, seed):
    """Changing the selection in Edit Mode: never a step."""
    def run():
        ob = bpy.data.objects[name]
        if ob.mode != "EDIT":
            select(name)
            bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        return bpy.ops.mesh.select_random(ratio=0.5, seed=seed)
    return Op(f"select in Edit Mode on {name}", run, [], edit=True)


# ----------------------------------------------------------------------------- sequences per row
def sequences() -> dict:
    """A fixed operation sequence for each Tier 1 row (COVERAGE id -> [Op])."""
    return {
        "obj.transforms": [translate("A", (1, 0.5, 0)), rotate("A", 0.7), resize("A", 1.5),
                           translate_snapped("B", (0.37, 0, 0.12)), translate_multi(["A", "B"], (0, 0, 1)),
                           rotate_about_cursor(["B", "C"], 0.4), to_deltas("C"), rotate("C", -1.1, "X"),
                           apply_transform("A", "scale"), apply_transform("B", "rotation"),
                           origin_to_geometry("C"), shade("A", True), translate("Pivot", (-1, 0, 0))],
        "obj.parenting": [parent("A", "Pivot", True), translate("Pivot", (0, 1, 0)), parent("B", "A", False),
                          rotate("A", 0.5), unparent("B", "CLEAR_KEEP_TRANSFORM"), unparent("A", "CLEAR_INVERSE"),
                          parent("C", "B", True), unparent("C", "CLEAR")],
        "obj.constraints": [add_constraint("A", "COPY_LOCATION"), add_constraint("A", "LIMIT_ROTATION"),
                            add_constraint("A", "TRACK_TO"), move_constraint("A", 2, 0), copy_constraint("A", 1),
                            delete_constraint("A", 0), add_constraint("B", "CHILD_OF"), delete_constraint("B", 0)],
        "obj.collections": [move_to_collection("A", "Props"), link_to_collection("B", "Props"),
                            move_to_collection("C", "Extra", new=True), remove_from_collection("B", "Props"),
                            link_to_collection("A", "Extra"), move_to_collection("A", "Props")],
        "obj.addremove": [add_primitive(k, (i * 2.5 - 10, -4, 0)) for i, k in enumerate(PRIMITIVES)]
                         + [add_primitive("empty", (0, -7, 0)), duplicate("A"), duplicate("B", linked=True),
                            delete(["C"]), parent("B", "Pivot", True), delete(["A", "Pivot", "B"])],
        "obj.editsteps": edit_ops("E", 11) + [select_only_in_edit("E", 3)] + [leave_edit("E")]
                         + [o for o in edit_ops("B", 41) if o.label.split(": ")[1] in ("grab", "rotate", "scale", "extrude")] + [leave_edit("B")],
    }
