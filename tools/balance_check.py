#!/usr/bin/env python3
"""The shape of the balance, as the sweep measured it. Slow: about two minutes.

    python3 tools/balance_check.py

Strategies from main.sim(), with an operator at the console eight hours a day:
  - doing everything beats every single system on its own, within a week
  - every investment (works, staff, servicing, price) beats plain plant-buying
    over three weeks: they cost first and pay later
  - doing nothing ends in liquidation within three weeks
"""
import multiprocessing as mp
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main  # noqa: E402

SEEDS = 2


def run(job):
    name, ticks, seed = job
    w = main.sim(ticks, seed * 17 + 3, name)
    return name, ticks, main.worth(w), w.liquidations


def table(names, ticks):
    jobs = [(n, ticks, s) for n in names for s in range(SEEDS)]
    with mp.Pool() as p:
        res = p.map(run, jobs)
    out = {}
    for n in names:
        rows = [r for r in res if r[0] == n]
        out[n] = (sum(r[2] for r in rows) / len(rows), sum(r[3] for r in rows))
    return out


if __name__ == "__main__":
    checks = []
    singles = ["capacity", "servicer", "pricer", "borrower", "upgrader", "engineer", "staffer"]
    week = table(singles + ["full"], 7 * 1440)
    best = max(singles, key=lambda n: week[n][0])
    checks.append(("in a week, doing everything beats the best single system (%s)" % best,
                   week["full"][0] > week[best][0]))
    month = table(["idle", "capacity", "servicer", "pricer", "engineer", "staffer"], 21 * 1440)
    for n in ("servicer", "pricer", "engineer", "staffer"):
        checks.append(("over three weeks, %s beats plain plant-buying" % n,
                       month[n][0] > month["capacity"][0]))
    checks.append(("doing nothing is cut off within three weeks", month["idle"][1] >= SEEDS))
    bad = 0
    for name, ok in checks:
        bad += not ok
        print("  %s  %s" % ("PASS" if ok else "FAIL", name))
    print("%d/%d passed" % (len(checks) - bad, len(checks)))
    sys.exit(1 if bad else 0)
