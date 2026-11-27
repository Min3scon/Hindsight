# Rust engine design (R2 to R6)

Status: M1 implemented in this branch. M2 to M6 are design only. The rewrite of everything else (#24) is planned in `dev/RUST_PLAN.md`, which replaces "What should not move" and "M3 revisited" below.

## Goal

The owner decided Rust is required. Core computation and data processing run in Rust. Python keeps only what needs the Blender API, plus thin glue that converts Python objects (dicts, numpy arrays) to and from buffers. No fallback: a Python copy of moved logic lives in `tests/reference/` as a bit-exact oracle, and a test fails if anything under `hindsight/` imports it. If the engine cannot load, `hindsight/core/native.py` raises one exception, `EngineMissing`, with the reason.

## Modules (crate `native/`, one cdylib `hindsight_native`)

| file | content | milestone |
|---|---|---|
| `lib.rs` | module init, API number, exceptions, `guard` (panic to RuntimeError), existing delta functions | now |
| `codec.rs` | none, zlib, lz4, zstd: compress, decompress, bounded sizes | M1 |
| `step.rs` | step container: header, CRC, kinds, formats 1 to 4, snapshot parts, v1 records | M1 |
| `record.rs` | one call per stroke: diff, pack, step encode, compress | M2 |
| `history.rs` | `#[pyclass] History`: entries, cursor, goto, caps, tiers | M3 |
| `packer.rs` | background recompression thread | M4 |
| `journal.rs`, `registry.rs`, `replay.rs` | journal framing, registry stream, replay | M5 |
| `timeline.rs`, `framing.rs`, `morph.rs` | global timeline, camera extents, morph blend | M6 |

## Thin facade

`step.py`, `codec.py`, `delta.py`, `history.py`, `store.py` keep their public names and signatures. Each public function validates arguments that are Python objects, calls one native function, and wraps the result into the existing dataclasses (`Step`, `ChannelDelta`, `Snapshot`, `DataChange`). No algorithm stays in the facade. JSON for `Step.extra` and data records stays in Python in M1 because the values are Python objects (dicts with `MISSING`); Rust treats them as opaque bytes. Moving the JSON layer is only worth it once records are produced by Rust (M5).

## Objects and ownership

Step bytes are immutable `bytes` objects owned by Python (an `Entry.blob`). Rust reads them through the buffer protocol and never keeps a pointer after the call returns. Decoded arrays are allocated by Rust as `bytearray` (writable, 8 byte aligned, exactly sized) and handed to Python, where `np.frombuffer` wraps them with no copy; the bytearray is the numpy base object. From M3 on, `History` is a `#[pyclass]` that owns its entry list: blobs as `Arc<[u8]>`, raw steps as Rust structs, each with a byte count. Python holds a handle, never a second copy.

## Zero-copy borrows (numpy to Rust)

Existing rule (`Buf` in `lib.rs`): a view is taken with `PyObject_GetBuffer`, released in `Drop`, never stored. Writable views (apply targets) must be C-contiguous, are checked for overlap with every other view of the call, and are held only inside one call. From M3 on a state array handed to `goto` stays borrowed for the call only.

## GIL rules

1. Everything that reads or writes Python objects runs with the GIL: buffer acquisition, allocation of result objects, exception creation.
2. Everything else runs in `py.allow_threads`: CRC, compress, decompress, delta decode, diff, pack. The closure gets plain `&[u8]` slices and raw output pointers wrapped in a `Send` struct. Output buffers are allocated under the GIL first, so no Python API is called without it.
3. A Rust thread (M4 packer) never touches a `PyObject`. It owns `Arc<[u8]>` and `Vec<u8>` only and posts results through a channel that the main thread drains under the GIL.
4. A panic never crosses the FFI boundary: `guard` turns it into `RuntimeError("hindsight_native: internal error")`.

## Errors

Rust defines four exception classes, all `ValueError` subclasses, exported by the module: `CorruptBlob` (codec data damaged), `CorruptDelta` (record damaged), `CorruptStep` (container or header damaged; a delta or blob error inside a step is re-raised as `CorruptStep` with the old message form), and `NewerFormatError` (a format number above what this build reads; used by the registry reader in M5, defined in M1). The Python modules re-export the same class objects, so `except step.CorruptStep` keeps working. Messages stay the same as the Python text where tests or users saw them.

## Memory accounting

Every length read from data is checked in `u128` or with `get`, before any allocation. Allocation uses `try_reserve`. A body is bounded as before: 1 GB and 65,536 times the payload. Peak memory of an encode is one body buffer plus its compressed form, never the parts joined twice. Entry byte counts stay what `Entry.nbytes()` reports today; in M3 `History` computes them in Rust with the same formula, so caps and eviction do not change.

## Milestones

M1 (done here): step container and codecs in Rust, facade, reference copy, equivalence tests.
M2: `record(arrays_old, arrays_new, meta, codec) -> blob` in one call per stroke: diff, pack, compress, with the GIL released after the arrays are borrowed. Journal bytes come from the same call.
M3: native `History` behind today's API (`goto`, `append`, `truncate`, `drop`, caps, tiers). `history.py` becomes a facade.
M4: packer as a Rust thread with the idle policy of `store.py`; Python only submits and swaps.
M5: journal writer and crash-tolerant reader, registry stream (read 1 to 4, write 4), replay.
M6: global timeline, framing extents, morph blending. Python passes arrays.

## What should not move

Anything that needs `bpy`: `foreach_get` and `foreach_set`, RNA reads in `datarec` and `adapters`, handlers, timers, operators, UI, drawing. Python object marshalling of `Step.extra` and `DataChange` values. Settings and preferences. Rust is not used where the cost is a Blender call.

## Compatibility

Files from beta 2 and from the current release load bit for bit: the reader accepts step formats 1 to 4. Bytes written by the engine stay what the Python code wrote, checked per codec by tests that encode the same step with the reference and the engine and compare bytes; where a library version could change bytes (zstd, zlib, lz4 frames), tests assert that both decode to the same data and report whether bytes are equal. Six abi3 wheels (Linux x86-64 and ARM64, Windows x86-64 and ARM64, macOS x86-64 and arm64) ship in `hindsight/wheels`, listed in the manifest. The crate links zstd, zlib and lz4 from source (C, built with zig for every target). No new user dependency: `zstandard` and `lz4` Python wheels are no longer used.

## Multires step (Blender side, no engine change)

A visit to Sculpt Mode on an object with a Multires modifier is one step, because a stroke changes the displacement layers (CD_MDISPS), which are not attributes and have no diff. Preference "Record Multires", off by default.

The step is a TOPO step (extra `{"multires": 1}`): `before` and `after` snapshots, each with one int8 channel `multires:blob|POINT|INT8` holding the mesh datablock as the bytes of a .blend file (`bpy.data.libraries.write`), compressed with the engine's zstd before it enters the Step, so the raw tier and the caps count the compressed size. Restore is `libraries.load` plus `user_remap`, then the copies the load made of the materials are dropped. The step container, journal and save need nothing new: it is a TOPO step with an int8 array. The state of such an object holds the blob as its only array. The recorder commits nothing else on a Multires object while the preference is on (`commit` returns None unless it is given the visit's step).

## M3 revisited (measured): a native History object does not pay

Profile of `History.goto` over a mixed 100k-vertex history (1,500 packed steps, 2% TOPO, 2% DATA; `mixprof.py`): 105 ms for a full jump, of which 61% is the engine decoding the 60 TOPO and DATA steps, 31% `apply_steps`, and about 3% Python (`_cross_runs`, `_mesh`, `load`). A TOPO step of 100k vertices (2.4 MB raw) takes 1.24 ms in `step_decode`; the zstd decompression alone takes 1.27 ms, and the `set_snapshot` copy 0.07 ms. So the glue a native `History` would replace costs under 5% of a jump, and what it would call (decompress, apply) is already native. Entries also hold Python Step objects (the raw tier), which the runtime and tests edit directly (`h.entries`), so owning them in Rust would add a conversion at every access. M3 became: History uses the engine only, the Python loop is `tests/reference/history.py` (the oracle of the twin-history tests), `native.lib` and `HINDSIGHT_NATIVE` no longer select anything. Caps and eviction (`store.py`) stay Python: they do O(1) work per commit and a pass every 64 commits.
