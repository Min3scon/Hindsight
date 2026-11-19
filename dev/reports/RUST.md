# RUST report: M1, step container and codecs in Rust

Branch `worktree-agent-a388cd589ccb15b22`. Design: `dev/RUST_DESIGN.md`. Engine `hindsight_native` 0.3.0, API 3.

## What runs in Rust now

* Codecs: none, zlib (levels 1 and 9), lz4 frame, zstd (levels 3 and 19). C libraries built from source (zstd-sys, libz-sys static, lz4-sys). Bytes written equal the Python libraries' bytes (checked for all codecs, strong and normal).
* Step container, formats 3 and 4 written and read, formats 1 and 2 read: header, CRC, all kinds (DELTA, TOPO, JOIN, DATA), snapshots, extras and records as bytes, every delta record (format 3 and old format 1 records), `recompress` with read-back check, header info.
* Exceptions `CorruptStep`, `CorruptBlob`, `CorruptDelta`, `NewerFormatError` are Rust classes; Python modules re-export them.
* GIL released for compress, decompress, CRC, record decode. Decoded arrays: Rust `bytearray`, wrapped by `np.frombuffer` (owned, writable, no copy).
* `hindsight/core/native.py`: engine missing or not API 3 raises `EngineMissing` at import. `HINDSIGHT_NATIVE=0` now only turns off delta.py's per-function Python paths (still there, M2).

Stays Python (facade): JSON of extras and data records (Python objects), `_snapshot_parts` (numpy to buffers), `same_step` (used by `HINDSIGHT_VERIFY` and tests), `pack_delta` (until M2).

## Tests

All `tests/test_core_*.py` pass on bpy53 (3.13) and bpy42 (3.11), `HINDSIGHT_NATIVE=require`, with the 0.3.0 Linux wheel installed by `pip install --target` into a private folder. bl suites on bpy53: recorder 20, recovery 12 + faults 9, persistence 8, t0_gate 6, movie 14, all pass.

New `tests/test_core_reference.py` (16 tests), against `tests/reference/{step,codec}.py` (old Python, run with delta paths off):
* import guard (AST scan of `hindsight/`), no zstandard/lz4/zlib import in facade
* codec bytes equal python zstandard, lz4, zlib: 8 data sets x 4 codecs x strong/normal, 0 differences
* codec reads like the reference: 45 blobs x 7 size claims (trailing junk, second frame, truncation, bad id)
* 3,840 steps written, read, recompressed both ways: all bytes equal (hypothesis property too)
* corpus `tests/fixtures/step_corpus_base.zip`: 46 steps written by baseline 66fcdf8; baseline, reference and engine write identical bytes; engine rewrites 42 back identically; 4 format-1 steps read
* beta 1 and beta 2 registries: 26 steps read, equal
* hostile: 4,887 damaged blobs, 759 header-byte variants: no panic, no leaked type, accept/refuse agree with reference. 3 GB length claims refused, peak memory +0 MB.
* GIL: another thread ran 32,243 ticks during a 2 s compress.

Mutation or longer fuzz runs: not done.

## Measured, old (5a909c5) against new

Command: `python3 bench/ab.py --a <5a909c5 checkout> --rounds 5 --only commit_100k,commit_1m,save_folder_100k_1000,open_folder_100k_1000,save_embed_100k_1000,open_embed_100k_1000 --py bpy53`. Machine loaded (load 2 to 4). Min ms, old / new:

| scenario | old | new | ratio |
|---|---|---|---|
| commit_100k | 0.379 | 0.365 | 0.96 |
| commit_1m | 2.695 | 2.620 | 0.97 |
| save folder 100k 1000 steps | 95.4 | 95.1 | 1.00 |
| open folder 100k 1000 steps | 176.8 | 176.2 | 1.00 |
| save embed | 136.9 | 140.7 | 1.03 (inside spread 18%) |
| open embed | 200.9 | 202.7 | 1.01 |

Container alone (`bench_native.py --container --rounds 9`, reference against engine, one 2% stroke step, min ms, old / new): 100k write 0.066 / 0.065, read 0.035 / 0.030, recompress quick 2.24 / 2.09. 1M write 0.730 / 0.785 (median 0.814 / 0.812), read 0.197 / 0.196, recompress 21.97 / 21.42. Python peak: read 0.06 to 0.03 MB (100k), 0.60 to 0.32 MB (1M); recompress 0.12 to 0.01 MB, 1.16 to 0.04 MB. VmHWM not readable here (reads 0).

Jumps (`ab.py --rounds 4 --only _100k,_1m`, min ms, old / new): 100k jump_300 fwd 5.62 / 5.08, back 5.08 / 5.06; jump_3000 fwd 49.3 / 50.6, back 49.0 / 50.0; drag_5000 fwd 101 / 87.9, back 100.8 / 90.2. 1M jump_300 fwd 25.8 / 27.4, back 26.8 / 27.2; jump_3000 fwd 250 / 283 (spread 36%), back 248 / 246; drag_5000 fwd 719 / 747, back 654 / 701. All inside run spread (7 to 56%) except 100k drag, 10% faster. Jumps run through `apply_steps`, unchanged by M1: no gain or loss expected, none shown. First A/B (commit, save, open) ran before a container restart; jumps were rerun after it.

Reading: no loss anywhere; gain is small because stroke steps are small (6 KB at 100k) and the cost sits in the record transforms, already native. Migration is the point of M1, not speed.

## Differences from the Python reference (deliberate)

* Snapshot dtype strings limited to numpy numeric forms (`<f4`, `|b1` ...). Odd dtypes refused.
* `recompress` does not parse extras or records JSON; a step with damaged JSON is stored again as it was and refused on read, as before. Test checks it reads back the same as the input.
* Format 1 recompress: extras re-written as compact UTF-8 JSON by serde_json (same values; float spelling may differ).
* Hostile v1 steps: every failure is `CorruptStep` (was sometimes KeyError or JSON errors).
* Decode of zlib or lz4 or zstd without a size claim is bounded by 1 GB and 65,536 x payload (was unbounded).
* `HINDSIGHT_NATIVE=0` no longer gives pure Python; older or newer module API is refused.

## Tests changed on purpose

* `test_core_store::test_codec_fallback`: missing-codec simulation removed (all four codecs are inside the engine); asserts available list, resolve, all codecs round-trip.
* `test_core_codec::test_recompress_refuses_a_bad_result`: monkeypatching Python `step_to_bytes` can't reach Rust; uses engine hook `_test_fault(True)` (read-back reads a different step).
* `test_core_native::test_switch`, `test_core_native_steps::test_old_module_and_loader`: old, newer or missing module now `EngineMissing` in every mode.

## Wheels

Six abi3 wheels 0.3.0 rebuilt with `tools/build_native.sh` (Linux x86-64 and ARM64, Windows x86-64 and ARM64, macOS x86-64 and arm64), about 0.63 to 0.77 MB each, in `hindsight/wheels`; manifest list updated. Only the Linux x86-64 wheel was run. The others cross-built, not run.

## Not done, next

* M2 onward. `delta.py` Python paths (pack, unpack, diff, apply fallbacks) still in `hindsight/`; move to `tests/reference/delta.py` in M2.
* Clippy and Windows/macOS runs not done.

## Patches for files I don't own

* `tools/mutation_test.py` mutates strings of old `step.py` (`recompress` same_step line, `RECORDS_FLAG` read); those mutants no longer apply. Needs rewriting for Rust sources.
* `tools/ci/install_deps.py`, `.github/workflows/*`: keep zstandard and lz4 for the reference tests; hindsight itself no longer needs them.
* `INTERFACES.md` section 2 and `DEVELOPMENT.md` section c: step container and codecs are Rust; `native.py` raises `EngineMissing`; API 3.
* CHANGELOG proposal: "The step container and codecs moved into the Rust engine (0.3.0). zstandard and lz4 Python packages are no longer used."

---

# M2 (second job)

Merged main-session-3 (9547ce3) first. Engine 0.4.0, API 4; six wheels rebuilt, manifest and `native.py` updated.

## What moved to Rust

* `step_encode` takes the deltas as arrays `(key, idx, vals, itemsize, width, exists, add)` and packs the records itself, GIL released, arrays borrowed (no `pack_delta` call per channel, no joined temporaries). Bytes unchanged (3,840-step equivalence test still passes).
* New `record(...)` / `step.record_step`: diff of every channel + packing + codec + checksum in one call, GIL released after borrowing; None when nothing changed; appearing/going channels as None arrays; size-mismatch channels left out as `runtime` does. Test `test_record_equals_diff_and_encode`: 60 random strokes x 4 codecs x quick/normal x strong, bytes equal the Python reference writing the diffed Step.
* `diff` core shared (`diff_core`).

## D-d custom normals

`INT16_2D` (int16, 2, `value`) added to `CHANNEL_TYPES`; the engine handles itemsize 2 already, no native change. `tests/test_bl_custom_normals.py`: channel type through diff, step and apply (both Blenders); on 5.0+ Set Custom Normals goes back and forward exact (fails without the type: "Set Custom Normals made no step"). On 4.2 the Blender part prints `SKIP ... keeps custom normals in a hidden layer, not an attribute`; 4.2 custom normals stay unrecorded (needs a different reader, not an attribute channel).

## Tests

test_core_* all pass on bpy53 and bpy42 (HINDSIGHT_NATIVE=require, 0.4.0 Linux wheel in a private folder). bl recorder 20, recovery 12 + faults 9, persistence 8, t0_gate 6, movie 14, custom_normals 4 (3 on 4.2): all pass on both.

## Numbers

A/B `bench/ab.py --a <main-session-3> --rounds 5 --only commit,_100k,_1m,drag_5000_` (bpy53, load 3 to 5), min ms ms3 / m2: commit_100k 0.515 / 0.483, commit_1m 2.82 / 2.78, goto_bl_300_100k 5.57 / 5.19, jump_300 fwd 100k 5.86 / 5.11, jump_3000 fwd 100k 49.1 / 55.3 (spread 11%), drag_5000 fwd 100k 86.4 / 99.6, 1M jump_3000 fwd 262 / 296 (spread 37 to 41%), drag 1M fwd 653 / 656, back 697 / 657. Everything inside spread: no change shown, as expected, because `runtime.commit` does not call `record_step` yet and jumps are unchanged. No OPTIMISATION_LOG row claimed for the A/B.

In-process (`rec_bench.py` in the scratchpad, alternating 15 rounds, one 2% stroke, min ms): old path diff + Step + `step_to_bytes` vs `record_step`: 100k quick 0.181 / 0.152, normal 0.240 / 0.200; 1M quick 1.456 / 1.373, normal 2.040 / 2.022. Python peak 0.04 to 0.01 MB (100k), 0.39 to 0.06 MB (1M). Row for OPTIMISATION_LOG: "record_step vs diff + Step + encode, journal quick 100k: 0.181 to 0.152 ms (kept, 16%); 1M: 1.456 to 1.373 ms (6%); Python peak memory 6x lower".

## Unfinished

* `delta.py` Python fallbacks (diff, apply, pack_delta, unpack_at, `native.lib` None paths) are still in `hindsight/` and still used by twin-engine tests. Not moved to `tests/reference/delta.py`: needs engine versions of the cases `apply` refuses today (unsorted indices) and rewriting four test files. Next.
* Patch for `hindsight/bl/runtime.py` (not mine), where the stroke step is built (about lines 495 to 515) and `Entry.to_bytes` runs for the journal: call `step.record_step("DELTA", 0, time.time(), label, mode, uid, [(key, old, new, e0, e1), ...])` to get the quick blob in one call, keep building the raw Step for the history. Not applied, so commit time is unchanged.
* Windows and macOS wheels still never run.

---

# Third job: runtime patch (not applied), delta.py to reference, D-f

Merged main line 099815c (ENGINE_ZSTD change) at the end; core suites green after it.

## 1. `record_step` in `runtime.commit`: not applied, measured loss

`_observe` already diffs every channel once to build the raw Step the history needs. The journal blob then comes from that Step (`Entry.to_bytes`), and since M2 its records are packed in the engine. `record_step` would diff again. Measured (`rec2.py`, 100k / 1M, min ms): diff only 0.069 / 0.968; journal blob from the Step 0.030 / 0.222; `record_step` (diff + blob) 0.115 / 1.199. Using it means 0.115 instead of 0.030 at 100k: a loss. Kept in the engine and tested (it is the right call once the history keeps packed steps from the start, M3), not wired into runtime. Reverted by measurement, nothing to prove equal.

## 2. delta.py fallbacks moved to `tests/reference/delta.py`

* `hindsight/core/delta.py` now checks arguments and calls the engine for diff, apply, pack_delta, unpack_at (new engine `pack_record`, `unpack_record`; `apply` takes any indices: unsorted or repeated go through a gather, combine, scatter path with numpy's semantics; overlapping buffers are read from a copy). Engine API 5, hindsight_native 0.5.0, six wheels rebuilt, manifest updated.
* `tests/reference/delta.py` is the old numpy code (native branches removed). `reference.pure()` swaps the engine's delta functions for it; `test_core_native.py` compares engine against it as before (diff, apply 612 cases plus adversarial, pack 8,740 records, unpack 24,008, 6,000 damaged records).
* Still in `hindsight/`: `as_xor`, `same_change` (numpy, used by undo matching), `packed_size` (struct arithmetic), `native.lib` None switch (only turns off `History.goto`'s `apply_steps` fast path; the per-step Python loop goes with M3).
* Changed on purpose: apply of values that don't fit the indices says "N indices but M values" (numpy's broadcast message before; same type, array untouched; test compares type and bytes). Read-only target keeps numpy's words. Index out of range for unsorted input is a ValueError (numpy raised IndexError).
* Check: diff and apply timing unchanged (`dtime.py`, 100k diff 0.083 old / 0.081 new ms, 1M 1.02 / 0.94; apply equal).

## 3. D-f Weight Paint

* `WEIGHT_PAINT` is a record and track mode (`runtime.RECORD_MODES`, `TRACK_MODES`, label "Weight Paint", follow-mode list). A stroke's step holds the weights of every vertex group (channels `vg:<name>`, NaN where the vertex is not a member, so a member at 0.0 differs from a non-member), read in `_observe` for Weight Paint whether or not Record everything is on. Entering Weight Paint on a state read without weights (a file just loaded) takes the weights as baseline instead of recording them as a step (found by the reload test: it made a bogus fifth step).
* Read: `meshio.read_weights` (loop over `me.vertices` with `enumerate`). Measured at 100,489 vertices, 3 groups (`wbench.py`, min of 5, exact on all): current loop 84.6 ms (5.3) / 64.2 ms (4.2); enumerate 65.6 / 47.8 ms; bmesh 64.3 / 66.1 ms. Chosen: enumerate, a quarter to a third faster on both. Write: existing remove and add by value, 47 / 44 ms; bmesh write 92 / 95 ms, not used. No native helper: the cost is the Blender API call per vertex.
* Tests `tests/test_bl_weight_paint.py` (6 tests, bpy53 and bpy42): strokes are steps with exact membership, go back and forward over every position, a stroke after going back replaces the undone step, save and reload then scrub, journal step blobs hold the weight channels, Blender's native undo moves the cursor, an object without groups. All fail without the change ("a weight stroke made no step") and pass with it. Journal replay itself was not driven end to end (the recovery suites pass; the test checks the journal payload decodes to weight deltas).
* Unfinished: adding or removing a group during a stroke on an object without Record everything is not a list step (the group stays, weights come back); the old read is still used for full reads (same function, faster).

## Tests run

test_core_* 14 files: pass on bpy53 and bpy42 (HINDSIGHT_NATIVE=require, 0.5.0 Linux wheel in a private folder). bl recorder 20, recovery 12 + faults 9, persistence 8, t0_gate 6, movie 14, custom_normals 4 / 3, weight_paint 6, t1_lists, t1_meshdata, engine_missing: pass on bpy42 and bpy53 (engine_missing on 53). Whole `test_bl_` on bpy53: 61 of 71 files pass; the 10 that fail (adapters, metadata, t1_added, t1_corrupt, t1_gate, t1_jumps, t1_migrate, t1_prop, t1_real_undo, t1_reload) fail with the same counts on the baseline 9547ce3, so not from this branch.

## Numbers (A/B against 9547ce3)

`bench/ab.py --a <9547ce3> --rounds 5 --only commit,_100k,_1m,drag_5000_`, bpy53, load 3 to 5, min ms old / new: commit_100k 0.436 / 0.550 (spread 33%, medians 0.615 / 0.663), commit_1m 2.78 / 2.92 (medians 3.43 / 3.29), goto_bl_300_100k 5.42 / 5.65, jump_300 fwd 100k 5.21 / 4.99, jump_3000 fwd 100k 49.2 / 48.5, drag_5000 fwd 100k 98.4 / 99.4, 1M jump_3000 fwd 292 / 292, drag_5000 fwd 654 / 647. All inside run spread (2 to 58%). No gain or loss shown; no OPTIMISATION_LOG row except the weight read: "read vertex group weights, 100k verts, 3 groups: 84.6 to 65.6 ms (5.3), 64.2 to 47.8 ms (4.2), exact, kept".

---

# Fourth job: CI subnormal time, D-f gaps, texture painting (Multires not started)

## CI failure `test_hostile_blobs` (assert e1 == r1)

Reproduced the two causes locally (`repro.py`): the mutation lands in the 8 time bytes of the header (blob offsets 22 to 29).
* A time of zero of either sign: the reference writes `float(time or 0.0)`, so -0.0 goes out as +0.0; the engine kept the bits. Fixed in the engine: `assemble` writes a zero time as +0.0 (platform independent). Read-back check treats the two zeros as equal. Engine 0.5.1, same API 5, six wheels rebuilt, manifest updated.
* A subnormal time (your blob: `00 ff 00 00` before the raw length): Python's `time or 0.0` is falsy for a subnormal only where the process flushes subnormals (Blender sets DAZ, so the real binary and the bpy build on Windows flush it; the local numpy/Python does not). The reference therefore writes 0.0 there and the engine keeps the bits. Decision: the engine keeps the bits, everywhere (exact, no platform dependence); the test accepts it: when the input time is subnormal, recompress output must have the same length and the input's time bits, and everything else still equal.
* Deterministic regression: `test_subnormal_time_is_kept` (every byte of the time field set to 0xFF, 0x01, 0x80, 0x7F, checksum patched: 32 mutations; zero time becomes +0.0, subnormal and ordinary times kept; reference equality checked wherever the reference is platform independent). Passes on bpy53 and bpy42. Not run under DAZ here (no way to set it from Python); that is exactly the CI case.

## 1. D-f gaps

Tests in `tests/test_bl_weight_paint.py` (now 9, bpy53 and bpy42): a group added between strokes (steps restore weights exactly, the group stays and is empty before its first stroke), a group removed between strokes (the others restore exactly; the removed group can't come back, a list step would), and crash recovery replay: save, two more strokes only in the journal, reopen without saving, history has all four steps, every position exact. Save and reopen was already covered (`test_save_and_reload`). These pass without code changes (gaps in tests, not bugs).

## 2. Texture painting per stroke (preference off by default)

* `prefs.record_texture_painting` (False), listed under History. `runtime.texture_on()`, `runtime.FORCE_TEXTURE` for tests. TEXTURE_PAINT is a record and track mode only while the preference is on; with it off `commit` returns None and no baseline is read.
* A stroke step is a DELTA step of the painted object with one channel per image of its materials' texture nodes (plus the image paint canvas): key `img:<name>`, float32 RGBA, engine diff and pack. Baselines are read when entering the mode (and after a rebuilt mesh); an image with no baseline or a new size takes one silently (no step). Restore writes pixels back and calls `image.update()`. Memory: step bytes are normal step bytes under the existing caps; the live baseline of each image (4 floats per pixel, 17 MB at 1K, 67 MB at 2K) and one reusable read buffer are not counted by the caps.
* Cost per stroke, read + diff (`imgbench.py`, 3 repeats, min ms, bpy53 / bpy42): 1K 3.1 + 2.3 = 5.4 / 4.7 + 1.8 = 6.5; 2K 13.3 + 8.3 = 21.6 / 19.5 + 7.6 = 27.1; 4K 61.9 + 37.4 = 99 / 89.4 + 37.7 = 127; write on restore 1K 4.6 / 5.2, 2K 16 / 21, 4K 66 / 108 ms. 4K also holds 268 MB for the baseline and 268 MB for the read buffer: too much for 4 to 8 GB machines. Decision: images over 2048 x 2048 are not recorded (`meshio.TEXTURE_MAX_PIXELS`), and the preference text says so. Proposal for later: a dirty-rectangle read (Blender gives none to Python) or a tile hash would lift the limit.
* Tests `tests/test_bl_texture_paint.py` (7, bpy53 and bpy42): off by default records nothing, byte and float images exact across strokes, back and forward, two images on one object, images over 2K left out, steps hold only the changed pixels, leaving the mode and painting again. Failing before: no TEXTURE_PAINT mode.
* Not done: the Image editor's paint mode (no object, no stroke-end signal found; needs a real window), a real-window check (owner rule: stays off), saving and reopening an unsaved generated image (it is not in the file).

## 3. Multires step per Sculpt Mode visit: not started

Needs a new step kind that carries a .blend datablock snapshot (the container holds arrays, so it would be a uint8 channel), the `libraries.write/load` plus `user_remap` restore, undo-stack handling and its own failing-first tests, and real-Blender checks I can't run here. A half version would be worse than the pause it replaces, so I stopped before starting it. Proposal: do it as its own job, after M3 (History in Rust) settles what a step may hold.

## Tests run

test_core_* (14 files) pass on bpy53 and bpy42 with the 0.5.1 module. bl recorder, recovery, persistence, t0_gate, movie, weight_paint, custom_normals, t1_meshdata, t1_lists, texture_paint pass on both. `tools/check_copy.py`: 0 problems.

## The 10 baseline test_bl_ failures (names)

test_bl_adapters, test_bl_metadata, test_bl_t1_added, test_bl_t1_corrupt, test_bl_t1_gate, test_bl_t1_jumps, test_bl_t1_migrate, test_bl_t1_prop, test_bl_t1_real_undo, test_bl_t1_reload (bpy53). Not my environment: I ran them on a plain `git archive 9547ce3` with no PYTHONPATH (venv engine 0.3.0, as the main session does) and got the same pass and fail counts per file. The tip of main-session-3 (c7bb443) passed t1_jumps, t1_added, t1_gate, t1_corrupt and adapters when I tried it, so those five were fixed after 9547ce3.

---

# Fifth job: Multires, one exact step per Sculpt Mode visit

Merged main-session-3 (81bc008) first (fast-forward). No engine change (0.5.1, API 5).

## Design (also in `dev/RUST_DESIGN.md`)

* New module `hindsight/bl/multires.py`, preference `record_multires` (off by default, listed under History in the add-on preferences, `check_copy` clean).
* Step: kind TOPO, extra `{"multires": 1}`. `before` and `after` are Snapshots with one int8 channel `multires:blob|POINT|INT8`: the mesh datablock written by `bpy.data.libraries.write` (displacements and shape keys travel with it), compressed with the engine's zstd before it goes into the Step (so the raw tier and the caps count compressed bytes). Journal, save and load need nothing new.
* Restore: `libraries.load` the mesh, put the original materials on it (the load makes copies, which are removed), `user_remap`, remove the old mesh, rename. Done in Object Mode (the TOPO path leaves and re-enters the mode).
* A visit: `begin_visit` on entering Sculpt Mode takes the baseline, refreshed at the first tick after 0.25 s (Sculpt Mode adds its own layers to the mesh after its first update, which is not a change). Leaving Sculpt Mode marks the visit ended; the step is taken at the next timer tick, before a jump (`_goto_history`) or before a save (`persistence.on_save_pre`, one added line): inside the mode change the mesh is not settled yet (4.2 gave a different blob there, so a no-op visit looked like a change). A change of the Multires levels while sculpting ends the visit's step and starts the next. A visit whose blob digest equals the baseline makes no step.
* With the preference on, `warnings` no longer pauses recording for Multires (`_has_multires` is False), and `runtime.commit` of such an object returns None unless it is given the visit's step (`_mr_step`). With it off, everything is as before (a test checks the pause stays).

## Deterministic change for tests

Strokes can't run headless. `multires_reshape` against a displaced copy of the evaluated top level (noise from a seeded generator) changes the displacement layers identically every run; it runs inside the visit with the mode switch hidden from the recorder (`runtime.suspended()`), and leaving the mode takes the step.

Exactness check: hashes of the evaluated vertices at every level 0 to top plus the base mesh. The bytes of a written mesh are not compared: they are not a function of the content alone (a restored mesh differs by a flag; on 4.2 the size differs between two restores), but the visit detector compares two blobs of the same mesh in the same state, which are equal.

## Tests `tests/test_bl_multires.py` (7, bpy53 and bpy42)

Off by default pauses as before; three visits make three steps and every position (back, forward, jumps) is exact for every level, materials and modifier kept; a visit that changes nothing makes no step; a level change takes the step; save and reopen then scrub; crash recovery (save, one more visit only in the journal, reopen without saving, both visits replay exactly); step sizes are the compressed blob (counted by the caps). Failing before: the module and mode did not exist (no step was made).

Tests run: all above plus recorder 20, recovery 12 + faults 9, persistence 8, t0_gate 6, movie 14, weight_paint 9, texture_paint 7, custom_normals, t1_meshdata, t1_lists, panels, warnings on both venvs; `test_core_*` on both. Whole `test_bl_` on bpy53 (without t1_prop): see the end of this section.

## Measured (`mrbench.py`, bpy53 / bpy42; min of 3 writes; `libraries.write` to a temp file and read; zstd level 3; load = `libraries.load` + remap, min of 2)

10,086-face base (cube, 40 cuts), unsculpted displacement (zero, so it compresses extremely; a real sculpt compresses much less, my noise test about 1.0):

| level | quads | write | size | zstd | compressed | load + remap |
|---|---|---|---|---|---|---|
| 2 | 161,376 | 12 / 6 ms | 7.7 / 7.4 MB | 10 / 11 ms | 0.5 / 0.5 MB | 31 / 30 ms |
| 4 | 2,582,016 | 60 / 41 ms | 42.6 / 42.3 MB | 39 / 59 ms | 0.5 / 1.7 MB | 104 / 87 ms |
| 6 | not measured: 41 M quads, over 600 MB of displacement | | | | | |

Level 6 on an 80-face base (327,680 quads): write 7 / 4 ms, 3.3 MB, zstd 3 / 4 ms, load 21 / 8 ms.
Noise-displaced Suzanne-like sphere (tests, level 3, 3,842 verts at top): blob 345 KB, a step (two sides) about 250 to 350 KB each side at zstd 3: random displacement does not compress.
Cost per visit at a 10k base and level 4: one write at the start (about 60 ms), one at the end (60 ms) plus compress (40 to 60 ms); a jump costs a load (about 100 ms). Memory of a step is two compressed blobs; a sculpt that touches a lot of the surface makes them large (up to the blob size, 42 MB at level 4 each side), which the caps then evict as for any step.

## Unfinished

* Not tried in a real window (the preference stays off): real strokes, Blender's own undo inside a visit (the visit is the unit; undo during a visit is not tracked), and a timer that fires between leaving the mode and the step.
* The first visit's `before` blob is taken inside Sculpt Mode, so going back to position 0 restores the mesh with Sculpt Mode's own layers; the geometry and displacement are as before.
* Objects sharing a Multires mesh, and a Multires modifier added or removed between visits, are not handled specially (a changed modifier stack is a normal change of the mesh blob only).
* A restored mesh is a new datablock (same name, same users); anything holding a pointer to the old one (the shared-mesh bookkeeping of D39/D44 when Record everything is on) may take it for a mesh change.

Whole `test_bl_` on bpy53 without t1_prop: 81 of 84 files pass; the three failures are metadata and migrate (known) and t1_hair_points (fixed upstream in main-session-4 8c1838d).

---

# Sixth job: M3, measured; History on the engine only

Merged main-session-3 (dbad258) fast-forward. No engine change (0.5.1, API 5), so no wheel rebuild.

## Result: the native `History` object was not built; why

Evidence (`mixprof.py`, `topodec.py`, bpy53, 100,489 vertices, min of 30): a full jump over a mixed history of 1,500 packed steps (2% TOPO, 2% DATA) takes 105 ms: 61% is `step_decode` of the 60 TOPO and DATA steps (a TOPO step: 1.24 ms, of which zstd decompression alone is 1.27 ms; the `set_snapshot` copy is 0.07 ms), 31% is `apply_steps`, about 3% is Python glue. A native History would replace that 3%. `Entry` holds raw Python Steps that runtime and tests edit through `h.entries`, so owning entries in Rust would add a conversion at every access. Caps and eviction (`store.py`): O(1) per commit, a full pass every 64 commits, entangled with the packer thread and Entry objects; nothing measurable to gain. Not done, on purpose; written up in `dev/RUST_DESIGN.md` ("M3 revisited").

## What changed (the part of M3 that is real: no Python path left in history)

* `hindsight/core/history.py`: `goto` always crosses runs of packed steps with `ENGINE.apply_steps`; the per-step Python loop (`_steps`, the `native.lib` branch) is gone from the shipped add-on. Steps the engine doesn't take are crossed one at a time in `_cross` (snapshots, data records, appearing channels; that is the 3%).
* `tests/reference/history.py`: `History` subclass with the old loop as `goto` (the oracle). `test_core_native_steps.py` twins compare engine `History` against it (3 engines became 2: the "older module" mode went with the per-function module versions; "python" is the reference class); the hostile direct calls compare against it.
* `hindsight/core/native.py`: `HINDSIGHT_NATIVE` and the per-function switch are removed; `lib` is the engine, `AVAILABLE` True. `test_switch` updated: the variable has no effect; a wrong or missing module still raises `EngineMissing` in every mode.
* `tests/reference/__init__.py` `pure()` only swaps the delta functions now. `bench/bench_native.py`: `--jumps` compares the engine History with the reference History; `both()` uses `pure()`.
* Numbers (`bench_native.py --jumps --nv 100000 --rounds 2`, min ms engine / reference loop, machine load 7): 30 steps 0.49 / 1.02, 300 steps 4.96 / 10.48, 3,000 steps 48.9 / 103.5, drag over 5,000 steps 85.7 / 172.3. The reference loop runs on the native codec and delta, so this is the cost of crossing in Python step by step, not the old pure-Python time.

## Tests

`test_core_*` (14 files) pass on bpy53 and bpy42. Whole `test_bl_` without t1_prop on both: result below. No A/B is claimed for jumps: they run the same code as before (`apply_steps` unchanged).

Whole `test_bl_` without t1_prop: 82 of 84 files pass on bpy53 and on bpy42; the two failures are test_bl_metadata and test_bl_t1_migrate (known, need a Blender binary and `.tmp/old/b1.zip`).

Appends (`append.py`, 5,000 steps at 100k vertices, bpy53): `History.push` 1.7 us per step. `enforce_caps` full pass 1.46 ms average over a history that grows to 5,000 entries (it sums `nbytes` over every entry); the runtime runs a full pass every 64 commits, so about 25 us per commit amortised, against a commit of about 400 us. Not worth moving.

For OPTIMISATION_LOG (nothing kept as an optimisation; one measured non-attempt): "native History object: not built. Python glue is 3% of a mixed 100k jump (105 ms: 61% decode of TOPO and DATA steps, bound by zstd decompression 1.27 ms per 2.4 MB, 31% apply_steps); push 1.7 us; caps pass about 25 us per commit amortised. Nothing to gain; History made engine-only instead."

---

# Seventh job: R4 step 1 (record_step in `runtime.commit`) measured, not applied

Fast-forwarded to main-session-3 (2d105aa). No code change in `hindsight/` or `native/`.

## Profile of `runtime.commit` first (`cprof.py`, bpy53, 100k vertices, Stroker strokes of 2%, 60 commits, cProfile on; journal on)

Median 0.95 ms with the profiler (about 0.5 ms without). By own time: `step_encode` 17% (the journal blob, zstd level 3 on the main thread), `diff` 14%, Blender `foreach_get` 10%, `commit` self 5%, `_observe` 3.5%, `tag_redraw_all` 3.5%, journal file writes 3%, everything else under 2% each.

## Why `record_step` does not pay here (`recmeasure.py`, min and median of 200, same arrays as a Stroker stroke)

`record_step` replaces diff + Step + `step_to_bytes`. The runtime still needs the raw Step's deltas (they are what the history keeps and what `goto` applies), and `record_step` returns only the blob, so using it means diffing twice. Best case, if the engine also returned the deltas and the blob were re-stamped with the final seq and extra (a new engine call: new header and CRC over the body), the saving is the Python glue only:

| | diff + Step + journal blob | `record_step` | saving |
|---|---|---|---|
| 100k, 1,988 changed | 0.151 min / 0.193 med ms | 0.141 / 0.151 | 0.010 to 0.042 ms (2 to 8% of a 0.5 ms commit) |
| 1M, 19,893 changed | 1.652 / 1.765 | 1.560 / 1.650 | 0.09 to 0.115 ms (3 to 4% of about 2.8 ms) |

And with the journal off (no `journal_hook`) the blob is never built today; building it in the commit would add its compress cost. The saving is below the run spread of `bench/ab.py` (commit_100k spread 25 to 38%), so an A/B could not show it; no A/B run, no code written. For OPTIMISATION_LOG: "record_step in commit: not built; upper bound 0.01 to 0.04 ms of a 0.5 ms commit at 100k (3 to 4% at 1M), needs engine API change; reverted before coding".

## Next measured hotspot: the journal blob's compression

Time of the journal blob alone (QUICK step, one stroke), min ms and size, 100k / 1M: zstd (today) 0.187 ms, 10,119 B / 1.652 ms, 91,510 B; lz4 0.115, 10,775 B / 1.300, 103,754 B; none 0.108, 23,966 B / 1.234, 238,826 B (each includes the 0.064 / 1.01 ms diff). So zstd costs about 0.08 ms at 100k and 0.4 ms at 1M on the main thread: 15 to 16% of a commit. Using lz4 or none for the crash journal would take most of it back (journal 2.4x larger with none, 1.06x with lz4) and recovery reads any codec. I did not change it: it changes the bytes the journal holds (not the history), which rule 8 puts under "unless the brief says so". Proposal for the owner: journal blobs with lz4 (cost 0.07 ms less at 100k, 0.35 ms at 1M, size +6 to 13%). Alternatively compress on a thread, which needs the journal writer (core/journal.py, persistence.py) to take ownership of the order of records; bigger change.

---

# Eighth job: journal step blobs with lz4 (approved)

Merged main-session-3 (245df4e). No engine change.

## Change

`types.JOURNAL_CODEC = "lz4"`; the five places that build a journal STEP blob (`runtime.py` x2, `datarec.py`, `joinsep.py` x2) pass it instead of `SETTINGS.compression`. History, packer, save and the user's codec setting are unchanged (`registry_parts` still uses the user's codec).

## Tests

* `tests/test_core_journal_codec.py` (4): the constant, old zstd journal, new lz4 journal and a mixed one (zstd, zstd, lz4, lz4, zlib, none) replay to the same steps; one file written with zstd records, closed, reopened and appended with lz4 records (an upgrade) replays all four exactly; size growth on test strokes (x1.03).
* `tests/test_bl_journal_codec.py` (1): a real stroke commit writes an lz4 blob whatever the history codec is (zstd, zlib, none, auto).
* Green on bpy53 and bpy42 (HINDSIGHT_NATIVE=require): test_core_* (all, including test_core_journal and test_core_journal_faults), test_bl_recovery (12) and recovery_faults (9), persistence (8), recorder (20), t0_gate (6).

## Measured: in-process A/B, journal on with real `Journal` appends (`jab.py`, bpy53, alternating zstd / lz4 order flipped each round, 16 rounds x 6 strokes = 96 commits per side, same mesh and strokes)

| | zstd min / median | lz4 min / median | lz4 / zstd | journal bytes per step zstd / lz4 |
|---|---|---|---|---|
| 100k (2% strokes) | 0.608 / 1.122 ms | 0.482 / 0.977 ms | x0.79 min, x0.87 median | 10,754 / 10,892 (+1.3%) |
| 1M | 3.654 / 5.398 ms | 2.936 / 5.008 ms | x0.80 min, x0.93 median | 97,337 / 104,304 (+7.2%) |

Kept. For OPTIMISATION_LOG: "journal step blobs lz4 instead of zstd level 3: commit with journal on, 100k min 0.608 to 0.482 ms, median 1.122 to 0.977; 1M min 3.654 to 2.936, median 5.398 to 5.008; journal +1.3% (100k), +7.2% (1M) bytes per step; kept (owner approved)".

Also for "Tried": `record_step` in commit: not built, upper bound 0.01 to 0.04 ms of a 0.5 ms commit at 100k (3 to 4% at 1M), needs engine API change. cProfile of commit at 100k (60 commits, profiler on, median 0.95 ms): `step_encode` (journal blob) 17%, `diff` 14%, Blender `foreach_get` 10%, `commit` self 5%, `_observe` 3.5%, `tag_redraw_all` 3.5%, journal writes 3%, rest under 2% each.

## Next hotspots: no concrete change, none tried

* `diff` (0.064 ms at 100k for 2.4 MB read, 1.01 ms at 1M for 24 MB): about 24 to 37 GB/s, already near memory bandwidth for a block-wise compare. Only more threads could help, which compete with Blender's own sculpt threads on the 2 to 4 core machines this is for. Not tried.
* `foreach_get` (about 0.1 ms per 1.2 MB position channel): Blender's call, no bulk alternative that reads less; a partial read would need to know which vertices a stroke touched, which Python does not get.

---

# Ninth job: data recorder overhead, round 1; D146 (node group animation)

Merged main-session-3 (b3aa2e8, then 9a78ce5).

## Profile (`drprof.py`, bpy53, 500 cubes, a third with a constraint, `finish_operation` with nothing changed, load 6 to 7)

51 ms per operator (min 50.9, median 51.4). cProfile, 5 operators: `datarec.capture` 2,500 calls, 0.41 s cumulative of 0.55 s: `RNAAdapter.capture` 0.173 s (own 0.055), `ObjectAdapter.capture` 0.158 s, `normalise` 76,000 calls 0.08 s, `_same_values` 0.047 s (marshal compare), `idmap.claim` 7,500 calls 0.038 s, `idmap.find` 5,010 calls 0.032 s, `id_props` 5,010 calls 0.027 s, `struct_values` (constraints) 905 calls 0.022 s, `path_resolve` 50,000 calls 0.026 s. So 100 us per object per operator: about 20 RNA rows read by `path_resolve` plus `normalise` per row, then the object adapter's parts, then the compare.

## Round 1, one hypothesis: RNA rows through `getattr` and plain values past `normalise`

`RNAAdapter.capture` read every row with `idb.path_resolve(path)` and called `normalise` on the result. A path that is one name (no dot, no index; nearly all rows) needs no path parsing (`getattr` 0.09 to 0.27 us against `path_resolve` 0.33 to 0.53 us), and float, int, bool and str results are returned as they are by `normalise`'s first line, so the call is skipped. Same values, same exceptions (an AttributeError is caught like the ResolveError was); `kind_on` inlined (`ENABLED_KINDS[0]` read once per datablock).

* In-process A/B (`h1.py`, old and new `RNAAdapter.capture` over the 500 objects, alternating, 15 rounds, results compared equal on 50 objects first): 9.91 / 10.59 ms (min / median) old, 7.68 / 8.55 ms new: x0.78 / x0.81. That is 2.2 ms of the 50 ms operator: 4 to 5%.
* `bench/ab.py --only dr_finish,dr_prime,dr_on_update`, 7 rounds against main-session-3 b3aa2e8, bpy53, load 4 to 6 during the run: dr_finish_nochange_500 min 39.2 / 40.7 ms (x1.04), median 49.7 / 47.6 (x0.96); dr_finish_move_500 41.6 / 46.1 (x1.11), median 52.2 / 52.1 (x1.00); dr_prime_500 118.7 / 111.6 (x0.94), median 137.1 / 130.8 (x0.95); dr_on_update_500 x1.03 / x1.07 (0.12 ms, noise). Spread 8 to 58%: the 4 to 5% expected is below it, so `ab.py` can neither confirm nor refute; the in-process A/B is the evidence.
* Kept: exact (values, key order checked equal), test_bl_adapters*, test_bl_datarec*, every test_bl_t1_* (t1_prop separately) on bpy53 and bpy42 pass. Row for OPTIMISATION_LOG: "RNA adapter rows read with getattr and plain values past normalise: RNAAdapter.capture over 500 objects 9.91 to 7.68 ms min (10.59 to 8.55 median), in-process alternating 15 rounds; ab.py inconclusive (spread 8 to 58%, load 4 to 6); kept".
* Next candidates seen in the profile, not tried: `id_props` per object and per data (5,010 calls) and `idmap.claim` (7,500 calls) are the two largest remaining fixed costs; constraints (`struct_values`, 905 calls) are read every operator. Any cache of them must be validated by the depsgraph or a fingerprint (custom properties and constraint values change without a tag; R041's hair_points case).

## D146: keys and drivers on node group inputs

`group_record` had no animation. Now the group's own animation (`anim_of(group)`: how it plays, and its action when the group is its only user) is recorded as `@nga:<group id>` next to the group's `@ng:` record, by the same owner rule as D126 (the object that records the group; the scene for groups no object uses, D133), always present (None without animation) so the first key is a change. Written back after the group (`write_anim_of`); the object's kept parts (R041) depend on the groups' actions as well as the groups. `owned_path` and `kind_of` know the new prefix. Test `test_group_animation_comes_back` in `tests/test_bl_t1_node_groups.py`: a key on a Set Position offset, a second key, a driver on a Math input, an edited driver expression; every position back and forward exact; fails before the change (two steps instead of four), passes on bpy53 and bpy42. DEFECTS row D146 added.

## D147: a UI drag became two "Change" steps

Two parts, both in `datarec.py`, tests in `tests/test_bl_datarec_settle.py` (5, fake clock `datarec._now`, both venvs):

* Adaptive wait: `on_update` notes the time of every update that made something dirty (`_note_update`); the gap between the last two updates (only gaps under 1.5 s count) sets the wait: `max(SETTLE, 1.5 x gap)` (`_settle_time`). Isolated changes keep 0.3 s. By itself this cannot fix the reported case: the first gap (0.44 s) is longer than 0.3 s, so the timer has committed before the second update is seen.
* Continuation: a "Change" step committed by the settle timer (new `settled=True` flag, passed only by `tick`) is remembered per history (`_RECENT`). If the next batch of changes begins within 1.5 s of it (measured from the batch's first update, `_CLOCK["batch"]`), changes only values that step changed, and the step is still the newest of its history with the cursor at the end, the two become one step (same seq, old values of the older step, new values of the newer), replacing the entry; the journal gets a TRUNC and a STEP at the same position (replay reads that already); if the merged step changes nothing (the drag ended where it began) the step is removed. Operator-end commits, direct `commit()` calls (scripts, tests) and changes to other values are never merged: a first version merged any two quick Change commits and broke test_bl_tier1 and test_bl_adapters (two quick scripted edits are two steps), so it is limited to the timer and to the same paths.
* Tests: the 0.44, 0.24, 0.23, 0.25, 0.23, 0.24 s drag is ONE step and back goes to the start in one jump (it was two); a single change still commits after 0.3 s (not at 0.25 s); the wait follows the gap (0.31 s after an update that followed a 0.24 s gap: no step yet, 0.41 s: step); a drag back to where it began leaves no step (with a stored value: an unset property reads as the default, which is not the same record as an explicit default); a change after another step is not merged and a change 5 s later is separate.
* Not covered headless: the real-window probe (panel slider dragged 8 x 6 px) for you to rerun; journal replay of a merged step was reasoned from the existing replay semantics (STEP at an earlier position truncates) but not driven end to end.
* DEFECTS row D147 added.

---

# Tenth job: LIMITS-A 1.6, the TOPO chain (measured round)

Merged main-session-3 (b5bea4a). Engine 0.6.0 (API 5 unchanged), six wheels rebuilt, manifest updated. Only PIDs I started were killed.

## Measured first

Real Blender 5.3, 100,442-vertex sphere, six TOPO steps made by topology edits between commits (`chainbl.py`: Object Mode subdivide plus move hidden from the recorder, `runtime.commit`): before this round, `before` of each step held its own copy of the mesh the step before had left, and the packed body put the two meshes of a step in one zstd level 3 body whose window (2 MB) never reached back to the first mesh.
* In memory: arrays held by the six raw steps 117.2 MB (the state's copies were taken, not shared).
* Packed: 7.47 MB per step, 125 ms per step to pack, goto 0 and back over the six packed steps 405 ms.
* Synthetic 100k mesh with local edits (`topochain.py`): one mesh 6.4 MB; a step body (before + after) 13.1 MB; level 3 default window 5.24 MB; level 3 with a 16 MB window and long distance matching 2.66 MB, the same as `after` alone (2.64 MB). Level 19 (the packer's) already gets 1.86 MB. lz4 9.42 MB.

## Decision: two changes, no format bump

* Packed (engine, `native/src/codec.rs`): a zstd body over 1 MB is compressed with `windowLog` = ceil(log2(size)) (at most 27, zstd's default decoder limit) and long distance matching (ldm hash log 20); smaller bodies keep the level's own settings (the parameters are reset on every call, the compressors are reused). The frame is an ordinary zstd frame: no step format change, every reader of this and earlier builds decodes it (a test decodes with python's zstd, which knows nothing of the build). Written bytes for bodies over 1 MB differ from before (smaller); bodies under 1 MB are byte-identical (test_core_reference still compares them to the Python libraries). This removes the second mesh of a step from the blob instead of sharing it across steps: a cross-step reference needs the neighbouring entry at read time, breaks on drop-oldest, truncate and the independent journal and save records, and needs a format bump; the gain left on top of this is the `after` of each step (one mesh per link), which only a delta-coded `after` could cut (not attempted).
* In memory (`core/history.py`, `History.push`): a new TOPO step's `before` takes the previous raw TOPO step's `after` arrays wherever they are bit for bit equal (checked with an exact compare, 1 to 2 ms per 6 MB mesh, against a commit of 100+ ms). Safe because nothing writes into a step's arrays: the state takes copies (`set_snapshot`), `goto` writes into the state's arrays. Only raw neighbours share; a DELTA between two TOPO steps ends the sharing for the arrays it changed. Byte counts are unchanged (`Step.nbytes` still counts both snapshots, so the caps count the chain as before and evict a little earlier than the memory needs; changing that would change eviction).

## Before and after (same script, `chainbl.py`, bpy53, SHARE=0 with the 0.5.1 engine against SHARE=1 with 0.6.0)

| | before | after |
|---|---|---|
| arrays held, 6 TOPO steps, 100k | 117.2 MB | 68.4 MB (-42%) |
| packed per step (level 3) | 7.47 MB | 3.83 MB (-49%) |
| pack time per step | 125 ms | 105 ms |
| goto 0 and back, 6 packed steps | 405 ms | 261 ms (-36%) |

Synthetic: blob per TOPO step 5.30 to 2.68 MB (-49%), pack time 79 to 80 ms. Kept. For OPTIMISATION_LOG: "TOPO chain: zstd window as large as a body over 1 MB plus long distance matching, and `before` sharing the previous raw TOPO step's `after`; 100k-vertex chain of 6 TOPO steps in Blender 5.3: arrays held 117.2 to 68.4 MB, packed 7.47 to 3.83 MB per step, pack 125 to 105 ms per step, jump over the chain 405 to 261 ms; kept; caps still count the old size".

## Exactness: tests/test_core_topo_chain.py (5, bpy53 and bpy42)

Sharing is by identity for consecutive TOPO steps and memory drops; 12 jumps across a history of TOPO, DELTA and DATA steps give the same state and changed keys as a history that owns its arrays, and no array of any step changes during the jumps; save and reload (registry blob) then jumps equal; drop-oldest keeps the next step's `before`; a step after going back truncates; a 100k-size step is about one mesh in the blob (within 1.25 x `after` alone), reads back exactly with the engine and with python's zstd; a quick (journal-style) step recompresses with the strong setting and is the same step. JOIN steps hold per-member snapshots and are not shared. The journal blobs are lz4 and not touched. Failing first: before the change the blob is 2 x (5.30 against 2.68 MB measured above) and the arrays were separate.

## Not done

* Delta-coded `after` (a TOPO step storing only what differs from its `before`): would take the chain to a few hundred KB per step for local edits; needs a format bump and a rule for edits that renumber everything (Dyntopo reindexing).
* Caps accounting by unique arrays.

Tests: test_core_* (all, including the new test_core_topo_chain) on bpy53 and bpy42 with the 0.6.0 wheel; whole test_bl_ (t1_prop included) on both: 86 of 89 files, the failures are test_bl_metadata and test_bl_t1_migrate (known) and test_bl_datarec_fast_paths test_changes_while_off_then_on, which fails the same way on the unchanged main-session-3 tip b5bea4a (not from this round; looks like the D147 marker change).

# M5: journal framing and the registry stream (measured first; one change kept: the engine's crc32)

Merged main-session-3 (71a8846) fast-forward first. Engine 0.7.0, API 6 (new function `crc32`), six wheels rebuilt, manifest updated.

## Measured first (bpy53, load average 5 to 7, 4 cores; 6,000 steps, 52.3 MB, the coordinator's `load_sc.py`)

`registry_from_bytes` through `_FileBytes`: 44.0 to 45.6 ms min (45.4 to 47.9 median). cProfile: file reads 27%, the loop's own Python 26%, zlib.crc32 18%, json 6%. Floors measured on the same file: reading the whole file into one bytes 25.8 ms (the kernel's copy, about 2 GB/s on this machine), zlib.crc32 of 52 MB 24.7 ms (2.1 GB/s), json header 5.5 ms. `crc32fast` does 20 GB/s (3.2 ms for 64 MB; 0.19 us against 2.0 us for a 9 KB step).

## Attempts (for OPTIMISATION_LOG)

1. **Registry step run read in Rust** (`registry_steps(path, pos, counts, crcs)`: u64-length walk, blob copied into a fresh bytes with the GIL released, CRC-32, damaged and missing slots as None; prototype, 1 MB BufReader): 34.7 ms min for the steps and the Entry objects plus 5.9 ms json = about 41 ms against 45 ms. **Reverted, not kept**: the reads are the kernel's copy (floor 26 ms), Rust only removes the interpreter loop, and the extra copy through the buffer eats most of that. Cost: a second reader to keep bit-equal for formats 1 to 4, truncation, damage cuts. For a 10% gain.
2. **crc32 in the engine** (`hindsight_native.crc32(data, init=0)`, same value as zlib.crc32; the GIL is released for a bytes object over 64 KB; used by registry load and save, the journal and `Entry`): load 44.0/44.6/45.6 -> 31.5/34.0/33.6 ms min (**-29%**), median 45.4-47.9 -> 32.7-36.3. **Kept.** Python structure unchanged (the loop and the Entry contract are as before).
3. **Journal framing in Rust** (prototype `journal_scan(path, limit, want_payload...)`, CRC over a reused 1 MB buffer): the size scan Journal() does on open, 52.8 MB journal of 6,000 steps: 30.5 ms (zlib) -> 21.4 ms (Python loop, engine crc32, kept) -> 7.3 to 8.1 ms (Rust scan). `read()` of everything: 74 -> 59 ms with engine crc32; the rest is the json of 6,000 headers and the payload copies. **Reverted, not kept**: 13 ms more on a path that only runs when a file with a journal is opened (the journal is emptied at every save), for a second crash-tolerant reader to keep exactly equal.
4. **Journal writer in Rust**: not built. `append` of 6,000 steps (52.8 MB) 159 ms, almost all of it the write into the page cache; one append is 2.2 us for a header-only record and about 26 us for a 9 KB step, of which the CRC was 2.8 us and is now 0.24 us. 20,000 small appends: 1.89 against 2.19 us (min of 8 alternating rounds, same within noise).
5. **`registry_parts` in Rust**: not built. 7.5 ms for 6,000 steps, mostly the json of the metas and building the list; the blobs and CRCs are already carried by `Entry` (R046).

## Coordinator addition: step metas share their strings

`registry_from_bytes` makes the `label`, `mode`, `target` and `kind` values of equal text one object per load (a dict local to the call, not `sys.intern`); `extra` dicts are not shared. `meta_mem.py`, 6,000 steps: metas 599 -> 388 B/step (deep size, shared counted once; 3.43 -> 2.22 MB, held by Python 56.88 -> 55.68 MB; 100,000 steps: about 21 MB less). Parse time: 30.3 to 32.9 ms with against 29.3 to 32.0 ms without, same within noise (`load_sc.py`, 3 alternating runs). Kept. Journal replay builds its entries from decoded steps (`Entry(step=...)`), not from JSON metas, so there is nothing to share there.

## Tests

`tests/test_core_framing_crc.py` (9 tests): crc32 equals zlib.crc32 for 22 sizes (0 to 3 MB, around the 64 KB threshold), start values, six buffer kinds, chaining, bad buffers; a second thread runs while 40 MB are hashed; the pre-M5 `persist.py` and `journal.py` (zlib) are kept as `tests/reference/persist.py` and `tests/reference/journal.py` and compared with the live code: bytes written (journal, registry for five codec settings), what is read back (entries, metas, CRCs, cursors, morph targets, `load_report`, error class and message), for the whole files, every cut at every byte (journal 2,337 files; registry 79,203 files in all with flips), every byte flipped three ways, damaged steps with and without the saved CRC patched to match, claimed step lengths up to 2^64-1, a 9.5 MB journal record (over the 4 MB read chunk), the two real legacy files (formats 1 and 2) cut and flipped, newer format refused, loaded metas share strings and not `extra`.
