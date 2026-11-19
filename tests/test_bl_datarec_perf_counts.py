"""The data recorder's cost, as work counted rather than time (headless, so it holds on any machine).

  - Record set to Sculpting only: the timer reads nothing when an operator finishes or updates
    settle (no capture, no evaluation, no walk over the objects, no id lookups);
  - a depsgraph update with the scene recorded doesn't search for every recorded object's id;
  - one commit reads bpy.data.objects for its id lookups at most once;
  - prime() (after loading a file) reads each recorded mesh once;
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


for fn in (test_sculpting_only_timer_reads_nothing, test_depsgraph_update_does_not_search_every_id,
           test_one_objects_read_per_commit, test_prime_reads_each_mesh_once,
           test_open_hashes_each_mesh_once):
    H.run(fn)
H.finish()
