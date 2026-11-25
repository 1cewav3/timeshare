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

    M.World.roll_fault = spy
    for s in range(SEEDS):
        w = M.World(None)
        rng = random.Random(s + 11)
        for _ in range(TICKS):
            w.tick(rng)
    M.World.roll_fault = orig
    breaks = calls["n"]

    expected = SEEDS * (TICKS / C.BALANCE["fault_roll_every"]) * C.BALANCE["fault_roll_chance"]
    tol = 0.4 * expected          # wide: 30 rolls is a small sample
    ok = abs(breaks - expected) <= tol
    print(f"rolls: {SEEDS * TICKS // C.BALANCE['fault_roll_every']}  "
          f"breaks: {breaks}  expected: {expected:.0f} +/- {tol:.0f}")
    print(("  PASS  " if ok else "  FAIL  ") + ("observed break rate matches %.0f%% per %d ticks"
                    % (100 * C.BALANCE["fault_roll_chance"], C.BALANCE["fault_roll_every"])))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
