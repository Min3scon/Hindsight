"""D119: with Record everything, posing an armature is recorded: each pose bone's location, rotation
(and rotation mode), scale, its constraints and custom properties, and the armature's settings (Pose or
Rest Position). Values an animation sets are left out."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def _r(v):
    return tuple(round(x, 4) for x in v)


def make_rig():
    arm = bpy.data.armatures.new("Rig")
    ob = bpy.data.objects.new("Rig", arm)
    C.scene.collection.objects.link(ob)
    C.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    a = arm.edit_bones.new("Root")
    a.head, a.tail = (0, 0, 0), (0, 0, 1)
    b = arm.edit_bones.new("Tip")
    b.head, b.tail = (0, 0, 1), (0, 0, 2)
    b.parent = a
    bpy.ops.object.mode_set(mode="POSE")
    return ob


def pose_state(ob):
    out = []
    for pb in ob.pose.bones:
        out.append((pb.name, _r(pb.location), pb.rotation_mode, _r(pb.rotation_quaternion), _r(pb.rotation_euler),
                    _r(pb.scale), tuple((c.type, c.name, round(getattr(c, "influence", 1.0), 4)) for c in pb.constraints),
                    pb.get("twist")))
    return (tuple(out), ob.data.pose_position)


def test_pose_comes_back():
    L.fresh_scene()
    ob = make_rig()
    L.track_all()
    L.tick_like("baseline")
    states = [pose_state(ob)]
    root, tip = ob.pose.bones["Root"], ob.pose.bones["Tip"]

    def con():
        c = tip.constraints.new("COPY_ROTATION")
        c.target = bpy.data.objects["A"]
        c.influence = 0.5
    for label, fn in (("move", lambda: setattr(root, "location", (0.5, 0, 0))),
                      ("rotate", lambda: setattr(tip, "rotation_quaternion", (0.7071, 0.7071, 0, 0))),
                      ("mode", lambda: setattr(tip, "rotation_mode", "XYZ")),
                      ("euler", lambda: setattr(tip, "rotation_euler", (0.3, 0.2, 0.1))),
                      ("scale", lambda: setattr(root, "scale", (1.5, 1.5, 1.5))),
                      ("constraint", con),
                      ("influence", lambda: setattr(tip.constraints[0], "influence", 0.25)),
                      ("prop", lambda: tip.__setitem__("twist", 0.75)),
                      ("rest", lambda: setattr(ob.data, "pose_position", "REST"))):
        fn()
        L.tick_like(label)
        states.append(pose_state(ob))
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(ob)
    assert len(h.entries) == len(states) - 1, (len(h.entries), len(states))
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert pose_state(ob) in states, (pos, pose_state(ob))
    runtime._goto_history(h, 0, True)
    assert pose_state(ob) == states[0], (pose_state(ob), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert pose_state(ob) == states[-1], (pose_state(ob), states[-1])


def test_animated_bone_left_out():
    L.fresh_scene()
    ob = make_rig()
    root = ob.pose.bones["Root"]
    root.location = (0, 0, 0)
    root.keyframe_insert("location", frame=1)
    root.location = (2, 0, 0)
    root.keyframe_insert("location", frame=20)
    C.scene.frame_set(5)
    L.track_all()
    L.tick_like("baseline")
    h = runtime.history_of(ob)
    n = len(h.entries) if h else 0
    for f in (10, 15):
        C.scene.frame_set(f)
        L.tick_like("frame")
    h = runtime.history_of(ob)
    assert (len(h.entries) if h else 0) == n
    at15 = _r(root.location)
    ob.pose.bones["Tip"].scale = (2, 2, 2)
    L.tick_like("scale tip")
    h = runtime.history_of(ob)
    runtime._goto_history(h, h.cursor - 1, True)
    assert _r(ob.pose.bones["Tip"].scale) == (1, 1, 1)
    assert _r(root.location) == at15, (_r(root.location), at15)


if __name__ == "__main__":
    H.run(test_pose_comes_back)
    H.run(test_animated_bone_left_out)
    H.finish()
