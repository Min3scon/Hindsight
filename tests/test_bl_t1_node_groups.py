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


if __name__ == "__main__":
    H.run(test_geometry_nodes_group_comes_back)
    H.run(test_shader_group_in_material_and_shared_group)
    H.finish()
