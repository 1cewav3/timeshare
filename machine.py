#!/usr/bin/env python3
"""TIMESHARE: the machine, in one window.

    python3 machine.py                      # play; the save lives in the usual place
    python3 machine.py --db /tmp/demo.db    # a different save

The world runs inside this window's own process: nothing listens on the network.
Commands go straight to the engine, and every number drawn here comes from the
engine's snapshot. Nothing on the canvas is invented — if the engine does not
report it, it is not shown.
"""
import collections
import json
import math
import os
import signal
import sys
import textwrap
import time
import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox

import main as engine

# ------------------------------------------------------------------ palette
BG = "#0b0d09"
PANEL = "#12150e"
RULE = "#3a4230"
STENCIL = "#95a289"
LAMP = "#a8eb9a"
GLOW = "#1d2b18"
OFF = "#242a1e"
FAULT = "#dc4a3c"
BOOST = "#8cb8f0"            # places the staff squeeze out of the machine
BOOST_GLOW = "#16223a"
TAKEN = "#e8c95a"            # places the works have taken out of service
TAKEN_GLOW = "#2c2610"
FAULT_BG = "#2b1512"
PAPER = "#e9e3d2"
PAPER_RULE = "#d5cdb6"
INK = "#231f19"
FADE = "#8d856f"
SHADOW = "#b9b19c"

W, H = 1180, 700
RACK_MODULES, RACK_LINES = 8, 32   # one rack page: eight module slots, 32 line slots
MAX_LAMPS = 26                     # the most callers any generation of module carries
MAX_BOOST = 4                      # blue lamps drawn after a row before it says +n
BUILD = "v" + engine.VERSION
FAMILY = "DejaVu Sans Mono"        # replaced by pick_font() with what this machine has
# Monospace faces, best first. Menlo shares DejaVu's letter widths; Consolas is a
# little narrower, so text only gains room. The layout measures what it gets.
FONTS = ["DejaVu Sans Mono", "Menlo", "Consolas", "Liberation Mono", "Monaco",
         "Lucida Console", "Courier New", "Courier"]


def pick_font(root):
    """Use the first monospace face this machine has. A face Tk does not have
    is silently swapped for a proportional one, which wrecks every column."""
    global FAMILY
    have = set(tkfont.families(root))
    FAMILY = next((f for f in FONTS if f in have), "TkFixedFont")


def resource(name):
    """A file shipped with the game, whether run from source or packaged."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)
LOG = "/tmp/timeshare-launch.log"
PX0, PX1 = 648, 1164          # paper sheet
LX0, LX1 = 16, 632            # left column


def mono(size, bold=True):
    """Pixel sizes, deliberately negative.

    A positive size is in points, which tk multiplies by its scaling factor
    (1.778 on this display) and blows the layout apart. Negative means pixels,
    so the text keeps the size the coordinates were designed for.
    """
    return (FAMILY, -size, "bold" if bold else "normal")


class Board:
    """The latest snapshot of the world, and when it was taken."""
    def __init__(self):
        self.state = {}
        self.fetched = 0.0
        self.stop = False


class Machine:
    def __init__(self, root, session):
        self.root = root
        self.session = session
        pick_font(root)
        try:
            self.icon = tk.PhotoImage(file=resource(os.path.join("assets", "icon.png")))
            root.iconphoto(True, self.icon)
        except (tk.TclError, OSError):
            pass                                   # no icon is no reason to stop
        self.note = ""
        self.log = collections.deque(maxlen=40)   # what you typed, and what came back
        root.title("timeshare — SYS/370-145")
        root.configure(bg=BG)
        root.resizable(False, False)
        self.poller = Board()                     # refreshed from the session each frame
        self.closing = False

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

        if sys.platform != "darwin" and root.winfo_screenheight() < H + 120:
            # a 1366x768 laptop: the window and its title bar only just fit
            # above the taskbar, so open it at the top, where the prompt shows.
            # Not on macOS, which has no taskbar to clear and shrank the
            # window to 200 by 200 when it was placed like this.
            root.geometry("%dx%d+%d+0" % (W, H, max(0, (root.winfo_screenwidth() - W) // 2)))
        elif sys.platform == "darwin":
            # on a Mac screen narrower than the window, Tk falls back to its
            # default 200 by 200; stating the size keeps the whole window
            root.geometry("%dx%d" % (W, H))
        if os.environ.get("TIMESHARE_DIAG"):
            # the release check asks: what screen, what window, what was asked for
            root.after(3000, lambda: print(
                "DIAG screen %dx%d window %dx%d at %d,%d requested %dx%d scaling %.3f font %s"
                % (root.winfo_screenwidth(), root.winfo_screenheight(), root.winfo_width(),
                   root.winfo_height(), root.winfo_x(), root.winfo_y(), root.winfo_reqwidth(),
                   root.winfo_reqheight(), float(root.tk.call("tk", "scaling")), FAMILY),
                flush=True))

        root.after(200, self.refresh)

    # -------------------------------------------------------------- plumbing
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
        word = line.split()[0].upper()
        if word in ("HELP", "?"):
            # the manual is its own sheet, a tab at a time; the last answer stays
            self.open_help(" ".join(line.split()[1:]).upper())
            return
        if word == "BUY":
            # buying is done on the order form, not typed
            self.open_form("buy_form", "order form — SYS/370-145",
                           "N O R T H G A T E   ·   P U R C H A S E   O R D E R", build_buy)
            return
        if word == "STAFF" or word == "BANK":
            if word == "STAFF":
                self.open_form("staff_form", "staff — SYS/370-145",
                               "N O R T H G A T E   ·   T H E   S T A F F", build_staff,
                               watch=("cash", "roles"))
            else:
                self.open_form("bank_form", "the bank — SYS/370-145",
                               "N O R T H G A T E   ·   T H E   B A N K   M A N A G E R",
                               build_bank, watch=("cash", "bank", "tick_seconds"))
            return
        if word in ("SETTINGS", "SETTING", "SET", "OPTIONS"):
            self.open_form("settings_form", "settings — SYS/370-145",
                           "N O R T H G A T E   ·   O P E R A T I N G   S E T T I N G S",
                           build_settings)
            return
        self.log.append(("cmd", line))
        answer = self.fetch(line, seconds=2.0)
        for row in answer.splitlines():
            self.log.append(("out", row.rstrip()))   # the engine's columns are the layout
        if line.split()[0].upper() in ("BYE", "QUIT", "EXIT", "LOGOFF"):
            # the engine hangs up on these; leave the sign-off on the paper for
            # a moment, then close, which also stops a world this window started
            self.entry.configure(state="disabled")
            self.root.after(1200, self.quit_soon)
            return

    def set_tick(self, seconds):
        line = "tick %d" % seconds
        self.log.append(("cmd", line))
        for row in self.fetch(line, seconds=2.0).splitlines():
            self.log.append(("out", row.rstrip()))

    def fetch(self, command, seconds=None):
        """Run one command on the engine and return its answer, for the paper."""
        try:
            out, _closing = self.session.command(command)
        except Exception as exc:                   # a bug should not take the window down
            return "  THE MACHINE FAULTED ON THAT COMMAND: %s" % exc
        # strip newlines only: the first line's indent is part of the layout
        return out.replace("\r\n", "\n").strip("\n")

    def open_form(self, slot, title, heading, builder, watch=None):
        form = getattr(self, slot, None)
        if form is not None and form.alive():
            form.lift()
            return
        Form(self, slot, title, heading, builder, watch=watch or Form.WATCH).redraw(force=True)

    def open_help(self, want=""):
        """The manual on its own sheet, opened at the tab asked for, if any."""
        form = getattr(self, "help_form", None)
        if form is None or not form.alive():
            form = Form(self, "help_form", "operator's manual — SYS/370-145",
                        "N O R T H G A T E   ·   O P E R A T O R ' S   M A N U A L",
                        build_help, watch=(), footer=False)
            form.hint = "ESC closes this sheet   ·   LEFT / RIGHT OR PGUP / PGDN TURN THE TABS"

            def turn(d):
                keys = [k for k, _t in getattr(form, "tabs", [])] or ["DAY"]
                here = form.qty.get("tab", keys[0])
                form.qty["tab"] = keys[(keys.index(here) + d) % len(keys)]
                form.redraw(force=True)
            for key, d in (("<Left>", -1), ("<Right>", 1), ("<Prior>", -1), ("<Next>", 1)):
                form.win.bind(key, lambda _e, d=d: turn(d))
            form.redraw(force=True)                # fetches the tabs
        if want:
            want = want.replace("THE ", "")
            for k, title in getattr(form, "tabs", []):
                if want in (k, title, title.replace("THE ", "")):
                    form.qty["tab"] = k
        form.redraw(force=True)
        form.lift()

    def show_effects(self):
        """Every trade, + and -, on its own sheet; reopening prints it fresh."""
        old = getattr(self, "effects_win", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        self.show_sheet(self.fetch("EFFECTS"), "effects_win",
                        "effects — SYS/370-145",
                        "N O R T H G A T E   ·   W H A T   P U S H E S   T H E   B U S I N E S S")

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
        # the ledger of every trade: what pushes the business up or down
        rect = c.create_rectangle(790, 17, 900, 35, fill=PANEL, outline=RULE)
        txt = c.create_text(845, 26, text="E F F E C T S", fill=STENCIL, font=mono(10))
        for item in (rect, txt):
            c.tag_bind(item, "<Button-1>", lambda _e: self.show_effects())
        c.create_line(26, 40, W - 26, 40, fill=RULE)

        # ---- module rack
        self.panel(LX0, 56, LX1, 452, "M O D U L E   L A M P S")
        self.mod_rows = []
        for i in range(RACK_MODULES):
            y = 88 + i * 44
            label = c.create_text(30, y - 6, anchor="w", fill=STENCIL, font=mono(13),
                                  text="MOD %d" % (i + 1))
            dead = c.create_rectangle(139, y - 9, 389, y + 9, fill=PANEL, outline="")
            lamps = []
            for j in range(MAX_LAMPS + MAX_BOOST):
                halo = c.create_oval(0, 0, 0, 0, fill=PANEL, outline="")
                lamp = c.create_oval(0, 0, 0, 0, fill=OFF, outline="")
                lamps.append((halo, lamp))
            tag = c.create_text(404, y, anchor="w", fill=FAULT, font=mono(11), text="")
            info = c.create_text(30, y + 10, anchor="w", fill="#4a5240", font=mono(9), text="")
            self.mod_rows.append({"label": label, "lamps": lamps, "y": y,
                                  "dead": dead, "tag": tag, "info": info})
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
                y = 554 + r * 22          # four rows inside the panel
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
        self.lamp_pitch = pitch

    def show_rack(self, n):
        self.rack = max(0, min(self.racks - 1, n))
        if self.poller.state:
            self.paint(self.poller.state)

    # --------------------------------------------------------------- painting
    def quit_soon(self):
        """Close after whatever is drawing now has finished. A quit request (the
        OS at logout, Ctrl-C) can land in the middle of a refresh; tearing the
        window down there leaves the rest of the refresh drawing on nothing."""
        if not self.closing:
            self.closing = True
            self.root.after(0, self.root.destroy)

    def refresh(self):
        """Run what is due, take a fresh snapshot, paint. While a long absence
        is being caught up, the sheet says so; then it opens on the report."""
        if self.closing:
            return
        self.session.step()
        if not self.session.catching_up() and not self.session.welcomed:
            self.welcome(self.session.welcome())
        state = self.session.snapshot()
        state["_fetched"] = self.poller.fetched = time.time()
        self.poller.state = state
        if self.poller.state:
            self.paint(self.poller.state)
            self.watch_statement(self.poller.state)
            for slot in ("buy_form", "settings_form", "staff_form", "bank_form"):
                form = getattr(self, slot, None)
                if form is not None and form.alive():
                    form.redraw()
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

        # ---- the wiring. Each module's lamps are wired to a block of lines:
        # MOD 1 to the first lines, MOD 2 to the next, one lamp per caller the
        # module's model carries. Callers fill the lamps whose module and line
        # both work. A dead line turns its lamp red; if a spare line beyond the
        # wired block picks up that caller, the lamp stays lit in a red ring.
        mods = list(s.get("modules") or [])
        while len(mods) < cores:
            mods.append({"model": "", "users": per_core, "wear": 1.0, "age": 0})
        users = [max(1, int(m.get("users", per_core))) for m in mods[:cores]]
        away = s.get("servicing")
        first_slot, k = {}, 0
        for n in range(1, cores + 1):
            first_slot[n] = k
            k += users[n - 1]
        total_slots = k

        def module_up(n):
            return n not in down_mods and n != away

        raw = int(s.get("capacity_raw", working * per_core))
        cap = int(s.get("capacity", raw))
        avail, red = [], []
        for n in range(1, cores + 1):
            if not module_up(n):
                continue
            for j in range(users[n - 1]):
                line = first_slot[n] + j + 1
                if line > lines:
                    continue
                (red if line in down_lines else avail).append((n, j))
        callers = min(served, raw)
        lit = set(avail[:callers])
        rerouted = set(red[:max(0, callers - len(lit))])
        taken = set(avail[len(lit):len(lit) + max(0, raw - cap)])
        busy = {first_slot[n] + j + 1 for n, j in lit}
        # every other caller (a dead line's caller, or one the staff squeezed in)
        # rides a free working line: past the wired block, or one whose module
        # is down or out for service
        free = [ln for ln in range(1, lines + 1) if ln not in down_lines and ln not in busy]
        busy |= set(free[:max(0, min(served, working_lines) - len(busy))])

        # blue: places the staff squeeze out, a few per busy module
        extra = max(0, served - raw)
        boost, rest = {}, extra
        busy_mods = sorted({n for n, _j in lit | rerouted})
        while rest and busy_mods and any(boost.get(n, 0) < MAX_BOOST for n in busy_mods):
            for n in busy_mods:
                if rest and boost.get(n, 0) < MAX_BOOST:
                    boost[n] = boost.get(n, 0) + 1
                    rest -= 1
        last_busy = max(busy_mods, default=0)

        first_mod = self.rack * RACK_MODULES
        page = [users[n - 1] for n in range(first_mod + 1, min(cores, first_mod + RACK_MODULES) + 1)]
        self.layout_lamps(max(page or [per_core]))
        pitch = getattr(self, "lamp_pitch", 30)
        slots_here = 0
        for i, row in enumerate(self.mod_rows):
            n = first_mod + i + 1
            installed = n <= cores
            dead = n in down_mods
            has_slot = n <= max(cores, room_m)
            slots_here += has_slot
            vis = "normal" if has_slot else "hidden"
            for item in (row["label"], row["dead"], row["tag"], row["info"]):
                c.itemconfigure(item, state=vis)
            c.itemconfigure(row["label"], text="MOD %d" % n)
            if not has_slot:
                for halo, lamp in row["lamps"]:
                    c.itemconfigure(halo, state="hidden")
                    c.itemconfigure(lamp, state="hidden")
                continue
            own = users[n - 1] if installed else per_core
            c.itemconfigure(row["label"], fill=STENCIL if installed else "#323828")
            if installed and dead:
                c.itemconfigure(row["dead"], fill=FAULT, outline="")
                c.itemconfigure(row["tag"], text="F A U L T", fill=FAULT)
            elif installed and n == away:
                c.itemconfigure(row["dead"], fill=PANEL, outline="")
                c.itemconfigure(row["tag"], text="IN SERVICE", fill=TAKEN)
            else:
                c.itemconfigure(row["dead"], fill=PANEL, outline="")
                c.itemconfigure(row["tag"], text="" if installed else "spare",
                                fill="#4a5240")
            blue = boost.get(n, 0)
            c.coords(row["tag"], max(404, 150 + (own + blue - 1) * pitch + 16), row["y"])
            if installed and not dead and n == last_busy and rest:
                c.itemconfigure(row["tag"], text="+%d" % rest, fill=BOOST)
            # model and age, faint; brighter once it is worn enough to matter
            if installed:
                m = mods[n - 1]
                age = int(m.get("age", 0))
                age_s = "%dD%02d" % (age // 1440, age % 1440 // 60) if age >= 1440 \
                    else "%dH%02d" % (age // 60, age % 60)
                worn = float(m.get("wear", 1.0)) >= 2.0     # brighter: worth a SERVICE
                c.itemconfigure(row["info"], text="%s %s" % (m.get("model", ""), age_s),
                                fill=STENCIL if worn else "#4a5240")
            else:
                c.itemconfigure(row["info"], text="")
            for j, (halo, lamp) in enumerate(row["lamps"]):
                if dead or j >= own + blue:
                    c.itemconfigure(lamp, state="hidden")
                    c.itemconfigure(halo, state="hidden")
                    continue
                if j >= own:                      # squeezed out by the staff
                    c.itemconfigure(lamp, state="normal", fill=BOOST, outline="")
                    c.itemconfigure(halo, state="normal", fill=BOOST_GLOW)
                    continue
                if not installed:
                    c.itemconfigure(lamp, state="normal", fill=OFF, outline="")
                    c.itemconfigure(halo, state="normal", fill=PANEL)
                    continue
                line = first_slot[n] + j + 1
                if n == away or (n, j) in taken:   # out of service for the works
                    fill, glow, ring = TAKEN, TAKEN_GLOW, ""
                elif (n, j) in rerouted:           # its line is dead; a spare carries it
                    fill, glow, ring = LAMP, FAULT_BG, FAULT
                elif line <= lines and line in down_lines:
                    fill, glow, ring = FAULT, FAULT_BG, ""
                elif (n, j) in lit:
                    fill, glow, ring = LAMP, GLOW, ""
                else:
                    fill, glow, ring = OFF, PANEL, ""
                c.itemconfigure(lamp, state="normal", fill=fill, outline=ring,
                                width=2 if ring else 1)
                c.itemconfigure(halo, state="normal", fill=glow)
        c.itemconfigure(self.rack_empty,
                        text="" if slots_here else "NO MODULE SLOTS IN THIS RACK")

        # tabs: lit for the rack on show, red if a rack you cannot see has a fault
        for k, (rect, txt) in enumerate(self.rack_tabs):
            if k >= self.racks or self.racks == 1:
                c.itemconfigure(rect, state="hidden")
                c.itemconfigure(txt, state="hidden")
                continue
            mrange = range(k * RACK_MODULES + 1, (k + 1) * RACK_MODULES + 1)
            lrange = range(k * RACK_LINES + 1, (k + 1) * RACK_LINES + 1)
            faulty = any(m in down_mods for m in mrange) or any(n in down_lines for n in lrange)
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
        capacity = cap
        pct = int(100 * served / max(1, capacity))
        c.itemconfigure(self.cores_text,
                        text="C O R E S   %d        L O A D   %d%%" % (cores, pct))
        c.coords(self.load_bar, 31, 495, 31 + int(2.99 * min(100, pct)), 505)
        note = ""
        if capacity > raw:
            note = "   +%d: %s" % (capacity - raw, s.get("capacity_why") or "EFFECTS")
        elif capacity < raw:
            note = "   %d OFF: %s" % (raw - capacity, s.get("capacity_why") or "EFFECTS")
        c.itemconfigure(self.load_text, text="%d of %d callers%s" % (served, capacity, note))

        # the line field agrees with the lamps: a line is busy when its lamp is
        # lit, or when it is the spare carrying a dead line's caller
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
        ts = float(s.get("tick_seconds") or 60)
        due = float(s.get("_fetched", time.time())) + float(s.get("to_next", 0))
        into = max(0.0, due - time.time())          # seconds to the next tick

        def left(ticks):
            # real time until that many more ticks have passed, at today's rate
            secs = max(0.0, (int(ticks) - 1) * ts + into)
            secs = int(secs + 0.999)
            return ("%dS" % secs if secs < 60 else "%dM%02dS" % (secs // 60, secs % 60)
                    if secs < 3600 else "%dH%02dM" % (secs // 3600, secs % 3600 // 60))
        c.itemconfigure(self.decision_text,
                        text=("DECISION WAITING  %s" % left(inc["left"])) if inc else "")
        c.itemconfigure(self.project_text, text=(
            "WORKS %d%%  %s" % (int(100 * (proj["total"] - proj["left"]) / max(1, proj["total"])),
                                left(proj["left"])) if proj else ""))

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
        elif self.session.catching_up():
            done = self.session.owed_total - self.session.owed
            body = ["C A T C H I N G   U P", ""] + self.wrap(
                "THE MACHINE RAN WHILE YOU WERE AWAY. REPLAYING %d OF %d MINUTES; THE "
                "REPORT FOLLOWS." % (done, self.session.owed_total))
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


# ------------------------------------------------------------------ forms
class Form:
    """A sheet of paper in its own window that you can work: rows of text and
    stamps you click. It redraws from /state whenever what it shows changes,
    and prints the machine's answer to the last thing you did at the foot."""

    ROW = 30

    WATCH = ("cash", "cores", "lines", "room", "shop", "tick_seconds", "mode", "price", "slots")

    def __init__(self, machine, slot, title, heading, builder, watch=WATCH, footer=True):
        self.m = machine
        self.heading = heading
        self.builder = builder
        self.watch = watch
        self.footer = footer
        self.qty = {}
        self.result = ""
        self.sig = None
        self.win = tk.Toplevel(machine.root)
        self.win.title(title)
        self.win.configure(bg=PAPER)
        self.win.resizable(False, False)
        self.cw, self.ch = 880, 400
        self.c = tk.Canvas(self.win, width=self.cw, height=self.ch, bg=PAPER,
                           highlightthickness=0)
        self.c.pack()
        self.win.bind("<Escape>", lambda e: self.win.destroy())
        self.placed = False
        setattr(machine, slot, self)

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def lift(self):
        self.win.lift()
        self.win.focus_force()

    # -- drawing helpers, used by the builders ---------------------------------
    def text(self, x, y, s, size=11, fill=INK, anchor="w", bold=True):
        self.c.create_text(x, y, anchor=anchor, text=s, fill=fill, font=mono(size, bold))

    def stamp(self, x0, y, label, action, width=None, lit=False, dim=False):
        """A clickable box: lit is the current choice, dim cannot be done now
        (it still asks the machine, which says why)."""
        w = width or (16 + 9 * len(label))
        ink = FADE if dim else INK
        rect = self.c.create_rectangle(x0, y - 11, x0 + w, y + 11,
                                       fill=INK if lit else PAPER, outline=ink, width=1)
        txt = self.c.create_text(x0 + w / 2, y, text=label, font=mono(10),
                                 fill=PAPER if lit else ink)
        for item in (rect, txt):
            self.c.tag_bind(item, "<Button-1>", lambda _e: action())
            self.c.tag_bind(item, "<Enter>", lambda _e: self.c.config(cursor="hand2"))
            self.c.tag_bind(item, "<Leave>", lambda _e: self.c.config(cursor=""))
        return x0 + w

    def counter(self, x0, y, key, lo, hi, steps=None, fmt="%d"):
        """- n + : the quantity for one row of the form."""
        n = self.qty.get(key, lo)

        def nudge(d):
            seq = steps or list(range(lo, hi + 1))
            i = min(range(len(seq)), key=lambda k: abs(seq[k] - self.qty.get(key, lo)))
            self.qty[key] = seq[max(0, min(len(seq) - 1, i + d))]
            self.redraw(force=True)
        self.stamp(x0, y, "-", lambda: nudge(-1), width=22)
        self.text(x0 + 48, y, fmt % n, anchor="center")
        self.stamp(x0 + 74, y, "+", lambda: nudge(+1), width=22)
        for item in self.c.find_overlapping(x0 + 24, y - 10, x0 + 72, y + 10):
            self.c.tag_bind(item, "<Button-4>", lambda _e: nudge(+1))
            self.c.tag_bind(item, "<Button-5>", lambda _e: nudge(-1))
        return n

    def send(self, command):
        """Ask the machine to do it; keep the answer for the foot of the sheet."""
        answer = self.m.fetch(command, seconds=2.0)
        rows = [r.strip() for r in answer.replace("\r", "").split("\n") if r.strip()]
        self.result = rows[0] if rows else "the machine sent nothing back"
        self.redraw(force=True)

    # -- the sheet ------------------------------------------------------------
    def redraw(self, force=False):
        s = self.m.poller.state or {}
        sig = json.dumps([s.get(k) for k in self.watch],
                         sort_keys=True) + json.dumps(self.qty, sort_keys=True) + self.result
        if sig == self.sig and not force:
            return
        self.sig = sig
        c = self.c
        c.delete("all")
        c.create_text(30, 24, anchor="w", fill=FADE, font=mono(10), text=self.heading)
        y = self.builder(self, s, 58)
        y += 8
        c.create_line(30, y, self.cw - 30, y, fill=FADE)
        y += 20
        if self.footer:
            # the machine's answer, folded onto a second line if it is long
            for part in (textwrap.wrap(self.result, 98)[:2] or ["NOTHING ORDERED YET."]):
                self.text(30, y, part, size=10, fill=INK if self.result else FADE)
                y += 20
            y += 6
        self.text(30, y, getattr(self, "hint", "ESC closes this sheet"), size=9, fill=FADE)
        ch = y + 22
        for ry in range(58 + 13, ch - 10, self.ROW):        # ruled paper, under each row
            c.tag_lower(c.create_line(26, ry, self.cw - 14, ry, fill=PAPER_RULE))
        for ry in range(42, ch - 20, 22):
            c.tag_lower(c.create_oval(10, ry, 16, ry + 6, fill=SHADOW, outline=""))
        if ch != self.ch or not self.placed:
            self.ch = ch
            c.config(height=ch)
            top = max(0, min(60, self.m.root.winfo_screenheight() - ch - 60))
            self.win.geometry("%dx%d+90+%d" % (self.cw, ch, top))
            self.placed = True


def money(v):
    return "{:,.2f}".format(v)


def build_buy(f, s, y):
    """The order form: plant, equipment, premises."""
    cash = float(s.get("cash", 0.0))
    shop = s.get("shop") or {}
    room = s.get("room") or {}
    f.text(30, y, "CASH %s" % money(cash), size=12)
    f.text(330, y, "ROOM FOR %d MODULES AND %d LINES HERE"
           % (room.get("modules", 0), room.get("lines", 0)), size=10, fill=FADE)
    y += f.ROW + 6

    def plant_row(y, key, name, price, info, have, cap, command):
        f.text(40, y, name)
        f.text(330, y, money(price), anchor="e")
        f.text(350, y, info, size=10, fill=FADE)
        f.text(552, y, ("%d/%d" % (have, cap)) if cap is not None else str(have),
               size=10, anchor="e")
        left = max(1, (cap - have) if cap is not None else 99)
        f.qty[key] = max(1, min(f.qty.get(key, 1), left))
        n = f.counter(568, y, key, 1, left)
        total = n * price
        full = cap is not None and have >= cap
        f.text(748, y, money(total), anchor="e", fill=FADE if total > cash else INK)
        f.stamp(764, y, "ORDER", lambda: f.send(command % n), width=86,
                dim=full or total > cash)
        return y + f.ROW

    f.text(30, y, "P L A N T", size=10, fill=FADE)
    for x, head, anchor in ((330, "EACH", "e"), (552, "HAVE", "e"), (618, "HOW MANY", "center"),
                            (748, "TOTAL", "e")):
        f.text(x, y, head, size=9, fill=FADE, anchor=anchor)
    y += f.ROW - 6
    lines, cores = int(s.get("lines", 0)), int(s.get("cores", 0))
    y = plant_row(y, "lines", "DIAL-IN LINES", shop.get("line", 0),
                  "ONE CALLER EACH",
                  lines, room.get("lines"), "BUY LINES %d")
    for m in shop.get("models", []):
        y = plant_row(y, "mod:" + m["name"], "%s MODULE" % m["name"], m["price"],
                      "%d CALLERS%s" % (m["users"], ", AHEAD" if m["ahead"] else ""),
                      cores, room.get("modules"), "BUY " + m["name"] + " %d")
    y = plant_row(y, "slots", "BATCH SLOTS", shop.get("slot", 0),
                  "ONE MORE JOB AT ONCE",
                  int(s.get("slots", 0)), None, "BUY SLOTS %d")

    offers = [u for u in shop.get("upgrade_to", []) if u["costs"]]
    if offers:
        y += 6
        f.text(30, y, "U P G R A D E   M O D U L E S", size=10, fill=FADE)
        f.text(552, y, "CAN", size=9, fill=FADE, anchor="e")
        y += f.ROW - 6
        for u in offers:
            key = "up:" + u["name"]
            costs = u["costs"]
            f.text(40, y, "TO " + u["name"])
            f.text(330, y, money(costs[0]), anchor="e")
            f.text(350, y, "%d CALLERS, TRADE-IN" % u["users"], size=10, fill=FADE)
            f.text(552, y, str(len(costs)), size=10, anchor="e")
            f.qty[key] = max(1, min(f.qty.get(key, 1), len(costs)))
            n = f.counter(568, y, key, 1, len(costs))
            total = sum(costs[:n])
            f.text(748, y, money(total), anchor="e", fill=FADE if total > cash else INK)
            f.stamp(764, y, "ORDER", lambda n=n, m=u["name"]: f.send("UPGRADE MODULES %d %s" % (n, m)),
                    width=86, dim=total > cash)   # a swap is not engineers' work
            y += f.ROW

    y += 6
    f.text(30, y, "E Q U I P M E N T", size=10, fill=FADE)
    if shop.get("busy"):
        f.text(330, y, "THE ENGINEERS ARE BUSY: " + shop["busy"], size=10, fill=FADE)
    y += f.ROW - 6
    for u in shop.get("upgrades", []):
        f.text(40, y, u["name"])
        f.text(330, y, money(u["price"]), anchor="e")
        desc = u["desc"].upper()
        if len(desc) > 50:                        # cut at a word, never mid-word
            desc = desc[:49].rsplit(" ", 1)[0] + "…"
        f.text(350, y, desc, size=10, fill=FADE)
        if u["owned"]:
            f.text(807, y, "OWNED", size=10, fill=FADE, anchor="center")
        elif u["fitting"]:
            f.text(807, y, "FITTING", size=10, fill=INK, anchor="center")
        else:
            f.stamp(764, y, "ORDER", lambda n=u["name"]: f.send("BUY " + n), width=86,
                    dim=u["price"] > cash or bool(shop.get("busy")))
        y += f.ROW

    y += 6
    f.text(30, y, "P R E M I S E S", size=10, fill=FADE)
    y += f.ROW - 6
    b = shop.get("building") or {}
    f.text(40, y, "THE BUILDING")
    f.text(330, y, money(b.get("price", 0)), anchor="e")
    f.text(350, y, "THE RENT LINE GOES. PRESTIGE BECOMES POSSIBLE", size=10, fill=FADE)
    if b.get("owned"):
        f.text(807, y, "OURS", size=10, fill=FADE, anchor="center")
    else:
        f.stamp(764, y, "ORDER", lambda: f.send("BUY BUILDING"), width=86,
                dim=b.get("price", 0) > cash)
    return y + f.ROW - 10


def build_help(f, s, y):
    """The manual, a tab at a time. The tabs and every page come from the
    machine (HELP INDEX, HELP <tab>), so content.py stays the one place to edit."""
    if not getattr(f, "tabs", None):
        answer = f.m.fetch("HELP INDEX")
        f.tabs = [pair.split("=", 1) for pair in answer.split()[1:] if "=" in pair]
        f.tabs = [(k, t.replace("_", " ")) for k, t in f.tabs] or [("DAY", "EVERY DAY")]
        f.pages = {}
    key = f.qty.get("tab", f.tabs[0][0])

    def show(k):
        f.qty["tab"] = k
        f.redraw(force=True)
    x = 30
    for k, title in f.tabs:
        x = f.stamp(x, y, title, lambda k=k: show(k), lit=k == key) + 8
    y += f.ROW
    if key not in f.pages:
        f.pages[key] = f.m.fetch("HELP " + key).replace("\r", "").split("\n")
    page = [r for r in f.pages[key] if r.strip()]
    if not page:
        f.text(40, y, "THE MACHINE SENT NOTHING BACK.", fill=FADE)
        return y + f.ROW
    f.text(30, y, page[0].strip(), size=12)
    if len(page) > 1:
        f.text(260, y, page[1].strip().upper(), size=10, fill=FADE)
    y += f.ROW
    for row in page[2:]:
        command, desc = row[2:28].strip(), row[29:].strip()
        f.text(40, y, command)
        f.text(300, y, desc, bold=False)
        y += f.ROW
    return y - f.ROW + 10


MOODS = {-2: "BITTER", -1: "SULKING", 0: "", 1: "CONTENT", 2: "LOYAL"}


def short_time(ticks):
    return "%dH%02d" % (int(ticks) // 60, int(ticks) % 60)


def build_staff(f, s, y):
    """Who works here, what they do, and the stamps to hire, train and fire."""
    cash = float(s.get("cash", 0.0))
    roles = s.get("roles") or []
    pay = sum(r["hired"]["salary"] for r in roles if r["hired"])
    f.text(30, y, "CASH %s" % money(cash), size=12)
    f.text(330, y, "SALARIES %.2f A TICK" % pay, size=10, fill=FADE)
    y += f.ROW + 6
    sure = f.qty.get("fire")

    def fire(role):
        if f.qty.get("fire") == role:          # the second click means it
            f.qty["fire"] = None
            f.send("FIRE " + role)
        else:
            f.qty["fire"] = role
            f.redraw(force=True)

    for r in roles:
        h = r["hired"]
        f.text(40, y, r["role"])
        if h:
            f.text(150, y, h["name"])
            progress = ("  %d%%" % min(99, 100 * h["xp"] // h["next"])) if h["next"] else "  TOP"
            f.text(270, y, "LEVEL %d%s" % (h["level"], progress), size=10)
            f.text(500, y, "%.2f/TICK" % h["salary"], size=10, anchor="e")
            if h["level"] < 3 and not h["away"]:
                f.stamp(560, y, "TRAIN %.0f" % r["train_cost"], lambda k=r["role"]: f.send("TRAIN " + k),
                        width=120, dim=r["train_cost"] > cash)
            f.stamp(764, y, "SURE?" if sure == r["role"] else "FIRE", lambda k=r["role"]: fire(k),
                    width=86, lit=sure == r["role"])
            y += f.ROW - 8
            bits = [h["does"] or "NOTHING WHILE AWAY"]
            if h["away"]:
                bits.append("%s, BACK IN %s" % (h["away"], short_time(h["back_in"])))
            if MOODS.get(max(-2, min(2, h["morale"]))):
                bits.append(MOODS[max(-2, min(2, h["morale"]))])
            f.text(150, y, "  ·  ".join(bits), size=10, fill=FADE)
        else:
            f.text(150, y, "VACANT", fill=FADE)
            f.text(500, y, "%.2f/TICK" % r["salary"], size=10, anchor="e", fill=FADE)
            f.stamp(764, y, "HIRE %.0f" % r["fee"], lambda k=r["role"]: f.send("HIRE " + k),
                    width=86, dim=r["fee"] > cash)
            y += f.ROW - 8
            line = r["does"] + "  ·  " + r["desc"].upper()
            if len(line) > 84:                       # cut at a word, inside the paper
                line = line[:83].rsplit(" ", 1)[0].rstrip(",;") + "…"
            f.text(150, y, line, size=10, fill=FADE)
        y += f.ROW + 4
    f.text(30, y, "THEY LEVEL UP WITH EXPERIENCE AND ASK FOR A RAISE WHEN THEY DO. "
                  "TRAINING TAKES THEM AWAY 8 HOURS.", size=9, fill=FADE)
    return y + 6


LOAN_STEPS = [10, 20, 50, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000]


def build_bank(f, s, y):
    """The bank manager: what we owe, what he would lend, and the stamps."""
    cash = float(s.get("cash", 0.0))
    b = s.get("bank") or {}
    ts = float(s.get("tick_seconds") or 60)
    f.text(30, y, "CASH %s" % money(cash), size=12)
    y += f.ROW + 6
    owed = float(b.get("owed", 0.0))
    f.text(40, y, "WE OWE")
    f.text(330, y, money(owed), anchor="e")
    if owed:
        f.text(350, y, "INTEREST %.3f A TICK (%.0f%% A DAY)" % (b["interest"], 100 * b["rate_day"]),
               size=10, fill=FADE)
        y += f.ROW
        due = int(b.get("due_in", 0))
        f.text(40, y, "DUE IN")
        f.text(330, y, short_time(due), anchor="e")
        secs = int(due * ts)
        f.text(350, y, "ABOUT %s IN REAL TIME"
               % ("%dM" % (secs // 60) if secs < 3600 else "%dH%02dM" % (secs // 3600, secs % 3600 // 60)),
               size=10, fill=FADE)
        y += f.ROW - 8
        f.text(350, y, "THEN THE BANK TAKES IT, OVERDRAWN OR NOT.", size=10, fill=FADE)
    y += f.ROW + 8

    f.text(30, y, "B O R R O W", size=10, fill=FADE)
    y += f.ROW - 4
    limit = float(b.get("limit", 0.0))
    if b.get("barred_for"):
        f.text(40, y, "THE BANK WILL NOT LEND TO US FOR %s." % short_time(b["barred_for"]), fill=FADE)
    elif limit < 10:
        f.text(40, y, "THE MANAGER WILL NOT GO ANY FURTHER.", fill=FADE)
    else:
        f.text(40, y, "HE WOULD LEND")
        f.text(330, y, money(limit), anchor="e")
        steps = [v for v in LOAN_STEPS if v <= limit] or [int(limit)]
        f.qty["borrow"] = min(f.qty.get("borrow", steps[0]), steps[-1])
        n = f.counter(568, y, "borrow", steps[0], steps[-1], steps=steps)
        f.stamp(764, y, "BORROW", lambda: f.send("BORROW %d" % n), width=86)
        y += f.ROW
        f.text(40, y, "THE LIMIT FOLLOWS WHAT THE PLANT COST AND OUR REPUTATION. DUE IN %s."
               % short_time(b.get("term", 14400)), size=10, fill=FADE)
    y += f.ROW + 8

    f.text(30, y, "R E P A Y", size=10, fill=FADE)
    y += f.ROW - 4
    if owed <= 0:
        f.text(40, y, "NOTHING TO REPAY.", fill=FADE)
    else:
        top = min(owed, cash)
        steps = [v for v in LOAN_STEPS if v <= top] or [max(1, int(top))]
        f.qty["repay"] = min(f.qty.get("repay", steps[0]), steps[-1])
        n = f.counter(568, y, "repay", steps[0], steps[-1], steps=steps)
        f.text(40, y, "PAY SOME BACK")
        f.stamp(764, y, "REPAY", lambda: f.send("REPAY %d" % n), width=86, dim=top < 1)
        y += f.ROW
        f.text(40, y, "OR ALL %s AT ONCE" % money(owed))
        f.stamp(764, y, "ALL", lambda: f.send("REPAY ALL"), width=86, dim=cash < owed)
    return y + f.ROW - 10


TICK_STEPS = [1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 120, 300, 600, 1800, 3600]


def build_settings(f, s, y):
    """The clock, the mode, our price."""
    ts = int(s.get("tick_seconds") or 60)
    f.text(30, y, "T H E   C L O C K", size=10, fill=FADE)
    y += f.ROW - 4
    f.text(40, y, "SECONDS A TICK")
    x = 250
    for secs in (1, 3, 10, 60):
        x = f.stamp(x, y, str(secs), lambda n=secs: (f.qty.update(tick=n), f.send("TICK %d" % n)),
                    width=40, lit=ts == secs) + 8
    f.text(x + 18, y, "OR", size=10, fill=FADE)
    if f.qty.get("tick_seen") != ts:          # the clock moved: the counter follows
        f.qty["tick"], f.qty["tick_seen"] = ts, ts
    n = f.counter(x + 48, y, "tick", 1, 3600, steps=TICK_STEPS, fmt="%ds")
    f.stamp(x + 156, y, "SET", lambda: f.send("TICK %d" % n), width=56, lit=False)
    y += f.ROW
    f.text(40, y, "ONE TICK IS ONE MINUTE IN THE BUREAU. NOW EVERY %d SECOND%s."
           % (ts, "" if ts == 1 else "S"), size=10, fill=FADE)
    y += f.ROW + 8

    mode = s.get("mode", "wall")
    f.text(30, y, "T H E   M O D E", size=10, fill=FADE)
    y += f.ROW - 4
    f.text(40, y, "THE WORLD RUNS")
    x = f.stamp(250, y, "ON THE CLOCK", lambda: f.send("MODE WALL"), lit=mode == "wall") + 12
    f.stamp(x, y, "ONLY WHILE ON", lambda: f.send("MODE SESSION"), lit=mode == "session")
    y += f.ROW
    f.text(40, y, "ON THE CLOCK: IT KEEPS GOING WHILE YOU ARE AWAY, AND REPORTS WHEN YOU "
           "COME BACK." if mode == "wall" else
           "ONLY WHILE ON: NOTHING MOVES WHILE NOBODY IS DIALLED IN.", size=10, fill=FADE)
    y += f.ROW + 8

    pr = s.get("pricing") or {"min": 50, "max": 200, "fair": 125, "elasticity": 1.3}
    price = int(s.get("price", 100))
    f.text(30, y, "O U R   P R I C E", size=10, fill=FADE)
    y += f.ROW - 4
    f.text(40, y, "PER CENT OF LIST")
    if f.qty.get("price_seen") != price:
        f.qty["price"], f.qty["price_seen"] = price, price
    want = f.counter(250, y, "price", pr["min"], pr["max"],
                     steps=list(range(pr["min"], pr["max"] + 1, 5)), fmt="%d%%")
    f.stamp(358, y, "SET", lambda: f.send("PRICE %d" % want), width=56)
    f.text(430, y, "NOW %d%%" % price, size=10, fill=INK)
    y += f.ROW
    callers = round(100 * ((want / 100.0) ** -pr["elasticity"] - 1))
    note = "AT %d%%: %+d%% PER CALLER, %+d%% CALLERS FROM THE PRICE ALONE" % (
        want, want - 100, callers)
    if want > pr["fair"]:
        note += ". OVER %d%%, REPUTATION SLIPS" % pr["fair"]
    f.text(40, y, note, size=10, fill=FADE)
    return y + f.ROW - 10


def parse():
    import argparse
    ap = argparse.ArgumentParser(description="timeshare, in one window")
    ap.add_argument("--db", default=None, help="save file (default: the usual place)")
    ap.add_argument("--tick", type=int, default=None, help="seconds per tick")
    ap.add_argument("--selftest", action="store_true",
                    help="run the engine without a window and exit (for builds)")
    ap.add_argument("--version", action="version", version="timeshare " + engine.VERSION)
    return ap.parse_args()


def crash_log(db_path, closing=lambda: False):
    """A packaged window has no console: an error is written next to the save,
    and the player is told where, so it can be sent on."""
    import traceback
    path = os.path.join(os.path.dirname(os.path.abspath(db_path)), "timeshare-crash.log")

    def report(kind, value, tb, root=None):
        text = "".join(traceback.format_exception(kind, value, tb))
        try:
            with open(path, "a") as fh:
                fh.write("---- %s  timeshare %s  %s\n%s\n"
                         % (time.strftime("%Y-%m-%d %H:%M:%S"), engine.VERSION, sys.platform, text))
        except OSError:
            pass
        if closing():
            return                    # the window is going away anyway: say nothing
        try:
            tkinter.messagebox.showerror(
                "timeshare", "Something went wrong inside the machine.\n\n"
                "The details are in:\n%s\n\nThe game will try to carry on." % path)
        except tk.TclError:
            pass
    return report


def hold_save(db_path):
    """One window per save: two would both run the world and spoil it. Returns
    an open lock file to keep for the window's life, or None if it is taken."""
    path = db_path + ".lock"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fh = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def main():
    # a packaged windowed app on Windows has no console at all: printing would
    # crash it, so output goes nowhere (the selftest still answers by exit code)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    args = parse()
    if args.selftest:
        sys.exit(engine.selftest())
    db_path = args.db or engine.default_save()
    held = hold_save(db_path)
    if held is None:
        root = tk.Tk()
        root.withdraw()
        tkinter.messagebox.showinfo("timeshare", "TIMESHARE IS ALREADY OPEN.\n\n"
                                    "Only one window can run the bureau at a time.")
        root.destroy()
        return
    state = {"app": None}
    report = crash_log(db_path, closing=lambda: bool(state["app"] and state["app"].closing))
    sys.excepthook = report
    session = engine.Session(db_path, args.tick)
    root = tk.Tk()
    root.report_callback_exception = report        # errors inside the window, too
    app = state["app"] = Machine(root, session)
    root.protocol("WM_DELETE_WINDOW", app.quit_soon)
    root.bind("<Escape>", lambda e: app.quit_soon())
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        if hasattr(signal, name):                  # Windows has no SIGHUP
            signal.signal(getattr(signal, name), lambda *_: app.quit_soon())
    try:
        root.mainloop()
    finally:
        app.poller.stop = True
        session.close()                            # saved, and the next report starts here
        held.close()


if __name__ == "__main__":
    main()
