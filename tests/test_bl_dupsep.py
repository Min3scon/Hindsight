import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import bmesh
import tests.harness as H
from hindsight.bl import runtime, warnings

C = bpy.context


def setup():
    H.fresh()
    from hindsight.bl import dupsep
    dupsep.register()  # __init__ may not list it yet; must be safe to call twice
    return dupsep


def sculpted(n_strokes=2, name="Obj"):
    ob = H.make_mesh(1500, name=name)
    H.enter(ob, "SCULPT")
    for i in range(n_strokes):
        H.fake_stroke(ob, i + 1)
        assert H.commit(ob) is not None
    H.enter(ob, "OBJECT")
    assert len(runtime.history_of(ob).entries) == n_strokes
    return ob


def test_operators_and_keymaps_registered():
    setup()
    for cls in ("HINDSIGHT_OT_separate", "HINDSIGHT_OT_mask_extract", "HINDSIGHT_OT_duplicate_linked"):
        assert hasattr(bpy.types, cls), cls
    assert bpy.types.HINDSIGHT_OT_separate.bl_label == "Separate"
    assert bpy.types.HINDSIGHT_OT_mask_extract.bl_label == "Mask Extract"
    assert bpy.types.HINDSIGHT_OT_duplicate_linked.bl_label == "Duplicate Linked"
    kc = C.window_manager.keyconfigs.addon
    km = kc.keymaps.get("Mesh")
    assert km and any(k.idname == "hindsight.separate" and k.type == "P" and not k.alt
                      for k in km.keymap_items)
    km = kc.keymaps.get("Object Mode")
    assert km and any(k.idname == "hindsight.duplicate_linked" and k.type == "D" and k.alt
                      for k in km.keymap_items)


def test_shift_d_independent_history():
    dupsep = setup()
    ob = sculpted(2)
    uid = runtime.obj_uid(ob)
    bpy.ops.object.duplicate()
    dup = C.active_object
    assert dup != ob
    dup[runtime.UID_PROP] = uid  # as copied by Shift+D (the depsgraph handler may already have fixed it)
    changed = dupsep.fix_uids()
    assert changed == [dup.name], changed
    assert runtime.obj_uid(ob) == uid, "original keeps its uid"
    assert runtime.obj_uid(dup) != uid
    assert runtime.history_of(dup) is None
    assert len(runtime.history_of(ob).entries) == 2
    assert dupsep.fix_uids() == [], "second run changes nothing"
    # sculpting the duplicate starts its own history
    H.enter(dup, "SCULPT")
    H.fake_stroke(dup, 42)
    assert H.commit(dup) is not None
    assert len(runtime.history_of(dup).entries) == 1
    assert len(runtime.history_of(ob).entries) == 2
    assert runtime.history_of(dup) is not runtime.history_of(ob)
    H.enter(dup, "OBJECT")


def test_original_kept_when_renamed_copy_is_shorter():
    dupsep = setup()
    ob = sculpted(1, name="Sculpture")
    uid = runtime.obj_uid(ob)
    dupsep.fix_uids()  # records uid -> "Sculpture"
    bpy.ops.object.duplicate()
    dup = C.active_object
    dup.name = "A"
    dup[runtime.UID_PROP] = uid
    assert dupsep.fix_uids() == ["A"]
    assert runtime.obj_uid(ob) == uid and runtime.obj_uid(dup) != uid


def test_handler_fixes_on_object_count_change():
    setup()
    ob = sculpted(1)
    uid = runtime.obj_uid(ob)
    bpy.ops.object.duplicate()
    dup = C.active_object
    C.view_layer.update()
    for fn in list(bpy.app.handlers.depsgraph_update_post):
        if getattr(fn, "__name__", "") == "_on_depsgraph" and fn.__module__.endswith("dupsep"):
            fn(C.scene, C.evaluated_depsgraph_get())
    assert runtime.obj_uid(dup) != uid and runtime.obj_uid(ob) == uid


def test_linked_duplicate_warning():
    dupsep = setup()
    ob = sculpted(1)
    bpy.ops.object.duplicate(linked=True)
    dup = C.active_object
    assert dup != ob and dup.data == ob.data
    texts = [t for _s, t in warnings.collect(C, dup)]
    assert warnings.TXT_LINKED in texts, texts
    assert warnings.TXT_LINKED in [t for _s, t in warnings.collect(C, ob)]
    dupsep.fix_uids()
    assert runtime.obj_uid(dup) != runtime.obj_uid(ob)
    # the wrapping operator (exec path) duplicates linked too
    n = len(bpy.data.objects)
    assert bpy.ops.hindsight.duplicate_linked() == {"FINISHED"}
    assert len(bpy.data.objects) == n + 1 and C.active_object.data == ob.data
    assert len({runtime.obj_uid(o) for o in bpy.data.objects}) == len(bpy.data.objects)


def _select_some_faces(ob, n=20):
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces[:n]:
        f.select_set(True)
    bm.select_flush(True)
    bmesh.update_edit_mesh(ob.data)


def test_separate_new_object_new_history():
    dupsep = setup()
    ob = sculpted(2)
    uid = runtime.obj_uid(ob)
    h = runtime.history_of(ob)
    nfaces = len(ob.data.polygons)
    H.enter(ob, "EDIT")
    _select_some_faces(ob)
    names = set(bpy.data.objects.keys())
    assert bpy.ops.mesh.separate(type="SELECTED") == {"FINISHED"}
    new = [bpy.data.objects[n] for n in set(bpy.data.objects.keys()) - names]
    assert len(new) == 1
    part = new[0]
    H.enter(ob, "OBJECT")
    assert len(ob.data.polygons) == nfaces - 20
    assert len(h.entries) == 3, f"separate = one Edit Mode step on the original, got {len(h.entries)}"
    changed = dupsep.fix_uids()
    assert set(changed) <= {part.name}, changed  # the depsgraph handler may have fixed it already
    assert runtime.obj_uid(ob) == uid
    assert runtime.obj_uid(part) not in (None, uid)
    assert runtime.history_of(part) is None
    assert runtime.history_of(ob) is h and len(h.entries) == 3
    # scrubbing the original still works
    runtime.goto(C, 2)
    assert len(ob.data.polygons) == nfaces
    runtime.goto(C, 3)
    assert len(ob.data.polygons) == nfaces - 20


def test_separate_operator_exec():
    dupsep = setup()
    ob = sculpted(1)
    uid = runtime.obj_uid(ob)
    H.enter(ob, "EDIT")
    _select_some_faces(ob, 10)
    n = len(bpy.data.objects)
    assert bpy.ops.hindsight.separate(type="SELECTED") == {"FINISHED"}
    assert len(bpy.data.objects) == n + 1
    uids = [runtime.obj_uid(o) for o in bpy.data.objects]
    assert uids.count(uid) == 1, "operator fixes uids right away"
    H.enter(ob, "OBJECT")
    assert runtime.obj_uid(ob) == uid and len(runtime.history_of(ob).entries) == 2


for f in [test_operators_and_keymaps_registered, test_shift_d_independent_history,
          test_original_kept_when_renamed_copy_is_shorter, test_handler_fixes_on_object_count_change,
          test_linked_duplicate_warning, test_separate_new_object_new_history,
          test_separate_operator_exec]:
    H.run(f)
H.finish()
