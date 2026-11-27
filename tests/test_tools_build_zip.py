"""scripts/build_zip.py builds the zip and nothing else: no website download or page is written. No bpy."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import shutil
import tempfile
import traceback
import zipfile
from pathlib import Path

import build_zip as bz


def test_builds_the_zip_only():
    docs = Path(ROOT).resolve() / "docs"
    before = sorted(p.relative_to(docs).as_posix() for p in docs.rglob("*")) if docs.exists() else None
    tmp = Path(tempfile.mkdtemp())
    try:
        out = tmp / "hindsight.zip"
        assert bz.main(["--blender-ext", "", "--out", str(out)]) == 0
        assert zipfile.ZipFile(out).testzip() is None
        assert sorted(p.name for p in tmp.iterdir()) == ["hindsight.zip"]
        after = sorted(p.relative_to(docs).as_posix() for p in docs.rglob("*")) if docs.exists() else None
        if after != before:      # put the repo back before failing
            shutil.rmtree(docs / "download", ignore_errors=True)
            if before is None and docs.exists() and not any(docs.iterdir()):
                docs.rmdir()
        assert after == before, f"build_zip wrote into docs/: {after}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS test_builds_the_zip_only")


def test_no_site_option():
    try:
        bz.main(["--blender-ext", "", "--site", "x"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("--site is still accepted")
    print("PASS test_no_site_option")


def main():
    ok = True
    for t in (test_builds_the_zip_only, test_no_site_option):
        try:
            t()
        except BaseException:
            ok = False
            print("FAIL", t.__name__)
            traceback.print_exc()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
