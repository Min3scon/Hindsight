"""D109: a library override recorded with Record everything keeps its history when the file is opened
again. Blender builds overrides again from their library on load, so the id Hindsight stored on the
object was gone and so was the history. The override is found again by its library file and the
datablock it overrides."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile
import bpy
import tests.harness as H
from hindsight.bl import datarec, idmap, runtime
from hindsight.bl import prefs
from hindsight.core.types import SETTINGS

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hs_override_")


def make_library():
    lib = os.path.join(TMP, "lib.blend")
    H.fresh()
    ob = H.make_mesh(300, name="Statue")
    coll = bpy.data.collections.new("StatueColl")
    C.scene.collection.children.link(coll)
    coll.objects.link(ob)
    C.scene.collection.objects.unlink(ob)
    bpy.ops.wm.save_as_mainfile(filepath=lib)
    return lib


def make_override(lib):
    with bpy.data.libraries.load(lib, link=True) as (src, dst):
        dst.collections = ["StatueColl"]
    lc = dst.collections[0]
    inst = bpy.data.objects.new("inst", None)
    inst.instance_type = "COLLECTION"
    inst.instance_collection = lc
    C.scene.collection.objects.link(inst)
    try:
        lc.override_hierarchy_create(C.scene, C.view_layer, reference=inst)
    except TypeError:
        lc.override_hierarchy_create(C.scene, C.view_layer)
    return next(x for x in bpy.data.objects if x.name.startswith("Statue") and x.override_library is not None)


def test_override_keeps_its_history_on_reload():
    lib = make_library()
    H.fresh()
    SETTINGS.save_mode = "SIDECAR"
    SETTINGS.crash_recovery = "LAST_SAVED"
    prefs.FORCE["record_scope"] = "EVERYTHING"
    o = make_override(lib)
    datarec.set_tracked(o, True)
    uid = idmap.uid_of(o)
    o.location.x = 2.5
    assert datarec.commit(label="Move", uids=[uid])
    o.location.x = 4.0
    assert datarec.commit(label="Move", uids=[uid])
    path = os.path.join(TMP, "scene.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    prefs.FORCE["record_scope"] = "EVERYTHING"
    o = next(x for x in bpy.data.objects if x.name.startswith("Statue") and x.override_library is not None)
    h = runtime.history_of(o) or runtime.reg().history_for(idmap.uid_of(o) or "")
    assert idmap.uid_of(o) == uid, "the override has its id back"
    assert h is not None and len(h.entries) == 2, [(e.load().label, getattr(e.load(), "records", None) and [r.changes for r in e.load().records]) for e in h.entries]
    runtime._goto_history(h, 1, True)
    assert abs(o.location.x - 2.5) < 1e-6, o.location.x
    runtime._goto_history(h, 2, True)
    assert abs(o.location.x - 4.0) < 1e-6, o.location.x


if __name__ == "__main__":
    try:
        H.run(test_override_keeps_its_history_on_reload)
    finally:
        prefs.FORCE["record_scope"] = None
        shutil.rmtree(TMP, ignore_errors=True)
    H.finish()
