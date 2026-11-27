"""Add-on preferences for Hindsight (HINDSIGHT_AP_prefs) and their mirror into core SETTINGS.

`bl_idname` must equal the extension's root package, which is only known at register time,
so `register(base_package)` sets it before registering the class.
"""

import contextlib
import dataclasses

import bpy

from . import reg
from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty, IntProperty

from ..core import types as _types

SCRUB_DEFAULTS = dict(scrub_height=6, scrub_margin=18, scrub_width_pct=60, scrub_color=(1.0, 0.6, 0.24, 1.0),
                      scrub_bg_color=(0.1, 0.1, 0.1, 0.6), scrub_show_counter=True)

_base_package = None


def _root_package() -> str:
    pkg = __package__ or ""
    return pkg.rsplit(".", 1)[0] if "." in pkg else pkg


def sync_settings(prefs) -> None:
    """Copy every Settings field present on `prefs` into core.types.SETTINGS.

    Enums are kept as identifiers, except compression which is lower-cased
    (the codec layer uses "auto" / "zstd" / "lz4" / "zlib" / "none").
    """
    if prefs is None:
        return
    target = _types.SETTINGS
    for f in dataclasses.fields(_types.Settings):
        try:
            value = getattr(prefs, f.name)
        except Exception:
            continue
        if f.name == "compression":
            value = str(value).lower()
        elif isinstance(getattr(target, f.name, None), int) and not isinstance(value, bool):
            try:
                value = int(value)
            except Exception:
                continue
        elif isinstance(getattr(target, f.name, None), str):
            value = str(value)
        setattr(target, f.name, value)


def _redraw() -> None:
    with contextlib.suppress(Exception):
        from . import runtime
        runtime.tag_redraw_all()


def _update(self, context):
    sync_settings(self)


def _update_appearance(self, context):
    sync_settings(self)
    _redraw()


_KIND_ITEMS = [(k.upper(), v, f"Record {v.lower()}") for k, v in (
    ("transforms", "Transforms"), ("parenting", "Parenting"), ("visibility", "Visibility"),
    ("names", "Names"), ("collections", "Collections"), ("constraints", "Constraints"),
    ("edit_steps", "Edit Mode step by step"), ("objects", "Adding and deleting objects"),
    ("scene_collections", "Making and deleting collections"))]
_KIND_DEFAULT = {k for k, _l, _d in _KIND_ITEMS}


def _update_kinds(self, context):
    with contextlib.suppress(Exception):
        from . import datarec
        datarec.rebaseline()


RECORD_SCOPE_ITEMS = (
    ('SCULPT', "Sculpting only", "Record sculpt and paint strokes only"),
    ('EVERYTHING', "Everything", "Record sculpting and other changes in a file. To leave out an object "
                                 "or a whole file, switch it off under Record in the Undo History tab"),
)

SAVE_MODE_ITEMS = (
    ('SIDECAR', "Next to the .blend", "Save history in a folder next to the .blend file (fastest saves)"),
    ('EMBED', "Inside the .blend", "Save history inside the .blend file (one file to share)"),
    ('BOTH', "Both", "Save it in both places"),
)

CRASH_RECOVERY_ITEMS = (
    ('LAST_SAVED', "Last save", "Open files with the history as it was at the last save"),
    ('INTERRUPTED', "Unsaved steps", "Also bring back steps made after the last save if Blender closed unexpectedly"),
)

COMPRESSION_ITEMS = (
    ('AUTO', "Auto", "Use the best codec available"),
    ('ZSTD', "Zstandard", "Smallest history, a little slower"),
    ('LZ4', "LZ4", "Fastest, a little larger"),
    ('ZLIB', "Zlib", "Always available"),
    ('NONE', "None", "No compression"),
)


class HINDSIGHT_AP_prefs(bpy.types.AddonPreferences):
    bl_idname = __package__  # replaced by register(base_package)

    # Saving
    save_mode: EnumProperty(
        name="Save history", description="Where the history is saved",
        items=SAVE_MODE_ITEMS, default='SIDECAR', update=_update)
    crash_recovery: EnumProperty(
        name="On open", description="Which history to load when a file is opened",
        items=CRASH_RECOVERY_ITEMS, default='LAST_SAVED', update=_update)

    # History
    truncation_threshold: IntProperty(
        name="Ask before dropping",
        description="Ask before a new stroke drops at least this many later steps",
        default=10, min=1, soft_max=1000, update=_update)
    max_steps: IntProperty(
        name="Steps per object", description="Most steps kept for one object. Oldest go first",
        default=10000, min=1, max=100000, update=_update)

    # Memory
    memory_limit_mb: IntProperty(
        name="Memory limit (MB)",
        description="Total size of all histories. The oldest steps go first when it is reached",
        default=4096, min=16, soft_max=65536, update=_update)
    raw_steps: IntProperty(
        name="Uncompressed steps",
        description="Most recent steps per object kept uncompressed, for instant scrubbing",
        default=64, min=0, soft_max=1024, update=_update)
    compression: EnumProperty(
        name="Compression", description="How older steps are packed",
        items=COMPRESSION_ITEMS, default='AUTO', update=_update)

    # On-screen slider
    scrub_height: IntProperty(
        name="Height", description="Thickness of the on-screen slider",
        default=SCRUB_DEFAULTS["scrub_height"], min=2, max=64, subtype='PIXEL', update=_update_appearance)
    scrub_margin: IntProperty(
        name="Bottom margin", description="Gap between the slider and the bottom of the viewport",
        default=SCRUB_DEFAULTS["scrub_margin"], min=0, max=1000, subtype='PIXEL', update=_update_appearance)
    scrub_width_pct: IntProperty(
        name="Width", description="Slider width as a share of the viewport width",
        default=SCRUB_DEFAULTS["scrub_width_pct"], min=5, max=100, subtype='PERCENTAGE', update=_update_appearance)
    scrub_color: FloatVectorProperty(
        name="Colour", description="Colour of the filled part and the handle",
        size=4, subtype='COLOR_GAMMA', min=0.0, max=1.0,
        default=SCRUB_DEFAULTS["scrub_color"], update=_update_appearance)
    scrub_bg_color: FloatVectorProperty(
        name="Track colour", description="Colour of the empty part of the slider",
        size=4, subtype='COLOR_GAMMA', min=0.0, max=1.0,
        default=SCRUB_DEFAULTS["scrub_bg_color"], update=_update_appearance)
    scrub_show_counter: BoolProperty(
        name="Show step counter", description="Show the step number next to the slider",
        default=SCRUB_DEFAULTS["scrub_show_counter"], update=_update_appearance)

    record_scope: EnumProperty(
        name="Record",
        items=RECORD_SCOPE_ITEMS,
        default='EVERYTHING',
    )
    record_kinds: EnumProperty(
        name="Data kinds",
        description="What Record everything records on an object",
        items=_KIND_ITEMS,
        options={'ENUM_FLAG'},
        default=_KIND_DEFAULT,
        update=_update_kinds,
    )
    record_texture_painting: BoolProperty(
        name="Record texture painting",
        description=("Record each texture painting stroke as a step. Off by default. It reads the whole image "
                     "after every stroke. Images over 2K are left out"),
        default=False)
    record_multires: BoolProperty(
        name="Record Multires",
        description=("Record one step per Sculpt Mode visit on an object with a Multires modifier. "
                     "Off by default. "
                     "Off: recording pauses on a Multires object"),
        default=False)
    record_dyntopo: BoolProperty(
        name="Record Dyntopo",
        description=("Record each Dyntopo stroke as a step, on meshes up to 200,000 faces. Each step holds the "
                     "whole mesh: about a quarter of a second per stroke at 100,000 faces. Off by default. "
                     "Off: recording pauses while Dyntopo is on"),
        default=False)
    edit_steps_max: IntProperty(
        name="Edit Mode steps up to",
        description=("With Record everything, Edit Mode changes are recorded one operation at a time on meshes "
                     "up to this many vertices, and as one step per visit on bigger ones. Each operation costs "
                     "about 25 ms per 100,000 vertices"),
        default=300_000, min=10_000, max=5_000_000, update=_update)
    welcomed: BoolProperty(default=False, options={'HIDDEN'})

    def draw(self, context):
        layout = self.layout
        from .notice import draw_note
        draw_note(layout)

        def section(title, icon):
            layout.separator()
            layout.label(text=title, icon=icon)
            col = layout.column()
            col.use_property_split = True
            col.use_property_decorate = False
            return col

        col = section("Saving", 'FILE_TICK')
        col.prop(self, "save_mode")
        col.prop(self, "crash_recovery")

        col = section("History", 'RECOVER_LAST')
        col.prop(self, "record_scope")
        col.prop(self, "record_texture_painting")
        col.prop(self, "record_multires")
        col.prop(self, "record_dyntopo")
        col.prop(self, "edit_steps_max")
        col.prop(self, "max_steps")
        col.prop(self, "truncation_threshold")

        col = section("Memory", 'MEMORY')
        col.prop(self, "memory_limit_mb")
        col.prop(self, "raw_steps")
        col.prop(self, "compression")

        col = section("On-screen slider", 'ARROW_LEFTRIGHT')
        draw_scrubber_props(col, self)

        layout.separator()
        row = layout.row(align=True)
        row.operator("hindsight.self_check", text="Run self-check", icon='CHECKMARK')
        row.operator("hindsight.copy_diagnostics", text="Copy diagnostics", icon='COPYDOWN')


def draw_scrubber_props(layout, prefs) -> None:
    """Shared by the preferences and the sidebar On-screen slider sub-panel."""
    layout.prop(prefs, "scrub_height")
    layout.prop(prefs, "scrub_margin")
    layout.prop(prefs, "scrub_width_pct")
    layout.prop(prefs, "scrub_color")
    layout.prop(prefs, "scrub_bg_color")
    layout.prop(prefs, "scrub_show_counter")


FORCE = {}  # tests: preference name -> value (None unsets), for runs where the preferences aren't registered


def value(name, default):
    """One preference: a test's FORCE entry, else the registered preferences' value, else `default`."""
    v = FORCE.get(name)
    if v is not None:
        return v
    with contextlib.suppress(Exception):
        p = get_prefs()
        if p is not None:
            return getattr(p, name, default)
    return default


def get_prefs(context=None):
    """The add-on's preferences, or None (not registered / not enabled as an add-on)."""
    if not _base_package:
        return None
    try:
        ctx = context if context is not None else bpy.context
        prefs = getattr(ctx, "preferences", None) or bpy.context.preferences
        addon = prefs.addons.get(_base_package)
        return addon.preferences if addon is not None else None
    except Exception:
        return None


classes = (HINDSIGHT_AP_prefs,)


def register(base_package: str = None):
    global _base_package
    _base_package = base_package or _root_package()
    HINDSIGHT_AP_prefs.bl_idname = _base_package
    reg.register_classes(classes)
    with contextlib.suppress(Exception):
        sync_settings(get_prefs())


def unregister():
    reg.unregister_classes(classes)
