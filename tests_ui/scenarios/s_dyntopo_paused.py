"""A Dyntopo visit with Record Dyntopo off (the default) is one step when it ends (D153).

Two real strokes are two steps. Dyntopo on, two real strokes with it (they add triangles), Dyntopo off: the
visit is one more step. The slider puts the mesh from before the visit back exactly (quads, no new
vertices) and the earlier strokes too; forward again gives the visit's mesh.
"""
import hashlib

import bpy

import common as C


def _state(d):
    with d.override():
        ob = bpy.context.active_object
        if ob.mode == "SCULPT" and ob.use_dynamic_topology_sculpting:
            bpy.ops.ed.flush_edits()
        me = ob.data
        co = [0.0] * (3 * len(me.vertices))
        me.vertices.foreach_get("co", co)
        cv = [0] * len(me.loops)
        me.loops.foreach_get("vertex_index", cv)
        h = hashlib.sha1(repr(([round(x, 5) for x in co], cv)).encode()).hexdigest()[:12]
        return (len(me.vertices), len(me.polygons), h)


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    C.fresh_sphere(d, segments=32, rings=16)
    C.frame_view(d)
    C.mod("prefs").get_prefs().record_dyntopo = False
    C.set_mode(d, "SCULPT")
    yield 0.6
    states = [_state(d)]
    for k in range(2):
        yield from C.real_stroke(d, k)
        yield 0.4
        states.append(_state(d))
    i = C.info(d)
    d.check("two strokes before Dyntopo: two steps", i is not None and i["len"] == 2, i)
    with d.override():
        bpy.ops.sculpt.dynamic_topology_toggle()
        ts = bpy.context.scene.tool_settings.sculpt
        ts.detail_type_method = "RELATIVE"
        ts.detail_size = 4.0
    yield 0.8
    for k in range(2, 4):
        yield from C.real_stroke(d, k)
        yield 0.4
    d.check("strokes with Dyntopo while it pauses recording are no steps one by one", C.info(d)["len"] == 2, C.info(d))
    with d.override():
        bpy.ops.sculpt.dynamic_topology_toggle()
    yield 1.0
    after = _state(d)
    i = C.info(d)
    d.check("Dyntopo off: the visit is one step", i["len"] == 3 and i["pos"] == 3 and after[1] > states[2][1],
            (i, after, states[2]))
    for pos in (2, 0, 2, 3, 1, 3):
        yield from C.drag_slider_to(d, pos)
        yield 0.6
        got = _state(d)
        want = after if pos == 3 else states[pos]
        ok = got[:2] == want[:2] if pos == 3 else got == want
        d.check(f"slider to {pos}: the mesh of that step", C.info(d)["pos"] == pos and ok, (pos, C.info(d), got, want))
