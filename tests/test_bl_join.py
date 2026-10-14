import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import numpy as np
import tests.harness as H
from hindsight.bl import runtime, joinsep
from hindsight.core.types import SETTINGS, JOIN

C = bpy.context


def _select(active, others):
    for o in C.selected_objects:
        o.select_set(False)
    for o in [active] + others:
        o.select_set(True)
    C.view_layer.objects.active = active


def _setup():
    """A1, A2, B1, B2, A3 -> join -> A4. Returns objects, uids and expected states per cursor."""
    H.fresh()
    A = H.make_mesh(1500, name="A", location=(0, 0, 0))
    B = H.make_mesh(1200, name="B", location=(3, 1, 0))
    B.rotation_euler = (0.3, 0.2, 0.1)
    bpy.context.view_layer.update()
    a_s, b_s = [H.snapshot(A)], [H.snapshot(B)]
    H.enter(A, "SCULPT")
    for seed in (1, 2):
        H.fake_stroke(A, seed)
        assert H.commit(A) is not None
        a_s.append(H.snapshot(A))
    H.enter(B, "SCULPT")
    H.fake_stroke(B, 3)
    assert H.commit(B) is not None
    b_s.append(H.snapshot(B))
    H.fake_stroke(B, 4, channel="mask")
    assert H.commit(B) is not None
    b_s.append(H.snapshot(B))
    H.enter(A, "SCULPT")
    H.fake_stroke(A, 5)
    assert H.commit(A) is not None
    a_s.append(H.snapshot(A))
    H.enter(A, "OBJECT")
    b_matrix = [v for row in B.matrix_world for v in row]
    a_uid, b_uid = runtime.obj_uid(A), runtime.obj_uid(B)
    _select(A, [B])
    assert joinsep.join_with_history(C) is True
    assert bpy.data.objects.get("B") is None and runtime.find_object(b_uid) is None
    h = runtime.history_of(A)
    assert len(h.entries) == 6 and h.cursor == 6, (len(h.entries), h.cursor)
    assert h.members == [a_uid, b_uid], h.members
    assert h.entries[-1].kind == JOIN
    assert [e.target for e in h.entries] == [a_uid, a_uid, b_uid, b_uid, a_uid, a_uid]
    assert runtime.reg().history_for(b_uid) is h
    assert len(runtime.reg().all()) == 1
    assert "_hs_src" not in A.data.attributes
    joined = H.snapshot(A)
    assert len(A.data.vertices) == len(a_s[0]["position|POINT|FLOAT_VECTOR"]) // 3 + \
        len(b_s[0]["position|POINT|FLOAT_VECTOR"]) // 3
    H.enter(A, "SCULPT")
    H.fake_stroke(A, 6)
    assert H.commit(A) is not None
    j4 = H.snapshot(A)
    assert len(h.entries) == 7
    # expected (A state, B state or None) per cursor position
    exp = {0: (a_s[0], b_s[0]), 1: (a_s[1], b_s[0]), 2: (a_s[2], b_s[0]), 3: (a_s[2], b_s[1]),
           4: (a_s[2], b_s[2]), 5: (a_s[3], b_s[2]), 6: (joined, None), 7: (j4, None)}
    return A, a_uid, b_uid, b_matrix, h, exp


def _check(k, a_uid, b_uid, b_matrix, h, exp):
    assert h.cursor == k, (h.cursor, k)
    A = runtime.find_object(a_uid)
    assert A is not None and A.name == "A"
    ea, eb = exp[k]
    H.assert_same(H.snapshot(A), ea, f"A at {k}")
    B = runtime.find_object(b_uid)
    if eb is None:
        assert B is None, f"B must be gone at {k}"
        assert bpy.data.objects.get("B") is None
        assert b_uid not in runtime._STATES
    else:
        assert B is not None, f"B must exist at {k}"
        assert B.name == "B", B.name
        assert B.users_collection and B.name in C.scene.collection.all_objects
        m = [v for row in B.matrix_world for v in row]
        assert np.allclose(m, b_matrix, atol=1e-6), (m, b_matrix)
        H.assert_same(H.snapshot(B), eb, f"B at {k}")
        st = runtime._STATES.get(b_uid)
        assert st is not None
        for key, arr in eb.items():
            assert st.arrays[key].tobytes() == arr.tobytes(), f"B state {key} at {k}"


def test_join_unmerge_remerge():
    A, a_uid, b_uid, b_matrix, h, exp = _setup()
    assert A.mode == "SCULPT"
    for k in (7, 5, 3, 0, 5, 6, 7, 6, 5, 4, 7, 2, 6, 1, 7, 0, 7):
        runtime.goto(C, k)
        _check(k, a_uid, b_uid, b_matrix, h, exp)
        assert runtime.find_object(a_uid).mode == "SCULPT"
    # one step at a time, both directions
    for k in list(range(6, -1, -1)) + list(range(1, 8)):
        assert runtime.step(C, -1 if k < h.cursor else 1)
        _check(k, a_uid, b_uid, b_matrix, h, exp)


def test_join_packed_entries():
    A, a_uid, b_uid, b_matrix, h, exp = _setup()
    for e in h.entries:
        e.pack("zlib")
    assert all(e.is_packed() for e in h.entries)
    st = h.entries[5].load()
    assert b_uid in st.extra["_snapshots"] and st.extra["members"][0]["name"] == "B"
    for k in (0, 7, 4, 6, 5, 7):
        runtime.goto(C, k)
        _check(k, a_uid, b_uid, b_matrix, h, exp)


def test_unmerged_object_active_remerge():
    A, a_uid, b_uid, b_matrix, h, exp = _setup()
    runtime.goto(C, 3)
    H.enter(runtime.find_object(a_uid), "OBJECT")
    B = runtime.find_object(b_uid)
    _select(B, [])
    assert runtime.history_of(B) is h
    runtime.goto(C, 7)       # scrubbing B's (merged) history past the join removes B
    assert runtime.find_object(b_uid) is None
    assert C.view_layer.objects.active is not None and runtime.obj_uid(C.view_layer.objects.active) == a_uid
    _check(7, a_uid, b_uid, b_matrix, h, exp)
    runtime.goto(C, 2)
    _check(2, a_uid, b_uid, b_matrix, h, exp)


def test_join_materials_restored():
    H.fresh()
    A = H.make_mesh(800, name="A")
    B = H.make_mesh(800, name="B", location=(2, 0, 0))
    mat = bpy.data.materials.new("RedMat")
    B.data.materials.append(mat)
    H.enter(B, "SCULPT")
    H.fake_stroke(B, 1)
    assert H.commit(B) is not None
    H.enter(B, "OBJECT")
    b_uid = runtime.obj_uid(B)
    _select(A, [B])
    assert joinsep.join_with_history(C)
    h = runtime.history_of(A)
    assert len(h.entries) == 2 and h.members[1] == b_uid
    a_mats = [m.name if m else "" for m in A.data.materials]
    runtime.goto(C, 1)
    B = runtime.find_object(b_uid)
    assert [m.name for m in B.data.materials] == ["RedMat"]
    assert [m.name if m else "" for m in A.data.materials] == []
    runtime.goto(C, 0)
    runtime.goto(C, 2)
    assert runtime.find_object(b_uid) is None
    assert [m.name if m else "" for m in A.data.materials] == a_mats


def test_operator_plain_join_and_keymap():
    H.fresh()
    A = H.make_mesh(500, name="P")
    B = H.make_mesh(500, name="Q", location=(2, 0, 0))
    _select(A, [B])
    nv = len(A.data.vertices) + len(B.data.vertices)
    assert bpy.ops.hindsight.join('EXEC_DEFAULT') == {'FINISHED'}
    assert bpy.data.objects.get("Q") is None
    assert len(bpy.data.objects["P"].data.vertices) == nv
    assert runtime.history_of(bpy.data.objects["P"]) is None
    assert len(runtime.reg().all()) == 0
    # with history: EXEC joins and merges
    D = H.make_mesh(500, name="D", location=(0, 3, 0))
    H.enter(D, "SCULPT")
    H.fake_stroke(D, 9)
    assert H.commit(D) is not None
    H.enter(D, "OBJECT")
    P = bpy.data.objects["P"]
    _select(P, [D])
    assert bpy.ops.hindsight.join('EXEC_DEFAULT') == {'FINISHED'}
    h = runtime.history_of(P)
    assert h is not None and len(h.entries) == 2 and h.entries[-1].kind == JOIN
    km = C.window_manager.keyconfigs.addon.keymaps.get("Object Mode")
    ids = [(k.idname, k.type, k.ctrl, k.oskey) for k in km.keymap_items] if km else []
    assert ("hindsight.join", "J", True, False) in ids
    assert ("hindsight.join", "J", False, True) in ids
    assert bpy.types.HINDSIGHT_OT_join.bl_label == "Join"


H.run(test_join_unmerge_remerge)
H.run(test_join_packed_entries)
H.run(test_unmerged_object_active_remerge)
H.run(test_join_materials_restored)
H.run(test_operator_plain_join_and_keymap)
H.finish()
