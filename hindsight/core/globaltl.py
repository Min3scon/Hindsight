"""Global timeline: every object's steps on one timeline ordered by sequence number."""
from __future__ import annotations


def _visible_histories(reg, visible=None):
    hs = []
    for h in reg.histories.values():
        if visible is not None and not (set(h.members) & visible):
            continue
        hs.append(h)
    return hs


def global_order(reg, visible=None) -> list:
    out = []
    for h in _visible_histories(reg, visible):
        for i, e in enumerate(h.entries):
            out.append((e.seq, h.uid, i))
    out.sort()
    return out


def global_position(reg, visible=None) -> int:
    return sum(h.cursor for h in _visible_histories(reg, visible))


def plan_global_goto(reg, g: int, visible=None) -> dict:
    order = global_order(reg, visible)
    g = max(0, min(int(g), len(order)))
    cur = {h.uid: 0 for h in _visible_histories(reg, visible)}
    for _seq, hid, i in order[:g]:
        cur[hid] = i + 1
    return cur


def pivot_entry(reg, old_g: int, new_g: int, visible=None):
    """(hist_uid, entry_index) of the step that decides which object is 'current' after a move."""
    order = global_order(reg, visible)
    if not order:
        return None
    if new_g < old_g:
        k = min(new_g, len(order) - 1)
    else:
        k = max(0, min(new_g, len(order)) - 1)
    _seq, hid, i = order[k]
    return hid, i
