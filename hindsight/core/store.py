"""Storage tiering and caps. Pure Python + numpy, no bpy. The caps, the tiers and the caps memo are the engine's
(24.5a, #154; their Python is tests/reference/store.py); the Packer is still Python here (24.5b, #155).

Histories are duck-typed: h.entries (list[Entry]), h.cursor (int), h.drop_oldest(n) -> int.
Recent entries stay raw (fast undo); older ones are packed (compressed) by a background
worker thread; caps drop the oldest entries. When the worker has nothing else to do it recompresses
packed steps with a stronger setting, checked byte for byte, a few at a time.
"""
from __future__ import annotations

import collections
import queue
import threading
import time
import weakref

from .native import ENGINE as _N
from .step import parts_to_bytes, recompress, step_parts

_STOP = object()
_NUDGE = object()      # wakes the worker to look at its idle work again


QUIET_S = 0.3         # pack only once no new step has arrived for this long (the main thread is
                      # busy right after a commit; packing then would compete with it for Python)
IDLE_S = 1.0          # recompress only after the packer has been idle this long
USER_IDLE_S = 5.0     # and nothing was recorded or jumped to for this long (note_activity): the
                      # strong setting costs 3 to 50 ms of one core per step, which belongs to the
                      # user while they sculpt or scrub (Blender's own sculpting uses every core)
DUTY = 0.25           # and then use at most this share of one core (sleep in between)
STRONG_MAX_BYTES = 8 << 20   # larger steps are left at the normal setting (too slow to redo)


class Packer:
    """Background thread that packs entries off the main thread.

    The worker only touches numpy + codec. The swap is atomic for readers:
    entry.blob is set first, then entry.step = None, so Entry.load() always finds one.
    Recompression replaces entry.blob with a smaller blob that decodes to the same bytes.
    """

    def __init__(self, recompress_idle: bool = True):
        self._q = queue.SimpleQueue()
        self._cond = threading.Condition()
        self._pending = 0
        self._inflight = set()           # id(entry) queued and not yet processed
        self._stopped = False
        self.errors = 0
        self.last_error = None
        self.packed = 0
        self.recompressed = 0
        self._strong = collections.deque()   # weak refs to packed entries not yet recompressed
        self._recompress = recompress_idle
        self._wake = threading.Event()        # set on stop, ends a pause early
        self._last_submit = 0.0
        self._last_activity = float("-inf")   # note_activity: the user recorded or jumped
        self._hurry = 0                       # drains waiting: pack right away
        self._thread = threading.Thread(target=self._run, name="HindsightPacker", daemon=True)
        self._thread.start()

    def queue_recompress(self, entries) -> None:
        """Offer packed entries (for example after loading a file) for idle-time recompression."""
        if not self._recompress:
            return
        had = bool(self._strong)
        strong, ref, can_gain = self._strong, weakref.ref, _can_gain
        for e in entries:
            b = e._bc[0]                # a Slice is read by index, not copied (#125)
            if b is not None and can_gain(b):
                strong.append(ref(e))
        if self._strong and not had and not self._stopped:
            self._q.put(_NUDGE)      # the worker may be waiting for new work only

    def note_activity(self) -> None:
        """The user is working (a step was recorded, a jump made): recompression waits until
        USER_IDLE_S has passed without another call. Packing new steps is not held back."""
        self._last_activity = time.monotonic()

    # main thread -----------------------------------------------------------
    def submit(self, entry, codec: str) -> None:
        if entry.step is None:
            return
        if self._stopped or not self._thread.is_alive():
            entry.pack(codec)
            return
        with self._cond:
            if id(entry) in self._inflight:
                return
            self._inflight.add(id(entry))
            self._pending += 1
        self._last_submit = time.monotonic()
        step = entry.step
        try:
            parts = step_parts(step)     # the JSON now, on the main thread: the worker gets no Python values (owner point 1)
        except Exception as e:           # as the worker did: counted, the entry stays raw
            parts = e
        self._q.put((entry, codec, step, parts))

    def pending(self) -> int:
        with self._cond:
            return self._pending

    def drain(self, timeout: float | None = None) -> None:
        with self._cond:
            self._hurry += 1
            try:
                self._cond.wait_for(lambda: self._pending == 0, timeout)
            finally:
                self._hurry -= 1

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._wake.set()
        self._q.put(_STOP)
        self._thread.join(timeout=10.0)

    # worker thread ---------------------------------------------------------
    def _run(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=self._idle_wait())
            except queue.Empty:
                if self._user_idle_for() >= USER_IDLE_S:
                    self._recompress_one()
                continue
            if item is _STOP:
                return
            if item is _NUDGE:
                continue
            entry, codec, step, parts = item
            while not self._stopped and not self._hurry and time.monotonic() - self._last_submit < QUIET_S:
                time.sleep(0.05)
            try:
                if isinstance(parts, Exception):
                    raise parts
                if entry.step is step:
                    blob = parts_to_bytes(parts, codec)
                    if entry.step is step:      # not replaced/packed meanwhile
                        entry.blob = blob
                        entry.step = None
                        self.packed += 1
                        if self._recompress:
                            self._strong.append(weakref.ref(entry))
            except Exception as e:            # keep the worker alive; entry stays raw
                self.errors += 1
                self.last_error = repr(e)
            finally:
                with self._cond:
                    self._inflight.discard(id(entry))
                    self._pending -= 1
                    if self._pending == 0:
                        self._cond.notify_all()

    def _user_idle_for(self) -> float:
        return time.monotonic() - self._last_activity

    def _idle_wait(self):
        """How long to wait for new work before recompressing (None: only new work wakes it)."""
        if not self._strong:
            return None
        return max(IDLE_S, USER_IDLE_S - self._user_idle_for())

    def _recompress_one(self) -> None:
        """Idle work: recompress one packed step (oldest first), then rest in proportion."""
        while self._strong and not self._stopped:
            entry = self._strong.popleft()()
            if entry is None:
                continue
            bc = entry._bc              # the bytes as held: a step read out of its step part copies them (#125)
            blob = entry.blob
            if blob is None or len(blob) > STRONG_MAX_BYTES or not _can_gain(blob):
                continue
            t = time.perf_counter()
            try:
                new = recompress(blob)
            except Exception as e:            # damaged or unexpected: leave the step as it is
                self.errors += 1
                self.last_error = repr(e)
                return
            if new is not None and entry._bc is bc:
                entry.blob = new
                self.recompressed += 1
            dt = time.perf_counter() - t
            if dt > 0:
                self._wake.wait(min(1.0, dt * (1.0 - DUTY) / DUTY))
            return


EVICT_TO = 0.95     # past the memory limit, drop down to this share of it: the next full pass then
                    # comes 5% of the limit later instead of on every stroke (the engine's constant)

_can_gain = _N.store_can_gain
enforce_tiers = _N.enforce_tiers
total_bytes = _N.total_bytes
enforce_caps = _N.enforce_caps
_sig = _N.caps_sig
_packer_key = _N.packer_key
CapsMemo = _N.CapsMemo
enforce = _N.enforce
