# Parity checklist

One row per behaviour documented for the reference add-on (quick start guide, product page/FAQ text
from the documentation site's public source). "Where" = Hindsight implementation, "Test" = automated
headless test (`python tests/run_tests.py`), or **MANUAL** = needs a check in the Blender UI (see
DECISIONS.md "Manual UI checks"). Status: [x] implemented + tested, [~] implemented, manual check only,
[ ] not done (see DECISIONS.md).

## Activation and recording
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 1 | History starts on an object's first sculpt stroke | bl/runtime.py `commit` (creates History) | test_bl_recorder::test_first_stroke_starts_history | [x] |
| 2 | Painting first in Vertex Paint mode also starts history | runtime `RECORD_MODES` | test_bl_recorder::test_vertex_paint_starts_history | [x] |
| 3 | Opening a file records nothing | persistence load_post (no commit) | test_bl_persistence (history length after load) | [x] |
| 4 | Switching modes records nothing | runtime `_enter` / `check_mode_transition` | test_bl_recorder::test_first_stroke_starts_history, test_edit_mode_grouping | [x] |
| 5 | Each stroke is one step | runtime `tick` debounce + `commit` | test_bl_recorder (all); stroke-end timing **MANUAL** | [~] |
| 6 | Records sculpt strokes (all brushes incl. Cloth) | position channel | test_bl_recorder::test_all_channels_scrub_exact | [x] |
| 7 | Records masks | `.sculpt_mask` channel | test_all_channels_scrub_exact | [x] |
| 8 | Records Face Sets | `.sculpt_face_set` channel | test_all_channels_scrub_exact | [x] |
| 9 | Records visibility (hide/show) | `.hide_vert/.hide_edge/.hide_poly` | test_all_channels_scrub_exact | [x] |
| 10 | Records vertex colours | colour attribute channels (float + byte) | test_all_channels_scrub_exact, test_vertex_paint_starts_history | [x] |
| 11 | Records filters | any change diffed after the operator | covered by channel tests; real filter ops **MANUAL** | [~] |
| 12 | Topology tools (Voxel Remesh, Trim) recorded as full snapshot | TOPO step (`meshio.read_snapshot`) | test_bl_recorder::test_topology_snapshot | [x] |
| 13 | F9 filter adjustment updates the same step | native-undo match + silent single-step replace | test_bl_recorder::test_native_undo_sync_and_f9_replace | [x] |
| 14 | Edit Mode changes grouped into one step when leaving | `check_mode_transition` EDIT exit | test_bl_recorder::test_edit_mode_grouping | [x] |
| 15 | Shade Smooth/Flat in Edit Mode recorded | `sharp_face` channel in full diff | test_edit_mode_grouping | [x] |
| 16 | Entering and leaving Edit Mode without changes records nothing | empty diff skipped | test_edit_mode_grouping | [x] |
| 17 | Only changed vertices stored; full copies only on topology change | core/delta.py XOR deltas, TOPO snapshots | test_core_store, test_core_history | [x] |
| 18 | Up to 10,000 steps per object | core/store.enforce_caps | test_core_store (10,050 -> 10,000), test_bl_stress | [x] |

## Undo behaviour
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 19 | Works alongside Blender's native undo | ops_core fallback to ed.undo; native undo detected and synced | test_native_undo_sync_and_f9_replace | [x] |
| 20 | Ctrl+Z step back (Cmd+Z on Mac) | ops_core keymaps (Sculpt, Vertex Paint; ctrl + oskey) | test_step_back_forward_ops; real key press **MANUAL** | [x] |
| 21 | Ctrl+Shift+Z step forward (Cmd+Shift+Z) | ops_core keymaps | test_step_back_forward_ops | [x] |
| 22 | Step back then sculpt starts a new branch | History.push truncates | test_branch_silent_and_confirm | [x] |
| 23 | Confirmation only when >= Truncation Warning Threshold steps would be lost | runtime.commit + confirm dialog | test_branch_silent_and_confirm (dialog itself **MANUAL**) | [x] |
| 24 | Below threshold future steps dropped silently | runtime.commit | test_branch_silent_and_confirm | [x] |
| 25 | After confirming, repeat the stroke | stroke reverted, `confirm_truncate` | test_branch_silent_and_confirm | [x] |

## Scrubber
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 26 | Scrubber at the bottom of the 3D View, appears after the first stroke | bl/scrubber.py, runtime.scrub_info | test_bl_scrubber (layout), test_first_stroke_starts_history (hidden before); drawing **MANUAL** | [~] |
| 27 | Drag along it to move back and forth | scrubber gizmo modal -> runtime.goto | test_bl_scrubber (pos mapping, throttled drag); real drag **MANUAL** | [~] |
| 28 | Step counter (e.g. 214 / 10000) | scrubber draw | test_bl_scrubber (layout); **MANUAL** | [~] |
| 29 | Dot next to the scrubber opens the History Menu | scrubber gizmo hit test "dot" | test_bl_scrubber::hit test; **MANUAL** | [~] |
| 30 | Scrubber appearance configurable (sidebar) | prefs scrub_* + ui_panel Scrubber sub-panel | test_bl_panels | [x] |

## History Menu
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 31 | Use Global / Active Object Undo History | ui_menu toggle_global -> runtime.set_global | test_bl_menu, test_per_object_and_global | [x] |
| 32 | Preserve Modes | ui_menu toggle_preserve_modes | test_bl_menu, test_per_object_and_global | [x] |
| 33 | Set Morph Target Here | ui_menu -> runtime.set_morph_target | test_bl_menu, test_bl_morph | [x] |
| 34 | Morph History Brush (activates the tool) | ui_menu morph_brush -> wm.tool_set_by_id | test_bl_menu (tool switch **MANUAL**) | [~] |
| 35 | Delete Past Undo Steps (frees memory, later steps stay) | runtime.delete_past | test_delete_past_keeps_later | [x] |
| 36 | Reset Undo History (current mesh = new start) | runtime.reset_history | test_turn_off_on_and_reset | [x] |
| 37 | Turn Off Undo History (pause, keep steps) | runtime.turn_off | test_turn_off_on_and_reset | [x] |
| 38 | Changes while off become the new start when turned back on | runtime.turn_on bridging step | test_turn_off_on_and_reset | [x] |
| 39 | Reset and Turn Off (permanent, asks first) | runtime.reset_and_turn_off | test_turn_off_on_and_reset, test_bl_menu | [x] |
| 40 | Turn On Undo History | runtime.turn_on | test_turn_off_on_and_reset | [x] |

## Object and global history
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 41 | Per-object history; Alt+Q switching keeps each history | Registry per object uid | test_per_object_and_global | [x] |
| 42 | Global history: every object's steps on one timeline in order | core/globaltl.py, runtime.global_goto | test_per_object_and_global | [x] |
| 43 | Preserve Modes on: switching objects keeps each object's mode | runtime._switch_to | test_per_object_and_global | [x] |
| 44 | Preserve Modes off: geometry plays back without switching active object/mode | runtime.global_goto | test_per_object_and_global | [x] |

## Morph History brush
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 45 | Morph History tool in the Sculpt Mode toolbar | bl/morph.py WorkSpaceTool | **MANUAL** (toolbar) | [~] |
| 46 | Paints brushed areas back toward the target; rest stays put | core/morph.py, bl/morph.apply_morph_at | test_bl_morph | see test |
| 47 | Light strokes = subtle change, several passes move closer | strength blend | test_bl_morph (strength 0.5) | see test |
| 48 | Each Morph stroke is one normal undo step | runtime.commit on release | test_bl_morph | see test |
| 49 | Not with Shape Keys, Dyntopo or Multires | morph.begin refuses | test_bl_morph (shape keys) | see test |
| 50 | After remeshing set a new target; target survives normal sculpting | core/morph.target_positions | test_bl_morph | see test |

## Movie Mode
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 51 | Only works with Global history | bl/movie.py | test_bl_movie | see test |
| 52 | Sidebar Undo History > Movie Mode, header checkbox turns it on | ui_panel Movie Mode sub-panel | test_bl_panels | [x] |
| 53 | Quick Auto Key keys first and last steps on the Timeline | movie.quick_auto_key | test_bl_movie | see test |
| 54 | Step can be keyframed manually | scene.hindsight.movie_step (animatable) | test_bl_movie | see test |
| 55 | Frames per Step controls playback speed | props frames_per_step | test_bl_movie | see test |
| 56 | Scrubber locked while on; Timeline drives playback | runtime._movie_locked, movie frame handler | test_bl_movie | see test |
| 57 | Only plays back, never changes history | suspended playback | test_bl_movie | see test |
| 58 | Turning off pauses, keyframes stay | movie handler | test_bl_movie | see test |

## Sidebar
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 59 | N panel tab "Undo History" | ui_panel VIEW3D_PT_hindsight | test_bl_panels | [x] |
| 60 | Reset history for this object or all objects | ui_panel buttons | test_bl_panels, test_bl_menu | [x] |
| 61 | Set memory limits | ui_panel Memory sub-panel (prefs) | test_bl_panels | [x] |
| 62 | Live memory and step readout | runtime.memory_stats | test_bl_panels | [x] |
| 63 | Collection Filtering: Blacklist / Whitelist | bl/filtering.py | test_bl_panels, test_excluded_objects | [x] |
| 64 | Excluded objects record nothing; history hidden, not deleted; returns when moved back | runtime.history_of / commit | test_excluded_objects | [x] |

## Saving
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 65 | Save Mode in Add-on Preferences: Sidecar / Embed / Both | prefs.save_mode, bl/persistence.py | test_bl_persistence | see test |
| 66 | Sidecar: history folder beside the .blend | core/persist.save_sidecar | test_core_journal, test_bl_persistence | see test |
| 67 | Embed: inside the .blend (one file to share) | persistence Text ID property | test_bl_persistence | see test |
| 68 | Both | persistence | test_bl_persistence | see test |
| 69 | Renamed/moved .blend without folder -> "Undo History Not Found" | persistence missing_message | test_bl_persistence | see test |
| 70 | Crash Recovery: Last Saved (default) / Interrupted Session | core/journal + persist.replay | test_core_journal, test_bl_persistence | see test |
| 71 | History persists between sessions | persistence load_post | test_bl_persistence | see test |
| 72 | Delete Past Undo Steps reduces the saved size | registry serialisation of remaining entries | test_delete_past_keeps_later (+ size) | [x] |

## Warnings and data handling
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 73 | Dyntopo and Multires not supported; warning in the viewport | bl/warnings.py (overlay + pause) | test_bl_warnings; overlay **MANUAL** | [x] |
| 74 | Object transforms not recorded (old steps may play back misplaced) | warnings (info) | test_bl_warnings | [x] |
| 75 | Modifiers, Shape Keys, Materials, Vertex Groups, Weight Paint not stored; deform modifiers unpredictable | warnings | test_bl_warnings | [x] |
| 76 | Join (Ctrl+J): asks first, merges histories | bl/joinsep.py | test_bl_join | see test |
| 77 | Unmerge: scrub back past the join restores the separate objects; forward re-merges | joinsep.join_hook | test_bl_join | see test |
| 78 | Separate (P) / Mask Extract: asks first, new object starts its own history | bl/dupsep.py | test_bl_dupsep | see test |
| 79 | Alt+D linked duplicate warns about shared mesh data; Shift+D independent history | dupsep + warnings | test_bl_dupsep, test_bl_warnings | see test |
| 80 | Animated objects may play back differently (warning) | warnings | test_bl_warnings | [x] |
| 81 | Global Undo must be enabled (warning) | warnings | test_bl_warnings | [x] |

## Install
| # | Original behaviour | Where | Test | Status |
|---|---|---|---|---|
| 82 | Blender 4.2+, Install from Disk (zip) | blender_manifest.toml, scripts/build_zip.py | test_bl_install | see test |
| 83 | Add-on Preferences: save mode, crash recovery, truncation threshold, step cap, memory limit, tier sizes, compression | bl/prefs.py | test_bl_panels | [x] |
