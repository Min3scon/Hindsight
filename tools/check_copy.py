#!/usr/bin/env python3
"""Check every public piece of text against STYLE.md. Exit code 1 if anything breaks a rule.

    python tools/check_copy.py            # everything
    python tools/check_copy.py --list     # also print what was scanned

Scanned: README.md, CHANGELOG.md, .github/release-notes/*.md, .github/ISSUE_TEMPLATE/*.yml, the add-on
manifest tagline, and the add-on's user-facing strings (every string literal in hindsight/**/*.py except
docstrings and identifier-like values, plus the docstrings Blender shows as tooltips). Internal docs
(STYLE.md, DECISIONS.md, HANDOFF.md, docs/adr/, ...) are not scanned.
"""
import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BANNED = [
    "seamless", "seamlessly", "elevate", "unlock", "empower", "leverage", "harness", "streamline",
    "revolutionise", "revolutionize", "revolutionary", "cutting-edge", "cutting edge", "state-of-the-art",
    "robust", "powerful", "game-changer", "game changer", "next-level", "supercharge", "delve", "tapestry",
    "landscape", "realm", "journey", "crafted", "meticulously", "meticulous", "effortlessly", "effortless",
    "comprehensive", "holistic", "in today's fast-paced world", "whether you're", "say goodbye to",
    "look no further", "it's worth noting", "worth noting", "not just", "more than just", "at its core",
    "testament", "pivotal", "crucial", "intricate", "vibrant", "showcase", "showcasing", "underscore",
    "underscores", "foster", "fostering", "enhance", "enhancing", "multifaceted", "boasts", "unleash",
    "unparalleled", "best-in-class", "world-class", "next-gen", "ever-evolving", "dive into", "deep dive",
    "in summary", "in conclusion", "moreover", "furthermore", "additionally", "it's important to note",
    "serves as", "stands as", "nestled", "breathtaking", "stunning", "elevated", "transformative",
    "empowering", "game changing", "take your", "to the next level", "magic", "magical", "blazing",
    "lightning-fast", "butter-smooth", "buttery", "hassle-free", "out of the box", "plug and play",
    "here's the thing", "the best part", "simply put", "quietly", "delightful", "intuitive", "unrivaled",
    "unrivalled", "synergy", "paradigm", "ecosystem", "imagine",
]
BANNED_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(b) for b in sorted(BANNED, key=len, reverse=True))
                       + r")(?![\w-])", re.I)
DASHES = re.compile("[—–]")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿•…≈"
                   "✓✔✅✨→➤]")
EXCLAIM = re.compile(r"!(?![=\[(])")
PROPER = {"Hindsight", "Blender", "Sculpt", "Mode", "Vertex", "Paint", "Edit", "Object", "Undo", "History",
          "Menu", "Morph", "Movie", "Collection", "Filtering", "GitHub", "Windows", "macOS", "Linux", "Ctrl",
          "Shift", "Alt", "Cmd", "Python", "N", "F3", "Z", "Multires", "Dyntopo", "BETA", "I", "MB", "KB", "GB",
          "Preferences", "Extensions", "Install", "Disk", "Get", "Add-ons", "Steam", "Global", "Zstandard",
          "LZ4", "Zlib", "Discord", "Slack", "X", "Pages", "Actions", "Settings", "OK", "UI", "GPU", "CPU"}
MAX_WORDS_TEXT = 55
MAX_WORDS_UI = 40

findings = []


def report(where, rule, text):
    t = re.sub(r"\s+", " ", str(text)).strip()
    findings.append(f"{where}: {rule}: {t[:140]}")


def check_text(where, text, max_words=None, allow_question=False, heading=False):
    if not text or not text.strip():
        return
    for m in DASHES.finditer(text):
        report(where, "dash", text[max(0, m.start() - 40):m.end() + 40])
    for m in BANNED_RE.finditer(text):
        report(where, f"banned '{m.group(1)}'", text[max(0, m.start() - 40):m.end() + 40])
    for m in EMOJI.finditer(text):
        report(where, f"emoji or symbol U+{ord(m.group(0)):04X}", text[max(0, m.start() - 30):m.end() + 30])
    for m in EXCLAIM.finditer(text):
        report(where, "exclamation mark", text[max(0, m.start() - 40):m.end() + 10])
    if max_words is not None and len(text.split()) > max_words:
        report(where, f"paragraph over {max_words} words ({len(text.split())})", text)
    if heading:
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
        caps = [w for w in words[1:] if w[0].isupper() and w not in PROPER and not w.isupper()]
        if len(caps) >= 2:
            report(where, "Title Case heading", text)
        if text.strip().endswith("?") and not allow_question:
            report(where, "question as a heading", text)


# --------------------------------------------------------------------------- markdown / yaml
def check_markdown(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    fence = False
    para, start = [], 0

    def flush():
        if para:
            check_text(f"{path.relative_to(ROOT)}:{start}", " ".join(para), MAX_WORDS_TEXT)
        para.clear()

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            fence = not fence
            flush()
            continue
        if fence:
            continue
        stripped = re.sub(r"`[^`]*`", "", line)          # inline code is not prose
        stripped = re.sub(r"\]\([^)]*\)", "]", stripped)  # link targets are not prose
        stripped = re.sub(r"<[^>]+>", "", stripped)       # inline html tags
        if line.startswith("#"):
            flush()
            check_text(f"{path.relative_to(ROOT)}:{i}", stripped.lstrip("#").strip(), heading=True,
                       allow_question="faq" in path.name.lower())
        elif not line.strip() or line.lstrip().startswith(("- ", "* ", "|")) or re.match(r"\s*\d+\. ", line):
            flush()
            if line.strip():
                check_text(f"{path.relative_to(ROOT)}:{i}", re.sub(r"^\s*([-*|]|\d+\.)\s*", "", stripped),
                           MAX_WORDS_TEXT)
        else:
            if not para:
                start = i
            para.append(stripped)
    flush()


def check_yaml_text(path):
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = re.match(r"\s*(?:-\s*)?(?:label|description|placeholder|about|name|title|value|body)?:?\s*(.*)$", line)
        text = line.split(":", 1)[1] if ":" in line and not line.strip().startswith("-") else line
        text = text.strip().strip('"').strip("'").strip("|").strip(">")
        if re.search(r"[A-Za-z]{3,}\s+[A-Za-z]{2,}", text):
            check_text(f"{path.relative_to(ROOT)}:{i}", text, MAX_WORDS_TEXT)


# --------------------------------------------------------------------------- coverage
# Ran on the website's text; since the website was removed (#5) main() runs it on nothing. Kept, with its
# tests, until the owner decides which public text it should cover (README and CHANGELOG fail it as written).
# A data type that COVERAGE.md doesn't list as supported or partial may only be named in a sentence
# that says it isn't recorded (or is planned).
NEGATIONS = ("not recorded", "isn't recorded", "aren't recorded", "not record", "doesn't record",
             "don't record", "doesn't work with", "don't work with", "planned", "paused", "not yet",
             "not supported", "aren't part of", "isn't part of", "not part of")


def _coverage_terms(text=None):
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    import coverage_md as cov
    rows = cov.parse(text) if text is not None else cov.rows(ROOT / "COVERAGE.md")
    terms = []
    for row in rows:
        if row.get("status", "").lower() not in cov.CLAIMABLE:
            terms += cov.terms(row)
    return sorted(set(terms), key=len, reverse=True)


COVERAGE_TERMS = _coverage_terms()
_COVERAGE_SEEN = set()


def check_coverage(where, text):
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
        low = sentence.lower()
        if any(n in low for n in NEGATIONS):
            continue
        for term in COVERAGE_TERMS:
            if re.search(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", low):
                key = (where.split(" ")[0], sentence)
                if key not in _COVERAGE_SEEN:
                    _COVERAGE_SEEN.add(key)
                    report(where, "coverage", f"names \"{term}\", which COVERAGE.md doesn't list as supported: {sentence}")
                break


# --------------------------------------------------------------------------- add-on strings
def _docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ui_class = isinstance(node, ast.ClassDef) and re.search(r"_(OT|MT|PT|UL)_", node.name)
                if not ui_class:
                    ids.add(id(body[0].value))
    return ids


def check_addon():
    for path in sorted((ROOT / "hindsight").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstring_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                s = node.value
                if not re.search(r"[A-Za-z]{2,}[ ,.][^|]*[A-Za-z]", s) or re.fullmatch(r"[\w.|:/%{}()\[\]-]+", s):
                    continue    # identifiers, enum values, keys, paths, format specs
                check_text(f"{path.relative_to(ROOT)}:{node.lineno}", s, MAX_WORDS_UI)
    m = tomllib.loads((ROOT / "hindsight" / "blender_manifest.toml").read_text(encoding="utf-8"))
    check_text("hindsight/blender_manifest.toml tagline", m.get("tagline", ""))
    for reason in (m.get("permissions") or {}).values():
        check_text("hindsight/blender_manifest.toml permissions", reason)


def public_files():
    """The public text files, in scan order (the add-on's strings are scanned separately)."""
    files = [ROOT / name for name in ("README.md", "CHANGELOG.md") if (ROOT / name).is_file()]
    files += sorted((ROOT / ".github" / "release-notes").glob("*.md"))
    files += sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))
    return files


def main():
    scanned = public_files()
    for p in scanned:
        (check_yaml_text if p.suffix == ".yml" else check_markdown)(p)
    check_addon()
    if "--list" in sys.argv:
        for p in scanned:
            print("scanned", p.relative_to(ROOT))
    for f in findings:
        print(f)
    print(f"check_copy: {len(findings)} problem(s) in {len(scanned)} files plus the add-on's UI strings")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
