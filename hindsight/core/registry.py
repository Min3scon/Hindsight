"""Registry of all histories in the open file (pure Python)."""
from __future__ import annotations

import uuid

from .history import History


def new_uid() -> str:
    return uuid.uuid4().hex[:16]


class Registry:
    def __init__(self):
        self.histories: dict = {}      # history uid -> History
        self.by_object: dict = {}      # object uid -> history uid
        self.next_seq: int = 1
        self.use_global: bool = False
        self.preserve_modes: bool = True
        self.disabled: set = set()     # object uids with "Reset and Turn Off" applied
        self.everything: set = set()   # datablock uids with "Record everything" on (data adapters)
        self.load_report = None        # set by persist.registry_from_bytes

    def new_seq(self) -> int:
        s = self.next_seq
        self.next_seq += 1
        return s

    def history_for(self, obj_uid: str):
        hid = self.by_object.get(obj_uid)
        return self.histories.get(hid) if hid else None

    def create(self, obj_uid: str) -> History:
        h = self.history_for(obj_uid)
        if h is not None:
            return h
        h = History(new_uid(), [obj_uid])
        self.histories[h.uid] = h
        self.by_object[obj_uid] = h.uid
        return h

    def attach(self, obj_uid: str, h: History) -> None:
        """Make obj_uid share history h (join / merged timelines)."""
        self.by_object[obj_uid] = h.uid
        if obj_uid not in h.members:
            h.members.append(obj_uid)
        self.histories[h.uid] = h

    def remove_object(self, obj_uid: str) -> None:
        hid = self.by_object.pop(obj_uid, None)
        if hid is None:
            return
        h = self.histories.get(hid)
        if h is None:
            return
        if obj_uid in h.members:
            h.members.remove(obj_uid)
        if not h.members:
            self.histories.pop(hid, None)

    def all(self) -> list:
        return list(self.histories.values())

    def clear(self) -> None:
        self.__init__()
