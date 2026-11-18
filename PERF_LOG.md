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

## Movie Mode playback

Measured 2026-11-18 with `bench/bench_movie.py` (results in `bench/results_movie.json`) on Blender
5.3.0 as a Python module, headless, 4-core Xeon at 2.1 GHz with no GPU. Each scene is a numpy grid
with strokes of about 2% of the vertices, recorded with commit as the tests do. Movie Mode on, Quick
auto key with Frames per step 2, then `scene.frame_set(f)` on every frame of the movie, which is what
a render does before it draws each frame. "Add-on" is the time spent in Hindsight's frame change and
depsgraph handlers, timed around each call; "Blender" is the rest of `frame_set`. The before run
shared the machine with short test runs for part of the time, so Blender's own numbers moved by up to
15% between the runs; the add-on numbers are from the handlers alone.

| Scene | Frames | Add-on per frame, step changed | Add-on per frame, same step | Add-on total | `frame_set` total |
|---|---|---|---|---|---|
| 100k x 2 objects, 1,000 steps, before | 2,001 | 3.64 ms | 1.13 ms | 5.2 s | 12.0 s |
| same, after | 2,001 | 1.33 ms | 0.12 ms | 1.6 s | 11.0 s |
| 100k, 5,000 steps, before | 10,001 | 22.6 ms | 8.3 ms | 216 s | 307 s |
| same, after | 10,001 | 2.7 ms | 0.13 ms | 15.9 s | 93 s |
| 500k, 300 steps, before | 601 | 3.65 ms | 0.63 ms | 1.5 s | 36.2 s |
| same, after | 601 | 2.6 ms | 0.15 ms | 1.0 s | 24.9 s |

Medians. The worst frame in each scene is the first one, which undoes every step (1.5 to 1.8 s at
5,000 steps, 90 ms at 500k); that is the same work as a jump from the end to the start and did not
change.

What the profile showed (cProfile, own time per frame including the profiler's overhead, before):
the handler's bookkeeping was 88% at 1,000 steps and 92% at 5,000 steps. Every frame built the global
order of all steps from scratch (`globaltl.global_groups`, a sort of every step of every history) five
times: twice in `scrub_info`, then in `global_goto` for the position before, the plan and the
position after. Applying steps (decode and apply) was 6 to 8%, writing the mesh under 2%, the crash
journal 0.2% (one small record per frame, no fsync), redraw tagging 0.1% and Blender's undo nothing
(no undo push happens inside the handler). At 500k vertices the split was bookkeeping 30%, applying
28%, writing 7%, the rest numpy and Blender calls.

Changes, in order of what they saved:

1. While Movie Mode is on, the global order is built once and kept between frames
   (`runtime._groups`). It is built again when any history gains, loses or replaces steps or
   objects, when what is visible changes, and dropped when Movie Mode is off. The globaltl functions
   take the list instead of building it again (`groups=`); without it they work as before.
2. A frame that shows the step already shown does no history work: `movie._apply` remembers the
   Step value it applied and every history's cursor and length right after, and returns before
   `scrub_info` when both are unchanged (0.12 to 0.15 ms left, mostly listing the visible objects).
3. `global_goto` reads the position after a jump only when Follow object and mode needs it (it is
   off inside the handler).
4. Sub-frames: Cycles with motion blur calls the handler at (f, 0), (f - 1, +0.75), (f, +0.25) and
   (f, 0) for each frame f. The Step value at a sub-frame is the F-Curve between two steps, so the
   mesh went back and forth between two steps inside one frame (17 history jumps for 10 frames; the
   blur then mixes two states). Sub-frame calls now keep the step of the whole frame being rendered
   (7 jumps, the same as without motion blur), and a sub-frame call on its own uses the Step value of
   its whole frame.
5. Steps per frame in Quick auto key: the last key is at `frame_start + ceil(steps x Frames per step /
   Steps per frame)`. Steps per frame 1 keys exactly as before. When the scene's range ended at the
   old last key, re-keying also makes it shorter, so a shorter movie is not padded with frames that
   repeat the last step. `movie.movie_length(scene)` returns the steps, frames and seconds for the
   panel.

Not changed, measured too small to matter: the crash journal record per frame (0.03 ms) and redraw
tagging (0.01 to 0.03 ms). The position stored for crash recovery is therefore still written on every
jump and is right when playback stops (tested). Left as it is: after the cache, the remaining
bookkeeping on a frame that moves is reading the global position from the cursors, linear in the
number of steps (about 2 ms at 5,000 steps).

Handler calls in a real Cycles render of 10 frames (64 x 36, 1 sample, CPU): 11 calls without motion
blur, 41 with it (20 at sub-frames). History jumps: 7 and 7 now, 7 and 17 before. Frames per step 2
means every second frame repeats a step; those frames now cost the add-on 0.12 to 0.15 ms.

### Other ways to cut render time

Measured on a 100k grid with 40 steps, Cycles on the CPU at 64 x 36, 10 frames, Movie Mode on.

| Setting | Time per frame | Of which Hindsight |
|---|---|---|
| Frames per step 1, 1 sample (baseline) | 42.7 ms | 1.7 ms |
| Movie Mode off, so the mesh never changes | 36.3 ms | 0 |
| Frames per step 2 | 44.9 ms | 1.3 ms |
| Frames per step 2 with Persistent Data | 24.5 ms | 1.0 ms |
| Frames per step 1 with Persistent Data | 39.5 ms | 1.5 ms |
| Motion blur on | 41.3 ms | 1.7 ms |
| 16 samples | 48.9 ms | 1.5 ms |
| 1 sample at 320 x 180 | 77 ms | 1.4 ms |
| 16 samples at 320 x 180 | 276 ms | 1.4 ms |

`frame_set` with the recorded object left in Sculpt Mode: 15.0 ms of Blender per frame at 100k
vertices, against 0.26 ms in Object Mode (Blender rebuilds its sculpt data after every mesh write).
At 500k in Sculpt Mode Blender took 56 to 67 ms per frame against 2 to 4 ms for Hindsight.

Hindsight's share of a frame is 4% at this tiny size and shrinks as the picture grows: the renderer's
time goes up with pixels and samples (3.6 times from 1 to 16 samples at 320 x 180), Hindsight's does
not. From most to least effect:

1. Fewer frames. Every rendered frame costs a full render; a frame that repeats a step is no cheaper
   (44.9 ms against 42.7 ms). Steps per frame 4 renders a quarter of the frames (5,001 to 1,251 at
   5,000 steps; `frame_set` alone went from 294 s to 21 s with the other changes).
2. Frames per step 1 with a lower frame rate instead of Frames per step 2 at 24 fps: the same
   length on screen with half the frames to render.
3. Samples and resolution: the renderer's own cost, which dominates at real sizes.
4. With Frames per step above 1, turn on Persistent Data (Render Properties, Performance): repeated
   frames then reuse the scene and cost about half (24.5 ms against 44.9 ms).
5. Leave Sculpt Mode before rendering: 15 ms per frame at 100k in `frame_set`, more on bigger meshes.
6. Motion blur: no measurable cost here (41.3 against 42.7 ms), and since sub-frames keep their
   frame's step it no longer blurs two steps together. It adds nothing to a timelapse either, so off
   is the safe choice.

Not measured here: EEVEE and Workbench (Blender aborts without a GPU: libEGL.so.1 is missing), GPU
rendering, and renders at full resolution, where the renderer's share is larger still.

## Rust engine

Measured 2026-11-18 with `bench/bench_native.py` (writes `bench/results_native.json`) on the same shared
Linux VM as above (Intel Xeon at 2.10 GHz, 4 logical CPUs, load average 2.3 to 3.5 during the runs),
core only (no Blender), Python 3.11.18 with numpy 1.26.4 (the bpy 5.0 venv) and Python 3.13.18 with
numpy 2.5.5 (the bpy 5.3 venv), zstandard, and the `hindsight_native` 0.1.0 Linux wheel (PyO3 0.22.6,
abi3, cargo 1.97, release build with LTO). The stroke is a brush disc over 2% of the vertices of a
square grid mesh (1,996 of 99,856 and 20,009 of 1,000,000 vertices changed; one step is 6,286 and
43,346 bytes packed with zstd). Python path = the same process with `native.lib` set to None. Times
are medians of 30 runs (10 at 1M; 6 and 4 for the jump), in ms, Python / native.

| Operation | 3.11, 100k | 3.11, 1M | 3.13, 100k | 3.13, 1M |
|---|---|---|---|---|
| Diff (position channel) | 0.379 / 0.086 | 4.2 / 1.1 | 0.39 / 0.083 | 5.04 / 1.2 |
| Apply one step | 0.029 / 0.01 | 0.259 / 0.088 | 0.025 / 0.009 | 0.468 / 0.172 |
| Pack one record | 0.145 / 0.047 | 1.46 / 0.498 | 0.066 / 0.044 | 1.06 / 0.945 |
| Unpack one record | 0.047 / 0.016 | 0.35 / 0.135 | 0.03 / 0.014 | 0.412 / 0.241 |
| Pack one step (with zstd) | 0.175 / 0.077 | 1.86 / 0.868 | 0.095 / 0.067 | 1.61 / 1.44 |
| Unpack one step (with zstd) | 0.076 / 0.04 | 0.51 / 0.231 | 0.052 / 0.034 | 0.57 / 0.368 |
| Jump over 300 packed steps | 40.5 / 18.9 | 277 / 152 | 49.8 / 27.9 | 275 / 179 |

Peak temporary memory of the operation (tracemalloc, which also sees numpy's buffers and the native
code's output objects; Python 3.13, MB, Python / native; 3.11 is the same within 0.05 MB):

| Operation | 100k | 1M |
|---|---|---|
| Diff (position channel) | 1.70 / 0.03 | 17.00 / 0.32 |
| Apply one step | 0.04 / 0.00 | 0.40 / 0.00 |
| Pack one record | 0.33 / 0.06 | 3.32 / 0.56 |
| Unpack one record | 0.06 / 0.03 | 0.56 / 0.32 |
| Pack one step (with zstd) | 0.33 / 0.06 | 3.32 / 0.61 |
| Unpack one step (with zstd) | 0.09 / 0.06 | 0.84 / 0.60 |
| Jump over 300 packed steps | 0.12 / 0.10 | 1.17 / 0.93 |

The process peak (VmHWM, reset through /proc/self/clear_refs before each run) did not rise over the
RSS before any of these operations on either path (0.00 MB in every row): the transients are served
from memory the allocator already holds. The native code's own scratch memory is a 1024-element
buffer on the stack and the list of changed indices; nothing scales with the mesh.

Run-to-run noise on this VM is about 20 to 40% (two runs of the same build on 3.13 gave 17 and 28 ms
for the 100k jump), so read the ratios, not the last digit: diff is 4 times faster and its
transient goes from 1.3 times the position array (17 MB at 1M) to the changed elements only;
applying a step 2.5 to 3 times; packing a record 1.1 to 3 times and unpacking it 1.7 to 3 times;
a whole step, where zstd and the step header stay in Python, 1.1 to 2.3 times; a 300-step jump 1.5
to 2.4 times.

What is in Rust (`native/src/lib.rs`, loaded by `hindsight/core/native.py`): `diff` (changed
elements and their wrapping differences), `apply` (in place, ADD both ways and XOR), and the
transforms of a format 3 record in `pack_delta` and `unpack_at` (index coding: gaps and one run when
writing, gaps, runs and bitmap when reading; per-component prediction orders 0 to 3 with the same
size estimate and tie rule; zigzag; byte planes). Header parsing, error types, compression (zstd,
lz4, zlib), format 1 and 2 records and the step body stay in Python. `tests/test_core_native.py`
checks native against Python bit for bit (about 40,000 records, 6,000 damaged records, 1,600 damaged
steps, a 1M-vertex channel); 21 mutants of the Rust code were all caught by it.

Stage 2 (the step body encode and decode in `step.py`) was not done. A profile of the 100k jump
with the native engine (300 steps, 10 jumps) puts 20% of the time in `apply`, 17% in `unpack`, 15%
in zstd and the rest in Python around them (step header parsing, `unpack_at`'s header, the
`Step` objects); moving the body loop to Rust would remove part of that last share for one record per
stroke step, while every exception path of the step format (JSON extras, snapshots, data records)
would need its own bit-exact twin. Not clearly worth it on these numbers.

## Redraw cost (2026-11-18)

What Hindsight's own Python costs on every viewport and sidebar redraw, which a weak computer pays
during every stroke. There is no GPU here: gpu, gpu_extras and blf were replaced by recorders, so
these are the Python side only, and nothing was looked at on a screen. The new tests compare every
recorded gpu and blf call (each vertex, colour, text and position) with a verbatim copy of the old
drawing code, over 5 layouts and 46 states.

Method: a fake 3D View (1574x954 region, toolbar, sidebar), real recorded steps, the old build
(66fcdf8) and the new one in alternating runs, 5 rounds each, minimum of 7 batches per run, on
Blender 5.3. Microseconds per call, minimum over the rounds.

| scene | viewport redraw | panel redraw | hover |
|---|---|---|---|
| 10 steps, 1 object, per object | 50.9 to 36.5 | 55.5 to 51.6 | 6.7 to 4.3 |
| 10,000 steps, 1 object, per object | 76.8 to 37.7 | 62.6 to 54.0 | 7.0 to 4.3 |
| 10,000 steps, 1 object, global | 85.3 to 66.9 | 118.0 to 81.9 | 6.4 to 4.4 |
| 10,000 steps, 50 objects, global | 565 to 606 (noise) | 569 to 626 (noise) | 6.8 to 4.6 |
| 50 objects, per object, Collection Filtering | 139.5 to 56.6 | 162.5 to 105.2 | 6.8 to 4.4 |
| 50 objects, global, Collection Filtering | 3,943 to 1,247 | 4,240 to 1,312 | 6.8 to 4.4 |

- The slider's batches are kept by part (track, dot, fill, handle, tick). A stroke at the end of the
  history builds none (11 before); a drag move builds 5 (11 before). The share of a drag move that is
  the interface went from 217 to 95 microseconds at 5,000 steps.
- The viewport warning overlay ran every check on every redraw and kept only the errors. It runs
  the three error checks now.
- Collection Filtering asked Blender for every collection an object is in, which scans every
  collection in the file, for every object, three times per redraw in global mode.
- Not changed by this: global history with 50 objects and no filter. Nearly all of that redraw is
  `runtime.scrub_info`, which the runtime work below takes on.
