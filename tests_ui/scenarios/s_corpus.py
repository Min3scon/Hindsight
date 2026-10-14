"""Record real strokes into a corpus for the compression benchmark (bench/bench_codec.py).

args:  model=size_100k.blend  obj=Blob_100k  tag=100k  remesh=0 (voxel remesh first, for a
       different vertex order)  subsurf=0 (build the mesh by subdividing a coarse copy instead)

Strokes go through the real input path (simulated mouse) with several brushes and three brush
sizes, so the stored deltas are what a sculptor's session produces. Writes
.tmp/corpus/<tag>.npz: the starting mesh channels and every recorded delta (indices and XOR bits),
plus <tag>.json with labels, the touched fraction and the delta form (op: 0 XOR, 1 ADD) per step. Generated test models only.
"""
import json
import math
import os
import random

import bpy
import numpy as np

import common as C

BRUSHES = ["Draw", "Clay Strips", "Smooth", "Inflate/Deflate", "Grab", "Crease Sharp", "Layer",
           "Flatten/Contrast", "Scrape/Fill", "Pinch/Magnify", "Draw Sharp", "Blob", "Clay Thumb",
           "Snake Hook", "Elastic Grab", "Mask", "Face Set Paint", "Paint Hard", "Paint Soft"]


def set_brush(name):
    for lib in ("brushes/essentials_brushes-mesh_sculpt.blend/Brush/",):
        try:
            r = bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS', asset_library_identifier="",
                                             relative_asset_identifier=lib + name)
            if r == {'FINISHED'}:
                return True
        except Exception as e:
            print("brush", name, "failed:", str(e)[:200])
    return False


def set_radius(px):
    ts = bpy.context.tool_settings
    sc = ts.sculpt
    for owner in (getattr(sc, "unified_paint_settings", None), getattr(ts, "unified_paint_settings", None)):
        if owner is not None:
            try:
                owner.size = int(px)
            except Exception:
                pass
    try:
        if sc.brush is not None:
            sc.brush.size = int(px)
    except Exception:
        pass


def stroke_points(reg, rng, kind):
    cx, cy = reg.width / 2, reg.height / 2
    if kind == "small":
        x0 = cx + rng.uniform(-0.12, 0.12) * reg.width
        y0 = cy + rng.uniform(-0.18, 0.18) * reg.height
        ang = rng.uniform(0, 2 * math.pi)
        L = rng.uniform(220, 420)
        n = 14
        return [(x0 + math.cos(ang) * L * i / (n - 1) + math.sin(i * 0.7) * 6,
                 y0 + math.sin(ang) * L * i / (n - 1)) for i in range(n)]
    if kind == "medium":
        x0 = cx - reg.width * 0.18
        y0 = cy + rng.uniform(-0.15, 0.15) * reg.height
        n = 24
        return [(x0 + reg.width * 0.36 * i / (n - 1), y0 + math.sin(i / (n - 1) * math.pi * 2) * 60)
                for i in range(n)]
    # wide (about 10%): two passes; large (about 30%): zigzag over most of the visible surface
    pts = []
    rows = 2 if kind == "wide" else 5
    for r in range(rows):
        y = cy - reg.height * 0.3 + reg.height * 0.6 * r / (rows - 1)
        if kind == "wide":
            y = cy + rng.uniform(-0.1, 0.1) * reg.height + (r - 0.5) * reg.height * 0.18
        xs = [cx - reg.width * 0.2, cx + reg.width * 0.2]
        if r % 2:
            xs.reverse()
        for i in range(10):
            pts.append((xs[0] + (xs[1] - xs[0]) * i / 9, y))
    return pts


def dump(d, ob, base, tag):
    rt = C.mod("runtime")
    h = rt.history_of(ob)
    out_dir = os.path.join(d.spec["repo"], ".tmp", "corpus")
    os.makedirs(out_dir, exist_ok=True)
    arrays = {}
    for k, v in base.arrays.items():
        arrays["base|" + k] = v
    meta = {"tag": tag, "counts": base.counts, "steps": []}
    for i, e in enumerate(h.entries):
        st = e.load()
        sm = {"label": st.label, "kind": st.kind, "deltas": [], "extra": d._labels.get(i, "")}
        for j, dl in enumerate(st.deltas):
            arrays[f"s{i}_{j}_idx"] = np.asarray(dl.idx)
            arrays[f"s{i}_{j}_xor"] = np.asarray(dl.vals)
            sm["deltas"].append({"key": dl.key, "exists": [bool(dl.exists[0]), bool(dl.exists[1])],
                                 "k": int(dl.idx.size), "op": int(getattr(dl, "op", 0))})
        meta["steps"].append(sm)
    np.savez(os.path.join(out_dir, tag + ".npz"), **arrays)
    with open(os.path.join(out_dir, tag + ".json"), "w") as f:
        json.dump(meta, f, indent=1)
    return len(h.entries)


def run(d):
    a = d.args
    model, objname, tag = a.get("model", "size_100k.blend"), a.get("obj", "Blob_100k"), a.get("tag", "100k")
    rng = random.Random(7)
    d._labels = {}
    yield 1.0
    C.dismiss_splash(d)
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=os.path.join(d.spec["repo"], "test_models", model), load_ui=False)
    yield 1.0
    rt = C.mod("runtime")
    try:
        rt.SETTINGS.raw_steps = 100000
    except Exception:
        pass
    with d.override():
        ob = bpy.data.objects[objname]
        for o in bpy.context.view_layer.objects:
            o.select_set(o == ob)
        bpy.context.view_layer.objects.active = ob
        if a.get("remesh", "0") == "1":
            dims = ob.dimensions
            area = 4 * math.pi * (sum(dims) / 6) ** 2
            ob.data.remesh_voxel_size = math.sqrt(area / len(ob.data.vertices))
            bpy.ops.object.voxel_remesh()
        if a.get("subsurf", "0") == "1":
            m = ob.modifiers.new("Decimate", 'DECIMATE')
            m.decimate_type = 'UNSUBDIV'
            m.iterations = 2
            bpy.ops.object.modifier_apply(modifier=m.name)
            m = ob.modifiers.new("Subdivision", 'SUBSURF')
            m.levels = 2
            bpy.ops.object.modifier_apply(modifier=m.name)
        bpy.ops.view3d.view_selected()
    d.note("verts", len(ob.data.vertices))
    C.set_mode(d, "SCULPT")
    yield 0.6
    base = C.mod("meshio").read_snapshot(ob.data, sculpt_only=True)
    win, area, reg, ui, space = d.view3d()
    yield from C.warm_up(d)

    plan = []
    for b in BRUSHES:
        plan += [(b, "small", rng.choice((70, 100, 130))), (b, "small", rng.choice((70, 100, 130)))]
    for b in ("Draw", "Clay Strips", "Smooth", "Inflate/Deflate", "Layer", "Mask", "Paint Hard", "Draw Sharp",
              "Scrape/Fill", "Blob"):
        plan += [(b, "small", 110), (b, "small", 90), (b, "medium", 160), (b, "medium", 210)]
    for b in ("Draw", "Smooth", "Inflate/Deflate", "Mask", "Clay Strips", "Layer"):
        plan += [(b, "large", 170)]
    for b in ("Draw", "Clay Strips", "Smooth", "Inflate/Deflate", "Layer", "Mask", "Blob", "Scrape/Fill"):
        plan += [(b, "wide", 150), (b, "wide", 190)]
    rng.shuffle(plan)
    failed = set()
    recorded = 0
    for brush, kind, px in plan:
        if brush in failed:
            continue
        with d.override():
            ok = set_brush(brush)
            set_radius(px)
        if not ok:
            failed.add(brush)
            continue
        yield 0.05
        before = (C.info(d) or {"len": 0})["len"]
        yield from d.drag(reg, stroke_points(reg, rng, kind), step_wait=0.012)
        yield 0.45
        after = (C.info(d) or {"len": 0})["len"]
        if after > before:
            d._labels[after - 1] = f"{brush} {kind} {px}"
            recorded += 1
    with d.override():
        set_brush("Draw")
    d.note("brushes_failed", sorted(failed))
    n = dump(d, ob, base, tag)
    d.note("steps", n)
    d.check("corpus written", n >= 30, n)
