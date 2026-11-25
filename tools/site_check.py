#!/usr/bin/env python3
"""A site has room for so much plant; a bigger site raises the ceiling.

    python3 tools/site_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import content as C  # noqa: E402
import main  # noqa: E402

checks = []
w = main.World(None)
w.money = 1e6
mods, lines = C.SITE_ROOM[0]
ok, _ = main.do_buy(w, ["CORES", str(mods - w.cores)])
checks.append(("the room fills to its module count", ok and w.cores == mods))
ok, msg = main.do_buy(w, ["CORES", "1"])
checks.append(("one module past the room is refused, pointing at SITES",
               not ok and "SITES" in msg and w.cores == mods))
ok, _ = main.do_buy(w, ["LINES", str(lines - w.lines)])
ok2, _ = main.do_buy(w, ["LINES", "1"])
checks.append(("lines stop at the room's count", ok and not ok2 and w.lines == lines))
checks.append(("the first room never caps below its market", lines >= C.LOCATIONS[0][2]))
main.do_move(w, ["1"])
while w.project:
    w.advance_project()
ok, _ = main.do_buy(w, ["CORES", "1"])
checks.append(("a bigger site has room for more", ok and w.cores == mods + 1))
checks.append(("every site has a room entry", len(C.SITE_ROOM) >= len(C.LOCATIONS)))
checks.append(("rooms grow site by site",
               all(C.SITE_ROOM[i][0] < C.SITE_ROOM[i + 1][0] and C.SITE_ROOM[i][1] < C.SITE_ROOM[i + 1][1]
                   for i in range(len(C.SITE_ROOM) - 1))))
checks.append(("SITES prints the room", "room for %d modules" % mods in main.r_sites(w)))
checks.append(("/state carries the room", main.snapshot(w)["room"]["modules"] == C.SITE_ROOM[1][0]))

# a save already over the room keeps its plant; it just cannot buy more
w = main.World(None)
w.money, w.cores = 1e6, mods + 3
ok, _ = main.do_buy(w, ["CORES", "1"])
checks.append(("an old save over the room keeps its plant", not ok and w.cores == mods + 3))

bad = 0
for name, ok in checks:
    bad += not ok
    print("  %s  %s" % ("PASS" if ok else "FAIL", name))
print("%d/%d passed" % (len(checks) - bad, len(checks)))
sys.exit(1 if bad else 0)
