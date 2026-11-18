"""Geometry Nodes modifier inputs of an object that the global history deleted and brings back.

D100: on Blender 5.2 and later the inputs are no longer custom properties of the modifier
(modifier.properties.inputs.<identifier>), and reading them as custom properties raised, so a deleted
object came back with its Geometry Nodes inputs at their defaults. On 4.2 to 5.1 they are custom
properties and came back already; the test runs on every version.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.core import globaltl
from hindsight.bl import runtime

C = bpy.context


def glen():
    return globaltl.global_length(runtime.reg(), runtime._visible_uids())


def gn_group():
    ng = bpy.data.node_groups.new("HS GN", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    amount = ng.interface.new_socket("Amount", in_out="INPUT", socket_type="NodeSocketFloat")
    count = ng.interface.new_socket("Count", in_out="INPUT", socket_type="NodeSocketInt")
    target = ng.interface.new_socket("Target", in_out="INPUT", socket_type="NodeSocketObject")
    vec = ng.interface.new_socket("Offset", in_out="INPUT", socket_type="NodeSocketVector")
    gi = ng.nodes.new("NodeGroupInput")
    go = ng.nodes.new("NodeGroupOutput")
    ng.links.new(gi.outputs[0], go.inputs[0])
    return ng, (amount.identifier, count.identifier, target.identifier, vec.identifier)


def set_inputs(m, ids, target):
    """The values a user would type in the modifier panel, one input read from an attribute."""
    a, c, t, v = ids
    props = getattr(m, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        ins = props.inputs
        getattr(ins, a).value = 0.375
        getattr(ins, c).value = 7
        getattr(ins, t).value = target
        getattr(ins, v).value = (1.5, -2.0, 0.25)
        getattr(ins, a).type = "ATTRIBUTE"
        getattr(ins, a).attribute_name = "weight_map"
    else:
        m[a] = 0.375
        m[c] = 7
        m[t] = target
        m[v] = (1.5, -2.0, 0.25)
        m[a + "_use_attribute"] = True
        m[a + "_attribute_name"] = "weight_map"


def read_inputs(m, ids):
    a, c, t, v = ids
    props = getattr(m, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        ins = props.inputs
        tgt = getattr(ins, t).value
        return (round(getattr(ins, a).value, 6), getattr(ins, c).value, tgt.name if tgt else None,
                tuple(round(x, 6) for x in getattr(ins, v).value), getattr(ins, a).type,
                getattr(ins, a).attribute_name)
    tgt = m[t]
    return (round(m[a], 6), m[c], tgt.name if tgt else None, tuple(round(x, 6) for x in m[v]),
            "ATTRIBUTE" if m.get(a + "_use_attribute") else "VALUE", m.get(a + "_attribute_name", ""))


def test_deleted_object_keeps_geometry_nodes_inputs():
    L.fresh_scene()
    ng, ids = gn_group()
    ob = bpy.data.objects["A"]
    target = bpy.data.objects["B"]
    m = ob.modifiers.new("GeometryNodes", "NODES")
    m.node_group = ng
    set_inputs(m, ids, target)
    want = read_inputs(m, ids)
    assert want[0] == 0.375 and want[2] == "B", want
    L.track_all()
    T.delete(["A"]).run()
    L.tick_like("delete A")
    assert "A" not in C.scene.objects
    runtime.global_goto(C, 0, True)
    ob = C.scene.objects.get("A")
    assert ob is not None, "the global history brings A back"
    m = ob.modifiers.get("GeometryNodes")
    assert m is not None and m.node_group == ng
    got = read_inputs(m, ids)
    assert got == want, (got, want)
    runtime.global_goto(C, glen(), True)
    assert "A" not in C.scene.objects


if __name__ == "__main__":
    H.run(test_deleted_object_keeps_geometry_nodes_inputs)
    H.finish()
