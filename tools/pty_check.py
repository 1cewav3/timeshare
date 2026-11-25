#!/usr/bin/env python3
"""Run play.py under a real pty and check what a person would actually see.

    python3 tools/pty_check.py [port]

This is the test that catches "the terminal is bugged": typing must be visible,
backspace must erase, and the server's replies must arrive. It checks rendered
bytes, not the socket protocol.
"""
import os
import pty
import select
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def read_all(fd, wait=1.2):
    buf = b""
    end = time.time() + wait
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.15)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
    return buf.decode("latin-1", "replace")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = int(args[-1]) if args else 2087
    checks = []

    pid, fd = pty.fork()
    if pid == 0:  # child: the real client
        os.execv(sys.executable, [sys.executable,
                                  os.path.join(ROOT, "play.py"), "127.0.0.1", str(port)])
        os._exit(1)

    seen = read_all(fd, 2.0)
    checks.append(("banner arrives in the client", "NORTHGATE" in seen))
    checks.append(("it asks for an operator id", "OPERATOR ID?" in seen))

    os.write(fd, b"noxyr\r")
    seen = read_all(fd, 1.2)
    checks.append(("typed name is echoed back", "noxyr" in seen))
    checks.append(("welcome line arrives", "WELCOME, NOXYR" in seen))

    # type a command with a deliberate extra character, then backspace it away
    os.write(fd, b"STATUSX")
    typed = read_all(fd, 0.8)
    checks.append(("characters are visible as you type", "STATUSX" in typed))

    os.write(fd, b"\x7f")
    fixed = read_all(fd, 0.8)
    checks.append(("backspace erases on screen", "\b \b" in fixed))

    os.write(fd, b"\r")
    answered = read_all(fd, 1.5)
    checks.append(("the corrected command is accepted (no UNRECOGNISED)",
                   "UNRECOGNISED" not in answered))
    checks.append(("the corrected command actually ran", "SYSTEM REPORT" in answered))

    os.write(fd, b"STATUS\r")
    report = read_all(fd, 1.5)
    checks.append(("STATUS renders in the client", "SYSTEM REPORT" in report))
    checks.append(("report carries live numbers", "NET PER TICK" in report))

    os.write(fd, b"BYE\r")
    bye = read_all(fd, 1.5)
    checks.append(("BYE closes cleanly", "73." in bye))

    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
