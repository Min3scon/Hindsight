"""Tier 0 (sculpting) operations for the gate tests and Hypothesis.

Every Op runs one user action on a deterministic scene (build_scene) and names the objects whose
sculpt history should get exactly one step from it. Its prep switches to the mode the action needs
and runs first as a moment of its own: Blender can change the mesh when a mode is entered (entering
Vertex Paint adds a colour layer to a mesh without one), which Hindsight records as a step of its
own ("Changes Outside History") for an object that has a history. In background mode Blender has no sculpt
session, and every sculpt operator (brush strokes, mask fill, face sets, filters, hide) and Voxel
Remesh in Sculpt Mode crash it (probed on 5.1.2), so sculpt data changes here are data writes
through the harness, made in Sculpt Mode exactly where a brush writes them (the attributes Blender's
brushes use); the real operators run in the real-interface scenarios (s_round_c, s_t0_gate).
Vertex Paint operators, Edit Mode operators and Object Mode remeshing are real operators here.
"""
import bpy

import tests.harness as H
from hindsight.bl import runtime
from tests.t1_ops import Op, select

C = bpy.context
NAMES = ("Clay", "Rock")


def build_scene():
    """Two meshes to sculpt: an icosphere (2,562 vertices) and a subdivided grid (1,600). Not UV
    spheres: Blender 5 builds their edges in a different order after each file reset."""
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, location=(0, 0, 0))
    C.active_object.name = "Clay"
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=39, y_subdivisions=39, size=2, location=(3, 0, 0))
    C.active_object.name = "Rock"
    select()


def enter(name, mode):
    """Make name the active object in mode, as clicking it and picking the mode would."""
    ob = bpy.data.objects[name]
    vl = C.view_layer
    act = vl.objects.active
    if act is not None and act != ob and act.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
        _transition()
    if vl.objects.active != ob:
        for o in vl.objects:
            o.select_set(o == ob)
        vl.objects.active = ob
    if ob.mode != mode:
        bpy.ops.object.mode_set(mode=mode)
    _transition()
    return ob


def _transition():
    if H._registered[0]:
        runtime.check_mode_transition()


def _with_prep(op, name, mode):
    op.prep = lambda: enter(name, mode)
    return op


def stroke(name, seed, channel="position", n=200, mode="SCULPT"):
    """A brush stroke's worth of change to one channel (positions, mask, face sets, hiding, colour).
    Face sets start at 1 everywhere, as Blender makes them (Blender rewrites face set 0 whenever Sculpt
    Mode is entered, so a 0 never survives in real use)."""
    def fn():
        ob = enter(name, mode)
        if channel == "fset" and ".sculpt_face_set" not in ob.data.attributes:
            a = ob.data.attributes.new(".sculpt_face_set", "INT", "FACE")
            a.data.foreach_set("value", [1] * len(a.data))
        H.fake_stroke(ob, seed, n=n, channel=channel)
    return _with_prep(Op(f"{channel} stroke {seed} on {name}", fn, [name]), name, mode)


def mode(name, m):
    """Switch to a mode (no step; entering Vertex Paint makes Blender add a colour layer if the mesh
    has none, before anything is painted)."""
    return Op(f"{name} to {m}", lambda: enter(name, m), [])


def vp_op(name, which):
    """A Vertex Paint operator from the Paint menu."""
    ops = {"set": lambda: bpy.ops.paint.vertex_color_set(),
           "invert": lambda: bpy.ops.paint.vertex_color_invert(),
           "darken": lambda: bpy.ops.paint.vertex_color_brightness_contrast(brightness=-12, contrast=3),
           "hsv": lambda: bpy.ops.paint.vertex_color_hsv(h=0.6, s=1.1, v=0.9)}

    def fn():
        enter(name, "VERTEX_PAINT")
        return ops[which]()
    return _with_prep(Op(f"vertex paint {which} on {name}", fn, [name]), name, "VERTEX_PAINT")


def edit_visit(name, what, then="SCULPT"):
    """Edit Mode with one or more operators, then back to Sculpt Mode: one step for the visit."""
    actions = {
        "move": [lambda: bpy.ops.mesh.select_all(action="SELECT"),
                 lambda: bpy.ops.transform.translate(value=(0.05, 0, 0)),
                 lambda: bpy.ops.transform.translate(value=(0, 0.05, 0))],
        "smooth": [lambda: bpy.ops.mesh.select_all(action="SELECT"), lambda: bpy.ops.mesh.faces_shade_smooth()],
        "subdivide": [lambda: bpy.ops.mesh.select_all(action="DESELECT"),
                      lambda: _select_some(name), lambda: bpy.ops.mesh.subdivide()],
        "nothing": [],
    }

    def fn():
        enter(name, "EDIT")
        for a in actions[what]:
            r = a()
            if isinstance(r, set) and "FINISHED" not in r:
                raise RuntimeError(f"edit {what}: {r}")
        enter(name, then)
    return _with_prep(Op(f"edit mode {what} on {name}", fn, [] if what == "nothing" else [name]), name, "EDIT")


def _select_some(name):
    import bmesh
    ob = bpy.data.objects[name]
    bm = bmesh.from_edit_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces[: max(1, len(bm.faces) // 8)]:
        f.select_set(True)
    bmesh.update_edit_mesh(ob.data)
    return {"FINISHED"}


def remesh(name, kind="voxel"):
    """Remesh from Object Mode (Voxel Remesh in Sculpt Mode crashes in background mode), then back to
    sculpting: the new topology is one step."""
    def fn():
        ob = enter(name, "OBJECT")
        if kind == "voxel":
            ob.data.remesh_voxel_size = 0.08
            r = bpy.ops.object.voxel_remesh()
        else:
            r = bpy.ops.object.quadriflow_remesh(target_faces=600, seed=1)
        enter(name, "SCULPT")
        return r
    return _with_prep(Op(f"{kind} remesh {name}", fn, [name]), name, "OBJECT")


def join(active, others):
    """Ctrl+J in Object Mode (Hindsight's Join, which merges the histories; without Hindsight,
    Blender's). The active object's history gets the others' steps and one Join step; theirs go."""
    def fn():
        select(active, *others, active=active)
        if H._registered[0]:
            return bpy.ops.hindsight.join("EXEC_DEFAULT")
        return bpy.ops.object.join()

    def steps_check(before, after):
        want = before.get(active, 0) + sum(before.get(o, 0) for o in others) + 1
        assert after.get(active) == want, f"join: {active} has {after.get(active)} steps, want {want}"
        assert not any(o in after for o in others), f"join: {others} still there"
    op = _with_prep(Op(f"join {', '.join(others)} into {active}", fn, [active]), active, "OBJECT")
    op.steps_check = steps_check
    return op


def separate(name, then="SCULPT"):
    """P > Selection in Edit Mode: one Edit Mode step on the original; the new object has no history."""
    def fn():
        enter(name, "EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        _select_some(name)
        r = bpy.ops.mesh.separate(type="SELECTED")
        enter(name, then)
        return r
    return _with_prep(Op(f"separate part of {name}", fn, [name]), name, "EDIT")


def duplicate(name):
    """Shift+D (left where it is): the copy has no history; the original's stays its own."""
    def fn():
        select(name)
        return bpy.ops.object.duplicate()
    return _with_prep(Op(f"duplicate {name}", fn, []), name, "OBJECT")


def sequences() -> dict:
    """Per row, a sequence that exercises it (and switches objects and modes in between)."""
    return {
        "sculpt.positions": [stroke("Clay", 1), stroke("Clay", 2, n=2000), stroke("Rock", 3),
                             stroke("Clay", 4, n=5), stroke("Rock", 5), stroke("Clay", 6)],
        "sculpt.masks": [stroke("Clay", 1), stroke("Clay", 2, "mask"), stroke("Clay", 3, "fset"),
                         stroke("Clay", 4, "hide"), stroke("Rock", 5, "mask"), stroke("Clay", 6, "mask"),
                         stroke("Clay", 7, "hide"), stroke("Clay", 8)],
        "sculpt.colors": [stroke("Clay", 1, "color"), vp_op("Clay", "set"), vp_op("Clay", "invert"),
                          mode("Rock", "VERTEX_PAINT"), stroke("Rock", 2, "bytecolor", mode="VERTEX_PAINT"), vp_op("Clay", "hsv"),
                          stroke("Clay", 3, "color"), vp_op("Rock", "darken")],
        "sculpt.topology": [stroke("Clay", 1), edit_visit("Clay", "subdivide"), stroke("Clay", 2),
                            remesh("Clay"), stroke("Clay", 3), stroke("Rock", 4), remesh("Rock", "quadriflow"),
                            stroke("Rock", 5)],
        "sculpt.joinsep": [stroke("Clay", 1), stroke("Rock", 2, "mask"), stroke("Clay", 3), join("Clay", ["Rock"]),
                           stroke("Clay", 4), separate("Clay"), stroke("Clay.001", 5), duplicate("Clay"),
                           stroke("Clay.002", 6), stroke("Clay", 7)],
        "sculpt.editvisit": [stroke("Clay", 1), edit_visit("Clay", "move"), edit_visit("Clay", "nothing"),
                             stroke("Clay", 2), edit_visit("Clay", "smooth"), stroke("Rock", 3),
                             edit_visit("Rock", "move", then="OBJECT"), stroke("Rock", 4)],
    }
