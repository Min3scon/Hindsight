"""Movie Mode camera with modifiers and shape keys (review S2-1).

The camera must hold what renders (the evaluated object: Mirror, Subdivision, Solidify, Displace,
Array, shape keys, a scaled parent), with the 10% margin, at every step of the history, while only
the base mesh is recorded. Each case is built with a history whose early steps are much bigger, or
much smaller, than the shape now; Movie Mode frames once at the end, then every step is shown the
way Movie Mode playback shows it and the evaluated mesh is checked against the camera's frame.

Exact cases (Mirror, Array, and the history of a plain mesh) must also be tight: the union of every
step fills the frame up to the margin. The others are a conservative bound (camrig._measure_stack):
they must hold everything and may be looser.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import math

import bpy
import numpy as np
from mathutils import Euler, Matrix

import tests.harness as H
from hindsight.bl import camrig, runtime

C = bpy.context


def scale_mesh(ob, f, shift=(0.0, 0.0, 0.0)):
    me = ob.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.attributes["position"].data.foreach_get("vector", co)
    co = co.reshape(-1, 3) * np.float32(f) + np.asarray(shift, np.float32)
    me.attributes["position"].data.foreach_set("vector", co.ravel())
    me.update()


def half(ob):
    """One half of a symmetric model (x >= 0.6): the usual Mirror workflow."""
    me = ob.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.attributes["position"].data.foreach_get("vector", co)
    co = co.reshape(-1, 3)
    co[:, 0] = np.abs(co[:, 0]) + 0.6
    me.attributes["position"].data.foreach_set("vector", co.ravel())
    me.update()


def _evaluated_world(ob):
    dg = C.evaluated_depsgraph_get()
    me = ob.evaluated_get(dg).data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    mw = np.array([list(r) for r in ob.matrix_world], np.float64)
    return co.reshape(-1, 3).astype(np.float64) @ mw[:3, :3].T + mw[:3, 3]


def rendered_world(ob):
    """World positions of what a render shows: the evaluated mesh, and the evaluated mesh of a fresh
    copy of the object (in Sculpt Mode, Blender 4.2 and 5.0 keep an Array's relative offset from
    before the positions changed in the viewport; a render evaluates afresh)."""
    with runtime.suspended():
        tmp = ob.copy()
        tmp.data = ob.data.copy()
        C.scene.collection.objects.link(tmp)
        try:
            C.view_layer.update()
            pts = np.vstack([_evaluated_world(ob), _evaluated_world(tmp)])
        finally:
            me = tmp.data
            bpy.data.objects.remove(tmp)
            bpy.data.meshes.remove(me)
    return pts


def view_coords(sc, cam, pts):
    """world_to_camera_view for many points: (u, v, depth)."""
    mi = np.array([list(r) for r in cam.matrix_world.inverted()], np.float64)
    pc = pts @ mi[:3, :3].T + mi[:3, 3]
    fr = [tuple(v) for v in cam.data.view_frame(scene=sc)]
    xs = [v[0] for v in fr]
    ys = [v[1] for v in fr]
    fz = -fr[0][2]
    depth = -pc[:, 2]
    u = (pc[:, 0] * fz / depth - min(xs)) / (max(xs) - min(xs))
    v = (pc[:, 1] * fz / depth - min(ys)) / (max(ys) - min(ys))
    return u, v, depth


def goto_movie(pos):
    prev = getattr(runtime.goto, "_movie", False)
    runtime.goto._movie = True
    try:
        with runtime.suspended():
            runtime.goto(C, pos)
    finally:
        runtime.goto._movie = prev
    C.view_layer.update()


def make_history(ob, early):
    """Base mesh steps: `early` = "big" (3x), "small" (0.2x) or "plain"; then back to the shape
    now and one stroke. Returns the number of steps."""
    H.enter(ob, "SCULPT")
    if early == "big":
        scale_mesh(ob, 3.0, (0.4, 0.0, 0.0))
        assert H.commit(ob) is not None
        scale_mesh(ob, 1.0 / 3.0)
        scale_mesh(ob, 1.0, (-0.4 / 3.0, 0.0, 0.0))
        assert H.commit(ob) is not None
    elif early == "small":
        scale_mesh(ob, 0.2)
        assert H.commit(ob) is not None
        scale_mesh(ob, 5.0)
        assert H.commit(ob) is not None
    H.fake_stroke(ob, 7, n=80)
    assert H.commit(ob) is not None
    runtime.set_global(C, True)
    info = runtime.scrub_info(C)
    assert info["pos"] == info["len"], info
    return info["len"]


def check_every_step(ob, n, exact, msg, min_span=0.5):
    sc = C.scene
    sc.hindsight.movie_mode = True
    cam = camrig.rig(sc)["camera"]
    assert sc.camera is cam
    m0 = [tuple(r) for r in cam.matrix_world]
    us, vs = [], []
    for pos in list(range(n, -1, -1)) + [n]:
        goto_movie(pos)
        assert runtime.scrub_info(C)["pos"] == pos
        assert np.allclose(np.array([tuple(r) for r in cam.matrix_world]), np.array(m0), atol=1e-5), "camera moved"
        u, v, depth = view_coords(sc, cam, rendered_world(ob))
        assert (depth > 0).all(), f"{msg} step {pos}: behind the camera"
        eps = 1e-4
        assert u.min() >= -eps and u.max() <= 1 + eps, f"{msg} step {pos}: x out {u.min():.3f} {u.max():.3f}"
        assert v.min() >= -eps and v.max() <= 1 + eps, f"{msg} step {pos}: y out {v.min():.3f} {v.max():.3f}"
        us.append(u)
        vs.append(v)
    u, v = np.concatenate(us), np.concatenate(vs)
    span = max(u.max() - u.min(), v.max() - v.min())
    lo_u, hi_u, lo_v, hi_v = u.min(), 1 - u.max(), v.min(), 1 - v.max()
    free = 1 - camrig.MARGIN
    assert span <= free + 1e-3, f"{msg}: margin not free {span}"
    assert min(lo_u, hi_u, lo_v, hi_v) >= camrig.MARGIN / 2 - 1e-3, f"{msg}: margin {lo_u} {hi_u} {lo_v} {hi_v}"
    if exact:
        assert span >= free - 2e-3, f"{msg}: exact case not tight: {span:.4f}"
    else:
        assert span >= min_span, f"{msg}: far too loose: {span:.4f}"
    sc.hindsight.movie_mode = False
    return span


SPANS = {}


def run_case(name, setup, exact, n_verts=1200, min_span=0.5):
    for early in ("big", "small"):
        H.fresh()
        ob = H.make_mesh(n_verts, "Model")
        setup(ob)
        C.view_layer.update()
        n = make_history(ob, early)
        SPANS[(name, early)] = check_every_step(ob, n, exact, f"{name}/{early}", min_span)


def test_no_modifier_reference():
    run_case("plain", lambda ob: None, exact=True)


def test_mirror_x():
    def setup(ob):
        half(ob)
        ob.modifiers.new("Mirror", "MIRROR")
    run_case("mirror x", setup, exact=True)


def test_mirror_two_axes():
    def setup(ob):
        half(ob)
        scale_mesh(ob, 1.0, (0.0, 0.7, 0.0))
        m = ob.modifiers.new("Mirror", "MIRROR")
        m.use_axis = (True, True, False)
    run_case("mirror xy", setup, exact=True)


def test_mirror_object():
    def setup(ob):
        half(ob)
        e = bpy.data.objects.new("MirrorPlane", None)
        C.scene.collection.objects.link(e)
        e.matrix_world = Matrix.Translation((-0.8, 0.3, 0.2)) @ Euler((0.0, 0.0, math.radians(30))).to_matrix().to_4x4()
        m = ob.modifiers.new("Mirror", "MIRROR")
        m.mirror_object = e
    run_case("mirror object", setup, exact=True)


def test_subdivision():
    def setup(ob):
        m = ob.modifiers.new("Subdivision", "SUBSURF")
        m.levels = 1
    run_case("subdivision", setup, exact=False)


def test_solidify_outward():
    def setup(ob):
        m = ob.modifiers.new("Solidify", "SOLIDIFY")
        m.thickness = 0.3
        m.offset = 1.0
    run_case("solidify", setup, exact=False)


def test_displace():
    def setup(ob):
        m = ob.modifiers.new("Displace", "DISPLACE")
        m.strength = 0.8
    run_case("displace", setup, exact=False)


def test_array():
    def setup(ob):
        m = ob.modifiers.new("Array", "ARRAY")
        m.count = 3
        m.relative_offset_displace = (1.1, 0.0, 0.2)
    # relative offset: exact for the shape now; for the history the offset may be any size up to the
    # biggest shape's, so a little looser. Blender 4.2 and 5.0 space the copies of a step that is
    # shown again by the size the mesh had before (in the viewport and on a fresh copy alike), so the
    # big early step shows smaller than the bound allows for there
    run_case("array", setup, exact=False, min_span=0.8 if bpy.app.version >= (5, 1, 0) else 0.45)


def test_array_after_mirror():
    def setup(ob):
        half(ob)
        ob.modifiers.new("Mirror", "MIRROR")
        m = ob.modifiers.new("Array", "ARRAY")
        m.count = 2
        m.use_relative_offset = False
        m.use_constant_offset = True
        m.constant_offset_displace = (0.0, 2.5, 0.0)
    run_case("mirror then array", setup, exact=True)


def test_shape_key():
    def setup(ob):
        ob.shape_key_add(name="Basis")
        k = ob.shape_key_add(name="Tall")
        for d in k.data:
            d.co.z = d.co.z * 1.6 + 0.4
            d.co.x += 0.5
        k.value = 1.0
    # Blender renders the shape keys' own positions, so the recorded base mesh doesn't change what
    # renders; the bound still assumes it may (it never frames tighter), so a history 3x bigger than
    # now leaves the model small in the frame
    run_case("shape key", setup, exact=False, min_span=0.2)


def test_parent_with_scale():
    def setup(ob):
        p = bpy.data.objects.new("Parent", None)
        C.scene.collection.objects.link(p)
        p.matrix_world = Matrix.Translation((3, -2, 1)) @ Euler((0.3, 0.2, 0.9)).to_matrix().to_4x4() \
            @ Matrix.Diagonal((2.5, 0.6, 1.3, 1.0))
        ob.parent = p
        half(ob)
        ob.modifiers.new("Mirror", "MIRROR")
    run_case("scaled parent", setup, exact=True)


def test_geometry_nodes_scale():
    """A modifier Hindsight can't follow (Geometry Nodes scaling the mesh 1.5 times about its
    origin): covered by the reach measured now, grown with the history's size."""
    def setup(ob):
        scale_mesh(ob, 1.0, (0.5, 0.0, 0.3))
        ng = bpy.data.node_groups.new("Grow", "GeometryNodeTree")
        ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        gi = ng.nodes.new("NodeGroupInput")
        go = ng.nodes.new("NodeGroupOutput")
        tr = ng.nodes.new("GeometryNodeTransform")
        tr.inputs["Scale"].default_value = (1.5, 1.5, 1.5)
        ng.links.new(gi.outputs[0], tr.inputs["Geometry"])
        ng.links.new(tr.outputs["Geometry"], go.inputs[0])
        ob.modifiers.new("GN", "NODES").node_group = ng
    run_case("geometry nodes", setup, exact=False)


def test_crop_report():
    """The rendered model at the shape now, with a Mirror modifier, is in frame (review repro (a))."""
    H.fresh()
    ob = H.make_mesh(800, "Half")
    half(ob)
    ob.modifiers.new("Mirror", "MIRROR")
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    assert H.commit(ob)
    runtime.set_global(C, True)
    sc = C.scene
    sc.hindsight.movie_mode = True
    cam = camrig.rig(sc)["camera"]
    u, v, _d = view_coords(sc, cam, rendered_world(ob))
    assert u.min() >= 0 and u.max() <= 1, (u.min(), u.max())
    assert v.min() >= 0 and v.max() <= 1, (v.min(), v.max())


def test_scrubbed_back_then_framed():
    """Framed while the history is scrubbed back: the shape at the end (bigger) still fits. The
    cache of the history's extents held every position but the cursor's at the time, so a camera
    framed at step 0 lost the last step once the cursor moved."""
    H.fresh()
    ob = H.make_mesh(1200, "Model")
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    assert H.commit(ob)
    scale_mesh(ob, 3.0)
    assert H.commit(ob)
    runtime.set_global(C, True)
    sc = C.scene
    sc.hindsight.movie_mode = True              # frames at the end: walks the history once
    goto_movie(0)
    assert camrig.frame(sc)                      # framed again at step 0, from the cache
    cam = camrig.rig(sc)["camera"]
    goto_movie(2)
    u, v, _d = view_coords(sc, cam, rendered_world(ob))
    assert u.min() >= -1e-4 and u.max() <= 1 + 1e-4 and v.min() >= -1e-4 and v.max() <= 1 + 1e-4, \
        (u.min(), u.max(), v.min(), v.max())


def test_envelope_and_reach_used():
    """The stack read from Blender: Mirror with its axes and object, Array, the rest as OTHER."""
    H.fresh()
    ob = H.make_mesh(300, "M")
    assert not camrig.has_stack(ob)
    m = ob.modifiers.new("Mirror", "MIRROR")
    m.use_axis = (False, True, True)
    a = ob.modifiers.new("Array", "ARRAY")
    a.count = 4
    ob.modifiers.new("Sub", "SUBSURF")
    off = ob.modifiers.new("Off", "DISPLACE")
    off.show_viewport = off.show_render = False
    assert camrig.has_stack(ob)
    st = camrig.stack_of(ob)
    assert [s[0] for s in st] == ["MIRROR", "ARRAY", "OTHER"], st
    assert st[0][1] == (False, True, True) and np.allclose(st[0][2], np.eye(4))
    assert st[1][1] == 4 and np.allclose(st[1][3], (1, 0, 0))
    a.use_object_offset = True
    assert camrig.stack_of(ob)[1][0] == "OTHER", "object offset is left to the reach"
    for mod in list(ob.modifiers):
        ob.modifiers.remove(mod)
    assert not camrig.has_stack(ob)
    ob.shape_key_add(name="Basis")
    assert camrig.has_stack(ob)


if __name__ == "__main__":
    for f in [test_no_modifier_reference, test_mirror_x, test_mirror_two_axes, test_mirror_object,
              test_subdivision, test_solidify_outward, test_displace, test_array, test_array_after_mirror,
              test_shape_key, test_parent_with_scale, test_geometry_nodes_scale, test_crop_report, test_scrubbed_back_then_framed,
              test_envelope_and_reach_used]:
        H.run(f)
    print("spans (share of the frame the union of every step fills; 0.9 = exact):")
    for k, s in SPANS.items():
        print(f"  {k[0]:>18} {k[1]:>5}: {s:.3f}")
    H.finish()
