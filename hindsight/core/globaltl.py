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


def global_length(reg, visible=None, groups=None) -> int:
    return len(global_groups(reg, visible) if groups is None else groups)


def position_index(groups) -> dict:
    """R055: for global_position without walking every step. History uid -> a list whose item c is the
    index of the first group holding a step of that history at index c or later (len(groups) past its
    last step): the first operation not applied while the history's cursor is at c."""
    by = {}
    for gi, g in enumerate(groups):
        for _seq, hid, i in g:
            by.setdefault(hid, {})[i] = gi
    n = len(groups)
    out = {}
    for hid, d in by.items():
        top = max(d) + 1
        suf = [n] * (top + 1)
        m = n
        for i in range(top - 1, -1, -1):
            gi = d.get(i)
            if gi is not None and gi < m:
                m = gi
            suf[i] = m
        out[hid] = suf
    return out


def global_position(reg, visible=None, groups=None, index=None) -> int:
    """How many operations from the start are applied (up to the first one that isn't, when
    histories were moved one by one). groups: global_groups(reg, visible) when the caller has it;
    index: position_index(groups), kept with them (then the cost is one look-up per history)."""
    if index is not None and groups is not None:
        pos = len(groups)
        for h in _visible_histories(reg, visible):
            suf = index.get(h.uid)
            if suf is not None and h.cursor < len(suf):
                v = suf[h.cursor]
                if v < pos:
                    pos = v
        return pos
    cur = {h.uid: h.cursor for h in _visible_histories(reg, visible)}
    n = 0
    for g in global_groups(reg, visible) if groups is None else groups:
        if all(i < cur[hid] for _seq, hid, i in g):
            n += 1
        else:
            break
    return n


def plan_global_goto(reg, g: int, visible=None, groups=None) -> dict:
    """History uid -> cursor that applies exactly the first g operations."""
    if groups is None:
        groups = global_groups(reg, visible)
    g = max(0, min(int(g), len(groups)))
    cur = {h.uid: 0 for h in _visible_histories(reg, visible)}
    for grp in groups[:g]:
        for _seq, hid, i in grp:
            cur[hid] = max(cur[hid], i + 1)
    return cur


def transition(reg, g: int, visible=None, groups=None) -> list:
    """How to get every visible history to global position g, as moves [(history uid, cursor)] in
    step-number order: the steps to undo newest first, then the steps to redo oldest first, so a
    step is never applied before an earlier step of another history (an object brought back by the
    scene history exists before its own steps are replayed). Consecutive moves of one history are
    merged. Works from any starting point, also after histories were moved one by one."""
    target = plan_global_goto(reg, g, visible, groups)
    undo, redo = [], []
    free = {}
    for h in _visible_histories(reg, visible):
        t = target.get(h.uid, h.cursor)
        one = len(h.members) == 1
        for i in range(t, h.cursor):
            undo.append((h.entries[i].seq, h.uid, i))
            free[(h.uid, i)] = one and _stroke(h.entries[i])
        for i in range(h.cursor, t):
            redo.append((h.entries[i].seq, h.uid, i))
            free[(h.uid, i)] = one and _stroke(h.entries[i])
    moves = []
    for items, d in ((sorted(undo, reverse=True), "u"), (sorted(redo), "r")):
        run = {}            # R056: history uid -> its move in the current run of stroke steps
        for _seq, hid, i in items:
            cur = i if d == "u" else i + 1
            if free[(hid, i)]:
                k = run.get(hid)
                if k is not None:
                    moves[k] = (hid, cur, d)
                    continue
                if moves and moves[-1][0] == hid and moves[-1][2] == d:
                    moves[-1] = (hid, cur, d)
                else:
                    moves.append((hid, cur, d))
                run[hid] = len(moves) - 1
                continue
            run.clear()
            if moves and moves[-1][0] == hid and moves[-1][2] == d:
                moves[-1] = (hid, cur, d)
            else:
                moves.append((hid, cur, d))
    return [(hid, cur) for hid, cur, _ in moves]


# R056: a stroke's step (sculpt, paint, Edit Mode step by step) changes only its own object's mesh:
# no data records (those come with Object Mode operations, recorded with mode OBJECT), no operation
# shared with other histories. The moves of such steps of different histories commute, so a run of
# them is one move per history instead of one per step; any other step stays in its place in
# step-number order and ends the run (an object deleted or brought back by the scene, a join...).
_STROKE_MODES = frozenset(("SCULPT", "VERTEX_PAINT", "WEIGHT_PAINT", "TEXTURE_PAINT", "EDIT"))


def _stroke(e) -> bool:
    m = e.meta if isinstance(e.meta, dict) else {}
    if m.get("kind", "DELTA") != "DELTA" or m.get("mode") not in _STROKE_MODES:
        return False
    extra = m.get("extra")
    return not (isinstance(extra, dict) and ("op" in extra or "_snapshots" in extra))


def pivot_entry(reg, old_g: int, new_g: int, visible=None, groups=None):
    """(hist_uid, entry_index) of the step that decides which object is 'current' after a move: the
    first step of the operation the move ended on (or, going back, the one just undone)."""
    if groups is None:
        groups = global_groups(reg, visible)
    if not groups:
        return None
    if new_g < old_g:
        k = min(new_g, len(groups) - 1)
    else:
        k = max(0, min(new_g, len(groups)) - 1)
    _seq, hid, i = groups[k][0]
    return hid, i
