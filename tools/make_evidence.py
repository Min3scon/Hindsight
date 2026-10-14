"""Build TEST_EVIDENCE.md from real test runs, and check COVERAGE.md against it.

    python tools/make_evidence.py            # merge new runs, write evidence/ and TEST_EVIDENCE.md
    python tools/make_evidence.py --check    # only check the committed evidence (exit 1 on a problem)

Runs come from the runners' own output (never typed in):
  headless  .tmp/results/headless/<blender>/<file>.json   tests/run_tests.py; tags are the
            @covers(...) decorators in the test source (tests/gate.py), read with ast
  ui        .tmp/ui/<scenario>/<blender>/result.json      tests_ui/hs_ui.py; tags in each check
  tools     .tmp/results/tools/<tool>/<blender>/*.json    mutation, Hypothesis, soak, visual review
The newest run of each (source, file or scenario, Blender) is kept in evidence/results.json, with
its output (paths removed, in evidence/output) and, for real-UI runs, its screenshots. A gate item counts on a Blender when
at least one tagged test passed there in its newest run and none failed. Rows may be `supported` or
`partial` in COVERAGE.md only when every item counts on every Blender in tools/gate_spec.py
(or is listed with a reason in evidence/not_applicable.json).
"""
import ast
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import coverage_md  # noqa: E402
import gate_spec  # noqa: E402

TMP = ROOT / ".tmp"
EVID = ROOT / "evidence"
STORE = EVID / "results.json"
NA_FILE = EVID / "not_applicable.json"
OUT_MD = ROOT / "TEST_EVIDENCE.md"
PRIVATE_SCENARIOS = {"round_e"}       # the owner's own files: results only, never logs or pictures
LOG_CAP = 120_000
SHOT_WIDTH = 1280


# ----------------------------------------------------------------------------- sanitising
def _private_roots():
    roots = {str(ROOT), str(ROOT).replace("\\", "/"), os.path.expanduser("~"),
             os.path.expanduser("~").replace("\\", "/")}
    return sorted((r for r in roots if r and len(r) > 3), key=len, reverse=True)


_DRIVE_PATH = re.compile(r"(?i)\b[a-z]:[\\/][^\s\"'<>|]*")


def sanitise(text: str) -> str:
    for r in _private_roots():
        text = re.sub(re.escape(r), "<repo>" if r.rstrip("\\/").lower().endswith("hindsight") else "<home>",
                      text, flags=re.I)
    return _DRIVE_PATH.sub("<path>", text)


# ----------------------------------------------------------------------------- tags
def headless_tags() -> dict:
    """file -> {test name -> [[row, item], ...]} from @covers decorators."""
    out = {}
    for f in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        tags = {}
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            pairs = []
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                name = dec.func.attr if isinstance(dec.func, ast.Attribute) else getattr(dec.func, "id", "")
                if name != "covers" or not dec.args:
                    continue
                first = ast.literal_eval(dec.args[0])
                rows = [first] if isinstance(first, str) else list(first)
                items = [ast.literal_eval(a) for a in dec.args[1:]]
                pairs += [[r, i] for r in rows for i in items]
            if pairs:
                tags[node.name] = pairs
        out["tests/" + f.name] = tags
    return out


# ----------------------------------------------------------------------------- collecting runs
def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def collect_new() -> list:
    """Runs found in .tmp (not yet in the store), as store records plus where their files are."""
    runs = []
    tags = headless_tags()
    for p in sorted((TMP / "results" / "headless").glob("*/*.json")):
        r = _load(p)
        if not r:
            continue
        ftags = tags.get(r["file"], {})
        tests = [{"name": t["name"], "ok": bool(t["ok"]), "covers": ftags.get(t["name"], [])}
                 for t in r.get("tests", [])]
        runs.append({"source": "headless", "unit": r["file"], "blender": r["blender"], "date": r["date"],
                     "tests": tests, "_log": p.parent / r.get("log", ""), "_shots": []})
    for p in sorted((TMP / "ui").glob("*/*/result.json")):
        r = _load(p)
        if not r or not re.match(r"^\d+\.\d+\.\d+$", p.parent.name):
            continue
        scen = p.parent.parent.name
        private = scen in PRIVATE_SCENARIOS
        date = r.get("date") or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(p.stat().st_mtime))
        tests = [{"name": c["name"], "ok": bool(c["ok"]), "covers": c.get("covers") or []}
                 for c in r.get("checks", [])]
        if r.get("error"):
            tests.append({"name": "scenario finished without an error", "ok": False, "covers": []})
        runs.append({"source": "ui", "unit": scen, "blender": p.parent.name, "date": date, "tests": tests,
                     "_log": None if private else p.parent / "console.log",
                     "_shots": [] if private else sorted((p.parent / "shots").glob("*.png"))})
    for p in sorted((TMP / "results" / "tools").glob("*/*/*.json")):
        r = _load(p)
        if not r:
            continue
        runs.append({"source": "tool", "unit": r.get("unit") or p.stem, "blender": r["blender"],
                     "date": r["date"], "tests": [{"name": t["name"], "ok": bool(t["ok"]),
                                                   "covers": t.get("covers") or []} for t in r["tests"]],
                     "_log": (p.parent / r["log"]) if r.get("log") else None, "_shots": []})
    return runs


def _key(run) -> str:
    return f"{run['source']}|{run['unit']}|{run['blender']}"


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


def _copy_log(run, src: Path) -> str:
    dst = EVID / "output" / run["source"] / run["blender"] / (_slug(run["unit"]) + ".log")
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = sanitise(src.read_text(encoding="utf-8", errors="replace"))
    if len(text) > LOG_CAP:
        text = text[:LOG_CAP // 4] + "\n[... cut ...]\n" + text[-LOG_CAP * 3 // 4:]
    dst.write_text(text, encoding="utf-8")
    return dst.relative_to(ROOT).as_posix()


def _copy_shots(run, shots) -> list:
    out = []
    if not shots:
        return out
    try:
        from PIL import Image
    except Exception:
        return out
    d = EVID / "shots" / _slug(run["unit"]) / run["blender"]
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    for s in shots:
        try:
            im = Image.open(s).convert("RGB")
            if im.width > SHOT_WIDTH:
                im = im.resize((SHOT_WIDTH, round(im.height * SHOT_WIDTH / im.width)), Image.LANCZOS)
            dst = d / (s.stem + ".jpg")
            im.save(dst, "JPEG", quality=82, optimize=True)
            out.append(dst.relative_to(ROOT).as_posix())
        except Exception:
            continue
    return out


def merge(store: dict, runs: list) -> int:
    n = 0
    for run in runs:
        if not any(t["covers"] for t in run["tests"]):
            continue                              # nothing in it is evidence for a row
        k = _key(run)
        old = store["runs"].get(k)
        if old is not None and old["date"] >= run["date"]:
            continue
        rec = {k2: v for k2, v in run.items() if not k2.startswith("_")}
        rec["log"] = _copy_log(run, run["_log"]) if run["_log"] and Path(run["_log"]).is_file() else None
        visual = any(i == "VISUAL" for t in run["tests"] for _r, i in t["covers"])
        rec["shots"] = _copy_shots(run, run["_shots"]) if visual else []
        store["runs"][k] = rec
        n += 1
    return n


# ----------------------------------------------------------------------------- evaluating
def evaluate(store: dict, rows: list, na: dict):
    """{row id: {item: {blender: {"pass": [...], "fail": [...]}}}} and problems found."""
    ids = {r["id"] for r in rows}
    problems = []
    table = {rid: {it: {b: {"pass": [], "fail": []} for b in gate_spec.BLENDERS} for it in gate_spec.ITEMS}
             for rid in ids}
    for rec in store["runs"].values():
        for t in rec["tests"]:
            for row, item in t["covers"]:
                if row not in ids:
                    problems.append(f"{rec['unit']} :: {t['name']}: unknown COVERAGE row {row!r}")
                    continue
                if item not in gate_spec.ITEMS:
                    problems.append(f"{rec['unit']} :: {t['name']}: unknown gate item {item!r}")
                    continue
                if rec["blender"] not in gate_spec.BLENDERS:
                    continue
                cell = table[row][item][rec["blender"]]
                ref = {"source": rec["source"], "unit": rec["unit"], "name": t["name"], "date": rec["date"],
                       "log": rec.get("log"), "shots": rec.get("shots") or []}
                cell["pass" if t["ok"] else "fail"].append(ref)
    for row, items in na.items():
        if row not in ids:
            problems.append(f"not_applicable.json: unknown row {row!r}")
        for item in items:
            if item not in gate_spec.ITEMS:
                problems.append(f"not_applicable.json: unknown gate item {item!r} for {row}")
    return table, sorted(set(problems))


def item_ok(cell_by_blender: dict) -> bool:
    return all(c["pass"] and not c["fail"] for c in cell_by_blender.values())


def gate_status(table, row_id, na):
    items = table[row_id]
    done = [it for it in gate_spec.ITEMS if it in na.get(row_id, {}) or item_ok(items[it])]
    return done


# ----------------------------------------------------------------------------- writing
def _test_label(ref) -> str:
    unit = ref["unit"].replace("tests/", "")
    return f"{unit} › {ref['name']}"


def write_md(store, rows, table, na, problems) -> str:
    lines = ["# Test evidence", "",
             f"Generated by `tools/make_evidence.py` from real test runs (newest run of each test file or "
             f"scenario per Blender). Do not edit by hand. Gate: {len(gate_spec.ITEMS)} items "
             f"(`tools/gate_spec.py`) on Blender {', '.join(gate_spec.BLENDERS)}. An item counts on a Blender "
             "when a test tagged with it passed there in its newest run and none failed.", "",
             "| Row | Status | Gate items done | Tests | Last passing run | Details |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        rid = r["id"]
        done = gate_status(table, rid, na)
        names, dates, vers = set(), [], set()
        for it in gate_spec.ITEMS:
            for b, cell in table[rid][it].items():
                for ref in cell["pass"]:
                    names.add(_test_label(ref))
                    dates.append(ref["date"])
                    vers.add(b)
        tests = ", ".join(sorted(names)[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
        last = f"{max(dates)[:10]} ({', '.join(sorted(vers))})" if dates else "none"
        anchor = _slug(rid).replace(".", "-").lower()
        lines.append(f"| {rid} | {r['status']} | {len(done)}/{len(gate_spec.ITEMS)} | {tests or 'none'} | "
                     f"{last} | [details](#{anchor}) |")
    lines += [""]
    if problems:
        lines += ["## Problems", ""] + [f"- {p}" for p in problems] + [""]
    for r in rows:
        rid = r["id"]
        anchor = _slug(rid).replace(".", "-").lower()
        lines += [f'<a id="{anchor}"></a>', f"## {rid}: {r['data']}", "",
                  f"COVERAGE status: **{r['status']}**. Recording: {r['recording']}.", "",
                  "| Item | " + " | ".join(gate_spec.BLENDERS) + " | Tests |",
                  "|---|" + "---|" * len(gate_spec.BLENDERS) + "---|"]
        for it in gate_spec.ITEMS:
            if it in na.get(rid, {}):
                lines.append(f"| {it} | " + " | ".join("n/a" for _ in gate_spec.BLENDERS)
                             + f" | not applicable: {na[rid][it]} |")
                continue
            cells, refs = [], {}
            for b in gate_spec.BLENDERS:
                c = table[rid][it][b]
                cells.append("FAIL" if c["fail"] else ("pass" if c["pass"] else "-"))
                for ref in c["pass"] + c["fail"]:
                    refs.setdefault(_test_label(ref), ref)
            links = []
            for label, ref in sorted(refs.items())[:6]:
                link = f"[log]({ref['log']})" if ref.get("log") else ""
                if it == "VISUAL" and ref.get("shots"):
                    link += " " + " ".join(f"[shot]({s})" for s in ref["shots"][:3])
                links.append(f"{label} {link}".strip())
            more = f" and {len(refs) - 6} more" if len(refs) > 6 else ""
            lines.append(f"| {it} | " + " | ".join(cells) + " | " + ("; ".join(links) + more or "none") + " |")
        lines.append("")
    return "\n".join(lines) + "\n"


def check_statuses(rows, table, na) -> list:
    errors = []
    for r in rows:
        st = r["status"].lower()
        if st not in coverage_md.STATUSES:
            errors.append(f"{r['id']}: status {r['status']!r} isn't one of {coverage_md.STATUSES}")
            continue
        if st in ("supported", "partial"):
            done = gate_status(table, r["id"], na)
            missing = [it for it in gate_spec.ITEMS if it not in done]
            if missing:
                errors.append(f"{r['id']} is {st} in COVERAGE.md but the gate isn't complete: "
                              f"missing {', '.join(missing)}")
        if st == "partial" and not r.get("limits"):
            errors.append(f"{r['id']} is partial but Limits doesn't say what doesn't work")
    return errors


def main(argv):
    check_only = "--check" in argv
    rows = coverage_md.rows()
    store = _load(STORE) or {"runs": {}}
    na = _load(NA_FILE) or {}
    if not check_only:
        n = merge(store, collect_new())
        EVID.mkdir(exist_ok=True)
        STORE.write_text(json.dumps(store, indent=1, sort_keys=True), encoding="utf-8")
        print(f"make_evidence: {n} new run(s) merged, {len(store['runs'])} in the store")
    table, problems = evaluate(store, rows, na)
    errors = check_statuses(rows, table, na)
    md = write_md(store, rows, table, na, problems + errors)
    if check_only:
        if OUT_MD.is_file() and OUT_MD.read_text(encoding="utf-8") != md:
            errors.append("TEST_EVIDENCE.md is out of date: run python tools/make_evidence.py")
    else:
        OUT_MD.write_text(md, encoding="utf-8")
        print(f"make_evidence: wrote {OUT_MD.name}")
    for p in problems + errors:
        print("make_evidence:", p)
    return 1 if (errors or problems) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
