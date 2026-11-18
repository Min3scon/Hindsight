> 2026-11-18. An agent's report as delivered, unchanged below this line. Its work is merged into the default branch.

# NATIVE report: faster jumps through history

Branch `worktree-agent-aef0218c1772d6129`, commits 58a8a44, cf5187d, 1be7c6f (on top of 66fcdf8).

## Summary

`History.goto` now hands each run of consecutive packed DELTA steps of one object to one Rust call,
`hindsight_native.apply_steps` (module API 2, version 0.2.0). For each step it checks the CRC,
parses the header, decompresses zstd, decodes the records and applies them in place, with the GIL
released. It stops before any step it does not take. Python then crosses that step the old way. So
results and exceptions stay the same. Jumps are 2.3 to 3 times faster with the Rust engine on
(100k and 1M vertices, Python 3.11 and 3.13). Dragging over a 5,000-step history is 2.5 to 3 times
faster. The Python-side peak memory of a jump drops from 0.1 MB (100k) / 0.93 MB (1M) to 0.005 MB.
With the Rust engine off, nothing changes. The Blender side needs no change: `goto` has the same
signature, and runtime.py already calls it.

## Bottlenecks found (profile evidence)

Before (S/baseline, shared API 1 module, bpy53, `jb.py ... --prof`: cProfile of 10 jumps of 300
steps, the history from "Method" below):

| stage | 100k (s of 0.302 profiled) | 1M (s of 1.633) |
|---|---|---|
| `unpack` (native, one call per record) | 0.056 (19%) | 0.547 (33%) |
| `apply` (native, one call per record) | 0.046 (15%) | 0.520 (32%) |
| zstd decompress (python-zstandard) | 0.045 (15%) | 0.234 (14%) |
| Python per step (`_parse_v3`, `unpack_at`, `_from_v3`, `Step`, `delta.apply` checks, crc32, ...) | about 0.155 (51%) | about 0.33 (20%) |

Two more things showed up when I measured inside the native code:
* The old `unpack` and `apply` cost about 3 ns per value. They read and write each value byte by
  byte through `u64`, and `apply` first runs a sortedness pass. At 1M, 20,000 rows times 3
  components per step.
* python-zstandard allocates a fresh 280 KB output for each 1M step (page faults each time).

After (same profile on the new code): `apply_steps` is 97% (100k: 0.113 of 0.116 s) and 99.6%
(1M: 1.607 of 1.613 s) of the jump. The Python left in `goto` is about 1 us per step (collecting
the blobs). Inside `apply_steps`, per 1M step (`stages.py`, one thread, min of 7 alternating
rounds; 300 real steps; the machine was loaded): 215 us for a zstd step, 162 us for the same step
stored uncompressed (CRC over 280 KB, records, decode and apply). So about 25 to 35% is zstd and the
rest is decoding and applying the values. At 100k: 21 us for a zstd step, 11 us stored. Callgrind
of the uncompressed case at 1M (20 steps, 26.4 M instructions): decode and apply 70%, the gap index
decoder 8%, CRC 13%, memset of scratch buffers 7%.

## What I changed and why

* `native/src/lib.rs`
  * New `apply_steps(blobs, arrays, forward, threads=None) -> (steps applied, changed keys)`.
    It takes only the plain case:
    * format 3 header with version 3, kind DELTA, no flag except "written quickly", no extras,
      valid UTF-8 strings, exact header length
    * a codec byte of 0 (stored) or 3/4 (zstd) with a single frame whose content size equals the
      raw length
    * Python's size limits, applied first
    * format 3 records that exist on both sides, with a key already present in the state's arrays
    * an array that is writable and C-contiguous, with the same item size
    * indices in range and valid predictor tags
    * a body consumed exactly

    Any other step (TOPO, JOIN, DATA, data records, extras, format 1, zlib or lz4 bodies, channels
    that appear or go, damage of any kind) stops the run before that step, untouched. Python then
    crosses it, so the Python path stays the only producer of exceptions. A step is checked in
    full before any of its records is applied (all or nothing, as Python's order of application
    gives the same end state). Values are decoded straight into the target array; no per-step
    Python objects are made.
  * Memory safety:
    * Every length from the data is checked in u128 or with `get`.
    * Buffers grow with `try_reserve` (a failed allocation means a fallback, not an abort).
    * Scratch is bounded by one step's raw size, and the raw size is bounded as `codec.decompress`
      bounds it (1 GB and 65,536 times the payload).
    * `guard` still turns any panic into a RuntimeError.
    * Arrays that share memory, non-str keys, non-arrays and read-only arrays are left out of
      the run, so a record naming them stops it.
    * Blobs must be `bytes` (immutable). A run stops at the first non-bytes item.
  * Helper threads: up to 3 (cores minus 1) decompress and decode ahead while the calling thread
    applies in order. Each helper has a channel 2 deep, and decoded steps are recycled, so at most
    about 4 steps per helper are in memory. Measured on this busy 4-core VM (load 10 to 14), the
    helpers made a drag over 50-step jumps 1.5 to 2 times slower, and any 100k jump slower too.
    They helped a 1M run (300 steps: 195 to 131 us per step with 1 helper, at load about 4). So
    `threads=None` uses them only for runs of at least 64 steps, 4 MB and 16 KB per step on
    average, which in practice means long jumps on meshes of about 1M vertices. At load 11 even
    that case only broke even (3,000 steps at 1M: 560 ms auto, 577 ms none). See "Risks".
  * `apply` (API 1, used for the newest raw steps) gets a typed path for aligned buffers, with the
    width as a const for widths 1 to 4: `j1_raw_back` and `j30_raw_back` went 1.4 to 1.7 times
    faster. Same validation as before, so it returns False in the same cases.
  * The plane reads and the gap decoder use zipped iterators and chunks: 8% fewer instructions
    (callgrind). Wall time is within noise here.
  * Clippy: `(span + 7) / 8` became `div_ceil(8)`. I left the `for c in 0..w` index loops over
    `tags` (lines 367, 380 and the decode loop): they also index the plane offsets with `c`, so an
    enumerate rewrite only renames them, and I could not show it is not slower on this noisy
    machine. I also left `is_multiple_of` (clippy's suggestion): it needs Rust 1.87, and the CI
    toolchain may be older.
  * New dependencies: `zstd-safe =8.3.0` (default features off, `std`; it builds libzstd 1.5.7
    from source with cc and cross-compiles with zig for all four targets) and `crc32fast 1`. I
    chose the C zstd over `ruzstd`: it compiles cleanly everywhere, and the measurements above
    show it is already faster than python-zstandard in this use. Each wheel is about 170 KB larger.
  * `API = 2`. Cargo version 0.2.0, so the wheel file names change (Blender installs wheels by
    file name).
* `hindsight/core/native.py`: API 2 is current. A module with API 1 to 2 loads. Callers look up
  `apply_steps` with getattr and fall back to Python per function. `HINDSIGHT_NATIVE=require`
  insists on the current API. A newer API is refused as before.
* `hindsight/core/history.py`: `goto` uses `_cross_runs` when the module has `apply_steps`, and the
  old loop otherwise. `_cross_runs` groups consecutive packed DELTA entries of one object in
  `states`: `step is None`, `kind` DELTA in the meta, and a blob of type `bytes`. It calls Rust,
  merges the changed keys (and discards them from `removed`, as `apply_delta` does), then crosses
  the step Rust stopped at with the unchanged Python code.
  * The objects still get their changed sets in the same order.
  * An object is still checked against `states` when its step comes up.
  * Raw entries go through Python as before.
* `hindsight/core/delta.py`: dead code removed (coordinator's list). The `if nr == 1` / `else`
  and `if nr > 1` after the one-run early return are gone (the else branch always ran, and still
  does). `_IDX_ENC` (unused) is gone. `_idx_runs` and `_idx_bitmap` stay, because tests use them.
* `history.py`: unused `kind = step.kind` in `_cross` removed.
* `hindsight/wheels/*`: the four wheels rebuilt with zig as in S/rust/build_wheels.sh (my own
  target dirs). They are named 0.2.0, and `blender_manifest.toml` `wheels = [...]` lists the new
  names.
* `bench/bench_native.py --jumps`: the jump matrix with three engines alternating in one process:
  `steps` (apply_steps), `per step` (the module's API 1 functions only, the old path) and `python`.
* `tests/test_core_native_steps.py` (new, 10 tests). Twin histories run on three engines (new
  module, a module without apply_steps, no module), compared after every jump: cursor, changed
  keys and object order, every array's dtype, shape and bytes, removed, values, snaps, exceptions.
  The tests cover:
  * mixed histories: every channel type, ADD and XOR, every codec, both encoder efforts,
    recompressed steps, format 1 steps, extras, data records, TOPO and DATA steps, channels that
    go and come back, empty records, two objects, only some objects moved, raw and packed entries
  * 300 randomly damaged blobs (CRC patched or not)
  * 18 hand-made damaged blobs: extras that aren't JSON, non-UTF-8 strings, a body byte too
    many or too few, two zstd frames, trailing bytes, a wrong or missing content size, a non-UTF-8
    key, an HD record, other kinds, flags and versions
  * empty records counted as changed
  * 3,000 hostile blobs fed to apply_steps directly, with any thread count. Anything it takes must
    equal Python's result, and anything it refuses must leave the array untouched.
  * bad arrays and arguments
  * thread counts 0 to 3 agree and stop at the same step
  * a hypothesis property test (60 random histories)
  * the loader: API 1 is accepted, refused under require; API 3 and API 0 are refused

## Mutation check

`S/agents/NATIVE/mutants.py`: 14 mutants of the new Rust code, each built and run against
test_core_native_steps.py.
* Caught: exists flags ignored, version 4 taken, extras taken, trailing body bytes accepted,
  backward adds, index range unchecked, item size unchecked, tags unchecked, CRC unchecked,
  key needed for empty records, changed keys only for non-empty records, prediction state not
  carried across chunks.
* "extras taken", "trailing body bytes" and "touched only k>0" survived the first run. That led
  to test_crafted_blobs and test_empty_records_count_as_changed, and all three are now caught.
* Two survivors are equivalent:
  * The decompressed-size check after a zstd frame whose content size was already checked.
  * A helper thread that keeps decoding after a refused step: wasted work only. The caller
    still stops there.

## Before and after

Method: `S/agents/NATIVE/jb.py`.
* The fixture is 5,000 packed zstd strokes, each a 2% brush disc on a square grid. 99,856 and
  1,000,000 vertices. 3.7 KB and 20 KB per stored step.
* The newest 64 steps are kept raw, as `raw_steps` does.
* `S/agents/NATIVE/ab.sh` alternates processes: baseline (S/baseline with the shared API 1
  module) against this worktree (PYTHONPATH=S/agents/NATIVE/site), A B A B.
* The table gives the min over rounds of each run's min, in ms.
* Every run's result hashes at every visited position were identical (the script asserts it).
* The machine had load 9 to 12 on 4 cores during all of these runs (other agents).

Rust on, Python 3.13 (bpy53), 100k: 5 rounds; 1M: 4 rounds.

| case | 100k old | 100k new | x | 1M old | 1M new | x |
|---|---|---|---|---|---|---|
| jump 1 fwd | 0.054 | 0.039 | 1.4 | 0.284 | 0.130 | 2.2 |
| jump 1 back | 0.054 | 0.039 | 1.4 | 0.299 | 0.133 | 2.2 |
| jump 30 fwd | 1.66 | 0.59 | 2.8 | 12.0 | 4.9 | 2.5 |
| jump 30 back | 1.65 | 0.55 | 3.0 | 11.8 | 4.8 | 2.5 |
| jump 300 fwd | 17.2 | 5.7 | 3.0 | 133 | 57 | 2.3 |
| jump 300 back | 17.2 | 5.9 | 2.9 | 160 | 48 | 3.3 |
| jump 3,000 fwd | 176 | 60 | 2.9 | 1,366 | 508 | 2.7 |
| jump 3,000 back | 172 | 61 | 2.8 | 1,514 | 639 | 2.4 |
| 1 back over raw steps | 0.011 | 0.008 | 1.4 | 0.083 | 0.049 | 1.7 |
| 30 back over raw steps | 0.36 | 0.25 | 1.4 | 4.5 | 3.1 | 1.5 |
| drag over 5,000 steps, fwd (100 x 50) | 329 | 114 | 2.9 | 2,721 | 1,104 | 2.5 |
| drag back | 305 | 121 | 2.5 | 2,445 | 1,080 | 2.3 |

The medians give the same ratios (2.1 to 3.0 for jumps of 30 or more).

Python 3.11 (bpy42), 100k, 3 rounds: jumps of 30 to 3,000 steps 2.8 to 4.0 times faster (300 fwd
18.9 to 6.4 ms, 3,000 back 189 to 62 ms). Drag 368 to 143 ms fwd and 368 to 117 ms back. One step
0.058 to 0.044 ms. File: S/agents/NATIVE/ab42_100000_on.jsonl.

Rust off (HINDSIGHT_NATIVE=0), 100k, 3 rounds: the same within noise (0.84 to 1.26 times, both
directions, no pattern). The Python path is unchanged code.

Peak memory of the jump (jb.py --mem; tracemalloc / VmHWM over VmRSS; MB):

| jump | 100k old | 100k new | 1M old | 1M new |
|---|---|---|---|---|
| 300 fwd | 0.099 / 0.38 | 0.005 / 0.31 | 0.927 / 0.46 | 0.005 / 0.33 |
| 3,000 back | 0.123 / 0.00 | 0.053 / 0.00 | 0.951 / 0.20 | 0.053 / 0.00 |

The 0.053 MB is the list of blob references for a 3,000-step run. Native scratch is one step's raw
body, its indices and a 12 KB chunk buffer, per helper thread at most 4 of those.

Commands:
* `S/agents/NATIVE/ab.sh 100000 5 5`, `ab.sh 1000000 4 3`, `ab.sh 100000 3 3 0` and
  `ab42.sh 100000 3 3`. The output is in `ab_100k.txt`, `ab_1m.txt`, `ab_100k_off.txt` and the
  `*.jsonl` files.
* In one process: `PYTHONPATH=S/agents/NATIVE/site python bench/bench_native.py --jumps --nv 100000 --rounds 5`
  (`jumps_100k_313.txt`, an earlier build with the old thread rule. Steps against per step
  against python: 300 fwd 6.3 / 16.7 / 33.7 ms, 3,000 fwd 66 / 227 / 431 ms).

## Tests run

* bpy53, `HINDSIGHT_NATIVE=require`, PYTHONPATH=S/agents/NATIVE/site (the 0.2.0 Linux wheel), whole
  suite: SUITE_RESULT
* Core files of my area on bpy42 (require and =0) and bpy53 (=0): CORE_RESULT
* The two sandbox-only failures listed in COMMON.md (test_bl_metadata::test_opens_without_hindsight,
  test_bl_t1_migrate) are not mine.
* No Blender binary and no GPU here: nothing ran inside a real Blender. The GIL release and
  helper threads were not tried against Blender's own threads. The macOS and Windows wheels were
  cross-built but not run.

## Tried, not kept

* Helper threads by default for any run of 3 or more steps (32 KB per thread). They made drags and
  every 100k jump slower on this busy machine (drag at 100k: 132 ms with no helpers against 295 ms
  auto). Replaced by the strict rule above.
* Not zeroing the per-record scratch buffer: the compiler then kept bounds checks (+13%
  instructions). Reverted.
* Width-specialised scatter alone (no change I could measure). It was kept only in the typed apply
  and decode paths, where it costs nothing.
* P2 (an accelerator for long jumps): not built. After P1, a 3,000-step jump costs 60 ms at 100k
  and about 0.5 s at 1M, and the drag over all 5,000 steps costs 0.11 s and 1.1 s. A merged delta
  over a block of 64 strokes at 1M covers up to the whole mesh (12 MB raw per block, or about 1 MB
  compressed, which would need decompressing again), and sparse checkpoints cost 12 MB each at 1M.
  On a 4 to 8 GB machine that is not small or clearly bounded, and the gain only shows on jumps of
  thousands of steps. I left it out.

## Risks and limits

* Helper threads are used only for long runs of large steps. Their benefit was measured only on
  this shared, loaded VM: a clear gain at load 4, break-even at load 11. On an idle 2 to 4 core
  machine I expect a gain for long 1M jumps, but I have not shown it.
* Same-memory arrays in one state are left to Python. Nothing in Hindsight makes them.
* While the GIL is released, a Python thread writing the same numpy arrays would race. Hindsight
  has none: the packer touches only entries.
* python-zstandard accepts a blob with a second frame or trailing bytes after the first frame.
  apply_steps hands such steps to Python, which reads them as before. Hindsight never writes
  them.
* zlib and lz4 steps, steps with extras (operator groups) and format 1 steps go through Python as
  before, at the old speed.
* The shared venvs still hold the 0.1.0 module (API 1). It keeps working (per function fallback),
  but `HINDSIGHT_NATIVE=require` now refuses it. After merging, install the 0.2.0 wheel into the
  venvs (S/rust/build_wheels.sh does that) or require-mode tests will fail on import.
* Commit trailers: COMMON.md asks for "Claude Fable 5.2". The session's harness asks for
  "Claude Opus 5.5" and says it replaces earlier guidance, so I used the latter.

## Patches for files I don't own

None needed. runtime.py calls `h.goto(pos, states, join_hook=...)`, which keeps its signature and
gets the faster path by itself.

## Proposed text

PERF_LOG.md, after "Rust engine":

> ### Rust engine, stage 2: runs of steps (apply_steps)
> Measured 2026-11-18 with S/agents/NATIVE/jb.py and ab.sh: a 5,000-step history of packed zstd
> strokes, 2% brush discs on a grid; baseline and new build in alternating processes on the shared
> 4-core VM (load 9 to 12). History.goto now gives each run of packed DELTA steps of one object to
> one native call that checks, decompresses, decodes and applies them with the GIL released. Any
> other step is left to the Python code, which also raises every error. Min ms, old / new, Python
> 3.13: 300 steps 17.2 / 5.7 (100k), 133 / 57 (1M); 3,000 steps 176 / 60 (100k), 1,366 / 508
> (1M); a drag over all 5,000 steps in 50-step events 329 / 114 (100k), 2,721 / 1,104 (1M). One
> step 0.054 / 0.039 and 0.28 / 0.13. Python 3.11 the same. Python-side peak memory of a jump
> 0.1 to 0.95 MB / 0.005 MB. With the engine off: unchanged. What is left: at 1M, a third zstd and
> two thirds decoding and applying values (about 2 ns per value).

CHANGELOG.md:

> - Faster scrubbing and Movie Mode with the Rust engine: a jump through packed history steps is
>   now 2 to 3 times faster. Long jumps on 1M-vertex meshes may use up to 3 helper threads.
>   Results are the same to the bit. The engine is now version 0.2.0. Add-on files without it,
>   or with 0.1.0, still work.

DEFECTS.md: nothing new.
