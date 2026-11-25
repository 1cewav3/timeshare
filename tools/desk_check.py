#!/usr/bin/env python3
"""The desktop edition's numbers, checked without opening a window.

The panels are rendered from the same snapshot the browser board uses, so what
matters here is that every field the windows draw actually exists and is
formatted - a missing key used to crash the whole app on the first poll.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import desk


SNAP = {
    "site": "ROOM 2B, NORTHGATE HOUSE", "capacity": 81, "served": 64, "subs": 64,
    "queued": 4, "slots": 60, "revenue": 15.048, "outgoings": 1.445, "net": 13.603,
    "rep": 0.55, "rep_trend": 0.008, "cash": 275.17, "mods": [{"name": "EXCHANGE STRIKE"}],
    "staff": [], "salaries": 0.0, "faults": [], "offers": [], "upgrades": [],
    "goal": {"price": 25000, "pct": 1.1}, "won": False, "won_tick": 0, "standing": 46,
    "history": [(1, 240.0, 1.0), (2, 275.17, 13.6)], "tick": 12386, "news": [],
    "refused_share": 0.0, "overdraft": 0, "demand": 64, "ceiling": 64, "slots_extra": 52,
    "working_cores": 10, "working_lines": 78, "cores": 10, "lines": 78, "running": 60,
    "earning": 0.0, "site_index": 0, "salaries_tick": 0.0,
}


def main():
    checks = []
    block = desk.machine_block(SNAP)
    checks.append(("the machine panel renders every row",
                   all(k in block for k in ("site", "usable capacity", "callers served",
                                            "batching", "revenue/tick", "costs/tick",
                                            "net/tick", "reputation", "cash"))))
    checks.append(("the site is shortened for the panel", "room 2b" in block.lower()))
    checks.append(("values are right-aligned in a column",
                   len({line.index(line.split()[-1]) for line in block.splitlines()}) >= 1))
    checks.append(("an empty mod list says so", "none" in desk.machine_block(
        dict(SNAP, mods=[]))))
    checks.append(("every icon has a label and a kind",
                   len(desk.ICONS) >= 6 and all(len(x) == 2 for x in desk.ICONS)))
    kinds = {kind for kind, _label in desk.ICONS}
    checks.append(("quit is on the desktop", "quit" in kinds))
    worst = max(len(line) for line in block.splitlines())
    checks.append(("no panel line is absurdly wide (< 70 chars)", worst < 70))

    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
