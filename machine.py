#!/usr/bin/env python3
"""The machine, as its own window.

    python3 machine.py                      # game on 127.0.0.1:2023, board on :2024
    python3 machine.py host port webport

Two connections, each doing one job: a socket session carries commands and makes
you present, and /state supplies every number drawn here. Nothing on the canvas
is invented — if the engine does not report it, it is not shown.
"""
import atexit
import collections
import math
import hashlib
import time
import json
import os
import select
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import tkinter as tk
import tkinter.font as tkfont
import urllib.error
import urllib.request

# ------------------------------------------------------------------ palette
BG = "#0b0d09"
PANEL = "#12150e"
RULE = "#3a4230"
STENCIL = "#95a289"
LAMP = "#a8eb9a"
GLOW = "#1d2b18"
OFF = "#242a1e"
FAULT = "#dc4a3c"
FAULT_BG = "#2b1512"
PAPER = "#e9e3d2"
PAPER_RULE = "#d5cdb6"
INK = "#231f19"
FADE = "#8d856f"
SHADOW = "#b9b19c"

W, H = 1180, 700
RACK_MODULES, RACK_LINES = 8, 32   # one rack page: eight module slots, 32 line slots
MAX_LAMPS = 26                     # the most callers any generation of module carries
BUILD = time.strftime("build %m-%d %H:%M", time.localtime(os.path.getmtime(__file__)))
LOG = "/tmp/timeshare-launch.log"
PX0, PX1 = 648, 1164          # paper sheet
LX0, LX1 = 16, 632            # left column


def mono(size, bold=True):
    """Pixel sizes, deliberately negative.

    A positive size is in points, which tk multiplies by its scaling factor
    (1.778 on this display) and blows the layout apart. Negative means pixels,
    so the text keeps the size the coordinates were designed for.
    """
    return ("DejaVu Sans Mono", -size, "bold" if bold else "normal")


class Poller(threading.Thread):
    def __init__(self, host, port):
        super().__init__(daemon=True)
        self.url = "http://%s:%d/state" % (host, port)
        self.state = {}
        self.fetched = 0.0
        self.stop = False

    def run(self):
        while not self.stop:
            try:
                with urllib.request.urlopen(self.url, timeout=2) as r:
                    state = json.loads(r.read().decode())
                    # stamped inside the dict and swapped in whole, so the
                    # painter never pairs a new to_next with an old read time
                    state["_fetched"] = self.fetched = time.time()
                    self.state = state
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                pass
            threading.Event().wait(0.5)


class Machine:
    def __init__(self, root, host, port, web):
        self.root = root
        self.note = ""
        self.log = collections.deque(maxlen=40)   # what you typed, and what came back
        root.title("timeshare — SYS/370-145")
        root.configure(bg=BG)
        root.resizable(False, False)

        self.sock = None
        try:
            self.sock = socket.create_connection((host, port), timeout=8)
            self.sock.setblocking(False)
        except OSError as exc:
            self.note = "no line to %s:%d — %s" % (host, port, exc)

        self.login()                      # the handshake belongs at connect time
        self.poller = Poller(host, web)
        self.poller.start()

        self.c = tk.Canvas(root, width=W, height=H, bg=BG, highlightthickness=0)
        self.c.pack()
        self.build()

        self.entry = tk.Entry(root, font=mono(13), bg=PANEL, fg=LAMP,
                              insertbackground=LAMP, relief="flat",
                              highlightthickness=1, highlightbackground=RULE,
                              highlightcolor=RULE)
        self.entry.place(x=150, y=H - 46, width=W - 380, height=28)
        self.entry.bind("<Return>", self.submit)
        for key, step in (("<Prior>", -1), ("<Next>", 1)):
            root.bind_all(key, lambda _e, d=step: self.show_rack(self.rack + d))
        self.entry.focus_set()

        root.after(200, self.refresh)

    # -------------------------------------------------------------- plumbing
    def login(self):
        """Answer the operator prompt now, so the first command is not eaten."""
        if not self.sock:
            return
        out, end = "", time.time() + 2.5
        while time.time() < end:
            ready, _, _ = select.select([self.sock], [], [], 0.2)
            if not ready:
                continue
            try:
                data = self.sock.recv(65536)
            except (BlockingIOError, OSError):
                return
            if not data:
                return
            out += data.decode("latin-1")
            if "OPERATOR ID" in out.upper():
                try:
                    self.sock.sendall(b"OPERATOR 1\n")
                except OSError:
                    return
                self.welcome(self.read_to_prompt(4.0))
                return
            if "OPR>" in out:
                return

    def read_to_prompt(self, seconds):
        out, end = "", time.time() + seconds
        while time.time() < end and "OPR>" not in out:
            ready, _, _ = select.select([self.sock], [], [], 0.15)
            if not ready:
                continue
            try:
                data = self.sock.recv(65536)
            except (BlockingIOError, OSError):
                break
            if not data:
                break
            out += data.decode("latin-1")
        return out.replace("OPR>", "")

    def welcome(self, text):
        """The sheet opens on the away report, if the machine sent one.

        The welcome line itself is not printed: the status strip already
        carries the tick and the cash."""
        rows = text.replace("\r\n", "\n").split("\n")
        start = next((i for i, r in enumerate(rows) if "WHILE YOU WERE AWAY" in r), None)
        if start is None:
            return
        for row in rows[start:]:
            self.log.append(("out", row.rstrip()))

    def submit(self, _e=None):
        line = self.entry.get().strip()
        self.entry.delete(0, "end")
        if not line:
            return
        if line.upper() in ("HELP", "?"):
            # the manual is its own sheet; the last answer stays where it was
            self.show_help(self.fetch("HELP"))     # a sheet, not a line of text
            return
        self.log.append(("cmd", line))
        answer = self.fetch(line, seconds=2.0)
        for row in answer.splitlines():
            self.log.append(("out", row.rstrip()))   # the engine's columns are the layout
        if line.split()[0].upper() in ("BYE", "QUIT", "EXIT", "LOGOFF"):
            # the engine hangs up on these; leave the sign-off on the paper for
            # a moment, then close, which also stops a world this window started
            self.entry.configure(state="disabled")
            self.root.after(1200, self.root.destroy)
            return
        self.read_socket()

    def set_tick(self, seconds):
        line = "tick %d" % seconds
        self.log.append(("cmd", line))
        for row in self.fetch(line, seconds=2.0).splitlines():
            self.log.append(("out", row.rstrip()))

    def fetch(self, command, seconds=1.5):
        """Send one command and collect the answer, for printing on paper."""
        if not self.sock:
            return "no line to the machine"
        try:
            self.sock.sendall((command + "\n").encode("latin-1"))
        except OSError:
            return "the line dropped"
        out, end = "", time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.sock], [], [], 0.15)
            if not ready:
                continue
            try:
                data = self.sock.recv(65536)
            except (BlockingIOError, OSError):
                break
            if not data:
                break
            out += data.decode("latin-1")
            if "OPR>" in out:
                break
        # strip newlines only: the first line's indent is part of the layout
        return out.replace("OPR>", "").strip("\r\n")

    def show_help(self, text):
        """The manual, printed on a sheet of paper the length of the manual."""
        self.show_sheet(text, "help_win", "operator's manual — SYS/370-145",
                        "N O R T H G A T E   ·   O P E R A T O R ' S   M A N U A L")

    def show_sheet(self, text, slot, title, heading):
        """A second sheet of paper in its own window, sized to its text."""
        if getattr(self, slot, None) is not None and getattr(self, slot).winfo_exists():
            getattr(self, slot).lift()
            return
        text = (text or "the machine sent nothing back").replace("\r\n", "\n").replace("\r", "\n")
        lines = text.count("\n") + 1
        pitch = getattr(self, "pitch", 18)
        cw = 880
        chh = 46 + lines * pitch + 44     # measured pitch, plus air for the footer
        top = max(0, min(70, self.root.winfo_screenheight() - chh - 40))
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=PAPER)
        win.resizable(False, False)
        win.geometry("%dx%d+90+%d" % (cw, chh, top))
        c = tk.Canvas(win, width=cw, height=chh, bg=PAPER, highlightthickness=0)
        c.pack()
        c.create_text(30, 24, anchor="w", fill=FADE, font=mono(10), text=heading)
        body = c.create_text(30, 46, anchor="nw", fill=INK, font=mono(11), justify="left",
                             width=cw - 58,
                             text=text or "the machine sent nothing back")
        for y in range(46 - 1 + pitch, chh - 30, pitch):   # ruled paper, under each line
            c.tag_lower(c.create_line(26, y, cw - 14, y, fill=PAPER_RULE))
        for y in range(42, chh - 20, 22):
            c.tag_lower(c.create_oval(10, y, 16, y + 6, fill=SHADOW, outline=""))
        c.create_text(30, chh - 20, anchor="w", fill=FADE, font=mono(9),
                      text="ESC closes this sheet")
        win.bind("<Escape>", lambda e: win.destroy())
        setattr(self, slot, win)

    def read_socket(self):
        if not self.sock:
            return
        try:
            data = self.sock.recv(65536)
        except (BlockingIOError, OSError):
            return
        if not data:
            self.note = "the line dropped"
            return
        text = data.decode("latin-1")
        if "OPERATOR ID" in text.upper():
            try:
                self.sock.sendall(b"OPERATOR 1\n")
            except OSError:
                pass
            return
        self.note = " ".join(text.split()).replace("OPR>", "").strip()[:140]

    # ---------------------------------------------------------------- chrome
    def panel(self, x0, y0, x1, y1, label=None):
        c = self.c
        c.create_rectangle(x0, y0, x1, y1, fill=PANEL, outline=RULE)
        if label:
            c.create_rectangle(x0, y0 - 1, x0 + 8 + 11 * len(label), y0 + 15,
                               fill=PANEL, outline="")
            c.create_text(x0 + 6, y0 + 7, anchor="w", text=label,
                          fill=STENCIL, font=mono(10))

    def screw(self, x, y):
        self.c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=BG, outline=RULE)

    def build(self):
        c = self.c
        face = tkfont.Font(family="DejaVu Sans Mono", size=-11, weight="bold")
        head = tkfont.Font(family="DejaVu Sans Mono", size=-12, weight="bold")
        self.body_chars = max(28, int((PX1 - PX0 - 52) / max(1, face.measure("M"))))
        # measured, not guessed: tk scales even pixel sizes on this display, so
        # the rules, the line budget and the help window all use the real pitch
        self.pitch = max(1, face.metrics("linespace"))
        self.head_adv = max(1, head.measure("M"))
        self.head_ls = head.metrics("linespace")
        body_y = 96 + 3 * self.head_ls + self.pitch // 2
        # printed lines that fit between the header and the rule above the goal
        self.body_room = max(6, (H - 216 - body_y) // self.pitch)
        c.create_rectangle(6, 6, W - 6, H - 6, outline=RULE, fill=BG)
        for x, y in ((14, 14), (W - 14, 14), (14, H - 14), (W - 14, H - 14)):
            self.screw(x, y)

        c.create_text(26, 26, anchor="w", fill=STENCIL, font=mono(12),
                      text="N O R T H G A T E")
        c.create_text(W - 26, 26, anchor="e", fill=STENCIL, font=mono(11),
                      text="S Y S / 3 7 0 - 1 4 5")
        c.create_rectangle(240, 21, 430, 31, fill=PANEL, outline=RULE)
        self.tick_fill = c.create_rectangle(241, 22, 241, 30, fill=LAMP, outline="")
        self.tick_label = c.create_text(438, 26, anchor="w", fill=STENCIL,
                                        font=mono(10), text="")
        self.tick_buttons = {}
        for i, secs in enumerate((1, 3, 10, 60)):
            x = 600 + i * 40
            rect = c.create_rectangle(x, 17, x + 34, 35, fill=PANEL, outline=RULE)
            txt = c.create_text(x + 17, 26, text=str(secs), fill=STENCIL, font=mono(10))
            for item in (rect, txt):
                c.tag_bind(item, "<Button-1>", lambda _e, n=secs: self.set_tick(n))
            self.tick_buttons[secs] = (rect, txt)
        c.create_line(26, 40, W - 26, 40, fill=RULE)

        # ---- module rack
        self.panel(LX0, 56, LX1, 452, "M O D U L E   L A M P S")
        self.mod_rows = []
        for i in range(RACK_MODULES):
            y = 88 + i * 44
            label = c.create_text(30, y, anchor="w", fill=STENCIL, font=mono(13),
                                  text="MOD %d" % (i + 1))
            dead = c.create_rectangle(139, y - 9, 389, y + 9, fill=PANEL, outline="")
            lamps = []
            for j in range(MAX_LAMPS):
                halo = c.create_oval(0, 0, 0, 0, fill=PANEL, outline="")
                lamp = c.create_oval(0, 0, 0, 0, fill=OFF, outline="")
                lamps.append((halo, lamp))
            tag = c.create_text(404, y, anchor="w", fill=FAULT, font=mono(11), text="")
            self.mod_rows.append({"label": label, "lamps": lamps, "y": y,
                                  "dead": dead, "tag": tag})
        self.lamp_layout = None
        self.layout_lamps(8)

        # rack tabs: one per cabinet the site has room for; PgUp/PgDn flip
        self.rack = 0
        self.racks = 1
        self.rack_tabs = []
        for k in range(10):
            x = LX1 - 30 - k * 28
            rect = c.create_rectangle(x, 60, x + 22, 76, fill=PANEL, outline=RULE,
                                      state="hidden")
            txt = c.create_text(x + 11, 68, text="", fill=STENCIL, font=mono(10),
                                state="hidden")
            for item in (rect, txt):
                c.tag_bind(item, "<Button-1>", lambda _e, n=k: self.show_rack(n))
            self.rack_tabs.append((rect, txt))
        self.rack_hint = c.create_text(0, 68, anchor="e", text="", fill="#4a5240",
                                       font=mono(9))
        self.rack_empty = c.create_text(LX0 + 308, 250, text="", fill="#4a5240",
                                        font=mono(11))

        # ---- cores / load
        self.panel(LX0, 462, LX1, 520)
        self.cores_text = c.create_text(30, 480, anchor="w", fill=STENCIL,
                                        font=mono(12), text="")
        c.create_rectangle(30, 494, 330, 506, outline=RULE, fill=BG)
        self.load_bar = c.create_rectangle(31, 495, 31, 505, fill=LAMP, outline="")
        self.load_text = c.create_text(340, 500, anchor="w", fill=FADE, font=mono(10), text="")

        # ---- lines
        self.panel(LX0, 530, LX1, H - 62, "D I A L - I N   L I N E S")
        self.line_lamps = []
        for r in range(RACK_LINES // 8):
            for i in range(8):
                x = 42 + i * 74
                y = 560 + r * 24
                halo = c.create_oval(x - 8, y - 8, x + 8, y + 8, fill=PANEL, outline="")
                lamp = c.create_oval(x - 5, y - 5, x + 5, y + 5, fill=OFF, outline="")
                cross = (c.create_line(x - 6, y - 6, x + 6, y + 6, fill=FAULT, width=3,
                                       state="hidden"),
                         c.create_line(x - 6, y + 6, x + 6, y - 6, fill=FAULT, width=3,
                                       state="hidden"))
                num = c.create_text(x + 16, y, anchor="w", fill=OFF, font=mono(9), text="")
                self.line_lamps.append((halo, lamp, num, cross))
        self.turned = c.create_text(470, 480, anchor="w", fill=OFF,
                                    font=mono(10), text="")

        # ---- the sheet of paper
        c.create_rectangle(PX0 + 3, 59, PX1 + 3, H - 59, fill=SHADOW, outline="")
        c.create_rectangle(PX0, 56, PX1, H - 62, fill=PAPER, outline=SHADOW)
        y = body_y - 1 + self.pitch            # each rule sits under a printed line
        while y - self.pitch > 60:
            y -= self.pitch
        while y <= H - 70:
            c.create_line(PX0 + 26, y, PX1 - 12, y, fill=PAPER_RULE)
            y += self.pitch
        for i in range(24):
            y = 70 + i * 22
            if y > H - 74:
                break
            c.create_oval(PX0 + 9, y, PX0 + 15, y + 6, fill=SHADOW, outline="")

        self.p_head = c.create_text(PX0 + 30, 74, anchor="w", fill=FADE, font=mono(10),
                                    text="NORTHGATE  TELEPRINTER   6 LPI")
        self.p_main = c.create_text(PX0 + 30, 96, anchor="nw", fill=INK, font=mono(12),
                                    text="", width=PX1 - PX0 - 50, justify="left")
        # the fault count rides beside CASH; placed in paint(), after the cash figure
        self.fault_text = c.create_text(PX0 + 30, 96 + self.head_ls // 2, anchor="w",
                                        fill=FADE, font=mono(10), text="")
        # a waiting decision and work under way, in ink: neither is a fault
        self.decision_text = c.create_text(PX0 + 30, 96 + self.head_ls * 3 // 2, anchor="w",
                                           fill=INK, font=mono(10), text="")
        self.project_text = c.create_text(PX0 + 30, 96 + self.head_ls * 5 // 2, anchor="w",
                                          fill=FADE, font=mono(10), text="")
        self.p_body = c.create_text(PX0 + 30, body_y, anchor="nw", fill=INK, font=mono(11),
                                    text="", width=PX1 - PX0 - 50, justify="left")
        c.create_line(PX0 + 30, H - 208, PX1 - 24, H - 208, fill=FADE)
        self.goal_label = c.create_text(PX0 + 30, H - 190, anchor="w", fill=FADE,
                                        font=mono(10), text="")
        c.create_rectangle(PX0 + 30, H - 172, PX1 - 24, H - 152, fill=PAPER_RULE, outline="")
        self.goal_bar = c.create_rectangle(PX0 + 30, H - 172, PX0 + 30, H - 152, fill=INK, outline="")
        self.goal_text = c.create_text(PX0 + 30, H - 136, anchor="w", fill=INK,
                                       font=mono(10), text="")

        c.create_text(W - 26, H - 28, anchor="e", fill="#323828", font=mono(9),
                      text=BUILD)
        c.create_line(26, H - 58, W - 26, H - 58, fill=RULE)
        c.create_text(30, H - 28, anchor="w", fill=STENCIL, font=mono(13), text="O P R ›")
        self.note_text = c.create_text(W - 30, H - 28, anchor="e", fill=FADE,
                                       font=mono(10), text="")

    def wrap(self, row):
        """Fold a line to the sheet's width, continuing under its own indent."""
        if len(row) <= self.body_chars:
            return [row]
        if len(set(row.strip())) == 1:          # a rule of dashes is cut, not folded
            return [row[:self.body_chars]]
        lead = len(row) - len(row.lstrip())
        return textwrap.wrap(row, self.body_chars, subsequent_indent=" " * (lead + 2),
                             break_long_words=True) or [row[:self.body_chars]]

    def layout_lamps(self, per_core):
        """One lamp per caller the module carries; more callers, smaller lamps."""
        per_core = max(1, min(MAX_LAMPS, per_core))
        if self.lamp_layout == per_core:
            return
        self.lamp_layout = per_core
        pitch = min(30.0, 240.0 / per_core)
        r_lamp, r_halo = min(7.0, pitch * 0.3), min(11.0, pitch * 0.46)
        for row in self.mod_rows:
            y = row["y"]
            for j, (halo, lamp) in enumerate(row["lamps"]):
                x = 150 + j * pitch
                self.c.coords(halo, x - r_halo, y - r_halo, x + r_halo, y + r_halo)
                self.c.coords(lamp, x - r_lamp, y - r_lamp, x + r_lamp, y + r_lamp)
                state = "normal" if j < per_core else "hidden"
                self.c.itemconfigure(halo, state=state)
                self.c.itemconfigure(lamp, state=state)

    def show_rack(self, n):
        self.rack = max(0, min(self.racks - 1, n))
        if self.poller.state:
            self.paint(self.poller.state)

    # --------------------------------------------------------------- painting
    def refresh(self):
        if self.poller.state:
            self.paint(self.poller.state)
            self.watch_statement(self.poller.state)
        self.c.itemconfigure(self.note_text, text=self.note[-140:])
        self.root.after(200, self.refresh)

    def watch_statement(self, s):
        """Going broke is an ending: print the final statement on its own sheet
        the moment it happens. One that was printed before this window opened
        is left to the away report."""
        sid = int(s.get("statement", 0))
        if getattr(self, "statement_seen", None) is None:
            self.statement_seen = sid
        elif sid > self.statement_seen:
            self.statement_seen = sid
            self.show_sheet(self.fetch("STATEMENT"), "statement_win",
                            "final statement — SYS/370-145",
                            "N O R T H G A T E   ·   F I N A L   S T A T E M E N T")

    def paint(self, s):
        c = self.c
        cores = int(s.get("cores", 0))
        working = int(s.get("working_cores", cores))
        per_core = int(s.get("callers_per_core", 8)) or 8
        served = int(s.get("served", 0))
        lines = int(s.get("lines", 0))
        working_lines = int(s.get("working_lines", lines))
        demand = int(s.get("demand", 0))
        faults = s.get("faults") or []
        down_mods = {f["n"] for f in faults if f.get("kind") == "module"}
        down_lines = {f["n"] for f in faults if f.get("kind") == "line"}

        room = s.get("room") or {}
        room_m = int(room.get("modules", RACK_MODULES))
        room_l = int(room.get("lines", RACK_LINES))
        self.racks = max(1, min(len(self.rack_tabs), max(
            math.ceil(max(cores, room_m) / RACK_MODULES),
            math.ceil(max(lines, room_l) / RACK_LINES))))
        self.rack = min(self.rack, self.racks - 1)
        self.layout_lamps(per_core)

        # callers fill working modules lowest first, across every rack
        loads, left = {}, served
        for n in range(1, cores + 1):
            if n not in down_mods:
                loads[n] = min(per_core, max(0, left))
                left -= loads[n]

        first_mod = self.rack * RACK_MODULES
        slots_here = 0
        for i, row in enumerate(self.mod_rows):
            n = first_mod + i + 1
            installed = n <= cores
            dead = n in down_mods
            load = loads.get(n, 0)
            has_slot = n <= max(cores, room_m)
            slots_here += has_slot
            vis = "normal" if has_slot else "hidden"
            for item in (row["label"], row["dead"], row["tag"]):
                c.itemconfigure(item, state=vis)
            c.itemconfigure(row["label"], text="MOD %d" % n)
            if not has_slot:
                for halo, lamp in row["lamps"]:
                    c.itemconfigure(halo, state="hidden")
                    c.itemconfigure(lamp, state="hidden")
                continue
            c.itemconfigure(row["label"], fill=STENCIL if installed else "#323828")
            if installed and dead:
                c.itemconfigure(row["dead"], fill=FAULT, outline="")
                c.itemconfigure(row["tag"], text="F A U L T", fill=FAULT)
            else:
                c.itemconfigure(row["dead"], fill=PANEL, outline="")
                c.itemconfigure(row["tag"],
                                text="" if installed else "spare",
                                fill="#4a5240")
            for j, (halo, lamp) in enumerate(row["lamps"]):
                on = installed and not dead and j < load
                if dead or j >= per_core:
                    c.itemconfigure(lamp, state="hidden")
                    c.itemconfigure(halo, state="hidden")
                    continue
                c.itemconfigure(lamp, state="normal", fill=LAMP if on else OFF)
                c.itemconfigure(halo, state="normal", fill=GLOW if on else PANEL)
        c.itemconfigure(self.rack_empty,
                        text="" if slots_here else "NO MODULE SLOTS IN THIS RACK")

        # tabs: lit for the rack on show, red if a rack you cannot see has a fault
        for k, (rect, txt) in enumerate(self.rack_tabs):
            if k >= self.racks or self.racks == 1:
                c.itemconfigure(rect, state="hidden")
                c.itemconfigure(txt, state="hidden")
                continue
            mods = range(k * RACK_MODULES + 1, (k + 1) * RACK_MODULES + 1)
            lns = range(k * RACK_LINES + 1, (k + 1) * RACK_LINES + 1)
            faulty = any(m in down_mods for m in mods) or any(n in down_lines for n in lns)
            x = LX1 - 30 - (self.racks - 1 - k) * 28
            c.coords(rect, x, 60, x + 22, 76)
            c.coords(txt, x + 11, 68)
            active = k == self.rack
            c.itemconfigure(rect, state="normal", fill=LAMP if active else PANEL,
                            outline=FAULT if faulty and not active else RULE)
            c.itemconfigure(txt, state="normal", text=chr(ord("A") + k),
                            fill=BG if active else (FAULT if faulty else STENCIL))
        c.coords(self.rack_hint, LX1 - 36 - (self.racks - 1) * 28, 68)
        c.itemconfigure(self.rack_hint, text="RACK  PGUP/PGDN" if self.racks > 1 else "")

        # the engine's capacity is min(modules, lines): modules with no lines to
        # feed them add nothing, and the bar has to show that, not headroom
        capacity = int(s.get("capacity", working * per_core))
        pct = int(100 * served / max(1, capacity))
        c.itemconfigure(self.cores_text,
                        text="C O R E S   %d        L O A D   %d%%" % (cores, pct))
        c.coords(self.load_bar, 31, 495, 31 + int(2.99 * min(100, pct)), 505)
        c.itemconfigure(self.load_text, text="%d of %d callers" % (served, capacity))

        # a caller takes the lowest working line: the busy ones lead the field
        carrying = min(served, working_lines)
        busy = set()
        for n in range(1, lines + 1):
            if len(busy) >= carrying:
                break
            if n not in down_lines:
                busy.add(n)
        first_line = self.rack * RACK_LINES
        for idx, (halo, lamp, num, cross) in enumerate(self.line_lamps):
            n = first_line + idx + 1
            dead = n in down_lines and n <= lines
            for stroke in cross:
                c.itemconfigure(stroke, state="normal" if dead else "hidden")
            if n > max(lines, room_l):
                for item in (halo, lamp):
                    c.itemconfigure(item, state="hidden")
                c.itemconfigure(num, text="")
                continue
            for item in (halo, lamp):
                c.itemconfigure(item, state="hidden" if dead else "normal")
            if n > lines:                       # room for it, not bought
                c.itemconfigure(lamp, fill=OFF)
                c.itemconfigure(halo, fill=PANEL)
                c.itemconfigure(num, text="", fill=OFF)
                continue
            c.itemconfigure(num, text="%02d" % n,
                            fill=FAULT if dead else (FADE if n in busy else "#4a5240"))
            on = n in busy
            c.itemconfigure(lamp, fill=LAMP if on else OFF)
            c.itemconfigure(halo, fill=GLOW if on else PANEL)

        turned = max(0, demand - served)
        c.itemconfigure(self.turned, text="TURNED AWAY  %d" % turned,
                        fill=FAULT if turned else "#4a5240")

        net = s.get("net", 0.0)
        first = "TICK %d        CASH %s" % (s.get("tick", 0), "{:,.2f}".format(s.get("cash", 0.0)))
        c.itemconfigure(self.p_main, text=(
            first + "\n"
            "NET %+.2f      LINES %d/%d\n"
            "CALLERS %d      REFUSED %d") % (
            net, working_lines, lines, served, turned))
        side = PX0 + 30 + (len(first) + 3) * self.head_adv
        c.coords(self.fault_text, side, 96 + self.head_ls // 2)
        c.coords(self.decision_text, side, 96 + self.head_ls * 3 // 2)
        c.coords(self.project_text, side, 96 + self.head_ls * 5 // 2)
        inc, proj = s.get("incident"), s.get("project")
        c.itemconfigure(self.decision_text, text="DECISION WAITING" if inc else "")
        c.itemconfigure(self.project_text, text=(
            "WORKS %d%%" % int(100 * (proj["total"] - proj["left"]) / max(1, proj["total"]))
            if proj else ""))

        room = self.body_room
        rows = list(self.log)
        last = max((i for i, (kind, _t) in enumerate(rows) if kind == "cmd"), default=-1)
        if rows:
            # from the last command on; before any command, the away report
            shown = []
            for kind, text in rows[max(0, last):]:
                row = ("› " + text) if kind == "cmd" else text
                shown.extend(self.wrap(row))
            while len(shown) > 1 and not shown[-1].strip():
                shown.pop()
            if len(shown) > room:
                cut = len(shown) - room + 1
                shown = shown[:room - 1] + ["  … %d MORE LINE%s NOT PRINTED"
                                            % (cut, "" if cut == 1 else "S")]
            body = shown
        else:
            waiting = sum(1 for o in s.get("offers") or [] if o.get("state") == "offered")
            body = ["N O   C O M M A N D   Y E T", ""]
            if s.get("incident"):
                body += self.wrap("A DECISION IS WAITING: TYPE INCIDENT.")
            if waiting:
                body += self.wrap("A TELEX IS WAITING ON THE TELEPRINTER: TYPE OFFERS.")
            body += self.wrap("TYPE HELP FOR THE MANUAL, OR STATUS FOR THE SYSTEM REPORT.")
        c.itemconfigure(self.p_body, text="\n".join(body))

        if faults:
            c.itemconfigure(self.fault_text,
                            text="%d FAULT%s" % (len(faults), "" if len(faults) == 1 else "S"),
                            fill=FAULT)
        else:
            c.itemconfigure(self.fault_text, text="PLANT CLEAR", fill=FADE)

        ts = float(s.get("tick_seconds") or 60)
        # count down from when the state was read; past the deadline the next
        # tick has landed, so keep running into the following cycle instead of
        # sitting full until the next poll arrives
        due = float(s.get("_fetched", time.time())) + float(s.get("to_next", 0))
        remaining = (due - time.time()) % ts or ts
        span = 430 - 241
        c.coords(self.tick_fill, 241, 22, 241 + int(span * (1 - remaining / ts)), 30)
        c.itemconfigure(self.tick_label, text="%ds  ·  next in %ds" % (int(ts), int(remaining + 0.9)))
        for secs, (rect, txt) in self.tick_buttons.items():
            active = abs(secs - ts) < 0.5
            c.itemconfigure(rect, fill=LAMP if active else PANEL, outline=RULE)
            c.itemconfigure(txt, fill=BG if active else STENCIL)

        goal = s.get("goal") or {}
        gpct = max(0.0, min(100.0, float(goal.get("pct", 0))))
        span = (PX1 - 24) - (PX0 + 30)
        c.coords(self.goal_bar, PX0 + 30, H - 172,
                 PX0 + 30 + int(span * min(1.0, gpct / 100.0)), H - 152)
        c.itemconfigure(self.goal_label, text="G O A L   ·   %s" % s.get("site", ""))
        c.itemconfigure(self.goal_text, text="%.1f%% of %s  ·  %s to go" % (
            gpct, "{:,.0f}".format(goal.get("price", 0)),
            "{:,.0f}".format(max(0.0, goal.get("price", 0) - s.get("cash", 0.0)))))


def state_answers(web, host="127.0.0.1"):
    try:
        with urllib.request.urlopen("http://%s:%d/state" % (host, web), timeout=1) as r:
            return bool(json.loads(r.read().decode()))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def ensure_world(host, port, web, db, tick):
    """One launcher: if nothing is answering, start a world and own it.

    Returns the child process when this script started one, else None, so the
    window can stop it again on exit and never leaves a server behind.
    """
    if state_answers(web, host):
        return None
    if host not in ("127.0.0.1", "localhost"):
        return None                      # a remote world is not ours to start
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, "main.py"),
           "--port", str(port), "--web", str(web)]
    if db:
        cmd += ["--db", db]
    if tick:
        cmd += ["--tick", str(tick)]
    child = subprocess.Popen(cmd, cwd=here, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    for _ in range(60):                  # up to twelve seconds
        if state_answers(web, host):
            return child
        if child.poll() is not None:
            return None                  # it refused to start; the window says so
        threading.Event().wait(0.2)
    return child


def parse():
    import argparse
    ap = argparse.ArgumentParser(description="timeshare, in one window")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2023, help="command port")
    ap.add_argument("--web", type=int, default=2024, help="state port")
    ap.add_argument("--db", default=None, help="save file (default: the server's own)")
    ap.add_argument("--tick", type=int, default=None, help="seconds per tick")
    return ap.parse_args()


def log_launch(root):
    try:
        with open(__file__, "rb") as fh:
            digest = hashlib.md5(fh.read()).hexdigest()[:8]
        line = "%.0f build=%s file=%s md5=%s tk_scale=%.3f screen=%dx%d win=%s argv=%s\n" % (
            time.time(), BUILD, os.path.abspath(__file__), digest,
            root.tk.call("tk", "scaling"),
            root.winfo_screenwidth(), root.winfo_screenheight(),
            root.winfo_geometry(), " ".join(sys.argv[1:]))
        with open(LOG, "a") as fh:
            fh.write(line)
    except Exception as exc:              # never let logging break the game
        print("log failed:", exc, file=sys.stderr)


def main():
    args = parse()
    child = ensure_world(args.host, args.port, args.web, args.db, args.tick)

    def stop_child():
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()

    atexit.register(stop_child)

    root = tk.Tk()
    app = Machine(root, args.host, args.port, args.web)
    root.after(600, lambda: log_launch(root))
    root.bind("<Escape>", lambda e: root.destroy())
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: root.destroy())   # so the world goes with the window
    try:
        root.mainloop()
    finally:
        app.poller.stop = True
        stop_child()


if __name__ == "__main__":
    main()
