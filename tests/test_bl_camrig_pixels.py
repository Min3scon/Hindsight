"""A real Cycles frame: the rendered model (half model + Mirror) never touches the border (review S2-1).

64x36 pixels, 1 sample, CPU, transparent film: any pixel with alpha > 0 is the model. At every step
of the history the outer ring of pixels is empty and the model is there.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import tempfile

import bpy
import numpy as np

import tests.harness as H
from hindsight.bl import camrig
import test_bl_camrig_modifiers as T

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hs_camrig_px_")


def render_alpha(sc):
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 1
    sc.cycles.use_denoising = False
    sc.render.resolution_x, sc.render.resolution_y = 64, 36
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.render.filepath = os.path.join(TMP, "px.png")
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(sc.render.filepath)
    buf = np.empty(img.size[0] * img.size[1] * 4, np.float32)
    img.pixels.foreach_get(buf)
    assert tuple(img.size) == (64, 36), tuple(img.size)
    bpy.data.images.remove(img)
    return buf.reshape(36, 64, 4)[:, :, 3]


def test_mirror_model_clear_of_the_border():
    for early in ("big", "small"):
        H.fresh()
        ob = H.make_mesh(1200, "Model")
        T.half(ob)
        ob.modifiers.new("Mirror", "MIRROR")
        C.view_layer.update()
        n = T.make_history(ob, early)
        sc = C.scene
        sc.hindsight.movie_mode = True
        sc.render.resolution_x, sc.render.resolution_y = 64, 36
        camrig.frame(sc)
        for pos in list(range(n, -1, -1)) + [n]:
            T.goto_movie(pos)
            a = render_alpha(sc)
            ring = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
            assert a.max() > 0.5 and (a > 0.5).sum() > 20, f"{early} step {pos}: no model in the frame"
            assert ring.max() == 0.0, f"{early} step {pos}: model touches the border ({ring.max():.2f})"
        sc.hindsight.movie_mode = False


if __name__ == "__main__":
    H.run(test_mirror_model_clear_of_the_border)
    H.finish()
