"""Global timeline: every history's steps on one timeline, in step-number order.

A position on the global timeline is one operation: the steps one user operation made in several
histories (moving two objects at once, M > New Collection on a recorded scene) carry the number of
its first step in extra["op"] and are one position, applied or undone together. Steps without it
(sculpt strokes, single-history operations) are a position each.
"""
from __future__ import annotations


def _visible_histories(reg, visible=None):
    hs = []
    for h in reg.histories.values():
        if visible is not None and not (set(h.members) & visible):
            continue
        hs.append(h)
    return hs


def _group_key(e) -> int:
    extra = e.meta.get("extra") if isinstance(e.meta, dict) else None
    op = extra.get("op") if isinstance(extra, dict) else None
    return int(op) if op is not None else e.seq


def global_order(reg, visible=None) -> list:
    """Every step as (seq, history uid, index), in step-number order."""
    out = []
    for h in _visible_histories(reg, visible):
        for i, e in enumerate(h.entries):
            out.append((e.seq, h.uid, i))
    out.sort()
    return out


def global_groups(reg, visible=None) -> list:
    """The positions of the global timeline: one list of (seq, history uid, index) per operation,
    in the order of their first step."""
    by = {}
    for h in _visible_histories(reg, visible):
        for i, e in enumerate(h.entries):
            by.setdefault(_group_key(e), []).append((e.seq, h.uid, i))
    groups = [sorted(g) for g in by.values()]
    groups.sort(key=lambda g: g[0][0])
    return groups


def global_length(reg, visible=None) -> int:
    return len(global_groups(reg, visible))


def global_position(reg, visible=None) -> int:
    """How many operations from the start are applied (up to the first one that isn't, when
    histories were moved one by one)."""
    cur = {h.uid: h.cursor for h in _visible_histories(reg, visible)}
    n = 0
    for g in global_groups(reg, visible):
        if all(i < cur[hid] for _seq, hid, i in g):
            n += 1
        else:
            break
    return n


def plan_global_goto(reg, g: int, visible=None) -> dict:
    """History uid -> cursor that applies exactly the first g operations."""
    groups = global_groups(reg, visible)
    g = max(0, min(int(g), len(groups)))
    cur = {h.uid: 0 for h in _visible_histories(reg, visible)}
    for grp in groups[:g]:
        for _seq, hid, i in grp:
            cur[hid] = max(cur[hid], i + 1)
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
    """(hist_uid, entry_index) of the step that decides which object is 'current' after a move: the
    first step of the operation the move ended on (or, going back, the one just undone)."""
    groups = global_groups(reg, visible)
    if not groups:
        return None
    if new_g < old_g:
        k = min(new_g, len(groups) - 1)
    else:
        k = max(0, min(new_g, len(groups)) - 1)
    _seq, hid, i = groups[k][0]
    return hid, i
