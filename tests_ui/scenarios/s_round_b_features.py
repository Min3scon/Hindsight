"""Round B, features: each one exercised through the real interface with hard checks.

Morph History brush, Movie Mode playback, Join (and scrubbing back past it), Separate, Collection
Filtering, and the confirmation before a stroke drops 10 or more later steps.
"""
import hashlib

import bpy

import common as C


def mesh_hash(ob):
    snap = C.mod("meshio").read_snapshot(ob.data, sculpt_only=True)
    h = hashlib.blake2b(digest_size=12)
    h.update(snap.arrays["position|POINT|FLOAT_VECTOR"].tobytes())
    return h.hexdigest()


def activate(d, ob, mode="OBJECT"):
    with d.override():
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in bpy.context.view_layer.objects:
            o.select_set(o == ob)
        bpy.context.view_layer.objects.active = ob
        # an object can keep a mode it was in when another object was added (Movie Mode playback with
        # "Follow object and mode" puts A back in Sculpt Mode before B is added)
        if ob.mode != mode:
            bpy.ops.object.mode_set(mode=mode)
    assert ob.mode == mode, (ob.name, ob.mode, mode)


def run(d):
    rt = C.mod("runtime")
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    a = C.fresh_sphere(d, name="A")
    C.frame_view(d)
    activate(d, a, "SCULPT")
    yield 0.5
    states = [mesh_hash(a)]
    for k in range(4):
        yield from C.real_stroke(d, k)
        states.append(mesh_hash(a))
    d.check("4 strokes recorded", C.info(d)["len"] == 4, C.info(d))
    win, area, reg, ui, space = d.view3d()

    # ---------------------------------------------------------------- Morph History brush
    with d.override():
        rt.goto(bpy.context, 1)
        bpy.ops.hindsight.set_morph_target()
        rt.goto(bpy.context, 4)
        bpy.ops.hindsight.morph_brush()
    yield 0.3
    tool = bpy.context.workspace.tools.from_space_view3d_mode("SCULPT", create=False)
    d.check("Morph History brush is the active tool", tool is not None and tool.idname == "hindsight.morph_history",
            getattr(tool, "idname", None))
    before = mesh_hash(a)
    yield from C.real_stroke(d, 2)                 # paint over where strokes 3 and 4 went
    after = mesh_hash(a)
    d.check("Morph History stroke changes the mesh and records one step", after != before and C.info(d)["len"] == 5,
            C.info(d))
    with d.override():
        bpy.ops.wm.tool_set_by_id(name="builtin.brush", space_type='VIEW_3D')
        rt.goto(bpy.context, 4)
    d.check("going back before the morph stroke is exact", mesh_hash(a) == states[4])
    with d.override():
        rt.goto(bpy.context, 5)

    # ---------------------------------------------------------------- truncation confirmation
    for k in range(8):
        yield from C.real_stroke(d, k)
    n = C.info(d)["len"]
    with d.override():
        rt.goto(bpy.context, 1)
    yield 0.2
    mark = d.console_mark()
    yield from C.real_stroke(d, 1)
    yield 0.4
    d.shot("branch_dialog")
    d.check("a stroke that would drop 10+ steps is held back", C.info(d)["len"] == n and C.info(d)["pos"] == 1,
            C.info(d))
    d.check("...and the mesh is put back", mesh_hash(a) == states[1])
    d.key("RET")                                    # confirm the dialog
    yield 0.5
    d.check("after confirming, the later steps are dropped", C.info(d)["len"] == 1, C.info(d))
    yield from C.real_stroke(d, 1)
    d.check("the repeated stroke is recorded", C.info(d)["len"] == 2, C.info(d))

    # ---------------------------------------------------------------- Movie Mode
    activate(d, a, "OBJECT")
    sh = bpy.context.scene.hindsight
    sh.use_global = True
    sh.movie_mode = True
    with d.override():
        r = bpy.ops.hindsight.quick_auto_key()
    d.check("Quick auto key keyframes the history", r == {'FINISHED'}, r)
    scene = bpy.context.scene
    seen = []
    for f in range(scene.frame_start, scene.frame_end + 1):
        scene.frame_set(f)
        yield 0.01
        seen.append(C.info(d)["pos"])
    d.check("playing the timeline steps through the whole history", seen[0] == 0 and seen[-1] == C.info(d)["len"]
            and seen == sorted(seen), seen[:5] + ["..."] + seen[-3:])
    d.check("the slider is locked while Movie Mode is on", C.info(d)["locked"])
    sh.movie_mode = False
    sh.use_global = False
    yield 0.2

    # ---------------------------------------------------------------- Join, then scrub back past it
    with d.override():
        bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=0.8, location=(2.6, 0, 0))
        b = bpy.context.active_object
        b.name = "B"
        bpy.ops.view3d.view_all()
    activate(d, b, "SCULPT")
    yield 0.4
    from bpy_extras import view3d_utils
    p = view3d_utils.location_3d_to_region_2d(reg, space.region_3d, b.matrix_world.translation)
    yield from d.drag(reg, [(p.x - 30 + i * 6, p.y) for i in range(11)], step_wait=0.015)
    yield 0.5
    d.check("B has its own history", rt.history_of(b) is not None and len(rt.history_of(b).entries) == 1,
            None if rt.history_of(b) is None else len(rt.history_of(b).entries))
    activate(d, a, "OBJECT")
    with d.override():
        b.select_set(True)
        a.select_set(True)
        bpy.context.view_layer.objects.active = a
    d.ev("MOUSEMOVE", x=reg.x + reg.width // 2, y=reg.y + reg.height // 2)
    d.key("J", ctrl=True)
    yield 0.6
    d.shot("join_confirm")
    d.key("RET")
    yield 0.8
    d.check("Ctrl+J joined B into A", bpy.data.objects.get("B") is None and len(a.data.vertices) > 2100,
            len(a.data.vertices))
    h = rt.history_of(a)
    d.check("the joined object has one merged history", h is not None and len(h.entries) >= 3,
            None if h is None else [e.meta.get("label") for e in h.entries])
    with d.override():
        rt.goto(bpy.context, max(0, len(h.entries) - 1))
    yield 0.5
    d.check("scrubbing back past the join brings B back", bpy.data.objects.get("B") is not None)
    with d.override():
        rt.goto(bpy.context, len(rt.history_of(bpy.data.objects["A"]).entries))
    yield 0.3

    # ---------------------------------------------------------------- Separate (P) gives the part its own history
    a = bpy.data.objects["A"]
    activate(d, a, "EDIT")
    with d.override():
        bpy.ops.mesh.select_all(action='DESELECT')
        bpy.ops.mesh.select_random(ratio=0.2, seed=3)
        r = bpy.ops.hindsight.separate(type='SELECTED')
    yield 0.5
    activate(d, a, "OBJECT")
    parts = [o for o in bpy.data.objects if o.name.startswith("A.")]
    d.check("Separate made a new object", len(parts) == 1, [o.name for o in bpy.data.objects])
    if parts:
        d.check("the separated part starts with no history", rt.history_of(parts[0]) is None)

    # ---------------------------------------------------------------- Collection Filtering
    coll = bpy.data.collections.new("Ignored")
    bpy.context.scene.collection.children.link(coll)
    with d.override():
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=0.7, location=(0, 2.6, 0))
        c = bpy.context.active_object
        c.name = "C"
        bpy.ops.view3d.view_selected()
    for col in list(c.users_collection):
        col.objects.unlink(c)
    coll.objects.link(c)
    sh.filter_mode = 'BLACKLIST'
    sh.filter_collections.add().collection = coll
    activate(d, c, "SCULPT")
    yield 0.4
    yield from C.real_stroke(d, 0)
    d.check("an object in a blacklisted collection records nothing", rt.history_of(c) is None and C.info(d) is None,
            C.info(d))
    sh.filter_mode = 'NONE'
    activate(d, c, "OBJECT")
    d.shot("end")
