# Hindsight (public beta)

> **Beta — version 1.0.0-beta.1.** Hindsight works, but it's new: so far it has been tested headlessly
> (125 automated tests on Blender 4.2 and 5.2), not yet widely in the real Blender interface.
> Back up your .blend files and keep saving versions of important work.
> Found a bug? [Report it on GitHub](https://github.com/Min3scon/Hindsight/issues/new/choose) with your
> Blender version, OS and the steps to reproduce. What's tested and what still needs testing:
> [Beta status](https://min3scon.github.io/Hindsight/#beta).

Drag a slider along the bottom of the 3D Viewport and travel back through every stroke of your sculpt.

## What it is

Hindsight is a free Blender extension for Blender 4.2 and newer, released under GPL-3.0-or-later. It gives each object its own scrubbable sculpt history of up to 10,000 steps. Every sculpt stroke becomes one step, and you can move backward and forward through them without losing the steps you have not reached yet. Blender's own undo keeps working alongside it.

- **Scrubber:** a slider along the bottom of the 3D Viewport with a step counter. The dot at its left end opens the History Menu.
- **Morph History brush:** mark a step as a morph target, then paint areas back toward the shape they had at that step. Areas you leave alone stay as they are now.
- **Movie Mode:** plays the history back on the Timeline as a timelapse of the sculpt.
- **Per-object and global history:** each object has its own timeline by default. You can switch to one shared timeline for the whole scene, and Preserve Modes controls how modes are handled while you step through it.
- **Join with unmerge:** Ctrl+J merges the histories of joined objects. Scrubbing back past the join restores the original objects, each with its own history.
- **Separate and duplicate handling:** Separate (P) asks first and gives the new object its own history. Shift+D starts an independent history for the duplicate. Alt+D warns that linked duplicates share mesh data.
- **Collection Filtering:** choose which collections are tracked, so history is only kept where you want it.
- **Sidecar, Embed or Both saving:** keep history in a folder beside the .blend, inside the .blend, or in both places.
- **Crash recovery:** after a crash, restore the history as it was last saved, or from the interrupted session.

## Install

1. Download the beta `hindsight.zip` from https://min3scon.github.io/Hindsight/download/hindsight.zip, or from the [1.0.0-beta.1 release](https://github.com/Min3scon/Hindsight/releases/tag/v1.0.0-beta.1) ([all releases](https://github.com/Min3scon/Hindsight/releases)).
2. In Blender, go to **Edit > Preferences > Get Extensions > Install from Disk** and pick the zip.
3. In the 3D Viewport, press **N** and open the **Undo History** tab.

## Quick start

1. Select a mesh.
2. Enter Sculpt Mode and sculpt.
3. The scrubber appears after the first stroke.
4. Drag the scrubber, or use Ctrl+Z and Ctrl+Shift+Z to step one stroke at a time.

## Shortcuts

| Action | Shortcut | Notes |
|---|---|---|
| Step back | Ctrl+Z (Cmd+Z on macOS) | Sculpt and Vertex Paint modes. Falls back to Blender's undo when there is no Hindsight step left to undo. |
| Step forward | Ctrl+Shift+Z (Cmd+Shift+Z on macOS) | Sculpt and Vertex Paint modes |
| Switch objects, keeping each history | Alt+Q | Sculpt Mode |
| Join with history merge | Ctrl+J | Asks first |
| Separate | P | Edit Mode. Asks first. |
| Duplicate with independent history | Shift+D | The duplicate starts its own empty history |
| Linked duplicate | Alt+D | Shares mesh data, so a warning is shown |
| Open the sidebar | N | Opens the Undo History tab |

In other modes, Blender's native undo is unchanged.

## History Menu

The menu opens from the dot at the left end of the scrubber.

- **Use Global / Active Object Undo History:** switches between one shared timeline for the scene and a separate timeline per object.
- **Preserve Modes:** when on, stepping to a change makes the object that owns it active and puts it back into the mode it was recorded in.
- **Set Morph Target Here:** marks the current step as the target for the Morph History brush.
- **Morph History Brush:** activates the Morph History brush.
- **Delete Past Undo Steps:** frees the memory used by steps before the current position. Later steps are kept.
- **Reset Undo History:** clears the object's history. Its current shape becomes the new starting point.
- **Turn Off Undo History:** pauses recording and keeps the existing steps.
- **Reset and Turn Off:** clears the history and turns recording off. Asks first.
- **Turn On Undo History:** resumes recording. Changes made while it was off are kept as one step.

## Add-on Preferences

Set these in the Hindsight add-on preferences.

| Setting | What it does | Default |
|---|---|---|
| Save Mode | Sidecar (folder beside the .blend), Embed (inside the .blend), or Both | Sidecar |
| Crash Recovery | Load the history as last saved, or from the interrupted session | Last Saved |
| Truncation Warning Threshold | Ask for confirmation before a new step discards this many or more future steps | 10 |
| Max Undo Steps | Steps kept per object, up to 10,000 | 10,000 |
| Memory Limit (MB) | Total size of all histories. The oldest steps are dropped first. | 4096 |
| Uncompressed Recent Steps | Most recent steps per object kept uncompressed, for instant scrubbing | 64 |
| Compression | Codec for older steps: Auto, Zstandard, LZ4, Zlib or None | Auto |
| Scrubber appearance | Height, bottom margin, width, bar and track colours, and whether the step counter is shown | Built-in look |

## How it stays fast

- **Only what changed.** A brush stroke touches a small patch of the mesh, so only the vertices that moved are stored.
- **One record, both ways.** Each change is stored as the XOR of the old and new float bits. The same record turns the old shape into the new one and back again.
- **Compressed.** The difference records are mostly zeros, so they compress well with zstd, or with zlib where zstd is not available.
- **Full copies are rare.** A complete copy of the mesh is kept only when the topology changes, such as a Voxel Remesh or a Trim. Ordinary strokes never need one.
- **Scrubbing is one write.** A drag across many steps folds them into one buffer, and the mesh is written once.

For measured results, see [bench/RESULTS.md](bench/RESULTS.md).

## Known limitations

- Dyntopo and Multires are not supported. A warning appears in the viewport if either is in use.
- Object transforms are not recorded, so older steps may play back in the wrong place.
- Modifiers, shape keys, materials, vertex groups and weight paint are not stored.
- Deform modifiers such as Armature and Displace can give unpredictable results.
- Animated objects may play back differently.
- Morph History does not work with shape keys, Dyntopo or Multires.
- Movie Mode only works with global history.
- Blender's Global Undo must be enabled (Preferences > System).

## Development

Layout:

- `hindsight/core/`: the history logic (deltas, store, journal, persistence), written in pure numpy.
- `hindsight/bl/`: the Blender layer (runtime, operators, scrubber, History Menu, sidebar, preferences).

Tests run on Blender's `bpy` module:

```
python3 tests/run_tests.py
```

Install `bpy` with `pip install bpy==4.2.*` on Python 3.11, or set `HINDSIGHT_PY` to an interpreter that can import `bpy`.

Build the extension zip:

```
python3 scripts/build_zip.py
```

This writes `dist/hindsight.zip` and `docs/download/hindsight.zip`.

Documentation in the repository:

- `INTERFACES.md`: module interfaces.
- `PARITY.md`: checklist of each behaviour, with where it is implemented and how it is tested.
- `DECISIONS.md`: design decisions, behaviour to verify, and the manual UI checks.
- `RESEARCH.md`: background research.
- `HANDOFF.md`: handoff notes.

Some behaviour can only be checked in the Blender UI: scrubber drawing and dragging, the History Menu, the Morph History tool, the truncation dialog, and Ctrl+Z in Sculpt Mode. The list is under "Manual UI checks" in `DECISIONS.md`.

## Website

https://min3scon.github.io/Hindsight/

## License

Hindsight is licensed under the GNU General Public License v3.0 or later. See [LICENSE](LICENSE).

Hindsight is an independent project inspired by ZBrush's History Recall Brush. It is not affiliated with any other product.
