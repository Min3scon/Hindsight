"""Physics and particle settings of an object that the global history deleted and brings back.

D102: the object came back with each physics modifier at its default settings (cloth, soft body,
collision, the particle system with a new default settings datablock and seed), its force field and
its rigid body at defaults: only the modifier's own top-level values were recorded. Baked caches are
files on disk and are not part of it.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.core import globaltl
from hindsight.bl import runtime

C = bpy.context


def glen():
    return globaltl.global_length(runtime.reg(), runtime._visible_uids())


def read(ob):
    out = {}
    cl = ob.modifiers.get("Cloth")
    if cl is not None:
        out["cloth"] = (cl.settings.quality, round(cl.settings.mass, 5), round(cl.collision_settings.distance_min, 5))
    sb = ob.modifiers.get("Softbody")
    if sb is not None:
        out["soft"] = round(sb.settings.mass, 5)
    if ob.collision is not None and ob.modifiers.get("Collision") is not None:
        out["collision"] = round(ob.collision.damping, 5)
    pm = ob.modifiers.get("Particles")
    if pm is not None:
        ps = pm.particle_system
        out["particles"] = (ps.settings.name, ps.settings.count, ps.seed)
    out["field"] = (ob.field.type, round(ob.field.strength, 5)) if ob.field else None
    out["rigid"] = (ob.rigid_body.type, round(ob.rigid_body.mass, 5)) if ob.rigid_body else None
    return out


def setup_physics(ob):
    with C.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
        bpy.ops.rigidbody.object_add()
    ob.rigid_body.type = "ACTIVE"
    ob.rigid_body.mass = 7.5
    cl = ob.modifiers.new("Cloth", "CLOTH")
    cl.settings.quality = 9
    cl.settings.mass = 2.5
    cl.collision_settings.distance_min = 0.0123
    sb = ob.modifiers.new("Softbody", "SOFT_BODY")
    sb.settings.mass = 3.25
    ob.modifiers.new("Collision", "COLLISION")
    ob.collision.damping = 0.42
    pm = ob.modifiers.new("Particles", "PARTICLE_SYSTEM")
    pm.particle_system.settings.name = "MyParticles"
    pm.particle_system.settings.count = 77
    pm.particle_system.seed = 5
    ob.field.type = "WIND"
    ob.field.strength = 3.5


def test_deleted_object_keeps_physics_settings():
    L.fresh_scene()
    ob = bpy.data.objects["A"]
    setup_physics(ob)
    want = read(ob)
    assert want["particles"] == ("MyParticles", 77, 5) and want["rigid"] == ("ACTIVE", 7.5), want
    L.track_all()
    T.delete(["A"]).run()
    L.tick_like("delete A")
    assert "A" not in C.scene.objects
    runtime.global_goto(C, 0, True)
    ob = C.scene.objects.get("A")
    assert ob is not None, "the global history brings A back"
    got = read(ob)
    assert got == want, (got, want)
    assert not [p for p in bpy.data.particles if p.users == 0], "no stray default particle settings"
    runtime.global_goto(C, glen(), True)
    assert "A" not in C.scene.objects


if __name__ == "__main__":
    H.run(test_deleted_object_keeps_physics_settings)
    H.finish()
