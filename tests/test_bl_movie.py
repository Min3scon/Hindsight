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


for f in [test_quick_auto_key_and_playback, test_off_pauses_and_keeps_keys, test_needs_global_history,
          test_manual_step_value, test_unregister_removes_handler]:
    H.run(f)
H.finish()
