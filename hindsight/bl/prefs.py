"""Add-on preferences for Hindsight (HINDSIGHT_AP_prefs) and their mirror into core SETTINGS.

`bl_idname` must equal the extension's root package, which is only known at register time,
so `register(base_package)` sets it before registering the class.
"""

import dataclasses

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty, IntProperty

from ..core import types as _types

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
    try:
        from . import runtime
        runtime.tag_redraw_all()
    except Exception:
        pass


def _update(self, context):
    sync_settings(self)


def _update_appearance(self, context):
    sync_settings(self)
    _redraw()


SAVE_MODE_ITEMS = (
    ('SIDECAR', "Sidecar", "Store a history folder beside the .blend file (fastest saves)"),
    ('EMBED', "Embed", "Store history inside the .blend (one file to share)"),
    ('BOTH', "Both", "Keep both"),
)

CRASH_RECOVERY_ITEMS = (
    ('LAST_SAVED', "Last Saved", "Open with the history as it was when the file was last saved"),
    ('INTERRUPTED', "Interrupted Session", "Recover unsaved steps from an interrupted session"),
)

COMPRESSION_ITEMS = (
    ('AUTO', "Auto", "Use the best available codec"),
    ('ZSTD', "Zstandard", "Zstandard compression (best ratio)"),
    ('LZ4', "LZ4", "LZ4 compression (fastest)"),
    ('ZLIB', "Zlib", "Zlib compression (always available)"),
    ('NONE', "None", "Store packed steps without compression"),
)


class HINDSIGHT_AP_prefs(bpy.types.AddonPreferences):
    bl_idname = __package__  # replaced by register(base_package)

    # Saving
    save_mode: EnumProperty(
        name="Save Mode", description="Where the undo history is saved",
        items=SAVE_MODE_ITEMS, default='SIDECAR', update=_update)
    crash_recovery: EnumProperty(
        name="Crash Recovery", description="Which history to load when a file is opened",
        items=CRASH_RECOVERY_ITEMS, default='LAST_SAVED', update=_update)

    # History
    truncation_threshold: IntProperty(
        name="Truncation Warning Threshold",
        description="Ask for confirmation before a new step discards at least this many future steps",
        default=10, min=1, soft_max=1000, update=_update)
    max_steps: IntProperty(
        name="Max Undo Steps", description="Maximum number of undo steps kept per object",
        default=10000, min=1, max=10000, update=_update)

    # Memory
    memory_limit_mb: IntProperty(
        name="Memory Limit (MB)",
        description="Total size of all undo histories; the oldest steps are dropped first",
        default=4096, min=16, soft_max=65536, update=_update)
    raw_steps: IntProperty(
        name="Uncompressed Recent Steps",
        description="Number of most recent steps per object kept uncompressed for instant scrubbing",
        default=64, min=0, soft_max=1024, update=_update)
    compression: EnumProperty(
        name="Compression", description="Codec used to pack older steps",
        items=COMPRESSION_ITEMS, default='AUTO', update=_update)

    # Scrubber appearance
    scrub_height: IntProperty(
        name="Height", description="Thickness of the scrubber bar",
        default=6, min=2, max=64, subtype='PIXEL', update=_update_appearance)
    scrub_margin: IntProperty(
        name="Bottom Margin", description="Distance of the scrubber from the bottom of the viewport",
        default=18, min=0, max=1000, subtype='PIXEL', update=_update_appearance)
    scrub_width_pct: IntProperty(
        name="Width", description="Scrubber width as a percentage of the viewport width",
        default=60, min=5, max=100, subtype='PERCENTAGE', update=_update_appearance)
    scrub_color: FloatVectorProperty(
        name="Color", description="Color of the scrubber's filled part and handle",
        size=4, subtype='COLOR_GAMMA', min=0.0, max=1.0,
        default=(1.0, 0.6, 0.24, 1.0), update=_update_appearance)
    scrub_bg_color: FloatVectorProperty(
        name="Background Color", description="Color of the scrubber track",
        size=4, subtype='COLOR_GAMMA', min=0.0, max=1.0,
        default=(0.1, 0.1, 0.1, 0.6), update=_update_appearance)
    scrub_show_counter: BoolProperty(
        name="Show Counter", description="Show the step counter next to the scrubber",
        default=True, update=_update_appearance)

    def draw(self, context):
        layout = self.layout
        from .beta import draw_note
        head = layout.row()
        head.label(text="Hindsight BETA")
        draw_note(layout.column())
        layout.use_property_split = True
        layout.use_property_decorate = False

        box = layout.box()
        box.label(text="Saving", icon='FILE_TICK')
        col = box.column()
        col.prop(self, "save_mode")
        col.prop(self, "crash_recovery")

        box = layout.box()
        box.label(text="History", icon='RECOVER_LAST')
        col = box.column()
        col.prop(self, "truncation_threshold")
        col.prop(self, "max_steps")

        box = layout.box()
        box.label(text="Memory", icon='MEMORY')
        col = box.column()
        col.prop(self, "memory_limit_mb")
        col.prop(self, "raw_steps")
        col.prop(self, "compression")

        box = layout.box()
        box.label(text="Scrubber", icon='TIME')
        draw_scrubber_props(box.column(), self)


def draw_scrubber_props(layout, prefs) -> None:
    """Shared by the preferences and the sidebar Scrubber sub-panel."""
    layout.prop(prefs, "scrub_height")
    layout.prop(prefs, "scrub_margin")
    layout.prop(prefs, "scrub_width_pct")
    layout.prop(prefs, "scrub_color")
    layout.prop(prefs, "scrub_bg_color")
    layout.prop(prefs, "scrub_show_counter")


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
    for cls in classes:
        bpy.utils.register_class(cls)
    try:
        sync_settings(get_prefs())
    except Exception:
        pass


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
