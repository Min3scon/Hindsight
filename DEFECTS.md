# Defects

Internal. Every defect found by a test round, with severity, status and the regression test that pins it.
Severity: S1 data loss or crash, S2 feature broken, S3 wrong or confusing behaviour, S4 cosmetic.
Exit criteria for a release: no open S1 or S2; every S3 either fixed or listed in Known issues.

| ID | Found | Sev | Defect | Status | Regression test |
|----|-------|-----|--------|--------|-----------------|
| D1 | Step 2 | S2 | Recorder handlers dropped on file load: nothing recorded after File > Open (beta 1 "no UI") | fixed | regress_file_load, test_bl_regressions |
| D2 | Step 2 | S2 | Slider drawn under the brush asset shelf in Sculpt Mode on Blender 4.3+ | fixed | regress_slider_visible |
| D3 | Step 2 | S3 | Nothing visible before the first stroke | fixed | test_bl_regressions |
| D4 | Round C | S1 | Mask fill then clear taken for a native undo; the clear was not recorded and later jumps showed the wrong mask | fixed | test_bl_regressions |
| D5 | Round C | S3 | Quick strokes (under 0.15 s apart) merged into one step | fixed | regress_stroke_split |
| D6 | Round C | S1 | Blender's own undo after a Hindsight jump replayed stale stroke data and silently dropped later steps | fixed | regress_native_undo |
| D7 | Round D | S3 | Multires and Dyntopo objects invited strokes that were never recorded | fixed | round_d |
| D8 | Round D | S4 | "Object transforms" note on almost every object | fixed | test_bl_warnings (t_transform) |
| D9 | Round E | S3 | A slow modifier stack split one stroke into several steps | fixed | regress_stroke_split |
| D10 | Round E | S3 | A file saved in Edit Mode got an "Edit Mode" step on leaving Edit Mode | fixed | test_bl_regressions |
| D11 | Site | S2 | Demo Morph and Reset buttons swallowed by the sculpt stage's pointer capture | fixed | tests/site/site_qa.js |
| D12 | Compression work (headless suite) | S1 | While adding decode-ahead for long jumps, the objects a jump moves were fixed at its start, so a join crossed backwards brought its member back without replaying the member's own earlier steps (it showed its state at the join). Caught before any commit by test_bl_join in all three Blenders | fixed: checked as each step comes up | test_core_history (test_member_brought_back_mid_jump), test_bl_join, tools/mutation_test.py (two mutants) |
| D13 | Round B features (real interface, all three Blenders) | S2 | The Morph History brush raised an error on every stroke and its tool settings didn't draw in Blender 5.x: radius and strength moved from the tool settings into Sculpt Mode's own paint settings in Blender 5.0. The headless tests set the brush up without drawing or stroking it, so they missed it | fixed: read from Sculpt Mode's settings, older place as fallback | test_bl_morph (test_brush_settings_readable, fails on the old code in all three Blenders), round_b_features |
| D14 | Tier 2 design review (test audit) | S2 | On an object with Record everything, Object Mode operations that change the mesh (Apply > Scale, Apply Modifier, Set Origin, Shade Smooth) weren't recorded. Scrubbing back past Ctrl+A > Scale put the old scale back on the already scaled mesh | open: fix written on branch tier2 (mesh compared after each operator in Object Mode, in the same step), to be ported with a regression test | |
| D15 | Tier 2 design review (test audit) | S3 | Animated or driven constraint settings were read as user changes, so changing the frame recorded steps | open: fix written on branch tier2 (animated paths skipped), to be ported with a regression test | |
| B1 | Round D | n/a | Blender 5.1 crashes if a script switches mode mid-stroke (Blender bug, reproduced without Hindsight) | guarded: no jumps while a stroke runs | round_d |

## Test changes

Tests changed for a reason other than a defect in the product. A test is never weakened; each change says why.

| Date | Test | Change | Reason |
|---|---|---|---|
| 2026-10-14 | tests/test_tools_coverage.py | checks the rule against a fixed table instead of the live COVERAGE.md; adds a test that a malformed row is refused | the test guarantee changed the statuses (supported, partial, not covered; opt-in became a separate Recording column). The old test asserted the live file's statuses, which now change through the gate. The rule itself is unchanged and still tested, plus the live file must parse |
| 2026-10-14 | tests_ui/scenarios/s_round_b_features.py | the activate helper switches the target object to the requested mode even when it kept a mode from before | the test set up Ctrl+J in Sculpt Mode (after Movie Mode playback put A in Sculpt Mode), so the join never ran; the checks are unchanged |
