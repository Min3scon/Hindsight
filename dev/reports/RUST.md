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

Jumps 300 and 3000: JUMPS_PLACEHOLDER

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
