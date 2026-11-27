"""D112: with Record everything, a material's settings and its node tree are recorded with the object
that uses it: input values, node settings, nodes added and removed, links made and broken. A material
used by several recorded objects is recorded once, by the first of them by name (as for shared meshes),
so the others get no steps from it."""
import contextlib
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def _r(v):
    return tuple(round(x, 5) for x in v) if hasattr(v, "__len__") else round(v, 5)


def mat_state(m):
    nt = m.node_tree
    nodes = sorted((n.name, n.bl_idname) for n in nt.nodes)
    vals = []
    for n in sorted(nt.nodes, key=lambda n: n.name):
        for s in n.inputs:
            if hasattr(s, "default_value") and not s.is_linked:
                with contextlib.suppress(TypeError):
                    vals.append((n.name, s.identifier, _r(s.default_value)))
        if n.bl_idname == "ShaderNodeMath":
            vals.append((n.name, "operation", n.operation))
    links = sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                   for l in nt.links)
    return (tuple(nodes), tuple(vals), tuple(links), _r(m.diffuse_color), _r(m.metallic))


def principled(m):
    return next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")


def make_mat(name):
    m = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        m.use_nodes = True                 # 5.0+: materials always use nodes
    return m


def op(label, fn, states, m):
    fn()
    L.tick_like(label)
    states.append(mat_state(m))


def test_material_values_and_nodes_come_back():
    L.fresh_scene()
    m = make_mat("Clay")
    a = bpy.data.objects["A"]
    a.data.materials.append(m)
    L.track_all()
    states = [mat_state(m)]
    bsdf = principled(m)
    op("colour", lambda: setattr(bsdf.inputs["Base Color"], "default_value", (0.8, 0.1, 0.1, 1.0)), states, m)
    op("roughness", lambda: setattr(bsdf.inputs["Roughness"], "default_value", 0.2), states, m)
    op("viewport colour", lambda: setattr(m, "diffuse_color", (0.1, 0.9, 0.2, 1.0)), states, m)

    def add_math():
        n = m.node_tree.nodes.new("ShaderNodeMath")
        n.name = "Mix it"
        n.operation = "MULTIPLY"
        n.inputs[1].default_value = 0.25
    op("add node", add_math, states, m)
    op("link", lambda: m.node_tree.links.new(m.node_tree.nodes["Mix it"].outputs[0], bsdf.inputs["Metallic"]),
       states, m)
    op("operation", lambda: setattr(m.node_tree.nodes["Mix it"], "operation", "ADD"), states, m)
    op("unlink", lambda: m.node_tree.links.remove(next(l for l in m.node_tree.links
                                                         if l.to_socket == bsdf.inputs["Metallic"])), states, m)
    op("remove node", lambda: m.node_tree.nodes.remove(m.node_tree.nodes["Mix it"]), states, m)
    assert all(x != y for x, y in zip(states, states[1:])), "every operation changed the material"
    h = runtime.history_of(a)
    assert len(h.entries) >= len(states) - 1, (len(h.entries), len(states))
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert mat_state(m) in states, (pos, mat_state(m))
    runtime._goto_history(h, 0, True)
    assert mat_state(m) == states[0], (mat_state(m), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert mat_state(m) == states[-1], (mat_state(m), states[-1])


def test_shared_material_recorded_once():
    L.fresh_scene()
    m = make_mat("Shared")
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    a.data.materials.append(m)
    b.data.materials.append(m)
    L.track_all()
    before = L.lengths()
    principled(m).inputs["Roughness"].default_value = 0.9
    L.tick_like("roughness")
    after = L.lengths()
    grew = {u for u in after if after[u] != before.get(u, 0)}
    assert grew == {_uid(a)}, (grew, _uid(a), _uid(b))
    ha = runtime.history_of(a)
    runtime._goto_history(ha, ha.cursor - 1, True)
    assert round(principled(m).inputs["Roughness"].default_value, 5) == 0.5


def test_changed_outside_an_operator():
    """A value changed in the material's panel (no operator): the depsgraph update marks the object
    that records the material, and the recorder's settle commit records it."""
    from hindsight.bl import datarec
    L.fresh_scene()
    m = make_mat("Panel")
    a = bpy.data.objects["A"]
    a.data.materials.append(m)
    L.track_all()
    L.tick_like("baseline")
    n = _len(a)
    principled(m).inputs["Roughness"].default_value = 0.05
    C.view_layer.update()
    assert _uid(a) in datarec._DIRTY, "the material's object is marked"
    datarec.commit(label="Change", uids=None, mesh=True)
    h = runtime.history_of(a)
    assert len(h.entries) == n + 1, (len(h.entries), n)
    runtime._goto_history(h, h.cursor - 1, True)
    assert round(principled(m).inputs["Roughness"].default_value, 5) == 0.5


def test_animated_node_value_left_out():
    """A keyed input changes with the frame, not with the user: no steps when the frame changes, and
    a step for another change doesn't carry it (going back doesn't put an old frame's value in)."""
    L.fresh_scene()
    m = make_mat("Keyed")
    a = bpy.data.objects["A"]
    a.data.materials.append(m)
    bsdf = principled(m)
    sock = bsdf.inputs["Roughness"]
    sock.default_value = 0.1
    sock.keyframe_insert("default_value", frame=1)
    sock.default_value = 0.9
    sock.keyframe_insert("default_value", frame=20)
    C.scene.frame_set(5)
    L.track_all()
    L.tick_like("baseline")
    n = _len(a)
    for f in (10, 15):
        C.scene.frame_set(f)
        L.tick_like("frame")
    assert _len(a) == n, (_len(a), n)
    at15 = round(sock.default_value, 5)
    bsdf.inputs["Base Color"].default_value = (0.2, 0.3, 0.4, 1.0)
    L.tick_like("colour")
    h = runtime.history_of(a)
    assert len(h.entries) == n + 1, (len(h.entries), n)
    runtime._goto_history(h, h.cursor - 1, True)
    assert _r(tuple(bsdf.inputs["Base Color"].default_value)) != (0.2, 0.3, 0.4, 1.0)
    assert round(sock.default_value, 5) == at15, (sock.default_value, at15)


def _len(ob):
    h = runtime.history_of(ob)
    return len(h.entries) if h is not None else 0


def _uid(ob):
    from hindsight.bl import idmap
    return idmap.uid_of(ob)


if __name__ == "__main__":
    H.run(test_material_values_and_nodes_come_back)
    H.run(test_shared_material_recorded_once)
    H.run(test_changed_outside_an_operator)
    H.run(test_animated_node_value_left_out)
    H.finish()
