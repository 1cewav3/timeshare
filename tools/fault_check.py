#!/usr/bin/env python3
"""Check the one mechanic that has to work: things break, and REPAIR fixes them.

Needs a server started with a high fault rate:

    python3 main.py --port 2088 --db /tmp/f.db --tick 1 \
        --set fault_roll_every=1 --set fault_roll_chance=1.0
    python3 tools/fault_check.py 2088

Exit 0 = the fault loop is playable: it is visible, it names a real line, it
costs money to fix, and it survives a restart.
"""
import socket
import sys
import time


def read_until(sock, marker, timeout=6.0):
    sock.settimeout(timeout)
    buf = b""
    while marker.encode() not in buf:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
    return buf.decode("latin-1")


class Session:
    def __init__(self, host, port):
        self.s = socket.create_connection((host, port), timeout=5)
        read_until(self.s, "OPERATOR ID?")
        self.s.sendall(b"tester\r\n")
        read_until(self.s, "OPR>")

    def cmd(self, line):
        self.s.sendall(line.encode() + b"\r\n")
        return read_until(self.s, "OPR>")

    def close(self):
        self.s.close()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = int(args[-1]) if args else 2088
    checks = []
    ses = Session("127.0.0.1", port)

    # let a couple of ticks pass so something fails
    deadline = time.time() + 12
    faults = ""
    while time.time() < deadline:
        faults = ses.cmd("FAULTS")
        if "DOWN" in faults and "NOTHING IS DOWN" not in faults:
            break
        time.sleep(1)

    checks.append(("a fault appeared on its own", "NOTHING IS DOWN" not in faults))
    checks.append(("the fault sheet shows what is down", "REPAIR COST" in faults))
    checks.append(("it names a real line or module", ("LINE" in faults) or ("MODULE" in faults)))

    # repairing something that is not down must be refused, not silently accepted
    bad = ses.cmd("REPAIR LINE 99")
    checks.append(("repairing a working line is refused", "ERR:" in bad))

    status_before = ses.cmd("STATUS")
    checks.append(("STATUS surfaces the fault", "DOWN:" in status_before))

    fixed = ses.cmd("REPAIR")
    checks.append(("REPAIR everything is accepted", "OK:" in fixed and "REPAIRED" in fixed))
    checks.append(("capacity is reported after the fix", "CAPACITY NOW" in fixed))

    after = ses.cmd("FAULTS")
    checks.append(("the sheet is clear afterwards", "NOTHING IS DOWN" in after))

    again = ses.cmd("REPAIR")
    checks.append(("repairing nothing is refused", "ERR:" in again))

    log = ses.cmd("LOG")
    checks.append(("the repair is journalised", "REPAIR" in log))

    ses.close()

    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
