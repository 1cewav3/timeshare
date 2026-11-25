#!/usr/bin/env python3
"""The panel: timeshare drawn as the machine itself, inside a terminal.

    python3 panel.py                       # game on 127.0.0.1:2023, board on :2024
    python3 panel.py host port webport

Two connections, each doing one job. The socket session carries commands and
makes you present, so session mode counts you as on the line. The HTTP board
supplies the numbers the lamps are drawn from, so this file never invents a
figure the engine did not report.

Stdlib only. Needs a terminal of at least 100x30 and truecolour.
"""
import json
import os
import select
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    import termios
    import tty
except ImportError:                # not posix
    termios = None

W, H = 100, 30

METAL = (14, 15, 12)
STENCIL = (143, 156, 134)
LAMP = (155, 224, 143)
OFF = (42, 47, 38)
FAULT = (216, 72, 60)
PAPER = (232, 226, 210)
INK = (35, 32, 26)
FADE = (141, 133, 111)


class Grid:
    def __init__(self, fg, bg):
        self.cells = [[(" ", fg, bg) for _ in range(W)] for _ in range(H)]

    def put(self, x, y, text, fg=None, bg=None):
        if y < 0 or y >= H:
            return
        for i, ch in enumerate(text):
            cx = x + i
            if 0 <= cx < W:
                _, ofg, obg = self.cells[y][cx]
                self.cells[y][cx] = (ch, fg if fg is not None else ofg,
                                     bg if bg is not None else obg)

    def to_ansi(self):
        out = ["\x1b[H"]
        for row in self.cells:
            cur = None
            line = []
            for ch, fg, bg in row:
                if (fg, bg) != cur:
                    line.append("\x1b[38;2;%d;%d;%dm\x1b[48;2;%d;%d;%dm" % (fg + bg))
                    cur = (fg, bg)
                line.append(ch)
            out.append("".join(line) + "\x1b[0m")
        return "\r\n".join(out)


def money(v):
    return "{:,.2f}".format(v)


def draw(s, typed, reply, blink):
    """One frame from one state snapshot."""
    g = Grid(LAMP, METAL)
    cores = int(s.get("cores", 0))
    working_cores = int(s.get("working_cores", cores))
    lines = int(s.get("lines", 0))
    working_lines = int(s.get("working_lines", lines))
    served = int(s.get("served", 0))
    per_core = int(s.get("callers_per_core", 8)) or 8
    demand = int(s.get("demand", 0))
    faults = s.get("faults") or []
    down_mods = {f["n"] for f in faults if f.get("kind") == "module"}
    down_lines = {f["n"] for f in faults if f.get("kind") == "line"}

    g.put(0, 0, "═" * W, STENCIL)
    g.put(2, 0, "╡ NORTHGATE COMPUTER SERVICES · SYS/370-145 · INSTALLED SEP 79 ╞", METAL)
    g.put(74, 0, " SEP 1979 ", STENCIL)
    for y in range(1, 26):
        g.put(0, y, "║", STENCIL)
        g.put(48, y, "║", STENCIL)
        g.put(99, y, "║", STENCIL)
    g.put(0, 26, "═" * W, STENCIL)

    # ---- module lamps: one lamp per caller the card is carrying
    g.put(2, 2, "M O D U L E   L A M P S", STENCIL)
    left = served
    for i in range(max(8, cores)):
        y = 4 + i * 2
        if y > 19:
            break
        n = i + 1
        g.put(2, y, "M O D  %d" % n, STENCIL)
        if n > cores:
            g.put(11, y, "not installed", OFF)
            continue
        if n in down_mods:
            g.put(15, y, "████████", FAULT)
            g.put(25, y, "FAULT", FAULT)
            continue
        load = min(per_core, max(0, left))
        left -= load
        g.put(15, y, "●" * load + "○" * (per_core - load), LAMP if load else OFF)
    g.put(2, 20, "C O R E S   %2d   ·   L O A D   %3d%%"
          % (cores, int(100 * served / max(1, working_cores * per_core))), STENCIL)

    # ---- line lamps: one per dial-in card
    g.put(2, 22, "L I N E S", STENCIL)
    busy = min(served, working_lines)
    for n in range(1, max(1, min(lines, 32)) + 1):
        row, col = divmod(n - 1, 16)
        x, y = 2 + col * 2, 23 + row
        if n in down_lines:
            g.put(x, y, "✕", FAULT)
        elif n <= busy:
            g.put(x, y, "●", LAMP)
        else:
            g.put(x, y, "○", OFF)
    turned = max(0, demand - served)
    g.put(2, 25, "T U R N E D   A W A Y   %d" % turned, FAULT if turned else OFF)

    # ---- the teleprinter strip
    for y in range(1, 26):
        g.put(49, y, " " * 50, INK, PAPER)
    hist = s.get("history") or []
    net = s.get("net", hist[-1][2] if hist else 0.0)
    g.put(51, 2, "NORTHGATE  TELEPRINTER  6 LPI", FADE, PAPER)
    g.put(51, 3, "─" * 46, FADE, PAPER)
    g.put(51, 4, "TICK %d   CASH %s   NET %+.2f"
          % (s.get("tick", 0), money(s.get("cash", 0.0)), net), INK, PAPER)
    g.put(51, 5, "LINES %d/%d  CALLERS %d  REFUSED %d"
          % (working_lines, lines, served, turned), INK, PAPER)
    g.put(51, 6, "─" * 46, FADE, PAPER)

    y = 8
    for line in (s.get("news") or [])[-5:]:
        g.put(51, y, line[:46], FADE if y % 2 == 0 else INK, PAPER)
        y += 1
    y = max(y + 1, 13)
    for f in faults[:3]:
        g.put(51, y, "%s %d DOWN · REPAIR %s"
              % (f.get("kind", "?").upper(), f.get("n", 0), money(f.get("cost", 0))), INK, PAPER)
        y += 1
    y = max(y + 1, 17)
    live = [c for c in (s.get("offers") or []) if c.get("state") == "active"][:2]
    for c in live:
        g.put(51, y, "%s · %d TICKS · %s"
              % (c.get("company", "?")[:18], c.get("left", 0), money(c.get("fee", 0))), INK, PAPER)
        y += 1
    goal = s.get("goal") or {}
    pct = float(goal.get("pct", 0))
    filled = int(14 * pct / 100.0)
    g.put(51, 20, "GOAL %s" % s.get("site", ""), INK, PAPER)
    g.put(51, 21, "  %s%s  %d%%" % ("█" * filled, "░" * (14 - filled), int(pct)), INK, PAPER)
    staff = [i.get("name", "?") for i in (s.get("staff") or [])][:3]
    g.put(51, 23, "STAFF %s" % (", ".join(_cap(n) for n in staff) or "none"), FADE, PAPER)

    # ---- console band
    g.put(2, 27, "O P R ›", STENCIL)
    g.put(10, 27, typed, LAMP)
    if blink:
        g.put(10 + len(typed), 27, "█", LAMP)
    g.put(70, 27, "HELP ?", STENCIL)
    if reply:
        g.put(2, 28, reply.strip().replace("\r", "").replace("\n", " ")[:96], STENCIL)
    g.put(2, 29, "SEP 1979 · ROOM 2B, NORTHGATE HOUSE · TIMESHARING DIVISION", OFF)
    return g.to_ansi()


def _cap(name):
    return name.title() if isinstance(name, str) else str(name)


class Poller(threading.Thread):
    """Keeps the newest /state snapshot without blocking the frame."""

    def __init__(self, host, port):
        super().__init__(daemon=True)
        self.url = "http://%s:%d/state" % (host, port)
        self.state = {}
        self.stop = False

    def run(self):
        while not self.stop:
            try:
                with urllib.request.urlopen(self.url, timeout=2) as r:
                    self.state = json.loads(r.read().decode())
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                if os.environ.get("PANEL_DEBUG"):
                    print("poll %s: %s: %s" % (self.url, type(exc).__name__, exc),
                          file=sys.stderr, flush=True)
            time.sleep(0.5)


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 2023
    web = int(sys.argv[3]) if len(sys.argv) > 3 else port + 1

    size = os.get_terminal_size()
    if size.columns < W or size.lines < H:
        sys.exit("the panel needs %dx%d; this terminal is %dx%d"
                 % (W, H, size.columns, size.lines))

    sock = socket.create_connection((host, port), timeout=10)
    sock.setblocking(False)
    poller = Poller(host, web)
    poller.start()

    in_fd = sys.stdin.fileno()
    old = None
    if termios is not None and sys.stdin.isatty():
        old = termios.tcgetattr(in_fd)
        tty.setraw(in_fd)

    out = sys.stdout
    out.write("\x1b[2J\x1b[?25l")
    out.flush()

    typed, reply = "", ""
    phase = 0
    last_draw = 0.0

    def take_socket_text(text):
        """Login if asked, otherwise keep the last line for the status row."""
        nonlocal reply
        if not text.strip():
            return
        if "OPERATOR ID" in text.upper():
            sock.sendall(b"PANEL\n")
            reply = ""
        else:
            reply = " ".join(text.split()).replace("OPR>", "").strip()

    try:
        while True:
            readable, _, _ = select.select([sock, in_fd], [], [], 0.1)
            if sock in readable:
                try:
                    data = sock.recv(65536)
                except BlockingIOError:
                    data = None
                except OSError:
                    break
                if data == b"":
                    break
                if data:
                    take_socket_text(data.decode("latin-1"))
            if in_fd in readable:
                chunk = os.read(in_fd, 64)
                if not chunk:
                    break
                for ch in chunk.decode("latin-1"):
                    if ch in ("\x03", "\x1d"):
                        raise KeyboardInterrupt
                    if ch in ("\r", "\n"):
                        if typed.strip():
                            sock.sendall((typed + "\n").encode("latin-1"))
                            reply = ""
                        typed = ""
                    elif ch in ("\x7f", "\b"):
                        typed = typed[:-1]
                    elif ch.isprintable():
                        typed += ch
            now = time.time()
            if now - last_draw >= 0.4:
                last_draw = now
                phase = 1 - phase
                out.write(draw(poller.state, typed, reply, phase))
                out.write("\x1b[%d;%dH" % (H, W))
                out.flush()
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop = True
        if old is not None:
            termios.tcsetattr(in_fd, termios.TCSADRAIN, old)
        out.write("\x1b[0m\x1b[?25h\r\n")
        out.flush()
        sock.close()


if __name__ == "__main__":
    main()
