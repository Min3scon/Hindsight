"""Edit Mode step: ms of datarec.finish_operation after a Move of the selected vertices, with the
data recorder on (scope Everything), at 100k and 300k vertices (about 2% selected).

    <bpy python> bench/scenarios/editmode.py CODE_ROOT [--quick] [--only a,b] [--reps N]

Scenarios: edit_step_100k, edit_step_300k (the whole finish_operation; the split into the data
commit and the Edit Mode step is in the extra fields commit_ms and edit_step_ms, medians).
tracemalloc and VmHWM are those of one extra finish_operation.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as K  # noqa: E402

opts = K.Opts()
K.use_root(opts.root)
import bpy  # noqa: E402
import bmesh  # noqa: E402
import tests.harness as H  # noqa: E402
from hindsight.bl import datarec, runtime  # noqa: E402
import statistics  # noqa: E402

K.import_checked(opts.root)


def case(nv, reps):
    H.fresh()
    ob = H.make_mesh(nv, name="M")
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    datarec.set_tracked(ob, True)
    datarec.finish_operation("baseline")
    H.enter(ob, "EDIT")
    bm = bmesh.from_edit_mesh(ob.data)
    for v in bm.verts:
        v.select = v.index % 50 == 0
    bmesh.update_edit_mesh(ob.data)
    datarec.finish_operation("select")
    com, edit = [], []
    oc, oe = datarec.commit, datarec._edit_step

    def tc(*a, **k):
        t = time.perf_counter()
        r = oc(*a, **k)
        com.append((time.perf_counter() - t) * 1000)
        return r

    def te(*a, **k):
        t = time.perf_counter()
        r = oe(*a, **k)
        edit.append((time.perf_counter() - t) * 1000)
        return r
    datarec.commit, datarec._edit_step = tc, te
    tot = []
    try:
        for _ in range(2):                                   # warm up
            bpy.ops.transform.translate(value=(0, 0, 0.01))
            datarec.finish_operation("Move")
        com.clear()
        edit.clear()
        for _ in range(reps):
            bpy.ops.transform.translate(value=(0, 0, 0.01))
            t = time.perf_counter()
            datarec.finish_operation("Move")
            tot.append((time.perf_counter() - t) * 1000)
        bpy.ops.transform.translate(value=(0, 0, 0.01))
        with K.Peak() as p:
            datarec.finish_operation("Move")
    finally:
        datarec.commit, datarec._edit_step = oc, oe
    h = runtime.history_of(ob)
    return K.stats(tot, vertices=len(ob.data.vertices), commit_ms=round(statistics.median(com), 3),
                   edit_step_ms=round(statistics.median(edit), 3) if edit else None,
                   steps=len(h.entries) if h else 0, tracemalloc_mb=round(p.tracemalloc_mb, 3),
                   hwm_mb=round(p.hwm_mb, 3))


results = {}
for name, nv in (("edit_step_100k", 100_000 if not opts.quick else 10_000),
                 ("edit_step_300k", 300_000 if not opts.quick else 20_000)):
    if opts.want(name):
        K.attempt(results, name, lambda nv=nv: case(nv, opts.n(7, 3)))
K.emit(results, quick=opts.quick, blender=bpy.app.version_string)
