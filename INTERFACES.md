# Hindsight interfaces (contract for every module)

Python package = `hindsight/` (the extension source dir, has `blender_manifest.toml`).
`hindsight/core/**` is pure Python + numpy, **never imports bpy** (testable with plain python).
`hindsight/bl/**` is the Blender layer. Relative imports only (`from ..core.types import Step`).
Optional wheels: `zstandard`, `lz4` — always guarded by `try: import ...` with a zlib fallback.

Python for tests: any interpreter with bpy installed as a module, e.g. `$BPY42_VENV/bin/python` (Python
3.11, numpy, bpy 4.2.23, zstandard, lz4) or `$BPY52_VENV/bin/python` (bpy 5.2.2). Core tests must also pass
without bpy. Test runner: `python tests/run_tests.py [pattern]` (each `tests/test_*.py` runs in its own
subprocess; a test file is a script that exits 0 on success, uses plain `assert`, and prints `PASS <name>`
per test function). Every test file starts with:

```python
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
```

so `import hindsight.core.history` works. bpy tests also `import tests.harness as H` (see harness below).
In bpy-module mode sculpt operators (brush strokes, filters, masks) CRASH headless: never call them in
tests; simulate a stroke by writing mesh attributes directly and calling `recorder.commit(obj)`.
Always end bpy tests with `H.finish()` (it flushes stdout and `os._exit(code)` — bpy segfaults on normal
interpreter exit in sculpt mode).

## 1. Shared types — `hindsight/core/types.py` (exists, owned by main session, do not edit)

Read the file. Key points:
- Channel key: `"name|DOMAIN|TYPE"`, e.g. `POSITION_KEY = "position|POINT|FLOAT_VECTOR"`.
  `CHANNEL_TYPES[TYPE] = (native numpy dtype, width, foreach prop)`. Channel values are ALWAYS flat
  native arrays of length `count*width` (float32 / int32 / int8 / bool).
- `ChannelDelta(key, idx: uint32[k] sorted unique, vals: bits[k, width], exists=(before, after), op)`,
  bits dtype = `bits_dtype(native)` (uint8 for bool/int8, uint32 for float32/int32).
  `op == ADD` (every new step): `vals = view_bits(new) - view_bits(old)` at the changed elements,
  wrapping; forward is `bits[idx] += vals`, backward `bits[idx] -= vals`.
  `op == XOR` (steps from format 1 and 2 files): `vals = old ^ new`, the same op both ways.
  Both are exact for every bit pattern (NaN payloads, -0, denormals).
- `Snapshot(counts={"POINT","EDGE","FACE","CORNER"}, arrays={key: flat native array})` — full mesh,
  includes topology arrays `EDGE_VERTS_KEY, CORNER_VERT_KEY, CORNER_EDGE_KEY, FACE_OFFSETS_KEY`
  (face_offsets length = faces + 1).
- `Step(seq, label, mode, target, time, kind=DELTA|TOPO|JOIN, deltas=[ChannelDelta], before, after, extra={})`.
  DELTA: only `deltas`. TOPO: `before`/`after` Snapshots (state before / after), `deltas` empty.
  JOIN: `before`/`after` = Snapshot of the target object before / after join, `extra["members"]`
  = list of dicts for the absorbed objects (see §7).
- `Entry(step=None, blob=None, meta=None)`: slot in a History. `.meta` dict always present
  (`seq,label,mode,target,time,kind,extra`). `.load() -> Step`, `.pack(codec)`, `.to_bytes(codec)`,
  `.nbytes()`, `.is_packed()`.
- `Settings` dataclass + module global `SETTINGS` (max_steps=10000, memory_limit_mb=4096, raw_steps=64,
  compression="auto", truncation_threshold=10, save_mode="SIDECAR", crash_recovery="LAST_SAVED").

## 2. Storage engine — `core/codec.py`, `core/delta.py`, `core/step.py`, `core/store.py`

```python
# core/codec.py
def available() -> list[str]                    # subset of ["zstd", "lz4", "zlib", "none"], in preference order
def resolve(name: str) -> str                    # "auto" -> best available; unknown/unavailable -> "zlib"
def compress(data: bytes, codec: str = "auto", strong: bool = False) -> bytes
    # first byte = codec id: 0 none, 1 zlib(level 1), 2 lz4, 3 zstd(level 3), 4 zstd(level 19), 5 zlib(level 9)
def decompress(blob: bytes, expected: int | None = None) -> bytes
    # reads the codec id byte; with `expected`, the length is checked before any large allocation
    # and damage raises CorruptBlob (a ValueError)
def is_strong(blob) -> bool                      # already in the strong form (or stored as is)

# core/delta.py
def view_bits(arr: np.ndarray) -> np.ndarray     # flat native array -> same memory viewed as unsigned bits (bool -> uint8)
def diff(key: str, old: np.ndarray, new: np.ndarray, exists=(True, True)) -> ChannelDelta | None
    # old/new: flat native arrays of equal length (count*width). None if bit-identical and exists[0]==exists[1].
    # Missing side: pass a zeros array and exists=(False, True) etc.
def apply(arr: np.ndarray, d: ChannelDelta, forward: bool = True) -> None   # in place, see op above;
                                                 # raises ValueError for indices past the end (array untouched)
def pack_delta(d: ChannelDelta, effort: int = NORMAL) -> bytes
    # format 3 record (b"HE", see the docstring in delta.py): header + key + indices (gaps, or one run)
    # + per component a predictor tag and byte planes. effort QUICK stores values as they are (crash
    # journal, saving: main thread); NORMAL picks a predictor per component (packer thread).
    # Raises ValueError for unsorted or repeated indices.
def unpack_delta(b: bytes) -> ChannelDelta       # reads format 3 and the old b"HD" records; CorruptDelta on damage
def same_change(seen, rec, before, inverse) -> bool   # native undo/redo matching (runtime)

# core/step.py
def snapshot_to_bytes(s: Snapshot) -> bytes      # uncompressed
def snapshot_from_bytes(b: bytes) -> Snapshot
def step_to_bytes(step: Step, codec: str = "auto", effort: int = NORMAL, strong: bool = False) -> bytes
    # format 3: b"HSS3" + u32 CRC-32 + u16 header length + binary header + codec.compress(body)
def step_from_bytes(b: bytes) -> Step            # exact round trip; reads format 3 and the old b"HSS1" steps;
                                                 # CorruptStep (a ValueError) on any damage
def recompress(b: bytes) -> bytes | None         # smaller blob for the same step (strong codec, full re-pack of
                                                 # quick and old-format steps), verified with same_step
def same_step(a: Step, b: Step) -> bool

# core/store.py  — tiering + caps (operates on History objects from core/history.py, duck-typed:
#                  h.entries: list[Entry], h.cursor: int, h.drop_oldest(n) -> int)
class Packer:                                    # background thread that packs entries off the main thread;
                                                 # when idle, recompresses packed entries (throttled)
    def __init__(self, recompress_idle: bool = True): ...
    def queue_recompress(self, entries) -> None  # e.g. everything loaded from a file
    def submit(self, entry: Entry, codec: str) -> None   # packs in a worker thread (numpy+codec only);
                                                         # swap is atomic: sets entry.blob then entry.step=None
    def drain(self, timeout: float | None = None) -> None   # wait until queue empty (used by tests / save)
    def stop(self) -> None
def enforce_tiers(histories: list, settings: Settings, packer: Packer | None = None) -> None
    # for each history: entries older than the newest `settings.raw_steps` are packed (via packer if given,
    # else synchronously).
def enforce_caps(histories: list, settings: Settings) -> int
    # 1) per history: while len(h.entries) > settings.max_steps: h.drop_oldest(1)
    # 2) global: while total bytes > memory_limit_mb*2**20: drop the entry with the smallest seq across all
    #    histories (oldest first), never dropping the entry right before a history's cursor if that would
    #    leave the history empty... (just drop oldest; history keeps working since deltas are relative).
    # returns number of entries dropped
def total_bytes(histories: list) -> int
```

## 3. Restore engine — `core/history.py`

```python
class MeshState:
    """Current channel values of ONE object (the 'baseline' = what the mesh shows at the cursor)."""
    def __init__(self, counts: dict, arrays: dict): ...      # arrays: key -> flat native array (owned)
    counts: dict; arrays: dict
    def apply_delta(self, d: ChannelDelta, forward: bool) -> None
        # delta.apply in place. If key missing from arrays: create zeros of the right size first
        # (size from counts[domain]*width). After applying, if the delta's exists flag for the destination
        # side (exists[1] if forward else exists[0]) is False: remove the key from arrays and add it to
        # self.removed (set). If True: discard from self.removed.
    def set_snapshot(self, snap: Snapshot) -> None           # replace counts+arrays with copies of snap's
    def to_snapshot(self) -> Snapshot                        # deep copy
    removed: set                                             # keys that must be deleted from the mesh

class History:
    def __init__(self, uid: str, members: list[str] | None = None): ...
    uid: str; members: list[str]        # object uids sharing this timeline (>1 after a join)
    entries: list[Entry]; cursor: int   # mesh shows the state after entries[:cursor]
    enabled: bool = True                # recording on
    morph_target: int | None = None     # a cursor position, or None
    def __len__(self) -> int
    def future_count(self) -> int                            # len - cursor
    def push(self, step: Step) -> list[Entry]
        # drops entries[cursor:] (returned), appends Entry(step), cursor = len. morph_target beyond cursor -> None
    def truncate_future(self) -> list[Entry]
    def drop_oldest(self, n: int) -> int                     # remove first n entries, cursor -= n (min 0),
                                                             # morph_target shifted / None if dropped
    def delete_past(self) -> int                             # drop_oldest(cursor); returns removed count
    def reset(self) -> None                                  # entries=[], cursor=0, morph_target=None
    def goto(self, pos: int, states: dict[str, MeshState], join_hook=None) -> dict[str, set]
        # Moves the cursor to clamp(pos, 0, len). For each entry crossed (backward: entries[cursor-1] down to
        # entries[pos]; forward: entries[cursor] .. entries[pos-1]) load the step and:
        #   DELTA: states[step.target].apply_delta(d, forward) for d in step.deltas
        #   TOPO:  states[step.target].set_snapshot(step.before if backward else step.after)
        #   JOIN:  join_hook(step, forward, states) (if None, treated as TOPO on step.target)
        # Steps whose target is not in `states` are skipped silently (cursor still moves).
        # Returns {target_uid: set_of_changed_channel_keys or {"__TOPO__"}}.
        # All steps of a drag are folded into the same arrays, so the caller writes each mesh once.
    def positions_of(self, target: str) -> list[int]          # entry indices whose target == target
```

## 4. Journal and persistence — `core/journal.py`, `core/persist.py`

```python
# core/journal.py
MAGIC = b"HSJ1"
class Journal:
    """Append-only record file. Record = MAGIC(4) | type(u8) | header_len(u32) | payload_len(u64)
       | header JSON utf-8 | payload | crc32(u32 over everything before it in the record). Little endian."""
    def __init__(self, path: str): ...            # opens/creates (parent dirs created), appends at end
    def append(self, rtype: str, header: dict, payload: bytes = b"") -> int   # returns record start offset
    def flush(self, fsync: bool = True) -> None
    def size(self) -> int                          # bytes of valid data
    def read(self, upto: int | None = None) -> list[tuple[str, dict, bytes]]
        # stops silently at the first truncated/corrupt record (crash tolerance) and at `upto` bytes
    def truncate(self, size: int) -> None
    def close(self) -> None
RTYPES = {"STEP": 1, "CURSOR": 2, "TRUNC": 3, "DROP": 4, "RESET": 5, "META": 6, "SAVE": 7}
# STEP   header {"hist": uid, "at": cursor_before_push}, payload = step_to_bytes(step)
# CURSOR header {"hist": uid, "cursor": int}
# TRUNC  header {"hist": uid, "keep": int}          (entries[keep:] removed)
# DROP   header {"hist": uid, "n": int}             (n oldest removed)
# RESET  header {"hist": uid}
# META   header {"hist": uid, "members": [...], "enabled": bool, "morph_target": int|None, "objects": {...}}
# SAVE   header {"blend": path}                     (marks a successful save point)
def atomic_write(path: str, data: bytes) -> None   # tmp file in same dir + flush + fsync + os.replace
def atomic_write_json(path: str, obj) -> None

# core/persist.py   (operates on core.registry.Registry, see §5)
def registry_to_bytes(reg, codec: str = "auto") -> bytes      # complete compacted serialisation
def registry_from_bytes(b: bytes) -> "Registry"
def sidecar_dir(blend_path: str) -> str          # "<dir>/<blend stem>_hindsight"
def save_sidecar(reg, blend_path: str) -> None   # writes sidecar_dir/history.hsr atomically (+ index.json
                                                 # {"version":1,"blend":basename,"saved_at":time})
def load_sidecar(blend_path: str) -> "Registry | None"    # None if folder/file missing
def sidecar_missing(blend_path: str, expected: bool) -> bool   # True if expected but folder absent
def replay(reg, records: list[tuple[str, dict, bytes]]) -> int
    # applies journal records after the last SAVE onto reg (STEP: truncate hist to header["at"] then append;
    # CURSOR/TRUNC/DROP/RESET/META as above); creates histories that don't exist. Returns records applied.
    # NOTE: replay never touches meshes; the caller keeps each history's cursor at its saved value
    # (the mesh in the .blend matches the saved cursor) so the user can scrub forward to recovered steps.
```

## 5. Registry — `core/registry.py` (main session)

```python
class Registry:
    histories: dict[str, History]       # by history uid
    by_object: dict[str, str]           # object uid -> history uid
    next_seq: int
    use_global: bool; preserve_modes: bool
    def new_seq(self) -> int
    def history_for(self, obj_uid: str) -> History | None
    def create(self, obj_uid: str) -> History
    def remove_object(self, obj_uid: str) -> None
    def all(self) -> list[History]
# global timeline (core/globaltl.py, P3)
def global_order(reg, visible: set[str] | None = None) -> list[tuple[int, str, int]]   # (seq, hist_uid, entry_index) sorted
def global_position(reg, visible=None) -> int                  # number of applied entries in global order prefix
def plan_global_goto(reg, g: int, visible=None) -> dict[str, int]   # hist_uid -> cursor for global position g
```

## 6. Blender runtime API — `hindsight/bl/runtime.py` (main session). UI modules call ONLY these.

```python
def reg() -> Registry
def settings() -> Settings
def obj_uid(obj, create: bool = False) -> str | None     # stable id stored in obj["_hindsight_uid"]
def find_object(uid: str)                                 # bpy object or None
def history_of(obj) -> History | None                     # None if none / excluded by collection filter
def is_excluded(obj) -> bool                              # collection filtering
def is_recording(obj) -> bool
def scrub_info(context) -> dict | None   # {"pos": int, "len": int, "cap": int, "global": bool,
                                         #  "locked": bool (movie mode), "morph_target": int|None} for the
                                         #  scrubber of the active object (or global timeline); None = hide
def goto(context, pos: int, final: bool = True) -> None   # move the active timeline (object or global) to pos;
                                                          # final=False during a drag (fast refresh only)
def step(context, delta: int) -> bool                      # +1 / -1; False if nothing to step
def set_global(context, on: bool) -> None
def set_preserve_modes(context, on: bool) -> None
def set_morph_target(context) -> None
def delete_past(context, obj) -> int
def reset_history(context, obj) -> None        # current mesh = new start
def reset_all(context) -> None
def turn_off(context, obj) -> None             # pause, keep steps
def turn_on(context, obj) -> None              # resume; changes made while off become one step / new start
def reset_and_turn_off(context, obj) -> None   # delete history, stop recording permanently (until Turn On)
def memory_stats() -> dict   # {"total_bytes", "ram_bytes", "steps", "objects", "active_bytes", "active_steps"}
def warnings_for(context, obj) -> list[tuple[str, str]]   # (severity "ERROR"|"WARNING"|"INFO", text)
def tag_redraw_all() -> None
```

Scene properties (`bl/props.py`, main session): `scene.hindsight` (HINDSIGHT_PG_scene):
`use_global: Bool`, `preserve_modes: Bool(True)`, `movie_mode: Bool`, `movie_step: Int ("Step", animatable)`,
`frames_per_step: Int (default 2, min 1)`, `filter_mode: Enum('NONE','BLACKLIST','WHITELIST')`,
`filter_collections: Collection of HINDSIGHT_PG_coll_item(collection: Pointer(Collection))`, `filter_index: Int`.

Add-on preferences (`bl/prefs.py`): class `HINDSIGHT_AP_prefs(bpy.types.AddonPreferences)`, `bl_idname = __package__`
of the extension root (`base_package` passed from `__init__`), properties (each `update=` copies into
`core.types.SETTINGS` via `bl.prefs.sync_settings(prefs)`):
`save_mode` Enum SIDECAR/EMBED/BOTH ("Save Mode"), `crash_recovery` Enum LAST_SAVED/INTERRUPTED ("Crash Recovery",
items "Last Saved", "Interrupted Session"), `truncation_threshold` Int 10 ("Truncation Warning Threshold"),
`max_steps` Int 10000 ("Max Undo Steps", 1..10000), `memory_limit_mb` Int 4096 ("Memory Limit (MB)"),
`raw_steps` Int 64 ("Uncompressed Recent Steps"), `compression` Enum AUTO/ZSTD/LZ4/ZLIB/NONE ("Compression"),
scrubber appearance: `scrub_height` Int 6 px, `scrub_margin` Int 18 px, `scrub_color` FloatVector(4, COLOR_GAMMA)
(1.0, 0.6, 0.24, 1.0), `scrub_bg_color` (0.1, 0.1, 0.1, 0.6), `scrub_show_counter` Bool True, `scrub_width_pct` Int 60.
Helper: `def get_prefs(context=None) -> HINDSIGHT_AP_prefs | None`.

Operator idnames (all `bl_idname = "hindsight.<name>"`, labels exactly as listed):
- `hindsight.step_back` "Step Back" / `hindsight.step_forward` "Step Forward" (Ctrl+Z / Ctrl+Shift+Z, `oskey` variants)
- `hindsight.scrub` modal drag (scrubber), `hindsight.goto` (prop `position`)
- History Menu `HINDSIGHT_MT_history` "History": `hindsight.toggle_global` (label "Use Global Undo History" /
  "Use Active Object Undo History" depending on state), `hindsight.toggle_preserve_modes` "Preserve Modes",
  `hindsight.set_morph_target` "Set Morph Target Here", `hindsight.morph_brush` "Morph History Brush",
  `hindsight.delete_past` "Delete Past Undo Steps", `hindsight.reset_history` "Reset Undo History",
  `hindsight.turn_off` "Turn Off Undo History", `hindsight.reset_and_turn_off` "Reset and Turn Off",
  `hindsight.turn_on` "Turn On Undo History", `hindsight.reset_all` "Reset All Objects".
- Sidebar: `VIEW3D_PT_hindsight` category "Undo History"; sub-panels: Movie Mode (header checkbox
  `movie_mode`), Scrubber, Memory, Collection Filtering, Warnings.
- Movie: `hindsight.quick_auto_key` "Quick Auto Key".

## 7. Join info (P3) — `Step.extra` for kind JOIN

`extra = {"members": [{"uid": str, "name": str, "matrix": [16 floats], "collections": [names],
"materials": [names], "mesh_name": str}], "map_attr": "_hs_src"}`; absorbed members' pre-join
Snapshots are stored in `extra` as Snapshot objects under key `"_snapshots"` by uid (serialised by
step_to_bytes as additional snapshot blobs).

## 8. Test harness — `tests/harness.py` (main session)

```python
def fresh() -> None                      # empty scene, register the add-on (hindsight) in bpy
def make_mesh(n_verts_approx: int = 2000, name="Obj"): -> bpy object (UV sphere-like grid), active, selected
def enter(obj, mode: str) -> None        # object.mode_set
def fake_stroke(obj, seed: int, n: int = 50, channel="position") -> None   # randomly displace n verts (or mask/fset/hide/color)
def commit(obj) -> None                  # recorder.commit(obj) — what stroke-end detection calls
def snapshot(obj) -> dict[str, np.ndarray]     # all recorded channels read from the mesh
def assert_same(a: dict, b: dict) -> None       # bit-exact compare
def finish(code=0) -> None
```
