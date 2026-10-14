"""Round E: the owner's own files, COPIES ONLY (my_models_copies/, from copy_own_models.py).

Per copy: time File > Open with Hindsight on and off, sculpt the biggest visible mesh with real
strokes, check every jump bit-exact, save, reopen, check the history came back, list the warnings.
Results name files by number only; the manifest that maps numbers to names stays in .tmp/.
"""
import hashlib
import json
import os
import time

import bpy

import common as C


def mesh_hash(ob):
    snap = C.mod("meshio").read_snapshot(ob.data, sculpt_only=False)
    h = hashlib.blake2b(digest_size=16)
    for k in sorted(snap.arrays):
        h.update(k.encode())
        h.update(snap.arrays[k].tobytes())
    return h.hexdigest()


def timed_open(d, path):
    t = time.perf_counter()
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
    return (time.perf_counter() - t) * 1000


def biggest_mesh():
    obs = [o for o in bpy.context.view_layer.objects if o.type == "MESH" and o.visible_get() and o.data.vertices]
    return max(obs, key=lambda o: len(o.data.vertices)) if obs else None


def run(d):
    manifest = json.load(open(d.args["manifest"], encoding="utf-8"))
    copies = [r for r in manifest if r["status"] == "copied"]
    repo_copies = os.path.abspath(os.path.join(d.spec["repo"], "my_models_copies"))
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    rt = C.mod("runtime")
    results = []
    for rec in copies:
        n, path = rec["n"], rec["copy"]
        assert os.path.abspath(path).startswith(repo_copies), "only copies may be opened"
        res = {"n": n, "size_mb": rec["size_mb"]}
        mark = d.console_mark()
        timed_open(d, path)                     # warm the file cache
        yield 0.5
        res["open_ms_with"] = round(min(timed_open(d, path) for _ in range(2)), 1)
        yield 0.5
        for src, dst in rec.get("absolute_remap", {}).items():
            for img in bpy.data.images:
                if os.path.normcase(bpy.path.abspath(img.filepath)) == os.path.normcase(src):
                    img.filepath = dst
        ob = biggest_mesh()
        res["objects"] = len(bpy.data.objects)
        res["meshes"] = len([o for o in bpy.data.objects if o.type == "MESH"])
        if ob is None:
            res["note"] = "no visible mesh"
            results.append(res)
            continue
        res["verts"] = len(ob.data.vertices)
        obname = ob.name
        res["modifiers"] = sorted({m.type for m in ob.modifiers})
        with d.override():
            for o in bpy.context.view_layer.objects:
                o.select_set(False)
            bpy.context.view_layer.objects.active = ob
            ob.select_set(True)
            bpy.ops.view3d.view_selected()
        try:
            C.set_mode(d, "SCULPT")
        except Exception as e:
            res["note"] = f"cannot enter Sculpt Mode: {e}"
            results.append(res)
            continue
        yield 0.6
        with d.override():
            res["warnings"] = [f"{s}: {t}" for s, t in rt.warnings_for(bpy.context, ob)]
        paused = rt.blocking_reason(ob)
        start_len = (C.info(d) or {}).get("len", 0)
        hashes = [None] * start_len + [mesh_hash(ob)]     # index = history position
        trace = []
        for k in range(3):
            before = (C.info(d) or {}).get("len", 0)
            prev = mesh_hash(ob)
            yield from C.real_stroke(d, k)
            changed = mesh_hash(ob) != prev
            waited = 0.0
            while changed and (C.info(d) or {}).get("len", 0) == before and waited < 1.5:
                yield 0.1
                waited += 0.1
            i = C.info(d) or {"len": 0}
            while len(hashes) <= i["len"]:
                hashes.append(None)
            hashes[i["len"]] = mesh_hash(ob)
            trace.append({"changed": changed, "before": before, "after": i["len"], "commit_wait_s": round(waited, 1)})
        h = rt.history_of(ob)
        res["trace"] = trace
        res["labels"] = [e.meta.get("label") for e in h.entries] if h else []
        i = C.info(d)
        res["steps"] = 0 if i is None else i["len"]
        lost = [t for t in trace if t["changed"] and t["after"] == t["before"]]
        if paused:
            d.check(f"own file {n}: recording paused ({paused}) and says so", res["steps"] == start_len, res)
        else:
            d.check(f"own file {n}: every stroke that changed the mesh was recorded", not lost, trace)
        bad = []
        if res["steps"]:
            C.mod("slider_prop").sync()
            wm = bpy.context.window_manager
            for target in sorted({0, res["steps"], 1, res["steps"] // 2, start_len}):
                with d.override():
                    wm.hindsight_step = target
                if hashes[target] is not None and mesh_hash(ob) != hashes[target]:
                    bad.append(target)
            d.check(f"own file {n}: jumps bit-exact", not bad, bad)
        d.shot(f"own_{n:02d}")
        C.set_mode(d, "OBJECT")
        with d.override():
            bpy.ops.wm.save_mainfile()
        yield 0.5
        timed_open(d, path)
        yield 0.8
        ob2 = bpy.data.objects.get(obname)
        h2 = rt.history_of(ob2) if ob2 else None
        if res["steps"]:
            d.check(f"own file {n}: history persists after save and reopen",
                    h2 is not None and len(h2.entries) == res["steps"] and mesh_hash(ob2) == hashes[h2.cursor],
                    None if h2 is None else (len(h2.entries), h2.cursor))
        log = d.console_since(mark)
        d.check(f"own file {n}: no Python errors from Hindsight", "Traceback" not in log, log[-800:])
        results.append(res)

    # open times without Hindsight
    with d.override():
        bpy.ops.preferences.addon_disable(module=C.PKG)
    yield 0.5
    for res, rec in zip(results, copies):
        timed_open(d, rec["copy"])
        yield 0.3
        res["open_ms_without"] = round(min(timed_open(d, rec["copy"]) for _ in range(2)), 1)
        yield 0.3
    with d.override():
        bpy.ops.preferences.addon_enable(module=C.PKG)
    d.note("own_files", results)
