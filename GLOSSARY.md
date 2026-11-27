# Hindsight

A Blender add-on that keeps a history for each sculpted or recorded object, so the user can drag back to any
earlier state, look, and drag forward again. This page defines the words used about it, in code, documents
and reports. Use these words and no others.

## Versions

**V1**:
The version of Hindsight that is out now, used by the owner, classmates and the teacher's website; its files call it
1.0.0-beta.2.
_Avoid_: beta 2, beta

**V2**:
The next release, version 2.0.0.
_Avoid_: beta 3, 1.0.0

## The history

**Step**:
One change to recorded data that can be taken back and redone exactly: one stroke, one operation, or one
Edit Mode visit.
_Avoid_: undo step, entry, record, change

**History**:
The steps of one object in the order they were made, with a cursor. Objects that share one mesh share one
history.
_Avoid_: undo stack, timeline (for one object)

**Cursor**:
Where an object's history currently stands: position 0 is before the first step, position n is after step n.
_Avoid_: index, current step

**Jump**:
Moving the cursor to another position; every step in between is taken back or redone.
_Avoid_: goto, seek

**Scrub**:
Many jumps in a row while the slider is dragged.
_Avoid_: drag (for the jumps themselves)

**Slider**:
The bar in the viewport, and the one in the Undo History tab, that jumps through a history.

**Global timeline**:
Every recorded object's steps on one line, in the order they were made; one operation is one position.
_Avoid_: global history (in prose), global slider

**Baseline**:
What Hindsight last saw of an object's data; the next change is found by comparing against it.
_Avoid_: state, snapshot, cache

## Kinds of step

**Delta step**:
A step that keeps only the values that changed (the vertices a stroke moved, for example).
_Avoid_: stroke step, diff

**Topology step**:
A step after which the mesh has different vertices, edges or faces (a remesh, Dyntopo, Edit Mode
operations); it keeps the whole mesh before and after.
_Avoid_: remesh step, TOPO

**Join step**:
A step in which several objects were joined into one; it keeps every object as it was before.

**Data step**:
A step that changes something other than the mesh: a transform, a modifier, a material, an add-on's setting.
_Avoid_: object step, property step

## What is recorded

**Record scope**:
What Hindsight records: Everything in this file (the default), or Sculpting only.
_Avoid_: recording mode, tracking

**Adapter**:
The part that knows how to read and put back one kind of Blender data (modifiers, materials, poses, and so on).
_Avoid_: recorder (for one kind), handler

**Morph target**:
The step that the Morph History brush paints the mesh back towards.

**Movie Mode**:
Playing a history on Blender's timeline so it can be rendered as a timelapse.

## Keeping and saving history

**Raw step**:
A recent step kept unpacked in memory so taking it back is instant.
_Avoid_: hot step, unpacked entry

**Packed step**:
A step kept as compressed bytes; older steps are packed.
_Avoid_: blob, compressed entry

**Packer**:
The background worker that packs older steps, and later packs them again smaller while the user is idle.
_Avoid_: compressor, store worker

**Registry**:
All histories of the open file together; it is what gets saved.
_Avoid_: database, store

**Save mode**:
V1's setting for where the history is saved: in a history folder next to the .blend, embedded in the .blend, or
both. V2 has none: the history is always in the .blend (#8).

**History folder**:
The folder next to a .blend where V1 saved its history (V1's default). V2 never writes one; it reads it until the
file's next save puts the history in the .blend.
_Avoid_: sidecar (in anything a user reads)

**Embedded history**:
History saved inside the .blend itself, so the file alone carries it to another computer.
_Avoid_: internal history

**Crash journal**:
The file that records steps as they are made, so steps not yet saved come back after a crash.
_Avoid_: log, journal file, WAL

## The engine

**Engine**:
The compiled part of the add-on (written in Rust) that does the heavy number work; the add-on records nothing
without it.
_Avoid_: native module, hindsight_native, Rust core (in prose)

**Reference implementation**:
Older Python code kept only in the tests, used to check that the engine gives exactly the same results.
_Avoid_: fallback, pure Python version

## Working on Hindsight

**Round**:
One measured attempt to make one thing faster or lighter: measured before, changed, measured again in
alternating runs, then kept or undone. Rounds are numbered R001, R002, and so on; undone rounds still count.
_Avoid_: optimisation, pass, tweak

**Defect**:
A bug that was found and fixed, with a test that failed before the fix. Defects are numbered D1, D2, and so on.
_Avoid_: issue, bug report

**Full suite**:
Every automated test file, run in Blender as a Python module on one Blender version.
_Avoid_: test run, CI (for local runs)

**Window scenario**:
A test that drives a real Blender window with the mouse and keyboard.
_Avoid_: UI test, GUI test
