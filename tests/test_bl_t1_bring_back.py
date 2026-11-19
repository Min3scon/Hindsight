"""L12: when a step of an object's own history refers to an object that is deleted now (its parent, a
constraint target), the Warnings panel offers "Bring back <name>". It makes the deleted object exist
again as it was when it was deleted, as a new step of the scene's history, and the waiting reference
is put in."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from types import SimpleNamespace
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.bl import adapters, datarec, runtime, ui_panel

C = bpy.context


class FakeLayout:
    def __init__(self, log=None):
        self.__dict__["log"] = log if log is not None else []

    def __setattr__(self, k, v):
        self.__dict__[k] = v

    def __getattr__(self, name):
        def fn(*args, **kw):
            self.log.append((name, args, kw))
            return FakeLayout(self.log)
        return fn


def panel_ops(name):
    T.select(name)
    lay = FakeLayout()
    if not ui_panel.VIEW3D_PT_hindsight_warnings.poll(C):
        return []
    ui_panel.VIEW3D_PT_hindsight_warnings.draw(SimpleNamespace(layout=lay), C)
    return [(a[0], kw.get("text")) for n, a, kw in lay.log if n == "operator"]


def test_bring_back_a_deleted_parent():
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    a, piv = bpy.data.objects["A"], bpy.data.objects["Pivot"]
    pname = piv.name
    a.parent = piv
    L.tick_like("parent")
    a.parent = None
    L.tick_like("clear parent")
    with C.temp_override(selected_objects=[piv], active_object=piv, object=piv):
        bpy.ops.object.delete()
    L.tick_like("delete pivot")
    assert bpy.data.objects.get(pname) is None
    h = runtime.history_of(a)
    runtime._goto_history(h, h.cursor - 1, True)
    refs = adapters.missing_refs(a)
    assert refs, "the parent is deleted: the reference waits"
    assert ("hindsight.bring_back", f"Bring back {pname}") in panel_ops("A"), panel_ops("A")
    hs = runtime.history_of(C.scene)
    n = len(hs.entries)
    ob = datarec.bring_back(refs[0][1])
    assert ob is not None and ob.name == pname
    assert a.parent == ob, a.parent
    assert not adapters.missing_refs(a)
    assert len(hs.entries) == n + 1 and hs.entries[-1].meta.get("label") == datarec.BROUGHT_BACK, \
        [e.meta.get("label") for e in hs.entries]
    assert ("hindsight.bring_back", f"Bring back {pname}") not in panel_ops("A")
    runtime._goto_history(hs, hs.cursor - 1, True)
    assert bpy.data.objects.get(pname) is None, "going back over Bring Back takes it away again"


if __name__ == "__main__":
    H.run(test_bring_back_a_deleted_parent)
    H.finish()
