#!/usr/bin/env python3
"""The end goal: you cannot buy it early, buying it ends the game, and it sticks."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import content as C
import main as M


def main():
    checks = []
    price = C.BALANCE["building_price"]

    w = M.World(None)
    w.money = price - 1
    ok, msg = M.do_buy_building(w)
    checks.append(("you cannot buy the building short of the price", not ok and "NOT YET" in msg))
    checks.append(("the refusal points at GOAL", "GOAL" in msg))
    checks.append(("a failed purchase does not mark the game won", w.won_tick == 0))

    w = M.World(None)
    w.money = price + 500
    w.ticks = 4321
    ok, msg = M.do_buy_building(w)
    checks.append(("buying it succeeds with the money", ok and w.won_tick == 4321))
    checks.append(("the price is deducted", abs(w.money - 500) < 1e-9))
    checks.append(("the ending is delivered", any("DEEDS ARE SIGNED" in m for m in w.queue)))
    checks.append(("the win is recorded on the world", w.won_tick == 4321))

    ok2, msg2 = M.do_buy_building(w)
    checks.append(("buying it twice is refused", not ok2 and "ALREADY" in msg2))

    shown = M.r_goal(w)
    checks.append(("GOAL reports the purchase after the win", "IS YOURS" in shown))

    w2 = M.World(None)
    w2.money = 1000
    shown2 = M.r_goal(w2)
    checks.append(("GOAL shows the price and the progress",
                   ("%.0f" % price) in shown2 and "CASH" in shown2))

    # the goal must be reachable at all: measure when a competent player gets there
    best = None
    for seed in (3, 7, 11):
        w3 = M.World(None)
        rng = random.Random(seed)
        b = C.BALANCE
        for t in range(40000):
            if t % 30 == 0:
                if w3.queued and w3.money > b["price_slot"] * 1.2:
                    M.do_buy(w3, ["SLOTS", "1"])
                elif w3.lines < w3.cores * b["core_users"] and w3.money > b["price_line"] * 1.2:
                    M.do_buy(w3, ["LINES", "1"])
                elif w3.money > b["price_core"] * 1.2:
                    M.do_buy(w3, ["CORES", "1"])
                if w3.faults and w3.money > 80:
                    M.do_repair(w3, [])
                for c in w3.offers():
                    if w3.capacity >= c["want"] and not w3.active():
                        M.do_accept(w3, [str(c["id"])])
            prev = w3.money
            w3.tick(rng)
            if prev < price <= w3.money:
                best = w3.ticks if best is None else min(best, w3.ticks)
                break
    checks.append(("a competent operator reaches the price inside 40k ticks", best is not None))
    if best:
        print(f"  [measured] first reached the price at tick {best} "
              f"({best/1440:.1f} simulated days)")

    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
