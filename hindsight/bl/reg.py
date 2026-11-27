# SPDX-License-Identifier: GPL-3.0-or-later
"""The register and unregister steps every module shares: classes (tolerant on the way out) and keymap items."""
from __future__ import annotations

import bpy


def register_classes(classes, again_ok=False):
    for c in classes:
        try:
            bpy.utils.register_class(c)
        except ValueError:
            if not again_ok:
                raise


def unregister_classes(classes):
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass


def add_keymap(store, km_name, idname, key, **mods):
    """Add one item to the add-on key config and remember it in `store`; nothing when there is no key config."""
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc is None:
        return
    km = kc.keymaps.new(name=km_name, space_type='EMPTY')
    store.append((km, km.keymap_items.new(idname, key, 'PRESS', **mods)))


def remove_keymaps(store):
    for km, k in store:
        try:
            km.keymap_items.remove(k)
        except Exception:
            pass
    store.clear()
