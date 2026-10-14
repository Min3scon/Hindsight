"""History files from older builds load and play back bit for bit with this build.

The fixtures were written by the old builds' own code (tests/fixtures/make_legacy_fixtures.py):
beta 1 (format 1, XOR steps, zstd) and beta 2 before format 3 (format 2, XOR steps, zlib).
Every position of every history is checked against the recorded states, after loading, after
saving again with this build, after re-packing every step in the background form, and with new
steps (format 3) recorded on top of the old ones.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import random
import traceback

import numpy as np

from hindsight.core import delta, persist, step as stepmod
from hindsight.core.history import MeshState
from hindsight.core.types import ADD, DELTA, Entry, Step

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
FILES = {"beta1_format1.hsr": 1, "beta2wip_format2.hsr": 2}
EXP = np.load(os.path.join(FIX, "legacy_expected.npz"))
stepmod.VERIFY = True


def expected(hist, pos):
    pre = f"{hist}|{pos}|"
    return {k[len(pre):]: EXP[k] for k in EXP.files if k.startswith(pre)}


def counts_of(arrs):
    return {"POINT": arrs["position|POINT|FLOAT_VECTOR"].size // 3, "FACE": arrs[".sculpt_face_set|FACE|INT"].size,
            "EDGE": arrs[".edge_verts|EDGE|INT32_2D"].size // 2, "CORNER": arrs[".corner_vert|CORNER|INT"].size}


def same(ms, want):
    if set(ms.arrays) != set(want):
        return False
    return all(ms.arrays[k].dtype == want[k].dtype and ms.arrays[k].tobytes() == want[k].tobytes() for k in want)


def check_all_positions(reg, label, extra=None):
    """Start from the state at the end of each history and visit every position (random order)."""
    rnd = random.Random(7)
    for uid, h in reg.histories.items():
        n = len(h.entries)
        end = (extra or {}).get(uid) or expected(uid, n)
        ms = MeshState(counts_of(end), {k: v.copy() for k, v in end.items()})
        h.cursor = n
        order = list(range(n + 1))
        rnd.shuffle(order)
        obj = h.members[0]                       # steps are keyed by the object, not the history
        for pos in order + [n]:
            h.goto(pos, {obj: ms})
            want = (extra or {}).get(uid) if pos == n and extra else expected(uid, pos)
            assert same(ms, want), (label, uid, pos)


def load(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return persist.registry_from_bytes(f.read())


def test_loads_and_plays_back():
    for name, fmt in FILES.items():
        reg = load(name)
        assert reg.load_report["format"] == fmt and reg.load_report["migrated"], reg.load_report
        assert not reg.load_report["damaged"]
        for h in reg.histories.values():
            for e in h.entries:
                assert stepmod.version_of(e.blob) == 1          # read as they are
        check_all_positions(reg, name)
    print("PASS test_loads_and_plays_back")


def test_saved_again_by_this_build():
    for name in FILES:
        reg = load(name)
        blob = persist.registry_to_bytes(reg)
        reg2 = persist.registry_from_bytes(blob)
        assert reg2.load_report["format"] == persist.FORMAT_VERSION and not reg2.load_report["migrated"]
        check_all_positions(reg2, name + " resaved")
    print("PASS test_saved_again_by_this_build")


def test_repacked_in_the_background_form():
    for name in FILES:
        reg = load(name)
        before = sum(len(e.blob) for h in reg.histories.values() for e in h.entries)
        for h in reg.histories.values():
            for e in h.entries:
                new = stepmod.recompress(e.blob)
                if new is not None:
                    assert stepmod.version_of(new) == 3
                    assert stepmod.same_step(stepmod.step_from_bytes(e.blob), stepmod.step_from_bytes(new))
                    e.blob = new
        after = sum(len(e.blob) for h in reg.histories.values() for e in h.entries)
        assert after < before, (before, after)
        check_all_positions(reg, name + " repacked")
        # and once more through a save
        check_all_positions(persist.registry_from_bytes(persist.registry_to_bytes(reg)), name + " repacked+saved")
    print("PASS test_repacked_in_the_background_form")


def test_new_steps_on_top_of_old_ones():
    for name in FILES:
        reg = load(name)
        tops = {}
        for uid, h in reg.histories.items():
            n = len(h.entries)
            end = expected(uid, n)
            new = {k: v.copy() for k, v in end.items()}
            p = new["position|POINT|FLOAT_VECTOR"]
            p[::5] = np.nextafter(p[::5], np.float32(np.inf))       # one ulp up on every 5th value
            d = delta.diff("position|POINT|FLOAT_VECTOR", end["position|POINT|FLOAT_VECTOR"], p)
            assert d.op == ADD
            h.entries.append(Entry(Step(10_000 + n, "New stroke", "SCULPT", h.members[0], 1.0, DELTA, [d])))
            h.entries[-1].pack()                                     # format 3, packed
            tops[uid] = new
        check_all_positions(reg, name + " plus new", tops)
        reg2 = persist.registry_from_bytes(persist.registry_to_bytes(reg))
        kinds = {stepmod.version_of(e.blob) for h in reg2.histories.values() for e in h.entries}
        assert kinds == {1, 3}, kinds
        check_all_positions(reg2, name + " plus new, saved", tops)
    print("PASS test_new_steps_on_top_of_old_ones")


def main():
    ok = True
    for t in (test_loads_and_plays_back, test_saved_again_by_this_build, test_repacked_in_the_background_form,
              test_new_steps_on_top_of_old_ones):
        try:
            t()
        except Exception:
            ok = False
            print("FAIL", t.__name__)
            traceback.print_exc()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
