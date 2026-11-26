#!/usr/bin/env python3
"""The plant ages unit by unit, better machines can be bought, and both are kept.

    python3 tools/plant_check.py
"""
import os
import random
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import content as C  # noqa: E402
import main  # noqa: E402

checks = []


def world():
    w = main.World(None)
    w.money = 10000.0
    w.lesson_next = len(C.LESSONS)
    return w


# ---- wear
w = world()
w.sync_units()
checks.append(("a new plant has a record per unit",
               len(w.modules) == w.cores and len(w.line_born) == w.lines))
base = C.BALANCE["fault_roll_chance"]
checks.append(("a new plant breaks at the base rate", abs(w.fault_chance() - base) < 1e-9))
w.ticks += C.WEAR_TICKS
checks.append(("after WEAR_TICKS the odds have doubled", abs(w.fault_chance() - 2 * base) < 1e-9))
w.ticks += 100 * C.WEAR_TICKS
checks.append(("wear stops at the cap", abs(w.fault_chance() - C.WEAR_CAP * base) < 1e-9))

# the worn go first
w = world()
w.cores = 2
w.sync_units()
w.ticks = 10 * C.WEAR_TICKS
w.modules[0]["born"] = w.ticks             # module 1 is new, module 2 is ancient
hits = {1: 0, 2: 0}
rng = random.Random(5)
for _ in range(400):
    w.faults = []
    w.roll_fault(rng)
    for f in w.faults_of("module"):
        hits[f["n"]] += 1
checks.append(("a worn module breaks more often than a new one", hits[2] > 2 * hits[1]))

# ---- machines on sale
w = world()
names = [h["name"] for h, _p in w.on_sale()]
checks.append(("cycle 0 sells the standard and one generation ahead",
               names == [C.HARDWARE[0]["name"], C.HARDWARE[1]["name"]]))
ahead = C.HARDWARE[1]
cash = w.money
ok, msg = main.do_buy(w, [ahead["name"], "1"])
checks.append(("BUY <MODEL> buys one, at the premium",
               ok and w.modules[-1]["model"] == ahead["name"]
               and abs(cash - w.money - ahead["price_core"] * C.AHEAD_PREMIUM) < 1e-6))
checks.append(("its callers count", w.module_room_all == C.HARDWARE[0]["core_users"]
               + ahead["core_users"]))
ok, msg = main.do_buy(w, [C.HARDWARE[3]["name"], "1"])
checks.append(("a model two generations ahead is refused", not ok and "MACHINES" in msg))
ok, _ = main.do_buy(w, ["CORES", "1"])
checks.append(("BUY CORES still buys the standard", ok and w.modules[-1]["model"] == C.HARDWARE[0]["name"]))
out_before = w.outgoings
checks.append(("running costs follow each module's model", abs(
    out_before - (w.site_rent + sum(w.model(m["model"])["core_cost"] for m in w.modules)
                  + w.lines * C.BALANCE["line_cost"] + w.salaries) * w.effects()["cost_mult"]) < 1e-9))

# ---- replace and service
w = world()
w.cores = 2
w.sync_units()
w.ticks = 3 * C.WEAR_TICKS
ok, msg = main.do_replace(w, ["MODULE", "1", ahead["name"]])
checks.append(("REPLACE swaps a module for a new one", ok and w.modules[0]["model"] == ahead["name"]
               and w.modules[0]["born"] == w.ticks))
cap = w.capacity_raw
w.lines = 40
w.sync_units()
cap = w.capacity_raw
ok, msg = main.do_service(w, ["MODULE", "2"])
checks.append(("SERVICE takes the module out of the rack", ok and w.servicing() == 2
               and w.capacity_raw == cap - C.HARDWARE[0]["core_users"]))
ok2, _ = main.do_service(w, ["LINES"])
checks.append(("one job at a time", not ok2))
while w.project:
    w.advance_project()
checks.append(("and puts it back as good as new", w.servicing() is None
               and w.modules[1]["born"] == w.ticks and w.capacity_raw == cap))
w.ticks += C.WEAR_TICKS
ok, _ = main.do_service(w, ["LINES"])
while w.project:
    w.advance_project()
checks.append(("SERVICE LINES renews every line", ok and set(w.line_born) == {w.ticks}))
w.add_fault("module", 1)
ok, msg = main.do_service(w, ["MODULE", "1"])
checks.append(("a dead module is repaired or replaced, not serviced", not ok and "REPAIR" in msg))

# ---- upgrading modules: the most worn lesser ones go first
w = world()
w.cores = 3
w.sync_units()
w.ticks = 6 * C.WEAR_TICKS
w.modules[0]["born"], w.modules[1]["born"], w.modules[2]["born"] = w.ticks, 0, w.ticks // 2
cash = w.money
ok, msg = main.do_upgrade_modules(w, ["MODULES", "1", ahead["name"]])
checks.append(("UPGRADE MODULES swaps the most worn first",
               ok and [m["model"] for m in w.modules] == [C.HARDWARE[0]["name"], ahead["name"],
                                                          C.HARDWARE[0]["name"]]))
checks.append(("at the price less the trade-in", abs(
    cash - w.money - (ahead["price_core"] * C.AHEAD_PREMIUM
                      - C.HARDWARE[0]["price_core"] * C.TRADE_IN_RATE)) < 1e-6))
offer = [u for u in main.shop(w)["upgrade_to"] if u["name"] == ahead["name"]][0]
checks.append(("the order form knows how many are left to upgrade", len(offer["costs"]) == 2))
ok, _ = main.do_upgrade_modules(w, ["MODULES", "9", ahead["name"]])
checks.append(("asking for more than there are upgrades what there is",
               ok and all(m["model"] == ahead["name"] for m in w.modules)))
ok, msg = main.do_upgrade_modules(w, ["MODULES", "1", ahead["name"]])
checks.append(("and then says there is nothing left to upgrade", not ok))

# ---- waiting shows real time as well as bureau time
w = world()
w.tick_seconds = 3
w.start_project("BUILDING X", 480, {}, {})
r = main.r_project(w)
checks.append(("PROJECT gives the real time at today's rate", "8H00 OF BUREAU TIME" in r and "24M00S" in r))

# ---- the right unit leaves
w = world()
w.lines = 8
w.sync_units()
w.line_born = [10, 20, 30, 40, 50, 60, 70, 80]
w.add_fault("line", 3)
main.do_scrap(w, ["LINE", "3"])
checks.append(("scrap removes that line's record", w.line_born == [10, 20, 40, 50, 60, 70, 80]))
main.do_sell(w, ["LINES", "2"])
checks.append(("sell removes the lines it sold", w.line_born == [10, 20, 40, 50, 60]))

# ---- kept across a restart, and old saves gain records
path = os.path.join(tempfile.mkdtemp(), "p.db")
p = subprocess.Popen([sys.executable, os.path.join(HERE, "main.py"), "--port", "0",
                      "--db", path, "--tick", "60"],
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
p.stdout.readline(); p.terminate(); p.wait(timeout=5)


def load():
    w = main.World(sqlite3.connect(path, isolation_level=None))
    w.load(); w.load_faults(); w.load_upgrades(); w.load_contracts()
    w.load_pending(); w.load_units()
    return w


w = load()
w.money = 10000.0
w.ticks = 500
main.do_buy(w, [ahead["name"], "1"])
w.modules[0]["born"] = 123
w.units_dirty = True
w.save()
w2 = load()
checks.append(("models and ages survive a restart",
               [m["model"] for m in w2.modules] == [m["model"] for m in w.modules]
               and w2.modules[0]["born"] == 123))
w2.db.execute("DELETE FROM unit")
w3 = load()
checks.append(("a save from before ages gets a record per unit",
               len(w3.modules) == w3.cores and len(w3.line_born) == w3.lines))

# ---- a sale brings the next generation as standard
w = world()
w.won_tick, w.earned = 5, 1e6
main.do_prestige(w)
checks.append(("after a sale the plant is the new standard",
               all(m["model"] == C.HARDWARE[1]["name"] for m in w.modules)))
checks.append(("and the generation after it is on sale",
               [h["name"] for h, _ in w.on_sale()] == [C.HARDWARE[1]["name"], C.HARDWARE[2]["name"]]))

bad = 0
for name, ok in checks:
    bad += not ok
    print("  %s  %s" % ("PASS" if ok else "FAIL", name))
print("%d/%d passed" % (len(checks) - bad, len(checks)))
sys.exit(1 if bad else 0)
