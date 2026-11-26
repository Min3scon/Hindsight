# Format 5: how the crash journal fits

Design for #44, part of #7 ("Design the history's new layout inside the .blend"). It keeps to the owner's givens in
`DECISIONS.md` ("History inside the .blend", 2026-11-22): Blender's in-memory copy of the history is dropped after
opening and put back while saving (#9), a file recovered from Blender's autosave opens without its history, and after
a crash the crash journal brings everything back when the .blend itself is opened. It builds on the layout
(`dev/FORMAT5_LAYOUT.md`, #41), step encodings (`dev/FORMAT5_ENCODINGS.md`, #42) and damage
(`dev/FORMAT5_DAMAGE.md`, #43).

In short: the crash journal stays as it is today, record format included. Format 5 changes three things around it:
where the save id is read from, the order of work after a save, and what happens when a file without its history parts
is opened. Anything measured says so in "How it was measured". The rest comes from reading today's code
(`hindsight/bl/persistence.py`, `hindsight/core/journal.py`, `hindsight/core/persist.py`) and V1's (tag
`v1.0.0-beta.2`).

## New words

- **Save id**: the id every save gives the history (today's `save_id`, D91), 32 hex characters, new at each save.
- **File without its parts**: a .blend with the stub `.hindsight_history` but no `hindsight_parts` group (#41). This
  is a file Blender wrote on its own while Hindsight held the history: its autosave, or the `quit.blend` that File >
  Recover > Last Session opens.

## What the crash journal holds

The same as today: every change to the history since the last save, one record per change.

| Record | Written when | Holds |
|---|---|---|
| SAVE | the journal starts again | the save id of the save it follows, the .blend's path |
| STEP | a step is recorded | the history's uid, the step's position, the step's bytes: a packed step as it is, a raw step in the quick encoding (on a helper thread when it is big, R079) |
| CURSOR | a jump | the history's uid and its cursor |
| TRUNC | recording after going back cuts the steps ahead | the history's uid and how many steps it keeps |
| DROP | the step cap, the memory limit or Delete earlier steps drops the oldest steps (the first two only if `FORMAT5.md`'s owner point 8 keeps them) | the history's uid and how many |
| RESET | a history is cleared or rewritten (a join) | the history's uid and the cursor it was cleared at, or the meshes' fingerprints |
| META | a history's members or settings change; with no history, a session going on after a recovery (`resync`) | the history's uid and what changed |
| META_EVERYTHING | Record everything is turned on or off for something | its uid and on or off |

Every record carries the id of the Blender session that wrote it and a CRC-32. Reading stops at the first damaged
record, so a crash in the middle of a write never costs the records before it.

The journal holds nothing that is already in the .blend, and it never names a step part. Its records name a history
by its uid and a step by its position, and format 5 keeps both. So a save that writes only the new steps (#41) does
nothing different to the journal: once the save is in the .blend, the journal starts again from it. The packer's
smaller copy of a step isn't journaled either. It is the same step, and the next save stores it.

There is one journal per .blend path, a file in the add-on's user folder named after a hash of the .blend's full path
(`journal_path`).

## When it starts again

Starting again means emptying the journal and writing a SAVE record, synced to disk.

- **After every save of the session file**, once Blender has written it (`save_post`), naming the save id that save
  wrote into the index part. In format 5, `save_post` does this **first**, before it takes the parts back (#41). That
  way the file on disk and the journal name different saves only between Blender putting the new file in place and
  Hindsight's `save_post`.
- **At open, when nothing is brought back**: Crash recovery is set to "Last save", or the journal belongs to another
  save (with today's note when it holds changes). The SAVE record names the opened file's save id.

The journal does **not** start again:

- At open when steps are brought back. The journal goes on (a `resync` META marks where), because the .blend on disk is
  still that save, so a second crash brings back the same steps and the new ones.
- After Save Copy (the session file didn't change).
- After a save Blender failed to write (`save_post_fail`). Hindsight takes the parts back and the journal stays.
- After a save whose parts could not all be put back. V1 likewise keeps the journal when its history folder could not
  be written. What such a save writes into the .blend is #9's to settle. The journal is kept so that the steps since
  the last good save are not lost with it.
- When a file without its parts is opened (below).

## How recovery knows the journal belongs to this save of this file

- **This file**: the journal is found by the .blend's full path. A .blend copied or moved elsewhere has no journal.
- **This save**: `save_pre` gives the history a new save id. The index part holds it (#41, both copies, #43), and the
  SAVE record that starts the journal again names it. Steps come back only when the journal's last SAVE names the
  index part's save id (`journal_belongs`, today's rule since D91). A .blend replaced by another save of the same path
  has another id, so nothing is replayed and a note says so. That covers a `.blend1` renamed back, a copy put over the
  file, and a crash between Blender writing the file and the journal starting again.
- In format 5 the save id is read from the index part instead of the main-branch build's history header (#41). The
  Text's `save_id` property is read only in that build's files (#45). V1's files and journals have no save id and
  still match each other, as today.
- When both index copies are damaged (#43), there is no save id to match and nothing is replayed. The journal starts
  again at open: its records are positions in histories whose cursors and members were only in the index part.

## A crash before, during and after a save

The session opened the .blend at save A, and the user records and then saves (save B). What comes back assumes Crash
recovery is set to "Unsaved steps" (see "For the owner to check"). With "Last save", nothing since the last save comes
back in any row, and the journal starts again at open.

| Blender crashes | The .blend on disk | The crash journal | Opening the .blend brings back |
|---|---|---|---|
| Between saves (before save B) | save A | SAVE(A) and every change since, except the newest big records if they are still waiting for the helper thread (R079) | every step and change in the journal since save A (all of them, but for those big records); the objects as saved; scrub forward to see the steps |
| In `save_pre`, while the parts are built and put back | save A | SAVE(A) and every change since, except the newest big records if they are still waiting for the helper thread, because `save_pre` does not wait for it today (R079) | as in the first row. Blender's copy is the only copy of the full step parts by then (#41), but the process dies whole and nothing on disk changed |
| While Blender writes the file | save A (Blender writes the new file beside it and puts it in place only once it is whole) | as above | the same |
| After Blender put save B in place, before `save_post` | save B, which holds every step | SAVE(A) and the changes since | nothing is replayed and nothing is missing: every record was written before save B, which holds them all. Today's "another save" note shows, needlessly. Starting the journal first in `save_post` keeps this window short |
| In `save_post`, after the journal started again | save B | SAVE(B) | nothing is needed: save B holds everything |
| After save B | save B | SAVE(B) and every change since, but for big records still waiting, as in the first row | every step and change in the journal since save B |
| After a save Blender could not write | save A | SAVE(A) and everything since, kept, but for big records still waiting, as in the first row | every step in the journal since save A |

The one loss in the table is R079's: a record of a step held at 4 MB or more (a topology step on a big mesh) is written
by a helper thread, so a crash can lose the newest such records before they reach the journal. The journal stays a
prefix of the session: every step before them comes back. What waits for the helper today: `_mark_saved` (the journal's restart in `save_post`), `drain_journal_writer`
and closing the journal. `save_pre` does not, so a crash while a save is built or written can lose those records too:
until save B is in place, the file on disk is save A and the journal may lack them. From save B on, the file holds every
step. The build ticket must make `save_pre` call `drain_journal_writer()` first, which closes the window (see below).

If the crash falls while Blender swaps the files, its backup `.blend1` is save A, and the journal still names save A.
Renaming `.blend1` back gives a file the journal belongs to, and every step comes back.

## How the steps come back when the .blend is opened

1. `load_post` reads the index part, which gives the save id, then each step part, deleting Blender's copy as it goes
   (#9, #41).
2. If Crash recovery brings steps back and the journal's last SAVE names that save id, the journal is replayed as
   today (D77 to D83). The saved state stays at its cursor, and what the session recorded after it comes back ahead
   of it. A replayed step is held as its journal bytes (R064), which are a step's bytes in one of #42's encodings,
   written quickly: the same bytes a save writes for a raw step.
3. The journal goes on from there (`resync`).
4. The next save takes the replayed steps into the open step part as they are, without encoding them again. It
   rebuilds the step parts that replay changed (a TRUNC or DROP that reached steps in a part), reuses the rest
   (#41), and then starts the journal again.

Dropping Blender's copy (#9) changes nothing here. Recovery reads two things: the .blend on disk, which every V2 save
writes with all its parts, and the journal, which Hindsight writes from its own steps. Neither one reads Blender's
in-memory copy.

## A file recovered from Blender's autosave

Blender writes its autosave and `quit.blend` on its own and runs no Python handler for them: bpy 4.2.23 and 5.3.0 have
no such handler (measured). Between saves Blender holds only the stub (#41), so these files have no history parts. The
owner accepted that such a file opens without its history. Opening one, V2:

- Loads no history and shows a note, for example: "This file holds no history: Blender saved it on its own (an
  autosave). Open the .blend you saved to get its history back, with the steps since its last save. Saving this file
  over it replaces that history."
- Leaves the crash journal of the file's path as it is, neither replayed nor started again. A recovered file can open
  under the .blend's own path: Blender stores the original path in its autosave so that the recovered file saves back
  there. That couldn't be measured, because the bpy module can't autosave. V1's rule starts the journal again whenever the
  file has a journal and no history was loaded. Under that rule, the steps since the last save would be gone before
  the user opens the .blend itself.
- Journals what this session records as an unsaved file does, until it is saved. Nothing it records could be brought
  back from its own journal anyway, since recovery needs a history in the file it replays onto. Recording these steps
  into the .blend's journal would be wrong: they start from the autosave's objects, not the saved ones.
- If this session is saved over the .blend, that .blend's history is replaced by what this session recorded, and the
  journal of that path starts again. Blender's `.blend1` keeps the save before it (Save Versions, on by default).

Opening the .blend itself, at any time before saving over it, brings every step in the journal back, the steps since
the last save included. That is more than the autosave holds, since the autosave can be up to one interval (2 minutes
by default) older than the crash.

## Hindsight turned off (#124)

Between saves the Text holds only the stub, so a save after Hindsight was turned off (Preferences, a script reload)
would write a file without its parts. `unregister()` therefore does a save's build first: the parts go back on the
Text with a new save id, and a META record `{"parts": <that save id>}` tells the journal that those parts hold every
step journaled so far. `register()` in the same session takes Blender's copy back again (the registry is still the
session's). Blender also calls `unregister()` at every quit, so a quit costs one build; unchanged step parts are
reused (#65).

- **Saved while off**: the file has the new save id, the journal's last SAVE names the one before, so nothing is
  replayed onto steps the parts hold; recovery's "belong to another save" note looks for records after the marker
  only, so none is shown.
- **Not saved**: the file and its journal still match, the marker is ignored, recovery is as before.
- **Not covered**: steps recorded after Hindsight is turned on again, followed by a crash before a save, are not
  replayed onto a file saved while it was off (that file had no history at all before #124).

## The journal's record format does not change

The records keep the `HSJ1` magic, the same eight types and the same headers. Three reasons:

- A STEP's payload is a step's bytes, and #42 keeps every step's bytes as they are (the encoding number sits in today's
  `HSS3` prefix).
- Records name histories by uid and steps by position, which format 5 keeps.
- The save id a SAVE names is the same value the main-branch build writes. Only the place it is read from changes.

So V1's journals are read by the same reader. A V1 file's journal is replayed onto it as today (#4 showed every V1 save
mode and V1's crash journal come through exactly), and the first V2 save starts the journal again with the index
part's save id. A journaled step whose
encoding this build doesn't know is told apart from damage by its own bytes, and handled as one in a step part (#42).

Older builds read `HSJ1` too, but never replay onto a format 5 file, since they load no history from it. V1 opening such
a file empties its journal if it finds one (`on_load_post`, tag `v1.0.0-beta.2`, lines 367 and 416). So when V1 uses
the same journal folder, the steps of a crashed V2 session come back only if V2 opens the .blend first.

## For the owner to check

Crash recovery's default is "Last save" (`crash_recovery` in `hindsight/bl/prefs.py`). With that setting, opening the
.blend after a crash brings back nothing since the last save and starts the journal again, so those steps are gone even
if the setting is changed afterwards. The decision that a recovered autosave opens without its history counts on the
journal bringing everything back when the .blend is opened. That holds only with "Unsaved steps".

**Recommendation**: make "Unsaved steps" the default in V2, like the other new defaults of 2026-11-22. The cost: a
session closed normally without saving also leaves its steps in the journal. They come back ahead of the saved state
the next time the file opens, with the objects as saved. If that is unwanted, a normal close can empty the journal. The
`exit_pre` handler exists in bpy 5.3.0 but not 4.2.23 (measured), so 4.2 would need Python's `atexit`. Whether either
one runs at a close and not at a crash is for the build to measure.

## Alternatives considered

- **Starting the journal again in `save_pre`**: a crash while Blender writes would leave the old .blend and an empty
  journal, so every step since the last save would be lost. Lost; it stays in `save_post`, as today.
- **Starting it again after taking the parts back**, today's order carried over: the file and the journal would name
  different saves for a while longer (taking the parts back takes 0.06 to 0.07 s per 256 MB, #41), and a crash in that
  time shows the needless note. Lost to starting it first.
- **Letting Blender's autosave carry the history**: Blender runs no handler for its autosave. Keeping Blender's copy
  between saves is what #9 drops. And the autosave is up to 2 minutes old, while the journal loses at most the records
  still waiting for the helper thread. Lost.
- **Bringing the history into a recovered autosave** (reading the .blend's parts from disk, replaying the journal and
  finding where the autosave stands in it): the journal doesn't record when the autosave was written. The cursor would
  have to be found by comparing every recorded object with the state at each step, and a wrong match applies steps to
  the wrong mesh. The owner accepted the cost instead. Lost.
- **A record format that names step parts**: which steps share a part changes as parts fill and saves rebuild them, so
  the journal would be tied to the layout for nothing, and V1's journals would need a second reader. Lost.
- **Matching the journal to the file by its time or a hash of the .blend**: a copy changes the time, and hashing a big
  .blend at each open means reading the whole file. The save id costs 32 characters in the index part. Lost; today's
  rule stays.
- **Keeping the journal across saves** (only adding SAVE records): it grows with every step ever made, while replay
  reads only what comes after the last SAVE. Lost.

## What the build ticket does with this

- Keep the journal and its records as they are, with the save id taken from the index part.
- `save_pre`: wait for the helper thread first (`drain_journal_writer()`, before the parts are built), so a crash during
  a save loses no step. Test: queue a big record, crash in the middle of a save, recover: every step is back.
- `save_post`: start the journal again first, then take the parts back. `save_post_fail`: take the parts back and leave
  the journal. A save whose parts could not all be put back leaves the journal too.
- At open, a file without its parts: no history, the note, the journal of its path untouched, and the session
  journaling as an unsaved file until it is saved.
- Tests, each on a format 5 file:
  - A crash before a save and after one, each bringing every step back.
  - The journal of save A beside the file of save B: nothing replayed, every step in the file.
  - A failed save keeping the journal, so recovery replays onto save A.
  - Replayed steps saved into the open step part byte for byte as the journal held them.
  - A file without its parts at the journal's own path: the journal byte for byte the same after opening it, the note
    shown, and the .blend opened afterwards bringing every step back.
  - A V1 file and its V1 journal replayed, then the first V2 save starting the journal again with the index part's
    save id.

## How it was measured

Throwaway scripts, not merged, run in November 2026 on the owner's Windows 10 PC in the bpy modules 4.2.23 and 5.3.0.

- **Recovered files**: a .blend with the stub and a `hindsight_parts` group was saved. The group was then deleted and
  the file saved again with `save_as_mainfile(copy=True)`, as an autosave would hold it. Opening that file with
  `wm.open_mainfile` and with `wm.recover_auto_save` ran `load_post` both times and showed the stub without
  `hindsight_parts`. A copy of a saved file put in the temp folder as `quit.blend` (none was there) and opened with
  `wm.recover_last_session` also ran `load_post`. Each of these files opened under the path it was read from. They were
  written by `save_as_mainfile`, not by Blender's autosave, which the bpy module can't run.
- **Handlers**: `bpy.app.handlers` in 4.2.23 and 5.3.0 has no handler for Blender's autosave. `exit_pre` exists in 5.3.0
  and not in 4.2.23.
