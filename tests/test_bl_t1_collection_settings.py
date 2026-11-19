"""D127: with Record everything and the scene recorded, collection settings are recorded in the
scene's history: hide in viewport and render, selectable, colour tag, and per view layer Exclude,
Hide in Viewport, Holdout and Indirect Only."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def find_lc(lc, name):
    if lc.collection.name == name:
        return lc
    for ch in lc.children:
        r = find_lc(ch, name)
        if r is not None:
            return r
    return None


def state(name):
    c = bpy.data.collections[name]
    lc = find_lc(C.view_layer.layer_collection, name)
    return (c.hide_render, c.hide_select, c.color_tag, lc.exclude, lc.hide_viewport, lc.holdout, lc.indirect_only)


def test_collection_settings_come_back():
    L.fresh_scene()
    grp = bpy.data.collections.new("Grp")
    C.scene.collection.children.link(grp)
    L.track_all()
    L.tick_like("baseline")
    lc = lambda: find_lc(C.view_layer.layer_collection, "Grp")
    states = [state("Grp")]
    for label, fn in (("render", lambda: setattr(grp, "hide_render", True)),
                      ("select", lambda: setattr(grp, "hide_select", True)),
                      ("colour", lambda: setattr(grp, "color_tag", "COLOR_04")),
                      ("holdout", lambda: setattr(lc(), "holdout", True)),
                      ("indirect", lambda: setattr(lc(), "indirect_only", True)),
                      ("hide", lambda: setattr(lc(), "hide_viewport", True)),
                      ("exclude", lambda: setattr(lc(), "exclude", True))):
        fn()
        L.tick_like(label)
        states.append(state("Grp"))
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(C.scene)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert state("Grp") in states, (pos, state("Grp"))
    runtime._goto_history(h, 0, True)
    assert state("Grp") == states[0], (state("Grp"), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert state("Grp") == states[-1], (state("Grp"), states[-1])


if __name__ == "__main__":
    H.run(test_collection_settings_come_back)
    H.finish()
