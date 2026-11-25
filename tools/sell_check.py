#!/usr/bin/env python3
"""SELL, SCRAP and liquidation keep each fault on the unit it belongs to.

    python3 tools/sell_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main  # noqa: E402

checks = []


def plant(dead):
    w = main.World(None)
    w.lines = 8
    w.money = 0.0
    for n in dead:
        w.add_fault("line", n)
    return w


# lines 3 and 8 dead; selling two takes working 6 and 7, old 8 becomes 6
w = plant([3, 8])
ok, msg = main.do_sell(w, ["LINES", "2"])
checks.append(("sell 2 with lines 3 and 8 dead succeeds", ok))
checks.append(("eight lines become six", w.lines == 6))
checks.append(("the dead lines are now 3 and 6",
               sorted(f["n"] for f in w.faults_of("line")) == [3, 6]))
checks.append(("four lines still work", w.working_lines == 4))
checks.append(("paid for two lines", abs(w.money - 2 * 12.0 * 0.4) < 1e-9))

# the last working line cannot be sold, however many dead ones there are
w = plant([1, 2, 3, 4, 5, 6, 7])
ok, msg = main.do_sell(w, ["LINES", "1"])
checks.append(("refuses to sell the last working line", not ok and w.lines == 8))
checks.append(("the refusal points at SCRAP", "SCRAP" in msg))

# nothing dead: behaves as before
w = plant([])
ok, _ = main.do_sell(w, ["LINES", "3"])
checks.append(("plain sale still works", ok and w.lines == 5 and not w.faults))

# scrap closes up the numbering the same way
w = plant([3, 8])
w.money = 100.0
ok, _ = main.do_scrap(w, ["LINE", "3"])
checks.append(("scrap line 3 leaves seven lines", ok and w.lines == 7))
checks.append(("the old line 8 is now line 7", [f["n"] for f in w.faults_of("line")] == [7]))

w = main.World(None)
w.cores = 3
w.add_fault("module", 1)
w.add_fault("module", 3)
ok, _ = main.do_scrap(w, ["MODULE", "1"])
checks.append(("scrap module 1: the old module 3 is now 2",
               ok and w.cores == 2 and [f["n"] for f in w.faults_of("module")] == [2]))

# liquidation sells every line: no dead line survives it
w = plant([3, 20])
w.lines = 24
w.liquidate()
checks.append(("liquidation leaves no line faults", not w.faults_of("line")))

bad = 0
for name, ok in checks:
    bad += not ok
    print("  %s  %s" % ("PASS" if ok else "FAIL", name))
print("%d/%d passed" % (len(checks) - bad, len(checks)))
sys.exit(1 if bad else 0)
