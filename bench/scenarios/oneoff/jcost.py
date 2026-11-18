import os, sys, time, statistics, tempfile, shutil
ROOT = sys.argv[1]
sys.path.insert(0, ROOT)
import numpy as np
from hindsight.core import delta, step as S
from hindsight.core.journal import Journal
from hindsight.core.types import DELTA, POSITION_KEY, Step

for nv in (100_000, 1_000_000):
    rng = np.random.default_rng(1)
    pos = np.sin(np.linspace(0, 50, nv * 3, dtype=np.float32))
    k = nv // 50
    fall = np.sin(np.linspace(0, np.pi, k, dtype=np.float32)) ** 2
    tmp = tempfile.mkdtemp()
    j = Journal(os.path.join(tmp, "j.hsj"))
    tq, tw, tf, sz = [], [], [], []
    for i in range(200):
        new = pos.copy()
        s = int(rng.integers(0, nv - k))
        new.reshape(-1, 3)[s:s + k] *= (1 + 1e-3 * fall)[:, None]
        st = Step(seq=i, label="Draw", mode="SCULPT", target="x" * 32, time=1.0, kind=DELTA,
                  deltas=[delta.diff(POSITION_KEY, pos, new)])
        pos = new
        t0 = time.perf_counter()
        b = S.step_to_bytes(st, "auto", 0)
        t1 = time.perf_counter()
        j.append("STEP", {"hist": "h" * 32, "at": i}, b)
        t2 = time.perf_counter()
        j.flush(fsync=False)
        t3 = time.perf_counter()
        tq.append(t1 - t0); tw.append(t2 - t1); tf.append(t3 - t2); sz.append(len(b))
    j.close()
    size = os.path.getsize(os.path.join(tmp, "j.hsj"))
    t0 = time.perf_counter(); j2 = Journal(os.path.join(tmp, "j.hsj")); t_open = time.perf_counter() - t0
    t0 = time.perf_counter(); recs = j2.read(); t_read = time.perf_counter() - t0
    j2.close()
    shutil.rmtree(tmp)
    f = lambda xs: f"{statistics.median(xs)*1000:.3f}"
    print(f"{nv:>9} verts: quick blob {f(tq)} ms, append {f(tw)} ms, flush {f(tf)} ms, {statistics.mean(sz)/1024:.0f} KB/step; "
          f"journal {size/2**20:.1f} MB for 200 steps: open {t_open*1000:.1f} ms, read {t_read*1000:.1f} ms")
