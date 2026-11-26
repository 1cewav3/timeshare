#!/usr/bin/env python3
"""The first offers teach the game, and pay whether or not you follow them.

    python3 tools/lesson_check.py

A fresh world is walked through all four lessons twice: once by a player who
does each thing, once by one who turns everything down. Both must end with the
same grants paid. Old saves and sold-up worlds get no lessons.
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
TMP = tempfile.mkdtemp()
COUNT = [0]


def fresh():
    COUNT[0] += 1
    path = os.path.join(TMP, "l%d.db" % COUNT[0])
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "main.py"), "--port", "0",
                          "--db", path, "--tick", "60"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    p.stdout.readline()
    p.terminate()
    p.wait(timeout=5)
    return reload(path)


def reload(path):
    db = sqlite3.connect(path, isolation_level=None)
    w = main.World(db)
    w.load(); w.load_faults(); w.load_upgrades(); w.load_contracts()
    w.money = 1000.0                 # isolate: lessons should not depend on luck
    return w


def lesson(w):
    return next((c for c in w.contracts if c.get("lesson")), None)


def run(w, rng, n=1):
    for _ in range(n):
        w.tick(rng)


GRANTS = sum(L["fee"] for L in C.LESSONS)

# ---- the player who does every lesson
w = fresh()
rng = random.Random(4)
checks.append(("a fresh world starts its lessons", w.lesson_next == 0))
run(w, rng)
c = lesson(w)
checks.append(("lesson 1 is offered on the first tick", c and c["lesson"] == "accept"))
ok, _ = main.do_accept(w, [str(c["id"])])
checks.append(("lesson 1 can be accepted", ok))
paid0 = w.money
run(w, rng, c["duration"] + 1)
c = lesson(w)
checks.append(("lesson 2 follows lesson 1", c and c["lesson"] == "lines"))
checks.append(("lesson 2 wants more than we carry", c["want"] > w.capacity))
ok, msg = main.do_accept(w, [str(c["id"])])
checks.append(("ACCEPT's refusal points at the buy sheet", not ok and "TYPE BUY" in msg))
main.do_buy(w, ["CORES", "1"])
main.do_buy(w, ["LINES", str(c["want"] - w.capacity)])
ok, _ = main.do_accept(w, [str(c["id"])])
checks.append(("after buying, lesson 2 can be accepted", ok))
run(w, rng, c["duration"] + 1)
c = lesson(w)
checks.append(("lesson 3 breaks a real line", c and c["lesson"].startswith("repair:")
               and any(f["kind"] == "line" for f in w.faults)))
n = c["lesson"].split(":")[1]
ok, msg = main.do_accept(w, [str(c["id"])])
checks.append(("ACCEPT on the repair lesson says what to do", not ok and "REPAIR LINE " + n in msg))
main.do_repair(w, ["LINE", n])
before = w.money
run(w, rng)
checks.append(("the repair lesson pays once the line is back",
               w.money - before > C.LESSONS[2]["fee"] - 1))
run(w, rng)
c = lesson(w)
checks.append(("lesson 4 is the CLOSE lesson", c and c["lesson"] == "close"))
main.do_accept(w, [str(c["id"])])
run(w, rng, 5)
before = w.money
ok, msg = main.do_close(w, [str(c["id"])])
checks.append(("CLOSE pays the grant in full", ok and abs(w.money - before - c["fee"]) < 1e-6))
run(w, rng, 400)
checks.append(("no more lessons after four", w.lesson_next == len(C.LESSONS) and not lesson(w)))
path = w.db.execute("PRAGMA database_list").fetchone()[2]
checks.append(("the lesson count survives a restart", reload(path).lesson_next == len(C.LESSONS)))

# ---- the player who turns every lesson down gets the same money
w = fresh()
rng = random.Random(4)
paid = 0.0
for i in range(len(C.LESSONS)):
    run(w, rng)
    c = lesson(w)
    if c is None:
        break
    before = w.money
    ok, msg = main.do_drop(w, [str(c["id"])])
    paid += w.money - before
checks.append(("turning every lesson down still pays every grant", abs(paid - GRANTS) < 1e-6))
checks.append(("a turned-down repair lesson leaves the dead line to deal with",
               any(f["kind"] == "line" for f in w.faults)))

# ---- a lesson job that goes badly still pays, and charges nothing
w = fresh()
rng = random.Random(9)
run(w, rng)
c = lesson(w)
main.do_accept(w, [str(c["id"])])
w.lines = 1                          # cannot serve 3 callers on one line
run(w, rng, c["duration"] + 1)
kinds = [k for (k,) in w.db.execute("SELECT kind FROM log WHERE kind IN ('lesson', 'breach')")]
paid_line = w.db.execute("SELECT COUNT(*) FROM log WHERE kind='lesson' AND line LIKE ?",
                         ("%GRANT OF%",)).fetchone()[0]
checks.append(("a botched lesson still pays its grant", paid_line == 1))
checks.append(("and charges no penalty", "breach" not in kinds))

# ---- no lessons for an old save or after a sale
w = fresh()
w.db.execute("DELETE FROM upgrade WHERE name='lesson'")
old = main.World(w.db); old.load(); old.load_upgrades(); old.load_contracts()
run(old, random.Random(1), 5)
checks.append(("an old save gets no lessons", not lesson(old)))
w = fresh()
w.won_tick, w.earned = 5, 1e6
main.do_prestige(w)
run(w, random.Random(1), 5)
checks.append(("a world sold up and restarted gets no lessons", not lesson(w)))

bad = 0
for name, ok in checks:
    bad += not ok
    print("  %s  %s" % ("PASS" if ok else "FAIL", name))
print("%d/%d passed" % (len(checks) - bad, len(checks)))
sys.exit(1 if bad else 0)
