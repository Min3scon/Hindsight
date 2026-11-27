"""REFERENCE COPY (tests only; nothing under hindsight/ may import it). History.goto as it was before
the engine's apply_steps became the only way across packed steps: the plain Python loop, one step at
a time (decode, then apply). The bit-exact oracle of the engine path in hindsight/core/history.py.
It is the shipped History with goto replaced, so everything else (push, truncate, the crossing of
one step) is shared.
"""
from __future__ import annotations

from hindsight.core import history as _h


def _steps(entries: list, states: dict):
    """Yield the Step of each entry in order, or None when its object isn't being moved."""
    for e in entries:
        yield e.load() if e.target in states else None


class History(_h.History):
    def goto(self, pos: int, states: dict, join_hook=None) -> dict:
        n = len(self.entries)
        pos = max(0, min(int(pos), n))
        changed: dict = {}
        cur = self.cursor
        if pos != cur:
            forward = pos > cur
            order = range(cur, pos) if forward else range(cur - 1, pos - 1, -1)
            entries = [self.entries[i] for i in order]
            for entry, step in zip(entries, _steps(entries, states)):
                if step is not None:       # None: object not being moved, skipped without unpacking
                    self._cross(entry, step, forward, states, join_hook, changed)
        self.cursor = pos
        return changed
