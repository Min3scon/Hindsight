# Hindsight

A Blender add-on that gives every sculpted object its own history. Drag a slider to go back through up to 10,000 steps, look, and drag forward again. The history is saved with your file.

Version 1.0.0-beta.2. It records sculpting, and it can record objects too (opt-in), with Edit Mode step by step and a global timeline for every object.

Beta. See what it records and what it doesn't: [Limitations](https://min3scon.github.io/Hindsight/docs/limitations/). Object recording is off until you switch it on: [Install](https://min3scon.github.io/Hindsight/docs/install/#objects).

Object recording passed most of its release test gate on Blender 5.0.1, 5.1.1 and 5.1.2 on Windows. The long soak runs and the fixes from the latest review aren't finished. It hasn't been tried on macOS or Linux. Back up your files.

Website and in-browser demo: https://min3scon.github.io/Hindsight/

## Install

1. Download [hindsight.zip](https://min3scon.github.io/Hindsight/download/hindsight.zip). Keep it zipped.
2. In Blender (4.2 or newer), open Edit > Preferences > Get Extensions, click the arrow at the top right and choose Install from Disk.
3. Pick the zip. Press N in the 3D Viewport and open the Undo History tab.

## What it does

- Each stroke in Sculpt or Vertex Paint Mode is one step. A step stores only the vertices that stroke moved, so a stroke on a 100k mesh is about 12 KB.
- The slider along the bottom of the viewport, or the one in the Undo History tab, jumps to any step. Jumping is exact, bit for bit.
- Object recording is off until you switch it on. It records transforms, parenting, constraints, collections and visibility, and adding, deleting, duplicating and renaming objects. Edit Mode operations are recorded step by step.
- Global history puts every object's steps on one timeline in the order you made them. One operation is one position.
- Set a step as the morph target, then paint it back onto part of the mesh with the Morph History brush.
- Movie Mode plays the history on the timeline so you can render a timelapse.
- Collection Filtering picks which collections are recorded.
- The history is saved next to the .blend, inside it, or both.

To switch on object recording:

1. Open Edit > Preferences > Add-ons, find Hindsight and open its preferences. Set Record to Everything. The default is Sculpting only.
2. Select an object, press N in the 3D Viewport, open the Undo History tab and under Record: click Everything on this object.
3. To also record objects being added, deleted or duplicated, click Objects added and deleted under Record:. It applies to the whole scene.

The [docs](https://min3scon.github.io/Hindsight/docs/) cover every feature, the shortcuts, troubleshooting and known issues.

## Reporting a bug

Open the Undo History tab, open Help, click Copy diagnostics and paste the text into a [bug report](https://github.com/Min3scon/Hindsight/issues/new/choose). It has your Blender and Hindsight versions, settings and recent log lines, with your file paths removed.

## Development

- `hindsight/core/`: the history engine (deltas, storage, file format), plain Python and numpy.
- `hindsight/bl/`: the Blender side (recorder, slider, panels, operators, saving).
- `tests/`: headless tests. `python tests/run_tests.py --blender all` runs them inside every Blender found.
- `tests_ui/`: tests in the real Blender interface, with a window, in a throwaway profile. `python tests_ui/run_all.py` runs the regression scenarios and `python tests_ui/rounds.py --pass 1` runs a full test pass.
- `site/`: the website source. `python site/build.py` builds it into `docs/`, and `node tests/site/site_qa.js` checks it in Chromium, Firefox and WebKit.
- `tools/check_copy.py` checks every public piece of text against [STYLE.md](STYLE.md).

Build the zip with `python scripts/build_zip.py`.

## Licence

GPL 3.0 or later. See [LICENSE](LICENSE).
