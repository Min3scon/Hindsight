> 2026-11-18. An agent's report as delivered, unchanged below this line. Its work is merged into the default branch.

# SAVELOAD report

Branch `worktree-agent-a33d7bef5d9a99ac0`, commits on top of 66fcdf8:
637ec44 (Embed save), 79919d6 (packer, journal), 1c5f221 (packer bench). Commits carry the
attribution lines of this session's system reminder (Claude Opus 5.5), which override the
"Claude Fable 5.2" lines in COMMON.md.

Limits: no Blender binary and no GPU here. Everything in Blender was measured with Blender 5.3.0 as a
Python module (Python 3.13), headless. The machine was shared and loaded the whole time (load average
5 to 14 on 4 cores), so whole-file save and open times swing by 20 to 40% from run to run.

## Profile: where save and load time and memory go (before any change)

Method: `bench/saveload_bench.py` (new). It builds a history with the core and caches the steps, so
both builds are measured on identical bytes. Strokes touch 2% of the vertices, the newest 64 steps are
raw. cProfile of one save and one open (`S/agents/SAVELOAD/prof.py`):

| 100k vertices, 1,000 steps | Hindsight's Python | of which |
|---|---|---|
| save, history folder | 66 ms (whole save 300 to 440 ms) | fingerprint of the mesh 23 ms (runtime.saved_prints), fsyncs 18 ms, writing 4 ms, CRC-32 of the steps 5 ms |
| open, history folder | 76 ms (whole open 750 to 880 ms) | fingerprint 23 ms, baseline read 20 ms, mode-entry topology check 10 ms, `registry_from_bytes` 18 ms |
| save, Embed | 73 ms | base64 22 ms, Blender copying the string 14 ms, fingerprint 20 ms, `registry_parts` 10 ms |
| open, Embed | 74 ms | a2b_base64 14 ms, `registry_from_bytes` 8 ms, the rest as above |

| 1M vertices, 300 steps | Hindsight's Python | of which |
|---|---|---|
| save, history folder | 452 ms (whole save about 4 s) | fingerprint 262 ms, writing the folder 180 ms (fsync 65 ms) |
| open, history folder | 670 ms (whole open about 9 s) | fingerprint 240 ms, baseline read 193 ms, mode-entry topology check 196 ms, history file 25 ms |
| save, Embed | 656 ms | fingerprint 357 ms, base64 154 ms, `registry_to_bytes` 53 ms |

So at 1M, the storage code proper (persist, journal, codec) is under 10% of Hindsight's share, and
Hindsight's share is under 10% of what Blender takes to open the file. The biggest Hindsight costs in
save and open at 1M are mesh reads in runtime.py and meshio.py (not mine: patch proposed below).

Memory, before (tracemalloc peak during the operation, includes what is kept; VmHWM above the RSS
before it):

| | history | save folder | save Embed | open folder | open Embed |
|---|---|---|---|---|---|
| 100k, 1,000 steps | 10.3 MB | 3.0 / 6.9 MB | 26.9 / 11.8 MB | 20.2 / 2.3 MB | 20.9 / 10.9 MB |
| 100k, 6,000 steps | 54.4 MB | 4.2 / 8.2 MB | 159.7 / 143.2 MB | 67.6 / 2.4 MB | 124.2 / 125.5 MB |
| 1M, 300 steps | 39.1 MB | 30.5 / 8.8 MB | 75.4 / 73.7 MB | 132.0 / 27.8 MB | 132.0 / 52.7 MB |

A note on reading the PERF_LOG numbers: tracemalloc counts what is allocated after it starts, and
frees of memory allocated before do not count against it. So the "load 83 MB" in PERF_LOG is mostly
the new history and baseline themselves (at 1,000 steps: 9.05 MB step blobs + 6.1 MB baseline + 0.6 MB
metadata of 20.2 MB). The old registry is let go before the new one is read (checked with a weak
reference: `S/agents/SAVELOAD/who.py`), so load does not hold two histories.

Costs by size, the ones in my files:
1. Embed save: the joined history, its base64 bytes and the str, plus Blender's old copy of the
   stored string (1.33 times the history) while the new one is built. 2.9 times the history in Python,
   plus the old stored copy.
2. Idle re-packer: about 3 ms of CPU per 9 KB step at 100k and 25 ms per 86 KB step at 1M
   (zstd level 19 is 82% of it; `S/agents/SAVELOAD/rcp.py`). It does not wait while the user works
   when strokes come more than 1 s apart, or when the raw tier is not full yet (nothing is submitted
   for packing then), so on the first 64 strokes after opening a file it recompressed one step per
   second (1M: 15 steps, 0.58 s CPU in 20 s of strokes every 2 s, bursts of about 38 ms of one core).
3. After loading a file saved after recompression, every step was queued again; the worker read and
   checked one per idle second for nothing (6,000 steps: 100 minutes of wake-ups).
4. Journal per stroke: 0.15 ms (100k) and 0.56 ms (1M) to pack the step in the quick form, 0.04 to
   0.06 ms to append, 0.002 ms to flush (no fsync per stroke; fsync only at a save point). Small
   against a commit (1 ms at 100k, 12 to 23 ms at 1M); left as it is.
5. Load of the history file: 18 ms per 1,000 steps (half reading, CRC-32 of each step, JSON). The
   checks are what lets damaged steps be found, so they stay.

## What I changed

1. `hindsight/core/persist.py`: `parts_to_b64(parts)`, the base64 text of the history built piece by
   piece (65 KB per call) into one buffer of its exact final size, then decoded once. It is exactly
   `base64.b64encode(b"".join(parts)).decode("ascii")` (tested on 200+ random part layouts, on chunk
   boundaries and on a real registry), so the .blend gets the same string as before.
2. `hindsight/bl/persistence.py`, `on_save_pre` in Embed and Both: the registry parts are made first
   (anything that can fail does so before the stored copy is touched), then Blender's stored copy of
   the last history is deleted, then the new text is built and stored. Before, Blender's old copy, the
   new text and Blender's new copy were all alive at once.
3. `hindsight/core/store.py` (the Packer lives here, not in runtime.py, so I took it as the
   re-packer's own file; `enforce_tiers`, `enforce_caps` untouched):
   - `note_activity()`: idle recompression waits until `USER_IDLE_S` (5 s) has passed since the
     last recorded step, jump or other history change. Packing new steps is not held back.
   - `_can_gain(blob)`: steps `recompress()` would leave as they are without trying (format 3, strong
     or uncompressed, not written quickly) are told from their first 13 bytes and are not queued or
     looked at. Tested to agree with `recompress` on normal, quick, strong, uncompressed, zlib and
     format 1 blobs.
   - Bug fixed: `queue_recompress` after loading did nothing until the first step was submitted for
     packing (the worker waited for new work only), so a loaded file's steps were not recompressed
     until the 65th stroke. It now wakes the worker. Regression test:
     `test_packer_without_activity_recompresses_as_before` (fails on 66fcdf8).
   Recompressed bytes are the same as before (the same `step.recompress` call; tested).
4. `hindsight/bl/persistence.py`, `_on_journal`: tells the packer about every journal record (every
   stroke, jump, truncation, drop, reset, meta change goes through it).
5. `hindsight/core/journal.py` (the coordinator's finding): `flush(fsync=True)` now raises when fsync
   fails, so a save point is only reported as written when the disk confirmed it. Decision: fsync
   errors EINVAL, ENOTSUP and EOPNOTSUPP (file systems that have nothing to sync, some network and FUSE
   ones) are not failures and are still ignored; anything else (EIO, ENOSPC) raises. The callers:
   `_mark_saved` runs inside the save and load handlers' try blocks, so a failure becomes
   "journal error on save" through guard.fail (logged and reported), never a crash. The per-stroke
   path calls `flush(fsync=False)`, which never calls fsync, and runtime._journal already catches any
   exception from the hook, so a stroke is never broken by the journal.
   While there: a write or flush that fails part way (disk full) left part of a record in the file,
   and the reader stops at the first broken record, so every record written after it (once space was
   free again) was invisible to recovery. Now the file is cut back to where the failed record started
   (or to the last good flush if the buffered records can't be written either) and the error is
   raised. Recovery keeps everything it kept before, plus the records after a failed one.
   Tests: `test_journal_fsync_failure_is_reported`, `test_journal_full_disk_keeps_records_before_and_after`,
   `test_journal_failed_flush_is_reported_and_cut_back` (all fail on 66fcdf8).

No file format, step bytes, history bytes or defaults changed. File format version is still 4.

## Before and after

Save and open, `S/agents/SAVELOAD/ab.sh N STEPS 5` (runs `bench/saveload_bench.py` alternately on
S/baseline and this worktree, 5 rounds, 3 saves and 3 opens per run and mode; summary by
`S/agents/SAVELOAD/summ.py`). Memory: tracemalloc peak (median of runs) / VmHWM above RSS (min, median):

| Embed save | before | after |
|---|---|---|
| 100k, 1,000 steps | 26.9 MB / 11.7, 11.8 MB | 24.1 MB / -0.2, 5.2 MB |
| 100k, 6,000 steps | 159.7 MB / 142.3, 143.2 MB | 143.1 MB / 73.8, 85.7 MB |
| 1M, 300 steps | 75.4 MB / 66.9, 73.7 MB | 67.1 MB / 33.0, 33.2 MB |

Inside the handler alone (`S/agents/SAVELOAD/stages.py`, 100k, 6,000 steps): the peak above the RSS
before the save went from about 155 MB to 71 MB. Blender's own writing of the file then peaks 60 MB
above that RSS (the string written out); that part is Blender's.

Embed save time: the A/B whole-save medians were 878 / 1,134 ms (1,000 steps) and 2,087 / 2,619 ms
(6,000 steps), mins 635 / 932 and 1,816 / 2,284, which looked like a slowdown, so I measured both ways
in one process, alternated 6 times (`S/agents/SAVELOAD/embed_inproc.py`, 100k, 6,000 steps): storing
the text old 446 min / 628 med ms, new 434 / 534 ms; storing plus Blender's save old 1,026 / 1,221 ms,
new 1,001 / 1,168 ms. Building the text alone, 6,000 steps (`b64speed.py`, 7 alternations): old 376 /
401 ms, new 237 / 377 ms. No slowdown; the A/B difference was load on the machine (load average 9
to 14 during those runs).

Everything else (history folder save and open, Embed open) was not changed and measured the same
within noise: 100k/6,000 folder save 1,218 / 1,150 ms median, open 2,286 / 2,373 ms; 1M/300 folder
save 4,228 / 4,361 ms, open 10,945 / 9,728 ms; memory identical.

Re-packer CPU (`bench/packer_bench.py ROOT STEPFILE 20`, 2 alternated rounds each, 20 s per phase, thread
CPU from /proc in 10 ms ticks):

| 1M, 300 steps (86 KB each) | before | after |
|---|---|---|
| strokes every 2 s, raw tier not full (first 64 strokes after opening) | 0.58 / 0.55 s CPU, 15 steps | 0.00 / 0.00 s, 0 steps |
| strokes every 2 s, each submitting a pack | 0.41 / 0.38 s, 10 steps | 0.01 / 0.00 s, 0 steps |
| idle | 0.57 / 0.51 s, 15 steps | 0.46 / 0.47 s, 14 / 13 steps (starts 5 s later) |
| steps already strong (a file saved after recompression), idle | 19 looked at | 1 looked at |

| 100k, 1,000 steps, first bench version (strokes every 0.8 s with a pack each) | before | after |
|---|---|---|
| sculpting | 0.02 s, 0 steps | 0.02 s, 0 steps |
| idle | 0.07 s, 19 steps | 0.06 s, 16 steps |
| already strong | 19 looked at | 1 looked at |

Totals: recompressing a 1,000-step 100k history costs about 2.9 s of CPU on this Xeon (3 ms a step,
`rcp.py`; 3.0 ms with the Python engine), at one step per idle second (the pacing is unchanged), so
under 0.5% of one core on average. At 1M, 25.6 ms a step. A 2-core laptop at half the speed: about
6 s per 1,000 steps at 100k. It no longer runs while strokes or jumps come less than 5 s apart.

Journal per stroke (`S/agents/SAVELOAD/jcost.py`, 200 strokes): 100k: quick pack 0.146 ms, append
0.042 ms, flush 0.002 ms; 1M: 0.562, 0.060, 0.002 ms. Opening a 4.6 MB journal 2.7 ms, reading it 3.4
ms. Unchanged by my work (the error handling adds a try block).

## Tests

- New: `tests/test_core_saveload_storage.py` (10 tests: base64 text equality and peak, journal fsync,
  full disk, failed flush, `_can_gain`, packer skip, packer waits while working, packer after load),
  `tests/test_bl_saveload_embed.py` (stored text equals b64encode of the registry over two saves, the
  history folder and the embedded copy hold the same bytes, reload in Embed; strokes tell the packer).
  All 10 core tests fail on 66fcdf8 (run against S/baseline: missing functions, a failed fsync
  reported as durable, the full disk hiding later records, the packer never starting after load).
- Core tests on bpy53 (native engine) and on bpy42 with HINDSIGHT_NATIVE=0: all pass.
- Existing: test_core_journal, test_core_store, test_core_codec, test_core_migration,
  test_core_records, test_bl_persistence: pass.
- Whole suite on bpy53: SUITE_RESULT
- My area on bpy42: BPY42_RESULT

## Tried, not kept, and not done

- Storing the embedded text as a bytes ID property (Blender keeps it as a byte string): the Python
  peak would drop to about 1.1 times the encoded size, but the .blend bytes change (string subtype),
  and the RSS high-water mark is the Python object plus Blender's copy either way. Not done.
- io.StringIO to build the text: same peak as before on 3.13 (getvalue copies); measured, dropped.
- Lower thread priority for the packer (per-thread nice on Linux): Python parts of recompression
  hold the GIL; a starved low-priority holder can stall the main thread. Not done; the activity
  wait covers the case.
- A faster `recompress` that keeps the header and recompresses the body directly (skips re-pack and
  same_step): saves 0.5 of 2.9 ms per step with the native engine. It lives in core/step.py (NATIVE's),
  and for steps written by older builds it can give different (equally valid) bytes than today. Not done.
- Lazy or zero-copy loading of the embedded history: the floor is the Python string Blender hands
  over plus the history kept after loading, which is what it is now (decoding step by step from the
  string would remove the decoded copy but not lower the peak). Not done.
- Skipping the journal scan when it is truncated right after opening (Last Saved recovery): a few ms
  per 5 MB of journal; left alone, also because the coordinator is changing the recovery block next to it.
- Removing the embedded string from the Text after loading or saving (it holds 1.33 times the
  history in RSS for the whole session in Embed mode: 69 MB at 6,000 steps): Blender's autosave
  writes without save handlers, and a session where Hindsight is disabled before saving would lose the
  history. Behaviour change, so it's a decision for the owner.

## Risks

- Embed save now deletes the stored text before building the new one. If building the text fails
  (only a MemoryError is possible there; everything that can raise otherwise runs first), the file is
  saved without the embedded history instead of with the previous one. guard.fail reports it either way.
- A journal save point on a failing disk is now reported (logged, sidebar through guard) instead of
  silently passing.
- Idle recompression starts 5 s after the last recorded step instead of 1 s after the last pack.
  Which steps are strong when a file is saved already depended on timing; it still does.

## Patches for files I don't own

1. runtime.py / meshio.py (RUNTIME, and whoever owns meshio): on load, `check_outside_changes` reads the
   whole mesh again for the fingerprint right after `prime_states` read it (240 ms of 670 ms at 1M).
   Hash the baseline just read instead. Needs a test that the two digests are equal for every object
   kind (Record everything with weights and shape keys included).

```diff
--- a/hindsight/bl/meshio.py
+++ b/hindsight/bl/meshio.py
@@ def fingerprint(me, ob=None) -> str:
     return h.hexdigest()
+
+
+def fingerprint_arrays(counts: dict, arrays: dict) -> str:
+    """fingerprint() of a mesh already read with read_snapshot (same digest, no second read)."""
+    h = hashlib.blake2b(digest_size=16)
+    h.update(repr(sorted(counts.items())).encode())
+    for key in sorted(arrays):
+        a = arrays[key]
+        if a is None:
+            continue
+        a = np.ascontiguousarray(a)
+        h.update(key.encode() + b"\0" + str(a.dtype).encode() + b"\0")
+        h.update(a.view(np.uint8))
+    return h.hexdigest()
--- a/hindsight/bl/runtime.py
+++ b/hindsight/bl/runtime.py
@@ def check_outside_changes(saved: dict) -> None:
         fp = rec.get("mesh")
         if fp and isinstance(ob, bpy.types.Object) and ob.type == "MESH" and h is not None and h.entries:
-            if mesh_fingerprint(ob) != fp:
+            st = _STATES.get(u) if ob.mode != "EDIT" else None
+            now = (meshio.fingerprint_arrays(st.counts, st.arrays) if st is not None and st.counts
+                   else mesh_fingerprint(ob))
+            if now != fp:
```
   (Only valid if MeshState keeps the snapshot's arrays as read; check before applying.)

2. runtime.py: nothing else needed; the packer is told about activity from persistence._on_journal.
   If RUNTIME prefers it explicit, `packer.note_activity()` at the end of `commit()` and `goto()` does
   the same.

3. core/step.py (NATIVE), optional: the faster `recompress` above, if bytes from older builds may differ.

## Proposed text

INTERFACES.md, section 2, class Packer, add:

    def note_activity(self) -> None   # the user recorded or jumped: idle recompression waits USER_IDLE_S (5 s)

and after `queue_recompress`: "(steps that can't get smaller are skipped; wakes the worker)".

DEFECTS.md:
- Idle recompression of a loaded file only started after the first step was packed (65 strokes into
  the session): `queue_recompress` did not wake the worker. Fixed in store.py; test
  test_packer_without_activity_recompresses_as_before.
- Journal: a failed fsync at a save point was reported as written; a record cut off by a full disk
  hid every later record from crash recovery. Fixed in journal.py; three tests in
  test_core_saveload_storage.py.

PERF_LOG.md, under "Memory, and the Rust question", a new paragraph:
"Embed save (2026-11-18, Blender 5.3 module, `bench/saveload_bench.py`, 5 alternated runs): the base64
text is built piece by piece into one buffer and the previously stored copy is let go first. Python
peak at 100k with 6,000 steps 159.7 to 143.1 MB, process peak above the RSS before the save 143 to
74-86 MB; at 1M with 300 steps 75.4 to 67.1 MB and 74 to 33 MB. The stored text is the same string.
The idle re-packer no longer runs while strokes or jumps come less than 5 s apart (at 1M: 0.58 s of CPU
in 20 s of strokes before, none after), skips steps already in the strong form, and starts after
loading a file instead of after the first packed step (`bench/packer_bench.py`)."

CHANGELOG.md:
- "Saving with the history inside the .blend needs about half the extra memory it did."
- "Background recompression waits while you sculpt or scrub."
- "Crash recovery: a full or failing disk no longer hides later steps from recovery, and a failed
  save point is reported."
