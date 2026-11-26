#!/usr/bin/env python3
"""Drive a real session against a running server and assert what a player sees.

    python3 tools/smoke.py [host] [port]

Exit code 0 = every check passed. This is the test that matters: it talks to the
service over the socket the player uses, not to the engine directly.
"""
import re
import socket
import sys
import time


def read_until(sock, marker, timeout=5.0):
    sock.settimeout(timeout)
    buf = b""
    while marker.encode() not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf.decode("latin-1")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    host = args[0] if args else "127.0.0.1"
    port = int(args[1]) if len(args) > 1 else 2023
    checks = []

    s = socket.create_connection((host, port), timeout=5)
    banner = read_until(s, "OPERATOR ID?")
    checks.append(("banner mentions the service", "NORTHGATE" in banner))
    s.sendall(b"noxyr\r\n")
    greet = read_until(s, "OPR>")
    checks.append(("greeting reports tick and cash", "TICK" in greet and "CASH" in greet))

    # this suite spends money, so it must not be pointed at a world someone is playing
    m = re.search(r"TICK (\d+)", greet)
    if m and int(m.group(1)) > 5 and "--force" not in sys.argv:
        print("refusing: that world is %s ticks old, so it is a live game, not a test server.\n"
              "start a throwaway one:  python3 main.py --port 2099 --db /tmp/test.db --tick 1"
              % m.group(1))
        return 2

    def cmd(line, marker="OPR>"):
        s.sendall(line.encode() + b"\r\n")
        return read_until(s, marker)

    out = cmd("STATUS")
    checks.append(("STATUS shows revenue and outgoings",
                   "REVENUE PER TICK" in out and "OUTGOINGS PER TICK" in out))
    checks.append(("STATUS names the binding capacity limit",
                   "USABLE CAPACITY" in out and ("LIMITED BY" in out or "MATCHED" in out)))
    checks.append(("STATUS reports callers turned away", "TURNED AWAY" in out))
    checks.append(("STATUS shows cash", "CASH" in out))

    out = cmd("USERS")
    checks.append(("USERS renders a caller table", "CALLERS ON SYSTEM" in out))
    checks.append(("USERS accounts for every caller (served + refused)",
                   "dialled in" in out and "refused" in out
                   and "... more" not in out))

    out = cmd("JOBS")
    checks.append(("JOBS renders a queue", "BATCH QUEUE" in out))

    s.sendall(b"HELP\r\n")
    raw = read_until(s, "OPR>").encode("latin-1")
    checks.append(("HELP lists the buy and settings sheets", b"BUY" in raw and b"SETTINGS" in raw
                   and b"BUY LINES" not in raw))
    checks.append(("every HELP line carries a carriage return",
                   raw.count(b"\n") == raw.count(b"\r\n")))

    out = cmd("BUY LINES 1")
    checks.append(("BUY deducts and confirms", "OK:" in out and "INSTALLED" in out))

    out = cmd("BUY NONSENSE 1")
    checks.append(("BUY refuses what it does not sell", "ERR:" in out))

    out = cmd("BUY CORES 5")          # within the room, beyond the purse
    checks.append(("BUY refuses without funds", "INSUFFICIENT FUNDS" in out))

    out = cmd("BUY SLOTS 2")
    checks.append(("BUY SLOTS adds throughput", "OK:" in out and "SLOTS" in out))

    out = cmd("STATUS")
    checks.append(("STATUS reports batch slots", "BATCH SLOTS" in out))

    out = cmd("JOBS")
    checks.append(("JOBS shows the batch queue", "BATCH QUEUE" in out))

    out = cmd("SELL LINES 1")
    checks.append(("SELL disposes of a line for 40% back", "OK:" in out and "DISPOSED" in out))
    out = cmd("SELL CORES 1")
    checks.append(("SELL refuses things it cannot sell", "ERR:" in out))

    out = cmd("STAFF")
    checks.append(("STAFF lists roles you can hire", "STAFF" in out and "HIRE" in out))
    out = cmd("UPGRADES")
    checks.append(("UPGRADES lists the catalogue", "SECOND SHIFT" in out))
    out = cmd("SITES")
    checks.append(("SITES lists locations", "ROOM 2B" in out and "MOVE" in out))
    out = cmd("MODS")
    checks.append(("MODS renders (even when empty)", "IN FORCE" in out))
    out = cmd("HIRE ENGINEER")
    checks.append(("an unaffordable hire is refused", "ERR:" in out))
    out = cmd("MOVE 2")
    checks.append(("an unaffordable move is refused", "ERR:" in out))
    out = cmd("PRESTIGE")
    checks.append(("prestige is refused before the building is owned", "ERR:" in out))

    out = cmd("CLOSE 99")
    checks.append(("CLOSE refuses a job that does not exist", "ERR:" in out))

    out = cmd("GOAL")
    checks.append(("GOAL states the target and the distance",
                   "THE GOAL" in out and "BUILDING" in out and "CASH" in out))
    out = cmd("BUY BUILDING")
    checks.append(("BUY BUILDING refuses while you are short", "ERR:" in out and "NOT YET" in out))

    out = cmd("OFFERS")
    checks.append(("OFFERS renders the teleprinter", "WORK OFFERED" in out))
    out = cmd("ACCEPT 99")
    checks.append(("ACCEPT refuses an offer that does not exist", "ERR:" in out))
    out = cmd("DROP 99")
    checks.append(("DROP refuses an offer that does not exist", "ERR:" in out))

    out = cmd("PASSPORT")
    checks.append(("unknown command is refused", "UNRECOGNISED" in out))

    out = cmd("NEWS")
    checks.append(("NEWS renders without crashing", "CONSOLE MESSAGES" in out or "NO CONSOLE" in out))

    out = cmd("LOG")
    checks.append(("LOG reads the journal", "SYSTEM JOURNAL" in out))

    s.sendall(b"BYE\r\n")
    out = read_until(s, "73.")
    checks.append(("BYE drops carrier politely", "73." in out))
    s.close()

    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
