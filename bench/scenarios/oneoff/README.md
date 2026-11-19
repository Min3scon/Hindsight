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

`topo_mem.py CODE_ROOT grid1m|remesh` (R069): a recorded mesh's baseline before and after its topology is packed.
`edit_packed.py CODE_ROOT` (R069): an Edit Mode operator with Record everything at 300k vertices, with the baseline's
topology packed and unpacked.

`groups_ab.py CODE_ROOT` (R073): the global timeline's groups built for 10,000 and 100,000 steps, the earlier
function (kept in tests/test_core_global_position.py) against the current one in one process.
