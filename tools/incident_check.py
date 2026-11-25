#!/usr/bin/env python3
"""Incidents ask a question that matters; projects take time; both survive a restart.

    python3 tools/incident_check.py
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


def fresh(lessons_done=True):
    COUNT[0] += 1
    path = os.path.join(TMP, "i%d.db" % COUNT[0])
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "main.py"), "--port", "0",
                          "--db", path, "--tick", "60"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    p.stdout.readline(); p.terminate(); p.wait(timeout=5)
    w = load(path)
    if lessons_done:
        w.lesson_next = len(C.LESSONS)
    w.money, w.cores, w.lines = 500.0, 2, 16
    return w, path


def load(path):
    w = main.World(sqlite3.connect(path, isolation_level=None))
    w.load(); w.load_faults(); w.load_upgrades(); w.load_contracts(); w.load_pending()
    return w


def force(w, key):
    w.incident = {"key": key, "left": next(i for i in C.INCIDENTS if i["key"] == key)["ticks"]}
    w.save_pending()


# no questions while the teaching offers run
w, _ = fresh(lessons_done=False)
rng = random.Random(1)
w.ticks = C.INCIDENT_EVERY - 1
for _ in range(3):
    w.tick(rng)
w.roll_incident(rng)
checks.append(("no incident while the lessons are on", w.incident is None))

# an incident rolls, presses, and is answered
w, path = fresh()
w.roll_incident(random.Random(2))
checks.append(("an incident can roll once lessons are done", w.incident is not None))
force(w, "fan")
fm = w.effects()["fault_mult"]
checks.append(("an open incident presses on the plant", fm >= 1.5))
ok, msg = w.decide("TEAPOT")
checks.append(("a choice that is not offered is refused", not ok and "SWAP" in msg))
cap = w.capacity
ok, msg = w.decide("1")
checks.append(("choices can be given by number", ok and w.incident is None))
checks.append(("SWAP starts a project at half capacity",
               w.project and w.project["name"] == "FAN SWAP" and w.capacity < cap))
checks.append(("the incident's pressure lifts once answered", w.effects()["fault_mult"] < fm))
w.save()
w2 = load(path)
checks.append(("a project survives a restart", w2.project and w2.project["name"] == "FAN SWAP"))

# a busy engineer and an empty purse refuse the choice, never half-apply it
force(w, "reroute")
cash = w.money
ok, msg = w.decide("AGREE")
checks.append(("a project choice waits for the engineers", not ok and "BUSY" in msg
               and w.money == cash and w.incident))
force(w, "student")
w.money = 10.0
ok, msg = w.decide("HIRE")
checks.append(("an unaffordable choice is refused", not ok and "INSUFFICIENT" in msg))

# nobody answers: the default lands and is journaled for the away report
w, path = fresh()
w.mark_seen()
force(w, "ceiling")
w.save()
checks.append(("an incident survives a restart", load(path).incident["key"] == "ceiling"))
rng = random.Random(3)
for _ in range(100):
    w.tick(rng)
checks.append(("an unanswered incident takes its default", w.incident is None
               and any(f["kind"] == "line" for f in w.faults)))
r = main.r_away(w)
checks.append(("the away report tells what nobody answered", "NOBODY ANSWERED" in r))
w, _ = fresh()
w.mark_seen()
w.roll_incident(random.Random(5))
w.ticks += 1
checks.append(("the away report tells the question that is waiting",
               "DECISION WAITING" in main.r_away(w)))

# every choice of every incident applies cleanly
bad = []
for inc in C.INCIDENTS:
    for word, _d, _o in inc["choices"]:
        w, _ = fresh()
        force(w, inc["key"])
        ok, msg = w.decide(word)
        if not ok:
            bad.append("%s/%s: %s" % (inc["key"], word, msg))
checks.append(("every choice of every incident applies (%s)" % (bad or "all"), not bad))
checks.append(("every default is one of its choices",
               all(i["default"] in [c[0] for c in i["choices"]] for i in C.INCIDENTS)))

# a move is downtime, then a new site
w, path = fresh()
w.money = 10000.0
ok, msg = main.do_move(w, ["1"])
checks.append(("MOVE pays and puts the machine in the van",
               ok and w.capacity == 0 and w.site == 0 and "VAN" in msg))
ok2, msg2 = main.do_move(w, ["1"])
checks.append(("a second MOVE while moving is refused", not ok2 and "ALREADY MOVING" in msg2))
rng = random.Random(4)
for _ in range(C.MOVE_TICKS):
    w.tick(rng)
checks.append(("after the move the new site is ours", w.site == 1 and w.project is None))
checks.append(("and the machine is back on", w.capacity > 0))
w.save()
checks.append(("the new site survives a restart", load(path).site == 1))

bad_n = 0
for name, ok in checks:
    bad_n += not ok
    print("  %s  %s" % ("PASS" if ok else "FAIL", name))
print("%d/%d passed" % (len(checks) - bad_n, len(checks)))
sys.exit(1 if bad_n else 0)
