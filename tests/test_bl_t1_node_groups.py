"""D126: with Record everything, node groups an object uses are recorded with it: the Geometry Nodes
groups of its modifiers and the groups inside its materials (nested ones too). Nodes added and removed,
links, input values and the group's inputs and outputs come back. A group several objects use is
recorded once, by the first of them by name."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def _r(v):
    try:
        return tuple(round(x, 4) for x in v)
    except TypeError:
        return round(v, 4) if isinstance(v, float) else v


def tree_state(ng):
    nodes = tuple(sorted((n.name, n.bl_idname) for n in ng.nodes))
    vals = tuple(sorted((n.name, s.identifier, _r(s.default_value)) for n in ng.nodes for s in n.inputs
                        if hasattr(s, "default_value") and not s.is_linked
                        and not isinstance(s.default_value, bpy.types.ID)))
    links = tuple(sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                         for l in ng.links))
    iface = tuple((it.item_type, getattr(it, "in_out", ""), it.name) for it in ng.interface.items_tree)
    return (nodes, vals, links, iface)


def tree_state_by_name(ng):
    """tree_state with sockets told by name: a group made again gets new socket identifiers (Blender's)."""
    nodes, vals, links, iface = tree_state(ng)
    links = tuple(sorted((l.from_node.name, l.from_socket.name, l.to_node.name, l.to_socket.name) for l in ng.links))
    return (nodes, vals, links, tuple(sorted(iface)))


def _gn_named(name):
    ng = make_gn()
    ng.name = name
    return ng


def make_gn():
    ng = bpy.data.node_groups.new("Shape", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi = ng.nodes.new("NodeGroupInput")
    go = ng.nodes.new("NodeGroupOutput")
    ng.links.new(gi.outputs[0], go.inputs[0])
    return ng


def test_geometry_nodes_group_comes_back():
    L.fresh_scene()
    a = bpy.data.objects["A"]
    ng = make_gn()
    m = a.modifiers.new("GN", "NODES")
    m.node_group = ng
    L.track_all()
    L.tick_like("baseline")
    states = [tree_state(ng)]

    def add_node():
        sp = ng.nodes.new("GeometryNodeSetPosition")
        sp.name = "Lift"
        gi, go = ng.nodes["Group Input"], ng.nodes["Group Output"]
        ng.links.new(gi.outputs[0], sp.inputs["Geometry"])
        ng.links.new(sp.outputs["Geometry"], go.inputs[0])

    def remove_node():
        ng.nodes.remove(ng.nodes["Lift"])
    for label, fn in (("add node", add_node),
                      ("offset", lambda: setattr(ng.nodes["Lift"].inputs["Offset"], "default_value", (0, 0, 0.5))),
                      ("input", lambda: ng.interface.new_socket("Size", in_out="INPUT", socket_type="NodeSocketFloat")),
                      ("rename input", lambda: setattr(ng.interface.items_tree["Size"], "name", "Height")),
                      ("remove node", remove_node)):
        fn()
        L.tick_like(label)
        states.append(tree_state(ng))
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(a)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert tree_state(ng) in states, (pos, tree_state(ng))
    runtime._goto_history(h, 0, True)
    assert tree_state(ng) == states[0], (tree_state(ng), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert tree_state(ng) == states[-1], (tree_state(ng), states[-1])


def test_shader_group_in_material_and_shared_group():
    L.fresh_scene()
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    grp = bpy.data.node_groups.new("Tint", "ShaderNodeTree")
    grp.interface.new_socket("Out", in_out="OUTPUT", socket_type="NodeSocketFloat")
    val = grp.nodes.new("ShaderNodeValue")
    val.name = "Amount"
    val.outputs[0].default_value = 0.25
    go = grp.nodes.new("NodeGroupOutput")
    grp.links.new(val.outputs[0], go.inputs[0])
    mat = bpy.data.materials.new("Tinted")
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    gnode = mat.node_tree.nodes.new("ShaderNodeGroup")
    gnode.node_tree = grp
    a.data.materials.append(mat)
    mb = bpy.data.materials.new("Tinted B")
    if bpy.app.version < (5, 0, 0):
        mb.use_nodes = True
    mb.node_tree.nodes.new("ShaderNodeGroup").node_tree = grp
    b.data.materials.append(mb)
    L.track_all()
    L.tick_like("baseline")
    nb = len(runtime.history_of(b).entries) if runtime.history_of(b) else 0
    val.outputs[0].default_value = 0.75
    L.tick_like("amount")
    hb = runtime.history_of(b)
    assert (len(hb.entries) if hb else 0) == nb, "the group is recorded once, with A (first by name)"
    h = runtime.history_of(a)
    runtime._goto_history(h, h.cursor - 1, True)
    assert round(grp.nodes["Amount"].outputs[0].default_value, 4) == 0.25
    runtime._goto_history(h, h.cursor + 1, True)
    assert round(grp.nodes["Amount"].outputs[0].default_value, 4) == 0.75


def zone_state(ng):
    out = []
    for n in sorted(ng.nodes, key=lambda n: n.name):
        items = tuple((c, tuple(it.name for it in getattr(n, c)))
                      for c in ("repeat_items", "capture_items", "enum_items") if hasattr(n, c))
        po = getattr(n, "paired_output", None)
        out.append((n.name, n.bl_idname, items, po.name if po is not None else None))
    links = tuple(sorted((l.from_node.name, l.from_socket.name, l.to_node.name, l.to_socket.name) for l in ng.links))
    return (tuple(out), links)


def test_zones_and_items_come_back():
    """D130: a repeat zone (its pairing and its items), capture items and menu items come back,
    with the links to item sockets made again."""
    L.fresh_scene()
    a = bpy.data.objects["A"]
    ng = make_gn()
    a.modifiers.new("GN", "NODES").node_group = ng
    L.track_all()
    L.tick_like("baseline")
    states = [zone_state(ng)]

    def zone():
        ri = ng.nodes.new("GeometryNodeRepeatInput")
        ri.name = "RIn"
        ro = ng.nodes.new("GeometryNodeRepeatOutput")
        ro.name = "ROut"
        ri.pair_with_output(ro)
        gi, go = ng.nodes["Group Input"], ng.nodes["Group Output"]
        ng.links.new(gi.outputs[0], ri.inputs["Geometry"])
        ng.links.new(ri.outputs["Geometry"], ro.inputs["Geometry"])
        ng.links.new(ro.outputs["Geometry"], go.inputs[0])

    def item():
        ng.nodes["ROut"].repeat_items.new("FLOAT", "Value")

    def link_item():
        ng.links.new(ng.nodes["RIn"].outputs["Value"], ng.nodes["ROut"].inputs["Value"])

    def remove_item():
        ro = ng.nodes["ROut"]
        ro.repeat_items.remove(ro.repeat_items[1])

    def menu():
        m = ng.nodes.new("GeometryNodeMenuSwitch")
        m.name = "Menu"
        m.enum_items.new("Third")
    for label, fn in (("zone", zone), ("item", item), ("link item", link_item), ("remove item", remove_item),
                      ("menu", menu)):
        fn()
        L.tick_like(label)
        states.append(zone_state(ng))
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(a)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert zone_state(ng) in states, (pos, zone_state(ng))
    runtime._goto_history(h, 0, True)
    assert zone_state(ng) == states[0], (zone_state(ng), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert zone_state(ng) == states[-1], (zone_state(ng), states[-1])


def test_loose_group_and_compositor_with_the_scene():
    """D133: a node group no object uses (kept with a fake user, an add-on's tree) and the compositor
    are recorded in the scene's history."""
    L.fresh_scene()
    sc = bpy.context.scene
    grp = bpy.data.node_groups.new("Kept", "ShaderNodeTree")
    grp.use_fake_user = True
    v = grp.nodes.new("ShaderNodeValue")
    v.name = "K"
    v.outputs[0].default_value = 1.0
    if hasattr(sc, "compositing_node_group"):
        comp = bpy.data.node_groups.new("Comp", "CompositorNodeTree")
        sc.compositing_node_group = comp
    else:
        sc.use_nodes = True
        comp = sc.node_tree
    blur = comp.nodes.new("CompositorNodeBlur")
    blur.name = "Soft"
    soft = lambda: (comp.nodes["Soft"].filter_type if hasattr(comp.nodes["Soft"], "filter_type")
                    else round(comp.nodes["Soft"].inputs[-1].default_value, 4)
                    if hasattr(comp.nodes["Soft"].inputs[-1], "default_value") else None)
    L.track_all()
    L.tick_like("baseline")
    read = lambda: (round(grp.nodes["K"].outputs[0].default_value, 4), soft(),
                    tuple(sorted(n.name for n in comp.nodes)))

    def blur_change():
        n = comp.nodes["Soft"]
        if hasattr(n, "filter_type"):
            n.filter_type = "FAST_GAUSS" if n.filter_type != "FAST_GAUSS" else "CUBIC"
        else:
            n.inputs[-1].default_value = not n.inputs[-1].default_value if isinstance(
                n.inputs[-1].default_value, bool) else 0.5

    def comp_node():
        m = comp.nodes.new("CompositorNodeMixRGB" if bpy.app.version < (5, 0, 0) else "ShaderNodeMix")
        m.name = "Mixer"
    states = [read()]
    for label, fn in (("kept value", lambda: setattr(grp.nodes["K"].outputs[0], "default_value", 2.0)),
                      ("blur", blur_change), ("comp node", comp_node)):
        fn()
        L.tick_like(label)
        states.append(read())
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(sc)
    while h.cursor > 0:
        runtime._goto_history(h, h.cursor - 1, True)
    assert read() == states[0], (read(), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


def anim_state(ng):
    """The group's own animation: its action's F-curves with their keys, its drivers."""
    ad = ng.animation_data
    if ad is None:
        return None
    fc = ()
    if ad.action is not None:
        curves = getattr(ad.action, "fcurves", None)
        if curves is None:                                   # Blender 5: slotted actions
            curves = [f for lay in ad.action.layers for st in lay.strips for cb in st.channelbags for f in cb.fcurves]
        fc = tuple(sorted((f.data_path, f.array_index,
                           tuple((round(k.co[0], 4), round(k.co[1], 4)) for k in f.keyframe_points)) for f in curves))
    drv = tuple(sorted((d.data_path, d.array_index, d.driver.expression) for d in ad.drivers))
    return (fc, drv)


def test_group_animation_comes_back():
    """D146: keys on a group node's inputs and drivers on them (the group's own animation_data) are
    recorded with the group: going back and forward over the steps gives them back exactly."""
    L.fresh_scene()
    a = bpy.data.objects["A"]
    ng = make_gn()
    m = a.modifiers.new("GN", "NODES")
    m.node_group = ng
    sp = ng.nodes.new("GeometryNodeSetPosition")
    sp.name = "Lift"
    mix = ng.nodes.new("ShaderNodeMath")
    mix.name = "Gain"
    off = 'nodes["Lift"].inputs["Offset"].default_value'
    gain = 'nodes["Gain"].inputs[0].default_value'
    L.track_all()
    L.tick_like("baseline")
    assert ng.animation_data is None
    states = [anim_state(ng)]

    def key1():
        ng.keyframe_insert(data_path=off, index=2, frame=1)

    def key2():
        sp.inputs["Offset"].default_value = (0, 0, 0.5)
        ng.keyframe_insert(data_path=off, index=2, frame=10)

    def driver():
        fcu = ng.driver_add(gain)
        fcu.driver.type = "SCRIPTED"
        fcu.driver.expression = "frame * 0.5"

    def driver_edit():
        ng.animation_data.drivers[0].driver.expression = "frame * 2"
    for label, fn in (("key", key1), ("second key", key2), ("driver", driver), ("edit driver", driver_edit)):
        fn()
        L.tick_like(label)
        states.append(anim_state(ng))
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(a)
    assert h is not None and len(h.entries) >= 4, None if h is None else len(h.entries)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert anim_state(ng) in states, (pos, anim_state(ng))
    runtime._goto_history(h, 0, True)
    assert anim_state(ng) == states[0], (anim_state(ng), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert anim_state(ng) == states[-1], (anim_state(ng), states[-1])


def _group_states(name, ops, read):
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    states = [read()]
    for label, fn in ops:
        fn()
        L.tick_like(label)
        states.append(read())
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(C.scene)
    assert len(h.entries) == len(states) - 1, (len(h.entries), len(states))
    from hindsight.bl import datarec
    for pos in list(range(len(states) - 1, -1, -1)) + list(range(len(states))):
        runtime._goto_history(h, pos, True)
        datarec.resolve_missing_refs()
        assert read() == states[pos], (name, pos, read(), states[pos])
    return states


def test_group_made_and_deleted():
    """D152: a node group no object uses (kept with a fake user): going back over its making removes it,
    going back over its deletion makes it again with its nodes, links and inputs (geometry and shader)."""
    def make_gn():
        ng = _gn_named("KeptGN")
        ng.use_fake_user = True

    def make_shader():
        g = bpy.data.node_groups.new("KeptShader", "ShaderNodeTree")
        g.use_fake_user = True
        v = g.nodes.new("ShaderNodeValue")
        v.name = "K"
        v.outputs[0].default_value = 3.0

    def read():
        out = []
        for n in ("KeptGN", "KeptShader"):
            g = bpy.data.node_groups.get(n)
            out.append(None if g is None else (g.bl_idname, g.use_fake_user, tree_state_by_name(g),
                                               round(g.nodes["K"].outputs[0].default_value, 4) if "K" in g.nodes else None))
        return tuple(out)
    _group_states("gn+shader", [
        ("make geometry group", make_gn), ("make shader group", make_shader),
        ("edit", lambda: setattr(bpy.data.node_groups["KeptShader"].nodes["K"].outputs[0], "default_value", 5.0)),
        ("delete geometry group", lambda: bpy.data.node_groups.remove(bpy.data.node_groups["KeptGN"])),
        ("delete shader group", lambda: bpy.data.node_groups.remove(bpy.data.node_groups["KeptShader"]))], read)


def test_addon_tree_made_and_deleted():
    """D152: an add-on's own node tree type (tests/addons/hs_fake_addon.py: HSFakeTree) is made again."""
    from tests.addons import hs_fake_addon as FAKE
    FAKE.register()
    try:
        def make():
            g = bpy.data.node_groups.new("Fake", "HSFakeTree")
            g.use_fake_user = True
            n = g.nodes.new("HSFakeGain")
            n.name = "G"
            n.gain = 2.5
            n.kind = "LOG"

        def read():
            g = bpy.data.node_groups.get("Fake")
            return None if g is None else (g.bl_idname, round(g.nodes["G"].gain, 4), g.nodes["G"].kind, len(g.nodes))
        _group_states("hsfake", [("make", make),
                                 ("gain", lambda: setattr(bpy.data.node_groups["Fake"].nodes["G"], "gain", 0.5)),
                                 ("delete", lambda: bpy.data.node_groups.remove(bpy.data.node_groups["Fake"]))], read)
    finally:
        FAKE.unregister()


def test_group_made_then_used_is_not_taken_away():
    """D152: going back over the making of a group an object's modifier uses now leaves it (and the object's
    own history still has the modifier): never removes what is used."""
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    a = bpy.data.objects["A"]
    ng = _gn_named("Later")
    ng.use_fake_user = True
    L.tick_like("make group")
    m = a.modifiers.new("GN", "NODES")
    m.node_group = ng
    L.tick_like("use it")
    hs = runtime.history_of(C.scene)
    runtime._goto_history(hs, 0, True)
    assert bpy.data.node_groups.get("Later") is not None and m.node_group is not None, "a used group was removed"
    runtime._goto_history(hs, len(hs.entries), True)
    assert bpy.data.node_groups.get("Later") is not None


if __name__ == "__main__":
    H.run(test_geometry_nodes_group_comes_back)
    H.run(test_shader_group_in_material_and_shared_group)
    H.run(test_zones_and_items_come_back)
    H.run(test_loose_group_and_compositor_with_the_scene)
    H.run(test_group_animation_comes_back)
    H.run(test_group_made_and_deleted)
    H.run(test_addon_tree_made_and_deleted)
    H.run(test_group_made_then_used_is_not_taken_away)
    H.finish()
