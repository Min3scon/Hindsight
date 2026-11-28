# Baseline of the Rust moves (#129, 24.1)

The "before" of the whole rewrite (#24, `dev/RUST_PLAN.md`): what the code measured before any move, and the A of
the one `bench/ab.py` that 24.13 runs against the last move (owner point 4). Every number below is copied by a
script from the result files next to this one; none is typed by hand.

**Commit measured:** `8dbb008` on `v2/24-rust`. Its `hindsight/` is `v2/integration`'s 4bf282f (#143, all 32 V2 PRs
merged) but for two comments that name the renamed references. 24.13: `bench/ab.py --a <a checkout of 8dbb008>`.

**Conditions:** Windows 10 Pro 22H2 (build 19045), 12 logical cores, the owner's PC with other programs open (not a
quiet machine); Blender 5.3.0 as a Python module (`bpy` 5.3, Python 3.13.4); engine 0.14.0 (API 13), the tree's own
`win_amd64` wheel, unpacked to `%TEMP%\hs_bench_native` by `bench/suite.py` (`machine.rust_engine_loaded` true);
2026-11-27. Sizes are the suite's (`bench/README.md`).

| File | What |
|---|---|
| `8dbb008.json` | one full `bench/suite.py` run (07:28) |
| `ab_8dbb008_vs_8dbb008-again.json`, `.md` | `bench/ab.py` of 8dbb008 against itself, 5 rounds in alternating processes (07:31, 30 min): the noise |
| `held_once_mem_f5_8dbb008.json` | `bench/held_once_mem_f5.py` of 8dbb008 against itself, 5 rounds: 100,000 vertices, 6,000 steps, a 54.8 MB file |

**Noise.** The same code against itself, alternating: the ratio of the two sides' best times runs from 0.92 to 1.06,
and the per-round medians of one side spread from 1% to 84% ((max - min) / min). A move's ratio inside that band
is noise; quote each ratio with the spread `ab.py` prints.

**Not measured on this PC:** `hwm_mb` is 0 in every scenario (no `/proc` on Windows): native memory comes from
`held_once_mem_f5.py`, which polls the working set and private bytes from outside. The idle group is the wall time of
its loops (`perf_counter`; Windows' `process_time` moves in 15.6 ms ticks). The four `*_folder_*` scenarios are not
available: format 5 has no history folder (#120).

## Times (ms)

| Scenario | suite min | suite median | A/A min (A / A again) | A/A ratio of mins | spread (A / A again) |
|---|---|---|---|---|---|
| `commit_100k` | 0.6423 | 0.7022 | 0.6354 / 0.6427 | 1.011 | 38% / 29% |
| `commit_1m` | 4.286 | 5.037 | 3.983 / 3.941 | 0.9893 | 38% / 33% |
| `goto_bl_1_100k` | 0.2053 | 0.2325 | 0.2059 / 0.2024 | 0.983 | 35% / 38% |
| `goto_bl_300_100k` | 5.333 | 5.507 | 5.309 / 5.315 | 1.001 | 10% / 13% |
| `jump_1_fwd_100k` | 0.0259 | 0.0262 | 0.0259 / 0.0258 | 0.9961 | 3% / 4% |
| `jump_1_back_100k` | 0.0259 | 0.0261 | 0.026 / 0.0258 | 0.9923 | 4% / 44% |
| `jump_30_fwd_100k` | 0.5757 | 0.5795 | 0.5758 / 0.5743 | 0.9974 | 32% / 82% |
| `jump_30_back_100k` | 0.5763 | 0.5917 | 0.5753 / 0.5742 | 0.9981 | 20% / 80% |
| `jump_300_fwd_100k` | 5.843 | 5.967 | 5.867 / 5.816 | 0.9911 | 20% / 14% |
| `jump_300_back_100k` | 5.878 | 6.066 | 5.851 / 5.857 | 1.001 | 34% / 14% |
| `jump_3000_fwd_100k` | 57.17 | 57.38 | 57.11 / 56.96 | 0.9974 | 26% / 19% |
| `jump_3000_back_100k` | 56.98 | 57.48 | 57.16 / 57.04 | 0.9979 | 18% / 12% |
| `drag_5000_fwd_100k` | 96.1 | 97.32 | 96.09 / 96.32 | 1.002 | 27% / 19% |
| `drag_5000_back_100k` | 96.65 | 97.13 | 96.39 / 96.9 | 1.005 | 17% / 17% |
| `jump_1_fwd_1m` | 0.1312 | 0.1317 | 0.1306 / 0.1311 | 1.004 | 4% / 1% |
| `jump_1_back_1m` | 0.1308 | 0.1316 | 0.1307 / 0.1273 | 0.974 | 4% / 9% |
| `jump_30_fwd_1m` | 4.574 | 4.835 | 4.492 / 4.487 | 0.9989 | 12% / 9% |
| `jump_30_back_1m` | 4.467 | 4.573 | 4.492 / 4.493 | 1 | 13% / 11% |
| `jump_300_fwd_1m` | 30.51 | 30.93 | 30.19 / 30.49 | 1.01 | 32% / 24% |
| `jump_300_back_1m` | 30.48 | 30.88 | 30.35 / 30.62 | 1.009 | 23% / 25% |
| `jump_3000_fwd_1m` | 300.2 | 303.2 | 300 / 303.2 | 1.011 | 29% / 32% |
| `jump_3000_back_1m` | 302 | 303.7 | 300.7 / 301.2 | 1.001 | 40% / 23% |
| `drag_5000_fwd_1m` | 780.2 | 780.7 | 773.4 / 775.5 | 1.003 | 17% / 14% |
| `drag_5000_back_1m` | 778 | 781.6 | 773.4 / 778.7 | 1.007 | 14% / 11% |
| `save_embed_100k_1000` | 908.2 | 930.7 | 828.6 / 824.7 | 0.9953 | 12% / 13% |
| `open_embed_100k_1000` | 2969 | 2979 | 2847 / 2852 | 1.002 | 12% / 33% |
| `save_embed_100k_6000` | 952.1 | 956.8 | 944 / 934.9 | 0.9904 | 18% / 11% |
| `open_embed_100k_6000` | 3079 | 3129 | 3098 / 3083 | 0.9949 | 34% / 16% |
| `save_folder_100k_1000` | not available | | | | |
| `open_folder_100k_1000` | not available | | | | |
| `save_folder_100k_6000` | not available | | | | |
| `open_folder_100k_6000` | not available | | | | |
| `edit_step_100k` | 40.98 | 42.07 | 40.72 / 39.87 | 0.9793 | 11% / 17% |
| `edit_step_300k` | 112.7 | 114.6 | 113.9 / 113.2 | 0.9931 | 16% / 26% |
| `dr_tick_idle_500` | 0.0053 | 0.0054 | 0.0053 / 0.0053 | 1 | 17% / 19% |
| `dr_finish_nochange_500` | 57.33 | 58.26 | 56.69 / 57 | 1.005 | 17% / 29% |
| `dr_on_update_500` | 0.1796 | 0.22 | 0.1747 / 0.1845 | 1.056 | 15% / 12% |
| `dr_finish_move_500` | 61.29 | 62.17 | 59.95 / 60.72 | 1.013 | 14% / 10% |
| `dr_prime_500` | 187.8 | 190.8 | 185 / 185.1 | 1 | 13% / 10% |
| `redraw_viewport_1obj_object` | 0.0492 | 0.0498 | 0.0483 / 0.0494 | 1.023 | 3% / 3% |
| `redraw_panel_1obj_object` | 0.1026 | 0.1037 | 0.1042 / 0.1026 | 0.9846 | 10% / 5% |
| `redraw_viewport_1obj_global` | 0.0617 | 0.0623 | 0.0611 / 0.0608 | 0.9951 | 11% / 4% |
| `redraw_panel_1obj_global` | 0.1169 | 0.1183 | 0.1151 / 0.1144 | 0.9939 | 8% / 9% |
| `redraw_viewport_50obj_object` | 0.0492 | 0.0496 | 0.0492 / 0.0494 | 1.004 | 7% / 12% |
| `redraw_panel_50obj_object` | 0.1038 | 0.1052 | 0.1043 / 0.1041 | 0.9981 | 84% / 9% |
| `redraw_viewport_50obj_global` | 0.0906 | 0.0925 | 0.0898 / 0.0903 | 1.006 | 15% / 18% |
| `redraw_panel_50obj_global` | 0.1466 | 0.1491 | 0.1472 / 0.1458 | 0.9905 | 24% / 13% |
| `redraw_viewport_50obj_object_filtered` | 0.0695 | 0.0703 | 0.069 / 0.069 | 1 | 11% / 11% |
| `redraw_panel_50obj_object_filtered` | 0.1319 | 0.1334 | 0.1335 / 0.1311 | 0.982 | 9% / 24% |
| `redraw_viewport_50obj_global_filtered` | 0.0978 | 0.0994 | 0.0985 / 0.0988 | 1.003 | 18% / 5% |
| `redraw_panel_50obj_global_filtered` | 0.165 | 0.1658 | 0.1695 / 0.1646 | 0.9711 | 25% / 42% |
| `drag_5000` | 0.1316 | 0.1348 | 0.1281 / 0.1294 | 1.01 | 12% / 10% |
| `movie_addon_ms_per_frame_100k` | 0.4932 | 0.4958 | 0.487 / 0.4895 | 1.005 | 11% / 26% |
| `movie_frame_set_ms_per_frame_100k` | 4.331 | 4.341 | 4.28 / 4.27 | 0.9976 | 21% / 44% |
| `addon_import` | 200.8 | 201.7 | 201.4 / 200.7 | 0.9966 | 16% / 17% |
| `addon_register_cold` | 67.43 | 69.37 | 67.01 / 67.55 | 1.008 | 16% / 9% |
| `addon_unregister_cold` | 0.5295 | 0.5383 | 0.5324 / 0.4917 | 0.9236 | 23% / 24% |
| `addon_register_warm` | 2.092 | 2.207 | 2.131 / 2.13 | 0.9995 | 17% / 16% |
| `addon_unregister_warm` | 0.3922 | 0.4148 | 0.4116 / 0.3815 | 0.9269 | 25% / 31% |
| `idle_60s_1obj` | 12.78 | 12.84 | 12.7 / 12.71 | 1.001 | 19% / 27% |
| `depsgraph_handler_1obj` | 7.412 | 7.548 | 7.438 / 7.362 | 0.9897 | 48% / 16% |
| `idle_60s_50obj` | 13.01 | 13.12 | 13.04 / 12.97 | 0.9943 | 26% / 9% |
| `depsgraph_handler_50obj` | 7.444 | 7.535 | 7.346 / 7.363 | 1.002 | 11% / 4% |

## Held memory, format 5 (MB, median of 5 runs, min..max)

| | working set A / A again | private bytes A / A again |
|---|---|---|
| before open | 185.9 (185.1..186.3) / 185.5 (185.0..186.3) | 519.6 (518.8..520.1) / 519.3 (518.6..519.9) |
| after open | 270.8 (270.4..271.2) / 270.8 (270.7..271.6) | 604.7 (604.3..605.2) / 604.7 (604.6..605.5) |
| save peak | 348.3 (347.2..349.6) / 347.6 (347.3..349.9) | 692.7 (692.0..694.7) / 693.9 (691.5..695.0) |
| after save | 266.4 (265.8..268.6) / 268.8 (267.2..269.6) | 599.5 (598.8..601.3) / 602.0 (600.3..602.4) |

Machine CPU busy during each run (%): A [45.1, 40.6, 41.2, 40.4, 40.0], A again [39.0, 38.4, 40.4, 41.6, 39.6].
