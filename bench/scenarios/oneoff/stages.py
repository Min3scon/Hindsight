import os, sys, gc, tempfile
ROOT, N, STEPS = sys.argv[1], sys.argv[2], sys.argv[3]
WT = "/home/user/Hindsight/.claude/worktrees/agent-a33d7bef5d9a99ac0"
sys.argv = ["x", ROOT, N, STEPS]
sys.path.insert(0, WT)
import bench.saveload_bench as B
import bpy
from hindsight.bl import runtime, persistence as P
from hindsight.core import persist
from hindsight.core.types import SETTINGS
B.setup()
tmp = tempfile.mkdtemp()
SETTINGS.save_mode = "EMBED"
blend = os.path.join(tmp, "a.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
MB = 2 ** 20
def rss(tag):
    gc.collect()
    print(f"{tag:<40} RSS {B.proc('VmRSS')/MB:8.1f}  HWM {B.proc('VmHWM')/MB:8.1f}")
def reset():
    with open("/proc/self/clear_refs", "w") as fh:
        fh.write("5")
text = bpy.data.texts[P.TEXT_NAME]
reset(); rss("start (old prop present)")
parts = persist.registry_parts(runtime.reg(), "auto")
del text[P.DATA_PROP]
rss("old prop deleted")
data = persist.parts_to_b64(parts) if hasattr(persist, "parts_to_b64") else None
rss("text built")
text[P.DATA_PROP] = data
rss("assigned")
del data, parts
rss("python text dropped")
reset(); rss("before Blender's own write (handlers off)")
bpy.app.handlers.save_pre.remove(P.on_save_pre)
bpy.ops.wm.save_as_mainfile(filepath=blend)
rss("after write")
B.H.finish(0)
