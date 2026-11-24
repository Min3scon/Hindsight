import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from hindsight.bl import runtime

C = bpy.context


def build():
    """Global history of 4 steps across 2 objects: A1, B1, A2, B2. Returns (a, b, states)."""
    H.fresh()
    a = H.make_mesh(1200, "A", (-2, 0, 0))
    b = H.make_mesh(1200, "B", (2, 0, 0))
    H.enter(a, "SCULPT")
    sa = [H.snapshot(a)]
    H.enter(b, "SCULPT")
    sb = [H.snapshot(b)]
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 1); assert H.commit(a) is not None; sa.append(H.snapshot(a))
    H.enter(b, "SCULPT")
    H.fake_stroke(b, 2); assert H.commit(b) is not None; sb.append(H.snapshot(b))
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 3); assert H.commit(a) is not None; sa.append(H.snapshot(a))
    H.enter(b, "SCULPT")
    H.fake_stroke(b, 4); assert H.commit(b) is not None; sb.append(H.snapshot(b))
    states = [(sa[0], sb[0]), (sa[1], sb[0]), (sa[1], sb[1]), (sa[2], sb[1]), (sa[2], sb[2])]
    runtime.set_global(C, True)
    info = runtime.scrub_info(C)
    assert info["global"] and info["len"] == 4 and info["pos"] == 4
    return a, b, states


def lengths():
    return sorted((h.uid, len(h.entries)) for h in runtime.reg().all())


def check(a, b, states, g, msg=""):
    H.assert_same(H.snapshot(a), states[g][0], f"{msg} A@{g}")
    H.assert_same(H.snapshot(b), states[g][1], f"{msg} B@{g}")


def step_fcurve(scene):
    ad = scene.animation_data
    assert ad is not None and ad.action is not None, "no action"
    from hindsight.bl.movie import action_fcurves
    fc = action_fcurves(ad).find("hindsight.movie_step")
    assert fc is not None, "no Step F-Curve"
    return fc


def test_quick_auto_key_and_playback():
    a, b, states = build()
    sc = C.scene
    sh = sc.hindsight
    before = lengths()
    sh.movie_mode = True
    assert runtime.scrub_info(C)["locked"] is True, "scrubber locked in Movie Mode"
    runtime.goto(C, 1)
    assert runtime.scrub_info(C)["pos"] == 4, "scrubber goto refused while locked"
    assert not getattr(runtime.goto, "_movie", False)
    sc.frame_start = 1
    sc.frame_end = 5
    sh.frames_per_step = 2
    sc.frame_set(1)
    assert bpy.ops.hindsight.quick_auto_key() == {'FINISHED'}
    fc = step_fcurve(sc)
    kps = list(fc.keyframe_points)
    assert len(kps) == 2, len(kps)
    assert tuple(kps[0].co) == (1.0, 0.0), tuple(kps[0].co)
    assert tuple(kps[1].co) == (9.0, 4.0), tuple(kps[1].co)
    assert all(k.interpolation == 'LINEAR' for k in kps)
    assert sc.frame_end == 9, sc.frame_end
    # re-keying replaces the keys instead of piling them up
    assert bpy.ops.hindsight.quick_auto_key() == {'FINISHED'}
    assert len(step_fcurve(sc).keyframe_points) == 2
    seen = set()
    for f in [1, 3, 5, 7, 9, 2, 4, 6, 8, 5, 1, 9, 3]:
        sc.frame_set(f)
        if f % 2 == 1:
            g = (f - 1) // 2
            assert sh.movie_step == g, (f, sh.movie_step)
        g = runtime.scrub_info(C)["pos"]
        assert g == sh.movie_step, (f, g, sh.movie_step)
        check(a, b, states, g, f"frame {f}")
        seen.add(g)
        assert lengths() == before, "Movie Mode never changes history"
    assert seen == {0, 1, 2, 3, 4}
    assert not getattr(runtime.goto, "_movie", False)
    assert runtime.scrub_info(C)["len"] == 4


def test_off_pauses_and_keeps_keys():
    a, b, states = build()
    sc = C.scene
    sh = sc.hindsight
    sh.movie_mode = True
    sc.frame_start = 1
    sh.frames_per_step = 3
    sc.frame_set(1)
    bpy.ops.hindsight.quick_auto_key()
    kps = list(step_fcurve(sc).keyframe_points)
    assert [tuple(k.co) for k in kps] == [(1.0, 0.0), (13.0, 4.0)]
    sc.frame_set(7)
    check(a, b, states, 2)
    before = lengths()
    sh.movie_mode = False
    assert runtime.scrub_info(C)["locked"] is False
    for f in (1, 13, 4):
        sc.frame_set(f)
        check(a, b, states, 2, "movie off")
    assert len(step_fcurve(sc).keyframe_points) == 2, "keyframes stay"
    assert lengths() == before
    # back on: the Timeline drives the mesh again
    sh.movie_mode = True
    sc.frame_set(13)
    check(a, b, states, 4)
    sc.frame_set(1)
    check(a, b, states, 0)
    # the normal scrubber works again once Movie Mode is off
    sh.movie_mode = False
    runtime.goto(C, 3)
    check(a, b, states, 3)
    assert lengths() == before


def test_needs_global_history():
    a, b, states = build()
    sc = C.scene
    sh = sc.hindsight
    sh.movie_mode = True
    sc.frame_start = 1
    sh.frames_per_step = 1
    sc.frame_set(1)
    bpy.ops.hindsight.quick_auto_key()
    sc.frame_set(3)
    check(a, b, states, 2)
    runtime.set_global(C, False)
    assert runtime.scrub_info(C) is None or runtime.scrub_info(C)["locked"] is False
    before = lengths()
    snap_a, snap_b = H.snapshot(a), H.snapshot(b)
    for f in (1, 5, 2):
        sc.frame_set(f)
        H.assert_same(H.snapshot(a), snap_a, "use_global off")
        H.assert_same(H.snapshot(b), snap_b, "use_global off")
    sh.movie_step = 0
    H.assert_same(H.snapshot(a), snap_a)
    assert lengths() == before
    assert not bpy.ops.hindsight.quick_auto_key.poll(), "Quick Auto Key needs global history"


def test_manual_step_value():
    a, b, states = build()
    sh = C.scene.hindsight
    before = lengths()
    sh.movie_step = 1                       # movie mode off: nothing happens
    check(a, b, states, 4)
    sh.movie_mode = True
    sh.movie_step = 1
    check(a, b, states, 1)
    sh.movie_step = 3
    check(a, b, states, 3)
    sh.movie_step = 99                      # clamped to the end
    check(a, b, states, 4)
    sh.movie_step = 0
    check(a, b, states, 0)
    # manual keyframes on Step drive playback too
    sc = C.scene
    sh.movie_step = 2
    sc.keyframe_insert(data_path="hindsight.movie_step", frame=10)
    sh.movie_step = 4
    sc.keyframe_insert(data_path="hindsight.movie_step", frame=20)
    sc.frame_set(10)
    check(a, b, states, 2)
    sc.frame_set(20)
    check(a, b, states, 4)
    assert lengths() == before


def test_unregister_removes_handler():
    H.fresh()
    from hindsight.bl import movie
    assert movie.on_frame_change in bpy.app.handlers.frame_change_post
    import hindsight
    hindsight.unregister()
    assert movie.on_frame_change not in bpy.app.handlers.frame_change_post
    assert not hasattr(bpy.types, "HINDSIGHT_OT_quick_auto_key")
    hindsight.register()


def build_n(n):
    """Global history of n steps on one object. Returns (obj, states), states[g] = the mesh at g."""
    H.fresh()
    a = H.make_mesh(1200, "A")
    H.enter(a, "SCULPT")
    states = [H.snapshot(a)]
    for k in range(n):
        H.fake_stroke(a, 10 + k)
        assert H.commit(a) is not None
        states.append(H.snapshot(a))
    runtime.set_global(C, True)
    assert runtime.scrub_info(C)["len"] == n
    return a, states


def key(fps=1, spf=1, start=1):
    sc = C.scene
    sh = sc.hindsight
    sh.movie_mode = True
    sc.frame_start = start
    sh.frames_per_step = fps
    sh.movie_steps_per_frame = spf
    sc.frame_set(start)
    assert bpy.ops.hindsight.quick_auto_key() == {'FINISHED'}
    return [tuple(k.co) for k in step_fcurve(sc).keyframe_points]


def implied(keys, f):
    """The step the two LINEAR keys give at whole frame f (Blender rounds Int values half up)."""
    import math
    (f0, v0), (f1, v1) = keys
    return int(math.floor(v0 + (v1 - v0) * (f - f0) / (f1 - f0) + 0.5))


def test_steps_per_frame_one_keeps_keys():
    build()
    for fps, end in ((2, 9.0), (3, 13.0), (1, 5.0)):
        assert key(fps, 1) == [(1.0, 0.0), (end, 4.0)], (fps, key(fps, 1))


def test_steps_per_frame_four():
    import math
    n = 10
    a, states = build_n(n)
    sc = C.scene
    sc.frame_end = 2
    assert key(1, 1) == [(1.0, 0.0), (11.0, 10.0)]
    assert sc.frame_end == 11
    keys = key(1, 4)
    assert keys == [(1.0, 0.0), (1.0 + math.ceil(n / 4), 10.0)], keys
    assert sc.frame_end == 1 + math.ceil(n / 4), "the range ended at the old last key, so it gets shorter"
    seen = []
    for f in list(range(1, sc.frame_end + 1)) + [2, 1, 4]:
        sc.frame_set(f)
        g = implied(keys, f)
        assert sc.hindsight.movie_step == g, (f, sc.hindsight.movie_step, g)
        assert runtime.scrub_info(C)["pos"] == g
        H.assert_same(H.snapshot(a), states[g], f"frame {f}")
        seen.append(g)
    assert seen[:4] == [0, 3, 7, 10], seen
    # the last frame always shows the last step, also when Steps per frame doesn't divide the steps
    for fps, spf in ((1, 3), (2, 3), (1, 7), (3, 4), (1, 10), (1, 25)):
        keys = key(fps, spf)
        sc.frame_set(int(keys[1][0]))
        H.assert_same(H.snapshot(a), states[n], f"last frame, {fps} frames per step, {spf} steps per frame")
        assert runtime.scrub_info(C)["pos"] == n
    # a range the user made longer than the movie is kept
    sc.frame_end = 50
    key(1, 4)
    assert sc.frame_end == 50


def _counting(name):
    orig = getattr(runtime, name)
    calls = []

    def counted(*a, **k):
        calls.append(a[1:2])
        return orig(*a, **k)
    counted.__dict__.update(orig.__dict__)
    setattr(runtime, name, counted)

    def restore():
        orig.__dict__.update(counted.__dict__)
        setattr(runtime, name, orig)
    return calls, restore


def test_same_step_does_no_history_work():
    a, b, states = build()
    sc = C.scene
    key(3, 1)                                   # frames 1, 4, 7, 10, 13 start a step
    sc.frame_set(4)
    check(a, b, states, 1)
    gotos, r1 = _counting("goto")
    infos, r2 = _counting("scrub_info")
    try:
        for f in (4, 5, 4, 5):                  # frames 4 and 5 show step 1
            sc.frame_set(f)
        assert gotos == [] and infos == [], (gotos, infos)
        sc.frame_set(7)
        assert len(gotos) == 1, gotos
        del gotos[:], infos[:]
        sc.frame_set(8)
        assert gotos == [] and infos == [], (gotos, infos)
    finally:
        r1()
        r2()
    check(a, b, states, 2)
    # a history moved outside Movie Mode is noticed: the same frame moves it back
    sc.hindsight.movie_mode = False
    runtime.goto(C, 0)
    check(a, b, states, 0)
    sc.hindsight.movie_mode = True
    sc.frame_set(8)
    check(a, b, states, 2)


def test_subframes_keep_the_frames_step():
    a, b, states = build()
    sc = C.scene
    keys = key(3, 1)
    for f in range(1, 14):
        g = implied(keys, f)
        sc.frame_set(f)
        check(a, b, states, g, f"frame {f}")
        gotos, restore = _counting("goto")
        try:
            # what a render with motion blur does: the frame, then samples on either side of it
            for ff, sub in ((f, 0.5), (f - 1, 0.75), (f, 0.25), (f, 0.0)):
                sc.frame_set(ff, subframe=sub)
                check(a, b, states, g, f"frame {f} at {ff} + {sub}")
            assert gotos == [], (f, gotos)
        finally:
            restore()
    # a sub-frame call on its own shows the step of its whole frame
    for f, far in ((2, 13), (5, 1), (12, 1), (3, 13)):
        sc.frame_set(far)
        sc.frame_set(f, subframe=0.5)
        check(a, b, states, implied(keys, f), f"frame {f} + 0.5 after frame {far}")
    # the history moved while Movie Mode was off: a sub-frame of the same frame puts it back
    sc.frame_set(7)
    sc.hindsight.movie_mode = False
    runtime.goto(C, 0)
    sc.hindsight.movie_mode = True
    sc.frame_set(7, subframe=0.25)
    check(a, b, states, implied(keys, 7), "sub-frame after the history moved")


def test_playback_keeps_history_and_position():
    from hindsight.bl import persistence
    a, b, states = build()
    sc = C.scene
    keys = key(2, 1)
    before = lengths()
    j = persistence.current_journal()
    n0 = len(j.read()) if j is not None else 0
    for f in list(range(1, 10)) + [6]:
        sc.frame_set(f)
    g = implied(keys, 6)
    check(a, b, states, g)
    assert lengths() == before, "playback never adds steps"
    sc.hindsight.movie_mode = False
    assert runtime.scrub_info(C)["pos"] == g
    from hindsight.core import globaltl
    plan = globaltl.plan_global_goto(runtime.reg(), g)
    assert {h.uid: h.cursor for h in runtime.reg().all()} == plan
    j = persistence.current_journal()
    recs = j.read()[n0:]
    assert not [r for r in recs if r[0] != "CURSOR"], [r[0] for r in recs]
    last = {}
    for _rt, header, _p in recs:
        last[header["hist"]] = header["cursor"]
    for h in runtime.reg().all():
        assert last.get(h.uid, h.cursor) == h.cursor, (h.uid, last, h.cursor)
    # the stored cursors replay to the mesh that is shown
    for uid, cur in plan.items():
        assert runtime.reg().histories[uid].cursor == cur


def test_movie_length():
    import math
    from hindsight.bl import movie
    a, b, states = build()
    sc = C.scene
    sh = sc.hindsight
    sc.render.fps, sc.render.fps_base = 24, 1.0
    sh.frames_per_step, sh.movie_steps_per_frame = 2, 1
    assert movie.movie_length(sc) == {"steps": 4, "frames": 9, "seconds": 9 / 24}
    sh.frames_per_step, sh.movie_steps_per_frame = 1, 3
    assert movie.movie_length(sc) == {"steps": 4, "frames": 3, "seconds": 3 / 24}
    sc.render.fps, sc.render.fps_base = 30, 1.001
    r = movie.movie_length(sc)
    assert r["frames"] == 3 and math.isclose(r["seconds"], 3 / (30 / 1.001), rel_tol=1e-6), r  # fps_base is a float32
    # it matches what Quick auto key keys
    keys = key(1, 3)
    assert keys[1][0] - keys[0][0] + 1 == movie.movie_length(sc)["frames"]
    runtime.set_global(C, False)
    assert movie.movie_length(sc) is None
    H.fresh()
    runtime.set_global(C, True)
    assert movie.movie_length(C.scene) is None, "no global history yet"


def test_new_step_after_playback_plays():
    a, b, states = build()
    sc = C.scene
    keys = key(1, 1)
    for f in range(1, 6):
        sc.frame_set(f)
    check(a, b, states, 4)
    kept = runtime._GROUPS.get("groups")
    assert kept is not None, "the global timeline is kept while Movie Mode plays"
    sc.hindsight.movie_mode = False
    runtime.scrub_info(C)
    assert runtime._GROUPS.get("groups") is kept, "and between redraws when Movie Mode is off"
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 99)
    assert H.commit(a) is not None
    s5 = H.snapshot(a)
    assert runtime.scrub_info(C)["len"] == 5
    keys = key(1, 1)
    assert keys == [(1.0, 0.0), (6.0, 5.0)], keys
    sc.frame_set(1)
    check(a, b, states, 0)
    sc.frame_set(6)
    H.assert_same(H.snapshot(a), s5, "the new step plays")
    H.assert_same(H.snapshot(b), states[4][1])
    sc.frame_set(5)
    check(a, b, states, 4)
    assert runtime.scrub_info(C) == dict(runtime.scrub_info(C), pos=4, len=5)
    # a step added while Movie Mode stays on (a script) is seen too
    sc.frame_set(6)
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 98)
    assert H.commit(a) is not None
    s6 = H.snapshot(a)
    assert runtime.scrub_info(C)["len"] == 6
    assert key(1, 1) == [(1.0, 0.0), (7.0, 6.0)]
    sc.frame_set(1)
    check(a, b, states, 0)
    sc.frame_set(7)
    H.assert_same(H.snapshot(a), s6, "the step added in Movie Mode plays")


def test_frame_lists_objects_once():
    """Listing the visible objects walks every object in the file. A frame that shows the step
    already shown lists them once; a frame that moves the history lists them once in Movie Mode
    (and once more inside the jump while runtime.goto can't take the list)."""
    from hindsight.bl import movie
    a, b, states = build()
    for i in range(40):                         # other objects, never recorded
        e = bpy.data.objects.new(f"E{i}", bpy.data.meshes.new(f"E{i}"))
        C.scene.collection.objects.link(e)
    sc = C.scene
    key(2, 1)                                   # frames 2 and 3 show step 1, 4 and 5 step 2
    sc.frame_set(2)
    check(a, b, states, 1)
    calls, restore = _counting("_visible_uids")
    try:
        sc.frame_set(3)                         # still step 1
        assert len(calls) == 1, calls
        del calls[:]
        sc.frame_set(4)                         # step 2
        assert len(calls) == (1 if movie._GOTO_TAKES_VIS else 2), calls
        check(a, b, states, 2)
        del calls[:]
        sc.frame_set(9)
        assert len(calls) == (1 if movie._GOTO_TAKES_VIS else 2), calls
        check(a, b, states, 4)
    finally:
        restore()


def test_visibility_change_on_the_same_frame():
    """What the jump was planned from includes which objects are visible: excluding an object
    (Collection Filtering) between two calls for the same frame plans again, as the scrubber does."""
    a, b, states = build()
    sa = [states[0][0], states[1][0], states[3][0]]
    sb = [states[0][1], states[2][1], states[4][1]]
    sc = C.scene
    sh = sc.hindsight
    hide = bpy.data.collections.new("Hide")
    sc.collection.children.link(hide)
    hide.objects.link(b)
    for c in list(b.users_collection):
        if c != hide:
            c.objects.unlink(b)
    key(1, 1)                                   # frames 1 to 5 show steps 0 to 4
    sc.frame_set(5)
    check(a, b, states, 4)
    sh.filter_mode = 'BLACKLIST'
    sh.filter_collections.add().collection = hide
    sc.frame_set(5)                             # B's steps leave the timeline: A's 2 steps, at the end
    assert runtime.scrub_info(C)["len"] == 2
    H.assert_same(H.snapshot(a), sa[2])
    H.assert_same(H.snapshot(b), sb[2])
    sc.frame_set(2)                             # Step 1: A's first step; B is not on the timeline
    H.assert_same(H.snapshot(a), sa[1])
    H.assert_same(H.snapshot(b), sb[2])
    sh.filter_mode = 'NONE'
    sc.frame_set(2)                             # the same frame, B back on the timeline: Step 1 is A1
    check(a, b, states, 1)
    sc.frame_set(4)
    check(a, b, states, 3)


def test_quick_auto_key_with_data_no_object_uses():
    """#104: with Record on Everything, a file with data no object uses (a material library; in the
    window the startup file's unused Grease Pencil materials) lost its timelapse. Movie Mode stopped
    reading that data, so it counted as deleted: a step at each jump's position, and after Quick auto
    key's jump to step 0 every later step was thrown away and every frame showed step 0 (window
    scenario movie_mode)."""
    from hindsight.bl import datarec
    from tests import t1_gate_lib as L
    H.fresh()
    loose = bpy.data.materials.new("Loose")     # no object uses it: kept with a fake user
    loose.use_fake_user = True
    datarec.record_file_default()               # what opening a file does: Everything in this file
    a = H.make_mesh(1200, "A")
    L.tick_like("add A")
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 1); assert H.commit(a) is not None
    H.fake_stroke(a, 2); assert H.commit(a) is not None
    end = H.snapshot(a)
    H.enter(a, "OBJECT")
    runtime.set_global(C, True)
    sc, n = C.scene, runtime.scrub_info(C)["len"]
    sc.hindsight.movie_mode = True
    L.tick_like("Movie Mode on")
    assert runtime.scrub_info(C)["len"] == n, ("turning Movie Mode on is no step", n, runtime.scrub_info(C))
    sc.hindsight.frames_per_step = 1
    sc.frame_set(sc.frame_start)
    assert bpy.ops.hindsight.quick_auto_key() == {'FINISHED'}
    assert "A" not in sc.objects, "step 0: before A was added"
    L.tick_like("Quick auto key")               # what the timer does once the operator has finished
    assert runtime.scrub_info(C)["len"] == n, ("no step at step 0, none lost", n, runtime.scrub_info(C))
    sc.frame_set(sc.frame_start + n)
    assert "A" in sc.objects, "the last frame shows the last step"
    H.assert_same(H.snapshot(bpy.data.objects["A"]), end, "A as it was at the end")
    assert loose.name in bpy.data.materials


def test_lamps_follow_the_users_light_on_every_frame():
    """#104: with Record on Everything a timelapse can bring the user's deleted light back (step 0) and
    take it away again. Movie lighting's lamps follow in the same frame: before, only the timer put
    them back, and it waits for a render to end, so every frame after the light's deletion rendered
    without any light (window scenario movie_mode)."""
    from hindsight.bl import camrig, datarec
    from tests import t1_gate_lib as L
    H.fresh()
    sc = C.scene
    light = bpy.data.objects.new("Light", bpy.data.lights.new("Light", "POINT"))
    sc.collection.objects.link(light)
    datarec.record_file_default()
    bpy.data.objects.remove(light)
    L.tick_like("Delete")
    H.make_mesh(1200, "A")
    L.tick_like("add A")
    runtime.set_global(C, True)
    sh = sc.hindsight
    sh.movie_camera = sh.movie_lighting = True
    sh.movie_mode = True
    sh.movie_step = runtime.scrub_info(C)["len"]
    camrig.ensure(sc)
    lamps = set(camrig.LIGHT_ROLES)
    assert lamps <= set(camrig.rig(sc)), camrig.rig(sc)
    sh.frames_per_step = 1
    sc.frame_set(sc.frame_start)
    assert bpy.ops.hindsight.quick_auto_key() == {'FINISHED'}
    for f, own in ((sc.frame_start, True), (sc.frame_start + 2, False), (sc.frame_start, True),
                   (sc.frame_start + 1, False)):
        sc.frame_set(f)
        assert camrig.scene_has_own_light(sc) == own, (f, own)
        assert (lamps & set(camrig.rig(sc))) == (set() if own else lamps), (f, sorted(camrig.rig(sc)))


def test_a_change_waiting_to_be_recorded_survives_a_jump_back():
    """#107: a change still waiting to settle when Movie Mode jumps back (Quick auto key on the first
    frame) is recorded first, at the end. Before, the recorder kept it after the jump, at step 0, as a
    new step: every step after step 0 was thrown away, the object added in it never came back and the
    timelapse rendered step 0 on every frame."""
    from hindsight.bl import datarec, idmap
    from tests import t1_gate_lib as L
    H.fresh()
    datarec.record_file_default()               # what opening a file does: Everything in this file
    a = H.make_mesh(1200, "A")
    L.tick_like("add A")
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 1); assert H.commit(a) is not None
    H.fake_stroke(a, 2); assert H.commit(a) is not None
    end = H.snapshot(a)
    H.enter(a, "OBJECT")
    runtime.set_global(C, True)
    sc = C.scene
    n = runtime.scrub_info(C)["len"]
    assert n == 3, n
    sc.hindsight.movie_mode = True
    sc["take"] = 2.0                            # changed just now: waiting to settle
    datarec._DIRTY[idmap.uid_of(sc)] = datarec._now()
    sc.frame_set(sc.frame_start)
    assert bpy.ops.hindsight.quick_auto_key() == {'FINISHED'}
    assert "A" not in sc.objects, "step 0: before A was added"
    datarec.commit(label="Change", settled=True)   # what the timer does once changes settle
    last = runtime.scrub_info(C)["len"]
    assert last == n + 1, ("the change is one more step at the end; none is lost", n, last)
    sc.hindsight.movie_step = last
    assert "A" in sc.objects, "A comes back"
    H.assert_same(H.snapshot(bpy.data.objects["A"]), end, "A as it was at the end")
    assert sc.get("take") == 2.0


def test_a_stroke_at_the_end_moves_the_step_value():
    """#107: a stroke made at the end while Movie Mode is on moves the Step value onto it. Before, the
    Step value stayed one step back: the next playback jumped back one step and, with Record on
    Everything, a later change (render settings in the window scenario movie_mode) then threw the
    stroke away."""
    from hindsight.bl import datarec, idmap
    from tests import t1_gate_lib as L
    H.fresh()
    datarec.record_file_default()               # what opening a file does: Everything in this file
    a = H.make_mesh(1200, "A")
    L.tick_like("add A")
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 1); assert H.commit(a) is not None
    runtime.set_global(C, True)
    sc, sh = C.scene, C.scene.hindsight
    n = runtime.scrub_info(C)["len"]
    sh.movie_mode = True
    sh.movie_step = n                           # the end
    H.fake_stroke(a, 2); assert H.commit(a) is not None
    s1 = H.snapshot(a)
    sc.frame_set(sc.frame_current + 1)          # playback, before the timer has run
    H.assert_same(H.snapshot(a), s1, "playback keeps the stroke on screen")
    assert sh.movie_step == n + 1, ("the Step value follows the stroke", n, sh.movie_step)
    H.fake_stroke(a, 3); assert H.commit(a) is not None
    s2 = H.snapshot(a)
    runtime.tick()                              # the timer sees this stroke first
    assert sh.movie_step == n + 2, ("the timer moves the Step value", n, sh.movie_step)
    sc.frame_set(sc.frame_current + 1)
    H.assert_same(H.snapshot(a), s2, "playback keeps the second stroke")
    sc["take"] = 2.0                            # a later change (the scenario: render settings), one more step
    datarec._DIRTY[idmap.uid_of(sc)] = datarec._now()
    datarec.commit(label="Change", settled=True)
    assert runtime.scrub_info(C)["len"] == n + 3, ("no stroke thrown away", n, runtime.scrub_info(C))
    H.assert_same(H.snapshot(a), s2)


def test_undo_of_a_followed_stroke_sticks():
    """#108 review: Ctrl+Z of a stroke the Step value has followed moves the Step value back with it.
    Before, sculpt undo moved the history back without a new step number, the Step value stayed on
    the stroke and the next frame change (or the timer and then a frame change) put the stroke back."""
    from hindsight.bl import datarec, meshio
    from tests import t1_gate_lib as L
    H.fresh()
    datarec.record_file_default()
    a = H.make_mesh(1200, "A")
    L.tick_like("add A")
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 1); assert H.commit(a) is not None
    runtime.set_global(C, True)
    sc, sh = C.scene, C.scene.hindsight
    n = runtime.scrub_info(C)["len"]
    sh.movie_mode = True
    sh.movie_step = n
    s0 = H.snapshot(a)

    def undo():                                 # what Blender's sculpt undo does, then its handler
        meshio.write_changes(a.data, s0, ["position|POINT|FLOAT_VECTOR"])
        runtime.on_undo()
        assert runtime.scrub_info(C)["pos"] == n, ("undo moves the history back", runtime.scrub_info(C))

    H.fake_stroke(a, 2); assert H.commit(a) is not None
    sc.frame_set(sc.frame_current + 1)          # the frame change follows the stroke
    assert sh.movie_step == n + 1, sh.movie_step
    undo()
    sc.frame_set(sc.frame_current + 1)
    H.assert_same(H.snapshot(a), s0, "the undo sticks after a frame change")
    assert sh.movie_step == n, ("the Step value follows the undo", n, sh.movie_step)
    assert runtime.scrub_info(C)["pos"] == n
    H.fake_stroke(a, 3); assert H.commit(a) is not None
    runtime.tick()                              # the timer follows the stroke
    assert sh.movie_step == n + 1, sh.movie_step
    undo()
    runtime.tick()
    assert sh.movie_step == n, ("the timer follows the undo", n, sh.movie_step)
    sc.frame_set(sc.frame_current + 1)
    H.assert_same(H.snapshot(a), s0, "the undo sticks after the timer and a frame change")
    assert runtime.scrub_info(C)["pos"] == n


def test_undo_of_a_followed_change_sticks_in_object_mode():
    """#108 review: the same for a change Record everything records in Object Mode (datarec's undo)."""
    from hindsight.bl import datarec
    from tests import t1_gate_lib as L
    H.fresh()
    datarec.record_file_default()
    a = H.make_mesh(1200, "A")
    L.tick_like("add A")
    runtime.set_global(C, True)
    sc, sh = C.scene, C.scene.hindsight
    n = runtime.scrub_info(C)["len"]
    sh.movie_mode = True
    sh.movie_step = n
    a.location.x = 3.0
    L.tick_like("Move")
    sc.frame_set(sc.frame_current + 1)
    assert sh.movie_step == n + 1, sh.movie_step
    a.location.x = 0.0                          # what Blender's undo puts back, then its handler
    datarec.on_undo("undo")
    assert runtime.scrub_info(C)["pos"] == n, runtime.scrub_info(C)
    sc.frame_set(sc.frame_current + 1)
    assert a.location.x == 0.0, "the undo sticks after a frame change"
    assert sh.movie_step == n and runtime.scrub_info(C)["pos"] == n, (n, sh.movie_step, runtime.scrub_info(C))


def test_an_operator_that_just_ended_survives_a_jump_back():
    """#108 review: an operator that ended since the timer last ran is recorded before a jump, at the
    end. Hiding sends no update, so it never waits to settle; before, the timer recorded it after the
    jump, at the new position, and threw away every step after it."""
    from hindsight.bl import datarec
    from tests import t1_gate_lib as L
    H.fresh()
    datarec.record_file_default()
    a = H.make_mesh(1200, "A")
    L.tick_like("add A")
    runtime.set_global(C, True)
    k = runtime.scrub_info(C)["len"]
    for x in (1.0, 2.0):
        a.location.x = x
        L.tick_like("Move")
    sh = C.scene.hindsight
    n = runtime.scrub_info(C)["len"]
    assert n == k + 2, (k, n)
    sh.movie_mode = True
    sh.movie_step = n
    real = datarec._ops_marker
    datarec._OPS["marker"] = (0, 0)
    datarec._ops_marker = lambda: (1, 4242)    # Hide ended (the operator list gained one) ...
    try:
        a.hide_set(True)
        sh.movie_step = k                       # ... and the user jumps back before the timer runs
        assert not a.hide_get() and a.location.x == 0.0, "step k: A shown, where it was added"
        datarec.tick()                          # the timer
    finally:
        datarec._ops_marker = real
    last = runtime.scrub_info(C)["len"]
    assert last == n + 1, ("Hide is one more step at the end; none is lost", n, last)
    sh.movie_step = last
    assert a.hide_get() and a.location.x == 2.0, (a.hide_get(), a.location.x)


def test_a_change_waiting_in_sculpt_mode_survives_a_jump_back():
    """#108 review: a sculpt change still waiting to settle (a filter run from a menu, a script) is
    recorded before a jump, at the end, as one waiting in Record everything is."""
    from hindsight.bl import datarec
    from tests import t1_gate_lib as L
    H.fresh()
    datarec.record_file_default()
    a = H.make_mesh(1200, "A")
    L.tick_like("add A")
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 1); assert H.commit(a) is not None
    H.fake_stroke(a, 2); assert H.commit(a) is not None
    runtime.set_global(C, True)
    sh = C.scene.hindsight
    n = runtime.scrub_info(C)["len"]
    sh.movie_mode = True
    sh.movie_step = n
    H.fake_stroke(a, 3)
    runtime._DIRTY[runtime.obj_uid(a)] = runtime.time.time()   # waiting to settle
    end = H.snapshot(a)
    sh.movie_step = n - 2
    for uid in list(runtime._DIRTY):            # what the timer does once it settles
        runtime.commit(runtime.find_object(uid))
    last = runtime.scrub_info(C)["len"]
    assert last == n + 1, ("the change is one more step at the end; none is lost", n, last)
    sh.movie_step = last
    H.assert_same(H.snapshot(a), end, "the change at the end")


for f in [test_quick_auto_key_and_playback, test_off_pauses_and_keeps_keys, test_needs_global_history,
          test_manual_step_value, test_unregister_removes_handler, test_steps_per_frame_one_keeps_keys,
          test_steps_per_frame_four, test_same_step_does_no_history_work, test_subframes_keep_the_frames_step,
          test_playback_keeps_history_and_position, test_movie_length, test_new_step_after_playback_plays,
          test_frame_lists_objects_once, test_visibility_change_on_the_same_frame,
          test_quick_auto_key_with_data_no_object_uses, test_lamps_follow_the_users_light_on_every_frame,
          test_a_change_waiting_to_be_recorded_survives_a_jump_back,
          test_a_stroke_at_the_end_moves_the_step_value, test_undo_of_a_followed_stroke_sticks,
          test_undo_of_a_followed_change_sticks_in_object_mode, test_an_operator_that_just_ended_survives_a_jump_back,
          test_a_change_waiting_in_sculpt_mode_survives_a_jump_back]:
    H.run(f)
H.finish()
