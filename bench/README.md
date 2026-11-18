# Hindsight benchmarks

One suite, used by every optimisation round, so results are comparable across sessions.
Everything runs headless in Blender as a Python module (`bpy`). There is no GPU and no Blender
window here: drawing is measured as Python cost only (see "What it cannot measure").

## Run it

```
# the whole suite on this checkout -> bench/results/<git short hash>.json  (about 2 to 4 minutes)
python3 bench/suite.py

# a smoke run (small sizes, seconds; numbers are NOT comparable with full runs)
python3 bench/suite.py --quick

# some scenarios or groups only (substring match on group or scenario name)
python3 bench/suite.py --only commit,jump_300,idle

# old against new, alternating processes, 5 rounds (this is what an optimisation round cites)
python3 bench/ab.py --a /path/to/old/checkout [--b /path/to/new/checkout] [--rounds 5]
                    [--only ...] [--py /home/user/venvs/bpy53/bin/python]
```

Options of `suite.py`: `--root PATH` (code under test, default this checkout), `--only a,b`,
`--quick`, `--py PYTHON` (interpreter with `bpy`, default `/home/user/venvs/bpy53/bin/python`),
`--label NAME` (default: git short hash of the root, `-dirty` if it has uncommitted changes),
`--out FILE`. `ab.py` takes `--a`, `--b`, `--rounds`, `--only`, `--quick`, `--py`, `--label-a`,
`--label-b`, `--out` and writes `bench/results/ab_<A>_vs_<B>.json` and `.md`.

Never run two suites at once and avoid running other heavy jobs: the numbers are CPU time of a
shared 4-core machine. Run old and new through `ab.py`, never one after the other by hand.

The Rust engine used by a root is the one shipped in that root's own `hindsight/wheels`
(unpacked to `<tmp>/hs_bench_native/` and put first on `PYTHONPATH`), so an old root runs the
engine it was released with and a new one the engine it ships. `HINDSIGHT_NATIVE=0 python3
bench/suite.py` measures the Python fallback instead. The result file records whether the engine
loaded (`machine.rust_engine_loaded`).

## Files

| file | what |
|---|---|
| `suite.py` | runs the scenario groups, writes `results/<label>.json` with machine facts (cores, load average at start and end, Python, Blender, engine version, Rust engine loaded) |
| `ab.py` | alternating A B A B runs of two roots, table of min, median, ratio and spread |
| `scenarios/*.py` | one script per group, `script.py CODE_ROOT [--quick] [--only a,b] [--reps N]`, prints one JSON object (last line) |
| `scenarios/_common.py` | timing, memory and option helpers |
| `saveload_bench.py`, `bench_movie.py`, `bench_bl.py` | existing benches the scenarios reuse (`Stroker`, `make_mesh`, the movie helpers, the save and load bench) |
| `results/` | `<hash>.json` suite runs and `ab_*.json/.md` A/B runs. Older `results_*.json` in this folder belong to the other benches |

Result format: every scenario has `min_ms` and `median_ms` over its repeats and `n`, plus
`tracemalloc_mb` (Python and numpy allocations, peak over what was held before) and `hwm_mb`
(process peak, VmHWM reset through `/proc/self/clear_refs`, over the RSS before; native
allocations show only here) where memory applies. A scenario that cannot run on the code under
test is `{"error": ...}`; `ab.py` shows it as "not available on A", never as zero.

## Scenarios

Sizes are in vertices; a stroke touches about 2% of them. Strokes are simulated (positions
written with `foreach_set`, then `runtime.commit`) because sculpt brush operators cannot run
headless. The packer thread is drained before each timed commit.

| group (script) | scenarios | measures |
|---|---|---|
| commit | `commit_100k`, `commit_1m` | `runtime.commit` of one 2% stroke; memory of one commit |
| commit | `goto_bl_1_100k`, `goto_bl_300_100k` | `runtime.goto` in Blender (history read plus mesh write) over 1 and 300 steps (both ways) at 100k |
| jumps | `jump_{1,30,300,3000}_{fwd,back}_{100k,1m}` | core `History.goto` over a 5,000-step packed history (newest 64 raw). Memory on `jump_300_fwd_*` and `jump_3000_back_*` |
| jumps | `drag_5000_{fwd,back}_{100k,1m}` | a drag across the whole history: 100 slider events of 50 steps, each way |
| saveload | `{save,open}_{folder,embed}_100k_{1000,6000}` | `save_as_mainfile` and `open_mainfile`, history folder and Embed, 1,000 and 6,000 steps, memory included (`saveload_bench.py`) |
| editmode | `edit_step_100k`, `edit_step_300k` | `datarec.finish_operation` after a Move in Edit Mode (2% selected), split in `commit_ms`/`edit_step_ms` |
| datarec | `dr_finish_nochange_500`, `dr_finish_move_500` | operator finish with 500 recorded objects, nothing changed / one moved |
| datarec | `dr_tick_idle_500`, `dr_on_update_500`, `dr_prime_500` | recorder timer tick idle, depsgraph update handler (per call), prime at load; `held_bytes_per_tracked` |
| draw | `redraw_{viewport,panel}_{1obj,50obj}_{object,global}[_filtered]` | Python cost of one viewport or panel redraw at 10,000 steps (1 or 50 objects, per-object or global history, 50 objects also with Collection Filtering); `python_calls` and counted gpu stub calls |
| draw | `drag_5000` | one mouse move dragging the bar over 5,000 steps (modal plus redraw) |
| movie | `movie_addon_ms_per_frame_100k` | our handlers, ms per frame over a 300-frame movie at 100k (150 steps, 2 frames per step) |
| movie | `movie_frame_set_ms_per_frame_100k` | the whole `frame_set` per frame, Blender's share included |
| addon | `addon_import`, `addon_register_{cold,warm}`, `addon_unregister_{cold,warm}` | import of the package and all its modules, register and unregister (fresh interpreter per sample; `warm` = later cycles in the same process) |
| idle | `idle_60s_{1obj,50obj}` | CPU of 60 simulated seconds of our timers (`runtime.tick`, which includes the recorder, and the slider range timer), nothing changing |
| idle | `depsgraph_handler_{1obj,50obj}` | `runtime.on_depsgraph` for a geometry update, ms per 1,000 calls |

Fixtures: the 5,000-step histories of `jumps` and the step blobs of `saveload` are built once
and cached in `<tmp>/hs_bench_fixtures` and `<tmp>/hs_saveload_cache` (environment variables
`HS_BENCH_FIXTURES`, `HS_BENCH_CACHE`), so every code root reads the same bytes. `jumps` checks
that each case ends on the exact start or end bytes; the end state hash of every case is in the
result (`end_sha`) and must be the same for old and new.

## How to read the numbers

This machine is shared with other jobs (4 cores, load average often 4 to 8), so a single run is
only a hint. `ab.py` reports, per scenario and side: the best `min` over all rounds, the median
of the rounds' medians, the ratio B/A (below 1: B is faster or smaller) and the spread, the
difference between the lowest and highest round median as a percentage of the lowest. A ratio
is believable when it is clearly outside both spreads. Rules of thumb from the runs recorded in
`results/`: see the section "Noise" below. The `min` is the steadiest statistic on a busy machine;
the median shows what a user on a busy machine sees.

## What it cannot measure

- No GPU and no Blender window: `draw` runs the add-on's Python redraw code with stubbed `gpu`
  and `blf` (calls counted, nothing drawn). Blender's own drawing, shader cost and the real event
  loop are not measured. `movie` measures `frame_set` of the real scene but not a render
  (EEVEE and Workbench abort without a GPU).
- Strokes are simulated, not sculpted: the brush operator, undo pushes of real sculpting and
  the memory Blender itself uses for sculpting are not included.
- `addon_*` runs with warm disk caches. `saveload` times the operators in Blender but not the
  disk of a weak computer.
- Weak computers (2 to 4 slow cores, 4 to 8 GB RAM) are not available: use the ratios, not the
  absolute milliseconds.
- `idle` simulates the timers in virtual time; Blender's own scheduling cost is not included.

## Noise (what the recorded run showed)

Recorded run: `results/ab_66fcdf8_vs_dd9f35b.{json,md}`: old = commit 66fcdf8 (state before the
2026-11-18 optimisation work), new = dd9f35b (main line plus the bench files), 5 rounds,
`/home/user/venvs/bpy53/bin/python`, shared 4-core machine, load average 1 to 3 during the run
(about 22 minutes). Run-to-run spread (highest minus lowest round median, over the lowest):

- Steady (spread under about 20%): jumps at 100k and 1M, save and open, dr_finish_*, dr_prime,
  idle_60s_1obj, depsgraph_handler_1obj.
- Medium (20 to 60%): edit_step, movie, addon_* (fresh interpreter per sample, sub-ms to 15 ms),
  redraw_* (tens of microseconds, so one scheduler hiccup is a large percentage).
- Noisy (over 60%): commit_100k, commit_1m (sub-ms to 3 ms, packer thread), goto_bl_1_100k,
  dr_on_update_500 (new side), dr_tick_idle_500.
- The `min` is the steadiest statistic. Trust a ratio only when clearly outside both spreads
  (roughly 25% or more here). Ratios between 0.8 and 1.25 on commit, goto_bl_1, dr_tick_idle,
  movie, addon and idle are noise.

Not reliable: `commit_*` (no change within noise), `dr_tick_idle_500` (about 2.5 microseconds, below
the harness's resolution), `addon_*` (disk cache, import state), `idle_*` and `depsgraph_handler_*`
(virtual time, same code on both sides), `movie_*` (Blender's frame_set dominates),
`save_embed_100k_6000` (about equal; the memory columns show the difference).
Drawing is Python cost only: no GPU and no window.

Headline ratios (new/old, min): jumps 0.25 to 0.48 (3000 steps at 1M: 924 ms to 228 ms), drag
across 5,000 steps 0.37 to 0.43, open 0.26 to 0.58, save 0.45 to 0.68 (embed at 6000 steps 1.09,
slower), datarec finish 0.32 to 0.35, `dr_on_update_500` 0.014, redraw with 50 objects and global
history 0.10, with Collection Filtering 0.017 to 0.022. Every scenario ran on both roots; none was
"not available on A" in this run.
