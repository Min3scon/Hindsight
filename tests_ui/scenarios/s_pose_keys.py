"""Posing and keyframes in a real window (D119, D120; recorded headless before).

With Everything in this file on: in Pose Mode a bone rotated with R and moved with G (mouse, then a click)
is one step each, I inserts a keyframe as one step; the global slider puts the pose and the keyframes
back exactly, both ways.
"""
import bpy

import common as C


def _pose(ob):
    pb = ob.pose.bones[0]
    return (tuple(round(x, 4) for x in pb.location), tuple(round(x, 4) for x in pb.rotation_quaternion))


def _keys(ob):
    ad = ob.animation_data
    if ad is None or ad.action is None:
        return 0
    from_slot = C.mod("adapters")._fcurves(ad.action, getattr(ad, "action_slot", None))
    return sum(len(f.keyframe_points) for f in from_slot)


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    with d.override():
        for ob in list(bpy.data.objects):
            if ob.type == "MESH":
                bpy.data.objects.remove(ob)
        bpy.ops.object.armature_add(location=(0, 0, 0))
        rig = bpy.context.active_object
        rig.name = "Rig"
        bpy.ops.view3d.view_axis(type="FRONT")
        bpy.ops.view3d.view_selected()
    C.mod("prefs").get_prefs().record_scope = "EVERYTHING"
    with d.override():
        bpy.ops.hindsight.record_file(on=True)
    bpy.context.scene.hindsight.use_global = True
    yield 0.6
    C.set_mode(d, "POSE")
    yield 0.5
    with d.override():
        bpy.ops.pose.select_all(action="SELECT")
    yield 0.3
    n0 = (C.info(d) or {}).get("len", 0)
    states = [(_pose(rig), _keys(rig))]
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2

    def modal(key, dx, dy):
        d.ev("MOUSEMOVE", x=cx + 80, y=cy + 40)
        yield 0.2
        d.key(key, x=cx + 80, y=cy + 40)
        yield 0.3
        for i in range(1, 9):
            d.ev("MOUSEMOVE", x=cx + 80 + dx * i, y=cy + 40 + dy * i)
            yield 0.05
        d.ev("LEFTMOUSE", "PRESS", x=cx + 80 + dx * 8, y=cy + 40 + dy * 8)
        d.ev("LEFTMOUSE", "RELEASE", x=cx + 80 + dx * 8, y=cy + 40 + dy * 8)
        yield 1.0
    yield from modal("R", 0, 12)
    states.append((_pose(rig), _keys(rig)))
    yield from modal("G", 10, 0)
    states.append((_pose(rig), _keys(rig)))
    d.key("I", x=cx, y=cy)
    yield 1.0
    states.append((_pose(rig), _keys(rig)))
    i = C.info(d)
    d.check("R, G and I in Pose Mode: three steps, each changing the pose or the keys",
            i is not None and i["len"] == n0 + 3 and len(set(states)) == 4 and states[3][1] > 0,
            (n0, i, states))
    d.shot("pose_keys")
    for k in (2, 0, 1, 3):
        yield from C.drag_slider_to(d, n0 + k)
        yield 0.6
        got = (_pose(rig), _keys(rig))
        d.check(f"slider to {n0 + k}: the pose and the keyframes of that step", C.info(d)["pos"] == n0 + k
                and got == states[k], (k, C.info(d), got, states[k]))
