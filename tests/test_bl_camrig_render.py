"""Movie Mode camera frames what a RENDER shows, not what the viewport shows (review S2-1, follow-up).

The viewport's evaluated mesh isn't what renders when a modifier is on for renders only (or for the
viewport only), when Subdivision or Multires have other render levels, or when the object isn't
evaluated in the viewport at all (disabled in viewports, or its collection is). The camera must hold
the rendered model at every step of the history all the same. The oracle here evaluates a copy of
the object with every modifier set the way a render uses it.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import bpy
import numpy as np

import tests.harness as H
from hindsight.bl import camrig, runtime
from hindsight.bl import prefs
import test_bl_camrig_modifiers as T

C = bpy.context


def render_world(ob):
    """World positions of what a render shows of `ob` (oracle)."""
    if ob.mode == "EDIT":
        ob.update_from_editmode()
    with runtime.suspended():
        tmp = ob.copy()
        tmp.data = ob.data.copy()
        tmp.hide_viewport = False
        for m in tmp.modifiers:
            m.show_viewport = m.show_render
            if m.type in ("SUBSURF", "MULTIRES"):
                m.levels = m.render_levels
                if m.type == "MULTIRES":
                    m.sculpt_levels = m.render_levels
        C.scene.collection.objects.link(tmp)
        try:
            C.view_layer.update()
            pts = T._evaluated_world(tmp)
        finally:
            me = tmp.data
            bpy.data.objects.remove(tmp)
            bpy.data.meshes.remove(me)
    return pts


T.rendered_world = render_world


def in_frame(ob, msg):
    sc = C.scene
    cam = camrig.rig(sc)["camera"]
    u, v, depth = T.view_coords(sc, cam, render_world(ob))
    assert (depth > 0).all(), msg
    m = camrig.MARGIN / 2 - 1e-3
    assert u.min() >= m and u.max() <= 1 - m and v.min() >= m and v.max() <= 1 - m, \
        f"{msg}: {u.min():.3f} {u.max():.3f} {v.min():.3f} {v.max():.3f}"


def with_history(name, setup, after=None):
    for early in ("big", "small"):
        H.fresh()
        ob = H.make_mesh(1200, "Model")
        setup(ob)
        C.view_layer.update()
        n = T.make_history(ob, early)
        if after is not None:
            bpy.ops.object.mode_set(mode="OBJECT")
            after(ob)
        T.check_every_step(ob, n, False, f"{name}/{early}", 0.3)


def test_render_only_displace():
    def setup(ob):
        m = ob.modifiers.new("Displace", "DISPLACE")
        m.strength = 0.8
        m.show_viewport = False
    with_history("render-only displace", setup)


def test_hidden_in_viewports():
    """The model is disabled in viewports (it still renders): the viewport doesn't evaluate it."""
    def setup(ob):
        ob.modifiers.new("Displace", "DISPLACE").strength = 0.8

    def hide(ob):
        ob.hide_viewport = True
    with_history("disabled in viewports", setup, hide)


def test_collection_hidden_in_viewports():
    def setup(ob):
        ob.modifiers.new("Displace", "DISPLACE").strength = 0.8

    def hide(ob):
        coll = bpy.data.collections.new("Sculpt")
        C.scene.collection.children.link(coll)
        coll.objects.link(ob)
        C.scene.collection.objects.unlink(ob)
        coll.hide_viewport = True
    with_history("collection disabled in viewports", setup, hide)


def _no_history(setup):
    H.fresh()
    ob = H.make_mesh(1200, "Model")
    setup(ob)
    C.view_layer.update()
    assert runtime.history_of(ob) is None
    C.scene.hindsight.movie_mode = True
    return ob


def test_render_copy_budget():
    """A render copy bigger than COPY_BUDGET vertices isn't made (a Multires rendered several levels
    above what the viewport shows could take gigabytes): the viewport's mesh is framed instead,
    without an error; under the budget the copy is made."""
    made = []
    real = camrig._render_copy_points

    def spy(*a, **k):
        made.append(1)
        return real(*a, **k)
    camrig._render_copy_points = spy
    budget = camrig.COPY_BUDGET
    try:
        H.fresh()
        ob = H.make_mesh(1200, "Model")
        m = ob.modifiers.new("Subdivision", "SUBSURF")
        m.levels, m.render_levels = 0, 3
        C.scene.hindsight.movie_mode = True
        assert made, "under the budget: the render copy is evaluated"
        made.clear()
        camrig.COPY_BUDGET = len(ob.data.vertices) * 63
        assert camrig.frame(C.scene)
        assert not made, "over the budget: no copy"
        camrig.COPY_BUDGET = len(ob.data.vertices) * 64
        assert camrig.frame(C.scene) and made
    finally:
        camrig.COPY_BUDGET = budget
        camrig._render_copy_points = real


def test_viewport_only_subdivision_without_history():
    """A mesh without a history (framed as it is now): the render shows the cage, which is bigger
    than the subdivided surface the viewport shows."""
    ob = _no_history(lambda ob: None)
    m = ob.modifiers.new("Subdivision", "SUBSURF")
    m.levels = 3
    m.show_render = False
    co = np.empty(len(ob.data.vertices) * 3, np.float32)
    ob.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    co[co[:, 0] > 0.9, 0] += 0.8                        # a spike the subdivided surface shrinks a lot
    ob.data.vertices.foreach_set("co", co.ravel())
    ob.data.update()
    assert camrig.frame(C.scene)
    in_frame(ob, "viewport-only subdivision")


def test_multires_render_levels_without_history():
    """Multires shown at level 2 in the viewport, rendered at level 0 (the cage, with a spike the
    smoothed levels pull in)."""
    ob = _no_history(lambda ob: None)
    co = np.empty(len(ob.data.vertices) * 3, np.float32)
    ob.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    co[co[:, 0] > 0.9, 0] += 0.8
    ob.data.vertices.foreach_set("co", co.ravel())
    ob.data.update()
    C.view_layer.objects.active = ob
    ob.modifiers.new("Multires", "MULTIRES")
    for _ in range(2):
        bpy.ops.object.multires_subdivide(modifier="Multires", mode="CATMULL_CLARK")
    m = ob.modifiers["Multires"]
    m.levels = 2
    m.render_levels = 0
    assert camrig.frame(C.scene)
    in_frame(ob, "multires render levels")


def test_nothing_changed_or_left_behind():
    """Framing through the render copy leaves the user's data, the histories and bpy.data as they
    were: no object, mesh or step added (the scene and the model recorded with every kind of
    data), no cursor moved, the modifiers' settings untouched."""
    from hindsight.bl import datarec
    H.fresh()
    ob = H.make_mesh(1200, "Model")
    m = ob.modifiers.new("Displace", "DISPLACE")
    m.strength = 0.5
    m.show_viewport = False
    s = ob.modifiers.new("Subdivision", "SUBSURF")
    s.levels, s.render_levels = 0, 2
    T.make_history(ob, "plain")
    bpy.ops.object.mode_set(mode="OBJECT")
    runtime.set_global(C, True)
    sc = C.scene
    prefs.FORCE["record_scope"] = "EVERYTHING"
    prefs.FORCE["record_kinds"] = None
    datarec.set_tracked(sc, True)
    datarec.set_tracked(ob, True)
    datarec.finish_operation("start")

    def hist():
        return sorted((h.uid, h.cursor, len(h.entries)) for h in runtime.reg().histories.values())

    def now():
        return hist(), runtime.scrub_info(C)["pos"], runtime.scrub_info(C)["len"]
    try:
        sc.hindsight.movie_mode = True
        datarec.finish_operation("Movie Mode")
        before = now()
        n_obj, n_me = len(bpy.data.objects), len(bpy.data.meshes)
        names = sorted(o.name for o in bpy.data.objects)
        for _ in range(3):
            assert camrig.frame(sc)
            datarec.finish_operation("frame")
        C.view_layer.update()
        datarec.finish_operation("frame")
        assert len(bpy.data.objects) == n_obj and len(bpy.data.meshes) == n_me
        assert sorted(o.name for o in bpy.data.objects) == names
        assert (m.show_viewport, m.show_render, s.levels, s.render_levels) == (False, True, 0, 2)
        assert ob.users_collection[0] == sc.collection and len(sc.collection.objects) == n_obj
        assert now() == before, (now(), before)
        sc.hindsight.movie_mode = False
        assert not camrig.rig(sc)
        datarec.finish_operation("Movie Mode off")
        # the check has teeth: an object of the user's added the same way is recorded
        n = len(runtime.scrub_info(C) and hist())
        extra = ob.copy()
        sc.collection.objects.link(extra)
        datarec.finish_operation("add")
        assert hist() != before[0] or len(hist()) != n, "the recorder didn't see an added object"
    finally:
        prefs.FORCE["record_scope"] = None


def test_cache_not_kept_across_files():
    """The history walk is cached per history and object id with the history's length and step
    numbers. Two files saved from one session can hold histories with the same ids, length and
    step numbers but other shapes: the cache of the first must not frame the second."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="hs_camrig_cache_")
    H.fresh()
    ob = H.make_mesh(1200, "Model")
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    assert H.commit(ob)
    bpy.ops.object.mode_set(mode="OBJECT")
    runtime.set_global(C, True)
    a = os.path.join(tmp, "a.blend")
    b = os.path.join(tmp, "b.blend")
    bpy.ops.wm.save_as_mainfile(filepath=a)
    H.enter(ob, "SCULPT")
    T.scale_mesh(ob, 3.0)                       # B: a second step three times as big
    assert H.commit(ob)
    T.scale_mesh(ob, 1.0 / 3.0)
    assert H.commit(ob)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.wm.save_as_mainfile(filepath=b)
    bpy.ops.wm.open_mainfile(filepath=a)        # A: two small steps, same step numbers as B's
    ob = bpy.data.objects["Model"]
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 2)
    assert H.commit(ob)
    H.fake_stroke(ob, 3)
    assert H.commit(ob)
    bpy.ops.object.mode_set(mode="OBJECT")
    C.scene.hindsight.movie_mode = True         # frames A: the walk is cached
    bpy.ops.wm.open_mainfile(filepath=b)
    ob = bpy.data.objects["Model"]
    n = runtime.scrub_info(C)["len"]
    T.check_every_step(ob, n, False, "file B after file A", 0.3)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_framed_in_edit_mode():
    """Movie Mode switched on (or the camera framed) while the model is in Edit Mode: the history
    still counts, with the modifiers, and so does the mesh being edited."""
    for mods in ("none", "mirror", "armature"):
        H.fresh()
        ob = H.make_mesh(1200, "Model")
        if mods == "mirror":
            T.half(ob)
            ob.modifiers.new("Mirror", "MIRROR")
        elif mods == "armature":
            import test_bl_camrig_deform as D
            D.rig_armature(ob, 60.0)
        n = T.make_history(ob, "big")
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.transform.translate(value=(0.0, 0.0, 1.5))     # moved up in Edit Mode, not recorded yet
        sc = C.scene
        sc.hindsight.movie_mode = True
        cam = camrig.rig(sc)["camera"]
        in_frame(ob, f"edit mode now, modifiers {mods}")
        m0 = cam.matrix_world.copy()
        bpy.ops.object.mode_set(mode="OBJECT")
        sc.hindsight.movie_mode = False
        sc.hindsight.movie_mode = True
        sc.hindsight.movie_mode = False
        bpy.ops.object.mode_set(mode="EDIT")
        sc.hindsight.movie_mode = True                         # framed in Edit Mode again
        assert (cam := camrig.rig(sc)["camera"]) is not None
        bpy.ops.object.mode_set(mode="OBJECT")
        for pos in range(n + 1):
            T.goto_movie(pos)
            in_frame(ob, f"edit mode, step {pos}, modifiers {mods}")
        sc.hindsight.movie_mode = False
        del m0


if __name__ == "__main__":
    for f in [test_render_only_displace, test_hidden_in_viewports, test_collection_hidden_in_viewports,
              test_render_copy_budget, test_viewport_only_subdivision_without_history, test_multires_render_levels_without_history,
              test_nothing_changed_or_left_behind, test_cache_not_kept_across_files,
              test_framed_in_edit_mode]:
        H.run(f)
    H.finish()
