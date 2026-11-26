#!/usr/bin/env python3
"""The game runs the world in its own process: no network, one window per save.

    python3 tools/session_check.py
"""
import ast
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import machine  # noqa: E402
import main  # noqa: E402

checks = []
db = os.path.join(tempfile.mkdtemp(), "s.db")

# a new save: no time owed, the lessons begin, the first login has no report
s = main.Session(db, 1)
checks.append(("a new save owes no time", not s.catching_up()))
text = s.welcome()
checks.append(("the welcome names the tick and the cash", "TICK" in text and "CASH" in text))
out, closing = s.command("STATUS")
checks.append(("commands run straight on the engine", "SYSTEM REPORT" in out and not closing))
out, closing = s.command("BYE")
checks.append(("BYE says it is closing", closing))
s.world.money = 500.0
s.command("BUY LINES 2")
s.close()

# closed for 2000 seconds at a 1-second tick: that time is owed, caught up in slices
d = sqlite3.connect(db)
d.execute("UPDATE world SET last_tick=?", (int(time.time()) - 2000,))
d.commit(); d.close()
s = main.Session(db, 1)
checks.append(("an absence is owed as ticks", 1990 <= s.owed <= 2010))
checks.append(("what was bought before closing is still there", s.world.lines == 10))
s.step(budget=0.02)
checks.append(("catching up works in slices, not all at once", 0 < s.owed < s.owed_total))
while s.catching_up():
    s.step()
checks.append(("and finishes", s.world.ticks >= 1990))
report = s.welcome()
checks.append(("then the report of the absence, once", "WHILE YOU WERE AWAY" in report))
checks.append(("and not again", "WHILE YOU WERE AWAY" not in s.command("STATUS")[0]))
s.close()

# session mode: nothing is owed for time spent closed
d = sqlite3.connect(db)
d.execute("UPDATE world SET last_tick=?, mode='session'", (int(time.time()) - 5000,))
d.commit(); d.close()
s = main.Session(db, 1)
checks.append(("in session mode, closed time is not owed", not s.catching_up()))
s.close()

# one window per save
held = machine.hold_save(db)
probe = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, %r); import machine; "
                        "print(machine.hold_save(%r) is None)" % (HERE, db)],
                       capture_output=True, text=True)
checks.append(("a second window on the same save is refused", probe.stdout.strip() == "True"))
held.close()

# the game itself never opens a socket
tree = ast.parse(open(os.path.join(HERE, "machine.py")).read())
imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if isinstance(n, ast.Import) else [ast.alias(n.module or "")])}
checks.append(("the window imports no networking", not imported & {"socket", "urllib", "http", "select"}))

bad = 0
for name, ok in checks:
    bad += not ok
    print("  %s  %s" % ("PASS" if ok else "FAIL", name))
print("%d/%d passed" % (len(checks) - bad, len(checks)))
sys.exit(1 if bad else 0)
