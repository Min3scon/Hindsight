"""Tier 1 gate machinery shared by the headless gate tests (test_bl_t1_gate, test_bl_t1_reload,
test_bl_t1_migrate): run a row's operations with Hindsight recording, and check the result against
the independent oracle (tests/oracle.py), a replay without Hindsight, step counts, every history
position, the global timeline and Blender's undo.
"""
import random

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t1_ops as T
from hindsight.bl import datarec, idmap, runtime
from hindsight.bl import warnings as W
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


def fresh_scene():
    H.fresh()
    T.build_scene()


class Run:
    """The operations run with Hindsight recording: states, history lengths and the newest step
    number after each operation, and the name of every tracked object. setup() prepares the scene
    (default: a fresh scene from t1_ops.build_scene); every object in it is recorded."""

    def __init__(self, ops, setup=fresh_scene):
        setup()
        track_all()
        self.ops = []
        self.states, self.lens, self.seqs = [snap()], [lengths()], [max_seq()]
        self.names, self.names_at = {}, []
        self.renames = {}            # old name -> new name, for references read by name
        self._note_names()
        for op in ops:
            self.add(op)
        self.scene_uid = idmap.uid_of(C.scene)

    def add(self, op):
        """Run one more operation and record its state."""
        op.run()
        tick_like(op.label)
        self.ops.append(op)
        self.states.append(snap())
        self.lens.append(lengths())
        self.seqs.append(max_seq())
        self._note_names()

    def _note_names(self):
        """Tracked objects by id, noted after every operation (so deleted ones keep their name, and
        a renamed object is found under the name it had at each step)."""
        now = {}
        for uid in runtime.reg().everything:
            ob = idmap.find(uid)
            if isinstance(ob, bpy.types.Object):
                self.names[uid] = ob.name
                now[uid] = ob.name
        self.names_at.append(now)


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
        for names in (r.names_at[i], r.names_at[i + 1]):     # the names before and after the operation
            want |= {u for u, n in names.items() if n in op.steps}
        for u in set(before) | set(after):
            d = after.get(u, 0) - before.get(u, 0)
            exp = 1 if u in want else 0
            who = "the scene" if u == r.scene_uid else r.names.get(u, u)
            assert d == exp, f"{op.label}: {who} got {d} steps, want {exp}"


def _own(state, renames=None, shared_mesh=False):
    """An object's state without values that depend on other objects (its own history alone
    doesn't move those). renames: references to objects renamed since read by their new name.
    shared_mesh: the object's mesh is recorded by another object's history (D39), so it is left out."""
    s = {k: v for k, v in state.items() if k != "canon" and not (shared_mesh and k == "mesh")}
    s["rna"] = {k: v for k, v in s["rna"].items() if k not in ("matrix_world", "matrix_local")}
    return _renamed_refs(s, renames) if renames else s


def _renamed_refs(v, renames):
    if isinstance(v, tuple) and len(v) == 3 and v[0] == "ID" and v[2] in renames:
        new = v[2]
        while new in renames:
            new = renames[new]
        return ("ID", v[1], new)
    if isinstance(v, dict):
        return {k: _renamed_refs(x, renames) for k, x in v.items()}
    if isinstance(v, (tuple, list)):
        return type(v)(_renamed_refs(x, renames) for x in v)
    return v


def check_object_histories(r, full=True, seed=7):
    """Every object's own history, at every position in a shuffled order and (full) in full sweeps
    both ways."""
    rng = random.Random(seed)
    for uid, name in sorted(r.names.items(), key=lambda kv: kv[1]):
        h = runtime.reg().history_for(uid)
        if h is None or not h.entries or idmap.find(uid) is None:
            continue                    # a deleted object: checked through the global timeline
        # D39: a linked duplicate's mesh is recorded by its owner's history (checked there and on
        # the global timeline); this object's own history holds its object data
        shared = datarec.mesh_owned_elsewhere(idmap.find(uid))
        want = {}
        for i, ln in enumerate(r.lens):
            then = r.names_at[i].get(uid)
            if then is None or then not in r.states[i]:
                continue
            st = _own(r.states[i][then], r.renames, shared)
            if ln[uid] in want:
                assert want[ln[uid]] == st, (f"{name} changed without a step at operation {i}: "
                                             f"{O.diff(want[ln[uid]], st)[:3]}")
            else:
                want[ln[uid]] = st
        positions = sorted(want)
        order = positions[:]
        rng.shuffle(order)
        for pos in (order + positions + positions[::-1]) if full else order + [positions[-1]]:
            runtime._goto_history(h, pos, True)
            ob = idmap.find(uid)
            assert ob is not None, f"{name} is gone at its position {pos}"
            got = _own(O.object_state(ob, derived=False), r.renames, shared)
            gone = _deleted_refs(want[pos])
            if not gone:
                assert got == want[pos], f"{name} at its position {pos}: {O.diff(want[pos], got)[:4]}"
                continue
            # D37: an object's own history can't bring back an object it referred to and that is
            # deleted now: exactly those references are empty, everything else is exact, and the
            # sidebar says which object it was
            assert _same_but_deleted(want[pos], got, gone), \
                f"{name} at its position {pos} (refers to deleted {sorted(gone)}): {O.diff(want[pos], got)[:4]}"
            notes = " ".join(t for _s, t in W.everything_notes(C, ob))
            assert all(n in notes for n in gone), f"{name} at {pos}: no note about deleted {sorted(gone)}: {notes}"
        runtime._goto_history(h, len(h.entries), True)


def _deleted_refs(state) -> set:
    """Names of objects a recorded state refers to that don't exist now."""
    out = set()

    def walk(v):
        if isinstance(v, tuple) and len(v) == 3 and v[0] == "ID":
            if v[1] == "Object" and v[2] not in bpy.data.objects:
                out.add(v[2])
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(state)
    return out


def _same_but_deleted(want, got, gone) -> bool:
    """want == got, except that a reference to an object in `gone` may be empty (None) in got."""
    if isinstance(want, tuple) and len(want) == 3 and want[0] == "ID" and want[1] == "Object" and want[2] in gone:
        return got is None
    if isinstance(want, dict):
        return isinstance(got, dict) and want.keys() == got.keys() and \
            all(_same_but_deleted(want[k], got[k], gone) for k in want)
    if isinstance(want, (list, tuple)) and not (isinstance(want, tuple) and want and want[0] == "f"):
        return type(got) is type(want) and len(got) == len(want) and \
            all(_same_but_deleted(a, b, gone) for a, b in zip(want, got))
    return want == got


def goto_after(max_seq_):
    """Move the global slider so that exactly the steps numbered <= max_seq_ are applied."""
    vis = runtime._visible_uids()
    groups = globaltl.global_groups(runtime.reg(), vis)
    runtime.global_goto(C, sum(1 for g in groups if g[-1][0] <= max_seq_))


def check_global_positions(r):
    """D16: every position of the global timeline is a whole operation (no stop halfway through an
    operation that changed several objects)."""
    groups = globaltl.global_groups(runtime.reg(), runtime._visible_uids())
    ends = {g[-1][0] for g in groups}
    bounds = set(r.seqs[1:])
    assert len(groups) == len([i for i in range(len(r.ops)) if r.seqs[i + 1] != r.seqs[i]]) + \
        len([g for g in groups if g[-1][0] <= r.seqs[0]]), (len(groups), len(r.ops))
    assert ends <= bounds | {s for s in ends if s <= r.seqs[0]}, \
        f"positions that end inside an operation: {sorted(ends - bounds)}"


def check_global(r, full=True, seed=11):
    sp = C.scene.hindsight
    sp.use_global = True
    try:
        n = len(r.states)
        order = list(range(n))
        random.Random(seed).shuffle(order)
        for i in (order + list(range(n)) + list(range(n - 1, -1, -1))) if full else order:
            goto_after(r.seqs[i])
            got = snap()
            what = r.ops[i - 1].label if i else "the start"
            assert got == r.states[i], f"global timeline after {what}: {O.diff(r.states[i], got)[:4]}"
    finally:
        goto_after(r.seqs[-1])
        sp.use_global = False


def _saved_states(h):
    out = {}
    for m in h.members:
        st = runtime._STATES.get(m)
        if st is not None:
            out[m] = (dict(st.counts), {k: v.copy() for k, v in st.arrays.items()}, dict(st.values),
                      dict(st.snaps))
    return out


def _restore_states(saved):
    for m, (counts, arrays, values, snaps) in saved.items():
        st = runtime._STATES[m]
        st.counts, st.arrays, st.values, st.snaps = counts, arrays, values, snaps


def _emulate_blender_undo(group, direction):
    """What Blender's own undo or redo of one operation does: the data of every history it touched
    goes back (or forward) one step, Hindsight's cursors and recorded values stay where they were.
    on_undo must then move every one of those cursors. Returns [(history, cursor wanted)]."""
    steps = [(seq, runtime.reg().histories[hid], i) for seq, hid, i in group]
    saved, at = {}, {}
    for _seq, h, _i in steps:
        saved.update(_saved_states(h))
        at[h.uid] = h.cursor
    for _seq, h, i in (steps[::-1] if direction == "undo" else steps):
        runtime._goto_history(h, i if direction == "undo" else i + 1, True)
    for _seq, h, _i in steps:
        h.cursor = at[h.uid]
    _restore_states(saved)
    datarec.on_undo(direction)
    return [(h, i if direction == "undo" else i + 1) for _seq, h, i in steps]


def check_undo_matching(r, depth=6):
    """Blender's undo stack is one line of operations: Ctrl+Z six times from the end, then
    Ctrl+Shift+Z six times (headless emulation of the data Blender puts back; the real keys are in
    the real-UI gate). Every cursor of every operation follows exactly, and nothing is recorded."""
    reg = runtime.reg()
    vis = runtime._visible_uids()
    groups = globaltl.global_groups(reg, vis)
    n_all = {x.uid: len(x.entries) for x in reg.histories.values()}
    pos = globaltl.global_position(reg, vis)
    assert pos == len(groups)
    k = min(depth, len(groups))
    for direction, rng in (("undo", range(pos - 1, pos - 1 - k, -1)), ("redo", range(pos - k, pos))):
        for gi in rng:
            grp = groups[gi]
            label = reg.histories[grp[0][1]].entries[grp[0][2]].meta.get("label")
            for h, want in _emulate_blender_undo(grp, direction):
                assert h.cursor == want, f"{direction} of {label}: cursor {h.cursor}, want {want}"
    assert {x.uid: len(x.entries) for x in reg.histories.values()} == n_all, "a step was recorded"


def recheck(r):
    """Every check that only needs the recorded run (no replay): after a reload, a rename, ..."""
    T.select()
    check_object_histories(r)
    check_global_positions(r)
    check_global(r)
    T.select()
    check_undo_matching(r)
