#!/usr/bin/env python3
"""Do contracts actually pay? Count the fees and the penalties.

A game mechanic that feels good and loses money is a trap dressed as content, so
this runs a competent operator who takes every offer it can staff and prints the
ledger. Fees must beat penalties, with a margin worth the risk.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import content as C
import main as M

TICKS = 12000
SEEDS = 4


def run(seed, repair=True, grow=True):
    """grow=False models the player who takes a job and then stops paying attention."""
    w = M.World(None)
    rng = random.Random(seed)
    b = C.BALANCE
    for t in range(TICKS):
        if t % 30 == 0:
            if grow:
                if w.lines < w.cores * b["core_users"] and w.money > b["price_line"] * 1.2:
                    M.do_buy(w, ["LINES", "1"])
                elif w.money > b["price_core"] * 1.2:
                    M.do_buy(w, ["CORES", "1"])
            for c in w.offers():
                if w.capacity >= c["want"]:
                    M.do_accept(w, [str(c["id"])])
        if repair and w.faults and w.money > sum(M.repair_cost(f["kind"]) for f in w.faults) * 1.2:
            M.do_repair(w, [])
        w.tick(rng)
    fees = penalties = 0.0
    met = failed = 0
    for msg in w.queue:
        if "FEE" in msg and "PAID" in msg:
            fees += float(msg.split("FEE ")[1].split()[0]); met += 1
        if "PENALTY" in msg and "CHARGED" in msg:
            penalties += float(msg.split("PENALTY ")[1].split()[0]); failed += 1
    return fees, penalties, met, failed


def main():
    fees = penalties = 0.0
    met = failed = 0
    for s in range(SEEDS):
        f, p, m, x = run(s * 13 + 2)
        fees += f; penalties += p; met += m; failed += x
    net = fees - penalties
    nf, np_, nmet, nfailed = 0.0, 0.0, 0, 0
    for s in range(SEEDS):
        f, p, m, x = run(s * 13 + 2, repair=False, grow=False)
        nf += f; np_ += p; nmet += m; nfailed += x
    print("operator who keeps the machine healthy:")
    print(f"  met {met}, breached {failed}   fees {fees:.0f}  penalties {penalties:.0f}  net {net:+.0f}")
    print("operator who takes work, stops growing and never repairs:")
    print(f"  met {nmet}, breached {nfailed}   fees {nf:.0f}  penalties {np_:.0f}  net {nf-np_:+.0f}")
    checks = [
        ("contracts pay for a competent operator", net > 0),
        ("neglect turns contracts into losses", (nf - np_) < net),
        ("a negligent operator does breach them", nfailed > 0),
        ("most accepted work is honoured", met > failed),
    ]
    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
