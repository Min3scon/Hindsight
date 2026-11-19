import os, sys, time, statistics, random
ROOT = sys.argv[1]; sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
from hindsight.core import globaltl
from hindsight.core.history import History
from hindsight.core.registry import Registry
from hindsight.core.types import Entry
import test_core_global_position as T
for nh, per in ((20, 500), (20, 5000)):
    reg = Registry(); rng = random.Random(1); seq = 1
    hs = [History(f"h{k}", [f"o{k}"]) for k in range(nh)]
    for r in range(per):
        for h in hs:
            extra = {"op": seq - 1} if rng.random() < 0.05 and seq > 1 else {}
            h.entries.append(Entry(blob=b"x", meta={"seq": seq, "label": "Draw", "mode": "SCULPT", "target": h.members[0],
                                                   "time": 1.0 * seq, "kind": "DELTA", "extra": extra}))
            seq += 1
    for h in hs:
        h.cursor = len(h.entries); reg.histories[h.uid] = h
    res = {"before": [], "after": []}
    for r in range(9):
        for name, fn in ((("before", T.groups_before_r073), ("after", globaltl.global_groups)) if r % 2 else (("after", globaltl.global_groups), ("before", T.groups_before_r073))):
            t = time.perf_counter(); g = fn(reg); res[name].append((time.perf_counter() - t) * 1000)
    assert T.groups_before_r073(reg) == globaltl.global_groups(reg)
    b, a = statistics.median(res["before"]), statistics.median(res["after"])
    print(f"{nh * per} steps: before med {b:.1f} ms (min {min(res['before']):.1f}), after med {a:.1f} ms (min {min(res['after']):.1f}), ratio {a / b:.2f}")
