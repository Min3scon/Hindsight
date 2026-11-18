"""What the slider and the step list ask for on every redraw: the same answers as before they were
made cheaper. Run: <bpy python> tests/test_bl_runtime_ui.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy  # noqa: E402
import tests.harness as H  # noqa: E402
from hindsight.bl import rigmark, runtime  # noqa: E402

C = bpy.context


def _reference_visible():
    """_visible_uids as it was: runtime.is_excluded asked for every object."""
    real = runtime._exclusion_test
    runtime._exclusion_test = lambda: runtime.is_excluded
    try:
        return runtime._visible_uids()
    finally:
        runtime._exclusion_test = real


def test_visible_uids_same_answers():
    H.fresh()
    obs = []
    for i in range(6):
        ob = H.make_mesh(300, name=f"O{i}", location=(i * 3, 0, 0))
        H.enter(ob, "SCULPT")
        H.fake_stroke(ob, i + 1)
        assert runtime.commit(ob) is not None
        H.enter(ob, "OBJECT")
        obs.append(ob)
    rigmark.mark(obs[5], "camera")              # Hindsight's own Movie Mode object: left out
    coll = bpy.data.collections.new("Listed")
    C.scene.collection.children.link(coll)
    for ob in obs[:2]:
        coll.objects.link(ob)
    inner = bpy.data.collections.new("Inner")
    coll.children.link(inner)
    inner.objects.link(obs[2])
    p = C.scene.hindsight
    seen = {}
    for mode, listed in (("NONE", False), ("BLACKLIST", False), ("BLACKLIST", True),
                         ("WHITELIST", True), ("WHITELIST", False), ("NONE", True)):
        p.filter_collections.clear()
        if listed:
            it = p.filter_collections.add()
            it.collection = coll
        p.filter_mode = mode
        got, want = runtime._visible_uids(), _reference_visible()
        assert got == want, (mode, listed, got ^ want)
        seen[(mode, listed)] = len(got)
    uids = [runtime.obj_uid(o) for o in obs]
    assert seen[("NONE", False)] == 5 and uids[5] not in runtime._visible_uids()
    assert seen[("BLACKLIST", True)] == 2, seen      # O3, O4 (O5 is the rig)
    assert seen[("WHITELIST", True)] == 3, seen      # O0, O1, O2 (nested)
    assert seen[("WHITELIST", False)] == 0, seen


for f in [test_visible_uids_same_answers]:
    H.run(f)
H.finish()
