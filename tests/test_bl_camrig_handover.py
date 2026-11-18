"""Movie Mode gives the user's camera back (review S3-1).

The camera Hindsight's camera stands in for is remembered as the object itself
(scene.hindsight.movie_user_camera), not by name: renamed, it still comes back; deleted, the scene is
left without a camera (and the memory never keeps the deleted object in a saved file); picked by the
user while Movie Mode is on, their choice stays; linked from a library, it comes back; Blender's undo
across turning Movie Mode on and off, and saving and reopening with Movie Mode on, keep it right; a
file from an earlier build (the name only) still gives it back; Remove Hindsight data leaves nothing.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile

import bpy

import tests.harness as H
from hindsight.bl import camrig, rigmark, runtime

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hs_camrig_")


def scene_with_camera(name="MyCam"):
    H.fresh()
    ob = H.make_mesh(400, "Model")
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    assert H.commit(ob)
    runtime.set_global(C, True)
    cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    C.scene.collection.objects.link(cam)
    C.scene.camera = cam
    return cam


def tick():
    camrig._LAST[0] = -1e9
    camrig.tick()


def ours():
    return camrig.rig(C.scene).get("camera")


def test_renamed_camera_comes_back():
    cam = scene_with_camera()
    users = cam.users
    sh = C.scene.hindsight
    sh.movie_mode = True
    assert C.scene.camera is ours()
    cam.name = "Renamed"
    tick()
    sh.movie_mode = False
    assert C.scene.camera == cam and cam.name == "Renamed", "the renamed camera isn't given back"
    assert sh.movie_user_camera is None and sh.movie_prev_camera == ""
    assert cam.users == users, "the memory of the camera is dropped once it is given back"


def test_deleted_camera_leaves_the_scene_empty():
    cam = scene_with_camera()
    sh = C.scene.hindsight
    sh.movie_mode = True
    bpy.data.objects.remove(cam)
    sh.movie_mode = False
    assert C.scene.camera is None and sh.movie_user_camera is None


def _delete_with_operator(ob):
    for o in C.view_layer.objects:
        o.select_set(False)
    ob.select_set(True)
    C.view_layer.objects.active = ob
    assert bpy.ops.object.delete() == {'FINISHED'}


def test_deleted_camera_not_kept_in_the_file():
    for with_tick in (True, False):
        cam = scene_with_camera()
        sh = C.scene.hindsight
        sh.movie_mode = True
        H.enter(bpy.data.objects["Model"], "OBJECT")
        _delete_with_operator(cam)
        # Blender keeps an object deleted from the scene while something still points at it
        if with_tick:
            tick()
            assert sh.movie_user_camera is None, "forgotten as soon as it is in no scene"
        path = os.path.join(TMP, f"deleted_{with_tick}.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)        # save_pre forgets it when no tick ran yet
        bpy.ops.wm.open_mainfile(filepath=path)
        assert bpy.data.objects.get("MyCam") is None, "the deleted camera was saved in the file"
        sc = bpy.context.scene
        assert sc.hindsight.movie_mode and sc.camera is camrig.rig(sc)["camera"]
        sc.hindsight.movie_mode = False
        assert sc.camera is None


def test_camera_picked_by_the_user_wins():
    cam = scene_with_camera()
    other = bpy.data.objects.new("Other", bpy.data.cameras.new("Other"))
    C.scene.collection.objects.link(other)
    sc = C.scene
    sh = sc.hindsight
    sh.movie_mode = True
    sc.camera = other
    assert not camrig.needs_attention(sc)
    tick()
    assert sc.camera is other, "the timer doesn't take the camera back"
    sh.movie_view_angle = 0.5                               # a setting changed: still their choice
    bpy.ops.hindsight.movie_frame()
    assert sc.camera is other
    # ours deleted and put back by the timer: their choice stays
    bpy.data.objects.remove(ours())
    tick()
    assert ours() is not None and sc.camera is other
    sh.movie_mode = False
    assert sc.camera is other and sh.movie_user_camera is None, "nothing restored over their choice"
    assert cam.name in sc.objects
    # Movie Mode's camera switched off and on while Movie Mode stays on: a user action, it takes over
    sh.movie_mode = True
    assert sc.camera is ours() and sh.movie_user_camera == other
    sh.movie_camera = False
    assert sc.camera is other
    sh.movie_camera = True
    assert sc.camera is ours() and sh.movie_user_camera == other
    # the user picks the remembered camera itself again: it stays and nothing changes when off
    sc.camera = other
    tick()
    sh.movie_mode = False
    assert sc.camera is other and sh.movie_user_camera is None


def test_our_camera_deleted_then_back_keeps_the_memory():
    cam = scene_with_camera()
    sc = C.scene
    sh = sc.hindsight
    sh.movie_mode = True
    bpy.data.objects.remove(ours())
    assert sc.camera is None and camrig.needs_attention(sc)
    tick()
    assert sc.camera is ours() and sh.movie_user_camera == cam
    sh.movie_mode = False
    assert sc.camera == cam
    # Movie Mode off while ours is missing: the user's camera still comes back
    sh.movie_mode = True
    bpy.data.objects.remove(ours())
    sh.movie_mode = False
    assert sc.camera == cam and sh.movie_user_camera is None


def test_linked_camera():
    scene_with_camera()
    lib_path = os.path.join(TMP, "lib.blend")
    libcam = bpy.data.objects.new("LibCam", bpy.data.cameras.new("LibCam"))
    bpy.data.libraries.write(lib_path, {libcam})
    bpy.data.objects.remove(libcam)
    with bpy.data.libraries.load(lib_path, link=True) as (src, dst):
        dst.objects = ["LibCam"]
    linked = dst.objects[0]
    assert linked.library is not None
    local_same_name = bpy.data.objects.new("LibCam", None)   # a local object with the same name
    C.scene.collection.objects.link(local_same_name)
    C.scene.collection.objects.link(linked)
    sc = C.scene
    sc.camera = linked
    sc.hindsight.movie_mode = True
    assert sc.camera is ours() and sc.hindsight.movie_user_camera == linked
    sc.hindsight.movie_mode = False
    assert sc.camera == linked and sc.camera.library is not None


def test_undo_across_on_and_off():
    scene_with_camera()
    H.enter(bpy.data.objects["Model"], "OBJECT")
    bpy.ops.ed.undo_push(message="start")
    C.scene.hindsight.movie_mode = True
    bpy.ops.ed.undo_push(message="on")
    C.scene.hindsight.movie_mode = False
    bpy.ops.ed.undo_push(message="off")
    sc = C.scene
    assert sc.camera.name == "MyCam"
    bpy.ops.ed.undo()                                       # back to Movie Mode on
    sc = C.scene
    assert sc.hindsight.movie_mode and sc.camera is camrig.rig(sc)["camera"]
    assert sc.hindsight.movie_user_camera.name == "MyCam"
    bpy.ops.ed.undo()                                       # back to before
    sc = C.scene
    assert not sc.hindsight.movie_mode and sc.camera.name == "MyCam" and not camrig.rig(sc)
    bpy.ops.ed.redo()
    sc = C.scene
    assert sc.camera is camrig.rig(sc)["camera"]
    bpy.data.objects["MyCam"].name = "Renamed after undo"
    sc.hindsight.movie_mode = False
    assert sc.camera.name == "Renamed after undo" and sc.hindsight.movie_user_camera is None
    assert not [o for o in bpy.data.objects if rigmark.is_rig(o)]


def test_save_and_reload_with_movie_mode_on():
    scene_with_camera()
    sh = C.scene.hindsight
    sh.movie_mode = True
    path = os.path.join(TMP, "movie_on.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    sc = bpy.context.scene
    cam = bpy.data.objects["MyCam"]
    assert sc.hindsight.movie_mode and sc.camera is camrig.rig(sc)["camera"]
    assert sc.hindsight.movie_user_camera == cam
    cam.name = "Renamed after reload"
    sc.hindsight.movie_mode = False
    assert sc.camera == cam and sc.hindsight.movie_user_camera is None


def test_file_from_an_earlier_build():
    """Only the name was stored: it is read when the pointer is empty, and turned into the object."""
    for with_tick in (False, True):
        cam = scene_with_camera()
        sc = C.scene
        sh = sc.hindsight
        sh.movie_mode = True
        sh.movie_user_camera = None
        sh.movie_prev_camera = "MyCam"
        if with_tick:
            tick()
            assert sh.movie_user_camera == cam and sh.movie_prev_camera == ""
            cam.name = "Renamed"
        sh.movie_mode = False
        assert sc.camera == cam and sh.movie_prev_camera == "" and sh.movie_user_camera is None


def test_remove_hindsight_data_leaves_nothing():
    from hindsight.bl import cleanup
    for delete_first in (False, True):
        cam = scene_with_camera()
        users = cam.users
        sc = C.scene
        sc.hindsight.movie_mode = True
        if delete_first:
            H.enter(bpy.data.objects["Model"], "OBJECT")
            _delete_with_operator(cam)
        cleanup.remove()
        assert not [o for o in bpy.data.objects if rigmark.is_rig(o)]
        assert not cleanup._has_settings(sc)
        if delete_first:
            assert cam.users == 0, "nothing keeps the deleted camera"
            assert sc.camera is None
            path = os.path.join(TMP, "removed.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            assert bpy.data.objects.get("MyCam") is None
        else:
            assert sc.camera == cam and cam.users == users


for f in [test_renamed_camera_comes_back, test_deleted_camera_leaves_the_scene_empty,
          test_deleted_camera_not_kept_in_the_file, test_camera_picked_by_the_user_wins,
          test_our_camera_deleted_then_back_keeps_the_memory, test_linked_camera,
          test_undo_across_on_and_off, test_save_and_reload_with_movie_mode_on,
          test_file_from_an_earlier_build, test_remove_hindsight_data_leaves_nothing]:
    H.run(f)
shutil.rmtree(TMP, ignore_errors=True)
H.finish()
