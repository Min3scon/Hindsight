"""The mark on the camera and light objects Hindsight adds for Movie Mode.

Marked objects are Hindsight's own: the recorder never records them and the global history never
takes them away or brings them back. The mark is a custom property, so it survives saving.
"""
import contextlib
RIG_PROP = "_hindsight_rig"
ROLES = ("camera", "key", "fill", "rim")


def role_of(ob):
    """"camera", "key", "fill" or "rim" for one of Hindsight's Movie Mode objects, else None."""
    try:
        role = ob.get(RIG_PROP) if ob is not None else None
    except (AttributeError, ReferenceError, TypeError):
        return None
    return role if role in ROLES else None


def is_rig(ob) -> bool:
    return role_of(ob) is not None


def mark(ob, role: str) -> None:
    if role not in ROLES:
        raise ValueError(role)
    ob[RIG_PROP] = role


def unmark(ob) -> None:
    with contextlib.suppress(AttributeError, ReferenceError, TypeError):
        if RIG_PROP in ob.keys():
            del ob[RIG_PROP]
