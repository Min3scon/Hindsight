"""D107: a mesh changed in a session without Hindsight keeps its history when Blender's backup copy
(<file>.blend1, Save Versions) holds the mesh exactly as Hindsight saved it: the change becomes one
step, every position plays back exactly. Two saves without Hindsight (the backup no longer matches)
still leave the history out, as before (D46)."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile
import bpy
import tests.harness as H
from hindsight.bl import runtime
from hindsight.core.types import SETTINGS

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hs_outside_backup_")
NAME = "Sculpty"


def setup():
    H.fresh()
    SETTINGS.crash_recovery = "LAST_SAVED"
    C.preferences.filepaths.save_version = 1


def outside_session(path, seed):
    H.fresh(register=False)
    C.preferences.filepaths.save_version = 1
    bpy.ops.wm.open_mainfile(filepath=path)
    H.fake_stroke(bpy.data.objects[NAME], seed, n=80)
    bpy.ops.wm.save_mainfile()


def test_backup_bridges_the_change():
    setup()
    ob = H.make_mesh(1500, name=NAME)
    H.enter(ob, "SCULPT")
    states = [H.snapshot(ob)]
    for s in range(3):
        H.fake_stroke(ob, s, n=80)
        assert runtime.commit(ob) is not None
        states.append(H.snapshot(ob))
    H.enter(ob, "OBJECT")
    path = os.path.join(TMP, "bridge.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    outside_session(path, 99)
    assert os.path.isfile(path + "1")
    setup()
    bpy.ops.wm.open_mainfile(filepath=path)
    ob = bpy.data.objects[NAME]
    states.append(H.snapshot(ob))
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 4 and h.cursor == 4, (h and len(h.entries), h and h.cursor)
    assert h.entries[-1].load().label == runtime.OUTSIDE_LABEL
    assert any("goes on" in t for _s, t in runtime.load_notes), runtime.load_notes
    names = sorted(m.name for m in bpy.data.meshes)
    assert names == [ob.data.name], f"what loading the backup brought in is gone: {names}"
    H.enter(ob, "SCULPT")
    for i in [0, 1, 2, 3, 4, 2, 4]:
        runtime.goto(C, i)
        H.assert_same(H.snapshot(ob), states[i], f"position {i}")


def test_two_outside_saves_still_leave_it_out():
    setup()
    ob = H.make_mesh(1500, name=NAME)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1, n=80)
    assert runtime.commit(ob) is not None
    H.enter(ob, "OBJECT")
    path = os.path.join(TMP, "twice.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    outside_session(path, 98)
    outside_session(path, 97)
    setup()
    bpy.ops.wm.open_mainfile(filepath=path)
    assert runtime.history_of(bpy.data.objects[NAME]) is None
    assert any(runtime.OUTSIDE_MESH_TEXT.format(name=NAME) == t for _s, t in runtime.load_notes)


if __name__ == "__main__":
    try:
        for t in (test_backup_bridges_the_change, test_two_outside_saves_still_leave_it_out):
            H.run(t)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    H.finish()
