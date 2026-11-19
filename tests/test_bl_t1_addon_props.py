"""D114: with Record everything, custom properties and the data add-ons keep on objects, scenes and
materials are recorded and put back exactly: values, groups, collections, pointers to other objects,
and values that weren't set before (they read as the add-on's default again). The add-on here is a
stand-in (tests/addons/hs_fake_addon.py) registered like any add-on. Blender 4 keeps an add-on's data
in the datablock's custom properties, Blender 5 in a separate group: both are covered."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests.addons import hs_fake_addon as FAKE
from hindsight.bl import runtime

C = bpy.context
_on = [False]


def addon_on():
    if not _on[0]:
        FAKE.register()
        _on[0] = True


def addon_off():
    if _on[0]:
        FAKE.unregister()
        _on[0] = False


def _r(v):
    return round(v, 5)


def _prune(v):
    """Empty groups read as defaults (Blender 4 makes them when RNA only reads a group)."""
    if hasattr(v, "to_dict"):
        v = v.to_dict()
    if isinstance(v, dict):
        v = {k: _prune(x) for k, x in v.items()}
        return {k: x for k, x in v.items() if x != {}}
    return v


def obj_state(ob):
    s = ob.hsfake
    return ({k: x for k, x in ((k, _prune(ob[k])) for k in ob.keys() if not k.startswith("_hindsight"))
             if x != {}},
            _r(s.strength), s.mode, tuple(s.flags), s.sub.level,
            tuple((i.level, i.tag) for i in s.items), s.target.name if s.target else None)


def walk(h, states, read):
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert read() in states, (pos, read(), states)
    runtime._goto_history(h, 0, True)
    assert read() == states[0], (read(), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


def test_object_custom_and_addon_props():
    addon_on()
    L.fresh_scene()
    L.track_all()
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    L.tick_like("baseline")
    states = [obj_state(a)]

    def op(label, fn):
        fn()
        L.tick_like(label)
        states.append(obj_state(a))
    op("custom", lambda: a.__setitem__("level", 3))
    op("custom group", lambda: a.__setitem__("info", {"note": "x", "n": [1, 2]}))
    op("strength", lambda: setattr(a.hsfake, "strength", 0.8))
    op("mode", lambda: setattr(a.hsfake, "mode", "HARD"))
    op("flags", lambda: setattr(a.hsfake, "flags", (True, False, True)))
    op("sub", lambda: setattr(a.hsfake.sub, "level", 7))
    op("target", lambda: setattr(a.hsfake, "target", b))

    def add_items():
        it = a.hsfake.items.add()
        it.level, it.tag = 4, "first"
        a.hsfake.items.add().tag = "second"
    op("items", add_items)
    op("remove item", lambda: a.hsfake.items.remove(0))
    op("delete custom", lambda: a.__delitem__("level"))

    def bump():
        with C.temp_override(object=a, active_object=a):
            bpy.ops.hsfake.bump()
    op("operator", bump)
    assert all(x != y for x, y in zip(states, states[1:])), "every operation changed something"
    h = runtime.history_of(a)
    assert len(h.entries) == len(states) - 1, (len(h.entries), len(states))
    walk(h, states, lambda: obj_state(a))


def test_scene_and_material_props():
    addon_on()
    L.fresh_scene()
    m = bpy.data.materials.new("Paint")
    a = bpy.data.objects["A"]
    a.data.materials.append(m)
    L.track_all()
    L.tick_like("baseline")
    sc = C.scene
    read = lambda: (_r(sc.hsfake.strength), sc.get("shot"), _r(m.hsfake.strength), m.get("wear"))
    states = [read()]
    for label, fn in (("scene addon", lambda: setattr(sc.hsfake, "strength", 0.1)),
                      ("scene custom", lambda: sc.__setitem__("shot", 12)),
                      ("material addon", lambda: setattr(m.hsfake, "strength", 0.9)),
                      ("material custom", lambda: m.__setitem__("wear", 0.25))):
        fn()
        L.tick_like(label)
        states.append(read())
    hs = runtime.history_of(sc)
    ha = runtime.history_of(a)
    assert hs is not None and ha is not None
    # the scene's own steps are in the scene's history, the material's in its object's
    for h in (ha, hs):
        while h.cursor > 0:
            runtime._goto_history(h, h.cursor - 1, True)
    assert read() == states[0], (read(), states[0])
    for h in (hs, ha):
        runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


def test_object_data_props():
    """The mesh's own custom properties are recorded with the object that records the mesh; a linked
    duplicate sharing it gets no step from them."""
    addon_on()
    L.fresh_scene()
    a = bpy.data.objects["A"]
    dup = a.copy()                      # shares A's mesh (a linked duplicate)
    dup.name = "Z-dup"
    C.scene.collection.objects.link(dup)
    L.track_all()
    L.tick_like("baseline")
    me = a.data
    nd = _len(dup)
    me["wear"] = 0.5
    L.tick_like("mesh prop")
    me["wear"] = 0.75
    L.tick_like("mesh prop again")
    assert _len(dup) == nd, "the duplicate records nothing for it"
    h = runtime.history_of(a)
    runtime._goto_history(h, h.cursor - 1, True)
    assert me["wear"] == 0.5
    runtime._goto_history(h, h.cursor - 1, True)
    assert "wear" not in me.keys()
    runtime._goto_history(h, h.cursor + 2, True)
    assert me["wear"] == 0.75


def test_owner_deleted_is_not_a_step_for_the_others():
    """The object that records a shared mesh's properties and a shared material is deleted: the
    linked duplicate takes them over without a step of its own (it changed nothing)."""
    addon_on()
    L.fresh_scene()
    a = bpy.data.objects["A"]
    m = bpy.data.materials.new("Shared")
    a.data.materials.append(m)
    a.data["wear"] = 0.5
    m["tone"] = 2
    dup = a.copy()
    dup.name = "Z-dup"
    C.scene.collection.objects.link(dup)
    L.track_all()
    L.tick_like("baseline")
    nd = _len(dup)
    with C.temp_override(selected_objects=[a], active_object=a, object=a):
        bpy.ops.object.delete()
    L.tick_like("delete")
    assert _len(dup) == nd, (_len(dup), nd)
    dup.data["wear"] = 0.9
    L.tick_like("after")
    assert _len(dup) == nd + 1, "the new owner records the mesh's properties from now on"


def _len(ob):
    h = runtime.history_of(ob)
    return len(h.entries) if h is not None else 0


def test_reading_settings_is_not_a_step():
    """A panel drawing an add-on's settings only reads them (Blender 4 makes empty groups then):
    no step."""
    addon_on()
    L.fresh_scene()
    L.track_all()
    a = bpy.data.objects["A"]
    a["x"] = 1
    L.tick_like("set")
    n = len(runtime.history_of(a).entries)
    _ = (a.hsfake.sub.level, a.hsfake.target, len(a.hsfake.items), C.scene.hsfake.sub.level)
    L.tick_like("draw")
    assert len(runtime.history_of(a).entries) == n


def test_addon_missing_when_going_back():
    """The add-on is switched off: its data stays in the file and going back neither fails nor takes
    it away; switched on again, the data is there as it was at that step."""
    addon_on()
    L.fresh_scene()
    L.track_all()
    a = bpy.data.objects["A"]
    a.hsfake.strength = 0.7
    L.tick_like("set")
    a.hsfake.strength = 0.2
    L.tick_like("set again")
    h = runtime.history_of(a)
    n = len(h.entries)
    addon_off()
    L.tick_like("add-on off")
    assert len(h.entries) == n, "switching an add-on off isn't a step"
    runtime._goto_history(h, h.cursor - 1, True)
    addon_on()
    assert _r(a.hsfake.strength) == 0.7, a.hsfake.strength


def test_deleted_object_keeps_them():
    """An object deleted and brought back has its custom properties and add-on data again."""
    addon_on()
    L.fresh_scene()
    L.track_all()
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    a["level"] = 5
    a.hsfake.strength = 0.9
    a.hsfake.target = b
    a.hsfake.items.add().tag = "kept"
    L.tick_like("set")
    want = obj_state(a)
    name = a.name
    with C.temp_override(selected_objects=[a], active_object=a, object=a):
        bpy.ops.object.delete()
    L.tick_like("delete")
    assert bpy.data.objects.get(name) is None
    h = runtime.history_of(C.scene)
    runtime._goto_history(h, h.cursor - 1, True)
    a = bpy.data.objects.get(name)
    assert a is not None
    assert obj_state(a) == want, (obj_state(a), want)


def test_animated_custom_prop_left_out():
    addon_on()
    L.fresh_scene()
    a = bpy.data.objects["A"]
    a["k"] = 0.0
    a.keyframe_insert('["k"]', frame=1)
    a["k"] = 1.0
    a.keyframe_insert('["k"]', frame=20)
    a.hsfake.strength = 0.0
    a.keyframe_insert("hsfake.strength", frame=1)
    a.hsfake.strength = 1.0
    a.keyframe_insert("hsfake.strength", frame=20)
    C.scene.frame_set(5)
    L.track_all()
    L.tick_like("baseline")
    h = runtime.history_of(a)
    n = len(h.entries) if h else 0
    for f in (10, 15):
        C.scene.frame_set(f)
        L.tick_like("frame")
    h = runtime.history_of(a)
    assert (len(h.entries) if h else 0) == n
    a["other"] = 1
    L.tick_like("other")
    h = runtime.history_of(a)
    at15 = (_r(a["k"]), _r(a.hsfake.strength))
    runtime._goto_history(h, h.cursor - 1, True)
    assert "other" not in a.keys()
    assert (_r(a["k"]), _r(a.hsfake.strength)) == at15


def test_custom_node_tree():
    """An add-on's own node tree type (its tree, node and socket classes), kept with a fake user and
    used by no object, is recorded in the scene's history: a node's settings, a socket's value, links,
    nodes added and removed; going back and forward puts each state back."""
    addon_on()
    L.fresh_scene()
    tree = bpy.data.node_groups.new("FakeTree", "HSFakeTree")
    tree.use_fake_user = True
    for name in ("A", "B"):
        tree.nodes.new("HSFakeGain").name = name
    L.track_all()
    L.tick_like("baseline")

    def read():
        return (tuple(sorted((n.name, _r(n.gain), n.kind, _r(n.inputs[0].default_value)) for n in tree.nodes)),
                tuple(sorted((lk.from_node.name, lk.to_node.name) for lk in tree.links)))
    states = [read()]
    for label, fn in (("gain", lambda: setattr(tree.nodes["A"], "gain", 2.5)),
                      ("kind", lambda: setattr(tree.nodes["B"], "kind", "LOG")),
                      ("socket", lambda: setattr(tree.nodes["B"].inputs[0], "default_value", 0.25)),
                      ("link", lambda: tree.links.new(tree.nodes["A"].outputs[0], tree.nodes["B"].inputs[0])),
                      ("add node", lambda: setattr(tree.nodes.new("HSFakeGain"), "name", "C")),
                      ("remove node", lambda: tree.nodes.remove(tree.nodes["A"]))):
        fn()
        L.tick_like(label)
        states.append(read())
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(C.scene)
    assert h is not None and len(h.entries) >= 6, h and len(h.entries)
    walk(h, states, read)


if __name__ == "__main__":
    H.run(test_object_custom_and_addon_props)
    H.run(test_scene_and_material_props)
    H.run(test_object_data_props)
    H.run(test_owner_deleted_is_not_a_step_for_the_others)
    H.run(test_reading_settings_is_not_a_step)
    H.run(test_addon_missing_when_going_back)
    H.run(test_deleted_object_keeps_them)
    H.run(test_animated_custom_prop_left_out)
    H.run(test_custom_node_tree)
    addon_off()
    H.finish()
