#!/usr/bin/env python3
"""Liquidation must not cost the save anything it did not take in play.

    python3 tools/liquidate_check.py

Builds a world in a throwaway database, gives it standing, prestiges, a site,
bodges, an upgrade and extra slots, liquidates it, reloads from disk, and
checks the reloaded world matches the one that was playing.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import main  # noqa: E402


def schema(path):
    """Let the server make its own tables, so this never drifts from serve()."""
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "main.py"),
                          "--port", "0", "--db", path, "--tick", "60"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    p.stdout.readline()          # "timeshare up ..." once the tables exist
    p.terminate()
    p.wait(timeout=5)


def load(path):
    db = sqlite3.connect(path, isolation_level=None)
    w = main.World(db)
    w.load()
    w.load_faults()
    w.load_upgrades()
    w.load_contracts()
    return w


def main_():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "liq.db")
    schema(path)
    w = load(path)
    w.standing, w.prestiges, w.site, w.bodges = 3, 2, 1, 4
    for name, val in (("standing", 3), ("prestiges", 2), ("site", 1), ("bodges", 4),
                      ("up:COOLING PLANT", 1)):
        w.put(name, val)
    w.owned = {"COOLING PLANT"}
    w.buy_slot(3)
    w.lines = 20
    w.save()

    w.liquidate()
    w.save()
    played = {"standing": w.standing, "prestiges": w.prestiges, "site": w.site,
              "bodges": w.bodges, "owned": sorted(w.owned),
              "extra_slots": w.extra_slots, "lines": w.lines}
    r = load(path)
    reloaded = {"standing": r.standing, "prestiges": r.prestiges, "site": r.site,
                "bodges": r.bodges, "owned": sorted(r.owned),
                "extra_slots": r.extra_slots, "lines": r.lines}
    bad = 0
    for k in played:
        ok = played[k] == reloaded[k]
        bad += not ok
        print("  %s  %-12s in play %-20s reloaded %s"
              % ("PASS" if ok else "FAIL", k, played[k], reloaded[k]))
    print("%d/%d passed" % (len(played) - bad, len(played)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main_())
