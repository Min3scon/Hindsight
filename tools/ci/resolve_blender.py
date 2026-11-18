#!/usr/bin/env python3
"""Find the newest Blender release of a series on download.blender.org.

    python tools/ci/resolve_blender.py 4.2            # newest 4.2.x
    python tools/ci/resolve_blender.py 5.0.1          # exactly 5.0.1
    python tools/ci/resolve_blender.py 5.3 --github-output   # also append key=value lines to $GITHUB_OUTPUT
    python tools/ci/resolve_blender.py 5.2 --listing-file page.html   # offline (tests)

Prints key=value lines: version, series, filename, url, sha256_url, platform.
Needs only the standard library. A bare "4.2" means the newest patch release, "4.2.23" means that one.
"""
import argparse
import os
import re
import sys
import urllib.request

MIRROR = os.environ.get("BLENDER_MIRROR", "https://download.blender.org/release").rstrip("/")
# Official file name suffix per platform.
SUFFIX = {
    "linux-x64": "linux-x64.tar.xz",
    "windows-x64": "windows-x64.zip",
    "macos-arm64": "macos-arm64.dmg",
    "macos-x64": "macos-x64.dmg",
}


def fetch(url: str, timeout: int = 60) -> str:
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hindsight-ci"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # network hiccups on shared runners
            last = e
    raise SystemExit(f"cannot read {url}: {last}")


def series_of(spec: str) -> str:
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", spec.strip())
    if not m:
        raise SystemExit(f"bad version {spec!r}; use 4.2 or 5.0.1")
    return f"{m.group(1)}.{m.group(2)}"


def pick(listing: str, spec: str, platform: str):
    """Return (version, filename) for the newest (or the exact) release in a directory listing."""
    suffix = SUFFIX[platform]
    found = {}
    for m in re.finditer(r"blender-(\d+)\.(\d+)\.(\d+)-" + re.escape(suffix), listing):
        found[tuple(int(x) for x in m.groups())] = m.group(0)
    if not found:
        return None
    if spec.count(".") == 2:
        want = tuple(int(x) for x in spec.split("."))
        return (spec, found[want]) if want in found else None
    major, minor = (int(x) for x in spec.split("."))
    own = {k: v for k, v in found.items() if k[:2] == (major, minor)}
    if not own:
        return None
    best = max(own)
    return ".".join(map(str, best)), own[best]


def resolve(spec: str, platform: str = "linux-x64", listing=None) -> dict:
    series = series_of(spec)
    base = f"{MIRROR}/Blender{series}"
    if listing is None:
        listing = fetch(base + "/")
    got = pick(listing, spec, platform)
    if got is None:
        raise SystemExit(f"no {platform} release matching {spec} in {base}/")
    version, filename = got
    return {"version": version, "series": series, "filename": filename, "platform": platform,
            "url": f"{base}/{filename}", "sha256_url": f"{base}/blender-{version}.sha256"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--platform", default="linux-x64", choices=sorted(SUFFIX))
    ap.add_argument("--listing-file", help="read this HTML instead of the network (tests)")
    ap.add_argument("--github-output", action="store_true", help="append the result to $GITHUB_OUTPUT")
    a = ap.parse_args(argv)
    listing = open(a.listing_file, encoding="utf-8").read() if a.listing_file else None
    res = resolve(a.spec, a.platform, listing)
    lines = [f"{k}={v}" for k, v in res.items()]
    print("\n".join(lines))
    if a.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        if not out:
            raise SystemExit("--github-output needs $GITHUB_OUTPUT")
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
