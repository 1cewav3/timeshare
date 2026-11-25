#!/usr/bin/env python3
"""Connect to the timeshare service from your terminal.

    python3 play.py                 # localhost:2023
    python3 play.py 192.168.1.144   # the pi

Ctrl-] or Ctrl-C quits. Stdlib only, works over a raw socket.

Note on the input loop: it reads the terminal through os.read on the raw fd,
never through python's buffered sys.stdin. Mixing select() with a buffered
reader hides the rest of the typed characters inside the buffer, so select
reports "nothing to read" while the keystrokes are already in hand. The symptom
is typing that arrives one character per second and commands that reach the
server truncated - which reads as "the terminal is bugged".
"""
import os
import select
import socket
import sys

try:
    import termios
    import tty
except ImportError:      # not posix; still usable when piped
    termios = None


class LineEditor:
    """Local echo and editing: the server does neither, so the client must."""

    def __init__(self, out=sys.stdout):
        self.out = out
        self.line = ""

    def _w(self, text):
        self.out.write(text)
        self.out.flush()

    def backspace(self):
        if self.line:
            self.line = self.line[:-1]
            self._w("\b \b")

    def add(self, ch):
        self.line += ch
        self._w(ch)

    def take(self):
        line, self.line = self.line, ""
        self._w("\r\n")
        return line

    def redraw(self):
        if self.line:
            self._w(self.line)


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 2023

    sock = socket.create_connection((host, port), timeout=10)
    sock.setblocking(False)

    in_fd = sys.stdin.fileno()
    old = None
    if termios is not None and sys.stdin.isatty():
        old = termios.tcgetattr(in_fd)
        tty.setraw(in_fd)          # no echo, no line buffering, no signal keys

    editor = LineEditor()

    try:
        while True:
            try:
                readable, _, _ = select.select([sock, in_fd], [], [], 1.0)
            except (OSError, ValueError):
                break

            if sock in readable:
                try:
                    data = sock.recv(4096)
                except BlockingIOError:
                    data = None
                except OSError:
                    break
                if data == b"":
                    print("\r\n[connection closed]")
                    break
                if data:
                    sys.stdout.write(data.decode("latin-1"))
                    sys.stdout.flush()
                    editor.redraw()

            if in_fd in readable:
                try:
                    chunk = os.read(in_fd, 64)
                except OSError:
                    break
                if not chunk:
                    break
                quit_now = False
                for ch in chunk.decode("latin-1"):
                    if ch in ("\x1d", "\x03"):       # Ctrl-] or Ctrl-C
                        quit_now = True
                        break
                    if ch in ("\r", "\n"):
                        sock.sendall((editor.take() + "\n").encode("latin-1"))
                    elif ch in ("\x7f", "\b"):
                        editor.backspace()
                    elif ch.isprintable():
                        editor.add(ch)
                if quit_now:
                    break
    finally:
        if old is not None:
            termios.tcsetattr(in_fd, termios.TCSADRAIN, old)
        sock.close()


if __name__ == "__main__":
    main()
