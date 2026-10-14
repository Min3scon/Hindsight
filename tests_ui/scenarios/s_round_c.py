"""Round C: real recording and exact playback.

args:  model=size_100k.blend  obj=Blob_100k  mouse=8 (strokes with simulated mouse input)
       ops=40 (strokes through bpy.ops.sculpt.brush_stroke, plus mask / face set / hide / filter)
       session=0 (extra short strokes, committed right away, for the 1,000 / 10,000-step runs)
       jumps=60

Every recorded step gets a hash of all mesh channels (positions, mask, face sets, hide flags,
colours). Every jump (sidebar slider, on-screen slider drag, Ctrl+Z, random far jumps) is checked
against the hash of that step: bit-exact or it fails. Memory (process working set and history
size) and timings go to result.json notes.
"""
import ctypes
import ctypes.wintypes
import hashlib
import os
import random
import time

import bpy

import common as C


def rss_mb():
    try:
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.wintypes.DWORD), ("PageFaultCount", ctypes.wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(PMC), ctypes.wintypes.DWORD]
        if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return -1.0
        return pmc.WorkingSetSize / 2**20
    except Exception:
        return -1.0


def mesh_hash(ob):
    meshio = C.mod("meshio")
    snap = meshio.read_snapshot(ob.data, sculpt_only=False)
    h = hashlib.blake2b(digest_size=16)
    for k in sorted(snap.arrays):
        h.update(k.encode())
        h.update(snap.arrays[k].tobytes())
    return h.hexdigest()


def run(d):
    a = d.args
    model, objname = a.get("model", "size_100k.blend"), a.get("obj", "Blob_100k")
    n_mouse, n_ops = int(a.get("mouse", 8)), int(a.get("ops", 40))
    session, n_jumps = int(a.get("session", 0)), int(a.get("jumps", 60))
    rng = random.Random(1234)
    yield 1.0
    C.dismiss_splash(d)
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=os.path.join(d.spec["repo"], "test_models", model), load_ui=False)
    yield 1.0
    with d.override():
        ob = bpy.data.objects[objname]
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        bpy.ops.view3d.view_selected()
    C.set_mode(d, "SCULPT")
    yield 0.6
    rt = C.mod("runtime")
    win, area, reg, ui, space = d.view3d()
    hashes = [mesh_hash(ob)]                       # hashes[i] = mesh at history position i
    d.note("verts", len(ob.data.vertices))
    d.note("rss_start_mb", round(rss_mb(), 1))

    def recorded():
        i = C.info(d)
        return 0 if i is None else i["len"]

    problems = []

    def after_action(where):
        """Either a new step was recorded at the end, or the mesh did not change."""
        i = C.info(d) or {"pos": 0, "len": 0}
        n, p = i["len"], i["pos"]
        cur = mesh_hash(ob)
        if p != n:
            problems.append((where, "cursor not at the end", p, n))
        if n == len(hashes):
            hashes.append(cur)
        elif n == len(hashes) - 1:
            if cur != hashes[n]:
                problems.append((where, "mesh changed but no step was recorded"))
        else:
            problems.append((where, "history length jumped", n, len(hashes)))

    # ------------------------------------------------------------ strokes with real mouse input
    for k in range(n_mouse):
        yield from C.real_stroke(d, k)
        after_action(f"mouse stroke {k}")
    d.check(f"{n_mouse} mouse strokes, {n_mouse} steps", recorded() == n_mouse == len(hashes) - 1,
            (recorded(), len(hashes)))

    # ------------------------------------------------------------ operator strokes and other tools
    t_commit = []

    def op_stroke():
        """Generator: a short stroke with simulated mouse input (the real input path).
        bpy.ops.sculpt.brush_stroke in EXEC mode needs surface points, and Object.ray_cast uses a
        stale evaluated mesh in Sculpt Mode, so those strokes drift off the surface over time."""
        cx, cy = reg.width / 2, reg.height / 2
        x0, y0 = cx + rng.uniform(-0.14, 0.14) * reg.width, cy + rng.uniform(-0.16, 0.16) * reg.height
        dx, dy = rng.uniform(6, 12), rng.uniform(-4, 4)
        pts = [(x0 + j * dx, y0 + j * dy) for j in range(rng.randint(5, 9))]
        yield from d.drag(reg, pts, step_wait=0.008)
        yield 0.03                 # the release has to be processed before the commit
        op_results["strokes"] = op_results.get("strokes", 0) + 1

    op_results = {}
    tools = [
        ("mask", lambda: bpy.ops.paint.mask_flood_fill(mode='VALUE', value=rng.uniform(0.1, 0.9))),
        ("mask_clear", lambda: bpy.ops.paint.mask_flood_fill(mode='VALUE', value=0.0)),
        ("face_set", lambda: bpy.ops.sculpt.face_sets_create(mode='ALL')),
        ("filter", lambda: bpy.ops.sculpt.mesh_filter(type='INFLATE', strength=0.02)),
        ("filter_smooth", lambda: bpy.ops.sculpt.mesh_filter(type='SMOOTH', strength=0.3)),
    ]
    tool_fail = {}
    for k in range(n_ops):
        if k % 6 == 5:
            name, fn = tools[(k // 6) % len(tools)]
            try:
                with d.override():
                    fn()
            except Exception as e:
                tool_fail[name] = str(e)[:200]
        else:
            yield from op_stroke()
        t = time.perf_counter()
        with d.override():
            rt.commit(ob)
        t_commit.append((time.perf_counter() - t) * 1000)
        after_action(f"op {k}")
        yield 0.01
    d.check("every change recorded, nothing recorded twice", not problems, problems[:10])
    d.check("most operator strokes changed the mesh (test sanity)", recorded() >= n_mouse + n_ops * 0.5,
            (recorded(), n_mouse + n_ops))
    d.note("tool_failures", tool_fail)
    d.note("op_results", op_results)
    d.note("ops_steps", recorded())
    d.note("commit_ms_median", round(sorted(t_commit)[len(t_commit) // 2], 2) if t_commit else None)

    # ------------------------------------------------------------ jumps, bit-exact after each
    total = recorded()
    bad = []
    t_jump = []
    wm = bpy.context.window_manager

    def verify(where):
        p = C.info(d)["pos"]
        if mesh_hash(ob) != hashes[p]:
            bad.append((where, p))

    slider_prop = C.mod("slider_prop")
    slider_prop.sync()
    for j in range(n_jumps):
        target = rng.randint(0, total)
        t = time.perf_counter()
        with d.override():
            wm.hindsight_step = target            # the sidebar slider
        t_jump.append(((time.perf_counter() - t) * 1000, abs(target - C.info(d)["pos"])))
        if C.info(d)["pos"] != target:
            bad.append(("slider did not reach", target))
        verify(f"sidebar jump {j}")
        if j % 10 == 0:
            yield 0.02
    for target in (0, total, total // 2):          # on-screen slider, real mouse drag
        yield from C.drag_slider_to(d, target)
        verify(f"drag to {target}")
    d.ev("MOUSEMOVE", x=reg.x + reg.width // 2, y=reg.y + reg.height // 2)
    for _ in range(3):                              # Ctrl+Z / Ctrl+Shift+Z
        d.key("Z", ctrl=True)
        yield 0.15
        verify("ctrl+z")
    d.key("Z", ctrl=True, shift=True)
    yield 0.15
    verify("ctrl+shift+z")
    d.check(f"every jump bit-exact ({n_jumps} slider jumps, 3 drags, 4 shortcuts)", not bad, bad[:10])
    d.note("jump_ms_median", round(sorted(x for x, _ in t_jump)[len(t_jump) // 2], 2) if t_jump else None)
    far = [x for x, dist in t_jump if dist > total * 0.6]
    d.note("jump_ms_far_max", round(max(far), 1) if far else None)

    # ------------------------------------------------------------ long session
    if session:
        with d.override():
            wm.hindsight_step = total
        with d.override():
            bpy.ops.paint.mask_flood_fill(mode='VALUE', value=0.0)   # unmasked, so strokes land fully
            rt.commit(ob)
        after_action("clear mask before the session")
        start = recorded()
        t0 = time.time()
        mem = []
        commits = []
        k = -1
        while recorded() - start < session and k < session * 5:
            k += 1
            yield from op_stroke()
            t = time.perf_counter()
            with d.override():
                rt.commit(ob)
            commits.append((time.perf_counter() - t) * 1000)
            after_action(f"session {k}")
            if (recorded() - start) % 500 == 0 and recorded() > start and (not mem or mem[-1]["steps"] != recorded()):
                st = rt.memory_stats()
                mem.append({"steps": recorded(), "rss_mb": round(rss_mb(), 1),
                            "history_mb": round(st["total_bytes"] / 2**20, 2),
                            "commit_ms_last500": round(sum(commits[-500:]) / 500, 2)})
                d.log("session", mem[-1])
                yield 0.01
        d.note("session_steps", recorded() - start)
        d.note("session_seconds", round(time.time() - t0, 1))
        d.note("session_memory", mem)
        n = recorded()
        d.check(f"session reached {session} more steps, every change recorded", not problems and n - start >= session,
                (start, n, problems[:5]))
        bad = []
        t_far = []
        for j in range(120):
            target = rng.randint(0, n) if j % 3 else rng.choice((0, n, n // 2))
            t = time.perf_counter()
            with d.override():
                wm.hindsight_step = target
            t_far.append(((time.perf_counter() - t) * 1000, abs(target - C.info(d)["pos"])))
            verify(f"session jump {j}")
            if j % 10 == 0:
                yield 0.02
        d.check(f"{n}-step history: 120 jumps bit-exact", not bad, bad[:10])
        d.note("session_jump_ms", sorted(round(x, 1) for x, _ in t_far)[-5:])
        st = rt.memory_stats()
        d.note("session_final", {"steps": n, "history_mb": round(st["total_bytes"] / 2**20, 2),
                                 "rss_mb": round(rss_mb(), 1),
                                 "bytes_per_step": int(st["total_bytes"] / max(1, n))})
    d.shot("end")
