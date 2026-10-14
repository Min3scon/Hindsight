import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import numpy as np
from mathutils import Vector
import tests.harness as H
from hindsight.bl import runtime, meshio
from hindsight.bl import morph as BM
from hindsight.core import morph as CM
from hindsight.core.types import POSITION_KEY

C = bpy.context


def positions(ob):
    return meshio.read_channel(ob.data, POSITION_KEY).copy()


def setup(n=2000):
    """Sphere in Sculpt Mode, 1 stroke, morph target, 3 big strokes. Returns (ob, target_positions)."""
    H.fresh()
    ob = H.make_mesh(n)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1, n=200)
    assert H.commit(ob) is not None
    runtime.set_morph_target(C)
    target = positions(ob)
    h = runtime.history_of(ob)
    assert h.morph_target == 1
    for s in (2, 3, 4):
        H.fake_stroke(ob, s, n=100000)
        assert H.commit(ob) is not None
    assert len(h.entries) == 4 and h.cursor == 4
    return ob, target


def radius_for_fraction(co, centre, frac):
    d = np.sqrt(((co.reshape(-1, 3).astype(np.float64) - centre) ** 2).sum(axis=1))
    return float(np.sort(d)[int(len(d) * frac)]), d


def test_core_falloff_blend():
    w = CM.falloff(np.array([0.0, 0.5, 1.0, 2.0]), 1.0)
    assert w[0] == 1.0 and abs(w[1] - 0.5625) < 1e-12 and w[2] == 0.0 and w[3] == 0.0
    cur = np.zeros(9, np.float32)
    tgt = np.arange(9, dtype=np.float32)
    mask = np.array([0.0, 0.5, 1.0], np.float32)
    CM.morph_blend(cur, tgt, np.array([0, 1, 2]), np.ones(3), 1.0, mask)
    assert np.array_equal(cur[:3], tgt[:3]) and np.allclose(cur[3:6], tgt[3:6] * 0.5) and not cur[6:].any()


def test_morph_full_strength():
    ob, target = setup()
    h = runtime.history_of(ob)
    pre = H.snapshot(ob)
    cur = positions(ob)
    vi = 123
    centre = cur.reshape(-1, 3)[vi].astype(np.float64)
    r, d = radius_for_fraction(cur, centre, 0.10)
    assert BM.begin(ob), BM.last_message[0]
    moved = BM.apply_morph_at(ob, Vector(centre), r, 1.0)
    step = BM.end(ob)
    assert step is not None and step.label == "Morph History"
    assert len(h.entries) == 5 and h.cursor == 5, "exactly one new step"
    assert h.morph_target == 1, "target kept"
    new = positions(ob).reshape(-1, 3)
    tg = target.reshape(-1, 3)
    old = cur.reshape(-1, 3)
    inside = d < r
    assert 0.05 * len(d) < moved <= inside.sum() + 1, moved
    assert np.array_equal(new[vi], tg[vi]), "centre vertex lands exactly on the target"
    assert np.array_equal(new[~inside], old[~inside]), "outside the radius unchanged"
    # inside: between current and target (closer to the target near the centre)
    near = d < r * 0.3
    err_new = np.abs(new[near] - tg[near]).max()
    assert err_new < np.abs(old[near] - tg[near]).max() * 0.5
    runtime.goto(C, 4)
    H.assert_same(H.snapshot(ob), pre, "undo of the morph stroke")
    runtime.goto(C, 5)
    assert np.array_equal(positions(ob).reshape(-1, 3), new)


def test_morph_half_strength_and_passes():
    ob, target = setup()
    cur = positions(ob)
    vi = 321
    centre = cur.reshape(-1, 3)[vi].astype(np.float64)
    r, _d = radius_for_fraction(cur, centre, 0.10)
    assert BM.begin(ob)
    BM.apply_morph_at(ob, Vector(centre), r, 0.5)
    BM.apply_morph_at(ob, Vector(centre), r, 0.5)   # same stroke: no build-up beyond 0.5
    BM.end(ob)
    v = positions(ob).reshape(-1, 3)[vi]
    half = (cur.reshape(-1, 3)[vi].astype(np.float64) + target.reshape(-1, 3)[vi]) * 0.5
    assert np.allclose(v, half, atol=1e-6), (v, half)
    # a second pass moves closer (75%)
    assert BM.begin(ob)
    BM.apply_morph_at(ob, Vector(v.astype(np.float64)), r, 0.5)   # centred on the moved vertex
    BM.end(ob)
    v2 = positions(ob).reshape(-1, 3)[vi]
    q = cur.reshape(-1, 3)[vi] * 0.25 + target.reshape(-1, 3)[vi] * 0.75
    assert np.allclose(v2, q, atol=1e-6)
    assert len(runtime.history_of(ob).entries) == 6


def test_target_in_past_after_scrub_and_mask():
    ob, target = setup()
    # mask the whole mesh: nothing moves, no step
    me = ob.data
    a = me.attributes.new(".sculpt_mask", "FLOAT", "POINT")
    a.data.foreach_set("value", np.ones(len(me.vertices), np.float32))
    me.update()
    H.commit(ob)
    n0 = len(runtime.history_of(ob).entries)
    cur = positions(ob)
    centre = cur.reshape(-1, 3)[5].astype(np.float64)
    assert BM.begin(ob)
    assert BM.apply_morph_at(ob, Vector(centre), 10.0, 1.0) == 0
    assert BM.end(ob) is None
    assert len(runtime.history_of(ob).entries) == n0
    assert np.array_equal(positions(ob), cur)


def test_refusals():
    ob, _t = setup(800)
    ob.shape_key_add(name="Basis")
    assert not BM.begin(ob)
    assert "shape keys" in BM.last_message[0]
    assert not BM._SESSION
    # no morph target
    H.fresh()
    ob = H.make_mesh(800)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    H.commit(ob)
    assert not BM.begin(ob) and "morph target" in BM.last_message[0]
    # multires
    runtime.set_morph_target(C)
    ob.modifiers.new("M", "MULTIRES")
    assert not BM.begin(ob) and "Multires" in BM.last_message[0]


def test_topology_change_invalidates_target():
    ob, _t = setup(800)
    h = runtime.history_of(ob)
    # remesh-like topology change, recorded as a TOPO step
    H.enter(ob, "OBJECT")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide()
    bpy.ops.object.mode_set(mode="OBJECT")
    H.enter(ob, "SCULPT")
    assert h.entries[-1].kind == "TOPO", [e.kind for e in h.entries]
    st = runtime.ensure_state(ob)
    assert CM.target_positions(h, st, runtime.obj_uid(ob)) is None
    assert not BM.begin(ob)
    assert BM.last_message[0] == "The mesh was remeshed since the morph target. Set a new one"
    # a new target after the remesh works again
    runtime.set_morph_target(C)
    H.fake_stroke(ob, 9, n=100000)
    H.commit(ob)
    assert BM.begin(ob)
    BM.end(ob)


def test_target_positions_pure():
    ob, target = setup(800)
    h = runtime.history_of(ob)
    st = runtime.ensure_state(ob)
    before = st.arrays[POSITION_KEY].copy()
    tp = CM.target_positions(h, st, runtime.obj_uid(ob))
    assert np.array_equal(tp, target)
    assert np.array_equal(st.arrays[POSITION_KEY], before) and h.cursor == 4, "state untouched"
    # target in the future (after scrubbing back) also works
    runtime.goto(C, 0)
    h.morph_target = 3
    runtime.goto(C, 4)
    s3 = None
    runtime.goto(C, 3)
    s3 = positions(ob)
    runtime.goto(C, 0)
    tp = CM.target_positions(h, runtime.ensure_state(ob), runtime.obj_uid(ob))
    assert np.array_equal(tp, s3) and h.cursor == 0


def test_tool_registered_or_skipped():
    H.fresh()
    assert hasattr(bpy.types, "HINDSIGHT_OT_morph_stroke")
    if BM._tool_registered[0]:
        from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
        cls = ToolSelectPanelHelper._tool_class_from_space_type('VIEW_3D')
        ids = [t.idname for t in cls._tools_flatten(cls.tools_from_context(C, mode='SCULPT'))
               if t is not None and hasattr(t, "idname")]
        assert BM.TOOL_ID in ids, ids


for fn in (test_core_falloff_blend, test_morph_full_strength, test_morph_half_strength_and_passes,
           test_target_in_past_after_scrub_and_mask, test_refusals, test_topology_change_invalidates_target,
           test_target_positions_pure, test_tool_registered_or_skipped):
    H.run(fn)
H.finish()
