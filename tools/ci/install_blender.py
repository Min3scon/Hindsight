#!/usr/bin/env python3
"""Download (or take from a cache folder), verify and unpack an official Linux x64 Blender.

    python tools/ci/install_blender.py 5.3 --dest ~/blender --cache ~/blender-dl
    python tools/ci/install_blender.py 5.0.1 --dest ~/blender --cache ~/blender-dl --github-output

The tarball is kept in --cache under its official file name (a CI cache step keys on that name).
The SHA-256 from download.blender.org is checked when the site publishes one. Prints key=value lines:
version, exe (path of the blender binary), dir. With --github-output they also go to $GITHUB_OUTPUT.
"""
import argparse
import hashlib
import os
import re
import shutil
import sys
import tarfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resolve_blender as R  # noqa: E402


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(info, dest):
    """Download info["path"] from the mirror of the resolve step first, then the others; retry with backoff."""
    bases = [info["url"][:-len(info["path"]) - 1]] + [m for m in R.MIRRORS]
    seen, last = set(), None
    for base in bases:
        if base in seen:
            continue
        seen.add(base)
        url = f"{base}/{info['path']}"
        for attempt in range(R.RETRIES):
            try:
                print("downloading", url, file=sys.stderr)
                req = urllib.request.Request(url, headers={"User-Agent": R.USER_AGENT, "Accept": "*/*"})
                with urllib.request.urlopen(req, timeout=120) as r, open(dest + ".part", "wb") as fh:
                    shutil.copyfileobj(r, fh, 1 << 20)
                os.replace(dest + ".part", dest)
                return
            except Exception as e:
                last = e
                print(f"  attempt {attempt + 1}/{R.RETRIES} failed: {e}", file=sys.stderr)
                if attempt + 1 < R.RETRIES:
                    R.backoff(attempt)
    raise SystemExit(f"download of {info['path']} failed on every mirror: {last}")


def expected_sha256(info):
    try:
        text, _ = R.fetch_any(info["sha256_path"])
    except SystemExit:
        return None
    for line in text.splitlines():
        m = re.match(r"([0-9a-fA-F]{64})\s+\*?(\S+)", line.strip())
        if m and m.group(2) == info["filename"]:
            return m.group(1).lower()
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", help="4.2 (newest patch) or 5.0.1 (exact)")
    ap.add_argument("--dest", required=True, help="folder to unpack into")
    ap.add_argument("--cache", required=True, help="folder that holds the downloaded tarball")
    ap.add_argument("--github-output", action="store_true")
    a = ap.parse_args(argv)

    info = R.resolve(a.spec)
    os.makedirs(a.cache, exist_ok=True)
    os.makedirs(a.dest, exist_ok=True)
    tarball = os.path.join(a.cache, info["filename"])
    want = expected_sha256(info)
    if os.path.isfile(tarball) and want and sha256_of(tarball) != want:
        print("cached tarball does not match its checksum, downloading again", file=sys.stderr)
        os.remove(tarball)
    if not os.path.isfile(tarball):
        download(info, tarball)
    if want:
        got = sha256_of(tarball)
        if got != want:
            os.remove(tarball)
            raise SystemExit(f"checksum mismatch for {info['filename']}: {got} != {want}")
        print("sha256 ok", want, file=sys.stderr)
    else:
        print("no published sha256 found; skipped the check", file=sys.stderr)

    folder = info["filename"][:-len(".tar.xz")]
    root = os.path.join(a.dest, folder)
    if not os.path.isfile(os.path.join(root, "blender")):
        with tarfile.open(tarball, "r:xz") as tf:
            try:
                tf.extractall(a.dest, filter="data")      # Python 3.12+ and recent 3.11 patches
            except TypeError:
                tf.extractall(a.dest)
    exe = os.path.join(root, "blender")
    if not os.path.isfile(exe):
        raise SystemExit(f"{exe} missing after unpacking")
    lines = [f"version={info['version']}", f"exe={exe}", f"dir={root}"]
    print("\n".join(lines))
    if a.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
