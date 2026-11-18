# Performance log: history compression (file format 3)

Internal. Owner's brief (compression scope lock, 2026-10-14): 4 to 6 KB average per step at 100k
vertices for light strokes (about 2% touched), down from about 12.4 KB, fully lossless, behaviour
identical, no scenario more than 10% slower.

## Result in one paragraph

Every step is still stored exactly and restores bit for bit. On real strokes a light (2%) step at
100k vertices went from 10.8 KB to 7.9 KB (27% smaller) on the lat/long test mesh, and from 14.8 KB to
12.4 KB (16%) on a voxel-remeshed one; denser meshes gain more (41% at 500k, 47% at 1M). On the old
synthetic benchmark that produced the 12.4 KB figure, the same step is now 9.3 KB (25%). The 4 to 6 KB
target was not reached: on real strokes it isn't reachable losslessly with the techniques on the list
(see "Why not 4 to 6 KB"). Nothing got more than 10% slower: jumps through packed steps are faster
than before (10% to 47% depending on distance and mesh), commits and saves are unchanged within noise.

## How it was measured

- Real strokes: `tests_ui/scenarios/s_corpus.py` drives the real Blender 5.1.2 interface with simulated
  mouse input (18 sculpt brushes including mask, face sets and paint; small, medium, wide and large
  strokes) on generated test models, and dumps every recorded step. Five meshes: the 100k lat/long test
  model, the same model voxel-remeshed (140k vertices, scrambled vertex order), the same model
  decimated and subdivided twice (400k, subdivision vertex order), and the 500k and 1M test models.
  About 100 steps each. The files are git-ignored (`.tmp/corpus`); rerun the scenario to recreate them.
- Buckets by the share of vertices a step touches: "2%" is 1 to 3.5% (mean 2.0 to 2.2%), "10%" is 5 to
  16% (mean 7 to 9%; the wide strokes landed below 10%), "30%" is 16 to 45% (mean 20 to 23%).
- Every encoded step in the tables was decoded again and checked against the recorded old and new
  values, forwards and backwards (`bench/bench_format.py: check`).
- Format 2 numbers are produced by the format 2 writer itself (JSON header, XOR records, zstd level 3).
- Speed: old build = the commit before this work (`git archive d200842`), new build = this one, same
  machine, same Blender 5.1.2, runs alternated and the least disturbed run of each kept.
  CPU: see `bench/RESULTS.md`. The machine was in use during the runs (browser open), so differences
  under about 5% are noise.

## Stored size per step (bytes, mean), one technique added at a time

| Mesh | Stroke bucket | Steps | Mean touched | format 2 | container | difference | prediction | strong | Change |
|---|---|---|---|---|---|---|---|---|---|
| 100k, lat/long grid | 2% | 35 | 2.1% | 10,759 | 10,553 | 9,643 | 8,093 | 7,886 | -27% |
| 100k, lat/long grid | 10% | 9 | 7.2% | 38,965 | 38,746 | 33,395 | 27,982 | 27,403 | -30% |
| 100k, lat/long grid | 30% | 7 | 21.4% | 136,130 | 135,793 | 119,200 | 95,256 | 92,918 | -32% |
| 140k, voxel remesh | 2% | 36 | 2.0% | 14,757 | 14,545 | 13,489 | 12,769 | 12,429 | -16% |
| 140k, voxel remesh | 10% | 16 | 8.2% | 61,706 | 61,391 | 54,356 | 51,914 | 50,449 | -18% |
| 140k, voxel remesh | 30% | 7 | 23.1% | 201,319 | 201,027 | 190,080 | 176,530 | 172,300 | -14% |
| 400k, subdivided | 2% | 37 | 2.2% | 45,900 | 45,648 | 41,912 | 38,969 | 37,529 | -18% |
| 400k, subdivided | 10% | 9 | 7.1% | 154,420 | 154,225 | 136,721 | 128,604 | 124,682 | -19% |
| 400k, subdivided | 30% | 7 | 21.1% | 526,336 | 526,123 | 480,409 | 436,363 | 425,700 | -19% |
| 500k, lat/long grid | 2% | 36 | 2.1% | 51,269 | 51,044 | 43,225 | 31,232 | 30,041 | -41% |
| 500k, lat/long grid | 10% | 9 | 7.2% | 179,302 | 179,079 | 148,537 | 107,030 | 103,859 | -42% |
| 500k, lat/long grid | 30% | 7 | 21.2% | 612,274 | 611,964 | 513,855 | 357,616 | 347,521 | -43% |
| 1M, lat/long grid | 2% | 35 | 2.2% | 103,781 | 103,415 | 85,207 | 57,357 | 55,018 | -47% |
| 1M, lat/long grid | 10% | 11 | 8.7% | 411,286 | 411,230 | 335,959 | 226,649 | 220,374 | -46% |
| 1M, lat/long grid | 30% | 4 | 20.3% | 1,275,030 | 1,274,728 | 1,047,220 | 693,767 | 674,238 | -47% |

Stages: **container** is the format 3 step (binary header with its own CRC instead of JSON) with XOR
values as they are; **difference** stores new minus old (wrapping) instead of XOR; **prediction** adds
the zigzag mapping and a predictor per component (what the packer writes); **strong** is the same step
after idle-time recompression at zstd level 19.

Old synthetic benchmark (`bench/bench_bl.py`, the source of the 12.4 KB figure; contiguous strokes with
5% random noise per vertex, so prediction has little to work with): 12,361 to 9,254 bytes per step at
100k (25% smaller), 58,760 to 43,962 at 500k (25%).

## What each technique contributed (share of the format 2 size, 2% strokes)

| Technique | 100k grid | 140k remesh | 400k subdivided | 500k grid | 1M grid |
|---|---|---|---|---|---|
| Binary step container | -1.9% | -1.4% | -0.5% | -0.4% | -0.4% |
| Wrapping difference instead of XOR | -8.5% | -7.2% | -8.1% | -15.3% | -17.5% |
| Zigzag + per-component predictor | -14.4% | -4.9% | -6.4% | -23.4% | -26.8% |
| Background recompression (zstd 19) | -1.9% | -2.3% | -3.1% | -2.3% | -2.3% |

Prediction works along the order in which changed vertices are stored. It pays off most when
neighbouring vertex numbers are neighbours on the surface (grid-like meshes; denser meshes are
smoother between neighbours) and least on remeshed and subdivided meshes, whose vertex order jumps.

Already in format 2 and kept: one reversible array per step (XOR then, difference now) and component
split with byte shuffle. Index coding: gaps (minus one, so any stored bytes decode to sorted, unique
indices) or a single run.

## What didn't help (measured, not shipped)

| Idea | Measured effect | Why not |
|---|---|---|
| Grouped frames of 8, 16, 32 steps (cross-step context) | 0.0 to 0.4% smaller | Not worth losing single-step access |
| Chunk dedup across steps | 4 to 10% of non-trivial 64-byte chunks repeat, but grouping (which lets zstd see them) gains at most 0.4% | No gain left |
| Trained zstd dictionary (16, 64, 110 KB; trained on 100k and 500k, tested on the others) | -0.2% to +0.5% | Nothing to gain |
| Run-length and bitmap index coding | under 1% of a step | Slower to decode; the reader still accepts both |
| lzma instead of zstd | 4% smaller than zstd 19 | Decoding 10 to 15 times slower |
| Trying each encoding with a fast compressor (instead of an estimate) | 0.2% smaller | Several times the encoding cost |
| Sum of residuals as the estimate | 9% bigger than always using first order | Replaced by a bit-length estimate |
| Offset (bias) instead of zigzag | 12 to 47% bigger | Outliers spread the sign over high bytes |
| Two's complement residuals without zigzag | 11 to 33% bigger | Same reason |
| Rotation into the stroke direction (per 16 or 64 vertices) | 3% on the grid, up to 10% on the remeshed mesh (estimate, before storing the rotations) | Parameters and decode cost; would need integer-exact rotations |
| Rice-style split (low bits raw, high bits compressed) | 3 to 4% bigger than zstd on byte planes | zstd already codes the planes well |
| Prediction from mesh neighbours (topology) | Upper bound about 21% fewer residual bits on the remeshed mesh, none on the grid (idealised, not decodable) | Needs topology in the decoder, several decode passes |
| Fixed-point (exponent-aware) differences | 1.5% smaller (estimate) | Needs the mesh state to decode |
| Decoding ahead on worker threads during long jumps | 5 to 20% faster in plain Python, 70% slower inside Blender | Removed |

Most rows above were measured on the first corpus recording (same meshes and brushes, before the wide
strokes were added); rows marked "estimate" use the log-magnitude measure described below, not stored
bytes.

## Why not 4 to 6 KB

A light stroke at 100k changes about 2,000 vertices, so 4 to 6 KB means 5 to 8 bits per coordinate.
Format 3 stores about 10 bits per coordinate on the grid model (7.9 KB for 2,068 vertices). The
recorded strokes are smooth along each brush path, but the change of one vertex compared with its
neighbours still varies by tens to hundreds of steps of float precision: brushes like Clay Strips,
Smooth and Crease react to the local surface, the dabs of a stroke overlap, and each dab rounds
(`bench/lab/per_brush.py`). Measured as log-magnitude of the residuals (`bench/lab/order_entropy.py`,
`bench/lab/neighbour_oracle.py`): the best predictor along the record leaves 7.5 bits per coordinate
on the grid model, and an idealised predictor that sees every neighbour on both sides of each vertex
(which can't be decoded) also leaves 7.5. On the remeshed model they are 9.5 and 7.5. So on the grid
model nothing that predicts from neighbours can get much further; what remains is detail of the
sculpt, not redundancy. Removing it would mean storing something other than the exact result, which is
out of scope.

## Speed (old build against new, Blender 5.1.2, headless)

Synthetic strokes from `bench/bench_bl.py` (2% of the mesh, newest 64 steps raw, the rest packed),
least disturbed of alternated runs:

| Scenario | 100k old | 100k new | Change | 500k old | 500k new | Change |
|---|---|---|---|---|---|---|
| Commit, strokes back to back (ms, median) | 0.97 | 1.03 | +7% (noise range +-25%) | 5.29 | 5.29 | 0% |
| Commit, strokes spaced like a person sculpting (ms) | 1.09 | 1.08 | -1% | 5.19 | 5.33 | +3% |
| Jump 1 step through packed steps (ms) | 0.27 | 0.25 | -10% | 1.10 | 0.91 | -18% |
| Jump 100 steps (ms, `bench_bl`) | 14.0 | 11.5 | -18% | 45.1 | 35.3 | -22% |
| Jump 300 steps, whole history (ms) | 36.4 | 26.8 | -26% | 124.3 | 83.3 | -33% |
| Jump 5,000 steps (ms) | 699 | 572 | -18% | not measured | | |
| Save with 300 steps (ms) | 94 | 98 | +4% (noise) | 307 | 252 | -18% |
| Load with 300 steps (ms) | 170 | 164 | -3% | 790 | 811 | +3% (noise) |
| Save / load with 5,000 steps (ms) | 253 / 243 | 214 / 236 | -15% / -3% | | | |
| History file, 300 steps | 3.79 MB | 2.86 MB | -25% | 18.06 MB | 13.49 MB | -25% |
| History file, 5,000 steps | 64.4 MB | 46.5 MB | -28% | | | |

Commit times come from `.tmp/commit_probe.py` (three alternated runs each, median); the other rows
from two alternated `bench/bench_bl.py` runs.

Real strokes, storage engine only (`bench/scrub_ab.py`, whole corpus packed, `History.goto`):

| Corpus (about 100 steps) | Whole history back (ms) | Forward (ms) | One step (ms) | Stored |
|---|---|---|---|---|
| 100k grid | 38.7 -> 31.4 (-19%) | 38.7 -> 32.0 (-17%) | 0.28 -> 0.25 (-9%) | 3.03 -> 2.31 MB |
| 140k remesh | 51.8 -> 44.8 (-14%) | 53.9 -> 45.0 (-16%) | 0.38 -> 0.36 (-6%) | 4.66 -> 3.92 MB |
| 400k subdivided | 131.0 -> 115.3 (-12%) | 131.6 -> 114.0 (-13%) | 0.90 -> 0.87 (-4%) | 11.92 -> 9.70 MB |
| 500k grid | 156.5 -> 137.7 (-12%) | 157.2 -> 139.2 (-11%) | 1.07 -> 1.02 (-5%) | 13.94 -> 9.65 MB |
| 1M grid | 292.8 -> 256.4 (-12%) | 308.5 -> 251.6 (-18%) | 1.98 -> 1.88 (-5%) | 24.80 -> 16.14 MB |

Per step, real 2% strokes at 100k (`.tmp/onestep.py`): decoding 79 µs before, 76 µs now; applying 55
µs before, 40 µs now. At 500k: decoding 218 to 214 µs, applying 254 to 167 µs.

Where the speed came from: decoding does more work now (undo the zigzag mapping and the prediction),
so the rest got faster to pay for it. Applying a step uses `take` and an indexed write instead of an
in-place fancy update (about 35% faster), and a plain slice when the changed vertices are one
contiguous run; a single run decodes in one operation; records are parsed once; the predictor undo
works on whole arrays (two 32-bit values per 64-bit word for the zigzag step).

Encoding: the packer (background thread) takes about 370 µs for a 2% step at 100k (old: 170 µs) and
waits until no new step has arrived for 0.3 s, so it never competes with a commit. The crash journal
and saving write the quick form on the main thread: 190 µs per step (old: 170 µs). Background
recompression starts after 1 s idle and uses at most a quarter of one core.

## Memory for a 6,000-step history of 2% strokes

| Mesh | Mean stroke (vertices) | Before | Now | Saved |
|---|---|---|---|---|
| 100k, lat/long grid | 2,068 | 62.9 MB | 46.7 MB | 26% |
| 140k, voxel remesh | 2,731 | 86.2 MB | 73.0 MB | 15% |
| 400k, subdivided | 8,679 | 268.3 MB | 220.9 MB | 18% |
| 500k, lat/long grid | 10,603 | 300.6 MB | 180.4 MB | 40% |
| 1M, lat/long grid | 21,565 | 608.6 MB | 332.5 MB | 45% |

(64 newest steps uncompressed at 16 bytes per changed vertex, the other 5,936 at the measured packed
size.)

## Safety checks

- Checksums: CRC-32 inside every step blob (checked before anything is decoded), CRC-32 per step in the
  file (as in format 2), CRC-32 of the file's JSON header (trailer). Stated sizes are checked before
  any large allocation.
- `tests/test_core_codec.py`: 3,360 delta records and 480 full steps round-tripped through every codec,
  both encoder settings, the strong form and recompression, with NaN payloads, -0, denormals,
  infinities, extreme integers, empty steps, single elements, whole meshes and large gaps; 1,800
  damaged blobs (600 per codec, half with the checksum patched so the inner decoder sees the damage)
  and 600 damaged records: every one is refused cleanly or decodes, nothing else escapes, nothing hangs.
- `tests/test_core_migration.py`: history files written by beta 1 and by the format 2 code itself
  (`tests/fixtures`), checked at every position after loading, saving again, re-packing, and with new
  steps recorded on top.
- Verify-on-write in every test run (`HINDSIGHT_VERIFY=1`): each step written is read back and compared.
- `tools/mutation_test.py`: 23 deliberate breakages (arithmetic, index decoding, predictor choice,
  checksums, length checks, join handling); all are caught by the tests.
- Headless suite in Blender 5.0.1, 5.1.1 and 5.1.2: all files pass. Real-interface rounds: see
  TEST_ROUNDS.md.

## Reproduce

`<blender python>` is the Python inside a Blender install (`<Blender>/5.1/python/bin/python`):
it has numpy and zstandard.

```
python tests_ui/hs_ui.py build
python tests_ui/hs_ui.py run corpus --blender 5.1.2 --out-tag c100k --arg model=size_100k.blend --arg obj=Blob_100k --arg tag=100k
<blender python> bench/perf_log.py
<blender python> bench/scrub_ab.py <old core root> . 100k
<blender python> bench/bench_dict.py
<blender python> tools/mutation_test.py
```
(remesh: `--arg remesh=1`, subdivided: `--arg subsurf=1`; the old core root comes from
`git archive d200842 hindsight/core | tar -x -C <folder>`.) Exploration scripts behind "What didn't help"
are in `bench/lab/`; `bench/bench_codec.py` is the first lab (format variants side by side).

## Tier 1: recording other data (Record everything), test_bl_t1_perf

Measured 2026-10-15 on the development PC, medians of 5 runs (3 for the mesh check), headless.
Budgets in the test are about 3 times these; the test fails if one is exceeded.

| | 5.0.1 | 5.1.1 | 5.1.2 | Budget |
|---|---|---|---|---|
| small (6 objects): recording after a move | 2.0 ms | 1.8 ms | 1.7 ms | 8 ms |
| small: after parenting (relations change) | 2.4 ms | 2.5 ms | 2.4 ms | 10 ms |
| small: after adding an object | 2.9 ms | 3.1 ms | 3.1 ms | 12 ms |
| medium (60 objects, 20 on): after a move | 6.2 ms | 4.6 ms | 4.6 ms | 15 ms |
| medium: after parenting | 12.7 ms | 11.4 ms | 12.0 ms | 35 ms |
| medium: after adding an object | 23.0 ms | 12.5 ms | 17.7 ms | 40 ms |
| heavy (500 objects, 50 on): after a move | 13.4 ms | 15.0 ms | 13.6 ms | 45 ms |
| heavy: after parenting | 79.4 ms | 84.3 ms | 94.2 ms | 250 ms |
| heavy: after adding an object | 82.0 ms | 85.7 ms | 91.3 ms | 260 ms |
| restoring one step of an object | 0.07 ms | 0.07 ms | 0.07 ms | 2 ms |
| global jump, start to end and back (heavy) | 8.0 ms | 7.8 ms | 7.3 ms | 60 ms |
| step size: a move | 148 B | 148 B | 148 B | 512 B |
| step size: a constraint added | 360 B | 361 B | 361 B | 1 KB |
| step size: an object added (with a copy of its 500-vertex mesh) | 20.1 KB | 20.1 KB | 20.1 KB | 48 KB |
| Object Mode mesh check (Shade Smooth), 10k vertices | 3.4 ms | 4.8 ms | 4.7 ms | 20 ms |
| same, 100k vertices | 22.6 ms | 23.8 ms | 25.5 ms | 90 ms |
| same, 1M vertices | 266 ms | 470 ms | 237 ms | 2,000 ms |

In the heavy scene, an operation that changes relations (parenting, constraints, collections) makes
Blender report every object as updated, and with the scene recorded each object's stored details
(what brings a deleted object back exactly, D18) are read again: 129 of 160 ms in a profile. Reading
only the objects that can be deleted next would need to predict the selection; not done.

## Memory, and the Rust question

Measured 2026-11-18 with `bench/mem_profile.py` (writes `bench/results_mem.json`) on a shared Linux
VM: Intel Xeon at 2.10 GHz, 4 logical CPUs, 16 GB, Blender 5.3.0 as a Python module (Python 3.13),
headless, strokes simulated as in `bench/bench_bl.py` (about 2% of the vertices each, newest 64 steps
raw). Python-side numbers are tracemalloc (numpy reports its buffers to it) and `sys.getsizeof`;
process numbers are VmRSS and VmHWM, the peak reset before each measured operation. Blender's own
allocations (the mesh, ID properties) show only in RSS.

### Where it goes (held while sculpting)

| | 100k vertices, 6,000 strokes | 1M vertices, 300 strokes |
|---|---|---|
| Packed steps (blob bytes) | 51.6 MB (9,120 B per step) | 19.3 MB (85,900 B per step) |
| Mesh baseline (`MeshState`, one per recorded object) | 6.1 MB (topology 4.9 MB) | 61.0 MB (topology 49.5 MB) |
| Entry and metadata objects | 3.5 MB (605 B per step; 698 B after loading a file) | 0.2 MB |
| Raw tier (newest 64 steps, arrays) | 1.9 MB | 19.4 MB |
| meshio's reusable read buffers | 1.1 MB | 11.4 MB |
| Process RSS, Blender alone / with the mesh / after recording | 455 / 481 / 548 MB | 751 / 951 / 985 MB |

A second recorded object keeps its baseline while idle (6.1 MB at 100k): the baseline is what a
change made while it was not being edited is compared against, so it stays. A topology step (the mesh
rebuilt) holds a full `before` and `after` while it is in the raw tier: 6.1 + 6.1 MB at 100k, 61 + 61
MB at 1M (packed: 5 MB and 55 MB). Commits and jumps have small transients: a stroke commit 1.6 MB
at 100k and 16 MB at 1M (numpy temporaries of the diff, 1.3 times the position array), a jump over
all 6,000 steps 0.5 MB.

### What was cut (transients, measured before and after, same machine)

| Operation | Before | After |
|---|---|---|
| Save, history folder, 100k / 6,000 steps (Python peak, times the history) | 88.7 MB (1.29x) | 43.0 MB (0.63x) |
| Save, embedded (and Both), 100k | 251.5 MB (3.67x) | 205.8 MB (3.00x) |
| Save, history folder, 1M with three raw topology steps | 540 MB (2.81x) | 418 MB (2.17x) |
| Load, history folder, 100k | 140.7 MB (2.05x) | 83.3 MB (1.22x) |
| Load, embedded, 100k | 182.9 MB (2.67x) | 160.1 MB (2.34x) |
| Topology step commit, 100k (12 MB step) | 60.6 MB | 42.3 MB |
| Topology step commit, 1M (122 MB step) | 609 MB | 426 MB |
| Mesh fingerprint (saving, loading, jumps of objects recorded with Record everything), 1M | 61.0 MB, 2,424 ms | 30.5 MB, 2,044 ms |

What changed: the history file is written step by step from the blobs already in memory instead of
being joined into one bytes object (`persist.registry_parts`, `atomic_write` takes a list), and read
step by step from the file instead of reading it whole first; the embedded form drops each full-size
copy as soon as the next exists and decodes base64 from the stored text in place (`binascii`); a
step's snapshots are joined once, straight from the arrays (no `tobytes` copies, no second join for
the checksum); a topology step takes the baseline's arrays as its `before` instead of copying them
(the baseline gets fresh ones right after); the mesh fingerprint hashes one array at a time. File
formats are unchanged (same bytes as before, checked by the tests), every step is still stored and
restored bit for bit.

Speed (old build against new, alternated runs, medians; the VM was shared with other jobs, load
average 7 to 12, so differences under about 15% are noise). Storage engine alone
(`bench/mem_speed_core.py`, 100k mesh, 6,000-step history, last 3 pairs): writing a topology step
49 / 33 ms, writing a stroke step 0.064 / 0.064 ms, saving the history folder 90 / 80 ms (it needs a
1 MB write buffer: with Python's default 8 KB, writing step by step was 30% slower), loading it
43 / 48 ms (about 1 µs more per step in Python calls; on 9 KB random steps 82 to 111 ms old against
69 to 80 ms new), embedded form out 117 / 110 ms, in 117 / 71 ms. In Blender (`bench/mem_speed.py`,
300 steps, 5 alternated runs): commit 1.01 / 1.05 ms, 1-step jump 0.46 / 0.43 ms, 300-step jump
18.7 / 20.2 ms (neither path changed), topology commit 205 / 161 ms; whole-file save and open varied
from 90 to 240 ms run to run in both builds, so no difference could be measured there. The 5 ms
the history folder's loading can cost at 6,000 steps is under 1% of opening that file in Blender
(1.3 to 1.5 s in `bench/mem_profile.py`).

Not done, and why: the raw tier and the baselines are the biggest items left at 1M (19 MB and 61 MB),
but making them smaller means compressing what jumps and commits read directly (slower jumps), or a
new layer that unpacks the topology arrays on demand; the Entry/metadata objects are 6% of the
history at 100k (interning the label, mode and target strings after loading would save about 200 B
per step, 1.2 MB at 6,000 steps); meshio's read buffers (11 MB at 1M) are reused by every commit, and
freeing them when idle would make the next commit allocate again. Writing a topology step still peaks
at about 2.5 times its size: the compressor needs the joined body and its own output buffer;
streaming compression would remove that, at the cost of a codec change.

### The Rust question

(a) At 100k with 6,000 strokes, 60.7 of the 64.2 MB Hindsight holds (95%) is step bytes and numpy
arrays that a native core would hold at the same size. Python object overhead is the 3.5 MB of Entry
and metadata objects (605 to 698 B per step); a native core could get that to roughly 50 B per step,
saving about 3 MB (5%). At 1M with 300 strokes the Python share is 0.2 of 111 MB. The large
transients were copies made by the algorithm (joining, base64 strings, snapshot copies), not by
the language, and were fixed in Python.

(b) Shipping native code in a Blender extension means bundling wheels in the extension and listing
them in `blender_manifest.toml`: Windows x64, macOS arm64, macOS x64 and Linux x64, for Python 3.11
(Blender 4.2 to 5.0) and 3.13 (5.1 and later), or one abi3 wheel per platform built against 3.11
(the spike below loaded the same abi3 build in Python 3.11 and 3.13). That needs a CI matrix that
builds and tests on each platform (macOS signing and notarisation for the libraries), a per-platform
extension zip or one zip with all wheels, and the pure Python path kept as a fallback that stays
correct and tested, so every test would run twice.

(c) Spike (`delta.diff` for a 32-bit channel, PyO3 0.22 abi3 built with cargo 1.97, kept outside the
repository): on a 1M-vertex position channel with a 2% stroke the transient goes from 16.2 MB to 0.3
MB and the time from 5.1 to 5.1 ms (Python 3.13; 5.9 to 3.5 ms on Python 3.11), result identical. The
16 MB is returned to the allocator straight away (the RSS peak of a commit at 1M did not move), and a
commit at 1M is 12 to 23 ms in total, so this does not pay for (b). A Rust rewrite would not reduce
memory in a way users would notice; the remaining large items (packed steps, baselines, raw tier) are
data, the same size in any language.
