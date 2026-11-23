"""R069 in a real window: the baseline's topology is packed while the user pauses, then a real Voxel Remesh
(a topology change) and more strokes; the slider back over the remesh gives the mesh before it exactly,
and forward the remeshed and stroked mesh.

The packing threshold is lowered for this sphere (it is meant for meshes of about 150k vertices and more).
"""
import hashlib

import bpy

import common as C


def _state(d):
    with d.override():
        ob = bpy.context.active_object
        if ob.mode == "SCULPT":
            bpy.ops.ed.flush_edits()
        me = ob.data
        co = [0.0] * (3 * len(me.vertices))
        me.vertices.foreach_get("co", co)
        cv = [0] * len(me.loops)
        me.loops.foreach_get("vertex_index", cv)
        h = hashlib.sha1(repr(([round(x, 5) for x in co], cv)).encode()).hexdigest()[:12]
        return (len(me.vertices), len(me.polygons), h)


def _packed(d):
    rt = C.mod("runtime")
    with d.override():
        st = rt._STATES.get(rt.obj_uid(bpy.context.active_object))
    return st is not None and st.is_packed()


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    C.mod("prefs").get_prefs().record_scope = "SCULPT"    # #62: what this proves is about sculpting only
    yield 0.3
    rt = C.mod("runtime")
    rt.TOPO_PACK_MIN[0] = 0
    C.fresh_sphere(d, segments=64, rings=32)
    C.frame_view(d)
    C.set_mode(d, "SCULPT")
    yield 0.6
    states = [_state(d)]
    for k in range(2):
        yield from C.real_stroke(d, k)
        states.append(_state(d))
    for _ in range(12):                       # a pause: idle packing starts after 2 s
        yield 0.5
        if _packed(d):
            break
    d.check("a pause packs the baseline's topology", _packed(d))
    with d.override():
        ob = bpy.context.active_object
        ob.data.remesh_voxel_size = 0.06
        bpy.ops.object.voxel_remesh()
    yield 1.0
    states.append(_state(d))
    i = C.info(d)
    d.check("Voxel Remesh with the topology packed: one more step, a new topology",
            i is not None and i["len"] == 3 and states[3][:2] != states[2][:2], (i, states[2], states[3]))
    yield from C.real_stroke(d, 2)
    states.append(_state(d))
    for pos in (2, 0, 4, 2, 3, 4):
        yield from C.drag_slider_to(d, pos)
        yield 0.6
        got = _state(d)
        d.check(f"slider to {pos}: the mesh of that step", C.info(d)["pos"] == pos and got == states[pos],
                (pos, C.info(d), got, states[pos]))
    rt.TOPO_PACK_MIN[0] = rt.TOPO_PACK_DEFAULT
