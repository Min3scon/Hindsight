import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import traceback

import bpy  # noqa: F401  (scrubber imports bpy at module level)

from hindsight.bl import scrubber as S

ok = True


def run(fn):
    global ok
    try:
        fn()
        print("PASS", fn.__name__)
    except Exception:
        ok = False
        print("FAIL", fn.__name__)
        traceback.print_exc()


class P:  # prefs-like
    scrub_height = 6
    scrub_margin = 18
    scrub_width_pct = 60


def test_layout_basic():
    lay = S.layout(1000, 600, 1.0, P())
    x0, y0, x1, y1 = lay["bar"]
    assert abs(x0 - 200) < 1e-6 and abs(x1 - 800) < 1e-6, lay["bar"]
    assert abs(y0 - 18) < 1e-6 and abs(y1 - 24) < 1e-6, lay["bar"]
    cx, cy, r = lay["dot"]
    assert cx < x0 and abs(cy - 21) < 1e-6 and r > 0
    x, y = lay["counter"]
    assert x > x1
    # None prefs -> built-in defaults
    assert S.layout(1000, 600, 1.0, None)["bar"] == lay["bar"]
    # background ui_scale 0.0 behaves like 1.0
    assert S.layout(1000, 600, 0.0, P())["bar"] == lay["bar"]


def test_ends():
    lay = S.layout(1000, 600, 1.0, P())
    x0, _, x1, _ = lay["bar"]
    n = 214
    assert S.pos_from_x(x0, lay, n) == 0
    assert S.pos_from_x(x1, lay, n) == n
    assert S.pos_from_x(x0 - 50, lay, n) == 0
    assert S.pos_from_x(x1 + 50, lay, n) == n
    assert abs(S.x_from_pos(0, lay, n) - x0) < 1e-9
    assert abs(S.x_from_pos(n, lay, n) - x1) < 1e-9
    assert S.pos_from_x(500, lay, 0) == 0
    assert S.x_from_pos(5, lay, 0) == x0


def test_round_trip():
    lay = S.layout(1000, 600, 1.0, P())
    x0, _, x1, _ = lay["bar"]
    for n in (1, 7, 214, 10000):
        half = (x1 - x0) / n / 2.0
        for i in range(0, 601, 3):
            x = x0 + i
            p = S.pos_from_x(x, lay, n)
            assert 0 <= p <= n
            assert abs(S.x_from_pos(p, lay, n) - x) <= half + 1e-6, (n, x, p)
        for p in range(0, n + 1, max(1, n // 50)):
            assert S.pos_from_x(S.x_from_pos(p, lay, n), lay, n) == p


def test_hit():
    lay = S.layout(1000, 600, 1.0, P())
    cx, cy, r = lay["dot"]
    x0, y0, x1, y1 = lay["bar"]
    assert S.hit_test(cx, cy, lay) == "dot"
    assert S.hit_test(500, (y0 + y1) / 2, lay) == "bar"
    assert S.hit_test(500, y1 + 9, lay) == "bar"
    assert S.hit_test(500, y0 - 9, lay) == "bar"
    assert S.hit_test(500, y1 + 11, lay) is None
    assert S.hit_test(500, 300, lay) is None
    assert S.hit_test(100, (y0 + y1) / 2, lay) is None
    assert S.hit_test(900, (y0 + y1) / 2, lay) is None


def test_ui_scale_2():
    a = S.layout(1000, 600, 1.0, P())
    b = S.layout(1000, 600, 2.0, P())
    ha = a["bar"][3] - a["bar"][1]
    hb = b["bar"][3] - b["bar"][1]
    assert abs(hb - 2 * ha) < 1e-6
    assert abs(b["bar"][1] - 2 * a["bar"][1]) < 1e-6          # margin doubles
    assert abs(b["dot"][2] - 2 * a["dot"][2]) < 1e-6          # dot radius doubles
    assert abs(b["font_size"] - 2 * a["font_size"]) < 1e-6
    assert abs(b["hit_pad"] - 2 * a["hit_pad"]) < 1e-6
    assert b["bar"][0] == a["bar"][0] and b["bar"][2] == a["bar"][2]  # width is a % of region


def test_geometry():
    lay = S.layout(1000, 600, 1.0, P())
    g = S.geometry(lay, 50, 100, morph_target=20)
    for k in ("bg", "fill", "handle", "dot", "tick"):
        assert g[k] and len(g[k]) % 3 == 0, k
    assert "tick" not in S.geometry(lay, 50, 100)


class FakeRuntime:
    def __init__(self, info):
        self.info = info
        self.calls = []
        self.redraws = 0

    def scrub_info(self, context):
        return self.info

    def goto(self, context, pos, final=True):
        self.calls.append((pos, final))

    def tag_redraw_all(self):
        self.redraws += 1


def test_drag_throttle_and_final():
    lay = S.layout(1000, 600, 1.0, P())
    fake = FakeRuntime({"pos": 10, "len": 100, "cap": 10000, "global": False,
                        "locked": False, "morph_target": None})
    t = [0.0]
    old_rt, old_clock = S._runtime, S._clock
    S._runtime = lambda: fake
    S._clock = lambda: t[0]
    try:
        d = S._Drag(lay, fake.info)
        d.begin(None, S.x_from_pos(30, lay, 100))
        assert fake.calls == [(30, False)], fake.calls
        t[0] += 0.001                                  # within throttle window -> skipped
        d.move(None, S.x_from_pos(31, lay, 100))
        assert fake.calls == [(30, False)], fake.calls
        t[0] += 0.02                                   # flush applies the pending position
        d.flush(None)
        assert fake.calls[-1] == (31, False), fake.calls
        t[0] += 0.001                                  # same pos -> no call
        d.move(None, S.x_from_pos(31, lay, 100))
        assert len(fake.calls) == 2
        t[0] += 0.001
        d.move(None, S.x_from_pos(40, lay, 100))       # throttled, but end() uses it
        assert d.end(None, cancel=False) == 40
        assert fake.calls[-1] == (40, True), fake.calls
        assert fake.redraws == 3

        fake.calls.clear()
        d = S._Drag(lay, fake.info)
        d.begin(None, S.x_from_pos(70, lay, 100))
        assert d.end(None, cancel=True) == 10          # cancel restores the start position
        assert fake.calls == [(70, False), (10, True)], fake.calls
    finally:
        S._runtime, S._clock = old_rt, old_clock


def test_register():
    def registered():
        return [("bl_rna" in c.__dict__) for c in S.classes]
    S.register()
    assert all(registered()), registered()
    assert hasattr(bpy.ops.hindsight, "scrub")
    assert S._draw_handle is not None
    S.register()                                       # idempotent
    S.unregister()
    assert S._draw_handle is None
    assert not any(registered()), registered()
    S.unregister()                                     # safe twice
    S.register()
    S.unregister()


for f in (test_layout_basic, test_ends, test_round_trip, test_hit, test_ui_scale_2,
          test_geometry, test_drag_throttle_and_final, test_register):
    run(f)

sys.stdout.flush()
os._exit(0 if ok else 1)
