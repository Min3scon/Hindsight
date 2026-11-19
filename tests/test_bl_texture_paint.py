"""Phase 3 P4: texture painting per stroke (preference "Record texture painting", off by default).
A stroke in Texture Paint mode is a step of the painted object holding the pixels of the images its
materials paint (float RGBA, native diff and pack); going back and forward is exact for byte and
float images; images over 2K are left out; with the preference off nothing is recorded.

Strokes are made through the API (pixels written, then the recorder's stroke-end path, as the other
paint tests do): brush operators can't run headless. Not tried in a real window."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import numpy as np
import tests.harness as H
from hindsight.bl import meshio, runtime
from hindsight.core import step as stepmod

C = bpy.context
NAME = "Canvas"


def pixels(img):
    a = np.empty(img.size[0] * img.size[1] * 4, np.float32)
    img.pixels.foreach_get(a)
    return a


def setup(side=64, float_buffer=False, second=False):
    H.fresh()
    ob = H.make_mesh(800, name=NAME)
    mat = bpy.data.materials.new("Paint")
    mat.use_nodes = True
    ob.data.materials.append(mat)
    imgs = []
    for i in range(2 if second else 1):
        img = bpy.data.images.new(f"Tex{i}", side, side, alpha=True, float_buffer=float_buffer)
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = img
        imgs.append(img)
    return ob, imgs


def enter(ob):
    runtime.FORCE_TEXTURE[0] = True
    H.enter(ob, "TEXTURE_PAINT")


def paint(img, seed, rows=(10, 25)):
    rng = np.random.default_rng(seed)
    a = pixels(img).reshape(img.size[1], img.size[0], 4)
    a[rows[0]:rows[1], 5:40] = rng.random((rows[1] - rows[0], 35, 4)).astype(np.float32)
    img.pixels.foreach_set(a.reshape(-1))
    img.update()


def check_exact(ob, imgs, states):
    h = runtime.history_of(ob)
    for pos in [len(states) - 1, 1, 0, 2, len(states) - 1, 0, 1]:
        runtime.goto(C, pos)
        assert h.cursor == pos
        for img, want in zip(imgs, states[pos]):
            assert pixels(img).tobytes() == want.tobytes(), f"{img.name} at position {pos}"


def run_case(float_buffer, second=False):
    ob, imgs = setup(float_buffer=float_buffer, second=second)
    enter(ob)
    assert meshio.paint_images(ob), "the material's image isn't a paint image here"
    states = [[pixels(i) for i in imgs]]
    for k in range(3):
        paint(imgs[k % len(imgs)], 100 + k)
        assert H.commit(ob) is not None, f"stroke {k} made no step"
        states.append([pixels(i) for i in imgs])
    h = runtime.history_of(ob)
    assert len(h.entries) == 3 and all(e.meta["mode"] == "TEXTURE_PAINT" for e in h.entries)
    assert all(any(d.key.startswith("img:") for d in e.load().deltas) for e in h.entries)
    check_exact(ob, imgs, states)
    return ob, imgs, h


def test_off_by_default_records_nothing():
    runtime.FORCE_TEXTURE[0] = None
    ob, imgs = setup()
    H.enter(ob, "TEXTURE_PAINT")
    paint(imgs[0], 1)
    assert not runtime.texture_on()
    assert H.commit(ob) is None
    h = runtime.history_of(ob)
    assert h is None or not h.entries
    p = bpy.types.AddonPreferences
    from hindsight.bl import prefs
    assert prefs.HINDSIGHT_AP_prefs.__annotations__["record_texture_painting"].keywords["default"] is False
    print("PASS test_off_by_default_records_nothing")


def test_byte_image_strokes_are_exact():
    run_case(False)
    print("PASS test_byte_image_strokes_are_exact")


def test_float_image_strokes_are_exact():
    run_case(True)
    print("PASS test_float_image_strokes_are_exact")


def test_two_images_on_one_object():
    run_case(False, second=True)
    print("PASS test_two_images_on_one_object")


def test_images_over_2k_are_left_out():
    ob, imgs = setup(side=2100)
    enter(ob)
    assert not meshio.paint_images(ob)
    paint(imgs[0], 3, rows=(10, 12))
    assert H.commit(ob) is None, "an image over 2K was recorded"
    print("PASS test_images_over_2k_are_left_out")


def test_steps_are_small_and_counted():
    ob, imgs, h = run_case(False)
    big = max(e.nbytes() for e in h.entries)
    assert big < 64 * 64 * 16, f"a stroke step holds {big} bytes: not just the changed pixels"
    print("PASS test_steps_are_small_and_counted")


def test_leaving_the_mode_and_painting_again():
    ob, imgs = setup()
    enter(ob)
    paint(imgs[0], 1)
    assert H.commit(ob) is not None
    H.enter(ob, "OBJECT")
    paint(imgs[0], 2)                                   # changed outside any stroke
    enter(ob)
    n = len(runtime.history_of(ob).entries)
    want0 = pixels(imgs[0])
    paint(imgs[0], 3)
    assert H.commit(ob) is not None
    h = runtime.history_of(ob)
    assert len(h.entries) == n + 1
    runtime.goto(C, n)
    assert pixels(imgs[0]).tobytes() == want0.tobytes(), "the step holds more than the stroke"
    print("PASS test_leaving_the_mode_and_painting_again")


if __name__ == "__main__":
    for t in (test_off_by_default_records_nothing, test_byte_image_strokes_are_exact,
              test_float_image_strokes_are_exact, test_two_images_on_one_object, test_images_over_2k_are_left_out,
              test_steps_are_small_and_counted, test_leaving_the_mode_and_painting_again):
        H.run(t)
    runtime.FORCE_TEXTURE[0] = None
    H.finish()
