# Format 5: how V1's formats 1 to 4 are read and moved in

Design for #45, part of #7 ("Design the history's new layout inside the .blend"). It builds on the layout
(`dev/FORMAT5_LAYOUT.md`, #41), step encodings (`dev/FORMAT5_ENCODINGS.md`, #42), damage (`dev/FORMAT5_DAMAGE.md`,
#43) and the crash journal (`dev/FORMAT5_JOURNAL.md`, #44). It keeps to the owner's givens in `DECISIONS.md`
("History inside the .blend", 2026-11-22): every step is kept, exactly; the history folder goes as a save option, and
"histories already saved in a folder still open, and move into the .blend at the next save". #4 proved that V1
histories come through intact today, in every save mode and from V1's crash journal. This doc keeps that and adds
the move into format 5.

## New words

- **V1 history**: a history saved in formats 1 to 4, by V1 or by the builds before it.
- **Move-in**: the first format 5 save of a file whose history was a V1 history.

## What V1 left on disk

Four formats, all with the same container: `b"HSR1"`, a JSON header, then each step's bytes behind a u64 length
(`hindsight/core/persist.py`, top of file).

| Format | Written by | What it adds | Step encodings (#42) |
|---|---|---|---|
| 1 | V1's first public build (its files say 1.0.0-beta.1) | `version` only, no checksums | 1 |
| 2 | V1 work in progress | `format`, `writer`, a CRC-32 per step (`crcs`) | 1 |
| 3 | V1 work in progress (the compression build, fc2b6d3) | the trailer `HSRT` + CRC-32 of the JSON header | 3, and 1 not yet re-packed |
| 4 | V1 (1.0.0-beta.2); also the main-branch build | data records | 3, 4, and 1 not yet re-packed |

Each can sit in three places, set by V1's save mode (default: history folder, tag `v1.0.0-beta.2`, `prefs.py` 111
to 113). All of them use the Text `.hindsight_history`:

- **History folder**: `sidecar` True, and `<stem>_hindsight/history.hsr` beside the .blend.
- **Embedded history**: `sidecar` False, and `hindsight_data` holding base64 text. The main-branch build (never
  released; V2 is the next release) writes the same bytes without base64 as `hindsight_data_bin`, plus a `save_id`.
- **Both**: `sidecar` True, and the folder and the embedded copy, from the same save.

V1's crash journal (`HSJ1`) can sit beside any of them.

## How V2 tells what it opened

In this order, on the Text `.hindsight_history`:

1. No Text: no saved history.
2. A `hindsight_parts` group: format 5 (#41, #43). If `sidecar` is True as well, an older build saved the file after
   V2 (see "A format 5 file that V1 saved again").
3. `hindsight_data` equal to the stub and no `hindsight_parts`: a copy without its parts, such as Blender's autosave
   (#44). V2 knows the stub by its text, which never changes (#41); it does not decode it.
4. Anything else: a V1 history, read as below.

## Reading a V1 history

Exactly as today, by today's code, minus the save mode setting. The order is today's for the folder and both modes
(`hindsight/bl/persistence.py` `_load_registry`, lines 520 to 533), which is also V1's own (tag, `persistence.py` 318
to 325):

- `sidecar` True: the folder's `history.hsr` (`persist.load_sidecar`). If the folder is missing, the embedded copy if
  there is one, else no history and the missing-folder note. If `history.hsr` can't be read, it is set aside and
  the embedded copy is tried.
- Otherwise the embedded copy: `hindsight_data` (base64) if it is there, else `hindsight_data_bin`. An unreadable copy
  is kept as `hindsight_data_unreadable`, as today.
- A main-branch build file with a `save_id` keeps today's check: a folder from another save gives way to the embedded
  copy when that one names the .blend's save, else the stale-folder note. V1's files have no save id, and skip it.
  This is the only place the Text's `save_id` is read (#44 left it to this ticket).

`persist.registry_from_bytes` reads all four formats. It checks the header trailer (format 3 and 4) and each step's
CRC-32 (format 2 on). It computes a CRC-32 for every step, so format 1 steps have one from here on. A damaged step
cuts the history to the steps reachable from the saved cursor, with today's notes. The steps stay as the bytes they
were saved as, with their metas from the JSON header; nothing is decoded. Then:

- **The crash journal** is replayed as today when Crash Recovery is "Unsaved steps" (#44: V1's journals have no save
  id and match V1's files, as now).
- **D46** compares every recorded object with the history's saved prints, as today.
- **Blender's copy goes.** For an embedded or both file, V2 deletes V1's embedded copy from Blender's memory once it
  has read it, and puts the stub in `hindsight_data`. That way the history is held once (#9's given), and a copy
  Blender autosaves after that is a stub without parts, which #44 already handles: it opens without history and
  leaves the journal alone. The .blend on disk is unchanged until the user saves. A folder file has nothing to drop.
  Whether changing the Text marks the file as changed is checked by the build ticket, as #9 checks it for the parts.

## What the first format 5 save writes

The move-in is an ordinary format 5 save (#41) in which every step part is new.

| V1 file | Read from | In the Text after the move-in | Left as it was |
|---|---|---|---|
| History folder | `history.hsr` | the stub, `sidecar` False, `hindsight_parts` | the folder, byte for byte |
| Embedded history | `hindsight_data` or `hindsight_data_bin` | the stub, `sidecar` False, `hindsight_parts`; `hindsight_data_bin` and `save_id` deleted | `hindsight_data_unreadable`, if there |
| Both | the folder (the embedded copy if the folder is missing or unreadable) | as for embedded history | the folder, byte for byte |
| Any of these after a crash | as above, then the journal | as above; the journal starts again with the index part's save id (#44) | |

The same for every format:

- **The index part** (both copies, #43) takes the registry's settings from V1's JSON header (next sequence number,
  global timeline, preserved modes, objects per history, histories turned off, Record everything objects). It takes
  each history's uid, members, cursor, morph target and number of steps. It gets a new save id, and new saved prints
  (D46) made at this save.
- **The step parts** get every step's bytes, in order, cut into step parts of up to 4 MiB as #41 says. Each step keeps
  the CRC-32 computed when it was read and its meta from V1's header. The save copies each step's bytes once and
  decodes nothing. Each step's own bytes object is dropped once its step part is built, so the peak is the history
  plus one part, as in any format 5 save.
- From the second save on, only what changed is built (#41).
- **The history folder is never deleted.** It is the only copy if the user later goes back to an older .blend (a
  `.blend1`), and deleting someone's files is a one-way door. After the move-in nothing reads it any more, because
  `sidecar` is False. So V2 says once, after that save: "The history is now inside the .blend. The folder '{folder}'
  beside it is no longer used". The move-in deletes nothing outside the .blend. Blender's Save Versions keeps the
  V1 file as `.blend1`.
- **A folder file opened without its folder** saves with no history for those objects, and the folder is not read
  again even if it comes back. So V2's missing-folder note says: "History folder missing. Put '{folder}' next to the
  .blend and open it again to see this file's history. Once the file is saved, the folder is no longer read".

## V1 steps keep their own encoding

Every V1 step goes into its step part as the bytes it was saved as. Format 1 and 2 steps are encoding 1; format 3 and
4 steps are encodings 3 and 4, and 1 where V1's packer hadn't re-packed them yet. Why:

- **Exact by construction.** A byte copy can't change a step. Re-encoding would decode and encode every step at once,
  and each one is another chance to break exactness.
- **Fast.** At 100,000 steps, a byte copy beats decoding and encoding them all.
- **Nothing is lost by waiting.** The packer already re-packs encoding 1 steps and steps written quickly into
  encoding 3 or 4 while the user is idle. It keeps a new form only when `same_step` says it is the same step (#42).
  Each swap changes its step part at the next save, which #41 already counts. A beta 1 history therefore gets smaller
  over the idle time after the move-in, not during it.
- **Encodings 1, 3 and 4 already fit format 5.** The step's bytes say how to read them (#42), so a step part can mix
  them.

## A format 5 file that V1 saved again

This is what #41 left to this ticket. Suppose V1 opens a format 5 file, records in it and saves it. V1 (and the main-branch build)
leave the stub and the parts untouched (#41, measured in all 9 cases). What happens to what V1 recorded depends on its save mode:

- **History folder or both:** V1 writes its own history folder, holding only the steps it recorded since it opened
  the file, and sets `sidecar` True.
- **Embedded:** V1 writes nothing (tag, lines 226 to 227), so its steps are gone.

**V2 opens the format 5 parts and does not read V1's folder.** Changes V1 made to recorded objects are changes made
without Hindsight, and D46 handles them as it does today:

- If `.blend1` still holds the mesh V2 saved (V1 saved once, with Save Versions on), D107 turns the change into one
  topology step.
- Otherwise that object's history is left out, with D46's message.

When V2 sees `sidecar` True next to `hindsight_parts` (V2 always writes False, so only an older build sets it), it
adds a note: "This file was saved by an older Hindsight after this one. The steps it recorded are in '{folder}' and
were not brought back". The next save writes `sidecar` False. The folder stays as it was.

V1's steps are not joined onto the format 5 history, for three reasons:

- **Sequence numbers clash.** V1 recorded with a fresh registry (`next_seq` starts at 1, `hindsight/core/registry.py`
  17), so its sequence numbers repeat the format 5 history's. They are in each step's bytes as well as in its meta
  (#42), so renumbering would change the two separately.
- **The seam would need proof.** Only playing V1's steps back to their start and comparing the mesh with the index
  part's saved print shows that V1 started from where V2 saved.
- **Two registries' settings would have to be merged.**

That is a lot of work for a case that needs V1 on one PC and V2 on another, and that ends when the PC is updated
(the owner's given). It is the owner's call: see below.

## Which V1 code stays and which goes

**Stays for good.** There is no way to know that every V1 file has been opened and saved by V2: they sit on
classmates' and the teacher's disks. V1 also keeps writing formats 1 to 4 on any PC not yet updated, including a
history folder beside a format 5 file. What stays:

- `persist.registry_from_bytes` (formats 1 to 4, the header trailer, per-step CRC-32s, the damaged-step cut).
- `_FileBytes`, `load_sidecar`, `sidecar_dir`, `sidecar_file`, `sidecar_missing` and `set_aside`.
- In `hindsight/bl/persistence.py`: `_load_registry`'s reading order above, `from_text` (base64 and bytes),
  `from_sidecar`, the stale-folder check, the missing, unreadable and damaged notes, and `_report_load`.
- The engine's decoders for encodings 1, 3 and 4. Format 5 step parts hold them too: a history moved in and saved
  before the packer reached it keeps encoding 1 steps.
- The crash journal's reader, `replay` and `journal_belongs`, shared with format 5 (#44).

**Goes, once V2 writes only format 5.** This is everything that writes formats 1 to 4 or a folder:

- `registry_to_bytes`, `registry_parts`, `parts_to_bytes` and `save_sidecar` (with its `index.json`).
- The folder half of `on_save_post`.
- `_FALLBACK` with `EMBED_FALLBACK_TEXT`, `SIDECAR_FAILED_TEXT` and `_PROTECT["sidecar"]`.
- Writing `hindsight_data_bin` and the Text's `save_id`.
- The `save_mode` preference and `_mode()`.

`_encode_raw` stays: format 5 encodes raw steps the same way (#41). For any one file, none of the V1 reading code runs
again after the move-in, since V2 finds `hindsight_parts` first.

## Tests that prove it

These exist and keep passing with their checks unchanged:

- `tests/test_bl_beta2_upgrade.py`: real V1 files in `tests/fixtures/beta2` (folder, embedded, both, and a crash
  with two strokes only in the journal). Every step is exact both ways, the journal comes back with "Unsaved steps"
  and not with "Last save", the file opens alone after one save, and it opens moved without its folder.
- `tests/test_core_migration.py`: formats 1 and 2 (`tests/fixtures/beta1_format1.hsr`, `beta2wip_format2.hsr`,
  `legacy_expected.npz`) load, play back, are saved again, are re-packed and take new steps on top.
- `tests/test_bl_t1_migrate.py`: .blend files saved by the format 3 build and by the format 1 build, made fresh by
  those builds' own code. Its save in the folder mode becomes a format 5 save when the option goes.
- `tests/test_bl_embed_bytes.py`: the main-branch build's `hindsight_data_bin`, base64 winning when both are there,
  and a damaged copy kept aside.
- `tests/test_core_reference.py` with `step_corpus_base.zip`: encodings decode bit for bit.

New ones the build ticket adds, each also on the files above:

- **Move-in byte for byte**: after the first save, every step part holds the V1 steps' bytes in order, with the same
  CRC-32s and metas. The index part has V1's settings, cursors and members. Reopened from the .blend alone, every
  position is exact both ways.
- **Mixed encodings**: one history holding encodings 1, 3 and 4 moves in as it is and plays back. A new fixture is
  needed: the format 1 file recorded on and saved by V1, made by `tests/fixtures/beta2/make_beta2_files.py`.
- **The Text after the move-in**: the stub, `sidecar` False, no `hindsight_data_bin` and no `save_id`.
  `hindsight_data_unreadable` is kept. The history folder has the same SHA-256 as before. The folder note shows once.
- **Blender's copy dropped**: after opening an embedded V1 file the Text holds the stub. Closing without saving
  leaves the .blend with the same SHA-256.
- **The round trip**: a format 5 file opened, recorded on and saved by the real V1 code in each save mode (as #41 ran
  it), then opened by V2. The format 5 history comes back, D46 and D107 handle the changed meshes, the note shows in
  the folder and both modes, and the next save writes `sidecar` False with the folder untouched.
- **Opened without its folder, then saved**: format 5 with no history for those objects. The folder put back
  afterwards is not read, and the note says so beforehand.
- The full suites on Blender 4.2, 5.0, 5.1, 5.2 and 5.3.

## For the owner to check

A format 5 file that V1 records in loses V1's steps as separate steps. At best, D107 keeps them as one step. In V1's
embedded mode V1 already drops them itself. My recommendation is to leave it that way, since the case ends when every
PC has V2. If you want V1's steps joined on, that is new work for the build ticket (the three points above), and the
doc would change only in that section.

## Alternatives considered

- **Re-encoding every V1 step at the move-in**: lost; see "V1 steps keep their own encoding" and #42.
- **Moving in at open instead of at the first save**: a file opened only to look at would be rebuilt for nothing.
  The first save has to write every part anyway. Lost.
- **Keeping V1's embedded copy in Blender's memory until the first save**: an autosave would then keep the history,
  but the history would be held twice until that save, against #9's given. Lost.
- **Reading the embedded copy first in a both file**: neither copy is always the newer one. V1 keeps an older
  embedded copy when building the new one fails (tag, `persistence.py`: line 229 raises before 230 stores it), and an
  older folder when writing that fails (249). So V1's own order was kept, and D46 handles a copy that doesn't fit.
- **Deleting the history folder at the move-in**: one-way, and the folder is the only copy behind an older `.blend1`.
  Lost to the note.
- **Writing a V1 copy beside format 5 so V1 still sees the history**: every save would join the whole history again
  (a peak of the history plus two copies, #41), against the given that older builds open the file without it. Lost.
- **Joining V1's steps after a round trip**: see "A format 5 file that V1 saved again". Left to the owner.
- **Dropping the V1 reader after a while**: no one can know when the last V1 file has moved in. Lost.

## How it was checked

By reading this branch's code and V1's (tag `v1.0.0-beta.2`), #4's fixtures and tests, and #41's measured runs of
older builds on format 5 files. This doc measures nothing new and gives no timings. Line numbers are this branch's
(`v2/7-format5-design`, 2e318b0) unless they say "tag".
