"""D120: with Record everything, animation is recorded: inserting keyframes, moving them, changing
their interpolation, deleting keyframes and F-curves, and which action the object plays. Going back
over the first keyframe takes the animation off; going forward brings the action back, made again if
it was gone from the file. Changing the frame is never a step."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import adapters, runtime

C = bpy.context


def fcurves(ob):
    ad = ob.animation_data
    if ad is None or ad.action is None:
        return []
    return adapters._fcurves(ad.action, getattr(ad, "action_slot", None))


def anim_state(ob):
    ad = ob.animation_data
    act = ad.action.name if ad is not None and ad.action is not None else None
    rows = []
    for fc in sorted(fcurves(ob), key=lambda f: (f.data_path, f.array_index)):
        rows.append((fc.data_path, fc.array_index,
                     tuple((round(k.co[0], 4), round(k.co[1], 4), k.interpolation) for k in fc.keyframe_points)))
    return (act, tuple(rows))


def fc_of(ob, path, index):
    return next(f for f in fcurves(ob) if f.data_path == path and f.array_index == index)


def test_keyframes_come_back():
    L.fresh_scene()
    a = bpy.data.objects["A"]
    L.track_all()
    L.tick_like("baseline")
    C.scene.frame_set(1)
    states = [anim_state(a)]

    def op(label, fn):
        fn()
        L.tick_like(label)
        states.append(anim_state(a))
    op("key 1", lambda: a.keyframe_insert("location", frame=1))

    def key10():
        a.location.x += 2
        a.keyframe_insert("location", index=0, frame=10)
    op("key 10", key10)
    op("move key", lambda: setattr(fc_of(a, "location", 0).keyframe_points[1], "co", (12.0, 3.0)))
    op("interpolation", lambda: setattr(fc_of(a, "location", 0).keyframe_points[0], "interpolation", "LINEAR"))
    op("key scale", lambda: a.keyframe_insert("scale", index=2, frame=5))

    def del_key():
        fc = fc_of(a, "location", 0)
        fc.keyframe_points.remove(fc.keyframe_points[0])
    op("delete key", del_key)

    def del_curve():
        fc = fc_of(a, "location", 1)
        coll = adapters._fcurve_coll(a.animation_data.action,
                                     getattr(getattr(a.animation_data, "action_slot", None), "identifier", None),
                                     create=False)[0]
        coll.remove(fc)
    op("delete curve", del_curve)
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(a)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert anim_state(a) in states, (pos, anim_state(a))
    runtime._goto_history(h, 0, True)
    assert anim_state(a) == states[0], (anim_state(a), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert anim_state(a) == states[-1], (anim_state(a), states[-1])


def test_frame_change_is_not_a_step_and_gone_action_is_made_again():
    L.fresh_scene()
    a = bpy.data.objects["A"]
    L.track_all()
    L.tick_like("baseline")
    a.keyframe_insert("location", frame=1)
    a.location.z += 1
    a.keyframe_insert("location", frame=20)
    L.tick_like("keys")
    keyed = anim_state(a)
    h = runtime.history_of(a)
    n = len(h.entries)
    for f in (5, 10, 15):
        C.scene.frame_set(f)
        L.tick_like("frame")
    assert len(h.entries) == n, "the frame isn't a step"
    runtime._goto_history(h, 0, True)
    assert anim_state(a)[0] is None
    act = next(x for x in bpy.data.actions if x.users == 0)
    bpy.data.actions.remove(act)            # what saving and opening does to an action nothing uses
    runtime._goto_history(h, len(h.entries), True)
    assert anim_state(a) == keyed, (anim_state(a), keyed)


if __name__ == "__main__":
    H.run(test_keyframes_come_back)
    H.run(test_frame_change_is_not_a_step_and_gone_action_is_made_again)
    H.finish()
