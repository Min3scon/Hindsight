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
import random
import sys
import time
import urllib.request

# Official sources, tried in this order. All serve the same tree (Blender<series>/blender-<v>-<platform>,
# blender-<v>.sha256). BLENDER_MIRROR (one URL, or several separated by spaces or commas) goes first.
# download.blender.org answered 403 to one CI job while it answered another a minute earlier, so every
# read retries with backoff and then falls back to the next mirror.
OFFICIAL = [
    "https://download.blender.org/release",
    "https://ftp.nluug.nl/pub/graphics/blender/release",
    "https://mirror.clarkson.edu/blender/release",
    "https://mirror.freedif.org/blender/release",
]
MIRRORS = [m.rstrip("/") for m in re.split(r"[\s,]+", os.environ.get("BLENDER_MIRROR", "")) if m] + OFFICIAL
MIRROR = MIRRORS[0]
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36 hindsight-ci")
RETRIES = int(os.environ.get("BLENDER_RETRIES", "4"))
# Official file name suffix per platform.
SUFFIX = {
    "linux-x64": "linux-x64.tar.xz",
    "windows-x64": "windows-x64.zip",
    "macos-arm64": "macos-arm64.dmg",
    "macos-x64": "macos-x64.dmg",
}


def backoff(attempt: int) -> None:
    time.sleep(min(2 ** attempt, 30) * (0.75 + random.random() / 2))


def fetch(url: str, timeout: int = 60) -> str:
    """Read one URL with retries and exponential backoff (a 403 or 5xx from a CDN is often brief)."""
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # network hiccups on shared runners
            last = e
            print(f"fetch {url} attempt {attempt + 1}/{RETRIES}: {e}", file=sys.stderr)
            if attempt + 1 < RETRIES:
                backoff(attempt)
    raise SystemExit(f"cannot read {url}: {last}")


def fetch_any(path: str, timeout: int = 60):
    """Read <mirror>/<path> from the first mirror that answers. Returns (text, base)."""
    errors = []
    for base in MIRRORS:
        try:
            return fetch(f"{base}/{path}", timeout), base
        except SystemExit as e:
            errors.append(str(e))
    raise SystemExit("no mirror answered:\n  " + "\n  ".join(errors))


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
        listing, root = fetch_any(f"Blender{series}/")
        base = f"{root}/Blender{series}"
    got = pick(listing, spec, platform)
    if got is None:
        raise SystemExit(f"no {platform} release matching {spec} in {base}/")
    version, filename = got
    return {"version": version, "series": series, "filename": filename, "platform": platform,
            "url": f"{base}/{filename}", "sha256_url": f"{base}/blender-{version}.sha256",
            "path": f"Blender{series}/{filename}", "sha256_path": f"Blender{series}/blender-{version}.sha256"}


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
