"""Tier 1 gate, save and reload (gate items RELOAD.*), headless, through real operators.

Each row's recorded run is saved with its history in a sidecar folder, embedded in the .blend, or
both; reopened; and checked again exactly like right after recording (every object's history at
every position, the global timeline, Blender's undo). Then: an object renamed keeps its history
(also after a reload); a copy pasted into the same file or another file starts its own history and
leaves the original's alone; appending and linking from a recorded file; Shift+D and Alt+D.

Run: blender -b --factory-startup --python tests/test_bl_t1_reload.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers
from hindsight.bl import datarec, idmap, persistence, runtime
from hindsight.core import persist
from hindsight.core.types import SETTINGS

C = bpy.context
ROWS = ("obj.transforms", "obj.parenting", "obj.constraints", "obj.collections", "obj.addremove", "obj.editsteps")
TMP = tempfile.mkdtemp(prefix="hs_t1_reload_")


def save_and_reopen(path, mode):
    SETTINGS.save_mode = mode
    T.select()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    side = persist.sidecar_dir(path)
    assert os.path.isdir(side) == (mode in ("SIDECAR", "BOTH")), (mode, os.path.isdir(side))
    if mode == "EMBED":
        assert not os.path.isdir(side)
    bpy.ops.wm.open_mainfile(filepath=path)
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    text = bpy.data.texts.get(persistence.TEXT_NAME)
    assert text is not None and ("hindsight_data" in text) == (mode in ("EMBED", "BOTH"))


def reload_all_rows(mode):
    for row in ROWS:
        r = L.Run(T.sequences()[row])
        path = os.path.join(TMP, f"{row.replace('.', '_')}_{mode.lower()}.blend")
        save_and_reopen(path, mode)
        if mode == "BOTH":
            # either copy alone is enough: the sidecar is preferred, the embedded copy is the backup
            shutil.rmtree(persist.sidecar_dir(path))
            bpy.ops.wm.open_mainfile(filepath=path)
        L.recheck(r)


@covers(ROWS, "RELOAD.SIDECAR")
def test_reload_sidecar():
    reload_all_rows("SIDECAR")


@covers(ROWS, "RELOAD.EMBED")
def test_reload_embed():
    reload_all_rows("EMBED")


@covers(ROWS, "RELOAD.BOTH")
def test_reload_both():
    reload_all_rows("BOTH")


def rename(r, name, new):
    """Rename (what F2 does). The oracle names references by name, so the checks are told that
    references to `name` recorded earlier now read `new` (the object's own name is recorded data)."""
    def run():
        bpy.data.objects[name].name = new
        r.renames[name] = new
        return {"FINISHED"}
    return T.Op(f"rename {name} to {new}", run, [new])


@covers(ROWS, "RELOAD.RENAME")
def test_renamed_object_keeps_its_history():
    """Renaming (what F2 or a double click in the outliner does) is a step of the object's own
    history; the history is found by id, before and after a reload."""
    for row in ROWS:
        r = L.Run(T.sequences()[row])
        for uid, name in list(r.names.items()):
            ob = idmap.find(uid)
            if ob is not None and not name.endswith(" renamed"):
                r.add(rename(r, name, name + " renamed"))
        L.check_steps(r)
        L.recheck(r)
        save_and_reopen(os.path.join(TMP, f"rename_{row.replace('.', '_')}.blend"), "SIDECAR")
        L.recheck(r)


def _object_history_snapshot(r, uid):
    h = runtime.reg().history_for(uid)
    return None if h is None else (len(h.entries), h.cursor)


def survivors(r):
    """Tracked mesh objects that still exist at the end of the run, those with a history first."""
    out = []
    for u, name in sorted(r.names.items(), key=lambda kv: kv[1]):
        ob = idmap.find(u)
        if ob is not None and ob.type == "MESH":
            out.append((runtime.reg().history_for(u) is None, ob.name))
    return [n for _no_history, n in sorted(out)]


@covers(ROWS, "RELOAD.PASTE")
def test_copy_paste_same_and_other_file():
    """Ctrl+C, Ctrl+V, for every row: a pasted copy carries the original's id property; Hindsight
    gives it its own id. In the same file the original's history is untouched and still exact; in
    another file the copy starts with no history (the history stays in the file it was recorded in)
    and records from scratch; the source file's history is still there when it is opened again."""
    for row in ROWS:
        r = L.Run(T.sequences()[row])
        name = survivors(r)[0]
        src = bpy.data.objects[name]
        su = idmap.uid_of(src)
        before = _object_history_snapshot(r, su)
        T.select(name)
        assert bpy.ops.view3d.copybuffer() == {"FINISHED"}
        existing = set(bpy.data.objects)
        assert bpy.ops.view3d.pastebuffer() == {"FINISHED"}
        pasted = [o for o in bpy.data.objects if o not in existing]     # the parent comes along too
        assert pasted, row
        known = {idmap.uid_of(o) for o in existing}
        for o in pasted:
            pu = idmap.claim(o)
            assert pu not in known, f"{row}: the copy {o.name} shares an original's id"
        L.tick_like("paste")
        for o in pasted:
            assert runtime.reg().history_for(idmap.uid_of(o)) is None and not datarec.is_tracked(o)
        assert _object_history_snapshot(r, su) == before
        for o in pasted:
            bpy.data.objects.remove(o)
        L.tick_like("remove the copy")
        T.select()
        L.check_object_histories(r)
        path = os.path.join(TMP, f"paste_{row.replace('.', '_')}.blend")
        save_and_reopen(path, "SIDECAR")
        T.select(name)
        assert bpy.ops.view3d.copybuffer() == {"FINISHED"}
        H.fresh()
        datarec.FORCE_SCOPE[0] = "EVERYTHING"
        assert bpy.ops.view3d.pastebuffer() == {"FINISHED"}
        ob = bpy.data.objects[name]
        assert all(runtime.history_of(o) is None for o in bpy.data.objects), "a history came along"
        datarec.set_tracked(ob, True)
        want0 = O.object_state(ob)
        T.translate(ob.name, (0, 0, 2)).run()
        L.tick_like("move")
        h = runtime.history_of(ob)
        assert h is not None and len(h.entries) == 1
        runtime._goto_history(h, 0, True)
        assert O.object_state(ob) == want0, O.diff(want0, O.object_state(ob))[:3]
        bpy.ops.wm.open_mainfile(filepath=path)                # the source file's history is still there
        assert _object_history_snapshot(r, su) == before


@covers(ROWS, "RELOAD.APPEND")
def test_append_and_link_from_a_recorded_file():
    """For every row: appending an object from a recorded file gives an object without history
    (histories belong to their file), which records once switched on. A linked object can't be
    edited or recorded: switching it on is refused (D25) and nothing fails."""
    for row in ROWS:
        r = L.Run(T.sequences()[row])
        names = survivors(r)
        path = os.path.join(TMP, f"append_{row.replace('.', '_')}.blend")
        save_and_reopen(path, "SIDECAR")
        H.fresh()
        datarec.FORCE_SCOPE[0] = "EVERYTHING"
        directory = os.path.join(path, "Object")
        assert bpy.ops.wm.append(filepath=os.path.join(directory, names[0]), directory=directory,
                                 filename=names[0]) == {"FINISHED"}
        a = bpy.data.objects[names[0]]
        assert runtime.history_of(a) is None
        datarec.set_tracked(a, True)
        want0 = O.object_state(a)
        T.rotate(a.name, 0.4).run()
        L.tick_like("rotate")
        h = runtime.history_of(a)
        assert h is not None and len(h.entries) == 1
        runtime._goto_history(h, 0, True)
        assert O.object_state(a) == want0
        other = names[0]                    # the object just appended: same id (D28)
        assert bpy.ops.wm.link(filepath=os.path.join(directory, other), directory=directory,
                               filename=other) == {"FINISHED"}
        b = next(o for o in bpy.data.objects if o.library is not None)
        datarec.set_tracked(b, True)                          # D25: refused, it can't be written back
        assert not datarec.is_tracked(b) and runtime.history_of(b) is None
        T.select()                           # the linked one shares the local one's name: select it directly
        b.select_set(True)
        C.view_layer.objects.active = b
        assert bpy.ops.hindsight.record_everything(on=True) == {"CANCELLED"}
        L.tick_like("nothing")
        assert runtime.history_of(b) is None
        assert idmap.find(idmap.uid_of(a)) == a, "the id finds the local object, never the linked one"
        assert runtime.history_of(a) is not None and len(runtime.history_of(a).entries) == 1


@covers(ROWS, "RELOAD.DUPLICATE")
def test_duplicates_shift_d_and_alt_d():
    """For every row: Shift+D and Alt+D give a copy with its own id and no history (Record everything
    is off for it until switched on); the original's history is untouched and still exact; the
    scene history has one step per duplicate; a linked duplicate shares the mesh."""
    for row in ROWS:
        r = L.Run(T.sequences()[row])
        names = survivors(r)
        snapshot = {u: _object_history_snapshot(r, u) for u in r.names}
        sh = runtime.reg().history_for(r.scene_uid)
        n_scene = len(sh.entries) if sh is not None else 0
        for name, linked in ((names[0], False), (names[-1], True)):
            T.duplicate(name, linked).run()
            L.tick_like("duplicate")
            copy = [o for o in C.selected_objects if o.name != name][0]
            assert idmap.claim(copy) != idmap.uid_of(bpy.data.objects[name])
            assert runtime.history_of(copy) is None and not datarec.is_tracked(copy)
            assert (copy.data == bpy.data.objects[name].data) == linked
        assert {u: _object_history_snapshot(r, u) for u in r.names} == snapshot
        assert len(runtime.reg().history_for(r.scene_uid).entries) == n_scene + 2
        T.select()
        L.check_object_histories(r)


for fn in (test_reload_sidecar, test_reload_embed, test_reload_both, test_renamed_object_keeps_its_history,
           test_copy_paste_same_and_other_file, test_append_and_link_from_a_recorded_file,
           test_duplicates_shift_d_and_alt_d):
    H.run(fn)
H.finish()
