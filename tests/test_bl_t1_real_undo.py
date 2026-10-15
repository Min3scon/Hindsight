"""Tier 1 with Blender's real undo and redo (bpy.ops.ed.undo / redo), headless.

Every operation is pushed onto Blender's undo stack the way the interface does it (the operator,
Blender's undo push, then Hindsight's timer). After every Ctrl+Z and Ctrl+Shift+Z, Blender's data is
that entry's, every history's cursor is the one that data belongs to, and nothing is recorded.

D42: ids Hindsight gives out after Blender's undo push (added objects, an object switched on, a new
collection, a duplicate's own id) were missing after Blender's undo or redo, cutting the datablocks off
from their histories.

Run: blender -b --factory-startup --python tests/test_bl_t1_real_undo.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bpy

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers
from tests.t1_real_undo import RealUndo
from hindsight.core import globaltl
from hindsight.bl import datarec, idmap, runtime

C = bpy.context
ROWS = ("obj.transforms", "obj.parenting", "obj.constraints", "obj.collections", "obj.addremove", "obj.editsteps")


def setup(track=True):
    L.fresh_scene()
    if track:
        L.track_all()
    else:
        datarec.FORCE_SCOPE[0] = "EVERYTHING"
    runtime.reg().preserve_modes = False


def glen():
    return globaltl.global_length(runtime.reg(), runtime._visible_uids())


def gpos():
    return globaltl.global_position(runtime.reg(), runtime._visible_uids())


def undo_ok(u, direction):
    label, problems = u.move(direction)
    assert not problems, f"{direction} to '{label}': {problems}"


@covers("obj.addremove", "UNDO")
def test_an_added_object_keeps_its_id():
    setup()
    u = RealUndo()
    try:
        u.op("add cube", lambda: T.add_primitive("cube", (0, -5, 0)).run())
        cu = idmap.uid_of(bpy.data.objects["Cube"])
        u.op("move cube", lambda: T.translate("Cube", (0, 0, 1)).run())
        undo_ok(u, "undo")
        assert bpy.data.objects["Cube"].get(idmap.UID_PROP) == cu, "the cube lost its id"
        u.op("rotate cube", lambda: T.rotate("Cube", 0.6).run())
        h = runtime.reg().history_for(cu)
        assert h is not None and [e.meta.get("label") for e in h.entries] == ["rotate cube"], h
        end = L.snap()
        runtime.global_goto(C, 0, True)
        assert "Cube" not in C.scene.objects
        runtime.global_goto(C, glen(), True)
        assert L.snap() == end, "the global timeline gives back the scene as it was"
    finally:
        u.close()


@covers("obj.transforms", "UNDO")
def test_an_object_switched_on_keeps_its_id():
    setup(track=False)
    u = RealUndo()
    try:
        u.op("move B", lambda: T.translate("B", (0, 1, 0)).run())
        T.select("A")
        assert bpy.ops.hindsight.record_everything(on=True) == {"FINISHED"}   # no undo push of its own
        ua = idmap.uid_of(bpy.data.objects["A"])
        u.op("move A", lambda: T.translate("A", (1, 0, 0)).run())
        u.op("move A again", lambda: T.translate("A", (1, 0, 0)).run())
        undo_ok(u, "undo")
        undo_ok(u, "undo")
        a = bpy.data.objects["A"]
        assert a.get(idmap.UID_PROP) == ua and datarec.is_tracked(a), "A lost its id or its recording"
        u.op("rotate A", lambda: T.rotate("A", 0.4).run())
        assert len(runtime.reg().history_for(ua).entries) == 1
    finally:
        u.close()


@covers("obj.collections", "UNDO")
@covers("obj.addremove", "UNDO")
def test_new_collection_and_duplicate_keep_their_ids():
    setup()
    u = RealUndo()
    try:
        u.op("new collection", lambda: T.move_to_collection("Pivot", "NewColl", new=True).run())
        ku = idmap.uid_of(bpy.data.collections["NewColl"])
        u.op("duplicate A", lambda: T.duplicate("A").run())
        du = idmap.uid_of(bpy.data.objects["A.001"])
        u.op("move the copy", lambda: T.translate("A.001", (0, 3, 0)).run())
        for d in ("undo", "undo", "undo", "redo", "redo", "redo"):
            undo_ok(u, d)
        assert bpy.data.collections["NewColl"].get(idmap.UID_PROP) == ku
        assert bpy.data.objects["A.001"].get(idmap.UID_PROP) == du, "the copy got the original's id back"
    finally:
        u.close()


@covers("obj.editsteps", "UNDO.MODES")
def test_a_jump_made_in_edit_mode_is_undone():
    """D53: a jump on the grid's own history made in Edit Mode, then Blender's real Ctrl+Z and
    Ctrl+Shift+Z: the jump is undone and redone, and Hindsight never reverses Blender's undo."""
    setup()
    put_back = []
    orig = runtime._put_back_after_native_undo
    runtime._put_back_after_native_undo = lambda *a, **k: (put_back.append(a), orig(*a, **k))
    u = RealUndo()
    try:
        u.op("move A", lambda: T.translate("A", (1, 0, 0)).run())

        def tab_in():
            T.select("E")
            bpy.ops.object.mode_set(mode="EDIT")
            runtime.check_mode_transition()
        u.op("Tab", tab_in)
        for o in T.edit_ops("E", 5)[:3]:
            u.op(o.label, o.run)
        h = runtime.history_of(bpy.data.objects["E"])
        runtime.goto(C, h.cursor - 2, final=True)               # E's own slider, in Edit Mode
        assert bpy.data.objects["E"].mode == "EDIT"
        for d in ("undo", "redo", "undo", "undo", "undo"):
            undo_ok(u, d)
        assert not put_back, "Hindsight reversed Blender's undo of its own jump"
    finally:
        runtime._put_back_after_native_undo = orig
        u.close()


@covers(ROWS, "UNDO")
def test_real_undo_and_redo_for_every_row():
    """Each row's operations, then Blender's undo six times and redo six times, then a jump on the
    global timeline undone and redone, then two more undos and a new operation."""
    for row, ops in T.sequences().items():
        setup()
        u = RealUndo()
        try:
            for op in ops:
                u.op(op.label, op.run)
            T.select()
            k = min(6, len(ops))
            for d in ["undo"] * k + ["redo"] * k:
                label, problems = u.move(d)
                assert not problems, f"{row}: {d} to '{label}': {problems}"
            g = gpos()
            if g >= 3:
                runtime.global_goto(C, g - 3, True)
                for d in ("undo", "redo", "undo", "undo", "undo"):
                    label, problems = u.move(d)
                    assert not problems, f"{row}, after a jump: {d} to '{label}': {problems}"
            pivot = next(o for o in C.scene.objects if o.type == "EMPTY")
            u.op("move Pivot", lambda: T.translate(pivot.name, (0, 0, 2)).run())
            h = runtime.history_of(pivot)
            assert h is not None and h.cursor == len(h.entries) and                 h.entries[-1].meta.get("label") == "move Pivot", f"{row}: the new operation after undo wasn't recorded"
        finally:
            u.close()


for fn in (test_an_added_object_keeps_its_id, test_an_object_switched_on_keeps_its_id, test_a_jump_made_in_edit_mode_is_undone,
           test_new_collection_and_duplicate_keep_their_ids, test_real_undo_and_redo_for_every_row):
    H.run(fn)
H.finish()
