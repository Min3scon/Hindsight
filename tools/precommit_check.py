"""Pre-commit safety check: large files, secrets and machine/personal paths.

Usage:
    python tools/precommit_check.py          # check the staged changes (what the next commit will contain)
    python tools/precommit_check.py --all    # check every tracked file

Exit code 0 = clean, 1 = something must be fixed before committing.

Extra private patterns (one regex per line, e.g. your OS username) can be listed in
.tools/precheck_private.txt. That folder is git-ignored, so the patterns themselves never get published.

A line can opt out of the path/secret scan with the marker `precheck: allow` (use sparingly, and only for
lines that are provably not personal data, e.g. a regex literal).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 5 * 1024 * 1024
PRIVATE_PATTERNS = ROOT / ".tools" / "precheck_private.txt"
ALLOW_MARKER = "precheck: allow"

# Folders that must never be committed, even if someone force-adds them.
FORBIDDEN_DIRS = (".tools/", ".venv/", "node_modules/", ".blender_profiles/", "test_models/",
                  "my_models_copies/", "test_screens_raw/", "build/", "logs/", "dist/", ".cache/")

# Internal working docs may name the local workspace (D:\Hindsight, D:\Hindsight-backup, D:\Hindsight-work).
# Every other file is treated as public and may not contain any drive-letter path.
INTERNAL_DOCS = {"HANDOFF.md", "DEFECTS.md", "DECISIONS.md", "TEST_ROUNDS.md", "STYLE.md", "SITE_PLAN.md",
                 "ROLLBACK.md"}
WORKSPACE_PATH = re.compile(r"\bD:[\\/]Hindsight(?:-backup|-work)?\b", re.IGNORECASE)

SECRET_PATTERNS = [
    ("GitHub token", re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,})")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Anthropic/OpenAI key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("hard-coded credential", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|passw(?:or)?d|auth[_-]?token|access[_-]?token)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]")),
]

PATH_PATTERNS = [
    ("Windows user folder", re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]")),
    ("Windows user folder", re.compile(r"(?i)(?:^|[\s'\"(=])/[a-z]/Users/")),
    ("AppData path", re.compile(r"(?i)\bAppData[\\/]")),
    ("home folder", re.compile(r"(?:^|[\s'\"(=`])/home/[A-Za-z0-9._-]+/")),
    ("macOS home folder", re.compile(r"(?:^|[\s'\"(=`])/Users/[A-Za-z0-9._-]+/")),
    ("agent scratch path", re.compile(r"(?i)/tmp/claude|\bscratchpad[\\/]")),
]
DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?![\\/])")


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def file_list(all_files: bool) -> list[str]:
    if all_files:
        out = git("ls-files", "-z")
    else:
        out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.decode("utf-8").split("\0") if p]


def read_blob(path: str, all_files: bool) -> bytes:
    if all_files:
        return (ROOT / path).read_bytes()
    return git("show", f":{path}")


def load_private_patterns() -> list[tuple[str, re.Pattern]]:
    if not PRIVATE_PATTERNS.is_file():
        return []
    pats = []
    for line in PRIVATE_PATTERNS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pats.append(("private pattern", re.compile(line, re.IGNORECASE)))
    return pats


def in_forbidden_dir(path: str) -> bool:
    return any(path.startswith(d) or f"/{d}" in path for d in FORBIDDEN_DIRS)


def scan_text(path: str, text: str, private: list) -> list[str]:
    problems = []
    internal = Path(path).name in INTERNAL_DOCS and "/" not in path
    checks = SECRET_PATTERNS + PATH_PATTERNS + private
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for label, rx in checks:
            if rx.search(line):
                problems.append(f"{path}:{n}: {label}: {line.strip()[:120]}")
        stripped = WORKSPACE_PATH.sub("", line) if internal else line
        if DRIVE_PATH.search(stripped):
            problems.append(f"{path}:{n}: drive-letter path: {line.strip()[:120]}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="check every tracked file instead of the staged ones")
    args = ap.parse_args()

    self_path = Path(__file__).resolve().relative_to(ROOT).as_posix()
    private = load_private_patterns()
    problems = []
    files = file_list(args.all)
    for path in files:
        if in_forbidden_dir(path):
            problems.append(f"{path}: lives in a local-only folder; never commit it")
            continue
        data = read_blob(path, args.all)
        if len(data) > MAX_BYTES:
            problems.append(f"{path}: {len(data) / 1e6:.1f} MB is over the 5 MB limit")
        if b"\0" in data[:8192]:
            continue  # binary; size-checked only
        if path == self_path:
            continue  # this file holds the patterns themselves
        problems.extend(scan_text(path, data.decode("utf-8", errors="replace"), private))

    # COVERAGE.md may not claim more than the committed evidence shows (tools/make_evidence.py)
    if (ROOT / "tools" / "make_evidence.py").is_file() and (ROOT / "COVERAGE.md").is_file():
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_evidence.py"), "--check"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            problems += ["evidence: " + line for line in r.stdout.strip().splitlines()
                         if line.startswith("make_evidence:")] or ["evidence: make_evidence.py --check failed"]

    scope = "tracked" if args.all else "staged"
    if problems:
        print(f"precommit_check: {len(problems)} problem(s) in {len(files)} {scope} file(s):")
        for p in problems:
            print("  " + p)
        return 1
    print(f"precommit_check: ok ({len(files)} {scope} file(s), private patterns: {len(private)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
