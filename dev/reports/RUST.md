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
