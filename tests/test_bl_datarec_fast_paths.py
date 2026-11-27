"""The data recorder's shortcuts never change what it records (headless).

Shortcuts under test (hindsight/bl/datarec.py, adapters.py, idmap.py):
  - _same_values: one marshal compare stands in for _diff when the values are identical;
  - idmap.frame: lookups by id read bpy.data.objects once per commit instead of once per lookup;
  - adapters.users_collection: one table of every collection's objects per commit;
  - adapters.normalise: vectors and number arrays become lists without a call per item;
  - _is_scene_uid / _scene_uids_in: only scenes are searched for a scene's id;
  - tick: with nothing recorded it reads nothing, but leaves the same state behind;
  - prime: an owner's mesh state is read once, not twice.

Each one is checked against the plain path: unit checks on awkward values, and whole operation
sequences recorded twice in one process (shortcuts on, shortcuts off) with every step compared
byte for byte. Plus the cases a shortcut could get wrong: a change right after an operator that
changed nothing, changes while recording was off, Blender's undo and redo, many objects changed by
one operator, renames, and an object deleted and another made with its name.

Run: blender -b --factory-startup --python tests/test_bl_datarec_fast_paths.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import contextlib
import itertools
import math
import random
import struct

import bpy
import mathutils

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.bl import adapters, datarec, dupsep, idmap, runtime
from hindsight.bl import prefs
from hindsight.core import registry
from hindsight.core.step import step_to_bytes
from hindsight.core.types import DATA

C = bpy.context


# ----------------------------------------------------------------------------- the plain paths
def old_normalise(v):
    """adapters.normalise before the fast path (reference)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, bpy.types.ID):
        return idmap.ref_of(v)
    if hasattr(v, "to_list") and hasattr(v, "col"):
        return [list(map(float, row)) for row in v]
    try:
        return [old_normalise(x) for x in v]
    except TypeError:
        return str(v)


def old_is_scene_uid(uid):
    if uid in datarec._SCENE_UIDS:
        return True
    idb = idmap.find(uid)
    if isinstance(idb, bpy.types.Scene):
        datarec._SCENE_UIDS.add(uid)
        return True
    return False


class _NoFrame:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


@contextlib.contextmanager
def plain_paths():
    """Every shortcut off: the recorder runs as it did before them."""
    saved = [(idmap, "frame", idmap.frame), (idmap, "frame_cache", idmap.frame_cache),
             (datarec, "_fast_diff_ok", datarec._fast_diff_ok), (adapters.core, "normalise", adapters.core.normalise),
             (datarec, "_is_scene_uid", datarec._is_scene_uid),
             (datarec, "_local_scene_ids", datarec._local_scene_ids)]
    idmap.frame = _NoFrame
    idmap.frame_cache = lambda: None
    datarec._fast_diff_ok = lambda: False
    adapters.core.normalise = old_normalise
    datarec._is_scene_uid = old_is_scene_uid
    datarec._local_scene_ids = lambda: None
    try:
        yield
    finally:
        for mod, name, v in saved:
            setattr(mod, name, v)


@contextlib.contextmanager
def fixed_ids():
    """Ids from a counter (so two recordings of the same operations can be compared byte for byte)."""
    n = itertools.count(1)

    def nu():
        return f"{next(n):016x}"
    saved = [(m, m.new_uid) for m in (registry, idmap, runtime, dupsep)]
    for m, _f in saved:
        m.new_uid = nu
    try:
        yield
    finally:
        for m, f in saved:
            m.new_uid = f


def dump() -> dict:
    """Every history: cursor and each step's bytes (the time it was recorded set to 0), and every
    recorded state's values."""
    out = {}
    for hid in sorted(runtime.reg().histories):
        h = runtime.reg().histories[hid]
        steps = []
        for e in h.entries:
            s = e.load()
            s.time = 0.0
            # Blender doesn't make the same edge and face order every time (a second UV sphere in a
            # session differs from the first, so do some Edit Mode results): mesh copies (added and
            # deleted objects, topology steps) compare by sizes and positions, the rest byte for byte
            snaps = (s.extra or {}).pop("_snapshots", None)
            before, after = s.before, s.after
            s.before = s.after = None
            steps.append(step_to_bytes(s, "none"))
            steps.append(sorted((k, _mesh_summary(v)) for k, v in (snaps or {}).items()))
            steps.append((_mesh_summary(before), _mesh_summary(after)))
        out[hid] = (h.cursor, sorted(h.members), steps)
    vals = {u: sorted((repr(k), struct_repr(v)) for k, v in st.values.items())
            for u, st in runtime._STATES.items()}
    return {"hist": out, "values": vals, "everything": sorted(runtime.reg().everything)}


def _mesh_summary(snap):
    if snap is None:
        return None
    import numpy as np
    pos = next((a for k, a in snap.arrays.items() if k.startswith("position")), None)
    rows = None if pos is None else np.sort(np.asarray(pos).reshape(-1, 3).view("V12").ravel()).tobytes()
    return sorted(snap.counts.items()), rows


def struct_repr(v):
    """repr that tells -0.0 from 0.0 and keeps NaN bits."""
    if isinstance(v, float):
        return ("f", struct.pack("<d", v))
    if isinstance(v, list):
        return ("l", tuple(struct_repr(x) for x in v))
    if isinstance(v, dict):
        return ("d", tuple((k, struct_repr(x)) for k, x in v.items()))
    return (type(v).__name__, v)


def record(ops, plain: bool, undo_at=()):
    """Run the operations with Hindsight recording (ids from a counter). undo_at: after which
    operations (index) Blender's undo then redo runs."""
    with fixed_ids():
        L.fresh_scene()
        L.track_all()
        L.tick_like("baseline")
        bpy.ops.ed.undo_push(message="start")
        ctx = plain_paths() if plain else contextlib.nullcontext()
        with ctx:
            for i, op in enumerate(ops):
                op.run()
                bpy.ops.ed.undo_push(message=op.label)
                L.tick_like(op.label)
                if i in undo_at:
                    bpy.ops.ed.undo()
                    bpy.ops.ed.redo()
                    L.tick_like("after undo")
        return dump()


# ----------------------------------------------------------------------------- unit checks
def _values(rng):
    pick = rng.random()
    if pick < 0.2:
        return rng.choice([0.0, -0.0, 1.0, 1, True, False, float("nan"), float("inf"), 1e-310, 0.1, None, "a", ""])
    if pick < 0.6:
        return [rng.choice([0.0, -0.0, 0.5, 1, True, struct.unpack("<d", struct.pack("<Q", 0x7ff8000000000001))[0]])
                for _ in range(rng.randint(0, 4))]
    if pick < 0.8:
        return {"ref": rng.choice([None, "abc", "0123456789abcdef"])}
    return [[rng.random() for _ in range(3)] for _ in range(3)]


def _mutate(rng, v):
    """A value that differs from v in one way _diff sees (or an equal copy)."""
    if isinstance(v, float):
        return rng.choice([-v, v + 1.0, int(v) if math.isfinite(v) else 0, v])
    if isinstance(v, bool):
        return rng.choice([int(v), not v, v])
    if isinstance(v, int):
        return rng.choice([float(v), bool(v), v])
    if isinstance(v, list):
        if v and rng.random() < 0.7:
            i = rng.randrange(len(v))
            return v[:i] + [_mutate(rng, v[i])] + v[i + 1:]
        return rng.choice([v + [0.0], list(v)])
    if isinstance(v, dict):
        return {"ref": rng.choice([None, "x", v["ref"]])}
    return rng.choice([v, "b", None])


def test_same_values_never_hides_a_difference():
    """_same_values(a, b) is True only when _diff(a, b) is empty: -0.0 against 0.0, 1 against 1.0
    against True, NaN payloads, nested lists and refs, keys in another order."""
    H.fresh()
    rng = random.Random(7)
    seen_same = seen_diff = 0
    for _ in range(4000):
        keys = [(rng.choice(["rna", "object", "scene"]), f"p{k}") for k in range(rng.randint(1, 6))]
        a = {k: _values(rng) for k in keys}
        b = {k: _mutate(rng, v) for k, v in a.items()}
        if rng.random() < 0.2:
            b = dict(reversed(list(b.items())))
        if rng.random() < 0.1:
            b.pop(next(iter(b)))
        fast = datarec._same_values(a, b)
        with plain_paths():
            d = datarec._diff(a, b)                 # value by value with Adapter.changed
        assert set(datarec._diff(a, b)) == set(d), (a, b)   # the per-value shortcut agrees
        if fast:
            seen_same += 1
            assert d == {}, (a, b, d)
        elif d:
            seen_diff += 1
    assert seen_same > 100 and seen_diff > 1000, (seen_same, seen_diff)
    # what a recorder actually holds: an object's capture against a copy of itself
    ob = bpy.data.objects.new("o", None)
    C.scene.collection.objects.link(ob)
    prefs.FORCE["record_scope"] = "EVERYTHING"
    cap = datarec.capture(ob)
    assert datarec._same_values(cap, datarec.capture(ob))
    ob.location.x = -0.0
    cap2 = datarec.capture(ob)
    assert not datarec._same_values(cap, cap2) and datarec._diff(cap, cap2)


def _same_values_before_r070(old, new):
    """datarec._same_values as it was before R070: the marshal compare alone."""
    import marshal
    if len(old) != len(new):
        return False
    try:
        if old.keys() != new.keys():
            return False
        ks = [k for k, v in new.items() if old[k] is not v]
        return marshal.dumps([old[k] for k in ks], 2) == marshal.dumps([new[k] for k in ks], 2)
    except Exception:
        return False


def test_same_values_answers_as_before_r070():
    """R070: _same_values asks the engine's strict comparison first. Its answer is the one of the marshal
    compare it had before, on random values and on what the recorder holds for 60 objects with changes;
    the only allowed difference is True where marshal saw a string interned on one side only, and then
    _diff finds nothing either."""
    H.fresh()
    rng = random.Random(70)
    calls = []
    real = datarec._STRICT_SAME

    def counted(a, b):
        r = real(a, b)
        calls.append(r)
        return r
    datarec._STRICT_SAME = counted
    try:
        n_same = n_diff = 0
        for _ in range(4000):
            keys = [(rng.choice(["rna", "object", "scene"]), f"p{k}") for k in range(rng.randint(1, 6))]
            a = {k: _values(rng) for k in keys}
            b = {k: _mutate(rng, v) for k, v in a.items()} if rng.random() < 0.7 else {k: v for k, v in a.items()}
            if rng.random() < 0.2:
                b = dict(reversed(list(b.items())))
            got, want = datarec._same_values(a, b), _same_values_before_r070(a, b)
            assert got == want or (got and not datarec._diff(a, b)), (a, b, got, want)
            n_same += got
            n_diff += not got
        assert n_same > 500 and n_diff > 1000, (n_same, n_diff)
        # what the recorder holds: 60 objects, a third with a constraint, captured, changed, captured again
        prefs.FORCE["record_scope"] = "EVERYTHING"
        obs = []
        for i in range(60):
            bpy.ops.mesh.primitive_cube_add(size=0.3, location=(i * 0.5, 0, 0))
            ob = C.active_object
            if i % 3 == 0:
                ob.constraints.new("COPY_ROTATION")
            obs.append(ob)
        before = [datarec.capture(ob) for ob in obs]
        calls.clear()
        for ob, cap in zip(obs, before):
            assert datarec._same_values(cap, datarec.capture(ob)) and _same_values_before_r070(cap, datarec.capture(ob))
        assert len(calls) == len(obs) and all(calls), "the engine decided every unchanged object"
        for i, ob in enumerate(obs):
            if i % 4 == 0:
                ob.location.x = -0.0 if ob.location.x == 0.0 else ob.location.x + 1e-6
            elif i % 4 == 1:
                ob.hide_render = not ob.hide_render
            elif i % 4 == 2:
                ob["prop"] = {"a": [1.0, 2.0], "b": 3}
        for i, (ob, cap) in enumerate(zip(obs, before)):
            now = datarec.capture(ob)
            got, want = datarec._same_values(cap, now), _same_values_before_r070(cap, now)
            assert got == want == (i % 4 == 3), (i, got, want)
    finally:
        datarec._STRICT_SAME = real


def test_fast_compare_only_with_exact_adapters():
    """An adapter with its own comparison (or a test's fault injection) always goes through _diff."""
    H.fresh()
    assert datarec._fast_diff_ok()
    a = adapters.ADAPTERS["rna"]
    a.changed = lambda path, old, new: True
    try:
        assert not datarec._fast_diff_ok()
    finally:
        del a.changed
    assert datarec._fast_diff_ok()


def test_normalise_matches_the_plain_path():
    H.fresh()
    ob = bpy.data.objects.new("o", bpy.data.meshes.new("m"))
    C.scene.collection.objects.link(ob)
    ob.location = (-0.0, 1e-30, float("nan"))
    ob.rotation_mode = "QUATERNION"
    ob.rotation_quaternion = (0.5, -0.0, 0.25, 1.0)
    vals = [ob.location, ob.rotation_euler, ob.rotation_quaternion, ob.scale, ob.matrix_parent_inverse,
            ob.matrix_world, ob.rotation_axis_angle, ob.data, None, 1, 1.5, True, "x", ob.parent,
            mathutils.Color((0.1, -0.0, 1.0)), mathutils.Vector((1.0, 2.0)), [ob.location, 1], (1, 2.0, True),
            C.scene.render.resolution_x, ob.hide_viewport, ob.data.vertices, b"ab", [[1.0, -0.0], [None, "s"]]]
    for v in vals:
        a, b = old_normalise(v), adapters.normalise(v)
        assert struct_repr(a) == struct_repr(b), (v, a, b)
    for o in list(C.scene.objects):
        for path, _k, _c in adapters.ALLOWLIST["Object"]:
            v = o.path_resolve(path)
            assert struct_repr(old_normalise(v)) == struct_repr(adapters.normalise(v)), path


def test_users_collection_table_matches_blender():
    """In a frame (and past the first few objects, when the table is built) the collections of every
    object are Blender's, in Blender's order: nested collections, several scenes, scene collections."""
    H.fresh()
    s1 = C.scene
    s2 = bpy.data.scenes.new("Other")
    cols = [bpy.data.collections.new(f"C{i}") for i in range(5)]
    s1.collection.children.link(cols[0])
    cols[0].children.link(cols[1])
    s2.collection.children.link(cols[2])
    cols[2].children.link(cols[3])                      # cols[4] is in no scene
    obs = []
    for i in range(30):
        ob = bpy.data.objects.new(f"O{i}", None)
        for j, c in enumerate([s1.collection, s2.collection] + cols):
            if (i >> j) & 1 or (i % 7 == j):
                c.objects.link(ob)
        obs.append(ob)
    want = [ob.users_collection for ob in obs]
    with idmap.frame():
        got = [adapters.users_collection(ob) for ob in obs]
        assert idmap.frame_cache().get("users_collection") is not None
    assert got == want
    assert [adapters.users_collection(ob) for ob in obs] == want          # outside a frame: Blender's


def test_find_in_a_frame_matches_find():
    """Lookups by id in a frame give what plain lookups give: cached and not cached, ids copied by
    a duplicate (the first by name wins a search, the cached one a cached lookup), renamed objects,
    an id given out inside the frame, scenes, collections, meshes, ids no one has."""
    H.fresh()
    prefs.FORCE["record_scope"] = "EVERYTHING"
    obs = []
    for i in range(12):
        bpy.ops.mesh.primitive_cube_add(location=(i, 0, 0))
        C.active_object.name = f"K{i:02d}"
        obs.append(C.active_object)
    uids = [idmap.claim(o) for o in obs]
    coll = bpy.data.collections.new("Coll")
    C.scene.collection.children.link(coll)
    extra = [idmap.claim(C.scene), idmap.claim(coll), idmap.claim(obs[0].data), "ffffffffffffffff", ""]
    # a copy carrying an id (not claimed yet), named so it comes first
    dup = obs[3].copy()
    C.scene.collection.objects.link(dup)
    dup.name = "A_copy"
    obs[5].name = "Z_renamed"

    def lookups():
        return [(None if x is None else x.as_pointer()) for x in (idmap.find(u) for u in uids + extra)]
    for warm in (False, True):
        if not warm:
            idmap.forget()
        plain = lookups()
        if not warm:
            idmap.forget()
        with idmap.frame():
            framed = lookups()
            fresh_ob = bpy.data.objects.new("New", None)        # not in the frame's index yet
            C.scene.collection.objects.link(fresh_ob)
            nu = idmap.claim(fresh_ob)
            assert idmap.find(nu) == fresh_ob
            nu2 = idmap.claim(dup)                    # the copy may get its own id now
            assert idmap.find(nu2) == dup
            after = lookups()                        # the index follows the new id
        assert framed == plain, (warm, framed, plain)
        assert after == lookups(), warm
        bpy.data.objects.remove(fresh_ob)


def test_scene_uids_match_the_plain_check():
    H.fresh()
    prefs.FORCE["record_scope"] = "EVERYTHING"
    s2 = bpy.data.scenes.new("S2")
    ob = bpy.data.objects.new("o", None)
    C.scene.collection.objects.link(ob)
    ids = [idmap.claim(C.scene), idmap.claim(s2), idmap.claim(ob), idmap.claim(bpy.data.collections.new("c")),
           "0000000000000000"]
    for _ in range(2):
        datarec._SCENE_UIDS.clear()
        plain = [old_is_scene_uid(u) for u in ids]
        datarec._SCENE_UIDS.clear()
        assert [datarec._is_scene_uid(u) for u in ids] == plain
        datarec._SCENE_UIDS.clear()
        assert datarec._scene_uids_in(ids) == [u for u, p in zip(ids, plain) if p]


# ----------------------------------------------------------------------------- whole recordings
def _sequences():
    seqs = T.sequences()
    return [(row, seqs[row]) for row in ("obj.transforms", "obj.parenting", "obj.constraints",
                                          "obj.collections", "obj.addremove", "obj.editsteps")]


def test_recordings_are_byte_for_byte_the_plain_ones():
    """Every Tier 1 operation sequence recorded with the shortcuts and without: the same steps, byte
    for byte, the same cursors and recorded values."""
    for row, ops in _sequences():
        fast = record(ops, plain=False)
        plain = record(ops, plain=True)
        assert fast["everything"] == plain["everything"], row
        assert fast["hist"].keys() == plain["hist"].keys(), row
        for hid in fast["hist"]:
            assert fast["hist"][hid] == plain["hist"][hid], (row, hid)
        assert fast["values"] == plain["values"], row
        assert sum(len(v[2]) for v in fast["hist"].values()) >= len(ops), row


def test_recordings_with_undo_and_redo_are_the_plain_ones():
    """Blender's undo and redo after operations (the recorder re-reads everything after each): the
    same in both."""
    for row, ops in _sequences()[:5]:
        at = set(range(0, len(ops), 3))
        fast = record(ops, plain=False, undo_at=at)
        plain = record(ops, plain=True, undo_at=at)
        assert fast == plain, row


def test_prime_reads_the_same_baselines():
    """prime() (after a file is loaded) reads each owner's mesh once: the states are the same as
    reading it twice."""
    with fixed_ids():
        L.fresh_scene()
        T.duplicate("B", linked=True).run()
        L.track_all()
        L.tick_like("baseline")

        def states():
            return {u: (sorted(st.counts.items()), {k: bytes(memoryview(a)) for k, a in st.arrays.items()},
                        sorted((repr(k), struct_repr(v)) for k, v in st.values.items()))
                    for u, st in runtime._STATES.items()}

        def reset():
            for st in runtime._STATES.values():
                st.values = {}
            datarec.forget()
            idmap.forget()
        reset()
        datarec.prime()
        fast = states()
        reset()
        datarec.refresh_mesh_users()                 # what prime did before
        for uid in datarec.tracked_uids():
            idb = idmap.find(uid)
            if idb is not None:
                datarec.baseline(idb, uid, refresh=True)
        assert states() == fast


# ----------------------------------------------------------------------------- cases a shortcut could miss
def _hist(name):
    return runtime.reg().history_for(idmap.uid_of(bpy.data.objects[name]))


def _n(name):
    h = _hist(name)
    return len(h.entries) if h is not None else 0


def test_change_right_after_an_operator_that_changed_nothing():
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    n = _n("A")
    for _ in range(3):
        L.tick_like("Select")                    # nothing changed: no step
    assert _n("A") == n
    a = bpy.data.objects["A"]
    a.location.x += 0.001
    L.tick_like("Move")
    assert _n("A") == n + 1
    step = _hist("A").entries[-1].load()
    ch = {(r.adapter, p): v for r in step.records for p, v in r.changes.items()}
    assert ch[("rna", "location")][1][0] == a.location.x
    a.location.x = -0.0                          # a change only the sign bit shows
    L.tick_like("Move")
    a.location.x = 0.0
    L.tick_like("Move")
    assert _n("A") == n + 3


_FAKE_OP = [0]


def _finished_tick():
    """What the timer does when an operator has finished. Headless no operator is ever registered, so
    Blender's operator list gains one here: an emptied list is no operation ending (D147)."""
    _FAKE_OP[0] += 1
    real = datarec._ops_marker
    datarec._OPS["marker"] = (0, 0)
    datarec._ops_marker = lambda: (1, _FAKE_OP[0])
    try:
        datarec.tick()
    finally:
        datarec._ops_marker = real


def _tick_state():
    return (dict(datarec._DIRTY), dict(datarec._MESH_OWNER), dict(datarec._MESH_USERS), dict(datarec._MESH_OF),
            set(datarec._MESH_DIRTY), datarec._OPS["marker"],
            {hid: len(h.entries) for hid, h in runtime.reg().histories.items()})


def _idle_tick_run(full: bool):
    # each run starts from the default Record: the first run's Sculpting only must not reach the second run's
    # fresh scene, where turning Hindsight on opens the file (#125) and the default gives ids out (#105)
    prefs.FORCE["record_scope"] = None
    L.fresh_scene()
    T.duplicate("B", linked=True).run()
    L.track_all()
    L.tick_like("baseline")
    datarec._DIRTY[idmap.uid_of(bpy.data.objects["A"])] = 0.0
    prefs.FORCE["record_scope"] = "SCULPT"
    if full:                                     # what the timer did before the shortcut
        datarec._OPS["marker"] = datarec._ops_marker()
        datarec.finish_operation("Op")
    else:
        _finished_tick()
        datarec._OPS["marker"] = datarec._ops_marker()
    st = _tick_state()
    return (st[0], set(st[1].values()), set(map(tuple, st[2].values())), set(st[3]), st[4], st[6])


def test_tick_with_nothing_recorded_leaves_what_the_full_path_left():
    """With Record set to Sculpting only (or nothing switched on) the timer reads nothing, and leaves
    the recorder as finish_operation and commit would have."""
    out = []
    for full in (False, True):
        with fixed_ids():
            out.append(_idle_tick_run(full))
    assert out[0][1:] == out[1][1:] and not out[0][0] and not out[1][0], out


def test_changes_while_off_then_on():
    """Changes made while Record was Sculpting only are recorded as one step by the first operator
    after it is set to Everything again (as before the shortcut), never lost."""
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    n = _n("A")
    prefs.FORCE["record_scope"] = "SCULPT"
    a = bpy.data.objects["A"]
    a.location.y += 1.0
    C.view_layer.update()
    _finished_tick()
    datarec._OPS["marker"] = datarec._ops_marker()
    assert _n("A") == n
    prefs.FORCE["record_scope"] = "EVERYTHING"
    _finished_tick()
    assert _n("A") == n + 1
    ch = {p for r in _hist("A").entries[-1].load().records for p in r.changes}
    assert "location" in ch


def test_many_objects_changed_by_one_operator():
    L.fresh_scene()
    for i in range(40):
        bpy.ops.mesh.primitive_cube_add(size=0.2, location=(i * 0.3, 3, 0))
        C.active_object.name = f"M{i}"
    L.track_all()
    L.tick_like("baseline")
    names = [o.name for o in C.scene.objects]
    before = {nm: _n(nm) for nm in names}
    T.translate_multi(names, (0.0, 0.0, 0.25)).run()
    steps = datarec.finish_operation("Move all")
    assert all(_n(nm) == before[nm] + 1 for nm in names), [nm for nm in names if _n(nm) != before[nm] + 1]
    ops = {s.extra.get("op", s.seq) for s in steps}
    assert len(ops) == 1, "one operation on the global timeline"


def test_rename_is_recorded_and_found_again():
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    a = bpy.data.objects["A"]
    uid = idmap.uid_of(a)
    n = _n("A")
    a.name = "Renamed"
    L.tick_like("Rename")
    assert idmap.find(uid) == a
    h = runtime.reg().history_for(uid)
    assert len(h.entries) == n + 1
    ch = {(r.adapter, p): v for r in h.entries[-1].load().records for p, v in r.changes.items()}
    assert ch[("rna", "name")] == ("A", "Renamed")
    a.location.z += 1                            # the cached name is stale now: found by id anyway
    L.tick_like("Move")
    assert len(h.entries) == n + 2


def test_deleted_object_and_a_new_one_with_its_name():
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    old = bpy.data.objects["A"]
    old_uid = idmap.uid_of(old)
    scene_uid = idmap.uid_of(C.scene)
    T.delete(["A"]).run()
    bpy.ops.mesh.primitive_cube_add(location=(5, 5, 0))
    C.active_object.name = "A"
    new = C.active_object
    L.tick_like("Delete and add")
    new_uid = idmap.uid_of(new)
    assert new_uid is not None and new_uid != old_uid
    assert idmap.find(old_uid) is None and idmap.find(new_uid) == new
    sh = runtime.reg().history_for(scene_uid)
    ch = {p: v for r in sh.entries[-1].load().records for p, v in r.changes.items()}
    assert f"body:{old_uid}" in ch and f"body:{new_uid}" in ch, sorted(ch)
    # back and forth over the step: the old A comes back, the new one goes
    runtime._goto_history(sh, len(sh.entries) - 1, True)
    assert idmap.find(new_uid) is None and idmap.find(old_uid) is not None
    runtime._goto_history(sh, len(sh.entries), True)
    assert idmap.find(old_uid) is None and idmap.find(new_uid) is not None


def test_undo_then_a_change():
    """After Blender's undo every value is read again; a change right after it is still a step."""
    L.fresh_scene()
    L.track_all()
    bpy.ops.ed.undo_push(message="start")
    L.tick_like("baseline")
    T.translate("A", (1, 0, 0)).run()
    bpy.ops.ed.undo_push(message="move")
    L.tick_like("move")
    n = _n("A")
    bpy.ops.ed.undo()
    assert _hist("A").cursor == n - 1
    bpy.ops.ed.redo()
    assert _hist("A").cursor == n
    bpy.ops.ed.undo()
    T.translate("A", (0, 2, 0)).run()
    L.tick_like("move again")
    h = _hist("A")
    assert h.cursor == len(h.entries) and h.entries[-1].load().kind == DATA


for fn in (test_same_values_never_hides_a_difference, test_same_values_answers_as_before_r070,
           test_fast_compare_only_with_exact_adapters,
           test_normalise_matches_the_plain_path, test_users_collection_table_matches_blender,
           test_find_in_a_frame_matches_find, test_scene_uids_match_the_plain_check,
           test_recordings_are_byte_for_byte_the_plain_ones, test_recordings_with_undo_and_redo_are_the_plain_ones,
           test_prime_reads_the_same_baselines, test_change_right_after_an_operator_that_changed_nothing,
           test_tick_with_nothing_recorded_leaves_what_the_full_path_left, test_changes_while_off_then_on,
           test_many_objects_changed_by_one_operator, test_rename_is_recorded_and_found_again,
           test_deleted_object_and_a_new_one_with_its_name, test_undo_then_a_change):
    H.run(fn)
H.finish()
