#!/usr/bin/env python3
"""Build the Hindsight extension zip and refresh the download on the website.

Usage:
    python3 scripts/build_zip.py [--blender-ext PATH] [--out dist/hindsight.zip] [--site docs]

Steps:
  1. Build <out> from hindsight/ and validate it.
       * With a usable blender_ext.py (--blender-ext, else $BLENDER_EXT) the
         official `build` + `validate` commands run.
       * With --blender-ext "" (or a path that does not exist) a stdlib-only
         fallback zips the CONTENTS of hindsight/ at the zip root and runs a
         minimal manifest validation.
  2. Copy the zip to <site>/download/hindsight.zip.
  3. Rewrite the text between <!--HS_VERSION-->...<!--/HS_VERSION--> and
     <!--HS_SIZE-->...<!--/HS_SIZE--> in <site>/index.html (if it exists).
  4. Verify the zip and print "OK <path> <size> v<version>".

Requires Python 3.11+ (tomllib). Standard library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "hindsight"
MANIFEST = "blender_manifest.toml"

# Always excluded by the fallback builder (gitignore-style patterns). The
# manifest's [build] paths_exclude_pattern is applied on top of these.
FALLBACK_EXCLUDES = ["__pycache__/", "*.pyc", ".*", "tests/"]

# Add-on tags accepted by Blender 4.2 (_bpy_internal/extensions/tags.py).
VALID_TAGS = {
    "3D View", "Add Curve", "Add Mesh", "Animation", "Bake", "Camera", "Compositing",
    "Development", "Game Engine", "Geometry Nodes", "Grease Pencil", "Import-Export",
    "Lighting", "Material", "Mesh", "Modeling", "Node", "Object", "Paint", "Physics",
    "Pipeline", "Render", "Rigging", "Scene", "Sculpt", "Sequencer", "System",
    "Text Editor", "Tracking", "UV", "User Interface",
}
VALID_PERMISSIONS = {"files", "network", "clipboard", "camera", "microphone"}


class BuildError(Exception):
    """A fatal, user-facing build problem."""


# --------------------------------------------------------------------------
# Manifest helpers
# --------------------------------------------------------------------------

def load_manifest(source: Path) -> dict:
    path = source / MANIFEST
    if not path.is_file():
        raise BuildError(f"{path} not found")
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise BuildError(f"{path}: invalid TOML: {exc}") from exc


def _terse_text_error(label: str, value: object) -> str | None:
    """Rules shared by `tagline` and every [permissions] reason."""
    if not isinstance(value, str) or not value:
        return f"{label}: must be a non-empty string"
    if len(value) > 64:
        return f"{label}: {len(value)} characters, max is 64"
    if value != value.strip():
        return f"{label}: must not have leading/trailing whitespace"
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return f"{label}: must not contain control characters"
    if not (value[-1].isalnum() or value[-1] in ")]}"):
        return f"{label}: must end with an alphanumeric character or one of )]}} (got {value[-1]!r})"
    return None


def validate_manifest(manifest: dict, source: Path) -> list[str]:
    """Minimal re-implementation of `blender_ext validate` (strict) for add-ons."""
    errors: list[str] = []
    semver = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")

    for key in ("schema_version", "id", "version", "name", "tagline", "maintainer",
                "type", "blender_version_min", "license"):
        if key not in manifest:
            errors.append(f"missing required key: {key}")

    def need_str(key: str) -> str | None:
        value = manifest.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key}: must be a non-empty string")
            return None
        return value

    schema_version = need_str("schema_version")
    if schema_version is not None and not semver.match(schema_version):
        errors.append(f"schema_version: {schema_version!r} is not semver (X.Y.Z)")

    ext_id = need_str("id")
    if ext_id is not None:
        if (not ext_id.isidentifier() or "__" in ext_id or ext_id.startswith("_")
                or ext_id.endswith("_") or ext_id != ext_id.lower()):
            errors.append(f"id: {ext_id!r} must be a lowercase python identifier "
                          "without '__' or leading/trailing '_'")

    version = need_str("version")
    if version is not None and not semver.match(version):
        errors.append(f"version: {version!r} is not semver (X.Y.Z)")

    need_str("name")
    need_str("maintainer")

    if "tagline" in manifest:
        if (err := _terse_text_error("tagline", manifest["tagline"])) is not None:
            errors.append(err)

    if "type" in manifest and manifest["type"] != "add-on":
        errors.append(f"type: must be \"add-on\", got {manifest['type']!r}")

    bvm = need_str("blender_version_min")
    if bvm is not None and not re.fullmatch(r"\d+\.\d+\.\d+", bvm):
        errors.append(f"blender_version_min: {bvm!r} must be exactly three numbers (X.Y.Z)")

    bvmax = manifest.get("blender_version_max")
    if bvmax is not None and not (isinstance(bvmax, str) and re.fullmatch(r"\d+\.\d+\.\d+", bvmax)):
        errors.append(f"blender_version_max: {bvmax!r} must be exactly three numbers (X.Y.Z)")

    def need_str_list(key: str, *, non_empty: bool) -> list | None:
        value = manifest.get(key)
        if value is None:
            return None
        if (not isinstance(value, list) or (non_empty and not value)
                or not all(isinstance(v, str) and v.strip() for v in value)):
            errors.append(f"{key}: must be {'a non-empty ' if non_empty else 'a '}list of non-empty strings")
            return None
        return value

    need_str_list("license", non_empty=True)

    copyright_list = need_str_list("copyright", non_empty=False)
    for i, text in enumerate(copyright_list or []):
        year, _, who = text.partition(" ")
        if not re.fullmatch(r"\d+(?:-\d+)?", year) or not who.strip():
            errors.append(f"copyright[{i}]: {text!r} must be 'YYYY' or 'YYYY-YYYY', a space, then a name")

    tags = need_str_list("tags", non_empty=False)
    for tag in tags or []:
        if tag not in VALID_TAGS:
            errors.append(f"tags: {tag!r} is not a valid Blender add-on tag")

    platforms = need_str_list("platforms", non_empty=False)
    _ = platforms

    website = manifest.get("website")
    if website is not None and not (isinstance(website, str) and re.match(r"^https?://\S+$", website)):
        errors.append(f"website: {website!r} must be an http(s) URL")

    wheels = need_str_list("wheels", non_empty=False)
    for wheel in wheels or []:
        if not wheel.endswith(".whl") or "\\" in wheel:
            errors.append(f"wheels: {wheel!r} must end with .whl and use forward slashes")
        elif not (source / wheel).is_file():
            errors.append(f"wheels: {wheel!r} does not exist in the source dir")

    perms = manifest.get("permissions")
    if perms is not None:
        if not isinstance(perms, dict):
            errors.append("permissions: must be a table")
        else:
            for key, reason in perms.items():
                if key not in VALID_PERMISSIONS:
                    errors.append(f"permissions: unknown permission {key!r}")
                if (err := _terse_text_error(f"permissions.{key}", reason)) is not None:
                    errors.append(err)

    build = manifest.get("build")
    if build is not None:
        if not isinstance(build, dict):
            errors.append("build: must be a table")
        elif "paths" in build and "paths_exclude_pattern" in build:
            errors.append("build: declaring both 'paths' and 'paths_exclude_pattern' is not supported")

    if not (source / "__init__.py").is_file():
        errors.append("__init__.py is missing at the root of the source dir")

    return errors


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------

def run_checked(cmd: list[str], what: str) -> None:
    print("+", " ".join(cmd), flush=True)
    try:
        proc = subprocess.run(cmd, check=False)
    except OSError as exc:
        raise BuildError(f"{what}: could not run {cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise BuildError(f"{what} failed (exit code {proc.returncode})")


def build_with_blender_ext(blender_ext: Path, source: Path, out: Path) -> None:
    if sys.version_info < (3, 11):
        raise BuildError("blender_ext.py needs Python 3.11+")
    base = [sys.executable, str(blender_ext)]
    # Plain Python skips tag checks unless told where Blender keeps its tag list.
    tags_py = blender_ext.parents[3] / "modules" / "_bpy_internal" / "extensions" / "tags.py"
    tags_args = ["--valid-tags", str(tags_py)] if tags_py.is_file() else []
    out.unlink(missing_ok=True)
    run_checked(base + ["build", "--source-dir", str(source), "--output-filepath", str(out)] + tags_args,
                "blender_ext build")
    run_checked(base + ["validate", str(out)] + tags_args, "blender_ext validate")


def _compile_exclude(pattern: str) -> tuple[bool, bool, str]:
    """Return (anchored, dir_only, glob) for a gitignore-style pattern."""
    anchored = pattern.startswith("/")
    dir_only = pattern.endswith("/")
    glob = pattern.strip("/")
    if "/" in glob:
        anchored = True
    return anchored, dir_only, glob


def is_excluded(rel_parts: tuple[str, ...], is_dir: bool, patterns: list[tuple[bool, bool, str]]) -> bool:
    """True if the path (as components relative to the source dir) matches a pattern."""
    for anchored, dir_only, glob in patterns:
        if anchored:
            # Match the path prefix: a pattern excludes the entry itself and everything below it.
            glob_parts = glob.split("/")
            for depth in range(1, len(rel_parts) + 1):
                if depth != len(glob_parts):
                    continue
                if all(fnmatch.fnmatchcase(p, g) for p, g in zip(rel_parts[:depth], glob_parts)):
                    if not dir_only or depth < len(rel_parts) or is_dir:
                        return True
        else:
            # Unanchored: any component may match (directories only if dir_only).
            for depth, part in enumerate(rel_parts, start=1):
                part_is_dir = depth < len(rel_parts) or is_dir
                if dir_only and not part_is_dir:
                    continue
                if fnmatch.fnmatchcase(part, glob):
                    return True
    return False


def build_with_fallback(source: Path, out: Path, manifest: dict) -> None:
    errors = validate_manifest(manifest, source)
    if errors:
        raise BuildError("manifest validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    extra = (manifest.get("build") or {}).get("paths_exclude_pattern") or []
    patterns = [_compile_exclude(p) for p in FALLBACK_EXCLUDES + [p for p in extra if isinstance(p, str)]]

    files: list[tuple[Path, str]] = []
    for dirpath, dirnames, filenames in os.walk(source):
        rel_dir = Path(dirpath).relative_to(source).parts
        dirnames[:] = sorted(d for d in dirnames
                             if not is_excluded(rel_dir + (d,), True, patterns))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink() or is_excluded(rel_dir + (name,), False, patterns):
                continue
            files.append((path, "/".join(rel_dir + (name,))))
    files.sort(key=lambda item: item[1])

    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path, arcname in files:
            zf.write(path, arcname)
    print(f"fallback builder: wrote {len(files)} files to {out}")


# --------------------------------------------------------------------------
# Website + verification
# --------------------------------------------------------------------------

def human_size(num_bytes: int) -> str:
    kb = max(1, round(num_bytes / 1024))
    if kb < 1024:
        return f"{kb} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def update_index(index: Path, version: str, size: str) -> None:
    if not index.is_file():
        print(f"note: {index} does not exist yet; skipping version/size text update")
        return
    with open(index, encoding="utf-8", newline="") as fh:
        text = fh.read()
    original = text
    counts = {}
    for marker, value in (("HS_VERSION", version), ("HS_SIZE", size)):
        pattern = re.compile(rf"(<!--{marker}-->).*?(<!--/{marker}-->)", re.DOTALL)
        text, counts[marker] = pattern.subn(lambda m, v=value: m.group(1) + v + m.group(2), text)
    if text != original:
        with open(index, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    print(f"{index}: updated {counts['HS_VERSION']}x HS_VERSION -> {version}, "
          f"{counts['HS_SIZE']}x HS_SIZE -> {size}")
    for marker, n in counts.items():
        if n == 0:
            print(f"note: no <!--{marker}--> marker found in {index}")


def verify_zip(path: Path, version: str) -> None:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BuildError(f"{path} is not a valid zip: {exc}") from exc
    with zf:
        bad = zf.testzip()
        if bad is not None:
            raise BuildError(f"{path}: corrupt member {bad}")
        names = set(zf.namelist())
        for required in (MANIFEST, "__init__.py"):
            if required not in names:
                raise BuildError(f"{path}: {required} is not at the zip root")
        junk = sorted(n for n in names if "__pycache__/" in n or n.endswith(".pyc"))
        if junk:
            raise BuildError(f"{path}: contains compiled files: {', '.join(junk[:5])}")
        packed = tomllib.loads(zf.read(MANIFEST).decode("utf-8"))
        if packed.get("version") != version:
            raise BuildError(f"{path}: manifest version {packed.get('version')!r} != source {version!r}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def resolve_blender_ext(arg: str | None) -> Path | None:
    """Pick the blender_ext.py to use, or None for the pure-python fallback."""
    if arg is None:
        arg = os.environ.get("BLENDER_EXT")
    if not arg:
        return None
    path = Path(arg)
    if path.is_file():
        return path
    print(f"note: blender_ext.py not found at {path}; using the pure-python fallback builder")
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build hindsight.zip and refresh the website download.")
    ap.add_argument("--blender-ext", default=None, metavar="PATH",
                    help="path to blender_ext.py (default: $BLENDER_EXT); "
                         "pass an empty string to force the pure-python fallback builder")
    ap.add_argument("--out", default=str(ROOT / "dist" / "hindsight.zip"), metavar="ZIP",
                    help="output zip (default: dist/hindsight.zip)")
    ap.add_argument("--site", default=str(ROOT / "docs"), metavar="DIR",
                    help="website directory (default: docs)")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE), help=argparse.SUPPRESS)  # for tests
    args = ap.parse_args(argv)

    if sys.version_info < (3, 11):
        print("error: Python 3.11+ is required", file=sys.stderr)
        return 1

    source = Path(args.source).resolve()
    out = Path(args.out).resolve()
    site = Path(args.site).resolve()

    try:
        if not source.is_dir():
            raise BuildError(f"source directory {source} not found")
        manifest = load_manifest(source)
        if not (source / "__init__.py").is_file():
            raise BuildError(f"{source / '__init__.py'} not found: an add-on needs __init__.py "
                             "at the root of the extension folder")

        out.parent.mkdir(parents=True, exist_ok=True)
        blender_ext = resolve_blender_ext(args.blender_ext)
        if blender_ext is not None:
            print(f"builder: {blender_ext}")
            build_with_blender_ext(blender_ext, source, out)
        else:
            print("builder: pure-python fallback")
            build_with_fallback(source, out, manifest)

        version = str(manifest["version"])
        size = human_size(out.stat().st_size)

        download = site / "download" / "hindsight.zip"
        download.parent.mkdir(parents=True, exist_ok=True)
        if not (download.exists() and download.samefile(out)):
            shutil.copyfile(out, download)
        update_index(site / "index.html", version, size)

        verify_zip(out, version)
        verify_zip(download, version)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"OK {out} {size} v{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
