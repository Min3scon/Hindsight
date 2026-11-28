#!/usr/bin/env python3
"""A/B comparison of two code roots with the suite's scenarios, in ALTERNATING processes.

    python3 bench/ab.py --a OLD_ROOT --b NEW_ROOT [--rounds 5] [--only a,b] [--quick]
                        [--py PYTHON] [--label-a NAME] [--label-b NAME] [--out FILE]

Round r runs every scenario group for A, then for B (each in a fresh process), so load on this
shared machine hits both sides alike: A B A B ... Per scenario it prints, for each side, the
smallest "min" over the rounds and the median over the rounds of the per-round "median", the
ratio B/A of both (below 1 means B is faster or smaller), and the spread: how far the per-round
medians of that side were apart ((max - min) / min), the honest measure of noise. Memory
columns (tracemalloc / VmHWM, MB) show the smallest value over the rounds. A scenario an old root
cannot run is shown as "not available on A" (never as zero).

Writes bench/results/ab_<labelA>_vs_<labelB>.json (every round's raw numbers, the summary, the
machine facts and the load average around each round) and the same table as .md next to it.
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite  # noqa: E402


def _ok(r):
    return r is not None and "error" not in r


def summarize(rounds):
    """rounds: [{name: result}] for one side -> {name: summary}."""
    names = []
    for r in rounds:
        for n in r:
            if n not in names:
                names.append(n)
    out = {}
    for n in names:
        rs = [r[n] for r in rounds if n in r]
        good = [r for r in rs if _ok(r)]
        if not good:
            out[n] = {"error": next((r["error"] for r in rs if "error" in r), "no result"), "rounds": len(rs)}
            continue
        mins = [r["min_ms"] for r in good]
        meds = [r["median_ms"] for r in good]
        s = {"min": min(mins), "median": statistics.median(meds), "spread": (max(meds) - min(meds)) / min(meds)
             if min(meds) > 0 else 0.0, "rounds": len(good), "unit": good[0].get("unit", "ms")}
        for k in ("tracemalloc_mb", "hwm_mb"):
            v = [r[k] for r in good if r.get(k) is not None]
            if v:
                s[k] = min(v)
        out[n] = s
    return out


def ratio(b, a):
    if a is None or b is None or a == 0:
        return None
    return b / a


def table(sa, sb, la, lb):
    rows = []
    head = (f"| scenario | {la} min | {la} med | {lb} min | {lb} med | {lb}/{la} min | {lb}/{la} med "
            f"| spread {la} | spread {lb} | mem {la} tm/hwm MB | mem {lb} tm/hwm MB |")
    rows.append(head)
    rows.append("|" + "---|" * 11)

    def num(x):
        return "" if x is None else f"{x:.4g}"

    def mem(s):
        if "tracemalloc_mb" not in s and "hwm_mb" not in s:
            return ""
        return f"{num(s.get('tracemalloc_mb'))} / {num(s.get('hwm_mb'))}"
    for n in list(sa) + [x for x in sb if x not in sa]:
        a, b = sa.get(n), sb.get(n)
        if a is None or b is None or "error" in a or "error" in b:
            ea = "not available on A" if (a is None or "error" in a) else ""
            eb = "not available on B" if (b is None or "error" in b) else ""
            why = (a or {}).get("error") or (b or {}).get("error") or ""
            rows.append(f"| {n} | {ea or num(a['min'])} | {ea or num(a['median'])} | {eb or num(b['min'])} | "
                        f"{eb or num(b['median'])} | | | | | {why[:60]} | |")
            continue
        u = "" if a["unit"] == "ms" else " " + a["unit"]
        rows.append(f"| {n}{u} | {num(a['min'])} | {num(a['median'])} | {num(b['min'])} | {num(b['median'])} | "
                    f"{num(ratio(b['min'], a['min']))} | {num(ratio(b['median'], a['median']))} | "
                    f"{100 * a['spread']:.0f}% | {100 * b['spread']:.0f}% | {mem(a)} | {mem(b)} |")
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", required=True, help="old code root")
    ap.add_argument("--b", default=os.path.dirname(suite.HERE), help="new code root (default: this checkout)")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--only", default="")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--py", default=suite.DEFAULT_PY)
    ap.add_argument("--label-a", default=None)
    ap.add_argument("--label-b", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    roots = {"A": os.path.abspath(a.a), "B": os.path.abspath(a.b)}
    labels = {"A": a.label_a or suite.git_label(roots["A"]), "B": a.label_b or suite.git_label(roots["B"])}
    envs = {k: suite.native_env(r) for k, r in roots.items()}
    only = [x for x in a.only.split(",") if x]
    groups = suite.select_groups(only)
    per = {"A": [], "B": []}
    seconds = []
    loads = []
    t_start = time.time()
    facts = {k: suite.facts(a.py, roots[k], envs[k]) for k in roots}
    for rnd in range(a.rounds):
        got = {"A": {}, "B": {}}
        l0 = suite.loadavg()
        for g in groups:
            for side in ("A", "B"):
                res, secs = suite.run_group(a.py, roots[side], g, a.quick, only, envs[side])
                got[side].update(res)
            suite.log(f"round {rnd + 1}/{a.rounds} {g} done ({time.time() - t_start:.0f} s, load {(suite.loadavg() or [None])[0]})")
        per["A"].append(got["A"])
        per["B"].append(got["B"])
        loads.append({"round": rnd + 1, "start": l0, "end": suite.loadavg()})
        seconds.append(round(time.time() - t_start))
    sa, sb = summarize(per["A"]), summarize(per["B"])
    md = table(sa, sb, labels["A"], labels["B"])
    print(f"A = {labels['A']} ({roots['A']})\nB = {labels['B']} ({roots['B']})\n"
          f"{a.rounds} rounds, alternating A B per scenario group; min = best of all rounds, med = median of "
          f"the rounds' medians; ratio < 1: B is faster/smaller.\n")
    print(md)
    name = a.out or os.path.join(suite.HERE, "results",
                                 f"ab_{labels['A']}_vs_{labels['B']}{'.quick' if a.quick else ''}.json")
    os.makedirs(os.path.dirname(name), exist_ok=True)
    out = {"a": {"label": labels["A"], "root": roots["A"], "facts": facts["A"]},
           "b": {"label": labels["B"], "root": roots["B"], "facts": facts["B"]},
           "rounds": a.rounds, "quick": a.quick, "only": only, "started": time.strftime("%Y-%m-%d %H:%M:%S",
                                                                                          time.localtime(t_start)),
           "cores": os.cpu_count(), "load_average_per_round": loads, "elapsed_s_after_round": seconds,
           "summary": {"A": sa, "B": sb}, "per_round": per}
    with open(name, "w") as fh:
        json.dump(out, fh, indent=1)
    with open(name[:-5] + ".md", "w") as fh:
        fh.write(md + "\n")
    suite.log("wrote", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
