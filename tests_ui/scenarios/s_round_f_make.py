"""Round F, part 1: run with ANY build installed (old or new). Records the same scripted session
and saves it in every save mode. Writes <out>/<label>.json with the expected state of each file.

Uses only APIs that exist in every build since 1.0.0 (runtime.scrub_info/goto/history_of,
meshio.read_snapshot, the save_mode preference). Never opens a file: old builds lose their
recorder handlers on File > Open (the beta 1 bug), so everything happens in the start-up scene.

args: label=b1  out=<folder>
"""
import hashlib
import json
import os

import bpy

import common as C


def mesh_hash(ob):
    snap = C.mod("meshio").read_snapshot(ob.data, sculpt_only=False)
    h = hashlib.blake2b(digest_size=16)
    for k in sorted(snap.arrays):
        h.update(k.encode())
        h.update(snap.arrays[k].tobytes())
    return h.hexdigest()


def pos_hash(ob):
    a = C.mod("meshio").read_channel(ob.data, "position|POINT|FLOAT_VECTOR")
    return hashlib.blake2b(a.tobytes(), digest_size=12).hexdigest()


def run(d):
    label, out = d.args["label"], d.args["out"]
    os.makedirs(out, exist_ok=True)
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    rt = C.mod("runtime")
    ob = C.fresh_sphere(d, name="Blob")
    C.frame_view(d)
    C.set_mode(d, "SCULPT")
    yield 0.6
    hashes = [mesh_hash(ob)]
    positions = [pos_hash(ob)]
    snap = C.mod("meshio").read_snapshot(ob.data, sculpt_only=False)
    channels0 = {k: hashlib.blake2b(snap.arrays[k].tobytes(), digest_size=6).hexdigest() for k in sorted(snap.arrays)}
    for k in range(6):
        yield from C.real_stroke(d, k)
        i = C.info(d)
        if i and i["len"] == len(hashes):
            hashes.append(mesh_hash(ob))
            positions.append(pos_hash(ob))
    n = C.info(d)["len"]
    d.check(f"{label}: 6 strokes, 6 steps", n == 6 == len(hashes) - 1, (n, len(hashes)))

    # The step-by-step map: every history position -> mesh hash, after jumping there.
    pos_map = []
    for p in range(n + 1):
        with d.override():
            rt.goto(bpy.context, p)
        pos_map.append(mesh_hash(ob))
    with d.override():
        rt.goto(bpy.context, n)
    d.check(f"{label}: jumping reproduces every recorded state", pos_map == hashes, "")
    # Scripted session: the same seeded edits on every build, one commit per edit. Unlike mouse
    # strokes (whose dabs depend on event timing) this is identical across processes, so the
    # position-by-position result can be compared between builds.
    import numpy as np
    C.set_mode(d, "OBJECT")
    with d.override():
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1.0, location=(4, 0, 0))
        det = bpy.context.active_object
        det.name = "Scripted"
    C.set_mode(d, "SCULPT")
    yield 0.5
    rng = np.random.default_rng(2026)
    me = det.data
    nv, nf = len(me.vertices), len(me.polygons)

    def channel_hash():
        h = hashlib.blake2b(digest_size=12)
        for name in ("position", ".sculpt_mask", ".sculpt_face_set", "Col"):
            a = me.attributes.get(name)
            if a is None:
                h.update(b"-")
                continue
            prop = "vector" if name == "position" else ("color" if name == "Col" else "value")
            width = 3 if name == "position" else (4 if name == "Col" else 1)
            dt = np.int32 if name == ".sculpt_face_set" else np.float32
            v = np.empty(len(a.data) * width, dt)
            a.data.foreach_get(prop, v)
            h.update(v.tobytes())
        return h.hexdigest()

    scripted = [channel_hash()]
    for k in range(10):
        kind = k % 4
        if kind == 0:
            co = np.empty(nv * 3, np.float32)
            me.attributes["position"].data.foreach_get("vector", co)
            idx = rng.choice(nv, 120, replace=False)
            co.reshape(-1, 3)[idx] += rng.normal(0, 0.02, (120, 3)).astype(np.float32)
            me.attributes["position"].data.foreach_set("vector", co)
        elif kind == 1:
            a = me.attributes.get(".sculpt_mask") or me.attributes.new(".sculpt_mask", "FLOAT", "POINT")
            v = np.empty(nv, np.float32)
            a.data.foreach_get("value", v)
            v[rng.choice(nv, 200, replace=False)] = rng.random(200).astype(np.float32)
            a.data.foreach_set("value", v)
        elif kind == 2:
            a = me.attributes.get(".sculpt_face_set") or me.attributes.new(".sculpt_face_set", "INT", "FACE")
            v = np.empty(nf, np.int32)
            a.data.foreach_get("value", v)
            v[rng.choice(nf, 100, replace=False)] = rng.integers(1, 9, 100).astype(np.int32)
            a.data.foreach_set("value", v)
        else:
            a = me.color_attributes.get("Col") or me.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
            v = np.empty(nv * 4, np.float32)
            a.data.foreach_get("color", v)
            v.reshape(-1, 4)[rng.choice(nv, 150, replace=False)] = rng.random((150, 4)).astype(np.float32)
            a.data.foreach_set("color", v)
        me.update()
        with d.override():
            rt.commit(det)
        scripted.append(channel_hash())
    hs = rt.history_of(det)
    d.check(f"{label}: scripted session gives 10 steps", hs is not None and len(hs.entries) == 10,
            None if hs is None else len(hs.entries))
    scripted_jumps = []
    for p in list(range(11)) + [0, 10, 5]:
        with d.override():
            rt.goto(bpy.context, p)
        scripted_jumps.append(channel_hash())
    d.check(f"{label}: scripted session plays back exactly",
            scripted_jumps == [scripted[p] for p in list(range(11)) + [0, 10, 5]], "")
    with d.override():
        rt.goto(bpy.context, 10)
    C.set_mode(d, "OBJECT")
    with d.override():
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)

    prefs = bpy.context.preferences.addons[C.PKG].preferences
    files = {}
    C.set_mode(d, "OBJECT")
    for mode, cursor in (("SIDECAR", n), ("EMBED", n), ("BOTH", 3)):
        prefs.save_mode = mode
        with d.override():
            rt.goto(bpy.context, cursor)
        path = os.path.join(out, f"{label}_{mode.lower()}{'_mid' if cursor != n else ''}.blend")
        with d.override():
            bpy.ops.wm.save_as_mainfile(filepath=path)
        yield 0.3
        files[os.path.basename(path)] = {"mode": mode, "steps": n, "cursor": cursor}
    prefs.save_mode = "SIDECAR"
    snap = C.mod("meshio").read_snapshot(ob.data, sculpt_only=False)
    channels_end = {k: hashlib.blake2b(snap.arrays[k].tobytes(), digest_size=6).hexdigest() for k in sorted(snap.arrays)}
    json.dump({"label": label, "hashes": hashes, "positions": positions, "scripted": scripted, "files": files,
               "channels0": channels0,
               "channels_end": channels_end}, open(os.path.join(out, f"{label}.json"), "w"), indent=1)
    d.note("files", files)
