"""A broken draw handler or recorder must log once, switch off only that feature, and say so.

Also checks that File > Open keeps recording (the beta 1 bug) and that self-check brings a
switched-off feature back.
"""
import os

import bpy

import common as C


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    C.fresh_sphere(d)
    C.frame_view(d)
    yield from C.show_sidebar_tab(d)
    C.set_mode(d, "SCULPT")
    yield 0.5
    yield from C.real_stroke(d, 0)
    d.check("one step before the fault", C.info(d)["len"] == 1, C.info(d))

    # ---------------------------------------------------------------- break the slider's drawing
    sc = C.mod("scrubber")
    guard = C.mod("guard")
    real_draw = sc._draw

    def broken(context):
        raise RuntimeError("injected draw failure")
    sc._draw = broken
    mark = d.console_mark()
    for _ in range(6):
        d.redraw()
        yield 0.1
    log = d.console_since(mark)
    d.check("draw failure is logged once", log.count("failed in draw") == 1 and
            log.count("Traceback") == 1, f"messages={log.count('failed in draw')} "
                                         f"tracebacks={log.count('Traceback')}")
    d.check("only the slider is switched off", [n for n, _, _, _ in guard.failed()] == ["slider"], guard.failed())
    d.check("draw handler removed", not sc.draw_handler_installed())
    yield from C.real_stroke(d, 1)
    d.check("recording still works with the slider off", C.info(d)["len"] == 2, C.info(d))
    d.shot("sidebar_reports_slider_off")

    # ---------------------------------------------------------------- self-check brings it back
    sc._draw = real_draw
    with d.override():
        bpy.ops.hindsight.self_check()
    d.check("self-check re-enabled the slider", guard.ok("slider") and sc.draw_handler_installed(),
            guard.failed())
    d.redraw()
    yield 0.3
    d.check("slider draws again", guard.last_ok("slider") is not None)
    d.shot("slider_back")

    # ---------------------------------------------------------------- break the recorder
    rt = C.mod("runtime")
    real_obs = rt._observe

    def broken_observe(*a, **k):
        raise ValueError("injected recorder failure")
    rt._observe = broken_observe
    mark = d.console_mark()
    yield from C.real_stroke(d, 2)
    yield from C.real_stroke(d, 3)
    log = d.console_since(mark)
    d.check("recorder failure logged once", log.count("Traceback") == 1, log[-800:])
    d.check("recorder switched off, history kept", not guard.ok("recorder") and C.info(d)["len"] == 2, C.info(d))
    d.shot("recorder_off")
    rt._observe = real_obs
    with d.override():
        bpy.ops.hindsight.self_check()
    yield from C.real_stroke(d, 4)
    d.check("recording resumes after self-check", guard.ok("recorder") and C.info(d)["len"] == 3, C.info(d))

    # ---------------------------------------------------------------- File > Open keeps recording
    path = os.path.join(d.out, "reopen.blend")
    C.set_mode(d, "OBJECT")
    with d.override():
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
    yield 1.5
    d.check("recorder handlers survive File > Open", all(rt.handlers_installed().values()), rt.handlers_installed())
    with d.override():
        ob = bpy.data.objects["Blob"]
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
    C.set_mode(d, "SCULPT")
    yield 0.5
    before = (C.info(d) or {}).get("len", 0)
    yield from C.real_stroke(d, 0)
    after = (C.info(d) or {}).get("len", 0)
    d.check("a stroke after File > Open is recorded", after == before + 1, f"{before} -> {after}")
    d.check("history came back with the file", before == 3, before)
    d.shot("after_reopen")
