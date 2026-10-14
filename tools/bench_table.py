#!/usr/bin/env python3
"""Build the docs' benchmark section from measured results. Never type numbers by hand.

    python tools/bench_table.py [--blender 5.1.2]

Real Blender numbers come from the Round C runs (.tmp/ui/round_c/<blender>-<label>/result.json).
Headless numbers come from bench/results_bl.json (Blender's Python module, an earlier cloud machine).
Writes "benchmarks_html" and "bytes_per_step" into site/data.json.
"""
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / ".tmp" / "ui" / "round_c"


def cpu():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return out or platform.processor()
    except Exception:
        return platform.processor()


def load(tag, label):
    p = UI / f"{tag}-{label}" / "result.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def kb(b):
    return f"{b / 1024:.1f} KB" if b < 1024 * 1024 else f"{b / 2**20:.1f} MB"


def main():
    tag = sys.argv[sys.argv.index("--blender") + 1] if "--blender" in sys.argv else "5.1.2"
    rows = []
    for label, name in (("10k", "10k"), ("100k", "100k"), ("500k", "500k")):
        r = load(tag, label)
        if not r:
            continue
        n = r["notes"]
        rows.append(f"<tr><td>{n['verts']:,}</td><td>{n.get('commit_ms_median', '?')} ms</td>"
                    f"<td>{n.get('jump_ms_median', '?')} ms</td><td>{n.get('jump_ms_far_max') or '?'} ms</td>"
                    f"<td>{kb(n['history_bytes_per_step']) if 'history_bytes_per_step' in n else '?'}</td></tr>")
    sessions = []
    bytes_per_step = None
    for label in ("10k-1000", "100k-10000"):
        r = load(tag, label)
        if not r or "session_final" not in r["notes"]:
            continue
        n = r["notes"]
        f = n["session_final"]
        jumps = n.get("session_jump_ms") or []
        sessions.append(f"<tr><td>{n['verts']:,}</td><td>{f['steps']:,}</td><td>{f['history_mb']} MB</td>"
                        f"<td>{kb(f['bytes_per_step'])}</td><td>{f['rss_mb']:.0f} MB</td>"
                        f"<td>{max(jumps) if jumps else '?'} ms</td><td>{'yes' if r.get('ok') else 'no'}</td></tr>")
        if label.startswith("100k"):
            bytes_per_step = kb(f["bytes_per_step"])
    html = []
    html.append(f"<p>Measured in the real Blender {tag} interface on Windows 10 with an {cpu()} and 16 GB of RAM, "
                "using simulated mouse strokes on generated test meshes. Times are for Hindsight's own work, after "
                "you let go of the mouse or move the slider.</p>")
    if rows:
        html.append("<h3>Per stroke and per jump (real Blender)</h3><table><thead><tr><th>Vertices</th><th>Recording a stroke "
                    "(median)</th><th>A jump (median)</th><th>Longest jump</th><th>Size per step</th></tr></thead><tbody>"
                    + "".join(rows) + "</tbody></table>")
    if sessions:
        html.append("<h3>Long sessions (real Blender)</h3><table><thead><tr><th>Vertices</th><th>Steps</th><th>History</th>"
                    "<th>Per step</th><th>Blender's memory</th><th>Slowest of 120 random jumps</th><th>All jumps exact</th></tr>"
                    "</thead><tbody>" + "".join(sessions) + "</tbody></table>")
    hb = ROOT / "bench" / "results_bl.json"
    if hb.is_file():
        res = json.loads(hb.read_text())
        env = res["environment"]
        hrows = "".join(
            f"<tr><td>{r['verts']:,}</td><td>{kb(r['mem_packed_bytes_per_step'])}</td><td>{r['commit_ms_median']:.2f} ms</td>"
            f"<td>{r['goto1_ms']:.2f} ms</td><td>{r['goto300_ms']:.0f} ms</td></tr>" for r in res["results"])
        html.append(f"<h3>Headless (Blender's Python module {env['bpy'].split()[0]}, no viewport)</h3>"
                    f"<p>From beta 1, on a {env['logical_cpus']}-thread {env['cpu'].split('@')[-1].strip()} cloud machine, "
                    "with strokes that each move 2% of the vertices. Kept for comparison.</p>"
                    "<table><thead><tr><th>Vertices</th><th>Size per step</th><th>Recording</th><th>Jump 1 step</th>"
                    f"<th>Jump 300 steps</th></tr></thead><tbody>{hrows}</tbody></table>")
    data_path = ROOT / "site" / "data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["benchmarks_html"] = "\n".join(html)
    if bytes_per_step:
        data["bytes_per_step"] = bytes_per_step
    data_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"benchmarks: {len(rows)} stroke rows, {len(sessions)} session rows, bytes/step {bytes_per_step}")


if __name__ == "__main__":
    main()
