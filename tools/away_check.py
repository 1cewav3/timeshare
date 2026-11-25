#!/usr/bin/env python3
"""The away report: one per absence, a timeline not a total, and it fits the sheet.

    python3 tools/away_check.py

Engine checks run worlds in a throwaway database; the last checks start a real
server, so the login path (report after the welcome, only for the first operator
back) is tested over the socket the window uses.
"""
import os
import random
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import main  # noqa: E402

checks = []


COUNT = [0]


def fresh(path):
    COUNT[0] += 1                       # a new file each time: no stale WAL
    path = "%s.%d" % (path, COUNT[0])
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "main.py"), "--port", "0",
                          "--db", path, "--tick", "60"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    p.stdout.readline()
    p.terminate()
    p.wait(timeout=5)
    db = sqlite3.connect(path, isolation_level=None)
    w = main.World(db)
    w.load(); w.load_faults(); w.load_upgrades(); w.load_contracts()
    return w


def rows(text):
    return text.replace("\r\n", "\n").rstrip("\n").split("\n")


tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "away.db")

# shape: any length of absence fits the 51-column, 17-line sheet
for ticks, seed in ((30, 1), (552, 3), (4000, 7), (43200, 5)):
    w = fresh(db)
    w.cores, w.lines, w.money = 2, 16, 300.0
    w.mark_seen()
    rng = random.Random(seed)
    for _ in range(ticks):
        w.tick(rng)
    r = rows(main.r_away(w))
    checks.append(("%d ticks away: fits 49 columns and 17 rows" % ticks,
                   max(map(len, r)) <= 49 and len(r) <= 17))

# a fault is told as a timeline entry with its cost
w = fresh(db)
w.cores, w.lines, w.money = 2, 16, 300.0
for _ in range(10):
    w.tick(random.Random(0))
w.mark_seen()
rng = random.Random(1)
for _ in range(60):
    w.tick(rng)
w.add_fault("module", 2)
w.journal("FAULT: MODULE 2 DOWN", "fault", w.net + 0.3)    # as roll_fault writes it
for _ in range(120):
    w.tick(rng)
r = main.r_away(w)
checks.append(("the fault is placed in time", "+1:00" in r and "MODULE 2 FAILED" in r))
checks.append(("the fault says what it cost", "NOT EARNED" in r and "STILL DOWN" in r))

# nothing happened: still something to read, and no invented events
w = fresh(db)
w.mark_seen()
w.ticks += 30
r = main.r_away(w)
checks.append(("a quiet stretch still gets a report", "QUIET STRETCH" in r))

# no time, no report
w = fresh(db)
w.mark_seen()
checks.append(("no absence, no report", main.r_away(w) == ""))

# an old save is not handed its whole history as one report
w = fresh(db)
w.journal("FAULT: LINE 3 DOWN", "fault", 0.3)
w.db.execute("DELETE FROM upgrade WHERE name LIKE 'seen:%'")
w2 = main.World(w.db); w2.load(); w2.load_faults(); w2.load_upgrades()
checks.append(("an old save starts counting from now", "LINE 3" not in main.r_away(w2)))

# over the socket: the first operator back gets it, once
db2 = os.path.join(tmp, "live.db")
port = 2190 + os.getpid() % 50
srv = subprocess.Popen([sys.executable, os.path.join(HERE, "main.py"), "--port", str(port),
                        "--db", db2, "--tick", "1"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def login():
    for _ in range(50):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            break
        except OSError:
            time.sleep(0.1)
    buf = b""
    while b"OPERATOR ID?" not in buf:
        buf += s.recv(4096)
    s.sendall(b"check\r\n")
    buf = b""
    while b"OPR>" not in buf:
        buf += s.recv(4096)
    return s, buf.decode("latin-1")


try:
    s, _ = login()
    s.sendall(b"BYE\r\n"); s.close()
    time.sleep(3.5)
    a, text_a = login()
    b, text_b = login()
    checks.append(("login after an absence prints the report", "WHILE YOU WERE AWAY" in text_a))
    checks.append(("a second operator on the line does not get it again",
                   "WHILE YOU WERE AWAY" not in text_b))
    b.sendall(b"AWAY\r\n")
    buf = b""
    while b"OPR>" not in buf:
        buf += b.recv(4096)
    checks.append(("AWAY prints the last report again", b"WHILE YOU WERE AWAY" in buf))
    a.close(); b.close()
finally:
    srv.terminate()
    srv.wait(timeout=5)

bad = 0
for name, ok in checks:
    bad += not ok
    print("  %s  %s" % ("PASS" if ok else "FAIL", name))
print("%d/%d passed" % (len(checks) - bad, len(checks)))
sys.exit(1 if bad else 0)
