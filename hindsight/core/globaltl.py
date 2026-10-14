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


def transition(reg, g: int, visible=None) -> list:
    """How to get every visible history to global position g, as moves [(history uid, cursor)] in
    step-number order: the steps to undo newest first, then the steps to redo oldest first, so a
    step is never applied before an earlier step of another history (an object brought back by the
    scene history exists before its own steps are replayed). Consecutive moves of one history are
    merged. Works from any starting point, also after histories were moved one by one."""
    target = plan_global_goto(reg, g, visible)
    undo, redo = [], []
    for h in _visible_histories(reg, visible):
        t = target.get(h.uid, h.cursor)
        for i in range(t, h.cursor):
            undo.append((h.entries[i].seq, h.uid, i))
        for i in range(h.cursor, t):
            redo.append((h.entries[i].seq, h.uid, i))
    moves = []
    for _seq, hid, i in sorted(undo, reverse=True):
        if moves and moves[-1][0] == hid and moves[-1][2] == "u":
            moves[-1] = (hid, i, "u")
        else:
            moves.append((hid, i, "u"))
    for _seq, hid, i in sorted(redo):
        if moves and moves[-1][0] == hid and moves[-1][2] == "r":
            moves[-1] = (hid, i + 1, "r")
        else:
            moves.append((hid, i + 1, "r"))
    return [(hid, cur) for hid, cur, _ in moves]


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
