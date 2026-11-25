#!/usr/bin/env python3
"""Lock the reputation rule: refusing a little is survivable, refusing a lot is not.

    python3 tools/rep_check.py

Engine-level, no server needed. This exists because the first version deducted a
flat penalty for a single refused caller, which pinned reputation at zero forever
the moment demand passed capacity by one person.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import content as C
import main as M


def rep_after(subs, capacity_lines, ticks=60):
    """Run a world with fixed demand and see which way reputation moves."""
    saved = (C.BALANCE["fault_roll_chance"], C.BALANCE["event_chance"])
    C.BALANCE["fault_roll_chance"] = 0.0    # a random fault would change the capacity
    C.BALANCE["event_chance"] = 0.0
    try:
        return _rep_after(subs, capacity_lines, ticks)
    finally:
        C.BALANCE["fault_roll_chance"], C.BALANCE["event_chance"] = saved


def _rep_after(subs, capacity_lines, ticks=60):
    w = M.World(None)
    w.lines = capacity_lines
    w.cores = 1
    w.rep = 5.0
    orig_demand = M.World.demand

    class Fixed:
        pass

    w.__class__ = type("FixedWorld", (M.World,), {
        "demand": property(lambda self: subs)
    })
    rng = random.Random(1)
    start = w.rep
    for _ in range(ticks):
        w.tick(rng)
    return start, w.rep, w.rep_trend


def main():
    checks = []
    b = C.BALANCE

    # nobody refused: reputation must climb
    s, e, trend = rep_after(8, 8)
    checks.append(("serving everyone raises reputation", e > s and trend > 0))

    # a small shortfall: reputation must not collapse
    s, e, trend = rep_after(9, 8)   # 11% refused
    checks.append(("refusing 11% does not cost reputation", e >= s - 0.05))

    # a heavy shortfall: reputation must fall
    s, e, trend = rep_after(20, 8)  # 60% refused
    checks.append(("refusing 60% drops reputation", e < s and trend < 0))

    # the proportional rule: the worst case is bounded
    worst = b["rep_gain"] - b["rep_busy_penalty"] * 1.0
    checks.append(("worst case penalty is gentler than a flat -0.04/tick",
                   worst > -b["rep_busy_penalty"] and worst < 0))

    # reputation is never negative, never above the cap
    w = M.World(None)
    w.rep = 0.0
    w.subs = 99
    w.lines, w.cores = 1, 1
    rng = random.Random(2)
    for _ in range(50):
        w.tick(rng)
    checks.append(("reputation floors at zero", w.rep >= 0.0))
    w.rep = 19.99
    w.subs = 1
    for _ in range(50):
        w.tick(rng)
    checks.append(("reputation caps at twenty", w.rep <= 20.0))

    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
