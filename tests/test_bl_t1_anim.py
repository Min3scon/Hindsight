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
    # D162: the jump takes away the action the keys made, as Blender's undo (and saving and opening) does
    assert not [x.name for x in bpy.data.actions if x.users == 0], [x.name for x in bpy.data.actions]
    runtime._goto_history(h, len(h.entries), True)
    assert anim_state(a) == keyed, (anim_state(a), keyed)


def drv_state(ob):
    ad = ob.animation_data
    if ad is None:
        return ((), ())
    drv = tuple((f.data_path, f.array_index, f.driver.type, f.driver.expression,
                 tuple((v.name, v.type, tuple((t.id.name if t.id else None, t.data_path) for t in v.targets))
                       for v in f.driver.variables)) for f in ad.drivers)
    nla = tuple((t.name, t.mute, tuple((s.name, round(s.frame_start, 3), s.action.name if s.action else None)
                                       for s in t.strips)) for t in ad.nla_tracks)
    return (drv, nla)


def test_drivers_and_nla():
    L.fresh_scene()
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    act = bpy.data.actions.new("Walk")
    act.use_fake_user = True
    L.track_all()
    L.tick_like("baseline")
    states = [drv_state(a)]

    def op(label, fn):
        fn()
        L.tick_like(label)
        states.append(drv_state(a))

    def add_driver():
        fc = a.driver_add("scale", 0)
        v = fc.driver.variables.new()
        v.name = "x"
        v.targets[0].id = b
        v.targets[0].data_path = "location.x"
        fc.driver.expression = "x * 2"
    op("driver", add_driver)
    op("expression", lambda: setattr(a.animation_data.drivers[0].driver, "expression", "x + 1"))

    def add_var():
        v = a.animation_data.drivers[0].driver.variables.new()
        v.name = "y"
        v.targets[0].id = b
        v.targets[0].data_path = "location.y"
    op("variable", add_var)

    def strip():
        t = a.animation_data.nla_tracks.new()
        t.name = "Base"
        t.strips.new("Walk", 1, act)
    op("strip", strip)
    op("mute track", lambda: setattr(a.animation_data.nla_tracks[0], "mute", True))
    op("remove driver", lambda: a.animation_data.drivers.remove(a.animation_data.drivers[0]))
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(a)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert drv_state(a) in states, (pos, drv_state(a))
    runtime._goto_history(h, 0, True)
    assert drv_state(a) == states[0], (drv_state(a), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert drv_state(a) == states[-1], (drv_state(a), states[-1])


def _fc_count(x):
    ad = getattr(x, "animation_data", None)
    if ad is None or ad.action is None:
        return 0
    return sum(len(f.keyframe_points) for f in adapters._fcurves(ad.action, getattr(ad, "action_slot", None)))


def test_data_shape_key_and_material_animation():
    """D125: keyframes on a light's energy, a shape key's value and a material's node input are
    recorded with the object, and going back takes them off again."""
    L.fresh_scene()
    a = bpy.data.objects["A"]
    a.shape_key_add(name="Basis")
    smile = a.shape_key_add(name="Smile")
    m = bpy.data.materials.new("Keyed")
    if bpy.app.version < (5, 0, 0):
        m.use_nodes = True
    a.data.materials.append(m)
    bsdf = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    bpy.ops.object.light_add(type="POINT", location=(0, 0, 3))
    lamp = C.active_object
    L.track_all()
    L.tick_like("baseline")
    read = lambda: (_fc_count(lamp.data), _fc_count(a.data.shape_keys), _fc_count(m.node_tree))
    states = [read()]
    for label, fn in (("light key", lambda: lamp.data.keyframe_insert("energy", frame=1)),
                      ("light key 2", lambda: lamp.data.keyframe_insert("energy", frame=10)),
                      ("shape key", lambda: smile.keyframe_insert("value", frame=1)),
                      ("node key", lambda: bsdf.inputs["Roughness"].keyframe_insert("default_value", frame=1))):
        fn()
        L.tick_like(label)
        states.append(read())
    assert states == [(0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 1, 0), (2, 1, 1)], states
    hists = [runtime.history_of(lamp), runtime.history_of(a)]
    for h in hists:
        while h.cursor > 0:
            runtime._goto_history(h, h.cursor - 1, True)
    assert read() == states[0], read()
    for h in hists:
        runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], read()


if __name__ == "__main__":
    H.run(test_keyframes_come_back)
    H.run(test_frame_change_is_not_a_step_and_gone_action_is_made_again)
    H.run(test_drivers_and_nla)
    H.run(test_data_shape_key_and_material_animation)
    H.finish()
