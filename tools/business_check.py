#!/usr/bin/env python3
"""Price, customers, staff, engineering works, the rival and the bank.

    python3 tools/business_check.py
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


def check(name, ok):
    checks.append((name, bool(ok)))


def world():
    w = main.World(None)
    w.money, w.cores, w.lines = 5000.0, 4, 32
    w.lesson_next = len(C.LESSONS)
    w.rival_next = 10 ** 9                 # no rival unless a check asks for one
    w.sync_units()
    return w


# ---- 3. price
w = world()
w.ticks, w.rep = 3000, 8.0
d0, rate0 = w.demand, w.effects()["rate_mult"]
ok, _ = main.do_price(w, ["150"])
check("PRICE sets the rate", ok and w.price == 150 and abs(w.effects()["rate_mult"] - 1.5 * rate0) < 1e-9)
check("a dearer price loses callers", w.demand < d0)
ok, _ = main.do_price(w, ["300"])
check("the price has limits", not ok and w.price == 150)
rep0 = w.rep
w.tick(random.Random(1))
check("above fair, reputation slips", w.rep < rep0 + C.BALANCE["rep_gain"] - 1e-9)
w.price = 100
check("at list price the price adds nothing", w.price_effects() == {})

# ---- 4. customers who remember
w = world()
w.ticks = 1000
name = C.CONTRACT_COMPANIES[0][0]
rng = random.Random(2)
w.offer_contract(rng, company=C.CONTRACT_COMPANIES[0], force=True)
plain = w.offers()[-1]
w.drop_contract(plain["id"])
for _ in range(2):
    w.goodwill_change(name, +1, "met")
w.offer_contract(random.Random(2), company=C.CONTRACT_COMPANIES[0], force=True)
warm = w.offers()[-1]
check("a customer served well comes back bigger, and pays more",
      warm["want"] >= plain["want"] and warm["fee"] > plain["fee"])
rep0 = w.rep
for _ in range(5):
    w.goodwill_change(name, -1, "short")
check("an unhappy customer talks", w.rep < rep0)
check("a blacklisting customer is told", w.account(name)["goodwill"] <= C.BLACKLIST)
w.contracts = []
for _ in range(50):
    w.offer_contract(rng, force=True)
check("and never calls again", all(c["company"] != name for c in w.contracts))
w = world()
main.World.decide  # the student's firm comes back two days after HIRE
w.incident = {"key": "student", "left": 10}
w.money = 500
w.decide("HIRE")
check("a decision can promise a later customer", w.later and w.later[0]["company"] == "POLYTECHNIC SOFTWARE")
w.ticks = w.later[0]["at"]
w.keep_promises()
check("and the customer arrives with work", any(c["company"] == "POLYTECHNIC SOFTWARE"
                                                for c in w.offers()))
check("already on good terms", w.account("POLYTECHNIC SOFTWARE")["goodwill"] >= 2)

# ---- 5. staff are people
w = world()
main.do_hire(w, ["ENGINEER"])
eff1 = w.effects()["capacity_mult"]
w.staff["ENGINEER"]["xp"] = C.STAFF_LEVEL_TICKS[1] - 1
w.tend_staff(random.Random(3))
check("experience brings a level", w.staff["ENGINEER"]["level"] == 2)
check("a level makes the role count for more", w.effects()["capacity_mult"] > eff1)
check("and a request for a raise", w.incident and w.incident["key"] == "raise")
sal = w.salaries
ok, msg = w.decide("PAY")
check("paying raises the salary", ok and w.salaries > sal and w.staff["ENGINEER"]["morale"] == 1)
w.staff["ENGINEER"]["morale"] = -1
gone = False
rng = random.Random(4)
for t in range(1, 20000):
    w.ticks = t * C.STAFF_SICK_EVERY
    w.tend_staff(rng)
    if "ENGINEER" not in w.staff:
        gone = True
        break
check("a sulking person leaves in the end", gone)
w = world()
main.do_hire(w, ["CLERK"])
sick = False
rng = random.Random(5)
for t in range(1, 5000):
    w.ticks = t * C.STAFF_SICK_EVERY
    w.tend_staff(rng)
    if w.staff["CLERK"].get("away_why") == "SICK":
        sick = True
        check("someone off sick does not do the job", w.staff_effects("CLERK") == {})
        break
check("people fall ill", sick)
w = world()
main.do_hire(w, ["OPERATOR"])
ok, _ = main.do_train(w, ["OPERATOR"])
check("TRAIN sends them away", ok and w.staff_effects("OPERATOR") == {})
for _ in range(C.TRAIN_TICKS + 1):
    w.ticks += 1
    w.tend_staff(random.Random(6))
check("and they come back a level up", w.staff["OPERATOR"]["level"] == 2)

# ---- 6. engineering works
w = world()
ok, msg = main.do_build(w, ["REMOTE", "DIAGNOSTICS"])
check("a work that needs another waits for it", not ok and "LINE SCANNER" in msg)
ok, _ = main.do_build(w, ["LINE", "SCANNER"])
check("BUILD starts the engineers", ok and w.project)
fm = w.effects()["fault_mult"]
while w.project:
    w.advance_project()
check("once built, it works", "LINE SCANNER" in w.works and w.effects()["fault_mult"] < fm)
ok, _ = main.do_build(w, ["LINE", "SCANNER"])
check("and is not built twice", not ok)

# ---- 7. the rival
w = world()
w.rival_next = 50
w.ticks, w.rep = 49, 2.0
rng = random.Random(7)
for _ in range(3):
    w.tick(rng)
check("a rival opens when its time comes", w.rival is not None)
take = w.rival_take()
w.price = 150
check("it takes more of the market when we are dearer", w.rival_take() > take)
w.price = 100
d0 = w.demand
w.rival["strength"] = 1.0
check("it costs us callers", w.demand < d0)
moves = set()
rng = random.Random(8)
w.contracts, w.staff = [], {}
for k in range(1, 200):
    w.offer_contract(rng, force=True)
    before = len(w.offers())
    w.ticks = w.rival["born"] + k * C.RIVAL_EVERY
    w.tend_rival(rng)
    if len(w.offers()) < before:
        moves.add("undercut")
check("it undercuts our open offers", "undercut" in moves)
w.rep = 20.0
w.rival["strength"] = 0.05
rng = random.Random(9)
for _ in range(1000):
    w.ticks += 1
    w.tend_rival(rng)
    if w.rival is None:
        break
check("a well-thought-of bureau outlasts it", w.rival is None)

# ---- 8. the bank
w = world()
w.money, w.rep = 0.0, 5.0
limit = w.loan_limit()
ok, _ = main.do_borrow(w, [str(limit + 1)])
check("the manager has a limit", not ok)
ok, _ = main.do_borrow(w, ["100"])
check("BORROW pays out", ok and w.money == 100 and w.loan["owed"] == 100)
loan, w.loan = w.loan, None
base = w.outgoings
w.loan = loan
check("interest shows in outgoings", w.interest > 0 and abs(w.outgoings - base - w.interest) < 1e-9)
ok, _ = main.do_repay(w, ["40"])
check("REPAY pays some back", ok and abs(w.loan["owed"] - 60) < 1e-9)
w.money = 10.0
w.ticks = w.loan["due"]
w.tend_loan()
check("when it falls due the bank takes it, overdrawn or not", w.loan is None and w.money < 0)
w = world()
main.do_borrow(w, ["100"])
w.money = -1.0
w.liquidate()
check("a liquidation writes the loan off", w.loan is None)
ok, _ = main.do_borrow(w, ["10"])
check("and the bank will not lend again for a while", not ok)

# ---- everything is kept across a restart
path = os.path.join(tempfile.mkdtemp(), "b.db")
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
w.money, w.rep = 5000.0, 5.0
main.do_price(w, ["120"])
w.goodwill_change(name, 2)
main.do_hire(w, ["SALES"])
w.staff["SALES"]["xp"], w.staff["SALES"]["pay"] = 999, 1.3
w.works.add("JOB SCHEDULER"); w.put("work:JOB SCHEDULER", 1)
w.rival = {"name": "MERCIA COMPUTING", "where": "STATION ROAD", "strength": 0.4,
           "price": 80, "born": 0}
main.do_borrow(w, ["50"])
w.save()
w2 = load()
check("price, customers, staff, works, rival and loan survive a restart",
      w2.price == 120 and w2.account(name)["goodwill"] == 2
      and w2.staff["SALES"]["xp"] == 999 and w2.staff["SALES"]["pay"] == 1.3
      and "JOB SCHEDULER" in w2.works and w2.rival["name"] == "MERCIA COMPUTING"
      and abs(w2.loan["owed"] - 50) < 1e-9)
w2.db.execute("DELETE FROM pending WHERE kind='rival'")
w3 = load()
check("an old save gets two days before a rival opens",
      w3.rival is None and w3.rival_next == w3.ticks + C.RIVAL_ARRIVES)

bad = 0
for n, ok in checks:
    bad += not ok
    print("  %s  %s" % ("PASS" if ok else "FAIL", n))
print("%d/%d passed" % (len(checks) - bad, len(checks)))
sys.exit(1 if bad else 0)
