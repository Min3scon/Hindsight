"""Copy the owner's own .blend files (and what they reference) into my_models_copies/.

Runs in Blender's Python because some files are zstd-compressed:
    blender -b --factory-startup --python tests_ui/copy_own_models.py -- LIST_FILE OUT_DIR MANIFEST

Rules (from the brief): an original is only ever read byte for byte to copy it. It is never
opened in Blender, saved, moved or deleted. References are found by scanning the COPY's raw
bytes for file paths:
  * relative paths (//textures/x.png, //../lib.blend) are copied next to the copy at the same
    relative place, so they keep working;
  * absolute image paths are copied into the copy's folder and listed for remapping (the round
    scenario remaps them in the copy before anything draws);
  * a linked library with an absolute path would make Blender read another original .blend on
    open, so such a file is skipped as "can't be copied safely".
The manifest (JSON) lists every copy and what happened. Nothing here goes into git.
"""
import gzip
import json
import os
import re
import shutil
import sys

args = sys.argv[sys.argv.index("--") + 1:]
LIST, OUT, MANIFEST = args[0], os.path.abspath(args[1]), args[2]
EXTS = (b"blend", b"png", b"jpg", b"jpeg", b"exr", b"tga", b"tif", b"tiff", b"hdr", b"bmp", b"webp", b"abc", b"vdb")
PATH_RE = re.compile(rb"((?://|[A-Za-z]:[\\/])[^\x00\r\n\"<>|*?]{1,900}?\.(?:" + b"|".join(EXTS) + rb"))\x00", re.I)


def raw_bytes(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] == b"\x28\xb5\x2f\xfd":
        import zstandard
        return zstandard.ZstdDecompressor().stream_reader(data).read()
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def is_other_user_blend(ref, orig):
    """True only for an existing .blend of the user's that Blender would read when opening the copy.
    Not: the file's own saved path, Blender's bundled assets (brush weak references), the paste
    buffer (copybuffer.blend in the temp folder), or paths that don't exist."""
    r = os.path.normcase(os.path.normpath(ref))
    if r == os.path.normcase(os.path.normpath(orig)) or not os.path.isfile(ref):
        return False
    low = r.lower()
    if "datafiles" in low or "blender foundation" in low or "steamapps" in low or low.endswith("copybuffer.blend"):
        return False
    return True


def main():
    originals = [ln.strip() for ln in open(LIST, encoding="utf-8") if ln.strip()]
    os.makedirs(OUT, exist_ok=True)
    out = []
    for i, orig in enumerate(originals, 1):
        rec = {"n": i, "name": os.path.basename(orig), "size_mb": round(os.path.getsize(orig) / 2**20, 2)}
        try:
            # Scan a first copy for references, then decide where the copy has to live.
            stage = os.path.join(OUT, f"_stage_{i}.blend")
            shutil.copyfile(orig, stage)
            data = raw_bytes(stage)
            refs = sorted({m.group(1).decode("utf-8", "replace") for m in PATH_RE.finditer(data)})
            own = os.path.basename(orig).lower()
            refs = [r for r in refs if os.path.basename(r.replace("\\", "/")).lower() != own]
            libs_abs = [r for r in refs if r.lower().endswith(".blend") and not r.startswith("//")
                        and is_other_user_blend(r, orig)]
            if libs_abs:
                os.remove(stage)
                rec.update(status="skipped", reason="links a library by absolute path (would read another original)")
                out.append(rec)
                continue
            up = 0
            for r in refs:
                if r.startswith("//"):
                    up = max(up, len(re.findall(r"\.\.[\\/]", r[2:])))
            folder = os.path.join(OUT, f"{i:02d}", *(["_"] * up))
            os.makedirs(folder, exist_ok=True)
            copy = os.path.join(folder, os.path.basename(orig))
            shutil.move(stage, copy)
            src_dir = os.path.dirname(orig)
            copied, missing, absolute = [], [], {}
            for r in refs:
                if r.startswith("//"):
                    rel = r[2:].replace("\\", "/")
                    src = os.path.normpath(os.path.join(src_dir, rel))
                    dst = os.path.normpath(os.path.join(folder, rel))
                    if os.path.isfile(src):
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copyfile(src, dst)
                        copied.append(rel)
                    else:
                        missing.append(rel)
                elif r.lower().endswith(".blend"):
                    continue    # weak reference (own path, bundled asset, paste buffer): nothing to copy
                else:
                    if os.path.isfile(r):
                        dst = os.path.join(folder, "_abs", os.path.basename(r))
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copyfile(r, dst)
                        absolute[r] = dst
                    else:
                        missing.append(os.path.basename(r))
            rec.update(status="copied", copy=copy, refs=len(refs), copied=len(copied), missing=len(missing),
                       absolute_remap=absolute, compressed=data[:7] != b"BLENDER" and False)
        except Exception as e:
            rec.update(status="skipped", reason=f"copy failed: {e}")
        out.append(rec)
        print(f"{i}: {rec['status']} {rec.get('reason', '')} refs={rec.get('refs')} copied={rec.get('copied')} "
              f"missing={rec.get('missing')} abs={len(rec.get('absolute_remap', {}))}")
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)


main()
