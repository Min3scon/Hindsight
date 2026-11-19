"""Tier 1 gate, warnings (gate item WARN), headless: everything Record everything leaves out of a Tier
1 row is said in the sidebar's Warnings panel when it applies to the selected object, and not when it
doesn't. The texts are the limits listed in COVERAGE.md.

Run: blender -b --factory-startup --python tests/test_bl_t1_warn.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile
from types import SimpleNamespace

import bpy

import tests.harness as H
from tests import t1_ops as T
from tests import t1_gate_lib as L
from tests.gate import covers
from hindsight.bl import datarec, runtime, ui_panel
from hindsight.bl import warnings as W

C = bpy.context


class FakeLayout:
    """Accepts any UILayout call and keeps the label texts."""

    def __init__(self, log=None):
        self.__dict__["log"] = log if log is not None else []

    def __setattr__(self, k, v):
        self.__dict__[k] = v

    def __getattr__(self, name):
        def fn(*args, **kw):
            self.log.append((name, args, kw))
            return FakeLayout(self.log)
        return fn


def sidebar_text(name):
    """What the Warnings panel shows for the object (the panel's own draw code, wrapped lines joined)."""
    T.select(name)
    if not ui_panel.VIEW3D_PT_hindsight_warnings.poll(C):
        return ""
    lay = FakeLayout()
    ui_panel.VIEW3D_PT_hindsight_warnings.draw(SimpleNamespace(layout=lay), C)
    return " ".join(kw.get("text", "") for n, a, kw in lay.log if n == "label")


def shows(name, text):
    """True if the panel shows `text` (a template's fixed start) for object `name`."""
    words = text.split("{")[0].split()
    flat = " ".join(sidebar_text(name).split())
    return " ".join(words) in flat


def _commit():
    """What the timer does after an operator (headless runs have no timers)."""
    C.view_layer.update()
    datarec.refresh_mesh_users()
    datarec.commit(label="Op", uids=datarec.tracked_uids() | set(datarec._DIRTY), mesh=True)


def setup(scene_on=True):
    H.fresh()
    T.build_scene()
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    for n in T.NAMES:
        datarec.set_tracked(bpy.data.objects[n], True)
    if scene_on:
        datarec.set_tracked(C.scene, True)


@covers(["obj.transforms", "obj.constraints"], "WARN")
def test_animated_values():
    setup()
    a = bpy.data.objects["A"]
    assert not shows("A", W.TXT_EV_ANIMATED)
    a.keyframe_insert("location", frame=1)
    assert shows("A", W.TXT_EV_ANIMATED) and "location" in sidebar_text("A")
    c = bpy.data.objects["B"].constraints.new("COPY_LOCATION")
    d = c.driver_add("influence")
    d.driver.expression = "0.5"
    assert shows("B", W.TXT_EV_ANIMATED) and "influence" in sidebar_text("B")
    assert not shows("C", W.TXT_EV_ANIMATED)


@covers("obj.constraints", "WARN")
def test_lists_in_constraint_settings():
    setup()
    c = bpy.data.objects["A"].constraints.new("ARMATURE")
    assert not shows("A", W.TXT_EV_LISTS), "no targets yet: nothing left out"
    c.targets.new()
    assert shows("A", W.TXT_EV_LISTS)
    bpy.data.objects["B"].constraints.new("COPY_LOCATION")
    assert not shows("B", W.TXT_EV_LISTS)


@covers("obj.parenting", "WARN")
def test_parenting_with_deform():
    """Ctrl+P > Lattice Deform adds a Lattice modifier to the child. D110: it is recorded with the
    parenting (going back takes it off again), so the note that said it wasn't is gone."""
    setup()
    bpy.ops.object.add(type="LATTICE", location=(0, 4, 0))
    lat = C.active_object
    datarec.set_tracked(lat, True)
    T.select("A", "B", lat.name, active=lat.name)
    bpy.ops.object.parent_set(type="OBJECT")
    assert not shows("A", W.TXT_EV_PARENT_DEFORM), "a plain parent adds nothing"
    L.tick_like("parent")
    T.select("A", lat.name, active=lat.name)
    bpy.ops.object.parent_set(type="LATTICE")
    L.tick_like("parent lattice")
    a = bpy.data.objects["A"]
    assert any(m.type == "LATTICE" for m in a.modifiers), [m.type for m in a.modifiers]
    assert not shows("A", W.TXT_EV_PARENT_DEFORM)
    h = runtime.history_of(a)
    runtime._goto_history(h, h.cursor - 1, True)
    assert not any(m.type == "LATTICE" for m in a.modifiers), [m.type for m in a.modifiers]
    runtime._goto_history(h, h.cursor + 1, True)
    assert any(m.type == "LATTICE" for m in a.modifiers), [m.type for m in a.modifiers]


@covers("obj.addremove", "WARN")
def test_physics_and_particles_note():
    """D49: physics and particle systems don't come back with a deleted object: said on such objects."""
    setup()
    a = bpy.data.objects["A"]
    assert not shows("A", W.TXT_EV_PHYSICS)
    a.modifiers.new("Cloth", "CLOTH")
    assert shows("A", W.TXT_EV_PHYSICS)
    assert not shows("B", W.TXT_EV_PHYSICS)


@covers(["obj.addremove", "obj.transforms"], "WARN")
def test_objects_not_switched_on_and_other_types():
    setup()
    T.add_primitive("cube", (0, -5, 0)).run()
    _commit()
    assert datarec.is_tracked(bpy.data.objects["Cube"]), "D36: added while the scene is recorded: recorded fully"
    assert not shows("Cube", W.TXT_EV_NOT_ON)
    datarec.set_tracked(bpy.data.objects["B"], False)
    assert shows("B", W.TXT_EV_NOT_ON), "switched off: only adding and deleting it is recorded"
    assert not shows("A", W.TXT_EV_NOT_ON)
    bpy.ops.object.armature_add()
    _commit()
    rig = C.active_object.name
    assert shows(rig, W.TXT_EV_COMEBACK) and "armature" in sidebar_text(rig)
    bpy.ops.object.light_add(type="POINT")
    _commit()
    light = C.active_object.name
    assert not shows(light, W.TXT_EV_COMEBACK), "D122: a light comes back even when its data is gone"
    assert not shows("A", W.TXT_EV_COMEBACK) and not shows("Pivot", W.TXT_EV_COMEBACK)
    setup(scene_on=False)
    T.add_primitive("cube", (0, -5, 0)).run()
    assert not shows("Cube", W.TXT_EV_NOT_ON), "nothing about this object is recorded: nothing to say"


@covers("obj.editsteps", "WARN")
def test_big_mesh_edit_mode():
    setup()
    big = H.make_mesh(runtime.SETTINGS.edit_steps_max + 20_000, name="Big")
    datarec.set_tracked(big, True)
    assert shows("Big", W.TXT_EV_EDIT_BIG)
    assert not shows("A", W.TXT_EV_EDIT_BIG)


@covers("obj.collections", "WARN")
def test_new_collections_need_the_scene():
    setup(scene_on=False)
    assert shows("A", W.TXT_EV_NEW_COLLECTIONS)
    setup(scene_on=True)
    assert not shows("A", W.TXT_EV_NEW_COLLECTIONS)


@covers(["obj.transforms", "obj.addremove"], "WARN")
def test_linked_objects():
    setup()
    path = os.path.join(tempfile.mkdtemp(prefix="hs_warn_"), "lib.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    H.fresh()
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    bpy.ops.wm.link(filepath=os.path.join(path, "Object", "A"), directory=os.path.join(path, "Object"), filename="A")
    assert shows("A", W.TXT_EV_LINKED)


@covers("obj.transforms", "WARN")
def test_recorded_data_not_called_unrecorded():
    """D113: modifiers, vertex groups, shape keys and materials are recorded on an object Record
    everything is on for (D108, D110 to D112): the notes saying they aren't are not shown there."""
    setup()
    a = bpy.data.objects["A"]
    a.modifiers.new("Bevel", "BEVEL")
    a.vertex_groups.new(name="G")
    a.shape_key_add(name="Basis")
    a.data.materials.append(bpy.data.materials.new("M"))
    gone = (W.TXT_MODIFIERS, W.TXT_VGROUPS, W.TXT_SHAPE_KEYS, W.TXT_MATERIALS)
    flat = " ".join(sidebar_text("A").split())
    for t in gone:
        assert t not in flat, t
    datarec.set_tracked(a, False)
    flat = " ".join(sidebar_text("A").split())
    for t in gone:
        assert t in flat, t
    datarec.set_tracked(a, True)
    datarec.FORCE_SCOPE[0] = "SCULPT"
    flat = " ".join(sidebar_text("A").split())
    for t in gone:
        assert t in flat, t


@covers(["obj.transforms", "obj.constraints", "obj.addremove", "obj.editsteps", "obj.collections"], "WARN")
def test_nothing_said_with_sculpting_only():
    setup()
    bpy.data.objects["A"].keyframe_insert("location", frame=1)
    datarec.FORCE_SCOPE[0] = "SCULPT"
    text = sidebar_text("A")
    for t in (W.TXT_EV_ANIMATED, W.TXT_EV_NOT_ON, W.TXT_EV_NEW_COLLECTIONS, W.TXT_EV_LISTS):
        assert " ".join(t.split("{")[0].split()) not in " ".join(text.split()), t


for fn in (test_animated_values, test_lists_in_constraint_settings, test_parenting_with_deform, test_physics_and_particles_note, test_objects_not_switched_on_and_other_types,
           test_big_mesh_edit_mode, test_new_collections_need_the_scene, test_linked_objects,
           test_recorded_data_not_called_unrecorded, test_nothing_said_with_sculpting_only):
    H.run(fn)
H.finish()
