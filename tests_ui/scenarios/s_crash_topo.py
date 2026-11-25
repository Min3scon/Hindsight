"""Crash recovery in a real window after a big topology change (R079): s_crash with topo=True."""
import s_crash


def run(d):
    yield from s_crash.run(d, topo=True)
