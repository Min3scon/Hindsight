"""The data recorder's cost, as work counted rather than time (headless, so it holds on any machine).

  - Record set to Sculpting only: the timer reads nothing when an operator finishes or updates
    settle (no capture, no evaluation, no walk over the objects, no id lookups);
  - a depsgraph update with the scene recorded doesn't search for every recorded object's id;
  - one commit reads bpy.data.objects for its id lookups at most once;
  - prime() (after loading a file) reads each recorded mesh once;
  - an operator that only changes the selection (a click, box select, Select All) reads the selected,
    active and updated objects, not every recorded one, and in Edit Mode doesn't read the mesh (R048);
    the next operator that may change anything reads everything as before;
  - in Edit Mode a move, rotate or scale (TRANSFORM_OT_*) compares the element counts but doesn't read
    the edges and corners again: those operators change topology only by Auto Merge, which changes the
    counts (R050); any other operator reads them as before;
  - opening a file hashes each recorded mesh once to tell whether it changed without Hindsight (R045:
    it was hashed twice when unchanged, the usual case).

Timings for the same paths are in PERF_LOG.md ("Data recorder cost"); tests/test_bl_t1_perf.py holds
the time budgets.

Run: blender -b --factory-startup --python tests/test_bl_datarec_perf_counts.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import contextlib

import bpy

import tests.harness as H
from hindsight.bl import datarec, idmap, runtime

C = bpy.context


@contextlib.contextmanager
def counting(*targets):
    """Count calls to module functions: targets are (module, name)."""
    counts = {}
    saved = []
    for mod, name in targets:
        orig = getattr(mod, name)
        saved.append((mod, name, orig))
        counts[name] = 0

        def wrap(*a, _orig=orig, _name=name, **k):
            counts[_name] += 1
            return _orig(*a, **k)
        setattr(mod, name, wrap)
    try:
        yield counts
    finally:
        for mod, name, orig in saved:
            setattr(mod, name, orig)


def scene(n, tracked: bool):
    H.fresh()
    for i in range(n):
        bpy.ops.mesh.primitive_cube_add(size=0.2, location=(i * 0.3, 0, 0))
        C.active_object.name = f"N{i}"
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    if tracked:
        for o in list(C.scene.objects):
            datarec.set_tracked(o, True)
        datarec.set_tracked(C.scene, True)
    datarec.finish_operation("baseline")


def test_sculpting_only_timer_reads_nothing():
    scene(60, tracked=True)
    datarec.FORCE_SCOPE[0] = "SCULPT"
    ob = C.scene.objects[0]
    with counting((datarec, "capture"), (datarec, "finish_operation"), (datarec, "refresh_mesh_users"),
                  (datarec, "commit"), (idmap, "find"), (datarec, "check_exclusion")) as n:
        real = datarec._ops_marker
        try:
            for k in range(5):
                ob.location.x += 0.1
                C.view_layer.update()
                datarec._OPS["marker"] = (0, 0)
                datarec._ops_marker = lambda k=k: (1, 1000 + k)     # an operator finished (the list gained one)
                datarec.tick()
                datarec._ops_marker = real
                datarec._DIRTY["x"] = 0.0                     # updates that settled
                datarec.tick()
        finally:
            datarec._ops_marker = real
    assert all(v == 0 for v in n.values()), n
    assert not datarec._DIRTY


def test_depsgraph_update_does_not_search_every_id():
    scene(120, tracked=True)
    ob = C.scene.objects[5]
    with counting((idmap, "find")) as n:
        for _ in range(3):
            ob.location.z += 0.1
            C.view_layer.update()                        # runtime.on_depsgraph -> datarec.on_update
    assert n["find"] <= 3, n                             # was one search per recorded id and update
    assert idmap.uid_of(ob) in datarec._DIRTY


def test_one_objects_read_per_commit():
    scene(80, tracked=True)
    built, calls = [], []
    orig = idmap._objects_index

    def spy():
        calls.append(1)
        if idmap._IDX[0] is None:
            built.append(1)
        return orig()
    idmap._objects_index = spy
    try:
        datarec.finish_operation("Select")
    finally:
        idmap._objects_index = orig
    assert len(calls) >= 80, len(calls)    # every recorded object was looked up through the index
    assert len(built) <= 3, built          # check_exclusion, refresh_mesh_users, commit: one each at most


def test_prime_reads_each_mesh_once():
    scene(30, tracked=True)
    for st in runtime._STATES.values():
        st.values = {}
    datarec.forget()
    idmap.forget()
    with counting((runtime, "read_mesh_snapshot")) as n:
        datarec.prime()
    meshes = sum(1 for o in C.scene.objects if o.type == "MESH")
    assert n["read_mesh_snapshot"] == meshes, (n, meshes)


def test_open_hashes_each_mesh_once():
    import tempfile
    H.fresh()
    obs = []
    for i in range(3):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, location=(i * 3, 0, 0))
        ob = C.active_object
        H.enter(ob, "SCULPT")
        H.fake_stroke(ob, 1)
        assert runtime.commit(ob) is not None
        H.enter(ob, "OBJECT")
        obs.append(ob.name)
    path = os.path.join(tempfile.mkdtemp(prefix="hs_fp_"), "fp.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    with counting((runtime, "mesh_fingerprint")) as n:
        bpy.ops.wm.open_mainfile(filepath=path)
    assert all(runtime.history_of(bpy.data.objects[nm]) is not None for nm in obs)
    assert not [x for x in runtime.load_notes if x[0] == "ERROR"], runtime.load_notes
    assert n["mesh_fingerprint"] == 3, n


def _end_operator(idname, k):
    """The timer sees one operator end: the list gained `idname` (fake pointers, as headless Blender
    registers no operators run from Python)."""
    real_marker, real_info = datarec._ops_marker, datarec._last_ops_info
    datarec._OPS["marker"] = (k, 1000 + k)
    datarec._ops_marker = lambda: (k + 1, 1001 + k)
    datarec._last_ops_info = lambda: (idname, 1000 + k, k + 1)
    try:
        datarec.tick()
    finally:
        datarec._ops_marker, datarec._last_ops_info = real_marker, real_info


def test_selection_only_operator_reads_the_selection():
    scene(80, tracked=True)
    obs = list(C.scene.objects)
    for o in obs:
        o.select_set(False)
    obs[7].select_set(True)
    C.view_layer.objects.active = obs[7]
    datarec._DIRTY.clear()
    total = lambda: sum(len(h.entries) for h in runtime.reg().histories.values())
    with counting((datarec, "capture")) as n:
        _end_operator("VIEW3D_OT_select", 3)
    assert n["capture"] <= 3, n                          # the selected and active object (was all 81)
    # the next operator that may change anything compares every recorded object, and a change made
    # before it on an object that isn't selected is recorded then
    before = total()
    obs[40].location.x += 0.5
    with counting((datarec, "capture")) as n:
        _end_operator("TRANSFORM_OT_translate", 4)
    assert n["capture"] >= 80, n
    assert total() == before + 1, (before, total())
    # two operators ended between two ticks, the last a selection: everything is read
    with counting((datarec, "capture")) as n:
        real_marker, real_info = datarec._ops_marker, datarec._last_ops_info
        datarec._OPS["marker"] = (5, 1005)
        datarec._ops_marker = lambda: (7, 1007)
        datarec._last_ops_info = lambda: ("VIEW3D_OT_select", 1006, 7)
        try:
            datarec.tick()
        finally:
            datarec._ops_marker, datarec._last_ops_info = real_marker, real_info
    assert n["capture"] >= 80, n


def test_selection_only_operator_in_edit_mode_reads_no_mesh():
    H.fresh()
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    ob = H.make_mesh(20_000, name="E")
    datarec.set_tracked(ob, True)
    datarec.finish_operation("baseline")
    H.enter(ob, "EDIT")
    datarec.finish_operation("edit")
    assert runtime.edit_steps_on(ob)
    with counting((runtime, "commit_edit")) as n:
        _end_operator("MESH_OT_select_all", 1)
        _end_operator("VIEW3D_OT_select", 2)
    assert n["commit_edit"] == 0, n
    with counting((runtime, "commit_edit")) as n:
        _end_operator("MESH_OT_delete", 3)                 # any other operator: the mesh is read
    assert n["commit_edit"] == 1, n


def test_transform_in_edit_mode_reads_no_topology():
    import bmesh
    from hindsight.bl import meshio
    H.fresh()
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    ob = H.make_mesh(20_000, name="T")
    datarec.set_tracked(ob, True)
    datarec.finish_operation("baseline")
    H.enter(ob, "EDIT")
    datarec.finish_operation("edit")
    total = lambda: sum(len(h.entries) for h in runtime.reg().histories.values())
    bm = bmesh.from_edit_mesh(ob.data)
    for v in bm.verts:
        v.select = v.index % 7 == 0
    bmesh.update_edit_mesh(ob.data)
    n0 = total()
    bpy.ops.transform.translate(value=(0, 0, 0.05))
    with counting((meshio, "_corner_ints"), (meshio, "_edge_verts")) as n:
        _end_operator("TRANSFORM_OT_translate", 1)
    assert n["_corner_ints"] == 0 and n["_edge_verts"] == 0, n
    assert total() == n0 + 1, (n0, total())                        # the move is a step
    # Rotate Edge keeps the counts and changes the topology: any other operator still reads it
    bm = bmesh.from_edit_mesh(ob.data)
    for e in bm.edges:
        e.select = False
    bm.edges.ensure_lookup_table()
    inner = next(e for e in bm.edges if len(e.link_faces) == 2)
    inner.select = True
    bmesh.update_edit_mesh(ob.data)
    bpy.ops.mesh.edge_rotate(use_ccw=False)
    with counting((meshio, "_corner_ints")) as n:
        _end_operator("MESH_OT_edge_rotate", 2)
    assert n["_corner_ints"] >= 1, n
    h = runtime.history_of(ob)
    assert h.entries[-1].kind == "TOPO", h.entries[-1].kind
    # a move with Auto Merge that merges vertices changes the counts: recorded as a topology change
    bm = bmesh.from_edit_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    a, b = bm.verts[0], bm.verts[1]
    for v in bm.verts:
        v.select = False
    a.select = True
    bmesh.update_edit_mesh(ob.data)
    before = len(ob.data.vertices)
    C.scene.tool_settings.use_mesh_automerge = True
    try:
        off = (b.co - a.co)
        bpy.ops.transform.translate(value=tuple(off))
    finally:
        C.scene.tool_settings.use_mesh_automerge = False
    _end_operator("TRANSFORM_OT_translate", 3)
    ob.update_from_editmode()
    assert len(ob.data.vertices) == before - 1, (before, len(ob.data.vertices))      # merged
    assert h.entries[-1].kind == "TOPO", h.entries[-1].kind


for fn in (test_sculpting_only_timer_reads_nothing, test_depsgraph_update_does_not_search_every_id,
           test_one_objects_read_per_commit, test_prime_reads_each_mesh_once,
           test_open_hashes_each_mesh_once, test_selection_only_operator_reads_the_selection,
           test_selection_only_operator_in_edit_mode_reads_no_mesh, test_transform_in_edit_mode_reads_no_topology):
    H.run(fn)
H.finish()
