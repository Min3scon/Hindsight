One-off profiling scripts left by earlier agents (draw, data recorder, jumps, save and load stages,
commit profile). They are NOT part of `bench/suite.py`; each was written against an older layout
and may need small path edits. The suite's scenarios in the parent folder cover the same ground.

`embed_ab.py CODE_ROOT` (R061): Save mode Embed at 100k vertices and 6,000 steps; prints one `AB {...}` line
with the median save and open times, the .blend size and the memory peak while saving. Run it with
`<bpy python> -I bench/scenarios/oneoff/embed_ab.py CODE_ROOT`, alternating the two roots under test.

`replay_ab.py CODE_ROOT [N]` (R064): crash recovery of N (3,000) unsaved strokes at 100k vertices; prints one
`AB {...}` line with the open time, how much the process grew over the open, the history's size and how many
of its steps are decoded.

`rawsave_ab.py CODE_ROOT VERTICES` (R066): what a save spends building the history it writes
(`persist.registry_parts`) with the newest 64 steps of a mesh still unpacked; prints one `AB {...}` line.

`open_journal_ab.py CODE_ROOT` (R067): `replay_ab.py` with the crash recovery setting taken from `HS_RECOVERY`
(`LAST_SAVED` times an open that lets an earlier session's journal go).

`fingerprint_ab.py CODE_ROOT [K]` (R060): mesh fingerprints at 100k and 1M vertices, `runtime.saved_prints()` with K (3)
recorded 1M meshes, and opening that file (`check_outside_changes` alone and the whole open); prints one `AB {...}` line
with the engine's version. A venv holds one engine version, so put each side's own engine (its wheel unpacked) first
with PYTHONPATH (no `-I`): `PYTHONPATH=<engine folder> <bpy python> bench/scenarios/oneoff/fingerprint_ab.py CODE_ROOT`.

`same_values_ab.py CODE_ROOT [ROUNDS]` (R070): the recorder's "nothing changed" check at 500 recorded objects, the
function before R070 (copied into the probe) against the current one, alternating in one process; prints one `AB {...}`
line with an operation that changes nothing, a move, and the time inside `_same_values`.

`diff_threads_ab.py CODE_ROOT [ROUNDS] [--inproc]` (R071): a texture paint stroke on a 2K float image and a sculpt stroke
on a 1M mesh, committed, with the time inside the engine's diff; `--inproc` alternates one thread against the engine's
own rule in one process. Put the root's engine first with PYTHONPATH.

`strong_level.py CODE_ROOT [ROUNDS]` (R072): the engine's recompress at zstd levels 19, 17, 15 and 12 (its `_strong_level`
switch) on cached 100k stroke steps, 1M strokes and 100k topology steps: CPU per step, bytes, read time.
`packer_level_ab.py CODE_ROOT` (R072): a store.Packer recompressing those stroke steps as after an open (its 1 s wait
between steps cut to 1 ms); the packer thread's CPU per step and the bytes. No Blender needed; alternate the engines.
