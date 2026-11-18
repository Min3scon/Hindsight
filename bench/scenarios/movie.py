"""Movie Mode: ms per frame spent in our handlers over a 300-frame movie at 100k vertices.

    <bpy python> bench/scenarios/movie.py CODE_ROOT [--quick] [--only a,b] [--reps N]

Built with bench/bench_movie.py's helpers (grid mesh, 2% strokes, Quick auto key, a frame_set per
frame as a render does). 150 steps with 2 frames per step = about 300 frames.
Scenarios:
  movie_addon_ms_per_frame_100k   our frame_change_post and depsgraph handlers, mean ms per frame of
                                  one full playback (min and median over playbacks)
  movie_frame_set_ms_per_frame_100k  the whole frame_set per frame (Blender's share included)
Extra: frames, goto_calls. Memory: tracemalloc and VmHWM of one extra playback.
--quick: 20k vertices, 40 steps.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as K  # noqa: E402

opts = K.Opts()
K.use_root(opts.root)
import bpy  # noqa: E402
import numpy as np  # noqa: E402
import tests.harness as H  # noqa: E402

K.import_checked(opts.root)
M = K.load_bench_module("bench_movie")
C = bpy.context


def run():
    nv, steps = (100_000, 150) if not opts.quick else (20_000, 40)
    M.build(nv, steps, 1)
    f0, f1 = M.key_movie(fps_step=2)
    M.play(f0, f1)                                  # warm up
    addon, total, gotos = [], [], 0
    for _ in range(opts.n(5, 2)):
        C.scene.frame_set(f1)
        times, ad, _s, g, _p = M.play(f0, f1)
        n = len(times)
        addon.append(sum(ad) * 1000 / n)
        total.append(sum(times) * 1000 / n)
        gotos = g
    C.scene.frame_set(f1)
    with K.Peak() as pk:
        M.play(f0, f1)
    mem = {"tracemalloc_mb": round(pk.tracemalloc_mb, 3), "hwm_mb": round(pk.hwm_mb, 3)}
    extra = {"frames": n, "goto_calls": gotos, "vertices": nv, "steps": steps}
    return (K.stats(addon, **extra, **mem), K.stats(total, **extra))


results = {}
names = ("movie_addon_ms_per_frame_100k", "movie_frame_set_ms_per_frame_100k")
if opts.want(*names):
    try:
        a, t = run()
        results[names[0]], results[names[1]] = a, t
    except Exception as e:
        import traceback
        traceback.print_exc()
        for n in names:
            results[n] = {"error": f"{type(e).__name__}: {e}"[:300]}
K.emit(results, quick=opts.quick, blender=bpy.app.version_string)
