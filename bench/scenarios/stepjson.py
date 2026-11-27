"""The JSON of steps (#146): ms per pass over every step of the corpus (tests/corpus.py: the registries of
corpus.FILES and the 6,000 steps of corpus.make("big_format5")), from hindsight/core/step.py's names.

    <bpy python> bench/scenarios/stepjson.py CODE_ROOT [--quick] [--only a,b] [--reps N]

Scenarios: stepjson_write_corpus (_dumps of each step's extra, records_to_bytes of its records),
stepjson_read_corpus (_extra and records_from_bytes of those bytes), stepjson_header_corpus (read_header of
each step). The step count is in the extra field steps.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as K  # noqa: E402

opts = K.Opts()
K.use_root(opts.root)
from tests import corpus as C  # noqa: E402
from hindsight.core import persist, step  # noqa: E402

K.import_checked(opts.root)


def corpus_blobs():
    out = []
    for path, kind, _f in C.FILES:
        if kind == "registry":
            reg = persist.registry_from_bytes(C.payload(path, kind))
            out += [e.to_bytes() for h in reg.histories.values() for e in h.entries]
    big = C.make("big_format5")
    reg = persist.registry_from_format5(big["index"], big["index_copy"], lambda uid, i: big.get(uid, {}).get(str(i)))
    return out + [e.to_bytes() for h in reg.histories.values() for e in h.entries]


BLOBS = corpus_blobs()
STEPS = [step.step_from_bytes(b) for b in BLOBS]
EXTRAS = [{k: v for k, v in (s.extra or {}).items() if k != step.SNAPSHOTS_KEY} for s in STEPS]
RECS = [s.records for s in STEPS]
EXTRA_B = [step._dumps(e) if e else b"" for e in EXTRAS]
RECS_B = [step.records_to_bytes(r) if r else None for r in RECS]
REPS = opts.n(30, 5)


def write():
    d, rb = step._dumps, step.records_to_bytes
    for e, r in zip(EXTRAS, RECS):
        if e:
            d(e)
        if r:
            rb(r)


def read():
    x, rf = step._extra, step.records_from_bytes
    for e, r in zip(EXTRA_B, RECS_B):
        x(e)
        if r is not None:
            rf(r)


def header():
    h = step.read_header
    for b in BLOBS:
        h(b)


results = {}
for name, fn in (("stepjson_write_corpus", write), ("stepjson_read_corpus", read), ("stepjson_header_corpus", header)):
    if opts.want(name):
        K.attempt(results, name, lambda fn=fn: K.stats(K.time_it(fn, REPS), steps=len(BLOBS)))
K.emit(results)
