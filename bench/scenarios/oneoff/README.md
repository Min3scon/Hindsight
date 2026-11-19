One-off profiling scripts left by earlier agents (draw, data recorder, jumps, save and load stages,
commit profile). They are NOT part of `bench/suite.py`; each was written against an older layout
and may need small path edits. The suite's scenarios in the parent folder cover the same ground.

`embed_ab.py CODE_ROOT` (R061): Save mode Embed at 100k vertices and 6,000 steps; prints one `AB {...}` line
with the median save and open times, the .blend size and the memory peak while saving. Run it with
`<bpy python> -I bench/scenarios/oneoff/embed_ab.py CODE_ROOT`, alternating the two roots under test.
