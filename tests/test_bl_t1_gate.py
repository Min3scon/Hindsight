"""Tier 1 gate, headless: every row's operations through real operators, checked against the
independent oracle (tests/oracle.py) and a replay of the same operations without Hindsight.

For each row's sequence (tests/t1_ops.py):
  REPLAY  the states after each operation equal those of the same run without Hindsight;
  STEPS   each operation gives exactly one step in each history it says it changes and none in any
          other; no object changes without a step (a missed change shows up here);
  EXACT   every object's own history scrubbed to every position, in a shuffled order and in full
          sweeps both ways, matches the oracle at that position (mesh bit-exact); the global
          timeline at every operation boundary matches the whole scene.
Step boundaries through the real timer (operator end, settle) are checked in the real-UI scenarios.

Run: blender -b --factory-startup --python tests/test_bl_t1_gate.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import random

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t1_ops as T
from tests.gate import covers
from hindsight.bl import datarec, idmap, runtime
from hindsight.core import globaltl

C = bpy.context


def snap() -> dict:
    """Every object through the oracle, plus each mesh in canonical form for the replay check."""
    out = {"__scene__": O.scene_state()}
    for ob in C.scene.objects:
        out[ob.name] = O.object_state(ob)
        if ob.type == "MESH":
            out[ob.name]["canon"] = O.read_mesh(ob, O.canonical_mesh_state)
    return out


def track_all():
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    datarec.FORCE_KINDS[0] = None
    for ob in list(C.scene.objects):
        datarec.set_tracked(ob, True)
    datarec.set_tracked(C.scene, True)


def tick_like(label):
    """What the timer does when an operator finishes (headless runs have no timers)."""
    C.view_layer.update()
    datarec.refresh_mesh_users()
    datarec.commit(label=label, uids=datarec.tracked_uids() | set(datarec._DIRTY), mesh=True)
    datarec._edit_step(label)


def lengths() -> dict:
    out = {}
    for uid in set(runtime.reg().everything):
        h = runtime.reg().history_for(uid)
        out[uid] = len(h.entries) if h is not None else 0
    return out


def max_seq() -> int:
    return max((e.seq for h in runtime.reg().histories.values() for e in h.entries), default=0)


class Run:
    """The operations run with Hindsight recording: states, history lengths and the newest step
    number after each operation, and the name of every tracked object."""

    def __init__(self, ops):
        H.fresh()
        T.build_scene()
        track_all()
        self.ops = ops
        self.states, self.lens, self.seqs = [snap()], [lengths()], [max_seq()]
        for op in ops:
            op.run()
            tick_like(op.label)
            self.states.append(snap())
            self.lens.append(lengths())
            self.seqs.append(max_seq())
        self.names = {}
        for i, st in enumerate(self.states):            # names by id, deleted objects included
            for uid in self.lens[i]:
                ob = idmap.find(uid)
                if isinstance(ob, bpy.types.Object):
                    self.names[uid] = ob.name
        self.scene_uid = idmap.uid_of(C.scene)


def replay(ops) -> list:
    """The same operations without Hindsight."""
    H.fresh(register=False)
    T.build_scene()
    states = [snap()]
    for op in ops:
        op.run()
        states.append(snap())
    return states


def replay_view(states: list, ops) -> list:
    """The states with every mesh in canonical form (oracle.canonical_mesh_state): the replay can't
    match Blender's own element order, which depends on memory allocation history. After an
    operation that leaves new corner values unset (Fill, Bridge), that object's corner values are
    left out too."""
    out, unset = [], set()
    for i, st in enumerate(states):
        if i and ops[i - 1].unset_corners:
            unset |= set(ops[i - 1].steps)
        view = {}
        for n, v in st.items():
            if isinstance(v, dict) and "canon" in v:
                mesh = v["canon"]
                if n in unset:
                    mesh = {k: x for k, x in mesh.items() if "|CORNER|" not in k}
                v = dict(v, mesh=mesh, canon=None)
            view[n] = v
        out.append(view)
    return out


def check_replay(row, plain, r):
    a_all, b_all = replay_view(plain, r.ops), replay_view(r.states, r.ops)
    for i, (a, b) in enumerate(zip(a_all, b_all)):
        assert a == b, f"{row}: with Hindsight, operation {i} gave a different result: {O.diff(a, b)[:4]}"


def check_steps(r):
    for i, op in enumerate(r.ops):
        before, after = r.lens[i], r.lens[i + 1]
        want = {r.scene_uid} if "scene" in op.steps else set()
        want |= {u for u, n in r.names.items() if n in op.steps}
        for u in set(before) | set(after):
            d = after.get(u, 0) - before.get(u, 0)
            exp = 1 if u in want else 0
            who = "the scene" if u == r.scene_uid else r.names.get(u, u)
            assert d == exp, f"{op.label}: {who} got {d} steps, want {exp}"


def _own(state):
    """An object's state without values that depend on other objects (its own history alone
    doesn't move those)."""
    s = {k: v for k, v in state.items() if k != "canon"}
    s["rna"] = {k: v for k, v in s["rna"].items() if k not in ("matrix_world", "matrix_local")}
    return s


def check_object_histories(r):
    rng = random.Random(7)
    for uid, name in sorted(r.names.items(), key=lambda kv: kv[1]):
        h = runtime.reg().history_for(uid)
        if h is None or not h.entries:
            continue
        want = {}
        for i, ln in enumerate(r.lens):
            if name not in r.states[i]:
                continue
            st = _own(r.states[i][name])
            if ln[uid] in want:
                assert want[ln[uid]] == st, (f"{name} changed without a step at operation {i}: "
                                             f"{O.diff(want[ln[uid]], st)[:3]}")
            else:
                want[ln[uid]] = st
        positions = sorted(want)
        order = positions[:]
        rng.shuffle(order)
        for pos in order + positions + positions[::-1]:
            runtime._goto_history(h, pos, True)
            ob = idmap.find(uid)
            assert ob is not None, f"{name} is gone at its position {pos}"
            got = O.object_state(ob, derived=False)
            assert got == want[pos], f"{name} at its position {pos}: {O.diff(want[pos], got)[:4]}"
        runtime._goto_history(h, len(h.entries), True)


def goto_after(max_seq_):
    """Move the global slider so that exactly the steps numbered <= max_seq_ are applied."""
    vis = runtime._visible_uids()
    order = globaltl.global_order(runtime.reg(), vis)
    runtime.global_goto(C, sum(1 for seq, _h, _i in order if seq <= max_seq_))


def check_global(r):
    sp = C.scene.hindsight
    sp.use_global = True
    try:
        n = len(r.states)
        order = list(range(n))
        random.Random(11).shuffle(order)
        for i in order + list(range(n)) + list(range(n - 1, -1, -1)):
            goto_after(r.seqs[i])
            got = snap()
            what = r.ops[i - 1].label if i else "the start"
            assert got == r.states[i], f"global timeline after {what}: {O.diff(r.states[i], got)[:4]}"
    finally:
        goto_after(r.seqs[-1])
        sp.use_global = False


def gate(row):
    ops = T.sequences()[row]
    plain = replay(ops)
    r = Run(ops)
    check_replay(row, plain, r)
    check_steps(r)
    T.select()
    check_object_histories(r)
    check_global(r)


@covers("obj.transforms", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_transforms():
    gate("obj.transforms")


@covers("obj.parenting", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_parenting():
    gate("obj.parenting")


@covers("obj.constraints", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_constraints():
    gate("obj.constraints")


@covers("obj.collections", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_collections():
    gate("obj.collections")


@covers("obj.addremove", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_addremove():
    gate("obj.addremove")


@covers("obj.editsteps", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_editsteps():
    gate("obj.editsteps")


for fn in (test_gate_transforms, test_gate_parenting, test_gate_constraints, test_gate_collections,
           test_gate_addremove, test_gate_editsteps):
    H.run(fn)
H.finish()
