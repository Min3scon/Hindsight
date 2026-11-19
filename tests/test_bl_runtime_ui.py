"""What the slider and the step list ask for on every redraw: the same answers as before they were
made cheaper. Run: <bpy python> tests/test_bl_runtime_ui.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy  # noqa: E402
import tests.harness as H  # noqa: E402
from hindsight.bl import rigmark, runtime  # noqa: E402

C = bpy.context


def _reference_visible():
    """_visible_uids as it was: runtime.is_excluded asked for every object."""
    real = runtime._exclusion_test
    runtime._exclusion_test = lambda: runtime.is_excluded
    try:
        return runtime._visible_uids()
    finally:
        runtime._exclusion_test = real


def test_visible_uids_same_answers():
    H.fresh()
    obs = []
    for i in range(6):
        ob = H.make_mesh(300, name=f"O{i}", location=(i * 3, 0, 0))
        H.enter(ob, "SCULPT")
        H.fake_stroke(ob, i + 1)
        assert runtime.commit(ob) is not None
        H.enter(ob, "OBJECT")
        obs.append(ob)
    rigmark.mark(obs[5], "camera")              # Hindsight's own Movie Mode object: left out
    coll = bpy.data.collections.new("Listed")
    C.scene.collection.children.link(coll)
    for ob in obs[:2]:
        coll.objects.link(ob)
    inner = bpy.data.collections.new("Inner")
    coll.children.link(inner)
    inner.objects.link(obs[2])
    p = C.scene.hindsight
    seen = {}
    for mode, listed in (("NONE", False), ("BLACKLIST", False), ("BLACKLIST", True),
                         ("WHITELIST", True), ("WHITELIST", False), ("NONE", True)):
        p.filter_collections.clear()
        if listed:
            it = p.filter_collections.add()
            it.collection = coll
        p.filter_mode = mode
        got, want = runtime._visible_uids(), _reference_visible()
        assert got == want, (mode, listed, got ^ want)
        seen[(mode, listed)] = len(got)
    uids = [runtime.obj_uid(o) for o in obs]
    assert seen[("NONE", False)] == 5 and uids[5] not in runtime._visible_uids()
    assert seen[("BLACKLIST", True)] == 2, seen      # O3, O4 (O5 is the rig)
    assert seen[("WHITELIST", True)] == 3, seen      # O0, O1, O2 (nested)
    assert seen[("WHITELIST", False)] == 0, seen


def _reference_step_list(context, around=12):
    """step_list as it was: every step's label built, then the window taken."""
    from hindsight.core import globaltl  # noqa: F401
    out = []
    if runtime.use_global(context):
        vis = runtime._visible_uids()
        groups = runtime._groups(vis)
        cur = runtime._position(vis, groups)
        labels = []
        for grp in groups:
            _seq, hid, i = grp[0]
            h = runtime.reg().histories.get(hid)
            labels.append(h.entries[i].meta.get("label", "Step") if h else "Step")
    else:
        h = runtime.history_of(runtime._ctx_active(context))
        if h is None:
            return []
        cur = h.cursor
        labels = [e.meta.get("label", "Step") for e in h.entries]
    n = len(labels)
    lo, hi = max(0, cur - around), min(n, cur + around)
    for i in range(hi, lo, -1):
        out.append((i, labels[i - 1], i == cur))
    if lo == 0:
        out.append((0, "Start", cur == 0))
    return out


def _two_objects(strokes=30):
    H.fresh()
    obs = []
    for k in range(2):
        ob = H.make_mesh(300, name=f"S{k}", location=(k * 3, 0, 0))
        H.enter(ob, "SCULPT")
        for i in range(strokes):
            H.fake_stroke(ob, 100 * k + i)
            assert runtime.commit(ob, label=f"S{k} stroke {i}") is not None
        H.enter(ob, "OBJECT")
        obs.append(ob)
    return obs


def test_step_list_same_rows():
    obs = _two_objects()
    C.view_layer.objects.active = obs[0]
    for glob in (False, True):
        runtime.set_global(C, glob)
        n = runtime.scrub_info(C)["len"]
        for pos in (n, n - 1, n // 2, 13, 12, 11, 1, 0):
            runtime.goto(C, pos)
            for around in (12, 6, 1):
                got, want = runtime.step_list(C, around), _reference_step_list(C, around)
                assert got == want, (glob, pos, around)
    runtime.set_global(C, False)


def _brute_memory():
    total = steps = 0
    ah = runtime.history_of(runtime._ctx_active(C))
    ab = asn = 0
    for h in runtime.reg().all():
        b = sum(e.nbytes() for e in h.entries)
        total += b
        steps += len(h.entries)
        if h is ah:
            ab, asn = b, len(h.entries)
    # R069: a packed topology counts at its packed size (read without unpacking it)
    base = sum(sum(a.nbytes for a in st.light.values()) + sum(len(p[0]) for p in (st._topo or {}).values())
               for st in runtime._STATES.values())
    return {"total_bytes": total, "ram_bytes": total + base, "steps": steps,
            "objects": len(runtime.reg().by_object), "active_bytes": ab, "active_steps": asn}


def test_memory_stats_same_numbers():
    from hindsight.core.types import SETTINGS
    obs = _two_objects(70)                          # more than the 64 raw steps: some get packed
    checks = [lambda: None,
              lambda: runtime.packer.drain(60),
              lambda: runtime.goto(C, 10),
              lambda: setattr(C.view_layer.objects, "active", obs[1]),
              lambda: runtime.reset_history(C, obs[1])]
    C.view_layer.objects.active = obs[0]
    for fn in checks:
        fn()
        assert runtime.memory_stats() == _brute_memory(), fn
        assert runtime.memory_stats() == _brute_memory(), "asked twice"
    H.enter(obs[0], "SCULPT")
    old = SETTINGS.max_steps
    try:
        SETTINGS.max_steps = 20                     # the cap drops steps
        H.fake_stroke(obs[0], 999)
        runtime.commit(obs[0])
        assert runtime.memory_stats() == _brute_memory()
    finally:
        SETTINGS.max_steps = old
    h = runtime.history_of(obs[0])
    e = next(x for x in h.entries if not x.is_packed())
    runtime.packer.submit(e, "zlib")                # the packer changes one step's size
    runtime.packer.drain(60)
    assert e.is_packed() and runtime.memory_stats() == _brute_memory()


def _fresh_answer():
    return runtime.scrub_info(C)


def _check_cache(what):
    C.view_layer.update()                           # Blender runs the depsgraph before it redraws
    got = runtime.scrub_info(C, cached=True)
    assert got == _fresh_answer(), (what, got, _fresh_answer())


def test_scrub_info_cache_never_stale():
    from hindsight.core.types import SETTINGS
    obs = _two_objects(8)
    runtime.set_global(C, True)
    C.view_layer.objects.active = obs[0]
    _check_cache("start")
    runtime.goto(C, 5)
    _check_cache("jump")
    H.enter(obs[1], "SCULPT")
    H.fake_stroke(obs[1], 77)
    runtime.commit(obs[1])
    _check_cache("stroke (later steps dropped)")
    H.enter(obs[1], "OBJECT")
    old = SETTINGS.max_steps
    SETTINGS.max_steps = 3
    _check_cache("step cap")
    SETTINGS.max_steps = old
    p = C.scene.hindsight
    coll = bpy.data.collections.new("Skip")
    C.scene.collection.children.link(coll)
    coll.objects.link(obs[0])
    p.filter_mode = "BLACKLIST"
    _check_cache("filter mode")
    it = p.filter_collections.add()
    it.collection = coll
    _check_cache("collection listed")
    coll.objects.unlink(obs[0])
    _check_cache("object moved out of the listed collection")
    coll.objects.link(obs[1])
    _check_cache("object moved into the listed collection")
    p.filter_mode = "NONE"
    _check_cache("filter off")
    # Setting a custom property sends no depsgraph update by itself; Movie Mode marks the objects it
    # creates (the object count changes). Here the object is tagged as an edit in the UI would be.
    rigmark.mark(obs[1], "camera")
    obs[1].update_tag()
    _check_cache("marked as a Movie Mode object")
    rigmark.unmark(obs[1])
    obs[1].update_tag()
    _check_cache("mark taken off")
    p.movie_mode = True
    _check_cache("Movie Mode on")
    p.movie_mode = False
    _check_cache("Movie Mode off")
    runtime.delete_past(C, obs[0])
    _check_cache("delete earlier steps")
    bpy.data.objects.remove(obs[1])
    _check_cache("object deleted")
    runtime.set_global(C, False)
    assert runtime.scrub_info(C, cached=True) == _fresh_answer(), "per object"
    runtime.set_global(C, True)
    _check_cache("global again")
    sc2 = bpy.data.scenes.new("Other")
    win_scene = C.window.scene if C.window else None
    if win_scene is not None:
        C.window.scene = sc2
        _check_cache("another scene")
        C.window.scene = win_scene
    runtime.set_registry(runtime.Registry())
    _check_cache("another file's histories")
    runtime.set_global(C, False)


for f in [test_visible_uids_same_answers, test_step_list_same_rows, test_memory_stats_same_numbers,
          test_scrub_info_cache_never_stale]:
    H.run(f)
H.finish()
