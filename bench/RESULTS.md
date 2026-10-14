# Hindsight benchmark results

Measured with `bench/bench_core.py` (pure numpy storage engine) and `bench/bench_bl.py` (real Blender). **The Blender numbers are from the headless bpy module (bpy 4.2.23 LTS, `import bpy` in Python 3.11.17), not the Blender GUI**: no viewport redraw, no sculpt PBVH rebuild cost, strokes simulated by writing vertex positions and calling `runtime.commit`. Expect the GUI to add its own redraw time on top of scrub latency. Test mesh: a lat/long quad grid on a sphere built with `foreach_set`, in Sculpt Mode.

## Environment

- CPU: Intel(R) Xeon(R) Processor @ 2.10GHz (4 logical CPUs), x86_64
- OS: Linux-6.18.44-fc-v114-x86_64-with-glibc2.39
- Python 3.11.17, bpy 4.2.23 LTS, numpy 2.4.6
- Codecs available: zstd, lz4, zlib, none (used: zstd)

## Blender (bpy module), one object, strokes of ~2% of the vertices

| Metric | 99,904 verts | 500,000 verts |
|---|---|---|
| Stroke size (verts) | 1,998 | 10,000 |
| Memory per step, raw (bytes) | 32,120 | 159,520 |
| Memory per step, packed (bytes) | 12,361 | 58,760 |
| Full position array (bytes, for scale) | 1,198,848 | 6,000,000 |
| Recording overhead: `runtime.commit` median (ms) | 1.07 | 3.58 |
| Recording overhead: p90 (ms) | 1.44 | 4.57 |
| Scrub 1 step: `runtime.goto` median (ms) | 0.39 | 1.51 |
| Scrub 100 steps (ms) | 13.0 | 55.8 |
| Scrub 300 steps = whole history (ms) | 36.3 | 161.1 |
| Scrub 5,000 steps (ms) | 723.9 | not measured |
| Save, 300 steps (ms) | 105.4 | 739.8 |
| Load, 300 steps (ms) | 118.1 | 677.9 |
| History file, 300 steps (bytes) | 3,788,806 | 18,061,546 |

- 99,904 verts, 5,000 steps: history 62.5 MiB (13,102 B/step, newest 64 raw), save 723 ms, load 256 ms, file 64,310,319 B; recording 4,700 more strokes took 10 s including the simulated stroke writes.
- 500,000 verts: 5,000-step jump not measured (only attempted at 100k).
- Save/load method: bpy.ops.wm.save_as_mainfile + bpy.ops.wm.open_mainfile, save_mode=SIDECAR (includes writing/reading the .blend itself).

Definitions:

- Memory per step = `runtime.memory_stats()['total_bytes'] / steps` after 50 strokes. Raw = deltas held as numpy arrays (the newest 64 steps stay raw by default); packed = after `core.store.enforce_tiers` with `raw_steps=0` (compressed).
- Recording overhead = time spent inside `runtime.commit(obj)` after a stroke (read the sculpt channels, diff, push), median over all recorded strokes. It excludes the stroke itself.
- Scrub latency = time of `runtime.goto(context, pos)` including writing the mesh attributes; the newest 64 steps are raw, older ones are packed and decompressed on the way.
- Storage-engine numbers (pure numpy, no Blender) are in `bench/results_core.json`.
