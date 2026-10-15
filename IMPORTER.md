# History from saved versions (design)

Owner's rule (HANDOFF, "No fake history"): a file Hindsight never recorded has no history. Show
nothing, say so plainly, offer the honest alternative. Steps come only from versions that exist on
disk, one per version, with its source and time. Never invent steps, ticks, counters, thumbnails,
labels or progress.

## What the user sees

**Find saved versions** (History Menu, Advanced panel, and under "No history yet" in the sidebar)
opens a dialog for the open file.

1. **Looking** is instant: it only lists files and asks git. Nothing is read or written yet.
2. **Nothing found:** "No saved versions found for this file." and under it "Hindsight can only
   rebuild history from versions that exist on disk. Start recording now and everything from here on
   is kept stroke by stroke." Buttons: Start recording, Add file, Close. No list, no previews, no
   progress, no counts.
3. **Found:** one line per version, oldest first: when it was saved, where it is (Blender backup
   `.blend1`, autosave, numbered save, git commit `abc1234`, a file you added), a tick box. Under the
   list: "One step per saved version. What happened between two saves can't be rebuilt." Buttons:
   Import, Add file, Close.
4. **Reading:** a background Blender reads each ticked version ("Reading version 2 of 3"). No time
   or speed estimate. Cancel stops it; nothing is added and the temporary folder is removed.
5. **Result:** "Added 3 steps to Suzanne and 1 to Rock." Objects matched only by name say so: "Rock
   was matched by name (the saved version has no Hindsight id), so this match is uncertain." Versions
   identical to the one before them add nothing: "2 versions were identical to the one before them
   and added nothing." All identical: "These saved versions match this file: nothing to add."

Sidebar and on-screen slider before any import: "No history yet" with Start recording; the slider
shows an empty track with "0 steps" (or is hidden), never ticks or "1 / N". Same in Object, Sculpt,
Vertex Paint and Edit Mode.

## Which files count as saved versions

- Blender's backups next to the file: `<name>.blend1` to `<name>.blend32` (Save Versions).
- Autosaves of this file in the autosave folder (Preferences > File Paths > Temporary Files, or the
  system temporary folder): `<name>_autosave.blend` and `<name>_<number>_autosave.blend`.
- Numbered saves (File > Save Incremental): same name with a different number at the end
  (`robot_003.blend` next to `robot_004.blend`; `robot1.blend` next to `robot.blend`).
- Git: commits that changed the file, when the file is in a git working copy and git is installed
  (`git log --follow`), read with `git show <commit>:<path>` into the temporary folder.
- Add file: any `.blend` the user picks. Similarly named files (`robot_old.blend`, `robots.blend`)
  are never offered on their own.

Time shown: the file's modified time (a `.blend1` keeps the time of the save it came from), the
autosave's modified time, or the commit time ("committed <time>"). Nothing else is claimed.

## What a version adds

For each mesh object in the open file that has no history yet: its mesh (shape, topology and every
attribute: masks, face sets, colours, UVs) in each version, matched by Hindsight id when the version has one, otherwise by
name and type (said to be uncertain). Steps go oldest to newest; the last step leads to the mesh as
it is now. A step is labelled "Saved version, <date and time>" plus what changed ("shape changed",
"topology changed: 12,480 to 15,032 faces"). The step to the open file is labelled "This file"
(or "Changes since the last save" when it has unsaved changes). Objects that already have a history,
objects missing from every version and other object types are left out and listed. Other changes
between versions (transforms, modifiers, materials) are not imported, and the result says so.

## How it works

- `hindsight/core/importer.py` (no bpy): finding versions (paths, numbers, git output parsing),
  ordering, de-duplication by content digest, and building steps between two snapshots
  (`step_between`, the same deltas and topology steps the recorder makes).
- `hindsight/bl/version_reader.py`: run by a second Blender, `blender -b --factory-startup
  --disable-autoexec <version> --python version_reader.py -- <out>`: writes each mesh object's id,
  name, counts and arrays to `<out>/<n>.npz` plus a small JSON. The version is only opened, never
  saved; auto-run scripts stay off.
- `hindsight/bl/importer.py`: the dialog (operator), the background reads (a timer polls the
  process), matching, and adding the steps to new histories (journalled like recorded steps, so
  saving and reloading keep them). The temporary folder lives under Blender's session temp folder
  and is removed on finish, cancel and file load.
- Nothing is written next to the open file or the versions. The open file only changes when steps
  are added (then it is like any recorded history).

## Walk the stack

Only for an object with a real stack: 2+ modifiers, shape keys, Geometry Nodes inputs or Multires
levels. Otherwise the button is greyed out with one line saying why. It shows the object built up in
construction order, a level at a time, labelled "Construction order, not the order you worked in".
It changes nothing: closing it puts every setting back exactly, and nothing is recorded.

## Tests

- Untouched generated file, no siblings, all four modes, three Blenders, with and without a stack:
  zero steps, the empty-state text, no ticks or counters, no files written anywhere, the file
  byte-identical, no sidecar.
- One `.blend1`; only an autosave; only a git commit; an unrelated similarly named file (not offered
  unless added).
- Cancel before, during and after the read: zero steps, no temporary files.
- Imported steps: exact both ways against the versions (oracle), labels and times, identical
  versions add nothing, matching by name says uncertain, originals byte-identical with unchanged
  modified times.
- Screenshots of every empty state, looked at. check_copy fails on wording that implies recovering
  unsaved work, individual strokes, "go back on any file", and on a caption showing a filled timeline
  for a file with no saved versions.
