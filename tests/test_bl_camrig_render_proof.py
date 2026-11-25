"""#114 (23.4): the time lapse frame and lights, proven by rendering.

Each case makes a history, clicks Create time lapse, then plays the time lapse and renders every step
with Cycles: 192x108, one sample, a box filter 0.01 px wide (each pixel is what is at its centre) and a
transparent film, so the alpha is a flat pass that shows only what renders. CI's binary job runs this
file inside the real Blender binaries (filter `render_proof`, versions 4.2 and 5.3); the module runs
render the same way.
- at every step: no object pixel inside the margin, and the model is in the picture;
- over all steps: the content reaches the margin on one side at least (within 2 px), unless the
  panel says a safe bound was used;
- lights: with no light in the scene, each framed object rendered alone with the Hindsight lamps is lit;
- the panel names what was framed by a safe bound (not an exact measure).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import math
import subprocess
import tempfile

import bpy
import numpy as np

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests import test_bl_camrig_steps as S
from tests import test_bl_camrig_types as Y
from tests.test_bl_create_time_lapse import draw_panel
from hindsight.bl import camrig, runtime, ui_panel

C = bpy.context
W, HT = 192, 108
TMP = tempfile.mkdtemp(prefix="hs_camrig_render_")
TIGHT_PX = 2.0          # the content's outermost pixel centre lies this close to the margin, or closer


def _render(sc, samples=1):
    """(HT, W, 4) float RGBA of a Cycles render of the scene's camera."""
    sc.render.engine = "CYCLES"
    cy = sc.cycles
    cy.device = "CPU"
    cy.samples = samples
    cy.use_denoising = False
    cy.use_adaptive_sampling = False
    cy.pixel_filter_type = "BOX"
    cy.filter_width = 0.01
    r = sc.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = W, HT, 100
    r.film_transparent = True
    gp = getattr(sc, "grease_pencil_settings", None)
    if gp is not None:                  # Grease Pencil's own anti-aliasing would blur its edges by a pixel
        gp.antialias_threshold = 0.0
    sc.view_settings.view_transform = "Standard"
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGBA"
    r.filepath = os.path.join(TMP, "frame.png")
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(r.filepath, check_existing=False)
    try:
        assert tuple(img.size) == (W, HT), tuple(img.size)
        buf = np.empty(W * HT * 4, np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    return buf.reshape(HT, W, 4)


def _margins():
    """Boolean masks (rows, columns) of the pixels whose centre is inside the margin."""
    mx, my = camrig.MARGIN / 2 * W, camrig.MARGIN / 2 * HT
    cx, cy = np.arange(W) + 0.5, np.arange(HT) + 0.5
    return (cy < my) | (cy > HT - my), (cx < mx) | (cx > W - mx)


def _gap(mask):
    """Pixels from the content's outermost pixel centre to the margin, on its closest side."""
    mx, my = camrig.MARGIN / 2 * W, camrig.MARGIN / 2 * HT
    cols, rows = np.where(mask.any(0))[0] + 0.5, np.where(mask.any(1))[0] + 0.5
    return min(cols[0] - mx, W - mx - cols[-1], rows[0] - my, HT - my - rows[-1])


def _create():
    sc = C.scene
    assert bpy.ops.hindsight.create_time_lapse() == {'FINISHED'}
    assert sc.camera is not None and sc.camera is camrig.rig(sc).get("camera"), camrig.rig(sc)


def _every_step_rendered(msg, tight=True, cover=0.0):
    """Create time lapse, then render the time lapse at every step: nothing inside the margin and the
    model in the picture at each; tight: over all steps the content reaches the margin. A pixel is the
    model's when its alpha is over `cover`."""
    _create()
    sc = C.scene
    n = S.glen()
    assert n >= 2, (msg, n)
    f0, per = sc.frame_start, sc.hindsight.frames_per_step
    rows, cols = _margins()
    seen = np.zeros((HT, W), bool)
    for g in range(n + 1):
        sc.frame_set(f0 + g * per)                     # the time lapse shows step g
        assert runtime.scrub_info(C)["pos"] == g, (msg, g, runtime.scrub_info(C)["pos"])
        a = _render(sc)[:, :, 3] > cover
        assert a.sum() >= 4, f"{msg} step {g}: the model isn't in the picture ({a.sum()} px)"
        inside = int(a[rows, :].sum() + a[:, cols].sum())
        assert inside == 0, f"{msg} step {g}: {inside} object pixels inside the margin"
        seen |= a
    gap = _gap(seen)
    print(f"  {msg}: {n + 1} steps rendered, {gap:.2f} px from the margin")
    if tight:
        assert gap < TIGHT_PX, f"{msg}: not tight, {gap:.2f} px from the margin at the closest"
    return seen


def _cubes(*cubes):
    """Fresh scene, one cube (2 units wide) per (name, location, scale), recorded, global history on."""
    S._start(*cubes)


# ----------------------------------------------------------------------------- sizes
def test_a_1_mm_cube():
    _cubes(("A", (0, 0, 0), (0.0005, 0.0005, 0.0005)))
    S._run(T.translate("A", (0.002, 0, 0.001)), T.resize("A", 1.5))
    _every_step_rendered("1 mm")


def test_an_8_vertex_cube():
    _cubes(("A", (0, 0, 0), (1, 1, 1)))
    assert len(bpy.data.objects["A"].data.vertices) == 8
    S._run(T.translate("A", (3, 0, 1)), T.resize("A", 2.0))
    _every_step_rendered("8 vertices")


def test_a_normal_model():
    H.fresh()
    H.make_mesh(5000, "A")
    L.track_all()
    runtime.set_global(C, True)
    S._run(T.translate("A", (0, 2, 0)), T.rotate("A", math.radians(40), "X"), T.resize("A", 1.4))
    _every_step_rendered("normal")


def test_a_1_km_cube():
    _cubes(("A", (0, 0, 0), (500, 500, 500)))
    S._run(T.translate("A", (1500, 0, 300)), T.resize("A", 0.5))
    _every_step_rendered("1 km")


def test_a_1m_vertex_mesh():
    """A grid: Blender's UV sphere takes 4 minutes to make at 1M vertices, a grid one second."""
    H.fresh()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=1000, y_subdivisions=1000, size=4)
    ob = C.active_object
    ob.name = "A"
    assert len(ob.data.vertices) >= 1_000_000, len(ob.data.vertices)
    L.track_all()
    runtime.set_global(C, True)
    S._run(T.translate("A", (2, 0, 0)), T.resize("A", 1.5))
    _every_step_rendered("1M vertices")


# ----------------------------------------------------------------------------- object types (23.1)
def _moved_twice(ob):
    """Recorded (one move), then moved again: three steps."""
    Y._record(ob)
    S._run(T.translate(ob.name, (1.5, 0, 0.5)))
    return ob


def _curve():
    cu = bpy.data.curves.new("Rope", "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(3)
    for p, co in zip(sp.points, ((0, 0, 0), (3, 0, 1), (3, 2, 0), (0, 4, 2))):
        p.co = (*co, 1.0)
    cu.bevel_depth = 0.6
    return Y._link("Rope", cu)


def _text():
    cu = bpy.data.curves.new("Words", "FONT")
    cu.body = "Hindsight"
    cu.extrude = 0.2
    ob = Y._link("Words", cu)
    ob.rotation_euler = (math.radians(90), 0, 0)
    return ob


def _surface():
    bpy.ops.surface.primitive_nurbs_surface_torus_add(radius=2.0, location=(1, 2, 0))
    return C.object


def _metaball():
    mb = bpy.data.metaballs.new("Blob")
    for co in ((0, 0, 0), (2.5, 0, 0.5)):
        el = mb.elements.new()
        el.co = co
        el.radius = 1.5
    return Y._link("Blob", mb)


def _grease_pencil():
    g = bpy.data.grease_pencils.new("Sketch")
    fr = g.layers.new("Lines").frames.new(C.scene.frame_current)
    co = np.array([(0, 0, 0), (2, 0, 1), (2, 0, 3), (-1, 0, 2), (0, 1, 4)], np.float32)
    ob = Y._link("Sketch", g)
    if hasattr(fr, "drawing"):
        fr.drawing.add_strokes([len(co)])
        fr.drawing.attributes["position"].data.foreach_set("vector", co.reshape(-1))
        rad = fr.drawing.attributes.get("radius") or fr.drawing.attributes.new("radius", "FLOAT", "POINT")
        rad.data.foreach_set("value", np.full(len(co), 0.1, np.float32))
    else:
        s = fr.strokes.new()
        s.points.add(len(co))
        s.points.foreach_set("co", co.reshape(-1))
    return ob


def _hair_curves(radius=0.3):
    hc = bpy.data.hair_curves.new("Hair")
    hc.add_curves([3, 3])
    co = np.array([(0, 0, 0), (0, 0.5, 2), (0, 1, 3), (2, 0, 0), (2.5, 0, 1.5), (3, 0, 2.5)], np.float32)
    hc.attributes["position"].data.foreach_set("vector", co.reshape(-1))
    rad = hc.attributes.new("radius", "FLOAT", "POINT")
    rad.data.foreach_set("value", np.broadcast_to(np.asarray(radius, np.float32), len(co)).copy())
    return Y._link("Hair", hc)


def _point_cloud():
    pc = bpy.data.pointclouds.new("Dots")
    if not hasattr(pc, "resize"):
        return None                     # 4.2: no way to add points to a point cloud from Python
    co = np.array([(0, 0, 0), (3, 1, 0), (1, 3, 2), (-1, 1, 1)], np.float32)
    pc.resize(len(co))
    pc.attributes["position"].data.foreach_set("vector", co.reshape(-1))
    rad = pc.attributes.get("radius") or pc.attributes.new("radius", "FLOAT", "POINT")
    rad.data.foreach_set("value", np.full(len(co), 0.4, np.float32))
    return Y._link("Dots", pc)


def _volume():
    src = H.make_mesh(300, "Source", (0, 0, 0))
    src.hide_render = True
    vol = Y._link("Fog", bpy.data.volumes.new("Fog"))
    vol.modifiers.new("ToVolume", "MESH_TO_VOLUME").object = src
    mat = bpy.data.materials.new("Fog")             # Cycles shows a volume with no material as nothing
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    nt.links.new(nt.nodes.new("ShaderNodeVolumePrincipled").outputs[0], out.inputs["Volume"])
    vol.data.materials.append(mat)
    return vol


def _instancer():
    coll = bpy.data.collections.new("Parts")
    for name, loc in (("PartA", (0, 0, 0)), ("PartB", (4, 0, 1))):
        o = H.make_mesh(300, name, loc)
        C.scene.collection.objects.unlink(o)
        coll.objects.link(o)
    e = Y._link("Instancer", None)
    e.instance_type = "COLLECTION"
    e.instance_collection = coll
    e.location = (10, 5, 0)
    return e


def _type_case(make, msg, tight=True):
    Y._start()
    ob = make()
    if ob is None:
        print(f"  ({msg}: can't be made from Python here, nothing to render)")
        return None
    _moved_twice(ob)
    return _every_step_rendered(msg, tight)


def test_a_curve():
    _type_case(_curve, "curve")


def test_a_text():
    _type_case(_text, "text")


def test_a_surface():
    _type_case(_surface, "surface")


def test_a_metaball():
    _type_case(_metaball, "metaball")


def test_hair_curves_held_by_a_bound():
    """Cycles draws a hair's ends flat, so the ball around each point is a bound there: never cut off,
    not tight (5.3, before: 5.1 px short of the margin while claimed exact)."""
    _type_case(_hair_curves, "hair curves", tight=False)
    assert camrig.bounded(C.scene) == ("Hair",), camrig.bounded(C.scene)


def test_a_point_cloud():
    _type_case(_point_cloud, "point cloud")


def test_an_empty_instancing_a_collection():
    _type_case(_instancer, "instancing empty")


def test_a_volume_held_by_a_bound():
    """Its box: never cut off, not tight."""
    _type_case(_volume, "volume", tight=False)


def _grease_pencil_case():
    """Grease Pencil is drawn by its own engine where Blender can make a GPU context (in some background
    runs it renders nothing: then test_bl_camrig_types checks its frame, not this). Its strokes have a
    soft edge about a pixel wide (5.3: alpha 0.03 to 0.06 just past a tight margin), so a pixel is the
    stroke's when half covered or more, as for any anti-aliased edge."""
    Y._start()
    ob = _moved_twice(_grease_pencil())
    _create()
    sc = C.scene
    sc.frame_set(sc.frame_start)
    if not (_render(sc)[:, :, 3] > 0).any():
        print("  (Grease Pencil renders nothing in this background Blender: checked without rendering)")
        return
    _every_step_rendered("Grease Pencil", tight=ob.type == "GREASEPENCIL", cover=0.5)


def test_grease_pencil():
    """_grease_pencil_case in a Blender of its own, judged by what it prints: the GPU context it makes
    crashed the 5.3 bpy module at exit on Windows, after the case had passed."""
    exe, me = bpy.app.binary_path, os.path.abspath(__file__)
    cmd = [exe, "-b", "--factory-startup", "--python", me] if exe else [sys.executable, me]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, env=dict(os.environ, HS_RENDER_GP="1"))
    for ln in r.stdout.splitlines():
        if "Grease Pencil" in ln and ln.startswith("  "):
            print(ln)
    assert "PASS _grease_pencil_case" in r.stdout, (r.stdout[-3000:], r.stderr[-2000:])


# ----------------------------------------------------------------------------- lights
def _alone(ob, others):
    """hide_render on everything else that renders, without recording it."""
    with runtime.suspended():
        for o in others:
            o.hide_render = o is not ob


def test_with_no_scene_light_every_framed_object_is_lit():
    """No light and no world: only the Hindsight lamps light the model. Each framed object, rendered
    alone at the last step, shows its lit side: no black object. (Grease Pencil strokes have their own
    flat colours and a volume no solid side, so they aren't in it.)"""
    Y._start()
    made = [_curve(), _text(), _metaball(), _hair_curves(), _instancer(), _surface()]
    H.make_mesh(2000, "Ball", (-4, 0, 0))
    made.append(bpy.data.objects["Ball"])
    pc = _point_cloud()
    if pc is not None:
        made.append(pc)
    spread = [(0, 0, 0), (0, 6, 0), (6, 0, 0), (-6, 6, 0), (0, -6, 2), (-6, -6, 0), (6, 6, 0), (6, -6, 0)]
    for ob, at in zip(made, spread):
        ob.location = at
        Y._record(ob)
    sc = C.scene
    sc.world = None
    assert not camrig.scene_has_own_light(sc)
    _create()
    assert set(camrig.LIGHT_ROLES) <= set(camrig.rig(sc)), camrig.rig(sc)
    framed = camrig.model_objects(sc)
    assert set(made) <= set(framed), sorted(o.name for o in framed)
    sc.frame_set(sc.frame_start + S.glen() * sc.hindsight.frames_per_step)
    out = []
    try:
        for ob in framed:
            _alone(ob, framed)
            px = _render(sc, samples=16)
            body = px[px[:, :, 3] > 0.99]
            assert len(body) >= 10, f"{ob.name}: {len(body)} px in the picture"
            lum = body[:, :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
            dark = float((lum < 0.02).mean())
            out.append(f"{ob.name} {np.median(lum):.2f}/{dark:.0%}")
            assert np.median(lum) > 0.1 and dark < 0.1, \
                f"{ob.name} renders black: median brightness {np.median(lum):.3f}, {dark:.0%} of it black"
    finally:
        _alone(None, [])
        with runtime.suspended():
            for o in framed:
                o.hide_render = False
    print("  lit (median brightness / share black):", ", ".join(out))


# ----------------------------------------------------------------------------- the panel
def _panel_text():
    lay = draw_panel(ui_panel.VIEW3D_PT_hindsight_movie, C)
    return " ".join(str(kw.get("text", "")) for name, _a, kw in lay.log if name == "label")


def test_the_panel_says_when_a_safe_bound_was_used():
    Y._start()
    _moved_twice(_volume())
    _create()
    words = _panel_text()
    assert "safe bound" in words and "Fog" in words, words
    assert camrig.bounded(C.scene) == ("Fog",), camrig.bounded(C.scene)


def test_another_file_doesnt_show_the_last_files_bound():
    """Its scene has the same name: the panel says nothing of a bound there until it is placed."""
    Y._start()
    empty = os.path.join(TMP, "empty.blend")
    bpy.ops.wm.save_as_mainfile(filepath=empty, copy=True)
    _moved_twice(_volume())
    _create()
    assert camrig.bounded(C.scene) == ("Fog",), camrig.bounded(C.scene)
    name = C.scene.name_full
    bpy.ops.wm.open_mainfile(filepath=empty)
    assert C.scene.name_full == name, (C.scene.name_full, name)
    assert camrig.bounded(C.scene) == (), camrig.bounded(C.scene)


def test_the_panel_says_nothing_of_a_bound_when_everything_is_exact():
    _cubes(("A", (0, 0, 0), (1, 1, 1)))
    S._run(T.translate("A", (3, 0, 1)))
    _create()
    assert camrig.bounded(C.scene) == (), camrig.bounded(C.scene)
    assert "safe bound" not in _panel_text()


def test_points_of_different_radii_are_held_by_a_bound():
    """The largest radius stands for all of them: a bound, and the panel says so."""
    Y._start()
    pc = _point_cloud()
    if pc is None:
        print("  (4.2: no way to add points to a point cloud from Python)")
        return
    pc.data.attributes["radius"].data.foreach_set("value", np.array([0.1, 0.1, 0.5, 0.1], np.float32))
    _moved_twice(pc)
    _create()
    assert camrig.bounded(C.scene) == ("Dots",), camrig.bounded(C.scene)
    assert "Dots" in _panel_text()


def test_a_point_cloud_of_one_radius_is_exact():
    Y._start()
    pc = _point_cloud()
    if pc is None:
        print("  (4.2: no way to add points to a point cloud from Python)")
        return
    _moved_twice(pc)
    _create()
    assert camrig.bounded(C.scene) == (), camrig.bounded(C.scene)


if __name__ == "__main__" and os.environ.get("HS_RENDER_GP"):
    H.run(_grease_pencil_case)
    H.finish()
elif __name__ == "__main__":
    for t in (test_a_1_mm_cube, test_an_8_vertex_cube, test_a_normal_model, test_a_1_km_cube,
              test_a_1m_vertex_mesh, test_a_curve, test_a_text, test_a_surface, test_a_metaball,
              test_hair_curves_held_by_a_bound, test_a_point_cloud, test_an_empty_instancing_a_collection,
              test_a_volume_held_by_a_bound, test_grease_pencil,
              test_with_no_scene_light_every_framed_object_is_lit,
              test_the_panel_says_when_a_safe_bound_was_used,
              test_another_file_doesnt_show_the_last_files_bound,
              test_the_panel_says_nothing_of_a_bound_when_everything_is_exact,
              test_points_of_different_radii_are_held_by_a_bound,
              test_a_point_cloud_of_one_radius_is_exact):
        H.run(t)
    H.finish()
