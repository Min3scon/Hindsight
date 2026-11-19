"""D116: with Record everything, the scene's settings are recorded in the scene's history: render and
output settings, frame range, units, colour management, EEVEE and Workbench settings, the active camera,
and the world (its settings and node tree). The current frame, the 3D cursor and tool settings are not
part of it (no step), and nothing is recorded while Movie Mode is on."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def _r(v):
    return tuple(round(x, 5) for x in v) if hasattr(v, "__len__") else round(v, 5)


def _bg(w):
    return next(n for n in w.node_tree.nodes if n.bl_idname == "ShaderNodeBackground")


def state():
    sc = C.scene
    w = sc.world
    return (sc.render.resolution_x, sc.render.fps, sc.frame_end, sc.render.engine,
            _r(sc.unit_settings.scale_length), sc.view_settings.view_transform,
            sc.render.image_settings.file_format, sc.camera.name if sc.camera else None,
            _r(tuple(_bg(w).inputs["Color"].default_value)), _r(_bg(w).inputs["Strength"].default_value))


def _len(idb):
    h = runtime.history_of(idb)
    return len(h.entries) if h is not None else 0


def setup():
    L.fresh_scene()
    sc = C.scene
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    if bpy.app.version < (5, 0, 0):
        sc.world.use_nodes = True
    if sc.camera is None:
        bpy.ops.object.camera_add(location=(0, -8, 2))
        sc.camera = C.active_object
    bpy.ops.object.camera_add(location=(8, 0, 2))
    C.active_object.name = "Cam2"
    L.track_all()
    L.tick_like("baseline")


def test_scene_settings_come_back():
    setup()
    sc = C.scene
    states = [state()]
    eng = "BLENDER_WORKBENCH" if sc.render.engine != "BLENDER_WORKBENCH" else "CYCLES"
    for label, fn in (
            ("resolution", lambda: setattr(sc.render, "resolution_x", 1280)),
            ("fps", lambda: setattr(sc.render, "fps", 30)),
            ("frame end", lambda: setattr(sc, "frame_end", 120)),
            ("engine", lambda: setattr(sc.render, "engine", eng)),
            ("units", lambda: setattr(sc.unit_settings, "scale_length", 0.01)),
            ("view transform", lambda: setattr(sc.view_settings, "view_transform", "Standard")),
            ("file format", lambda: setattr(sc.render.image_settings, "file_format", "JPEG")),
            ("camera", lambda: setattr(sc, "camera", bpy.data.objects["Cam2"])),
            ("world colour", lambda: setattr(_bg(sc.world).inputs["Color"], "default_value", (0.2, 0.4, 0.8, 1))),
            ("world strength", lambda: setattr(_bg(sc.world).inputs["Strength"], "default_value", 2.5))):
        fn()
        L.tick_like(label)
        states.append(state())
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(sc)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert state() in states, (pos, state())
    runtime._goto_history(h, 0, True)
    assert state() == states[0], (state(), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert state() == states[-1], (state(), states[-1])


def test_frame_cursor_and_tools_are_not_steps():
    setup()
    sc = C.scene
    n = _len(sc)
    sc.frame_set(17)
    L.tick_like("frame")
    sc.cursor.location = (1, 2, 3)
    L.tick_like("cursor")
    sc.tool_settings.use_snap = not sc.tool_settings.use_snap
    L.tick_like("snap")
    sc.frame_preview_start = 3
    L.tick_like("preview range")
    assert _len(sc) == n, (_len(sc), n)


def test_nothing_recorded_in_movie_mode():
    setup()
    sc = C.scene
    n = _len(sc)
    sc.hindsight.use_global = True
    sc.hindsight.movie_mode = True
    L.tick_like("movie on")
    sc.render.resolution_y = 600
    L.tick_like("while on")
    sc.hindsight.movie_mode = False
    L.tick_like("movie off")
    assert _len(sc) == n, (_len(sc), n)
    sc.render.resolution_y = 700
    L.tick_like("after")
    assert _len(sc) == n + 1


def _keys(x):
    ad = getattr(x, "animation_data", None)
    if ad is None or ad.action is None:
        return 0
    from hindsight.bl import adapters
    return sum(len(f.keyframe_points) for f in adapters._fcurves(ad.action, getattr(ad, "action_slot", None))
               if not f.data_path.startswith("hindsight."))


def test_scene_and_world_animation():
    """D125: keyframes on the scene's settings and on the world's node inputs are recorded in the
    scene's history; Movie Mode's own keys aren't."""
    setup()
    sc = C.scene
    read = lambda: (_keys(sc), _keys(sc.world.node_tree))
    states = [read()]
    for label, fn in (("scene key", lambda: sc.keyframe_insert("audio_volume", frame=1)),
                      ("world key", lambda: _bg(sc.world).inputs["Strength"].keyframe_insert("default_value", frame=1))):
        fn()
        L.tick_like(label)
        states.append(read())
    assert states == [(0, 0), (1, 0), (1, 1)], states
    h = runtime.history_of(sc)
    runtime._goto_history(h, h.cursor - 2, True)
    assert read() == (0, 0), read()
    runtime._goto_history(h, h.cursor + 2, True)
    assert read() == (1, 1), read()


if __name__ == "__main__":
    H.run(test_scene_settings_come_back)
    H.run(test_frame_cursor_and_tools_are_not_steps)
    H.run(test_nothing_recorded_in_movie_mode)
    H.run(test_scene_and_world_animation)
    H.finish()
