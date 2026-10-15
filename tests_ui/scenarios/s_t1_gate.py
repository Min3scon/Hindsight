"""Tier 1 gate in the real interface (real Blender, a window, simulated keyboard and mouse).

  OPS, STEPS  every Tier 1 operation done the way a user does it (keys, menus, the move gizmo,
              typed values, F2, Edit Mode tools including loop cut and knife), each checked to give
              exactly one step in exactly the right histories and one position on the global
              timeline, through the real timer (operator end, and the settle path for a property
              edit); selecting and switching modes give none;
  EXACT       every object's history through the sidebar slider and the on-screen slider, the
              global timeline through its slider, compared with the independent oracle;
  UNDO        Blender's own Ctrl+Z and Ctrl+Shift+Z right after operations, mixed with scrubbing and
              across Edit Mode switches: after every key press Blender's data equals the recorded
              state at Hindsight's position, and nothing is recorded;
  VISUAL      screenshots of the sidebar, the step list, the header popover and the Advanced panel.

args: variant = default | globaloff (Global Undo off) | undomin | undomax (undo steps at the
      preference's minimum or maximum). Run each with --out-tag <version>-<variant>.
"""
import os
import sys

import bpy

import common as C

ROWS = {"move": "obj.transforms", "parent": "obj.parenting", "con": "obj.constraints",
        "coll": "obj.collections", "scene": "obj.addremove", "edit": "obj.editsteps"}


def run(d):
    sys.path.insert(0, d.spec["repo"])
    from tests import oracle as O
    from tests import t1_ops as T
    import importlib
    globaltl = importlib.import_module(C.PKG + ".core.globaltl")      # the installed add-on's own
    rt, datarec, idmap = C.mod("runtime"), C.mod("datarec"), C.mod("idmap")
    variant = d.args.get("variant", "default")
    undo_item = {"default": "UNDO", "globaloff": "UNDO.GLOBAL_OFF"}.get(variant, "UNDO.LIMITS")
    mark = d.console_mark()
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3

    # ------------------------------------------------------------------ preferences for this variant
    edit_prefs = bpy.context.preferences.edit
    rng = edit_prefs.bl_rna.properties["undo_steps"]
    edit_prefs.use_global_undo = variant != "globaloff"
    edit_prefs.undo_steps = {"undomin": max(1, rng.hard_min), "undomax": rng.hard_max}.get(variant, 32)
    d.note("undo", {"global": edit_prefs.use_global_undo, "steps": edit_prefs.undo_steps})

    # ------------------------------------------------------------------ the scene, everything recorded
    with d.override():
        bpy.ops.wm.read_homefile(use_empty=True)
    yield 1.0
    with d.override():
        T.build_scene()
        bpy.ops.view3d.view_all()
    prefs = C.mod("prefs").get_prefs()
    prefs.record_scope = "EVERYTHING"
    for name in T.NAMES:
        with d.override():
            T.select(name)
            bpy.ops.hindsight.record_everything(on=True)
    with d.override():
        bpy.ops.hindsight.record_scene(on=True)
    yield 0.6
    yield from C.show_sidebar_tab(d)
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2
    yield from C.warm_up(d)

    def ob(name):
        return bpy.data.objects.get(name)

    def dbg(where):
        """With --arg debug=1: every history's cursor, the active object's mode and the newest jump
        Blender can undo, printed to the console log."""
        if not d.args.get("debug"):
            return
        with d.override():
            hs_ = {}
            for h in rt.reg().histories.values():
                o = idmap.find(h.uid)
                hs_[getattr(o, "name", h.uid[:6])] = f"{h.cursor}/{len(h.entries)}"
            act = bpy.context.view_layer.objects.active
            jumps = [{"hists": {getattr(idmap.find(k), "name", k[:6]): v for k, v in j["hists"].items()},
                      "undone": j["undone"]} for j in datarec._JUMPS]
            e = ob("E")
            print(f"[dbg] {where}: g={gpos()} active={getattr(act, 'name', None)}/{getattr(act, 'mode', None)} "
                  f"hists={hs_} jumps={jumps} E.faces={len(e.data.polygons) if e else None}", flush=True)

    def snap():
        with d.override():
            return {"__scene__": O.scene_state(), **{o.name: O.object_state(o) for o in bpy.context.scene.objects}}

    def counts():
        """History length per recorded datablock (object or scene) id."""
        return {m: len(h.entries) for h in rt.reg().histories.values() for m in h.members}

    def gpos():
        return globaltl.global_position(rt.reg(), rt._visible_uids())

    def glen():
        return globaltl.global_length(rt.reg(), rt._visible_uids())

    def uid(name):
        return idmap.uid_of(ob(name))

    def screen_of(o):
        from bpy_extras import view3d_utils
        win_, area_, reg_, ui_, space_ = d.view3d()
        p = view3d_utils.location_3d_to_region_2d(reg_, space_.region_3d, o.matrix_world.translation)
        return (reg_.x + int(p.x), reg_.y + int(p.y)) if p is not None else (cx, cy)

    def names_now():
        return {idmap.uid_of(o): o.name for o in bpy.context.scene.objects if idmap.uid_of(o)}

    states, positions, labels, lens, names_at = [snap()], [gpos()], ["start"], [counts()], [names_now()]

    def step(label, row, histories, wait=0.9, items="OPS STEPS"):
        """Generator, after an operation: exactly one step in each of `histories` (object names or
        "scene"), none elsewhere, one global position."""
        yield wait
        now = counts()
        want = {uid(h) if h != "scene" else idmap.uid_of(bpy.context.scene) for h in histories}
        diffs = {u: now.get(u, 0) - before.get(u, 0) for u in set(now) | set(before)}
        bad = {u: n for u, n in diffs.items() if n != (1 if u in want else 0)}
        ok = not bad and gpos() == positions[-1] + (1 if histories else 0) and glen() == gpos()
        d.check(f"{label}: one step", ok, {"diffs": bad, "pos": gpos(), "len": glen()},
                covers=f"{row}: {items}")
        states.append(snap())
        positions.append(gpos())
        labels.append(label)
        lens.append(counts())
        names_at.append(names_now())

    def select(*names, active=None):
        with d.override():
            T.select(*names, active=active)

    def drag_move(x0, y0, dx, dy, ctrl=False):
        """G, move the mouse, click."""
        d.ev("MOUSEMOVE", x=x0, y=y0)
        d.key("G", x=x0, y=y0)
        for i in range(1, 9):
            d.ev("MOUSEMOVE", x=x0 + dx * i // 8, y=y0 + dy * i // 8, ctrl=ctrl)
        d.ev("LEFTMOUSE", "PRESS", x=x0 + dx, y=y0 + dy, ctrl=ctrl)
        d.ev("LEFTMOUSE", "RELEASE", x=x0 + dx, y=y0 + dy, ctrl=ctrl)

    # ------------------------------------------------------------------ object operations
    select("A")
    before = counts()
    drag_move(cx, cy, 60, 20)
    yield from step("G move", ROWS["move"], ["A"])

    before = counts()
    d.key("R", x=cx, y=cy)
    d.key("Z")
    C.type_text(d, "45")
    d.key("RET")
    yield from step("R Z 45, typed", ROWS["move"], ["A"])

    before = counts()
    d.key("S", x=cx, y=cy)
    C.type_text(d, "1.5")
    d.key("RET")
    yield from step("S 1.5, typed", ROWS["move"], ["A"])

    select("B")
    before = counts()
    drag_move(cx, cy, -45, 15, ctrl=True)
    yield from step("G with Ctrl (snapping)", ROWS["move"], ["B"])

    select("C")
    before = counts()
    with d.override():
        bpy.ops.wm.tool_set_by_id(name="builtin.move")
    yield 0.4
    x, y = screen_of(ob("C"))
    pts = [(x - reg.x + i * 4, y - reg.y + i * 2) for i in range(12)]
    yield from d.drag(reg, pts, step_wait=0.03)
    with d.override():
        bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
    yield from step("drag with the move gizmo", ROWS["move"], ["C"])

    select("A", "B", active="A")
    before = counts()
    drag_move(cx, cy, 0, 40)
    yield from step("G with two objects", ROWS["move"], ["A", "B"])

    select("B", "C", active="C")
    before = counts()
    with d.override():
        bpy.ops.wm.context_set_enum(data_path="tool_settings.transform_pivot_point", value="CURSOR")
    d.key("R", x=cx, y=cy)
    C.type_text(d, "30")
    d.key("RET")
    yield from step("R about the 3D cursor, two objects", ROWS["move"], ["B", "C"])
    with d.override():
        bpy.ops.wm.context_set_enum(data_path="tool_settings.transform_pivot_point", value="MEDIAN_POINT")

    select("Pivot")
    before = counts()
    ob("Pivot").location.x += 0.75            # a property edit (what the N panel field does): settle path
    yield from step("N panel location edit (settle)", ROWS["move"], ["Pivot"], wait=1.2, items="STEPS")

    select("C")
    before = counts()
    with d.override():
        bpy.ops.object.transforms_to_deltas(mode="ALL")
    yield from step("Apply > Transforms to Deltas", ROWS["move"], ["C"])

    select("A")
    before = counts()
    with d.override():
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    yield from step("Ctrl+A Apply Scale", ROWS["move"], ["A"])

    select("A", "Pivot", active="Pivot")
    before = counts()
    d.key("P", ctrl=True, x=cx, y=cy)
    yield 0.4
    d.key("RET")
    yield from step("Ctrl+P, Object", ROWS["parent"], ["A"])

    select("A")
    before = counts()
    d.key("P", alt=True, x=cx, y=cy)
    yield 0.4
    d.key("DOWN_ARROW")
    d.key("RET")
    yield from step("Alt+P, Clear and Keep Transformation", ROWS["parent"], ["A"])

    select("B")
    before = counts()
    with d.override():
        bpy.ops.object.constraint_add(type="COPY_LOCATION")
    yield from step("add a constraint", ROWS["con"], ["B"])
    before = counts()
    ob("B").constraints[0].influence = 0.4      # dragging the Influence slider: settle path
    yield from step("constraint influence (settle)", ROWS["con"], ["B"], wait=1.2, items="STEPS")

    select("C")
    before = counts()
    with d.override():                            # what M > + New Collection runs
        bpy.ops.object.move_to_collection(collection_uid=bpy.context.scene.collection.session_uid,
                                          is_new=True, new_collection_name="Extra")
    yield from step("M, New Collection", ROWS["coll"], ["C", "scene"])

    select("B")
    before = counts()
    d.key("H", x=cx, y=cy)
    yield from step("H", ROWS["coll"], ["B"])
    before = counts()
    d.key("H", alt=True, x=cx, y=cy)
    yield from step("Alt+H", ROWS["coll"], ["B"])

    select("Pivot")
    before = counts()
    d.key("F2", x=cx, y=cy)
    yield 0.5
    d.key("A", ctrl=True)
    C.type_text(d, "Hub")
    d.key("RET")
    yield from step("F2 rename", ROWS["move"], ["Hub"])       # named as it is after the operation

    select("B")
    before = counts()
    d.key("D", shift=True, x=cx, y=cy)
    yield 0.3
    d.ev("RIGHTMOUSE", "PRESS", x=cx, y=cy)
    d.ev("RIGHTMOUSE", "RELEASE", x=cx, y=cy)
    yield from step("Shift+D", ROWS["scene"], ["scene"])
    select("C")
    before = counts()
    warn = C.mod("warnings")
    shown = []
    real_check = warn.linked_duplicate_check

    def spy(context):
        ob_ = context.view_layer.objects.active
        shown.append(bool(ob_ is not None and warn._shares_with_tracked(ob_)))
        return real_check(context)
    warn.linked_duplicate_check = spy
    d.key("D", alt=True, x=cx, y=cy)
    yield 0.3
    d.ev("RIGHTMOUSE", "PRESS", x=cx, y=cy)
    d.ev("RIGHTMOUSE", "RELEASE", x=cx, y=cy)
    yield 0.4
    d.shot("alt_d_warning")
    d.check("Alt+D on a recorded mesh: the shared-mesh warning comes up", any(shown), shown,
            covers="sculpt.joinsep, obj.addremove: WARN")
    d.key("ESC")
    warn.linked_duplicate_check = real_check
    yield from step("Alt+D", ROWS["scene"], ["scene"])
    select("B.001")
    before = counts()
    d.key("X", x=cx, y=cy)
    yield 0.3
    d.key("RET")
    yield from step("X, Delete", ROWS["scene"], ["scene"])
    before = counts()
    with d.override():
        bpy.ops.mesh.primitive_monkey_add(location=(0, -4, 0))
    yield from step("Shift+A, Monkey", ROWS["scene"], ["scene"])

    # ------------------------------------------------------------------ Edit Mode step by step
    select("E")
    before = counts()
    d.key("TAB", x=cx, y=cy)
    yield from step("Tab into Edit Mode (no step)", ROWS["edit"], [])
    with d.override():
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.mesh.select_random(ratio=0.2, seed=4)
    before = counts()
    yield from step("selecting (no step)", ROWS["edit"], [])
    before = counts()
    d.key("E", x=cx, y=cy)
    for i in range(1, 7):
        d.ev("MOUSEMOVE", x=cx, y=cy + i * 5)
    d.ev("LEFTMOUSE", "PRESS", x=cx, y=cy + 30)
    d.ev("LEFTMOUSE", "RELEASE", x=cx, y=cy + 30)
    yield from step("E extrude", ROWS["edit"], ["E"])
    with d.override():
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.mesh.select_random(ratio=0.3, seed=6)
    gx, gy = screen_of(ob("E"))
    before = counts()
    d.ev("MOUSEMOVE", x=gx + 15, y=gy)
    d.key("B", ctrl=True, x=gx + 15, y=gy)               # bevel: moving away from the selection widens it
    for i in range(1, 9):
        d.ev("MOUSEMOVE", x=gx + 15 + i * 8, y=gy)
    d.ev("LEFTMOUSE", "PRESS", x=gx + 79, y=gy)
    d.ev("LEFTMOUSE", "RELEASE", x=gx + 79, y=gy)
    yield from step("Ctrl+B bevel", ROWS["edit"], ["E"])
    with d.override():
        bpy.ops.mesh.select_mode(type="FACE")
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.mesh.select_random(ratio=0.15, seed=9)
    before = counts()
    d.key("I", x=cx + 60, y=cy)
    for i in range(1, 7):
        d.ev("MOUSEMOVE", x=cx + 60 - i * 3, y=cy)
    d.ev("LEFTMOUSE", "PRESS", x=cx + 42, y=cy)
    d.ev("LEFTMOUSE", "RELEASE", x=cx + 42, y=cy)
    yield from step("I inset", ROWS["edit"], ["E"])
    # loop cut: hover an edge of the grid, Ctrl+R, click, right click (no slide)
    with d.override():
        bpy.ops.mesh.select_mode(type="VERT")
        bpy.ops.mesh.select_all(action="DESELECT")
    e = ob("E")
    from bpy_extras import view3d_utils
    me = e.data
    e.update_from_editmode()
    v0, v1 = me.vertices[me.edges[2].vertices[0]].co, me.vertices[me.edges[2].vertices[1]].co
    mid = e.matrix_world @ ((v0 + v1) / 2)
    p = view3d_utils.location_3d_to_region_2d(reg, space.region_3d, mid)
    ex, ey = reg.x + int(p.x), reg.y + int(p.y)
    before = counts()
    d.ev("MOUSEMOVE", x=ex, y=ey)
    yield 0.2
    d.key("R", ctrl=True, x=ex, y=ey)
    yield 0.3
    d.ev("MOUSEMOVE", x=ex + 1, y=ey)
    yield 0.2
    d.ev("LEFTMOUSE", "PRESS", x=ex + 1, y=ey)
    d.ev("LEFTMOUSE", "RELEASE", x=ex + 1, y=ey)
    yield 0.3
    d.ev("RIGHTMOUSE", "PRESS", x=ex + 1, y=ey)
    d.ev("RIGHTMOUSE", "RELEASE", x=ex + 1, y=ey)
    yield from step("Ctrl+R loop cut", ROWS["edit"], ["E"])
    # knife: K, a click on the leftmost and on the rightmost face of the flat grid, Enter. The knife
    # only takes points on the mesh, so the clicks go on face centers (projected to the screen)
    e.update_from_editmode()
    mw = e.matrix_world
    flat = [mw @ f.center for f in me.polygons if f.normal.z > 0.99]
    low = min(c.z for c in flat)
    pts = []
    for c in flat:
        if abs(c.z - low) < 1e-4:
            q = view3d_utils.location_3d_to_region_2d(reg, space.region_3d, c)
            if q is not None:
                pts.append((reg.x + int(q.x), reg.y + int(q.y)))
    k0, k1 = min(pts), max(pts)
    before = counts()

    def knife_gesture():
        """K, a click on each point (the pointer moved there in small steps first), Enter."""
        d.ev("MOUSEMOVE", x=k0[0], y=k0[1])
        yield 0.2
        d.key("K", x=k0[0], y=k0[1])
        yield 0.5
        prev = k0
        for (kx, ky) in (k0, k1):
            for t in (0.25, 0.5, 0.75, 1.0):
                d.ev("MOUSEMOVE", x=int(prev[0] + (kx - prev[0]) * t), y=int(prev[1] + (ky - prev[1]) * t))
                yield 0.05
            prev = (kx, ky)
            d.ev("LEFTMOUSE", "PRESS", x=kx, y=ky)
            d.ev("LEFTMOUSE", "RELEASE", x=kx, y=ky)
            yield 0.25
        d.key("RET")
        yield 0.3

    def edge_count():
        with d.override():
            e.update_from_editmode()
        return len(e.data.edges)

    n_edges = edge_count()
    yield from knife_gesture()
    if edge_count() == n_edges:
        # the simulated clicks didn't reach Blender's knife (no cut, so rightly no step): once more
        d.log("knife: Blender made no cut from the simulated clicks; the gesture is repeated once")
        d.note("knife_retry", True)
        yield from knife_gesture()
    yield from step("K knife", ROWS["edit"], ["E"])
    with d.override():
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.mesh.select_random(ratio=0.08, seed=13)
    before = counts()
    d.key("M", x=cx, y=cy)
    yield 0.4
    d.key("RET")                                  # At Center (first item)
    yield from step("M merge, At Center", ROWS["edit"], ["E"])
    with d.override():
        bpy.ops.mesh.select_random(ratio=0.05, seed=17)
    before = counts()
    d.key("X", x=cx, y=cy)
    yield 0.4
    d.key("RET")                                  # Vertices (first item)
    yield from step("X delete vertices", ROWS["edit"], ["E"])
    with d.override():
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.region_to_loop()
    before = counts()
    d.key("F", x=cx, y=cy)
    yield from step("F fill", ROWS["edit"], ["E"])
    before = counts()
    d.key("TAB", x=cx, y=cy)
    yield from step("Tab out of Edit Mode (no step)", ROWS["edit"], [])

    d.shot("sidebar_after_tier1_ops")

    # ------------------------------------------------------------------ exactness: every object's history
    wm = bpy.context.window_manager
    bad = []
    for name in ("A", "B", "C", "E", "Hub"):
        u = uid(name)
        h = rt.reg().history_for(u)
        want = {}                                    # position in this history -> recorded state
        for i, st in enumerate(states):
            then = names_at[i].get(u)
            if then is not None and then in st:
                want.setdefault(lens[i].get(u, 0), st[then])
        for pos in sorted(want)[::-1] + sorted(want):
            select(idmap.find(u).name)                # by id: scrubbing past a rename renames it back
            with d.override():
                C.mod("slider_prop").sync()
                wm.hindsight_step = pos
            yield 0.15
            with d.override():
                got = O.object_state(idmap.find(u), derived=False)
            if _own(got, name) != _own(want[pos], name):
                bad.append((name, pos, O.diff(_own(want[pos], name), _own(got, name))[:2]))
        select(idmap.find(u).name)
        with d.override():
            wm.hindsight_step = len(h.entries)
        yield 0.15
    d.check("every object's history is exact through the sidebar slider, both ways", not bad, bad[:3],
            covers="obj.transforms, obj.parenting, obj.constraints, obj.collections, obj.editsteps: EXACT")

    # the on-screen slider, dragged with the mouse, on A
    select("A")
    h = rt.history_of(ob("A"))
    yield 0.3
    target = max(1, len(h.entries) // 2)
    yield from C.drag_slider_to(d, target)
    d.check("dragging the on-screen slider moves A's history", h.cursor == target, (h.cursor, target),
            covers="obj.transforms: EXACT")
    with d.override():
        wm.hindsight_step = len(h.entries)
    yield 0.3

    # ------------------------------------------------------------------ the global timeline
    def hs():
        """The scene's Hindsight settings, looked up again each time: Blender's undo rebuilds the
        scene, and a reference kept across an undo points at freed memory."""
        return bpy.context.scene.hindsight

    hs().use_global = True
    yield 0.3
    gbad = []
    for i in list(range(len(states) - 1, -1, -1)) + list(range(len(states))):
        with d.override():
            rt.global_goto(bpy.context, positions[i])
        yield 0.1
        got = snap()
        if got != states[i]:
            gbad.append((labels[i], O.diff(states[i], got)[:2]))
    d.check("global timeline: every operation boundary is exact, both ways", not gbad, gbad[:3],
            covers="obj.transforms, obj.parenting, obj.constraints, obj.collections, obj.addremove, "
                   "obj.editsteps: EXACT")
    d.shot("global_timeline")
    with d.override():
        bpy.ops.wm.call_panel(name="VIEW3D_PT_hindsight_popover", keep_open=True)
    yield 0.5
    d.shot("header_popover_step_list")
    d.key("ESC")
    yield 0.3

    # ------------------------------------------------------------------ Blender's own undo
    def in_sync():
        """Blender's data equals the recorded state at Hindsight's global position, and every
        history's cursor is where it was at that state (a global position alone can hide one
        history whose cursor no longer describes its object)."""
        p = gpos()
        idx = max((i for i, q in enumerate(positions) if q == p), default=None)
        if idx is None:
            return False, (p, None)
        now = snap()
        cur = {m: h.cursor for h in rt.reg().histories.values() for m in h.members}
        off = {m: (cur.get(m), n) for m, n in lens[idx].items() if cur.get(m) != n}
        return now == states[idx] and not off, (p, labels[idx], O.diff(states[idx], now)[:3], off)

    n_all = counts()
    dbg("before Ctrl+Z")
    moved = []
    for k in range(3):
        p0 = gpos()
        d.key("Z", ctrl=True, x=cx, y=cy)
        yield 0.9
        dbg(f"Ctrl+Z {k + 1}")
        ok, where = in_sync()
        moved.append(gpos() - p0)
        d.check(f"Ctrl+Z {k + 1}: data and history in sync", ok, where,
                covers=f"obj.transforms, obj.addremove, obj.editsteps, obj.collections: {undo_item}")
    for k in range(3):
        d.key("Z", ctrl=True, shift=True, x=cx, y=cy)
        yield 0.9
        dbg(f"Ctrl+Shift+Z {k + 1}")
        ok, where = in_sync()
        d.check(f"Ctrl+Shift+Z {k + 1}: data and history in sync", ok, where,
                covers=f"obj.transforms, obj.addremove, obj.editsteps, obj.collections: {undo_item}")
    d.check("undo and redo recorded nothing", counts() == n_all, None,
            covers=f"obj.transforms, obj.addremove: {undo_item}")
    if variant in ("default", "undomax"):
        d.check("Ctrl+Z went back one operation each time", moved == [-1, -1, -1], moved,
                covers=f"obj.transforms: {undo_item}")

    # scrub, then Ctrl+Z undoes the jump, Ctrl+Shift+Z redoes it
    with d.override():
        rt.global_goto(bpy.context, max(0, gpos() - 4))
    yield 0.4
    dbg("after the scrub")
    ok0, _w = in_sync()
    d.key("Z", ctrl=True, x=cx, y=cy)
    yield 0.9
    dbg("scrub, Ctrl+Z")
    ok1, w1 = in_sync()
    d.key("Z", ctrl=True, shift=True, x=cx, y=cy)
    yield 0.9
    dbg("scrub, Ctrl+Shift+Z")
    ok2, w2 = in_sync()
    d.check("scrub, Ctrl+Z, Ctrl+Shift+Z: data and history in sync", ok0 and ok1 and ok2, (w1, w2),
            covers=f"obj.transforms, obj.addremove: {undo_item}")
    with d.override():
        rt.global_goto(bpy.context, glen())
    yield 0.4

    # across a mode switch: Tab, G in Edit Mode, Tab, then Ctrl+Z three times
    hs().use_global = False
    select("E")
    d.key("TAB", x=cx, y=cy)
    yield 0.5
    with d.override():
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.mesh.select_random(ratio=0.1, seed=21)
    drag_move(cx, cy, 25, 0)
    yield 0.9
    d.key("TAB", x=cx, y=cy)
    yield 0.6
    hs().use_global = True
    states.append(snap())
    positions.append(gpos())
    labels.append("G in Edit Mode")
    lens.append(counts())
    n_all = counts()
    sync = []
    for k in range(3):
        d.key("Z", ctrl=True, x=cx, y=cy)
        yield 0.9
        with d.override():
            if bpy.context.object is not None and bpy.context.object.mode == "EDIT":
                sync.append(("edit", rt.history_of(ob("E")).cursor))
            else:
                ok, where = in_sync()
                sync.append(("object", ok, where))
    for k in range(3):
        d.key("Z", ctrl=True, shift=True, x=cx, y=cy)
        yield 0.9
    with d.override():
        if bpy.context.object is not None and bpy.context.object.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
    yield 0.6
    ok, where = in_sync()
    d.check("Ctrl+Z and Ctrl+Shift+Z across Tab: back in sync, nothing recorded", ok and counts() == n_all,
            {"end": where, "steps": sync}, covers=f"obj.editsteps: UNDO.MODES; obj.transforms: UNDO.MODES")

    # ------------------------------------------------------------------ panels to look at
    with d.override():
        space.show_region_ui = True                   # undo may have hidden it: the shot needs it open
    yield 0.4
    d.shot("sidebar_end")
    errors = [l for l in d.console_since(mark).splitlines() if "Traceback" in l or "Error" in l and "hindsight" in l.lower()]
    d.check("no Python errors", not errors, errors[:5])


def _own(state, name):
    """An object's state without values that depend on other objects (its own history alone doesn't
    move those). References to the renamed Pivot read as Hub."""
    s = {k: v for k, v in state.items() if k != "canon"}
    s["rna"] = {k: v for k, v in s["rna"].items() if k not in ("matrix_world", "matrix_local")}
    return _rename(s)


def _rename(v):
    if isinstance(v, tuple) and len(v) == 3 and v[0] == "ID" and v[2] == "Pivot":
        return ("ID", v[1], "Hub")
    if isinstance(v, dict):
        return {k: _rename(x) for k, x in v.items()}
    if isinstance(v, (tuple, list)):
        return type(v)(_rename(x) for x in v)
    return v
