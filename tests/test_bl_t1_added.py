"""Tier 1: objects that come and go while the scene is recorded, headless.

D36: an object added or duplicated while the scene is recorded is recorded fully from the start.
Before, only its adding was recorded: scrubbing the global history back past it and forward again
brought it back as it was when added, and every later change to it was lost.
D37: an object's own history can't bring back an object it referred to (its parent, a constraint
target) that is deleted now. That reference stays empty, everything else is restored exactly, and
the sidebar says which object it was and that the global history brings it back.

Run: blender -b --factory-startup --python tests/test_bl_t1_added.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers
from hindsight.core import globaltl
from hindsight.bl import datarec, idmap, runtime
from hindsight.bl import warnings as W

C = bpy.context


def setup():
    L.fresh_scene()
    L.track_all()


def constraint_with_target(name, ctype, target):
    """Shift+Ctrl+C with the target selected too (Add Constraint (with Targets))."""
    return T.Op(f"add {ctype} to {name} targeting {target}", T._sel_then(
        [name, target], lambda: bpy.ops.object.constraint_add_with_targets(type=ctype), active=name), [name])


def glen():
    return globaltl.global_length(runtime.reg(), runtime._visible_uids())


def everything():
    return {ob.name: O.object_state(ob, derived=False) for ob in C.scene.objects}


@covers("obj.addremove", "EXACT", "STEPS")
def test_added_object_is_recorded_fully():
    setup()
    T.add_primitive("cube", (0, -5, 0)).run()
    L.tick_like("add a cube")
    cube = C.active_object
    assert datarec.is_tracked(cube), "an object added while the scene is recorded is recorded"
    assert not any(t == W.TXT_EV_NOT_ON for _s, t in W.everything_notes(C, cube))
    T.translate(cube.name, (2, 0, 0)).run()
    L.tick_like("move the cube")
    h = runtime.reg().history_for(idmap.uid_of(cube))
    assert h is not None and len(h.entries) == 1, "moving it is a step in its own history"
    end = everything()
    runtime.global_goto(C, 0, True)
    assert "Cube" not in C.scene.objects
    runtime.global_goto(C, glen(), True)
    assert everything() == end, O.diff(end, everything())[:4]


@covers("obj.addremove", "EXACT")
@covers("obj.parenting", "EXACT")
def test_duplicate_of_a_child_keeps_its_changes():
    """The shrunk Hypothesis case: Shift+D of a child, then Set Origin on its parent (Blender moves
    the children to compensate). Every global position is exact."""
    setup()
    ops = [T.parent("Pivot", "B", False), T.add_constraint("A", "COPY_LOCATION"), T.duplicate("Pivot", False),
           T.edit_ops("E", 0)[0], T.rotate("A", 0.0, "X"), T.origin_to_geometry("B")]
    r = L.Run(ops)
    L.check_global(r, full=True)
    L.check_object_histories(r, full=True)


@covers("obj.parenting", "WARN", "EXACT")
def test_reference_to_a_deleted_object():
    setup()
    ops = [T.parent("B", "A", False), constraint_with_target("C", "COPY_LOCATION", "A"), T.delete(["A"])]
    r = L.Run(ops)
    b, c = bpy.data.objects["B"], bpy.data.objects["C"]
    hb = runtime.reg().history_for(idmap.uid_of(b))
    runtime._goto_history(hb, 1, True)                     # B parented to A, but A is deleted now
    assert b.parent is None
    notes = " ".join(t for _s, t in W.everything_notes(C, b))
    assert "refers to A" in notes and "global history" in notes, notes
    hc = runtime.reg().history_for(idmap.uid_of(c))
    runtime._goto_history(hc, len(hc.entries) - 1, True)   # the constraint as it was, targeting A
    assert "refers to A" in " ".join(t for _s, t in W.everything_notes(C, c))
    # the global history brings A back: the references are set and the notes go
    runtime.global_goto(C, 2, True)
    assert b.parent == bpy.data.objects["A"], b.parent
    assert c.constraints[0].target == bpy.data.objects["A"]
    assert "refers to" not in " ".join(t for _s, t in W.everything_notes(C, b))
    assert "refers to" not in " ".join(t for _s, t in W.everything_notes(C, c))
    # and the per-object check of the gate accepts exactly this, nothing more
    runtime.global_goto(C, glen(), True)
    L.check_object_histories(r, full=True)


@covers("obj.parenting", "EXACT")
def test_restoring_a_parent_keeps_its_inverse():
    """D38: Blender resets the parent inverse whenever the parent is set. Deleting a parent changes
    only the child's parent (Blender keeps its inverse); going back over the deletion must give the
    recorded inverse again."""
    r = L.Run([T.parent("B", "A", False), T.delete(["A"])])
    b = bpy.data.objects["B"]
    kept = [tuple(row) for row in b.matrix_parent_inverse]      # the deletion kept the inverse
    assert b.parent is None and kept != [tuple(row) for row in __import__("mathutils").Matrix.Identity(4)]
    runtime.global_goto(C, 1, True)                       # back over the deletion: A comes back
    assert b.parent == bpy.data.objects["A"]
    assert [tuple(row) for row in b.matrix_parent_inverse] == kept, "the inverse is the recorded one"
    runtime.global_goto(C, glen(), True)
    L.check_global(r, full=True)
    L.check_object_histories(r, full=True)


@covers("obj.editsteps", "STEPS", "EXACT")
def test_first_edit_visit_is_a_step():
    """D50: with Edit Mode step by step off, or on a mesh over the step-by-step limit, an Edit Mode
    visit on a recorded object with no history yet is one step (it used to be lost)."""
    from tests import oracle as O
    for how in ("edit steps off", "big mesh"):
        L.fresh_scene()
        L.track_all()
        orig_max = runtime.EDIT_STEPS_MAX
        if how == "edit steps off":
            from hindsight.bl import adapters
            datarec.FORCE_KINDS[0] = set(adapters.KINDS) - {"edit_steps"}
        else:
            runtime.EDIT_STEPS_MAX = 10                 # stands in for a mesh over 300,000 vertices
        try:
            a = bpy.data.objects["A"]
            before = O.read_mesh(a, O.mesh_state)
            T.select("A")
            bpy.ops.object.mode_set(mode="EDIT")
            runtime.check_mode_transition()
            L.tick_like("Toggle Edit Mode")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.transform.translate(value=(0, 0, 0.5))
            L.tick_like("Move")
            bpy.ops.object.mode_set(mode="OBJECT")
            runtime.check_mode_transition()
            L.tick_like("Toggle Edit Mode")
            after = O.read_mesh(a, O.mesh_state)
            h = runtime.history_of(a)
            assert h is not None and len(h.entries) == 1, f"{how}: {None if h is None else len(h.entries)}"
            runtime._goto_history(h, 0, True)
            assert O.read_mesh(a, O.mesh_state) == before, how
            runtime._goto_history(h, 1, True)
            assert O.read_mesh(a, O.mesh_state) == after, how
        finally:
            runtime.EDIT_STEPS_MAX = orig_max
            datarec.FORCE_KINDS[0] = None


@covers("obj.editsteps", "EXACT")
def test_dragging_while_a_linked_duplicate_is_in_edit_mode():
    """D51: the global slider dragged (and released) while a linked duplicate of a recorded mesh is
    in Edit Mode: the shared mesh is written (Edit Mode is left and entered again around it), no
    error in the middle of the jump, cursors and mesh agree."""
    from tests import oracle as O
    L.fresh_scene()
    L.track_all()
    runtime.reg().preserve_modes = False
    start = O.read_mesh(bpy.data.objects["A"], O.mesh_state)
    for o in [T.duplicate("A", linked=True), T.edit("A", "grab", lambda: bpy.ops.transform.translate(value=(0, 0, 0.5))),
              T.leave_edit("A"), T.translate("B", (0, 1, 0))]:
        o.run()
        L.tick_like(o.label)
    T.select("A.001")
    bpy.ops.object.mode_set(mode="EDIT")
    runtime.check_mode_transition()
    L.tick_like("Toggle Edit Mode")
    runtime.global_goto(C, 2, final=False)             # dragging (position 1: the duplicate, not the grab)
    runtime.global_goto(C, 1, final=False)
    runtime.global_goto(C, 1, final=True)              # released
    a = bpy.data.objects["A"]
    assert bpy.data.objects["A.001"].mode == "EDIT", "Edit Mode is kept"
    assert O.read_mesh(a, O.mesh_state) == start, "before the grab the shared mesh is as at the start"
    runtime.global_goto(C, glen(), final=True)
    h = runtime.history_of(a)
    assert h.cursor == len(h.entries)


def mesh_steps(ob) -> int:
    h = runtime.reg().history_for(idmap.uid_of(ob))
    return 0 if h is None else sum(1 for e in h.entries if e.meta.get("kind") != "DATA")


@covers("obj.addremove", "EXACT", "STEPS", "WARN")
@covers("obj.editsteps", "EXACT", "STEPS")
def test_linked_duplicates_record_their_mesh_once():
    """D39: Alt+D, then Edit Mode through the original and through the duplicate. The shared mesh is
    recorded once, in the original's history (its owner); the duplicate records its own object data
    and says where its mesh is recorded. Every history is exact, nothing is read in the middle of an
    Edit Mode visit, and the global timeline is exact at every operation."""
    r = L.Run([T.duplicate("B", True), T.edit_ops("B", 0)[0], T.translate("B.001", (0, 2, 0))])
    T.select()
    L.tick_like("leave Edit Mode")
    b, b1 = bpy.data.objects["B"], bpy.data.objects["B.001"]
    assert b1.data == b.data and datarec.is_tracked(b1)
    assert mesh_steps(b1) == 0, "the duplicate recorded the shared mesh too"
    notes = " ".join(t for _s, t in W.everything_notes(C, b1))
    assert "Shares its mesh with B" in notes, notes
    n = mesh_steps(b)
    r.add(T.edit_ops("B.001", 5)[0])                       # Edit Mode through the duplicate
    T.select()
    L.tick_like("leave Edit Mode")
    assert mesh_steps(b1) == 0 and mesh_steps(b) == n + 1, (mesh_steps(b1), mesh_steps(b), n)
    r.states[-1] = L.snap()                                # the visit is one step, taken on leaving
    r.lens[-1] = L.lengths()
    r.seqs[-1] = L.max_seq()
    L.check_global(r, full=True)
    L.check_object_histories(r, full=True)


for fn in (test_added_object_is_recorded_fully, test_duplicate_of_a_child_keeps_its_changes,
           test_reference_to_a_deleted_object, test_restoring_a_parent_keeps_its_inverse,
           test_linked_duplicates_record_their_mesh_once, test_first_edit_visit_is_a_step,
           test_dragging_while_a_linked_duplicate_is_in_edit_mode):
    H.run(fn)
H.finish()
