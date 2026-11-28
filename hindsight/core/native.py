"""The native engine: the Rust module `hindsight_native` (source in native/ at the repository root,
shipped as wheels in hindsight/wheels). Pure Python, no bpy.

Hindsight's storage engine runs in Rust: the step container and the codecs (zstd, lz4, zlib), and
the number work of delta.py (diff, apply, packing and unpacking of records, runs of steps). There is
no Python fallback for those. If the module cannot be loaded, importing this file raises
EngineMissing with the reason, and so does importing anything that needs the engine.

ENGINE   the module (always set when this file imported)

Nothing in the shipped add-on has a Python path any more: delta.py, step.py, codec.py and
history.py call the engine. The Python versions are in tests/reference/.
"""
from __future__ import annotations

API = 18                    # the module's API number (native/src/lib.rs) this code is written for
# API 1: diff, apply, pack, unpack.  API 2: + apply_steps.  API 3: + codecs, step container,
# recompress, the exceptions CorruptStep, CorruptBlob, CorruptDelta, NewerFormatError.  API 4: step_encode
# packs the records itself (takes arrays), + record (diff, pack and codec of a stroke in one call).
# API 5: + pack_record, unpack_record; apply takes any indices (the numpy fallbacks of delta.py are gone).
# API 6: + crc32 (zlib.crc32 at 20 GB/s; registry, journal and Entry checksums).
# API 7: + Hasher128 (xxh3-128, incremental): mesh fingerprints.
# API 8: + same_values (strict equality of the data recorder's values, R070).
# API 9: + morph_dab (one dab of the Morph History brush in one pass, R083).
# API 10: + extents_into (the Movie Mode camera's extents of points, in place, R085).
# API 11: + index_build, index_read, part_build, part_read (format 5's index part and step parts, #64).
# API 12: + step_encoding, ENCODINGS, UnknownEncoding: a step's encoding from its step prefix; decoding a step
# in an encoding this build doesn't know raises UnknownEncoding, not CorruptStep; recompress leaves it (#66).
# API 13: + part_salvage: a damaged step part's steps, from its step table or by their prefix (#69).
# API 15: + Step, ChannelDelta, Snapshot, DataChange, Slice, Entry, MISSING, make_key, split_key, key_dtype,
# key_width, bits_dtype, types_setup, snapshot_from_bytes, step_from_bytes, same_step: the step's shapes, building a
# step from its blob and comparing two steps (#147).
# API 14: + json_setup, json_dumps, json_loads, records_to_bytes, records_from_bytes, step_extra, read_header:
# the JSON of a step's extra and data records and read_header's dict, with the stdlib json on the error path (#146).
# API 16: + topo_pack, topo_unpack, topo_hash, topo_pack_all, topo_packed_bytes: a mesh's topology packed, hashed and
# unpacked (topopack.py, #148).
# API 17: + Journal, journal_fault, journal_calls: the crash journal (journal.py, #152), with a test-only fault hook.
# API 18: + Journal.append_later, journal_pending, journal_drain, journal_stop, journal_writer_delay: the journal's
#   writer thread (#153); journal_calls counts what it encoded.


class EngineMissing(ImportError):
    """The Rust engine (hindsight_native, API 12) is missing, broken or the wrong version."""


def _load():
    try:
        import hindsight_native as m
    except Exception as e:          # ImportError, or a broken binary (OSError and the like)
        raise EngineMissing(
            f"Hindsight needs its Rust engine and could not load it ({type(e).__name__}: {e}). "
            "Reinstall the add-on, and check that a wheel for this platform is in its wheels folder.") from e
    api = getattr(m, "API", None)
    if api != API:
        raise EngineMissing(
            f"Hindsight needs hindsight_native API {API}, found API {api} ({getattr(m, '__file__', '?')}). "
            "Reinstall the add-on.")
    return m


ENGINE = _load()
crc32 = ENGINE.crc32        # zlib.crc32 (same values, any buffer, an optional start value), about 10x faster
