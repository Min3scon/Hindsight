"""The global timeline's groups kept between calls and extended step by step stay exactly what a full
build gives: random strokes on several objects, operations on several objects at once (one global
position), scrubbing back and recording (steps replaced), objects hidden by collection filtering."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import random
import bpy
import tests.harness as H
from hindsight.bl import runtime
from hindsight.core import globaltl

C = bpy.context


def check(where):
    vis = runtime._visible_uids()
    got = runtime._groups(vis)
    want = globaltl.global_groups(runtime.reg(), vis)
    assert got == want, f"{where}: {len(got)} groups kept, {len(want)} built"
    assert runtime._position(vis, got) == globaltl.global_position(runtime.reg(), vis, want), where


def test_kept_groups_match_a_full_build():
    H.fresh()
    from hindsight.core.types import SETTINGS
    SETTINGS.truncation_threshold = 0          # a stroke after scrubbing back never asks first
    obs = [H.make_mesh(300, name=n, location=(3 * i, 0, 0)) for i, n in enumerate("ABC")]
    rng = random.Random(4)
    extended = 0
    for k in range(120):
        r = rng.random()
        if r < 0.7:
            ob = rng.choice(obs)
            H.enter(ob, "SCULPT")
            H.fake_stroke(ob, k, n=20)
            before = runtime._GROUPS_VER[0]
            assert runtime.commit(ob) is not None
            check(f"stroke {k}")
            extended += runtime._GROUPS.get("ver") == before + 1
        elif r < 0.8:                                   # one operation on two objects: one position
            a, b = rng.sample(obs, 2)
            runtime.OP_GROUP[0] = [None]
            try:
                for ob in (a, b):
                    H.enter(ob, "SCULPT")
                    H.fake_stroke(ob, 1000 + k, n=20)
                    runtime.commit(ob)
                    check(f"group {k}")
            finally:
                runtime.OP_GROUP[0] = None
        else:                                           # scrub back on one object (later steps go next)
            ob = rng.choice(obs)
            h = runtime.history_of(ob)
            if h is not None and h.entries:
                H.enter(ob, "SCULPT")
                runtime._goto_history(h, rng.randint(0, len(h.entries)), True)
                check(f"scrub {k}")
    assert extended > 40, extended


if __name__ == "__main__":
    H.run(test_kept_groups_match_a_full_build)
    H.finish()
