"""Tier 0 gate machinery, headless: run a sequence of tests/t0_ops.py operations with Hindsight and
without it, then check every object's sculpt history and the global timeline against the
independent oracle (tests/oracle.py, mesh bit for bit)."""
import random

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t0_ops as T0
from tests.t1_ops import Op
from hindsight.bl import runtime
from hindsight.core import globaltl

C = bpy.context


def tick():
    """What the timer does once a stroke or operator has finished (headless runs have no timers):
    Blender evaluates the change, the depsgraph handler marks the object, the timer commits it."""
    C.view_layer.update()
    if not H._registered[0]:
        return
    for uid in list(runtime._DIRTY):
        runtime._DIRTY[uid] = 0.0          # settled
    runtime._tick_once()


def fresh_scene(register=True):
    H.fresh(register=register)
    T0.build_scene()


def snap() -> dict:
    """Every mesh through the oracle, by object name."""
    return {ob.name: O.read_mesh(ob, O.mesh_state) for ob in C.scene.objects if ob.type == "MESH"}


def lengths() -> dict:
    out = {}
    for ob in C.scene.objects:
        h = runtime.history_of(ob) if H._registered[0] else None
        out[ob.name] = len(h.entries) if h is not None else 0
    return out


class Run:
    """The operations with Hindsight recording: the scene after each, and each history's length."""

    def __init__(self, ops, setup=fresh_scene):
        setup()
        self.ops, self.states, self.lens = [], [snap()], [lengths()]
        for op in ops:
            self.add(op)

    def add(self, op):
        prep = getattr(op, "prep", None)
        if prep is not None:                  # switching to the mode first is a moment of its own
            self.add(Op(f"{op.label}: switch mode", prep, []))
        op.run()
        tick()
        self.ops.append(op)
        self.states.append(snap())
        self.lens.append(lengths())


def replay(ops) -> list:
    """The same operations without Hindsight."""
    fresh_scene(register=False)
    states = [snap()]
    for op in ops:
        if getattr(op, "prep", None) is not None:
            op.prep()
            C.view_layer.update()
            states.append(snap())
        op.run()
        C.view_layer.update()
        states.append(snap())
    return states


def check_replay(row, plain, r):
    for i, (a, b) in enumerate(zip(plain, r.states)):
        assert a == b, f"{row}: with Hindsight, operation {i} gave a different mesh: {O.diff(a, b)[:4]}"


def check_steps(r):
    for i, op in enumerate(r.ops):
        if getattr(op, "steps_check", None) is not None:      # Join merges histories
            op.steps_check(r.lens[i], r.lens[i + 1])
            continue
        for name in set(r.lens[i]) | set(r.lens[i + 1]):
            d = r.lens[i + 1].get(name, 0) - r.lens[i].get(name, 0)
            want = 1 if name in op.steps else 0
            assert d == want, f"{op.label}: {name} got {d} steps, want {want}"


def _wanted(r, name) -> dict:
    """Position in name's history -> its mesh there; a change without a step fails here. A sculpt
    history starts at the object's first stroke: position 0 is the mesh just before that stroke
    (entering Vertex Paint, for one, makes Blender add a colour layer first), so changes before
    the first step aren't in the history."""
    want, started = {}, False
    for i, ln in enumerate(r.lens):
        pos, st = ln.get(name, 0), r.states[i].get(name)
        if st is None:
            continue
        if i and pos > r.lens[i - 1].get(name, 0) + 1:
            # a Join merged other histories into this one: its positions are numbered anew (the
            # merged steps sit between its own); the ones before are checked on the global timeline
            want, started = {}, True
        if pos == 0 and not started:
            want[0] = st
            continue
        started = True
        if pos in want:
            assert want[pos] == st, f"{name} changed without a step at operation {i}: {O.diff(want[pos], st)[:3]}"
        else:
            want[pos] = st
    return want


def check_histories(r, full=True, seed=7):
    """Every object's own history at every position, in a shuffled order and (full) in sweeps both
    ways, matches the oracle bit for bit."""
    rng = random.Random(seed)
    for name in sorted(r.states[-1]):
        ob = bpy.data.objects[name]
        h = runtime.history_of(ob)
        if h is None or not h.entries:
            continue
        want = _wanted(r, name)
        positions = sorted(want)
        order = positions[:]
        rng.shuffle(order)
        for pos in (order + positions + positions[::-1]) if full else order + [positions[-1]]:
            runtime._goto_history(h, pos, True)
            got = O.read_mesh(ob, O.mesh_state)
            assert got == want[pos], f"{name} at its position {pos}: {O.diff(want[pos], got)[:4]}"
        runtime._goto_history(h, len(h.entries), True)


def check_global(r, full=True, seed=11):
    """The global timeline: one position per operation that made a step, each the whole scene as it
    was after that operation and any step-less ones that followed it (switching modes). An object
    whose history starts later is, at the positions before, as it was when its history started
    (just before its first stroke; COVERAGE lists this)."""
    C.scene.hindsight.use_global = True
    try:
        want, first = [r.states[0]], {}
        for i in range(len(r.ops)):
            stepped = [n for n, v in r.lens[i + 1].items() if v > r.lens[i].get(n, 0)]
            if stepped:              # (check_steps compares these with what each operation says)
                want.append(r.states[i + 1])
                for name in stepped:
                    first.setdefault(name, len(want) - 1)
            else:
                want[-1] = r.states[i + 1]
        for name, g0 in first.items():
            w = _wanted(r, name)
            if 0 not in w:
                continue                       # merged by a Join: its start is another object's
            for g in range(g0):
                want[g] = dict(want[g], **{name: w[0]})
        # objects without a history (a separated part, a copy) are never changed by a jump: as they
        # are now at every position, including those from before they were made
        final = r.states[-1]
        for name in final:
            if name not in first:
                for g in range(len(want)):
                    want[g] = dict(want[g], **{name: final[name]})
        vis = runtime._visible_uids()
        n = globaltl.global_length(runtime.reg(), vis)
        assert n == len(want) - 1, f"global timeline has {n} positions, want {len(want) - 1}"
        rng = random.Random(seed)
        order = list(range(len(want)))
        rng.shuffle(order)
        for g in (order + list(range(len(want))) + list(range(len(want)))[::-1]) if full else order:
            runtime.global_goto(C, g, True)
            got = snap()
            assert got == want[g], f"global position {g}: {O.diff(want[g], got)[:4]}"
        runtime.global_goto(C, len(want) - 1, True)
    finally:
        C.scene.hindsight.use_global = False


def gate(row, ops):
    plain = replay(ops)
    r = Run(ops)
    check_replay(row, plain, r)
    check_steps(r)
    check_histories(r)
    check_global(r)
    return r
