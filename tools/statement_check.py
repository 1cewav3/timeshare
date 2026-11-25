#!/usr/bin/env python3
"""Going broke is an ending: a final statement with the run's record, kept.

    python3 tools/statement_check.py
"""
import os
import random
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import main  # noqa: E402

checks = []
path = os.path.join(tempfile.mkdtemp(), "s.db")
p = subprocess.Popen([sys.executable, os.path.join(HERE, "main.py"), "--port", "0",
                      "--db", path, "--tick", "60"],
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
p.stdout.readline(); p.terminate(); p.wait(timeout=5)


def load():
    w = main.World(sqlite3.connect(path, isolation_level=None))
    w.load(); w.load_faults(); w.load_upgrades(); w.load_contracts()
    return w


w = load()
w.lesson_next = 99
w.cores, w.lines = 2, 16
w.staff = {"OPERATOR": {"name": "VOSS"}}
rng = random.Random(2)
for _ in range(300):
    w.tick(rng)
earned = w.run["earned"]
w.run["met"], w.run["short"] = 3, 1
w.money = -500.0
w.mark_seen()
while not w.statement:
    w.tick(rng)
w.save()
s = w.statement.replace("\r\n", "\n")
checks.append(("going broke prints a final statement", "FINAL STATEMENT" in s))
took = float(s.split("TOOK IN", 1)[1].split()[0].replace(",", ""))
checks.append(("it carries the run's record",
               took >= earned and "3 MET, 1 SHORTED" in s and "OPERATOR VOSS" in s))
checks.append(("it fits the sheet", max(map(len, s.split("\n"))) <= 49 and s.count("\n") <= 22))
checks.append(("a new run starts after it", w.run["met"] == 0 and w.run["earned"] < earned))
r = main.r_away(w)
checks.append(("the away report names it and points at STATEMENT", "TYPE STATEMENT" in r))
checks.append(("the away report does not dump the statement itself", "THE RUN" not in r))
w2 = load()
checks.append(("the statement survives a restart", w2.statement == w.statement))
checks.append(("so does the new run", w2.run["met"] == 0))
log = main.r_log(w2)
checks.append(("LOG does not print the statement", "THE RUN" not in log))
snap = main.snapshot(w2)
checks.append(("/state names the statement", snap["statement"] > 0))

bad = 0
for name, ok in checks:
    bad += not ok
    print("  %s  %s" % ("PASS" if ok else "FAIL", name))
print("%d/%d passed" % (len(checks) - bad, len(checks)))
sys.exit(1 if bad else 0)
