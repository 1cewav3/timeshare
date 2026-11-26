#!/usr/bin/env python3
"""Measure the fault rate instead of trusting the constant.

Runs whole worlds and counts how many times something actually broke. Reads the advertised rule
from content.py rather than repeating it here.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import content as C
import main as M

TICKS = 5000
SEEDS = 6


def main():
    breaks = 0
    orig = M.World.roll_fault
    calls = {"n": 0}

    def spy(self, rng):
        calls["n"] += 1
        return orig(self, rng)

    # the odds are not flat any more: wear raises them as the plant ages. The
    # engine asks fault_chance() once per roll, so the expected number of
    # breaks is the sum of the odds it was offered.
    offered = {"sum": 0.0, "first": None}
    orig_chance = M.World.fault_chance

    def spy_chance(self):
        p = orig_chance(self)
        offered["sum"] += min(1.0, p)
        if offered["first"] is None:
            offered["first"] = p
        return p

    M.World.roll_fault = spy
    M.World.fault_chance = spy_chance
    for s in range(SEEDS):
        w = M.World(None)
        rng = random.Random(s + 11)
        for _ in range(TICKS):
            w.tick(rng)
    M.World.roll_fault = orig
    M.World.fault_chance = orig_chance
    breaks = calls["n"]

    expected = offered["sum"]
    tol = 0.4 * expected          # wide: 30 rolls is a small sample
    ok = abs(breaks - expected) <= tol
    base_ok = abs(offered["first"] - C.BALANCE["fault_roll_chance"]) < 0.02
    print(f"rolls: {SEEDS * TICKS // C.BALANCE['fault_roll_every']}  "
          f"breaks: {breaks}  expected: {expected:.0f} +/- {tol:.0f}")
    print(("  PASS  " if base_ok else "  FAIL  ") + "a new plant breaks at the advertised %.0f%% per %d ticks"
          % (100 * C.BALANCE["fault_roll_chance"], C.BALANCE["fault_roll_every"]))
    print(("  PASS  " if ok else "  FAIL  ") + "observed breaks match the odds offered as the plant wears")
    return 0 if ok and base_ok else 1


if __name__ == "__main__":
    sys.exit(main())
