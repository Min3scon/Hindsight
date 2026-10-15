"""Tier 1 gate, property-based (gate item PROP): Hypothesis generates 2,000 random sequences of
real Tier 1 operations; after each sequence every object's history is restored to every recorded
position (shuffled) and the global timeline to every operation boundary, and compared with the
independent oracle; then Blender's undo and redo of the last operations are emulated.

Every sequence has at least one operation for each Tier 1 adapter (object settings, collections and
constraints, the scene, Edit Mode), so each adapter gets 2,000 sequences. Failing sequences are
shrunk by Hypothesis and saved in tests/corpus/t1_prop/ (kept in the repo); the corpus is replayed
first on every run. Hypothesis comes from .tools/pylib (tools are never installed into Blender).

Run: blender -b --factory-startup --python tests/test_bl_t1_prop.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hashlib
import json
import time

import bpy

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers

sys.path.insert(0, os.path.join(H.ROOT, ".tools", "pylib"))
try:
    from hypothesis import given, settings, strategies as st, HealthCheck
except ImportError:                     # never a silent skip: the gate needs this test to run
    raise SystemExit("FAIL test_t1_random_sequences: Hypothesis missing (pip install --target .tools/pylib hypothesis)")

C = bpy.context
EXAMPLES = 2000
CORPUS = os.path.join(H.ROOT, "tests", "corpus", "t1_prop")
ROWS = ("obj.transforms", "obj.parenting", "obj.constraints", "obj.collections", "obj.addremove", "obj.editsteps")

OBJ = st.sampled_from(["A", "B", "C", "Pivot", "E"])
MESH = st.sampled_from(["A", "B", "C", "E"])
F = st.floats(-3, 3, allow_nan=False, allow_infinity=False, width=32)
CONS = st.sampled_from(["COPY_LOCATION", "COPY_ROTATION", "LIMIT_LOCATION", "TRACK_TO", "CHILD_OF", "DAMPED_TRACK"])
EDITS = st.sampled_from(["grab", "rotate", "scale", "extrude", "bevel", "inset", "subdivide", "merge",
                         "dissolve", "delete", "select only"])

RNA = st.one_of(
    st.tuples(st.just("move"), OBJ, F, F, F),
    st.tuples(st.just("rotate"), OBJ, F, st.sampled_from("XYZ")),
    st.tuples(st.just("scale"), OBJ, st.floats(0.25, 2.5, width=32)),
    st.tuples(st.just("move2"), OBJ, OBJ, F),
    st.tuples(st.just("deltas"), OBJ),
    st.tuples(st.just("apply"), MESH, st.sampled_from(["location", "rotation", "scale"])),
    st.tuples(st.just("origin"), MESH),
    st.tuples(st.just("parent"), OBJ, OBJ, st.booleans()),
    st.tuples(st.just("unparent"), OBJ, st.sampled_from(["CLEAR", "CLEAR_KEEP_TRANSFORM", "CLEAR_INVERSE"])),
    st.tuples(st.just("rotmode"), OBJ, st.sampled_from(["XYZ", "ZXY", "QUATERNION", "AXIS_ANGLE"])),
    st.tuples(st.just("rename"), OBJ, st.integers(1, 9)),
)
OBJECT = st.one_of(
    st.tuples(st.just("con_add"), OBJ, CONS),
    st.tuples(st.just("con_del"), OBJ, st.integers(0, 2)),
    st.tuples(st.just("con_move"), OBJ, st.integers(0, 2), st.integers(0, 2)),
    st.tuples(st.just("con_copy"), OBJ, st.integers(0, 2)),
    st.tuples(st.just("con_influence"), OBJ, st.integers(0, 2), st.floats(0, 1, width=32)),
    st.tuples(st.just("coll_move"), OBJ, st.sampled_from(["Props", "Extra"])),
    st.tuples(st.just("coll_link"), OBJ, st.sampled_from(["Props", "Extra"])),
    st.tuples(st.just("coll_remove"), OBJ, st.sampled_from(["Props", "Extra"])),
    st.tuples(st.just("hide"), OBJ, st.booleans()),
)
SCENE = st.one_of(
    st.tuples(st.just("add"), st.sampled_from(list(T.PRIMITIVES) + ["empty"]), F),
    st.tuples(st.just("delete"), OBJ),
    st.tuples(st.just("dup"), OBJ, st.booleans()),
)
EDIT = st.tuples(st.just("edit"), st.sampled_from(["E", "B"]), EDITS, st.integers(0, 10_000))
ANY = st.one_of(RNA, OBJECT, SCENE, EDIT)
SEQUENCES = st.tuples(RNA, OBJECT, SCENE, EDIT, st.lists(ANY, max_size=5)).flatmap(
    lambda t: st.permutations(list(t[:4]) + list(t[4])))


def _ob(name):
    return bpy.data.objects.get(name)


def _con(name, i):
    ob = _ob(name)
    return ob.constraints[i] if ob is not None and i < len(ob.constraints) else None


EDIT_OPS = {o.label.split(": ")[1]: o for o in T.edit_ops("E", 0)}


def run_spec(spec):
    """Run one generated operation; {'CANCELLED'} when it doesn't apply to the scene as it is now
    (the object is gone, nothing to delete, ...)."""
    kind, a = spec[0], spec[1:]
    if kind not in ("add",) and not _ob(a[0]):
        return {"CANCELLED"}
    if kind == "move":
        return T.translate(a[0], a[1:4]).fn()
    if kind == "rotate":
        return T.rotate(a[0], a[1], a[2]).fn()
    if kind == "scale":
        return T.resize(a[0], a[1]).fn()
    if kind == "move2":
        if not _ob(a[1]) or a[0] == a[1]:
            return {"CANCELLED"}
        return T.translate_multi([a[0], a[1]], (a[2], 0, 0)).fn()
    if kind == "deltas":
        return T.to_deltas(a[0]).fn()
    if kind == "apply":
        return T.apply_transform(a[0], a[1]).fn() if _ob(a[0]).type == "MESH" and _ob(a[0]).data.users == 1 \
            else {"CANCELLED"}
    if kind == "origin":
        return T.origin_to_geometry(a[0]).fn() if _ob(a[0]).type == "MESH" else {"CANCELLED"}
    if kind == "parent":
        if not _ob(a[1]) or a[0] == a[1]:
            return {"CANCELLED"}
        return T.parent(a[0], a[1], a[2]).fn()
    if kind == "unparent":
        return T.unparent(a[0], a[1]).fn()
    if kind == "rotmode":                     # what the N panel's rotation mode menu does
        T.select(a[0])
        _ob(a[0]).rotation_mode = a[1]
        return {"FINISHED"}
    if kind == "rename":                      # what F2 does
        T.select(a[0])
        _ob(a[0]).name = f"{a[0]}_{a[1]}"
        return {"FINISHED"}
    if kind == "con_add":
        return T.add_constraint(a[0], a[1]).fn()
    if kind == "con_del":
        return T.delete_constraint(a[0], a[1]).fn() if _con(a[0], a[1]) else {"CANCELLED"}
    if kind == "con_move":
        return T.move_constraint(a[0], a[1], min(a[2], len(_ob(a[0]).constraints) - 1)).fn() \
            if _con(a[0], a[1]) else {"CANCELLED"}
    if kind == "con_copy":
        return T.copy_constraint(a[0], a[1]).fn() if _con(a[0], a[1]) else {"CANCELLED"}
    if kind == "con_influence":               # dragging the Influence slider
        c = _con(a[0], a[1])
        if c is None:
            return {"CANCELLED"}
        T.select(a[0])
        c.influence = a[2]
        return {"FINISHED"}
    if kind == "coll_move":
        return T.move_to_collection(a[0], a[1], new=a[1] not in bpy.data.collections).fn()
    if kind == "coll_link":
        return T.link_to_collection(a[0], a[1]).fn() if a[1] in bpy.data.collections else {"CANCELLED"}
    if kind == "coll_remove":
        ob = _ob(a[0])
        if a[1] not in bpy.data.collections or len(ob.users_collection) < 2 or \
                ob.name not in bpy.data.collections[a[1]].objects:
            return {"CANCELLED"}
        return T.remove_from_collection(a[0], a[1]).fn()
    if kind == "hide":                        # H and Alt+H
        T.select(a[0])
        _ob(a[0]).hide_set(a[1])
        return {"FINISHED"}
    if kind == "add":
        return T.add_primitive(a[0], (a[1], -6, 0)).fn()
    if kind == "delete":
        return T.delete([a[0]]).fn()
    if kind == "dup":
        return T.duplicate(a[0], a[1]).fn()
    if kind == "edit":
        if _ob(a[0]) is None or _ob(a[0]).type != "MESH":
            return {"CANCELLED"}
        if a[1] == "select only":
            return T.select_only_in_edit(a[0], a[2]).fn()
        tmpl = {o.label.split(": ")[1]: o for o in T.edit_ops(a[0], a[2])}[a[1]]
        return tmpl.fn()
    raise ValueError(spec)


class SpecOp(T.Op):
    def __init__(self, spec):
        super().__init__(" ".join(str(x) for x in spec), None, [])
        self.spec = spec

    def run(self):
        try:
            run_spec(self.spec)
        except RuntimeError:
            pass                                # Blender refused it (poll, nothing selected): no change


def check_sequence(specs):
    ops = [SpecOp(s) for s in specs]
    r = L.Run(ops)
    T.select()
    L.check_object_histories(r, full=False)
    L.check_global_positions(r)
    L.check_global(r, full=False)
    T.select()
    L.check_undo_matching(r, depth=2)


def _save_failure(specs):
    """Keep a failing sequence in the corpus (the shrunk one: Hypothesis runs it last)."""
    os.makedirs(CORPUS, exist_ok=True)
    data = json.dumps([list(s) for s in specs])
    name = hashlib.blake2b(data.encode(), digest_size=6).hexdigest()
    with open(os.path.join(CORPUS, name + ".json"), "w", encoding="utf-8") as fh:
        fh.write(data + "\n")


COUNT = [0]
FAILURES = []           # every failing sequence in order; Hypothesis replays each shrunk one last


@settings(max_examples=EXAMPLES, deadline=None, derandomize=True, database=None,
          suppress_health_check=list(HealthCheck))
@given(SEQUENCES)
def _property(specs):
    COUNT[0] += 1
    try:
        check_sequence(specs)
    except Exception:
        FAILURES.append(specs)
        raise


@covers(ROWS, "PROP")
def test_t1_random_sequences():
    if os.path.isdir(CORPUS):
        for f in sorted(os.listdir(CORPUS)):
            with open(os.path.join(CORPUS, f), encoding="utf-8") as fh:
                check_sequence([tuple(x) for x in json.load(fh)])
    t = time.time()
    try:
        _property()
    except BaseException as e:
        # Hypothesis ends by replaying each distinct shrunk failure once: keep all of them
        n = len(getattr(e, "exceptions", ())) or 1
        for specs in FAILURES[-n:]:
            _save_failure(specs)
            print("shrunk failing sequence saved:", specs)
        raise
    print(f"{COUNT[0]} generated sequences checked in {time.time() - t:.0f} s")
    assert COUNT[0] >= EXAMPLES, COUNT[0]


H.run(test_t1_random_sequences)
H.finish()
