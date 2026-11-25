#!/usr/bin/env python3
"""Contracts pay when met, cost when missed, and going broke actually bites.

Engine-level, no server. Money is isolated by zeroing revenue and costs, so every
change to cash in these tests comes from a contract or a liquidation.

    python3 tools/contract_check.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import content as C
import main as M


class Neutered(M.World):
    """A world with no income and no outgoings, so cash only moves by contract."""

    @property
    def revenue(self):
        return 0.0

    @property
    def outgoings(self):
        return 0.0


def make_world(subs=20, lines=20, cores=3):
    w = Neutered(None)
    w.lines, w.cores = lines, cores
    w.money = 1000.0
    w.rep = 5.0
    w.overdraft_ticks = 0
    return w


def with_fixed_demand(w, subs):
    cls = type("Fixed", (type(w),), {"demand": property(lambda self: subs)})
    w.__class__ = cls
    return w


def add_contract(w, want, duration, fee=50.0, penalty=30.0):
    c = {"id": w.next_contract_id, "company": "TEST AND CO", "work": "TEST WORK",
         "want": want, "duration": duration, "ticks_left": duration,
         "fee": fee, "penalty": penalty, "state": "active", "breached": 0}
    w.next_contract_id += 1
    w.contracts.append(c)
    return c


def main():
    checks = []
    saved = (C.BALANCE["fault_roll_chance"], C.BALANCE["event_chance"],
             C.BALANCE["contract_roll_chance"])
    C.BALANCE["fault_roll_chance"] = 0.0
    C.BALANCE["event_chance"] = 0.0
    C.BALANCE["contract_roll_chance"] = 0.0     # offers come only from the tests
    rng = random.Random(5)

    # 1. met contract pays the fee
    w = make_world()
    with_fixed_demand(w, 20)                    # capacity is 20, want is 15
    add_contract(w, want=15, duration=5, fee=50.0)
    before = w.money
    for _ in range(6):
        w.tick(rng)
    checks.append(("a met contract pays its fee", abs((w.money - before) - 50.0) < 1e-6))
    checks.append(("the fee is announced", any("FEE" in m and "PAID" in m for m in w.queue)))
    checks.append(("the contract leaves the book", not w.active()))

    # 2. a contract you cannot staff costs the penalty
    w = make_world()
    with_fixed_demand(w, 20)
    add_contract(w, want=30, duration=5, fee=50.0, penalty=30.0)   # want > capacity
    before = w.money
    for _ in range(6):
        w.tick(rng)
    checks.append(("a missed contract charges the penalty", abs((w.money - before) + 30.0) < 1e-6))
    checks.append(("the breach names the shortfall",
                   any("SHORTED THEM" in m or "SHORT" in m for m in w.queue)))

    # 3. a short blip inside tolerance still pays
    w = make_world()
    with_fixed_demand(w, 20)
    add_contract(w, want=25, duration=20, fee=50.0)                # want > capacity: always short
    before = w.money
    for _ in range(21):
        w.tick(rng)
    checks.append(("missing every tick breaches", abs((w.money - before) + 30.0) < 1e-6))

    # 4. liquidation: overdrawn past the grace period resets you, sorted out
    w = make_world(lines=40, cores=2)
    with_fixed_demand(w, 10)
    w.money = -500.0
    w.rep = 9.0
    w.extra_slots = 5
    C.BALANCE["overdraft_grace"] = 5
    for _ in range(8):
        w.tick(rng)
    checks.append(("going bust zeroes the debt, never leaves you negative", w.money >= 0.0))
    checks.append(("going bust cuts you back to the basic line set",
                   w.lines == C.BALANCE["start_lines"]))
    # reputation is set to 0 by the liquidation and may already be
    # recovering on the ticks that follow, so the test checks the collapse
    checks.append(("going bust clears reputation", w.rep < 1.0))
    checks.append(("going bust drops your contracts", not w.active()))
    checks.append(("going bust says so", any("DISCONNECTED" in m for m in w.queue)))

    # 5. an overdrawn world that recovers never liquidates
    w = make_world()
    with_fixed_demand(w, 10)
    w.money = -50.0
    C.BALANCE["overdraft_grace"] = 1000
    for _ in range(50):
        w.tick(rng)
    checks.append(("an overdraft being worked off does not liquidate", w.lines == 20))

    # 6. several jobs at once, up to the cap
    w = make_world()
    with_fixed_demand(w, 20)
    for _ in range(C.CONTRACT_MAX_ACTIVE):
        add_contract(w, want=5, duration=50)
    checks.append(("three jobs can run at once",
                   len(w.active()) == C.CONTRACT_MAX_ACTIVE))

    # 7. calling a job in early pays a share of the fee, plus the bonus
    w = make_world()
    with_fixed_demand(w, 20)
    c = add_contract(w, want=5, duration=100, fee=100.0)
    for _ in range(50):
        w.tick(rng)
    before = w.money
    ok, msg = M.do_close(w, [str(c["id"])])
    paid = w.money - before
    checks.append(("closing early is accepted", ok))
    checks.append(("closing early pays a share of the fee plus the bonus",
                   abs(paid - 100.0 * 0.5 * C.CONTRACT_EARLY_BONUS) < 2.0))
    checks.append(("a closed job leaves the book", not w.active()))

    # 8. closing a job you have already been short on costs the penalty
    w = make_world()
    with_fixed_demand(w, 20)
    c = add_contract(w, want=99, duration=100, fee=100.0, penalty=40.0)
    for _ in range(40):
        w.tick(rng)
    before = w.money
    ok, msg = M.do_close(w, [str(c["id"])])
    checks.append(("closing a failing job charges the penalty",
                   w.money - before < -30.0))

    C.BALANCE["fault_roll_chance"], C.BALANCE["event_chance"], \
        C.BALANCE["contract_roll_chance"] = saved
    C.BALANCE["overdraft_grace"] = 360

    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
