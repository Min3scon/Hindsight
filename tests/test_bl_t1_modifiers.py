"""D110: with Record everything, the modifier stack is recorded: adding, removing, reordering and
renaming modifiers, their settings, and Geometry Nodes inputs; going back over Apply Modifier gives
the modifier back with the mesh from before (D-b). Before, none of it was recorded and Apply came back
as the old mesh without its modifier."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def stack(ob):
    out = []
    for m in ob.modifiers:
        row = [m.type, m.name, m.show_viewport]
        if m.type == "SUBSURF":
            row += [m.levels, m.render_levels]
        elif m.type == "MIRROR":
            row += [tuple(m.use_axis), m.use_clip]
        elif m.type == "BEVEL":
            row += [round(m.width, 5), m.segments]
        out.append(tuple(row))
    return (tuple(out), len(ob.data.vertices))


def op(label, fn, states, ob):
    fn()
    L.tick_like(label)
    states.append(stack(ob))


def test_modifier_stack_comes_back():
    L.fresh_scene()
    L.track_all()
    ob = bpy.data.objects["A"]
    states = [stack(ob)]
    op("add subsurf", lambda: ob.modifiers.new("Subdivision", "SUBSURF"), states, ob)
    op("levels", lambda: setattr(ob.modifiers["Subdivision"], "levels", 3), states, ob)
    op("add mirror", lambda: ob.modifiers.new("Mirror", "MIRROR"), states, ob)
    op("axes", lambda: setattr(ob.modifiers["Mirror"], "use_axis", (True, True, False)), states, ob)
    op("add bevel", lambda: ob.modifiers.new("Bevel", "BEVEL"), states, ob)
    op("bevel width", lambda: setattr(ob.modifiers["Bevel"], "width", 0.25), states, ob)
    op("reorder", lambda: ob.modifiers.move(2, 0), states, ob)
    op("hide", lambda: setattr(ob.modifiers["Mirror"], "show_viewport", False), states, ob)
    op("remove", lambda: ob.modifiers.remove(ob.modifiers["Mirror"]), states, ob)

    def apply_first():
        C.view_layer.objects.active = ob
        with C.temp_override(object=ob, active_object=ob):
            bpy.ops.object.modifier_apply(modifier=ob.modifiers[0].name)
    op("apply", apply_first, states, ob)
    assert states[-1][1] != states[-2][1], "applying changed the mesh"
    h = runtime.history_of(ob)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert stack(ob) in states, (pos, stack(ob))
    runtime._goto_history(h, len(h.entries) - 1, True)
    assert stack(ob) == states[-2], ("back over Apply: the modifier and the mesh before it", stack(ob))
    runtime._goto_history(h, 0, True)
    assert stack(ob) == states[0]
    runtime._goto_history(h, len(h.entries), True)
    assert stack(ob) == states[-1]


if __name__ == "__main__":
    H.run(test_modifier_stack_comes_back)
    H.finish()
