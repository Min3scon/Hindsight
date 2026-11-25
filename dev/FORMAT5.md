# Format 5: the design

Design for #7 ("Design the history's new layout inside the .blend"), put together by #47 from the answers of #41 to
#46. It answers #7's question: how the history is laid out inside the .blend so that a save only writes the steps
added since the last save, and so that better step compression can be added later without changing the format again.

It keeps to the owner's givens in `DECISIONS.md` ("History inside the .blend", 2026-11-22): the history always lives
inside the .blend; every step is kept, exactly; Blender's in-memory copy is dropped after opening and put back while
saving (#9); older builds (V1) keep a format 5 history untouched and open the .blend without it. Blender writes the
whole .blend on every save, so "only new steps" means Hindsight's own work in a save.

This page gives each answer with its reasons in short, then where the answers meet, what the owner must decide and
what the build ticket (#11) does. The six docs below hold the detail, the measurements and the alternatives that lost;
read them in this order, since each builds on the ones before it.

| # | #7's question | Answer in one line | Doc |
|---|---|---|---|
| 1 | How the history is split, and what a save rewrites | Step parts of about 4 MiB per history plus an index part, on V1's Text behind a format 5 stub; a save builds only the open or changed step parts | `dev/FORMAT5_LAYOUT.md` (#41) |
| 2 | How step encodings are versioned | The encoding number is byte 10 of the step's own bytes, behind today's `HSS3` prefix; today's steps are encodings 1, 3 and 4 | `dev/FORMAT5_ENCODINGS.md` (#42) |
| 3 | How damage is found and what survives it | A CRC-32 on every part, the index part kept twice; a damaged part costs only its damaged steps and those reachable only through them | `dev/FORMAT5_DAMAGE.md` (#43) |
| 4 | How the crash journal fits | Unchanged, record format included; the save id comes from the index part, and `save_post` starts the journal again first | `dev/FORMAT5_JOURNAL.md` (#44) |
| 5 | How V1's formats 1 to 4 are read and moved in | Read by today's code; the first format 5 save copies every step's bytes into step parts, decoding nothing | `dev/FORMAT5_V1.md` (#45) |
| 6 | What opening costs at 100,000 steps on a 1M-vertex mesh | Too much at today's step size and at the 2 bytes per vertex target; the step size decides it, not the layout | `dev/FORMAT5_OPENING.md` (#46) |

## The layout

```
Text ".hindsight_history"   fake user; V1's container
├─ hindsight_data           the stub: a V1 history header saying "format 5", no steps (80 characters, never changes)
├─ sidecar                  False
└─ hindsight_parts          in the saved file, and in Blender's memory only while a save runs
   ├─ index                 the index part: settings, save id, each history's uid, members, cursor, morph target,
   │                        and its step parts (number of steps, length, CRC-32, where the steps begin); ends with
   │                        its own CRC-32
   ├─ index_copy            the same bytes again
   └─ <history uid>         one group per history
      ├─ "0", "1", ...      full step parts: step table + CRC-32, metas + CRC-32, then the packed steps back to back
      └─ last               the open step part, which grows until it is full
```

## 1. How the history is split, and what a save rewrites (#41)

Each history's steps fill its open step part up to 4 MiB; then the part is full and is never changed again unless its
steps change. A save after N new steps:

- encodes only the N new steps (raw steps in the quick encoding, packed steps as they are);
- builds the open step part of each history that changed (its older steps copied, not encoded), the full step parts
  it filled, and the step parts whose steps changed since the last save (the packer packed them smaller, the user went
  back and recorded, Delete earlier steps dropped the oldest steps);
- rebuilds the index part, whose size grows with histories and step parts, not with steps;
- hands every other step part to Blender as the bytes Hindsight already holds.

Reasons:

- **Parts on V1's Text, behind a stub**: older builds read the stub, say the file was saved by a newer Hindsight, and
  save the parts back byte for byte (measured with V1 and the main-branch build, 9 cases out of 9). The Text outlives
  deleted, linked and appended objects and scenes, and the main-branch build skips it by name when it records
  everything.
- **About 4 MiB per step part**: Blender 4.2 gets slow with many properties in one group (100,000 took 146 s to store),
  and a save rebuilds at most one open step part per changed history. 1,000 step parts cost Blender under 0.6 s to read
  at open (#46). 4 MiB is a starting value; a reader takes parts of any size, so tuning it changes no format.
- **Hand-over**: once Blender has copied a full step part, Hindsight drops its own reference, so the history is held
  once even during a save: 4 to 23 MB above 256 MB of steps at the peak, against 513 MB for V1's way. For that,
  Hindsight holds each full step part as one bytes object and reads its steps by offset.
- **Bytes, not base64**: base64, as in V1's `hindsight_data`, is a third bigger and slower to save and open (R061).

## 2. How step encodings carry a version (#42)

The first 11 bytes of every step since format 3 are the step prefix: `HSS3`, a CRC-32 of the rest, the header length,
and at byte 10 the encoding number. Encodings 3 and 4 already have it, and `HSS1` marks encoding 1, so no step is
re-encoded to fit. A later encoding takes the next number (5 to 254; 255 is kept for tests) and changes nothing else:
no new format, no change to the index part or step parts.

A step whose encoding a build doesn't know (an earlier V2 build opening a later one's file) is kept as its bytes, listed
with its meta from its step part, saved back as it was and never re-packed. Jumps stop at it, the user gets a note per
object, and a history whose saved cursor stands before one is held (kept and saved as it was, not recorded into),
since recording would cut it.

Reasons:

- **In the step's own bytes**: a step also lives outside step parts (the crash journal, memory), so its bytes must say
  how to read them, and the packer changes one place per swap, not two.
- **One number per whole recipe**: every codec, record coding and predictor is fixed on the day the number ships, so an
  unknown codec inside a known encoding can only be damage. Self-describing steps would cost bytes in every step.
- **The prefix CRC covers byte 10**: damage is never taken for a newer encoding, and the two are told apart from the
  step's bytes alone. Today's engine reads both as `CorruptStep` (measured), so the build splits them.

## 3. How a damaged part is found and what survives it (#43)

At open, the index part's CRC-32 is checked (`index_copy` only when `index` fails), then each step part's length and
CRC-32 against the index part: one pass over the history, 0.03 s per 256 MB with the engine's `crc32`, as today's load
already takes a CRC-32 of every step. Only a part that fails gets more work: its step table's and metas' own CRC-32s,
then each step's CRC-32, and when the step table is damaged its steps are found by their `HSS3` prefix (about 2 ms per
4 MiB part).

| Damaged | Lost |
|---|---|
| The stub, `sidecar`, one index copy, a step part's metas | nothing |
| A step part's step table | the steps its prefix search can't find |
| A step part missing or cut short | its steps, or those after the cut |
| A step | that step |
| Both index copies | the use of every history (cursors and members are only there); the parts are set aside as `hindsight_parts_unreadable`, untouched |

Wherever steps are lost, the steps reachable only through them go too: the history keeps the steps reachable from its
saved cursor, as V1 does today, with today's notes. Other histories are not touched. Nothing is written until the user
saves; the next save drops the damaged steps and those beyond them, and rebuilds only the step parts at the edges of
what is kept.

Reasons: a CRC-32 tells that bytes changed, not what they were, and every step undoes and redoes itself, so no jump can
cross a damaged step. The index part alone ties steps to objects and cursors, so it is the one part kept twice (tens of
KB per save at most). Everything else is already held twice elsewhere: each meta in its step, each step's place in its
prefix.

## 4. How the crash journal fits (#44)

The crash journal stays as it is, `HSJ1` record format included: its records name a history by uid and a step by
position, which format 5 keeps, and it never names a step part. So V1's journals are read by the same reader. Three
things change around it:

- **The save id** is read from the index part (both copies). Steps come back only when the journal's last SAVE names
  it, today's rule since D91.
- **`save_post` starts the journal again first**, then takes the parts back, so the file on disk and the journal name
  different saves only between Blender putting the new file in place and `save_post`. `save_post_fail` takes the parts
  back and keeps the journal; so does a save whose parts could not all be put back.
- **A file without its parts** (the stub and no `hindsight_parts`: Blender's autosave or `quit.blend`, written while
  Hindsight held the history) opens without history and with a note, and leaves the journal of its path untouched.
  Today's rule would empty it, and the steps since the last save would be gone before the user opens the .blend itself.

The doc walks through a crash before, during and after a save, with Crash recovery set to "Unsaved steps": every step
comes back in each case, with one exception. A crash between saves can lose the newest big records (steps held at 4 MB
or more, such as a Voxel Remesh on a big mesh) still waiting for the helper thread (R079). The journal stays a prefix,
so every step before them comes back. `save_post` and the journal's restart after a save wait for the helper, but `save_pre` does not today, so a crash
while the save is being built or written (before save B is in place) can lose the same newest records. Once save B is
in place it holds every step. The build ticket makes `save_pre` wait first (point 9 below).

## 5. How V1's formats 1 to 4 are read and moved in (#45)

V2 tells what it opened by the Text: no Text, no history; a `hindsight_parts` group, format 5; the stub without parts,
a file without its parts (#44); anything else, a V1 history. A V1 history (formats 1 to 4, in a folder, embedded or
both, with V1's crash journal) is read by today's code in V1's own order. Then V1's embedded copy is dropped from
Blender's memory and the stub put in its place, so the history is held once.

The move-in is the first format 5 save: an ordinary save in which every step part is new. Every step's bytes go into
step parts in their own encoding (1, 3 or 4), with the CRC-32 and meta they were read with; nothing is decoded or
re-encoded, and the packer re-packs encoding 1 steps later while the user is idle. The history folder is never deleted;
after the save it is no longer read, and a note says so once.

The V1 reading code stays for good (V1 files sit on disks V2 never sees). What writes a history folder goes with #8;
what writes formats 1 to 4 goes once #11 writes format 5. A format 5 file that V1 records in and saves keeps its parts intact; V2 opens the parts, and D46
and D107 handle what V1 changed, as for a session without Hindsight.

Reasons: a byte copy can't break a step and is fast at 100,000 steps; moving in at the first save, not at open, keeps a
file opened only to look at untouched; deleting someone's folder is a one-way door, and it is the only copy behind an
older `.blend1`.

## 6. What opening costs at 100,000 steps on a 1M-vertex mesh (#46)

Blender reads the whole .blend, every step part included, before any add-on code runs: 4.0 to 4.7 s per GB here, and
the history held once. Format 5 then costs Hindsight about 0.5 s per GB with a 4 MB peak, plus about 0.45 s per
100,000 steps. Today's opening costs about 1.05 s per GB, peaks at three copies of the history and keeps two.

Estimates for 100,000 strokes that each move 20,000 vertices:

| Steps of | History | Opening, format 5 | Peak, format 5 | Peak, today |
|---|---|---|---|---|
| today's size (4.1 bytes per vertex moved) | 7.6 GB | 36 to 42 s | 7.9 GB | 23 GB |
| the 2 bytes per vertex target | 3.7 GB | 19 to 22 s | 4.0 GB | 11.4 GB |
| about 10 KB | 0.93 GB | about 6 to 7.5 s | about 1.2 GB | not worked out |

That is too much for "it must run well on any PC" at either of the first two sizes. Nothing in #41 to #43 changes:
the layout already holds the history once plus one step part, which is as low as a history inside the .blend can go.
Only smaller steps bring it down, and a later encoding (#42) gives them without a new format.

## Where the answers meet

The six docs were read side by side for this page. They agree on the layout, the 4 MiB limit, the save id, the order of
work in a save and at open, and what each damage costs. One point was said two ways and is now settled in both docs:

- **When a step's prefix CRC is checked.** #42 lists it among the reader's checks; #43 and #46 check it only when a jump
  decodes the step. Both hold: at open and at a journal replay, the step part's CRC-32 (or the journal record's)
  already vouches for every byte, so the encoding is read from the first 4 bytes and byte 10 without it, which is how
  unknown steps are known at open; the prefix CRC is checked when the step is decoded. `FORMAT5_ENCODINGS.md` now says
  so. Likewise #46's prototype checked every step table's and metas' CRC-32, where #43 checks them only in a part that
  failed, and it did not read each step's encoding; `FORMAT5_OPENING.md` now says both.

**Opening**, in order (`load_post`):

1. Tell what was opened by the Text (#45). A file without its parts: no history, the note, the journal left alone
   (#44). A V1 history: today's reading, then V1's embedded copy replaced by the stub (#45).
2. Read and check the index part, `index_copy` if needed (#43). Both damaged: set the group aside, start the journal
   again (#43, #44).
3. For each step part the index part lists: copy it, check its length and CRC-32, delete Blender's copy straight away
   (#41); then delete the group. A part that failed gets #43's extra checks.
4. Read each step's encoding from its first bytes; list unknown steps with their metas (#42).
5. Cut each history to the steps reachable from its saved cursor, with today's notes (#43); hold a history whose
   cursor stands before an unknown step (#42).
6. Replay the crash journal if Crash recovery brings steps back and its last SAVE names the index part's save id;
   otherwise start it again (#44).
7. Compare the file with its history (D46), as today.

**Saving**:

1. `save_pre`: drain the packer; give the history a new save id; encode the new steps; build the changed step parts
   and both index copies (#41, #43); put the whole `hindsight_parts` group back into a fresh group, handing the full
   step parts over (#41). At the move-in every step part is new, `sidecar` becomes False, and `hindsight_data_bin` and
   the Text's `save_id` go (#45).
2. Blender writes the whole .blend.
3. `save_post`: start the journal again with the new save id, then take each part back and delete Blender's copy
   (#44, #41). `save_post_fail`: take the parts back and keep the journal.

## For the owner to decide

Each comes with a recommendation; none blocks the others.

1. **Hand-over during saves** (#41): while a save runs, Blender's copy of the full step parts is the only copy. It
   halves the peak of a save; the cost is that anything deleting the Text in the middle of a save would lose them
   (#9's build tests a failed save). Recommended: keep it.
2. **4 MiB step parts** (#41): a starting value, a choice and not a measurement; the build tunes it, with no format
   change. Recommended: start there.
3. **The held history** (#42): a history whose saved cursor stands before a step of an unknown encoding is kept
   untouched and not recorded into until Hindsight is updated. Encoding 255 is never given out. Recommended: as
   written.
4. **The index copy and the step part header split** (#43) change #41's layout slightly: the index part kept twice, and
   each step part's step table and metas with CRC-32s of their own. They are in the bytes, so they must be settled
   before the first build sub-issue starts. Recommended: keep them, for tens of KB per save.
5. **Crash recovery's default** (#44): today "Last save", which empties the journal at the first open after a crash, so
   the steps since the last save are gone even if the setting is changed afterwards. The accepted cost of #9 (an
   autosave opens without its history) counts on the journal bringing everything back. Recommended: "Unsaved steps" as
   V2's default; a session closed normally without saving then also leaves its steps in the journal, unless a normal
   close empties it (`exit_pre` on 5.3, `atexit` on 4.2, for the build to measure).
6. **V1's steps not joined on** (#45): when V1 records in a format 5 file and saves it, V2 opens the format 5 parts, and
   V1's steps come back at best as one step (D107). Recommended: leave it; the case ends when every PC has V2. Joining
   them is listed as optional build work.
7. **The size target as a first stop** (#46): 2 bytes per vertex moved still leaves 3.7 GB at 100,000 strokes of
   20,000 vertices. Recommended: treat it as a first stop and keep going towards about a kilobyte per step in the
   optimisation tickets, plus a round on the opening work that doesn't depend on the history (1.5 to 1.7 s on a
   1M-vertex mesh).
8. **The step cap and the memory limit** (`DECISIONS.md`): today both drop a history's oldest steps on their own
   (`max_steps`, 10,000 steps per object by default; `memory_limit_mb`, 4096 MB in all; `enforce_caps` in
   `hindsight/core/store.py`). `DECISIONS.md` still lists the step cap (up to 100,000, default 10,000), but the
   owner's later givens (2026-11-22) keep every step ("No merging or dropping of old steps"), and the design's target
   of 100,000 steps is past the cap's default. Recommended: in V2 neither drops steps. The step cap goes, and the
   memory limit warns instead of dropping. Steps then go only when the user asks: Delete earlier steps, or recording
   after going back. No byte of format 5 changes either way, so #11 need not wait for it.
9. **A wait in `save_pre`** (#44, review of this PR): today only `save_post` (`_mark_saved`) waits for the journal's helper
   thread, so a crash during a save can lose big records still queued. A requirement for #11, not an owner choice
   with a cost worth weighing: `save_pre` drains the helper first (`drain_journal_writer()`), at the price of waiting
   for a few big writes before each save. Recommended: require it. No format 5 byte depends on it.

## What the build ticket does

Each doc ends with what #11 does with it: #41 "What a save after N new steps does" and "How it fits #9", then the
"What the build ticket does with this" sections of #42, #43 and #44, "Tests that prove it" in #45, and nothing in #46
beyond the step size, which is for the optimisation tickets. How #11's sub-issues are re-split to fit is in a comment
on #11.

Left to other tickets:

- **#9**: what a save writes into the .blend when its parts could not all be put back (with #48's rule), whether
  deleting the parts after a save leaves the file unchanged in a real 4.2 window, and whether changing the Text at open
  marks the file as changed.
- **#8**: removing what writes a history folder, and the Save history preference.
- **The build**: how a step part numbers its first step after the oldest steps go (Delete earlier steps, and the step
  cap and the memory limit if point 8 keeps them), which decides whether the step parts after a drop are reused or
  rebuilt once (#43); and removing what writes formats 1 to 4.

With the owner's answer to point 4 above, #11 can start (its other blocker is #8): no question for the owner is left
that changes the bytes of a format 5 file. The other points change later sub-issues only.
