# Hindsight benchmark results

Measured with `bench/bench_core.py` (pure numpy storage engine) and `bench/bench_bl.py` (real Blender). **The Blender numbers are from headless Blender, started with blender -b (Blender 5.1.2, Python 3.13.9), not the Blender GUI**: no viewport redraw, no sculpt PBVH rebuild cost, strokes simulated by writing vertex positions and calling `runtime.commit`. Expect the GUI to add its own redraw time on top of scrub latency. Test mesh: a lat/long quad grid on a sphere built with `foreach_set`, in Sculpt Mode.

## Environment

- CPU: Intel(R) Core(TM) i5-10600KF CPU @ 4.10GHz (12 logical CPUs), AMD64
- OS: Windows-10-10.0.19045-SP0
- Python 3.13.9, Blender 5.1.2, numpy 2.3.4
- Codecs available: zstd, zlib, none (used: zstd)

## Blender, headless, one object, strokes of ~2% of the vertices

| Metric | 99,904 verts | 500,000 verts |
|---|---|---|
| Stroke size (verts) | 1,998 | 10,000 |
| Memory per step, raw (bytes) | 32,120 | 159,520 |
| Memory per step, packed (bytes) | 9,254 | 43,962 |
| Full position array (bytes, for scale) | 1,198,848 | 6,000,000 |
| Recording overhead: `runtime.commit` median (ms) | 1.13 | 6.80 |
| Recording overhead: p90 (ms) | 1.61 | 9.36 |
| Scrub 1 step: `runtime.goto` median (ms) | 0.30 | 1.33 |
| Scrub 100 steps (ms) | 11.5 | 39.8 |
| Scrub 300 steps = whole history (ms) | 26.8 | 100.4 |
| Scrub 5,000 steps (ms) | 612.5 | not measured |
| Save, 300 steps (ms) | 97.7 | 324.5 |
| Load, 300 steps (ms) | 164.0 | 906.6 |
| History file, 300 steps (bytes) | 2,859,757 | 13,489,195 |

- 99,904 verts, 5,000 steps: history 45.6 MiB (9,559 B/step, newest 64 raw), save 248 ms, load 239 ms, file 46,485,186 B; recording 4,700 more strokes took 7 s including the simulated stroke writes.
- 500,000 verts: 5,000-step jump not measured (only attempted at 100k).
- Save/load method: bpy.ops.wm.save_as_mainfile + bpy.ops.wm.open_mainfile, save_mode=SIDECAR (includes writing/reading the .blend itself).

Definitions:

- Memory per step = `runtime.memory_stats()['total_bytes'] / steps` after 50 strokes. Raw = deltas held as numpy arrays (the newest 64 steps stay raw by default); packed = after `core.store.enforce_tiers` with `raw_steps=0` (compressed).
- Recording overhead = time spent inside `runtime.commit(obj)` after a stroke (read the sculpt channels, diff, push), median over all recorded strokes. It excludes the stroke itself.
- Scrub latency = time of `runtime.goto(context, pos)` including writing the mesh attributes; the newest 64 steps are raw, older ones are packed and decompressed on the way.
- Storage-engine numbers (pure numpy, no Blender) are in `bench/results_core.json`.
