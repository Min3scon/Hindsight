"""#125 (adapted from #58's bench/held_once_mem.py on #9's branch): memory of Blender after opening a format 5
file with a large history, and its peak during a save: each saved step held beside its step part and the step parts
kept through the save (A, before #125), or read out of its step part by offset and the step parts handed over to
Blender during the save (B, after). Windows and Linux.

Run: <bpy python> bench/held_once_mem_f5.py --a ROOT_A --b ROOT_B [--n 100000] [--steps 6000] [--rounds 5]
  ROOT_A, ROOT_B  checkouts (A before #125, B after); each round runs A then B in fresh processes (A B A B ...)
Output: a table, and bench/results/held_once_mem_f5.json (every sample, the spread and the machine load).

The file with the history is built once (by B, Embed, in a child process) and both roots open the same
bytes. A parent process polls each child's memory every 5 ms (a thread inside Blender cannot run while a
save holds the GIL); the child only marks the phases on stdout. Memory is Windows working set and private
bytes, or VmRSS and VmData on Linux. "after open" and "after save" are the median of the samples over a
1 s idle wait; "save peak" is the highest sample between the start and end of the save. cpu_busy_pct is
the whole machine's CPU busy share over each run: other workers' Blender suites make the numbers noisy,
so judge them by the spread.
"""
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MB = 1 << 20


# ----------------------------------------------------------------------------- memory of a process (parent)
if os.name == "nt":
    import ctypes
    from ctypes import wintypes as W

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", W.DWORD), ("PageFaultCount", W.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ps = ctypes.WinDLL("psapi", use_last_error=True)
    _k32.OpenProcess.restype = W.HANDLE
    _k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    _ps.GetProcessMemoryInfo.argtypes = [W.HANDLE, ctypes.POINTER(_PMC), W.DWORD]

    def open_proc(pid):
        return _k32.OpenProcess(0x1000 | 0x0400, False, pid)

    def sample(h):
        m = _PMC()
        m.cb = ctypes.sizeof(m)
        if not _ps.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb):
            return None
        return m.WorkingSetSize, m.PrivateUsage

    def cpu_times():
        a, b, c = W.FILETIME(), W.FILETIME(), W.FILETIME()
        _k32.GetSystemTimes(ctypes.byref(a), ctypes.byref(b), ctypes.byref(c))
        f = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime
        return f(a), f(b) + f(c)                     # idle; kernel time includes idle, so total = kernel + user
else:
    def open_proc(pid):
        return pid

    def sample(pid):
        try:
            out = {}
            with open(f"/proc/{pid}/status") as fh:
                for line in fh:
                    if line.startswith(("VmRSS:", "VmData:")):
                        out[line.split(":")[0]] = int(line.split()[1]) * 1024
            return out["VmRSS"], out["VmData"]
        except (OSError, KeyError):
            return None

    def cpu_times():
        with open("/proc/stat") as fh:
            v = [int(x) for x in fh.readline().split()[1:]]
        return v[3] + v[4], sum(v)


def run_child(py, args, timeout=3000):
    """Run this script as a child and poll its memory: (result dict, [(t, working set, private)], marks, cpu busy %)."""
    p = subprocess.Popen([py, os.path.abspath(__file__), *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True)
    marks, res, tail, pid = {}, {}, [], [0]

    def reader():
        for line in p.stdout:
            if line.startswith("PID "):
                pid[0] = int(line.split()[1])
            elif line.startswith("MARK "):
                _, name, t = line.split()
                marks[name] = float(t)
            elif line.startswith("RESULT "):
                res.update(json.loads(line[7:]))
            else:
                tail.append(line.rstrip())
                del tail[:-8]

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    while not pid[0] and p.poll() is None:      # a venv python is a launcher: the real process reports its pid
        time.sleep(0.01)
    h = open_proc(pid[0] or p.pid)
    samples, i0, t0 = [], cpu_times(), time.time()
    while p.poll() is None and time.time() - t0 < timeout:
        s = sample(h)
        if s:
            samples.append((time.time(), *s))
        time.sleep(0.005)
    if p.poll() is None:
        p.kill()
    th.join(10)
    i1 = cpu_times()
    busy = round(100 * (1 - (i1[0] - i0[0]) / max(1, i1[1] - i0[1])), 1)
    if args[0] == "measure" and "n_entries" not in res or args[0] == "build" and p.returncode != 0:
        raise RuntimeError("child failed: " + " | ".join(tail))
    return res, samples, marks, busy


def idle(samples, t, width=1.0):
    """Median working set and private bytes (MB) over the idle second that starts at mark time t."""
    w = [s for s in samples if t + 0.1 <= s[0] <= t + width]
    return (round(statistics.median(s[1] for s in w) / MB, 1), round(statistics.median(s[2] for s in w) / MB, 1))


def measure(py, root, blend, n, steps):
    res, samples, m, busy = run_child(py, ["measure", root, str(n), str(steps), blend])
    w = [s for s in samples if m["save_start"] <= s[0] <= m["save_end"]]
    row = {"before_open_mb": idle(samples, m["before_open"]), "after_open_mb": idle(samples, m["after_open"]),
           "after_save_mb": idle(samples, m["after_save"]), "cpu_busy_pct": busy,
           "save_peak_mb": (round(max(s[1] for s in w) / MB, 1), round(max(s[2] for s in w) / MB, 1)),
           "open_s": round(m["after_open"] - m["open_start"], 2), "save_s": round(m["save_end"] - m["save_start"], 2)}
    row.update(res)
    return row


def spread(rows, key, i):
    v = [r[key][i] for r in rows]
    return {"median": round(statistics.median(v), 1), "min": min(v), "max": max(v), "all": v}


def parent():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--steps", type=int, default=6000)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--py", default=sys.executable)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "held_once_mem_f5.json"))
    a = ap.parse_args()
    roots = {"A": os.path.abspath(a.a), "B": os.path.abspath(a.b)}
    tmp = tempfile.mkdtemp(prefix="hs_heldmem_")
    blend = os.path.join(tmp, "big.blend")
    run_child(a.py, ["build", roots["B"], str(a.n), str(a.steps), blend])
    size = os.path.getsize(blend) / MB
    rows = {"A": [], "B": []}
    for r in range(a.rounds):
        for k in ("A", "B"):
            rows[k].append(measure(a.py, roots[k], blend, a.n, a.steps))
            x = rows[k][-1]
            print(f"round {r + 1} {k}: open {x['after_open_mb']} save peak {x['save_peak_mb']} "
                  f"after save {x['after_save_mb']} (working set, private MB) cpu {x['cpu_busy_pct']}%", flush=True)
    out = {"vertices": a.n, "steps": a.steps, "blend_mb": round(size, 1), "rounds": a.rounds, "roots": roots,
           "unit": "MB", "samples": rows}
    for k in rows:
        out[k] = {key: {"working_set": spread(rows[k], key, 0), "private": spread(rows[k], key, 1)}
                  for key in ("before_open_mb", "after_open_mb", "save_peak_mb", "after_save_mb")}
        out[k]["cpu_busy_pct"] = [r["cpu_busy_pct"] for r in rows[k]]
        out[k]["text_bytes_after_open"] = [r["text_after_open"] for r in rows[k]]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"\n{a.n} vertices, {a.steps} steps, file {out['blend_mb']} MB, {a.rounds} rounds; median (min..max) MB")
    for what in ("working_set", "private"):
        print(what)
        for key in ("before_open_mb", "after_open_mb", "save_peak_mb", "after_save_mb"):
            print(f"  {key:15}", *(f"{k}: {out[k][key][what]['median']} ({out[k][key][what]['min']}..{out[k][key][what]['max']})"
                                    for k in "AB"))
    print("text bytes after open: A", out["A"]["text_bytes_after_open"], "B", out["B"]["text_bytes_after_open"])
    print("cpu busy %: A", out["A"]["cpu_busy_pct"], "B", out["B"]["cpu_busy_pct"])
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------- child (inside Blender)
def mark(name):
    print(f"MARK {name} {time.time()!r}", flush=True)


def child():
    print(f"PID {os.getpid()}", flush=True)
    mode, root, n, steps, blend = sys.argv[1:6]
    sys.argv = [sys.argv[0], root, n, steps]            # saveload_bench reads ROOT N STEPS from argv
    sys.path.insert(0, HERE)
    import saveload_bench as S                          # puts ROOT first on sys.path
    import bpy
    import tests.harness as H
    from hindsight.bl import persistence, runtime
    from hindsight.core.types import SETTINGS
    try:
        if mode == "build":
            S.setup()
            SETTINGS.save_mode = "EMBED"
            runtime.packer.drain(60)
            bpy.ops.wm.save_as_mainfile(filepath=blend)
            return 0
        import gc
        H.fresh()
        SETTINGS.save_mode = "EMBED"
        gc.collect()
        mark("before_open")
        time.sleep(1.2)
        mark("open_start")
        bpy.ops.wm.open_mainfile(filepath=blend)
        gc.collect()
        SETTINGS.save_mode = "EMBED"
        mark("after_open")
        text = bpy.data.texts.get(persistence.TEXT_NAME)
        keep = sum(len(text[k]) for k in (persistence.DATA_PROP, persistence.DATA_BIN_PROP)
                   if text is not None and k in text)
        got = sum(len(x.entries) for x in runtime.reg().histories.values())
        time.sleep(1.2)
        mark("save_start")
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(os.path.dirname(blend), "saved.blend"))
        mark("save_end")
        gc.collect()
        mark("after_save")
        time.sleep(1.2)
        print("RESULT " + json.dumps({"n_entries": got, "text_after_open": keep}), flush=True)
        return 0
    except Exception:
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("build", "measure"):
        rc = child()
        sys.stdout.flush()
        os._exit(rc)        # bpy 5.0 crashes at exit on this PC; the result is already printed
    parent()
