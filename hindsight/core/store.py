"""Storage tiering and caps. Pure Python + numpy, no bpy. The caps, the tiers and the caps memo (24.5a, #154) and the
packer (24.5b, #155) are the engine's; their Python is tests/reference/store.py.

Histories are duck-typed: h.entries (list[Entry]), h.cursor (int), h.drop_oldest(n) -> int.
Recent entries stay raw (fast undo); older ones are packed (compressed) by a background
thread; caps drop the oldest entries. When the thread has nothing else to do it recompresses
packed steps with a stronger setting, checked byte for byte, a few at a time.
"""
from __future__ import annotations

import sys

from . import step as _step
from .native import ENGINE as _N

QUIET_S = 0.3         # pack only once no new step has arrived for this long (the main thread is
                      # busy right after a commit; packing then would compete with it for Python)
IDLE_S = 1.0          # recompress only after the packer has been idle this long
USER_IDLE_S = 5.0     # and nothing was recorded or jumped to for this long (note_activity): the
                      # strong setting costs 3 to 50 ms of one core per step, which belongs to the
                      # user while they sculpt or scrub (Blender's own sculpting uses every core)
DUTY = 0.25           # and then use at most this share of one core (sleep in between)
STRONG_MAX_BYTES = 8 << 20   # larger steps are left at the normal setting (too slow to redo)

# The packer: a Rust thread that packs entries off the main thread and recompresses them when idle
# (native/src/packer.rs). It gets bytes only; its results are taken in on the main thread at the next call into it
# (submit, queue_recompress, pending, drain, stop, take_in, its counters). A Packer reads the times above when made.
_N.packer_setup(sys.modules[__name__], _step)
Packer = _N.Packer


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
