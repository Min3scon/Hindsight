# Changelog

All notable changes to Hindsight. Dates are when the release went out.

## 1.0.0-beta.3

Movie Mode sets up its own camera and lighting, a timelapse needs fewer frames and less time per frame, long sessions use less memory, and Hindsight now works on Blender 5.2 and 5.3.

This build was tested without a Blender window, on Blender 5.0 to 5.3. It hasn't been through the test rounds in real Blender yet.

New:

- Movie Mode keeps a camera on the whole model. While Movie Mode is on, Hindsight adds its own camera and places it so every step of the timelapse is in frame. Your own camera isn't moved, and it is the scene camera again when Movie Mode goes off.
- Lighting when the scene has none: three sun lamps around the model, added only while the scene has no light of its own, and taken out again with Movie Mode.
- Use this view points the Movie Mode camera from the direction your viewport looks. Frame the model places it again after the model has changed.
- Steps per frame. Quick auto key can move several steps on each frame, so a long history becomes a short movie with fewer frames to render. The panel shows how many frames and seconds the movie will have.

Faster and lighter:

- Playing or rendering a timelapse: Hindsight's own work per frame went from 22.6 ms to 2.7 ms at 5,000 steps on a 100k mesh. A frame that shows the same step as the one before costs about 0.1 ms.
- With global history on, the slider, the header and the sidebar no longer rebuild the whole timeline on every redraw. That took 18 ms each time at 10,000 steps.
- Saving and loading history works step by step. Peak memory while saving a 6,000-step history of a 100k mesh went from 89 MB to 43 MB, and loading from 141 MB to 83 MB. Recording a remesh at 1M vertices needs 426 MB instead of 609 MB.
- What Hindsight remembers about its own jumps for Blender's undo is kept only as far back as Blender can undo. It used to keep up to 512 copies of every recorded value.
- Once the memory limit is reached, a stroke no longer rescans every step.
- Saving with the history inside the .blend needs about half the extra memory it did: 74 to 86 MB instead of 143 MB at 6,000 steps.
- Background recompression of steps waits while you sculpt or scrub. It also starts after a file is opened now, not only after the first new stroke was packed.
- Redraws cost less. The on-screen slider keeps its drawing between redraws and rebuilds only the handle while you drag. The viewport warning and Collection Filtering do less work on each redraw: with 50 objects, global history and a filter list, a redraw went from 3.9 ms to 1.2 ms.
- Scrubbing and Movie Mode are 2 to 3 times faster through packed history: a run of steps is checked, unpacked and applied in one call of the Rust engine. A jump over 3,000 steps of a 1M-vertex mesh went from 1.4 s to 0.5 s.
- Edit Mode step by step and topology changes read the mesh's edges and corners through its attributes, which is many times faster than the old way.
- Record everything costs less in big scenes: with 500 recorded objects an operation is recorded in under half the time, and each object takes about a third less memory. With Record set to Sculpting only the recorder does no work.
- The engine that finds what a stroke changed and applies steps is now written in Rust, for Windows, macOS and Linux. Finding the changes of a stroke at 1M vertices takes 1.2 ms instead of 5.0 ms, and a 300-step jump about half the time. If it can't load, the Python version takes over.

Fixed:

- The on-screen slider hung to the right of the viewport. The whole slider is centred now.
- Recovery after a crash (On open: Interrupted session) could bring back a history that didn't match the mesh in the file, so scrubbing showed shapes that never existed. It happened after the step cap or the memory limit dropped old steps, after going back past the last save and sculpting there, and after a join.
- Steps that can't be reached from the saved file are left out of the recovered history now. The Undo History tab says how many, and for which object.
- After opening a file that was changed without Hindsight, an interrupted session lost what was recorded next. Those steps come back now.
- Crash recovery no longer brings back steps from another save of the file (a .blend1 renamed back, a copy, a backup) or from the same file open in a second Blender.
- Crash recovery keeps one operation on several objects whole, and handles a crash in the middle of a join.
- Saving while recording is paused on an object that changed adds one step for those changes, so going back later starts from the right mesh.
- A saved history with a damaged step keeps the steps after it that can still be reached, instead of losing all of it.
- When the history can't be stored in the .blend (out of memory), it goes into the history folder next to it instead of being lost.
- Crash recovery on a full or failing disk: a record cut off part way no longer hides the steps written after it, and a save point that didn't reach the disk is reported.
- The memory limit could drop steps that an object you had scrubbed back hadn't reached yet. Going forward again then gave shapes that never existed.
- Blender 5.2 and 5.3: Remove Hindsight data from this file failed.
- Blender 5.3: switching an object to a paint mode and saving wrote a history for it, though nothing had been painted.
- Blender 5.0 and newer: Mask Extract failed, because Blender moved that operator. The Morph History brush was twice the size shown and ignored Lock size to Scene.
- Blender 5.0: scrubbing back over a colour layer could lose which layer is active.
- A damaged history block stored with LZ4 is refused cleanly now.
- After a long session a repeating error could fill the system console.
- Linked duplicates (Alt+D) share one history, as the tab says: going back on one no longer wipes strokes made on the other.
- Record everything: when only some items of a value are keyed (only X of Location), the others are recorded. Muted keys and drivers no longer stop a value from being recorded, and adding a keyframe makes no step.
- An object the global history deleted and brings back keeps its physics and particle settings: cloth, soft body, fluid, collision, particles, force field and rigid body. Dynamic Paint surfaces and baked caches still don't come back.
- An object the global history deleted and brings back keeps its Geometry Nodes inputs on Blender 5.2 and 5.3, and on every version its object inputs and other custom properties that point to something.
- Putting constraints back when one of them can't be made again no longer gives the others the wrong settings.

## 1.0.0-beta.2

This version records objects as well as sculpting. Object recording is off until you switch it on ([Install](https://min3scon.github.io/Hindsight/docs/install/#objects)). What it records and what it doesn't: [Limitations](https://min3scon.github.io/Hindsight/docs/limitations/).

New:

- Object recording: transforms, parenting, constraints, collections and visibility, and adding, deleting, duplicating and renaming objects. Set Record to Everything in the add-on preferences, then switch it on per object.
- Edit Mode step by step: each Edit Mode operation is its own step.
- Global history with one position per operation, so every object's steps sit on one timeline in the order you made them.
- Record: switches in the sidebar for the selected object and for objects added and deleted.

The big one: in beta 1 the add-on installed but most people saw nothing. Three things caused it.

- Fixed: recording stopped after you opened any file. Hindsight's recorder was dropped by Blender on every File > Open, File > New and double-click on a .blend, so no stroke was recorded and the slider never appeared. The recorder now survives file loads and puts itself back if anything removes it.
- Fixed: in Sculpt Mode on Blender 4.3 and newer the slider was drawn under the brush shelf. It now sits above the shelf and between the toolbar and the sidebar.
- Fixed: nothing showed until the first recorded stroke. In Sculpt Mode an empty slider now appears straight away, with a short hint.

New:

- The Undo History tab (press N) was rebuilt from standard Blender widgets. It has a real slider for the current step, back and forward buttons, a status line before the first stroke, and the options in collapsed panels.
- A header button with the same controls, next to the mode menus.
- F3 finds Step Back, Step Forward, Open History Menu, Run Self-check and Copy Diagnostics.
- Run self-check tests the add-on inside your session, including a record and jump round trip on a throwaway mesh. Copy diagnostics puts versions, settings and recent log lines on the clipboard for a bug report, with your paths removed.
- A one-line message in the status bar the first time Hindsight is switched on.
- History files now have a format version and a checksum per step. Damaged steps are never applied: the steps before them still load and the tab says what was lost. A file saved by a newer Hindsight is left untouched. Beta 1 files load as they are.
- History takes less memory and disk space: a typical stroke is stored 16 to 47% smaller, depending on the mesh, and still comes back exactly. Jumping through older steps is faster too.
- Beta 1 can open files saved by beta 2 but can't play back the steps recorded in beta 2. Don't go back to beta 1 with those files.
- A warning when the memory limit starts dropping old steps, and one for meshes over 1.5 million vertices.

Also fixed:

- Quick strokes, less than a fifth of a second apart, were merged into one step. Each stroke is now its own step.
- On a slow modifier stack one stroke could be split into several steps.
- Using Blender's own undo after a Hindsight jump in Sculpt Mode replayed old stroke data on top of the mesh and could drop later steps. Hindsight now reverses that and tells you to use Ctrl+Z or the slider.
- Filling a mask and then clearing it was taken for an undo and the clear was never recorded.
- A file saved in Edit Mode got an empty step as soon as you left Edit Mode.
- With Multires or Dyntopo on, the tab and the slider still invited a stroke. They now say recording is paused and why.
- Removed a note about moving objects that appeared on almost every object: moving an object doesn't change its sculpt history.
- A part of the add-on that fails now switches off on its own and says so, instead of printing the same error on every redraw. A module that fails to start no longer takes the whole add-on down with it.
- Ctrl+Z at the start of the history no longer falls through to Blender's undo once Hindsight has moved the mesh.
- The Morph History brush showed an error on every stroke in Blender 5.0 and newer, and its radius and strength settings were missing from the tool header.

Changed:

- History Menu and button labels are shorter and plainer, for example "Clear history" and "Pause recording".

Tested on Blender 5.0.1, 5.1.1 and 5.1.2 on Windows, in the real interface. See the docs for what was measured.

## 1.0.0-beta.1 (2026-10-14)

First public beta.

- Each object gets its own history of up to 10,000 steps, with a slider along the bottom of the viewport.
- History Menu, Morph History brush, Movie Mode, global history, Collection Filtering.
- Joining objects (Ctrl+J) merges their histories, and going back past the join brings the original objects back.
- History saved next to the .blend, inside it, or both, with crash recovery.
- Tested with Blender's Python module only, not in the real interface.

## 1.0.0 (2026-10-14)

Not released publicly. The first complete build, tested with Blender's Python module.
