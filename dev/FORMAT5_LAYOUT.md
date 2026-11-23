# Format 5: how the history is split inside the .blend

Design for #41, part of #7 ("Design the history's new layout inside the .blend"). It keeps to the owner's givens in
`DECISIONS.md` ("History inside the .blend", 2026-11-22): the history always lives inside the .blend, every step is kept
exactly, Blender's in-memory copy is dropped after opening and put back while saving (#9), and V1 keeps a format 5
history untouched and opens the .blend without it. Other parts of #7 build on this one: how a step's encoding carries a
version (#42), how a damaged part is found and what survives it (#43), the crash journal (#44), moving V1's formats 1
to 4 in (#45), and what opening costs at 100,000 steps (#46).

Every number below was measured, and "How it was measured" at the end says how, or it is marked as an estimate or a
choice.

## New words

- **Part**: one bytes custom property that holds a piece of the embedded history.
- **Index part**: the part that lists the histories, their cursors and their step parts.
- **Step part**: a part that holds a run of one history's packed steps, in order. A **full** step part is never
  changed again unless its steps change; each history has one **open** step part at its end, which grows until it is
  full.
- **The main-branch build**: the add-on on `claude/lucid-brahmagupta-9gsdsn` before the V2 work (commit 6168382), which
  `DECISIONS.md` calls beta 3. It reads histories the way V1 does, plus its own bytes property.

## The layout

```
Text ".hindsight_history"   fake user; V1's container, kept as it is
├─ hindsight_data           string, 80 characters, never changes          always in the file and in memory
│                           (a V1 history header saying "format 5", no steps)
├─ sidecar                  False                                         always in the file and in memory
└─ hindsight_parts          group                                         in the saved file, and in memory only
   ├─ index                 bytes: the index part                         while a save runs
   ├─ index_copy            bytes: the same index part again (#43)
   ├─ <history uid>         group, one per history (a uid is 16 hex characters)
   │  ├─ "0"                bytes: full step part (about 4 MiB)
   │  ├─ "1"                bytes: full step part
   │  └─ "2"                bytes: open step part
   └─ <history uid>         ...
```

### What each part holds

- **`hindsight_data`** is a V1 history header with no steps: `b"HSR1"`, its length, and the JSON
  `{"format":5,"version":5,"writer":"Hindsight 2.0.0"}`, as base64 text. It holds no history. It is there for older
  builds: V1 and the main-branch build read it, find format 5, say "This history was saved by a newer Hindsight
  (format 5)" and keep the Text as it is (see "Older builds"). V2 writes it once and never reads it.
- **`sidecar`** is always False in a file V2 saved, so older builds never read a history folder for it.
- **The index part** holds what V1 keeps at the top of its history file, without anything per step: the format number,
  the writer, the save id (which ties the crash journal to this save, #44), the registry's settings (next sequence
  number, global timeline, preserved modes, objects per history, histories turned off, Record everything objects, the
  saved prints of D46), and for each history its uid, members, cursor, morph target, number of steps and its step
  parts in order, each with its number of steps, its length and a CRC-32 of its bytes. Its size grows with the
  number of histories and step parts, not with the number of steps.
- **A step part** holds a header and then its packed steps back to back. The header gives the number of the first step,
  and for each step its length, its CRC-32 and the step's meta (label, kind, sequence number and so on). In V1 the metas
  of every step sit in the one JSON header and are written again at every save; here each meta is written with its
  step and stays there. What a step's bytes look like, and how their encoding is versioned, is in
  `dev/FORMAT5_ENCODINGS.md` (#42).

How each part is checked when the file opens, what survives a damaged one, and what that adds to this layout (a
second copy of the index part, and a CRC-32 on each step part's step table and metas) is in `dev/FORMAT5_DAMAGE.md`
(#43).

### How a history is split into step parts

A history's steps fill its open step part until it reaches the size limit; then it becomes full and a new open step
part starts. The limit is a starting value of **4 MiB** (a choice, to be tuned by the build ticket's measurements). A
step bigger than the limit (a topology step on a 1M-vertex mesh, say) gets a step part of its own.

The limit keeps two things small at once:

- **How many properties there are.** Blender 4.2 gets slow when one group holds many properties: storing 256 MB as
  1,024 properties took 0.10 to 0.11 s, as 16,384 took 1.0 s, as 100,000 took 146 s, and reading them back at open 0.10 to 0.12 s,
  1.3 s and 163 s (5.3: 0.12 to 0.14 s to store 16,384 and 0.12 s to read them). One group per history keeps each group to that history's
  step parts: at 4 MiB, a 256 MB history is 64 step parts and a 4 GB one 1,024 (arithmetic).
- **How much a save rebuilds** beyond the new steps: at most one open step part per history that changed, so under
  4 MiB each.

## What a save after N new steps does

| | Who | Work |
|---|---|---|
| Encode the N new steps | Hindsight | Raw steps get the quick encoding, as V1 does today; packed steps are taken as they are. No older step is encoded or checksummed again. |
| Build the index part | Hindsight | Rebuilt every save; its size grows with histories and step parts, not steps. |
| Build step parts | Hindsight | Only the open step part of each history that changed (its older steps are copied, not encoded), new full step parts it filled, and step parts whose steps changed since the last save: steps the packer packed smaller while the user was idle, and the step part where the cursor stood when the user went back and recorded a new step (the step parts after it go). A history that is gone loses its group. |
| Reuse every other step part | Hindsight | The same bytes Hindsight read at open or built at an earlier save, handed to Blender as they are. |
| Put the parts back | Hindsight, Blender | Blender copies each part into its group: 256 MB in 64 parts took 0.06 to 0.07 s. |
| Write the .blend | Blender | The whole file, history included, as on every save: 0.5 to 1.3 s for 256 MB of parts here. |
| Take the parts back | Hindsight, Blender | Each part is read back and Blender's copy is deleted straight away: 0.06 to 0.07 s for 256 MB. |

So Hindsight encodes and checks only the new steps; the rest of a save's cost is copying bytes it already has, and
Blender writing the file.

## How it fits #9

#9 drops Blender's copy of the history after opening and puts it back only while saving, so the history is held once.
In format 5:

- **Opening** (`load_post`): Hindsight reads the index part, then reads each step part and deletes Blender's copy of it
  straight away, then deletes the group. The stub and `sidecar` stay. Reading part by part, the memory above the
  opened file peaked at 4 MB (one part), against 257 MB when every part is read first and the group deleted after
  (256 MB in 64 parts, 4.2 and 5.3). What opening costs at 100,000 steps is #46.
- **Between saves**: Blender holds only the stub and `sidecar`. Blender's autosave therefore writes a file without
  the history parts (the accepted cost in `DECISIONS.md`); V2 knows such a file by the stub without a
  `hindsight_parts` group.
- **Saving** (`save_pre`): Hindsight builds the parts that changed, then puts back the whole `hindsight_parts` group.
  It **hands the full step parts over**: once Blender has copied a part, Hindsight drops its own reference, so during
  the save Blender's copy is the only one. The packer is drained before this, as it is today, so nothing reads a
  handed-over part during the save.
- **After saving** (`save_post`, and `save_post_fail` when the save failed; both handlers exist in bpy 4.2.23 and
  5.3.0): Hindsight takes each part back and deletes Blender's copy of it straight away, then deletes the group. On
  5.3, deleting the group after a save leaves `bpy.data.is_dirty` False; in the 4.2 bpy module `is_dirty` was already
  True straight after `save_as_mainfile`, so that check says nothing there and #9 checks it in a real 4.2 window.

For hand-over to work, Hindsight must hold each full step part as one bytes object and read its steps out of it by
offset (a copy of one step when a jump needs it), instead of one bytes object per step as today. Otherwise a step
part is held twice: once as the part and once as its steps.

### Memory at the peak of a save

MB above what the process held just before the save, with Hindsight holding 256 MB of step parts (64 parts):

| Layout | 4.2: put back | 4.2: save | 5.3: put back | 5.3: save |
|---|---|---|---|---|
| V1's way: join everything into one bytes object, then store it | 513 | 513 | 513 | 513 |
| Store part by part, keeping Hindsight's copy | 257 | 259 | 257 | 280 to 292 |
| Store part by part, handing the parts over (chosen) | 4 | 4 | 4 | 22 to 23 |

So the peak of a format 5 save is the history held once, plus one part, plus what Blender's writer needs (about
20 MB on 5.3 and nothing measurable on 4.2 at this size). V1's way peaks at the history plus two more copies of it.
Hand-over costs 0.02 s more to put back and 0.05 s more to take back per 256 MB.

## Older builds open it without the history and save it back untouched

Against V1's loading code (tag `v1.0.0-beta.2`, `hindsight/bl/persistence.py` and `hindsight/core/persist.py`):

1. On open, `on_load_post` (lines 351 to 355) looks for the Text `.hindsight_history` and passes it to
   `_load_registry` (278). The Text counts as embedded history only if it has `hindsight_data` (281); `hindsight_parts`
   is never looked at.
2. `from_sidecar` reads a history folder only when `sidecar` is True (302); V2 always stores False.
3. `from_text` (283) base64-decodes `hindsight_data` and calls `registry_from_bytes` (persist.py 143). That checks the
   magic, reads the JSON, and raises `NewerFormatError` for any format above 4 (162 to 163), before it looks for steps.
   `from_text` then sets `_PROTECT["embed"]` and notes "This history was saved by a newer Hindsight (format 5). Update
   Hindsight to see it. It is left untouched until then". No history is loaded.
4. On save, `on_save_pre` (209) writes `sidecar` (225), and with `_PROTECT["embed"]` set does nothing else to the Text
   (226 to 227). It only ever deletes `hindsight_data`, and only when the history is not protected (231 to 232). It
   never touches any other property, so `hindsight_parts` is saved as it was.
5. In the folder and both save modes, `on_save_post` writes a history folder with the steps V1 recorded itself, and
   `sidecar` becomes True. The format 5 parts are not touched.

The main-branch build does the same (commit 6168382): `from_text` reads `hindsight_data` before its own
`hindsight_data_bin` when both are there (persistence.py 477), `registry_from_bytes` raises `NewerFormatError` (persist.py
242) before it checks the trailer (244), and a protected save does not even write the save id (persistence.py 373 and
395). Recording everything, it reads every Text and every datablock's custom properties at each operation, but skips
`.hindsight_history` by name (adapters.py 3526 and 3546), so it never reads the parts.

Measured: a .blend with this layout (the stub, `sidecar` False, and a `hindsight_parts` group of four parts with zero
bytes in them) was opened by V1 in bpy 4.2.23 and by the main-branch build in bpy 4.2.23 and 5.3.0, in each save mode
(history folder, embedded, both): 9 cases. In all 9 the build showed the "newer Hindsight" note, loaded no steps,
recorded a sculpt stroke and a move of its own and saved; every part of the saved file had the same SHA-256 as
before. In the folder and both modes the build wrote its own history folder and set `sidecar` True.

What this leaves for #45: a file V2 saved, then saved again by an older build that recorded in it, has the format 5
parts beside the older build's own history folder (or, in the embedded mode, its new steps nowhere, as V1 does for any
newer history). Which one V2 opens is #45's to settle; the parts themselves are always intact. "Remove Hindsight data"
in an older build deletes the whole Text (V1 `cleanup.py` 128 to 130), as the user asked.

## Alternatives considered

- **V1's layout with a new format number** (one bytes property holding everything): every save writes every step's
  meta again and joins the whole history into one new bytes object before Blender copies it, so the peak of a save is
  the history plus two copies (513 MB above 256 MB of steps). Lost on both the work and the memory of a save.
- **One property per step**: storing 100,000 properties took 146 s and reading them back 163 s on 4.2, and each
  property adds about 180 bytes to the file (274.3 MB against 256.8 MB for the same bytes in 64 parts). Lost.
- **One step part per save** (each save adds a part holding just its new steps): the fewest bytes built per save, but
  the number of parts grows with every save, which 4.2 handles badly (above), and small parts would need merging
  later, which is the same rebuild moved elsewhere. Lost to size-limited step parts with one open part per history.
- **Parts in datablocks of their own** (a Text per history or per part): the main-branch build, recording everything,
  skips only `.hindsight_history`; it would read every part's bytes at each operation and write a uid onto the
  datablock, so the file would not come back untouched. They would also show up in Blender's file view. Lost.
- **A new datablock or key and no stub**: older builds would open the file without a word about its history. Lost to
  the stub, which makes them say that a newer Hindsight saved it and that it is left untouched.
- **Base64 text, as V1's `hindsight_data`**: a third bigger and slower to save and open (R061 in `OPTIMISATION_LOG.md`
  measured both). Lost.
- **Custom properties on a scene or on the objects**: a scene or object can be deleted, linked or appended into
  another file with its properties; the Text with a fake user is V1's container, outlives all of that, and older builds
  skip it by name. Lost.
- **Keeping Blender's copy and dropping Hindsight's**: against #9's given, and every read of a bytes property gives a
  new copy (checked on 4.2 and 5.3), so each jump to an older step would copy its part. Lost.
- **Keeping Hindsight's copy through the save** (store part by part without hand-over): the peak of a save is the
  history held twice (257 to 292 MB above 256 MB of steps) instead of once (4 to 23 MB above). Lost, for 0.07 s more
  per 256 MB with hand-over.
- **Steps in files on disk**: the owner ruled it out (`DECISIONS.md`, 2026-11-22).

## How it was measured

Throwaway prototypes, not merged, run in November 2026 on the owner's Windows 10 PC (16 GB, other test runs going at
the same time, so times are rough; memory figures are not affected), in the bpy modules 4.2.23 and 5.3.0:

- **Memory and time of a save and an open**: each case in a fresh Python process. The history was 256 MB of random
  bytes (zero bytes included, nothing to compress), held as Python bytes, split into 1, 64, 1,024, 16,384 or 100,000
  parts, stored on the Text `.hindsight_history` (fake user) one way or the other, then `bpy.ops.wm.save_as_mainfile`
  with Blender's default settings, then the parts deleted again. Memory is the process's private bytes and their peak
  (Windows `GetProcessMemoryInfo`, `PagefileUsage` and `PeakPagefileUsage`), given as MB above the private bytes
  just before the step; the peak before each step was within 0.1 MB of that, so no earlier peak hides in it. Opening
  read the saved file back the same way. Two runs per case, the layouts taking turns; ranges are over the runs. The
  100,000-part case ran once.
- **Older builds**: V1 from the tag `v1.0.0-beta.2` and the main-branch build from commit 6168382, each with its own
  `tests/harness.py` (the main-branch build with its own engine wheel), registered in a fresh process, set to a save
  mode, opening the .blend described above, recording, and saving to a new file; plain bpy 5.3.0 then opened that file
  and compared the parts by SHA-256.
- **Small facts**: reading a bytes property twice gives two objects; replacing an existing bytes property cuts the
  new value at its first zero byte (`b"xy\x00zw"` became `b"xy"`) while a new key keeps it whole (so parts always go
  into a fresh group); `save_post_fail` and `load_post_fail` exist (all on 4.2.23 and 5.3.0).
