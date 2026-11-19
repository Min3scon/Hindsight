| scenario | dd9f35b min | dd9f35b med | head min | head med | head/dd9f35b min | head/dd9f35b med | spread dd9f35b | spread head | mem dd9f35b tm/hwm MB | mem head tm/hwm MB |
|---|---|---|---|---|---|---|---|---|---|---|
| jump_300_fwd_100k | not available on A | not available on A | not available on B | not available on B | | | | | AssertionError: start state differs | |
| jump_30_fwd_1m | 3.673 | 4.569 | 3.624 | 4.429 | 0.9865 | 0.9692 | 38% | 21% |  |  |
| jump_300_fwd_1m | 25.7 | 30.48 | 24.55 | 32.52 | 0.955 | 1.067 | 21% | 26% | 0.005 / 0 | 0.005 / 0 |
| jump_3000_fwd_1m | 256.9 | 311 | 256.4 | 328.3 | 0.9982 | 1.056 | 8% | 10% |  |  |
| jump_3000_back_1m | 259.7 | 342.9 | 277.8 | 319.1 | 1.07 | 0.9306 | 5% | 4% | 0.05 / 0.098 | 0.05 / 0 |
| drag_5000_fwd_1m | 793.5 | 848.9 | 719.5 | 797 | 0.9067 | 0.9389 | 4% | 3% |  |  |
| drag_5000_back_1m | 789.4 | 809 | 712.7 | 822.5 | 0.9029 | 1.017 | 5% | 15% |  |  |
| jump_3000_fwd_100k | not available on A | not available on A | not available on B | not available on B | | | | | no result reported | |
| jump_3000_back_100k | not available on A | not available on A | not available on B | not available on B | | | | | no result reported | |
| idle_60s_1obj | 6.091 | 6.489 | 6.56 | 8.769 | 1.077 | 1.351 | 9% | 35% |  |  |
