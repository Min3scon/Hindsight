"""Tags that tie a test to COVERAGE.md rows and tier-gate items (see tools/gate_spec.py).

    from tests.gate import covers

    @covers("obj.parenting", "EXACT", "OPS")
    @covers(["obj.transforms", "obj.parenting"], "PROP")
    def test_something(): ...

tools/make_evidence.py reads these decorators from the source (it never imports the tests), so a
tag means nothing unless the test it sits on passed in a recorded run. The decorator itself only
stores the tags on the function.
"""


def covers(rows, *items):
    rows = [rows] if isinstance(rows, str) else list(rows)

    def deco(fn):
        tags = list(getattr(fn, "_covers", ()))
        tags += [(r, i) for r in rows for i in items]
        fn._covers = tags
        return fn
    return deco
