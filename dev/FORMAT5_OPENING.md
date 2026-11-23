# Format 5: what opening costs at 100,000 steps on a 1M-vertex mesh

Design for #46, part of #7. It works out the cost of opening a .blend whose history is laid out as
`dev/FORMAT5_LAYOUT.md` (#41) says, with the encodings of `dev/FORMAT5_ENCODINGS.md` (#42) and the checks of
`dev/FORMAT5_DAMAGE.md` (#43), and compares it with today's embedded history. Every number was measured ("How it was
measured" at the end says how) or is marked as an estimate with the arithmetic behind it.

## In short

A history of 100,000 steps on a 1M-vertex mesh is far too big to open on any PC at today's step size, and still too big
at the size target in `DECISIONS.md`. The size of the steps decides it, not the layout:

| Steps of | History (100,000 steps) | Blender's reading | Hindsight's work, format 5 | Peak memory, format 5 | Hindsight's work, today | Peak memory, today |
|---|---|---|---|---|---|---|
| today's size, 82,011 bytes | 7.6 GB | 31 to 36 s | 5.2 to 6.4 s | 7.9 GB | 10.2 to 11.2 s | 23 GB |
| 2 bytes per vertex moved, 40,000 bytes | 3.7 GB | 15 to 18 s | 3.5 to 4.3 s | 4.0 GB | 6.1 to 6.9 s | 11.4 GB |
| about a kilobyte | 95 MB | 0.6 to 0.8 s | 1.9 to 2.3 s | 0.34 GB | 2.4 to 3.0 s | 0.55 GB |

All of this table is estimates: the measured costs below, scaled to these sizes (arithmetic). The steps are strokes that
each move 20,000 vertices (2% of the mesh). Peak memory counts the whole process above an empty scene. In format 5,
Blender's reading accounts for the mesh and the history held once (3.8 GB at the target size), and Hindsight's work
for about 0.2 GB more: its step list and its work on the mesh. Today Hindsight's work adds two more copies of the
history.

- **Format 5 already cuts Hindsight's part.** Today's opening holds the history three times at its peak and twice
  afterwards, because Blender keeps its copy. Format 5 holds it once, plus one step part (4 MB).
- **It can't go below the history held once.** Blender reads the whole .blend into memory before any add-on code runs,
  so at open the memory is at least the history's size, and the time is mostly Blender's own reading: 4.0 to 4.7 s
  per GB here.
- **So the steps must get smaller.** Opening in about 6 to 7.5 s with about 1.2 GB needs steps of about 10 KB on average
  (estimate, below), which is about 0.5 bytes per vertex moved for these strokes.

## How big the history gets

Measured: strokes of the bench's shape that move 20,000 vertices of a 1M-vertex mesh are 85,848 bytes on average when
recorded and 82,011 bytes once the packer has packed them, 4.1 bytes per vertex moved. `DECISIONS.md` gives "about
4.5 today on synthetic strokes", which matches.

So 100,000 such packed steps are 8.2 billion bytes, 7.6 GB. At the target of about 2 bytes per vertex moved they are
3.7 GB, and at "about a kilobyte" per step, the owner's other words in `DECISIONS.md`, 95 MB. Strokes that move fewer
vertices scale it down in proportion (all arithmetic).

## What opening costs, measured

Each case is a fresh process, twice, with ranges over the runs. Times are in seconds, memory in MB above an empty
scene. "Format 5" is a prototype of #41's opening, not the build.

| | bpy 4.2.23 | bpy 5.3.0 |
|---|---|---|
| **Blender** reads the 1M-vertex mesh, no history | 0.23 s, holds 66 to 67 MB | 0.32 s, holds 64 MB |
| **Blender**, 256 MB more in 64 step parts | +0.78 to 0.83 s | +1.00 to 1.05 s |
| **Blender**, 512 MB more in 128 step parts | +1.99 to 2.00 s | +2.10 to 2.33 s |
| **Blender**, 512 MB more as one property (today) | +1.42 to 1.44 s | +1.90 to 1.97 s |
| **Blender**, 100,000 steps more in 1,000 step parts (41 MB) | +0.44 to 0.55 s | +0.15 to 0.18 s |
| **Hindsight, format 5**, per 256 MB / 512 MB of step parts | 0.11 to 0.13 s / 0.25 to 0.27 s | 0.12 to 0.13 s / 0.25 s |
| **Hindsight, format 5**, per 100,000 steps (step tables and metas) | 0.49 to 0.50 s, holds 83 to 84 MB | 0.41 to 0.45 s, holds 78 MB |
| **Hindsight, today**, per 256 MB / 512 MB | 0.26 to 0.28 s / 0.52 to 0.54 s | 0.27 to 0.28 s / 0.53 to 0.55 s |
| **Hindsight, today**, per 100,000 steps | 1.07 to 1.14 s, holds 108 to 109 MB | 0.82 to 0.90 s, holds 93 to 95 MB |
| **Hindsight**, work that doesn't depend on the history (today's load with a one-step history on the mesh) | 1.45 to 1.48 s, holds 69 to 70 MB, peak 103 to 104 MB | 1.67 to 1.74 s, holds 69 MB, peak 103 MB |

Memory, in every case:

- **Blender's reading** holds the history once, and its peak was at most 3 MB above what it then holds. Reading needs no
  second copy, on 4.2 or on 5.3 (whose files are compressed).
- **Format 5**: Hindsight's peak was 4.0 MB above the opened file (one step part), and afterwards the process held
  what it held when the file had opened: the history moved from Blender's copy to Hindsight's.
- **Today**: the peak was twice the history above the opened file (512 to 516 MB for 256 MB, 1,029 to 1,034 MB for
  512 MB), and afterwards it held the history once more, because Blender's copy stays.

At 100,000 small steps the whole of today's opening, Blender and Hindsight, took 2.6 to 2.9 s, and most of that was
Hindsight's work: the work on the mesh (1.5 to 1.7 s) and the steps (0.8 to 1.1 s).

## What is read at open and what later

- **At open, everything.** Blender reads the whole .blend, every step part included, before Hindsight's `load_post`
  runs; an add-on can't leave part of a .blend unread. Then Hindsight reads the index part, and for each step part
  copies it, checks its length and CRC-32 against the index part, deletes Blender's copy, and reads its step table
  and metas (#43). That gives the list of steps, each found by its offset in its step part (#41), and each step's
  encoding from its first bytes (#42). The step table's and metas' own CRC-32s are checked only in a part whose
  CRC-32 failed (#43). The prototype below checked them in every part, and did not read each step's encoding. Then comes the work that doesn't depend on the history, as today: reading the meshes and comparing the file
  with its history (D46).
- **Later, only when needed**: a step is decoded, and its own prefix CRC checked (#42), only when a jump reaches it.
  Nothing else is read later, and nothing more is read from disk.
- **In the background**: the packer packs loaded steps again smaller while the user is idle, as today.
- **While it is read, the user can do nothing.** Blender's reading and `load_post` both run on the main thread. Once
  `load_post` returns, the user can do everything: sculpt, jump to any step, save.

Only the CRC-32 checks could move to a background thread: copying a part out of Blender needs the main thread. They
cost 0.11 to 0.14 s per GB (#43's measurement), so at the target size about 0.5 s of the 19 to 22 s would move
(estimate), and for that time a damaged step part would not be known. #43 chose checking at open, and this doesn't
change that.

## Is it too much for "it must run well on any PC"?

Yes, at 100,000 steps of today's size or of the 2-bytes-per-vertex target. The history alone needs 3.7 to 7.6 GB from
the moment the file opens until it is closed, and opening takes 19 to 42 s on this PC (estimates, from the table at
the top). On a PC with 8 GB that leaves little or nothing for Blender, the mesh and the system. A slower disk adds
time: a hard disk reading 100 to 200 MB/s needs 19 to 37 s just to read 3.7 GB (estimate, arithmetic).

What in #41 to #43 should change, by what this measured:

- **#41, the layout: nothing.** It already holds the history once plus one step part, which is as low as a history
  inside the .blend can go. 1,000 step parts cost Blender under 0.6 s to read, so the 4 MiB limit stays.
- **#42, the encodings: nothing in the design; what it allows is the way out.** A later encoding (5 to 254) makes
  steps smaller without a new format, and the step size sets both the memory and most of the time. At about 10 KB a
  step, 100,000 steps are 0.93 GB, and opening takes about 4.0 to 4.7 s of Blender's reading and 2.3 to 2.8 s of
  Hindsight's work, with about 1.2 GB of memory (estimate, from the measured costs above).
- **#43, the damage checks: nothing.** One CRC-32 pass at open costs 0.11 to 0.14 s per GB, against Blender's 4.0 to
  4.7 s per GB of reading.

**For the owner**: every step is kept (`DECISIONS.md`), so only smaller steps bring this down. The size target of
about 2 bytes per vertex moved still leaves 3.7 GB at 100,000 strokes of 20,000 vertices. I recommend treating it as
a first stop and continuing towards "about a kilobyte" per step in the optimisation tickets, as #42 allows. Also, the
work that doesn't depend on the history (1.5 to 1.7 s on a 1M-vertex mesh) is the biggest part of Hindsight's share
whenever the history is small, so it is worth a round there too.

## How it was measured

Throwaway scripts, not merged, run on 23 November 2026 on the owner's Windows 10 PC (Intel Core i5-10600KF, 16 GB, about
4 GB of it free during the runs, other test runs going at the same time, so times are rough; memory figures are not
affected), in the bpy modules 4.2.23 and 5.3.0. These are Blender built as a Python module, not the Blender app: no
window is drawn.

- **The files**, each written by the same bpy version that opened it: a 1M-vertex mesh (999,698 vertices,
  `bench/bench_bl.py`'s sphere) and then
  - no history;
  - 256 or 512 MB of random bytes in 4 MiB step parts, in #41's layout (index part, `index_copy`, one history group);
  - the same bytes as one property: a real registry blob of today's format with 128 KB steps, in
    `hindsight_data_bin`;
  - 100,000 real packed steps of a 1,000-vertex mesh, 100 to a step part (as 40 KB steps fill 4 MiB parts), each part
    with #43's step table and metas and their CRC-32s;
  - the same 100,000 steps saved embedded by today's add-on, and a one-step history saved the same way.

  bpy 5.3.0 writes compressed .blend files (they start with zstd's magic) and bpy 4.2.23 doesn't, so the files differ in
  size (28 MB and 65 MB for the mesh alone) but not in memory.
- **Time and memory**: the time of `bpy.ops.wm.open_mainfile`, and the process's private bytes and their peak (Windows
  `GetProcessMemoryInfo`, `PagefileUsage` and `PeakPagefileUsage`), above the private bytes just before opening. The
  peak before opening was within 8 MB of that, so no earlier peak hides in it. Two runs per case, the cases taking
  turns. The files were not always in Windows' file cache: a plain read of one 577 MB file took 1.5 s, and 0.4 s right
  after. So Blender's reading here includes reading from the SSD.
- **Format 5 opening**: a prototype in #41's order. It reads the index part and checks its CRC-32. For each step part
  it copies the part, checks its length and CRC-32 (the engine's `crc32`), deletes Blender's copy, and reads the step
  table and metas with their CRC-32s. It keeps one tuple per step (step part, offset, length, CRC-32, meta), and
  samples the private bytes after each copy. The build's own step list will differ, so its memory per step is a guide.
- **Today**: what `from_text` does (read `hindsight_data_bin`, then `persist.registry_from_bytes`) in plain bpy for the
  bytes, and the real add-on's `load_post`, timed by wrapping it, for the steps and the work on the mesh.
- **Step size**: 60 strokes of the bench's shape (`bench_bl.Stroker`: a run of 20,000 vertices pushed out with a smooth
  falloff) on the same sphere, through `step_to_bytes` as recorded and as the packer packs them (`strong=True`).
- **The table at the top**: the mesh's reading, plus the history's size times the cost per GB of the 512 MB runs (256 MB
  for Hindsight's work too), plus the cost per 100,000 steps, plus the work that doesn't depend on the history. Peak
  memory is the mesh, the history once (three times today), the step list and the work on the mesh.
