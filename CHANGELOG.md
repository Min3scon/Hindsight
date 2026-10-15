# Changelog

All notable changes to Hindsight. Dates are when the release went out.

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
