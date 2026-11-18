"""ab.py ROUNDS OUTDIR [bench args...]: run draw_bench.py for baseline and worktree alternately."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
S = os.path.dirname(os.path.dirname(HERE))
OLD = os.path.join(S, "baseline")
NEW = "/home/user/Hindsight/.claude/worktrees/agent-a79aa8a5e8c63e2d4"
PY = "/home/user/venvs/bpy53/bin/python"
rounds = int(sys.argv[1])
out = sys.argv[2]
extra = sys.argv[3:]
os.makedirs(out, exist_ok=True)
for r in range(rounds):
    for tag, root in (("old", OLD), ("new", NEW)):
        p = os.path.join(out, f"{tag}_{r}.json")
        subprocess.run([PY, os.path.join(HERE, "draw_bench.py"), root, "--json", p] + extra,
                       check=True, capture_output=True)
        print(tag, r, "done", flush=True)
