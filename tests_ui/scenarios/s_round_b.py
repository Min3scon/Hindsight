"""Round B: the UI in every mode on small, large and multi-object models (generated test models).

Checks per model: the tab and the slider in Object, Sculpt, Vertex Paint and Edit Mode, no Python
errors while drawing, the sidebar redraw time, per-object histories when switching objects.
"""
import os
import time

import bpy

import common as C

MODELS = [("size_10k.blend", "Blob_10k"), ("size_1m.blend", "Blob_1m"), ("shapes.blend", "Torus"),
          ("scene_many.blend", "Sculpted_03")]


def _open(d, name):
    path = os.path.join(d.spec["repo"], "test_models", name)
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)


def _activate(d, name):
    with d.override():
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        ob = bpy.data.objects[name]
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        bpy.ops.view3d.view_selected()
    return ob


def _redraw_ms(d, n=5):
    t = time.perf_counter()
    for _ in range(n):
        d.redraw()
    return (time.perf_counter() - t) * 1000 / n


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    for fname, objname in MODELS:
        tag = fname.replace(".blend", "")
        _open(d, fname)
        yield 1.0
        ob = _activate(d, objname)
        yield from C.show_sidebar_tab(d)
        mark = d.console_mark()
        for mode in ("OBJECT", "SCULPT", "VERTEX_PAINT", "EDIT"):
            C.set_mode(d, mode)
            yield 0.6
            ms = _redraw_ms(d)
            d.note(f"{tag}_{mode}_redraw_ms", round(ms, 1))
            d.shot(f"{tag}_{mode.lower()}")
            if mode == "SCULPT":
                with d.override():
                    si = C.mod("scrubber").slider_info(bpy.context)
                d.check(f"{tag}: slider shown in Sculpt Mode", si is not None, si)
                yield from C.real_stroke(d, 0)
                i = C.info(d)
                d.check(f"{tag}: real stroke recorded", i is not None and i["len"] == 1, i)
        C.set_mode(d, "OBJECT")
        yield 0.3
        log = d.console_since(mark)
        d.check(f"{tag}: no Python errors while drawing", "Traceback" not in log, log[-1200:])
        d.check(f"{tag}: sidebar redraw under 60 ms", max(v for k, v in d.notes.items()
                                                          if k.startswith(tag) and k.endswith("_ms")) < 60,
                {k: v for k, v in d.notes.items() if k.startswith(tag)})

    # per-object histories in the multi-object scene
    a = _activate(d, "Sculpted_05")
    C.set_mode(d, "SCULPT")
    yield 0.5
    yield from C.real_stroke(d, 1)
    yield from C.real_stroke(d, 2)
    ia = C.info(d)
    b = _activate(d, "Sculpted_06")
    C.set_mode(d, "SCULPT")
    yield 0.5
    with d.override():
        sb = C.mod("scrubber").slider_info(bpy.context)
    d.check("switching objects shows the other object's (empty) history", ia["len"] == 2 and sb.get("empty"), (ia, sb))
    _activate(d, "Sculpted_05")
    C.set_mode(d, "SCULPT")
    yield 0.5
    d.check("switching back shows the first object's 2 steps", C.info(d)["len"] == 2, C.info(d))
    d.shot("multi_object_back_to_first")
