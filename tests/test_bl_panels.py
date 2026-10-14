import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Preferences sync, collection filtering logic/operators, sidebar panel registration and drawing."""

import traceback
from types import SimpleNamespace

import bpy

from hindsight.core import types as T
from hindsight.bl import filtering, prefs, ui_panel

ok = True
_tests = []


def test(fn):
    _tests.append(fn)
    return fn


def ensure_scene_props():
    """Use bl/props.py's scene.hindsight if registered, else a minimal stand-in (INTERFACES §6)."""
    if "hindsight" in bpy.types.Scene.bl_rna.properties:
        return
    from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                           PointerProperty)

    class HINDSIGHT_PG_coll_item_test(bpy.types.PropertyGroup):
        collection: PointerProperty(type=bpy.types.Collection)

    class HINDSIGHT_PG_scene_test(bpy.types.PropertyGroup):
        use_global: BoolProperty()
        preserve_modes: BoolProperty(default=True)
        movie_mode: BoolProperty()
        movie_step: IntProperty(name="Step")
        frames_per_step: IntProperty(default=2, min=1)
        filter_mode: EnumProperty(items=[('NONE', "Off", ""), ('BLACKLIST', "Blacklist", ""),
                                         ('WHITELIST', "Whitelist", "")], default='NONE')
        filter_collections: CollectionProperty(type=HINDSIGHT_PG_coll_item_test)
        filter_index: IntProperty()

    bpy.utils.register_class(HINDSIGHT_PG_coll_item_test)
    bpy.utils.register_class(HINDSIGHT_PG_scene_test)
    bpy.types.Scene.hindsight = PointerProperty(type=HINDSIGHT_PG_scene_test)


class FakeLayout:
    """Accepts any UILayout call, records labels/operators/props, returns another FakeLayout."""

    def __init__(self, log=None):
        self.__dict__["log"] = log if log is not None else []

    def __setattr__(self, k, v):
        self.__dict__[k] = v

    def __getattr__(self, name):
        def fn(*args, **kw):
            self.log.append((name, args, kw))
            return FakeLayout(self.log)
        return fn

    def texts(self, kind="label"):
        return [kw.get("text", "") for n, a, kw in self.log if n == kind]


def draw_panel(cls, context, header=False):
    lay = FakeLayout()
    fake_self = SimpleNamespace(layout=lay)
    (cls.draw_header if header else cls.draw)(fake_self, context)
    return lay


def link_new(name, coll):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def setup_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    scene = bpy.context.scene
    A = bpy.data.collections.new("A")
    B = bpy.data.collections.new("B")
    C = bpy.data.collections.new("C")
    scene.collection.children.link(A)
    scene.collection.children.link(B)
    A.children.link(C)
    objs = {
        "a": link_new("ObjA", A),
        "b": link_new("ObjB", B),
        "c": link_new("ObjC", C),
        "root": link_new("ObjRoot", scene.collection),
    }
    sh = scene.hindsight
    sh.filter_collections.clear()
    sh.filter_index = 0
    sh.filter_mode = 'NONE'
    return scene, A, B, C, objs


def set_list(scene, colls):
    sh = scene.hindsight
    sh.filter_collections.clear()
    for c in colls:
        sh.filter_collections.add().collection = c


def excl(objs, scene):
    return {k: filtering.is_excluded(o, scene) for k, o in objs.items()}


@test
def test_sync_settings():
    p = SimpleNamespace(save_mode='EMBED', crash_recovery='INTERRUPTED', truncation_threshold=3,
                        max_steps=500, memory_limit_mb=1024, raw_steps=8, compression='ZSTD',
                        scrub_height=9)
    saved = T.Settings(**vars(T.SETTINGS))
    try:
        prefs.sync_settings(p)
        s = T.SETTINGS
        assert s.save_mode == "EMBED" and s.crash_recovery == "INTERRUPTED"
        assert s.truncation_threshold == 3 and s.max_steps == 500
        assert s.memory_limit_mb == 1024 and s.raw_steps == 8
        assert s.compression == "zstd"
        prefs.sync_settings(SimpleNamespace(compression='AUTO'))   # partial objects are fine
        assert T.SETTINGS.compression == "auto" and T.SETTINGS.max_steps == 500
        prefs.sync_settings(None)
    finally:
        for k, v in vars(saved).items():
            setattr(T.SETTINGS, k, v)


@test
def test_prefs_class_definition():
    cls = prefs.HINDSIGHT_AP_prefs
    ann = cls.__annotations__
    for name in ("save_mode", "crash_recovery", "truncation_threshold", "max_steps",
                 "memory_limit_mb", "raw_steps", "compression", "scrub_height", "scrub_margin",
                 "scrub_color", "scrub_bg_color", "scrub_show_counter", "scrub_width_pct"):
        assert name in ann, name
        assert ann[name].keywords.get("update") is not None, name
    kw = ann["save_mode"].keywords
    assert kw["name"] == "Save history" and kw["default"] == 'SIDECAR'
    assert [i[0] for i in kw["items"]] == ["SIDECAR", "EMBED", "BOTH"]
    kw = ann["crash_recovery"].keywords
    assert kw["default"] == 'LAST_SAVED'
    assert [i[0] for i in kw["items"]] == ["LAST_SAVED", "INTERRUPTED"]
    assert ann["max_steps"].keywords["default"] == 10000 and ann["max_steps"].keywords["max"] == 10000
    assert ann["memory_limit_mb"].keywords["name"] == "Memory limit (MB)"
    assert ann["raw_steps"].keywords["name"] == "Uncompressed steps"
    assert "welcomed" in ann
    assert tuple(ann["scrub_color"].keywords["default"]) == (1.0, 0.6, 0.24, 1.0)
    assert ann["scrub_color"].keywords["subtype"] == 'COLOR_GAMMA'
    # update callback mirrors into SETTINGS
    saved = T.SETTINGS.raw_steps
    try:
        ann["raw_steps"].keywords["update"](SimpleNamespace(raw_steps=5), bpy.context)
        assert T.SETTINGS.raw_steps == 5
    finally:
        T.SETTINGS.raw_steps = saved
    # not registered as an add-on here -> None, never raises
    assert prefs.get_prefs() is None


@test
def test_prefs_register_unregister():
    prefs.register("hindsight_test_pkg")
    assert prefs.HINDSIGHT_AP_prefs.bl_idname == "hindsight_test_pkg"
    assert prefs.get_prefs() is None          # no add-on entry for that name
    prefs.unregister()


@test
def test_filter_none():
    scene, A, B, C, objs = setup_scene()
    set_list(scene, [A])
    scene.hindsight.filter_mode = 'NONE'
    assert not any(excl(objs, scene).values())


@test
def test_filter_blacklist():
    scene, A, B, C, objs = setup_scene()
    sh = scene.hindsight
    sh.filter_mode = 'BLACKLIST'
    set_list(scene, [])
    assert not any(excl(objs, scene).values())            # empty blacklist excludes nothing
    set_list(scene, [A])
    assert excl(objs, scene) == {"a": True, "b": False, "c": True, "root": False}   # C nested in A
    set_list(scene, [C])
    assert excl(objs, scene) == {"a": False, "b": False, "c": True, "root": False}
    set_list(scene, [A, B])
    assert excl(objs, scene) == {"a": True, "b": True, "c": True, "root": False}
    # object in several collections: excluded if any of them is listed
    B.objects.link(objs["root"])
    assert excl(objs, scene)["root"] is True
    # empty list rows are ignored
    sh.filter_collections.add()
    set_list(scene, [])
    sh.filter_collections.add()
    assert not any(excl(objs, scene).values())


@test
def test_filter_whitelist():
    scene, A, B, C, objs = setup_scene()
    sh = scene.hindsight
    sh.filter_mode = 'WHITELIST'
    set_list(scene, [])
    assert all(excl(objs, scene).values())                # empty whitelist excludes everything
    set_list(scene, [A])
    assert excl(objs, scene) == {"a": False, "b": True, "c": False, "root": True}
    set_list(scene, [C])
    assert excl(objs, scene) == {"a": True, "b": True, "c": False, "root": True}
    set_list(scene, [B, C])
    assert excl(objs, scene) == {"a": True, "b": False, "c": False, "root": True}
    # default scene argument
    bpy.context.window_manager  # noqa: just touching context
    assert filtering.is_excluded(objs["b"]) is False
    assert filtering.is_excluded(None, scene) is False


@test
def test_filter_operators():
    scene, A, B, C, objs = setup_scene()
    sh = scene.hindsight
    assert bpy.ops.hindsight.filter_remove.poll() is False
    assert bpy.ops.hindsight.filter_add(collection_name="A") == {'FINISHED'}
    assert len(sh.filter_collections) == 1 and sh.filter_collections[0].collection == A
    # duplicate is not added
    assert bpy.ops.hindsight.filter_add(collection_name="A") == {'CANCELLED'}
    assert len(sh.filter_collections) == 1
    # unknown name
    assert bpy.ops.hindsight.filter_add(collection_name="Nope") == {'CANCELLED'}
    # default = active collection
    vl = bpy.context.view_layer
    vl.active_layer_collection = vl.layer_collection.children["B"]
    assert bpy.ops.hindsight.filter_add() == {'FINISHED'}
    assert sh.filter_collections[1].collection == B and sh.filter_index == 1
    vl.active_layer_collection = vl.layer_collection.children["A"].children["C"]
    assert bpy.ops.hindsight.filter_add() == {'FINISHED'}
    assert [i.collection.name for i in sh.filter_collections] == ["A", "B", "C"]
    # remove the selected item
    sh.filter_index = 1
    assert bpy.ops.hindsight.filter_remove() == {'FINISHED'}
    assert [i.collection.name for i in sh.filter_collections] == ["A", "C"]
    assert sh.filter_index == 1
    assert bpy.ops.hindsight.filter_remove() == {'FINISHED'}
    assert [i.collection.name for i in sh.filter_collections] == ["A"] and sh.filter_index == 0
    assert bpy.ops.hindsight.filter_remove() == {'FINISHED'}
    assert len(sh.filter_collections) == 0
    assert bpy.ops.hindsight.filter_remove.poll() is False


@test
def test_panels_registered():
    for name in ("VIEW3D_PT_hindsight", "VIEW3D_PT_hindsight_movie", "VIEW3D_PT_hindsight_scrubber",
                 "VIEW3D_PT_hindsight_memory", "VIEW3D_PT_hindsight_filter", "VIEW3D_PT_hindsight_history",
                 "VIEW3D_PT_hindsight_help", "VIEW3D_PT_hindsight_warnings", "HINDSIGHT_UL_collections"):
        assert hasattr(bpy.types, name), name
    main = ui_panel.VIEW3D_PT_hindsight
    assert main.bl_category == "Undo History" and main.bl_label == "Undo History"
    assert main.bl_space_type == 'VIEW_3D' and main.bl_region_type == 'UI'
    assert not hasattr(main, "poll"), "the main panel must show in every mode, with or without history"
    labels = [c.bl_label for c in ui_panel.classes[1:]]
    assert labels == ["Warnings", "History options", "Movie Mode", "Collection Filtering", "Memory",
                      "On-screen slider", "Help"], labels
    assert all(c.bl_parent_id == "VIEW3D_PT_hindsight" for c in ui_panel.classes[1:])
    orders = [c.bl_order for c in ui_panel.classes]
    assert orders == sorted(orders), orders


class FakeRuntime:
    def __init__(self, hist=None, raise_all=False, warnings=(), recording=True, uid="u1", info=None):
        self.hist, self.raise_all, self.warn = hist, raise_all, list(warnings)
        self.recording, self.uid = recording, uid
        if info is None and hist is not None:
            info = {"pos": hist.cursor, "len": len(hist.entries), "cap": 10000, "global": False,
                    "locked": False, "morph_target": None}
        self.info = info

    def _chk(self):
        if self.raise_all:
            raise RuntimeError("boom")

    def history_of(self, obj):
        self._chk()
        return self.hist

    def scrub_info(self, context):
        self._chk()
        return self.info

    def is_excluded(self, obj):
        self._chk()
        return False

    def is_recording(self, obj):
        self._chk()
        return self.recording

    def obj_uid(self, obj):
        self._chk()
        return self.uid

    def memory_stats(self):
        self._chk()
        return {"total_bytes": 3 * 2**20 + 2**19, "ram_bytes": 2**20, "steps": 12, "objects": 2,
                "active_bytes": 2**20, "active_steps": 5}

    def warnings_for(self, context, obj):
        self._chk()
        return self.warn


def _joined(lay):
    return " ".join(lay.texts())


@test
def test_panel_draw_with_fake_layout():
    scene, A, B, C, objs = setup_scene()
    objs["a"].data.vertices.add(3)
    ctx = SimpleNamespace(scene=scene, active_object=objs["a"], view_layer=bpy.context.view_layer,
                          preferences=bpy.context.preferences, window_manager=bpy.context.window_manager,
                          region=None, area=None)
    orig = ui_panel._rt
    try:
        hist = SimpleNamespace(entries=[1] * 7, cursor=4)
        ui_panel._rt = lambda: FakeRuntime(hist, warnings=[("ERROR", "Bad thing"), ("WARNING", "Meh")])
        lay = draw_panel(ui_panel.VIEW3D_PT_hindsight, ctx)
        assert "Step 4 of 7, this object" in lay.texts(), lay.texts()
        ops = [a[0] for n, a, kw in lay.log if n == "operator"]
        assert "hindsight.step_back" in ops and "hindsight.step_forward" in ops, ops
        assert "hindsight.open_history_menu" in ops, ops
        # warnings sub-panel
        assert ui_panel.VIEW3D_PT_hindsight_warnings.poll(ctx) is True
        lay = draw_panel(ui_panel.VIEW3D_PT_hindsight_warnings, ctx)
        icons = [(kw.get("text"), kw.get("icon")) for n, a, kw in lay.log if n == "label"]
        assert icons == [("Bad thing", 'ERROR'), ("Meh", 'INFO')], icons
        # memory readout
        t = draw_panel(ui_panel.VIEW3D_PT_hindsight_memory, ctx).texts()
        assert "This object: 1.0 MB, 5 steps" in t and "All objects: 3.5 MB, 12 steps" in t, t
        # every sub-panel draws without raising
        for cls in ui_panel.classes[1:]:
            draw_panel(cls, ctx)
        draw_panel(ui_panel.VIEW3D_PT_hindsight_movie, ctx, header=True)
        scene.hindsight.use_global = False
        assert "Turn on global history first." in _joined(draw_panel(ui_panel.VIEW3D_PT_hindsight_movie, ctx))
        scene.hindsight.use_global = True
        assert "Global history first" not in _joined(draw_panel(ui_panel.VIEW3D_PT_hindsight_movie, ctx))
        scene.hindsight.filter_mode = 'WHITELIST'
        t = _joined(draw_panel(ui_panel.VIEW3D_PT_hindsight_filter, ctx))
        assert "Only objects in these collections are recorded." in t, t
        # runtime raising everywhere: a short note, nothing raised
        ui_panel._rt = lambda: FakeRuntime(hist, raise_all=True)
        t = _joined(draw_panel(ui_panel.VIEW3D_PT_hindsight, ctx))
        assert "History unavailable" in t and "Step 4" not in t, t
        assert ui_panel.VIEW3D_PT_hindsight_warnings.poll(ctx) is False
        draw_panel(ui_panel.VIEW3D_PT_hindsight_memory, ctx)
        # no runtime at all
        ui_panel._rt = lambda: None
        draw_panel(ui_panel.VIEW3D_PT_hindsight, ctx)
        # no history yet: the status line for new users
        ui_panel._rt = lambda: FakeRuntime(None, uid=None)
        t = _joined(draw_panel(ui_panel.VIEW3D_PT_hindsight, ctx))
        assert "Hindsight is on. Make a stroke in Sculpt Mode to start recording." in t, t
        # recording turned off: the enable button
        ui_panel._rt = lambda: FakeRuntime(None, recording=False)
        lay = draw_panel(ui_panel.VIEW3D_PT_hindsight, ctx)
        assert "hindsight.turn_on" in [a[0] for n, a, kw in lay.log if n == "operator"]
        assert "Enable for this object" in lay.texts("operator")
        # no active object / not a mesh
        ctx.active_object = None
        assert "Select a mesh object to see its history." in _joined(draw_panel(ui_panel.VIEW3D_PT_hindsight, ctx))
    finally:
        ui_panel._rt = orig
        scene.hindsight.filter_mode = 'NONE'
        scene.hindsight.use_global = False


def main():
    global ok
    ensure_scene_props()
    from hindsight.bl import ops_core, selfcheck, ui_menu
    extra = (ops_core, ui_menu, selfcheck)
    for m in extra:
        m.register()
    filtering.register()
    ui_panel.register()
    try:
        for fn in _tests:
            try:
                fn()
                print("PASS", fn.__name__)
            except Exception:
                ok = False
                print("FAIL", fn.__name__)
                traceback.print_exc()
    finally:
        ui_panel.unregister()
        filtering.unregister()
        for m in reversed(extra):
            m.unregister()


main()
sys.stdout.flush(); os._exit(0 if ok else 1)
