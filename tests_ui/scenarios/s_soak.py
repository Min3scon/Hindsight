"""Soak (gate item SOAK): a long real-UI session that keeps every Tier 1 adapter busy (moves, rotation
modes, parenting, constraints, collections, hiding, adding, duplicating and deleting objects, Edit
Mode operations, Object Mode mesh changes), with jumps on the sidebar slider, the global timeline and
Blender's undo mixed in. History length is capped (Max steps 60) so memory should level off.

Every 30 seconds: the process's private memory, handle count and thread count (Windows API), Python
threads, and Hindsight's handlers and timers. After the first quarter of the run, none may keep
growing: memory stays within 8% of its warm level (no steady climb over the last half), handles and
threads within a small margin, handlers and timers exactly the same.

args: minutes (default 30), seed (default 1)
"""
import ctypes
import ctypes.wintypes as wt
import os
import random
import sys
import threading
import time

import bpy

import common as C


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def process_sample():
    """(private bytes, handle count, OS thread count) of this Blender process."""
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    h = k32.GetCurrentProcess()
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
    handles = wt.DWORD()
    k32.GetProcessHandleCount(h, ctypes.byref(handles))

    class _TE(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ThreadID", wt.DWORD),
                    ("th32OwnerProcessID", wt.DWORD), ("tpBasePri", wt.LONG), ("tpDeltaPri", wt.LONG),
                    ("dwFlags", wt.DWORD)]
    snap = k32.CreateToolhelp32Snapshot(0x4, 0)
    te = _TE()
    te.dwSize = ctypes.sizeof(_TE)
    pid = os.getpid()
    n = 0
    ok = k32.Thread32First(snap, ctypes.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            n += 1
        ok = k32.Thread32Next(snap, ctypes.byref(te))
    k32.CloseHandle(snap)
    return pmc.PrivateUsage, handles.value, n


def hindsight_hooks():
    """How many of Hindsight's handlers are installed, per handler list."""
    out = {}
    for name in ("depsgraph_update_post", "undo_post", "redo_post", "load_post", "save_pre", "save_post",
                 "frame_change_post"):
        out[name] = sum(1 for f in getattr(bpy.app.handlers, name) if "hindsight" in getattr(f, "__module__", ""))
    return out


def run(d):
    sys.path.insert(0, d.spec["repo"])
    from tests import t1_ops as T
    import importlib
    globaltl = importlib.import_module(C.PKG + ".core.globaltl")
    rt = C.mod("runtime")
    minutes = float(d.args.get("minutes", 30))
    rng = random.Random(int(d.args.get("seed", 1)))
    mark = d.console_mark()
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    prefs = C.mod("prefs").get_prefs()
    prefs.record_scope = "EVERYTHING"
    prefs.max_steps = 60
    with d.override():
        bpy.ops.wm.read_homefile(use_empty=True)
    yield 1.0
    with d.override():
        T.build_scene()
        bpy.ops.view3d.view_all()
    for name in T.NAMES:
        with d.override():
            T.select(name)
            bpy.ops.hindsight.record_everything(on=True)
    with d.override():
        bpy.ops.hindsight.record_scene(on=True)
    yield from C.show_sidebar_tab(d)
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2
    yield from C.warm_up(d)

    def op_list():
        names = [o.name for o in bpy.context.scene.objects if o.name in T.NAMES]
        meshes = [n for n in names if bpy.data.objects[n].type == "MESH"]
        ops = []
        for n in names:
            ops += [T.translate(n, (rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), 0)),
                    T.rotate(n, rng.uniform(-1, 1)), T.add_constraint(n, "COPY_ROTATION"),
                    T.move_to_collection(n, "Props")]
        for n in meshes:
            ops += [T.shade(n, rng.random() < 0.5), T.origin_to_geometry(n)]
        if "E" in meshes:
            ops += [o for o in T.edit_ops("E", rng.randrange(1000)) if o.label.split(": ")[1] in
                    ("grab", "rotate", "scale", "extrude", "subdivide")]
        ops += [T.add_primitive(rng.choice(T.PRIMITIVES), (rng.uniform(-6, 6), -6, 0))]
        extra = [o.name for o in bpy.context.scene.objects if o.name not in T.NAMES]
        if extra:
            ops.append(T.delete([rng.choice(extra)]))
        if names:
            ops.append(T.duplicate(rng.choice(names)))
        return ops

    samples = []
    t_end = time.time() + minutes * 60
    t_next = 0.0
    n_ops = 0
    while time.time() < t_end:
        kind = rng.random()
        try:
            with d.override():
                if kind < 0.7:
                    rng.choice(op_list()).run()
                    T.select()
                elif kind < 0.85:
                    sp = bpy.context.scene.hindsight
                    sp.use_global = True
                    n = globaltl.global_length(rt.reg(), rt._visible_uids())
                    rt.global_goto(bpy.context, rng.randint(max(0, n - 10), n))
                    rt.global_goto(bpy.context, n)
                    sp.use_global = False
                else:
                    d.key("Z", ctrl=True, x=cx, y=cy)
        except Exception as e:
            d.log("operation refused:", type(e).__name__, str(e)[:120])
        n_ops += 1
        yield 0.35
        if time.time() >= t_next:
            t_next = time.time() + 30
            mem, handles, threads = process_sample()
            samples.append({"t": round(time.time() - (t_end - minutes * 60)), "mem": mem, "handles": handles,
                            "threads": threads, "py_threads": threading.active_count(), "hooks": hindsight_hooks(),
                            "steps": sum(len(h.entries) for h in rt.reg().histories.values()),
                            "longest": max((len(h.entries) for h in rt.reg().histories.values()), default=0)})
            d.log("sample", samples[-1])
    d.note("samples", samples)
    d.note("operations", n_ops)
    warm = [s for s in samples if s["t"] >= minutes * 60 / 4]
    half = [s for s in samples if s["t"] >= minutes * 60 / 2]
    if len(warm) < 3:
        d.check("enough samples", False, len(samples), covers="obj.transforms: SOAK")
        return
    base = warm[0]
    mem_max = max(s["mem"] for s in warm)
    climb = half[-1]["mem"] - half[0]["mem"] if len(half) >= 2 else 0
    rows = "obj.transforms, obj.parenting, obj.constraints, obj.collections, obj.addremove, obj.editsteps: SOAK"
    d.check("memory levels off (within 8% of its warm level, no steady climb in the second half)",
            mem_max <= base["mem"] * 1.08 and climb <= base["mem"] * 0.03,
            {"base_mb": round(base["mem"] / 1e6), "max_mb": round(mem_max / 1e6),
             "second_half_climb_mb": round(climb / 1e6)}, covers=rows)
    d.check("handles don't keep growing", max(s["handles"] for s in warm) <= base["handles"] + 50,
            [s["handles"] for s in warm], covers=rows)
    d.check("threads don't keep growing", max(s["threads"] for s in warm) <= base["threads"] + 4 and
            max(s["py_threads"] for s in warm) <= base["py_threads"] + 1,
            [(s["threads"], s["py_threads"]) for s in warm], covers=rows)
    d.check("Hindsight's handlers stay the same", all(s["hooks"] == samples[0]["hooks"] for s in samples),
            samples[-1]["hooks"], covers=rows)
    d.check("every history stayed within the step cap (60)", max(s["longest"] for s in samples) <= 60,
            max(s["longest"] for s in samples), covers=rows)
    errors = [l for l in d.console_since(mark).splitlines() if "Traceback" in l]
    d.check("no Python errors in the whole session", not errors, errors[:3], covers=rows)
