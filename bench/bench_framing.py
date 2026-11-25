"""Time camrig.frame on histories of N global steps (alternating moves of 3 objects, one 100k-vertex), and the
jumps alone. Run: <venv python> bench/bench_framing.py 50 200 1000 (#115, OPTIMISATION_LOG.md)."""
import os, sys, time; sys.path.insert(0, os.getcwd())
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.test_bl_camrig_steps import glen
from hindsight.bl import camrig, runtime
C = bpy.context
for n in [int(x) for x in sys.argv[1:]]:
    H.fresh()
    H.make_mesh(100000, "Big")
    for i, name in enumerate(("A", "B")):
        bpy.ops.mesh.primitive_cube_add(location=(5 * (i + 1), 0, 0)); C.active_object.name = name
    L.track_all(); runtime.set_global(C, True)
    names = ("Big", "A", "B")
    for k in range(n):
        op = T.translate(names[k % 3], (0.1, 0.05 * (k % 7), 0)); op.run(); L.tick_like(op.label)
    sc = C.scene
    sc.hindsight.movie_mode = True
    ts = []
    for _ in range(3):
        t = time.perf_counter(); camrig.frame(sc); ts.append(time.perf_counter() - t)
    print(f"RESULT steps={glen()} frame_s min={min(ts):.3f} max={max(ts):.3f}")
    here = runtime.scrub_info(C)["len"]
    t = time.perf_counter()
    with runtime.suspended():
        for g in range(glen() + 1):
            runtime.global_goto(C, g, False)
        runtime.global_goto(C, glen(), False)
    print(f"RESULT jumps_only steps={glen()} s={time.perf_counter() - t:.3f}")
