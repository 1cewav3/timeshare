#!/usr/bin/env python3
"""The machine, as its own window.

    python3 machine.py                      # game on 127.0.0.1:2023, board on :2024
    python3 machine.py host port webport

Two connections, each doing one job: a socket session carries commands and makes
you present, and /state supplies every number drawn here. Nothing on the canvas
is invented — if the engine does not report it, it is not shown.
"""
import json
import socket
import sys
import threading
import tkinter as tk
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
PX0, PX1 = 648, 1164          # paper sheet
LX0, LX1 = 16, 632            # left column


def mono(size, bold=True):
    return ("DejaVu Sans Mono", size, "bold" if bold else "normal")


class Poller(threading.Thread):
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
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                pass
            threading.Event().wait(0.5)


class Machine:
    def __init__(self, root, host, port, web):
        self.root = root
        self.note = ""
        root.title("timeshare — SYS/370-145")
        root.configure(bg=BG)
        root.geometry("%dx%d" % (W, H))
        root.resizable(False, False)

        self.sock = None
        try:
            self.sock = socket.create_connection((host, port), timeout=8)
            self.sock.setblocking(False)
        except OSError as exc:
            self.note = "no line to %s:%d — %s" % (host, port, exc)

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
        self.entry.focus_set()

        root.after(200, self.refresh)

    # -------------------------------------------------------------- plumbing
    def submit(self, _e=None):
        line = self.entry.get().strip()
        self.entry.delete(0, "end")
        if line and self.sock:
            try:
                self.sock.sendall((line + "\n").encode("latin-1"))
            except OSError:
                self.note = "the line dropped"
            self.read_socket()

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
        c.create_rectangle(6, 6, W - 6, H - 6, outline=RULE, fill=BG)
        for x, y in ((14, 14), (W - 14, 14), (14, H - 14), (W - 14, H - 14)):
            self.screw(x, y)

        c.create_text(26, 26, anchor="w", fill=STENCIL, font=mono(12),
                      text="N O R T H G A T E   C O M P U T E R   S E R V I C E S")
        c.create_text(W / 2 + 60, 26, anchor="e", fill=STENCIL, font=mono(11),
                      text="S Y S / 3 7 0 - 1 4 5")
        c.create_text(W - 26, 26, anchor="e", fill=STENCIL, font=mono(11),
                      text="S E P   1 9 7 9")
        c.create_line(26, 40, W - 26, 40, fill=RULE)

        # ---- module rack
        self.panel(LX0, 56, LX1, 452, "M O D U L E   L A M P S")
        self.mod_rows = []
        for i in range(8):
            y = 88 + i * 44
            label = c.create_text(30, y, anchor="w", fill=STENCIL, font=mono(13),
                                  text="MOD %d" % (i + 1))
            dead = c.create_rectangle(139, y - 9, 389, y + 9, fill=PANEL, outline="")
            lamps = []
            for j in range(8):
                x = 150 + j * 30
                halo = c.create_oval(x - 11, y - 11, x + 11, y + 11, fill=PANEL, outline="")
                lamp = c.create_oval(x - 7, y - 7, x + 7, y + 7, fill=OFF, outline="")
                lamps.append((halo, lamp))
            tag = c.create_text(404, y, anchor="w", fill=FAULT, font=mono(11), text="")
            self.mod_rows.append({"label": label, "lamps": lamps,
                                  "dead": dead, "tag": tag})

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
        for r in range(4):
            for i in range(8):
                x = 42 + i * 74
                y = 562 + r * 30
                halo = c.create_oval(x - 9, y - 9, x + 9, y + 9, fill=PANEL, outline="")
                lamp = c.create_oval(x - 6, y - 6, x + 6, y + 6, fill=OFF, outline="")
                num = c.create_text(x + 18, y, anchor="w", fill=OFF, font=mono(9), text="")
                self.line_lamps.append((halo, lamp, num))
        self.turned = c.create_text(LX0 + 14, H - 74, anchor="w", fill=OFF,
                                    font=mono(11), text="")

        # ---- the sheet of paper
        c.create_rectangle(PX0 + 3, 59, PX1 + 3, H - 59, fill=SHADOW, outline="")
        c.create_rectangle(PX0, 56, PX1, H - 62, fill=PAPER, outline=SHADOW)
        for i in range(1, 26):
            y = 56 + i * 22
            if y > H - 70:
                break
            c.create_line(PX0 + 26, y, PX1 - 12, y, fill=PAPER_RULE)
        for i in range(24):
            y = 70 + i * 22
            if y > H - 74:
                break
            c.create_oval(PX0 + 9, y, PX0 + 15, y + 6, fill=SHADOW, outline="")

        self.p_head = c.create_text(PX0 + 30, 74, anchor="w", fill=FADE, font=mono(10),
                                    text="NORTHGATE  TELEPRINTER   6 LPI")
        self.p_main = c.create_text(PX0 + 30, 96, anchor="nw", fill=INK, font=mono(12),
                                    text="", width=PX1 - PX0 - 50, justify="left")
        self.p_body = c.create_text(PX0 + 30, 148, anchor="nw", fill=INK, font=mono(11),
                                    text="", width=PX1 - PX0 - 50, justify="left")
        self.goal_label = c.create_text(PX0 + 30, H - 186, anchor="w", fill=FADE,
                                        font=mono(10), text="")
        c.create_rectangle(PX0 + 30, H - 172, PX1 - 24, H - 152, fill=PAPER_RULE, outline="")
        self.goal_bar = c.create_rectangle(PX0 + 30, H - 172, PX0 + 30, H - 152, fill=INK, outline="")
        self.goal_text = c.create_text(PX0 + 30, H - 136, anchor="w", fill=INK,
                                       font=mono(10), text="")

        c.create_line(26, H - 58, W - 26, H - 58, fill=RULE)
        c.create_text(30, H - 28, anchor="w", fill=STENCIL, font=mono(13), text="O P R ›")
        self.note_text = c.create_text(W - 30, H - 28, anchor="e", fill=FADE,
                                       font=mono(10), text="")

    # --------------------------------------------------------------- painting
    def refresh(self):
        if self.poller.state:
            self.paint(self.poller.state)
        self.c.itemconfigure(self.note_text, text=self.note[-140:])
        self.root.after(200, self.refresh)

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

        left = served
        for i, row in enumerate(self.mod_rows):
            n = i + 1
            installed = n <= cores
            dead = n in down_mods
            load = min(per_core, max(0, left)) if (installed and not dead) else 0
            if installed and not dead:
                left -= load
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
                c.itemconfigure(lamp, fill=PANEL if dead else (LAMP if on else OFF))
                c.itemconfigure(halo, fill=PANEL if dead else (GLOW if on else PANEL))

        capacity = max(1, working * per_core)
        pct = int(100 * served / capacity)
        c.itemconfigure(self.cores_text,
                        text="C O R E S   %d        L O A D   %d%%" % (cores, pct))
        c.coords(self.load_bar, 31, 495, 31 + int(2.99 * min(100, pct)), 505)
        c.itemconfigure(self.load_text, text="%d of %d callers" % (served, capacity))

        busy = min(served, working_lines)
        for idx, (halo, lamp, num) in enumerate(self.line_lamps):
            n = idx + 1
            if n > max(1, lines):
                c.itemconfigure(lamp, fill=OFF)
                c.itemconfigure(halo, fill=PANEL)
                c.itemconfigure(num, text="", fill=OFF)
                continue
            c.itemconfigure(num, text="%02d" % n, fill=FADE if n <= busy else "#4a5240")
            if n in down_lines:
                c.itemconfigure(lamp, fill=FAULT)
                c.itemconfigure(halo, fill=FAULT_BG)
            elif n <= busy:
                c.itemconfigure(lamp, fill=LAMP)
                c.itemconfigure(halo, fill=GLOW)
            else:
                c.itemconfigure(lamp, fill=OFF)
                c.itemconfigure(halo, fill=PANEL)

        turned = max(0, demand - served)
        c.itemconfigure(self.turned, text="TURNED AWAY  %d" % turned,
                        fill=FAULT if turned else "#4a5240")

        net = s.get("net", 0.0)
        c.itemconfigure(self.p_main, text=(
            "TICK %d      CASH %s      NET %+.2f\n"
            "LINES %d/%d   CALLERS %d   REFUSED %d") % (
            s.get("tick", 0), "{:,.2f}".format(s.get("cash", 0.0)), net,
            working_lines, lines, served, turned))

        news = (s.get("news") or [])[-4:]
        body = ["J O U R N A L", ""]
        body += [n[:46] for n in news] or ["N O   T R A F F I C   Y E T"]
        if faults:
            body += ["", "F A U L T S", ""]
            body += ["%s %d DOWN · REPAIR %s" % (str(x.get("kind", "?")).upper(),
                                                 x.get("n", 0),
                                                 "{:,.2f}".format(x.get("cost", 0)))
                     for x in faults[:3]]
        live = [x for x in (s.get("offers") or []) if x.get("state") == "active"]
        if live:
            body += ["", "C O N T R A C T S", ""]
            body += ["%s · %d TICKS · %s" % (str(x.get("company", "?"))[:20],
                                             x.get("left", 0),
                                             "{:,.2f}".format(x.get("fee", 0)))
                     for x in live[:3]]
        owned = [str(u).lower() for u in (s.get("upgrades") or [])][:4]
        mods = ["%s (%d)" % (str(m.get("name", "?")).lower(), m.get("left", 0))
                for m in (s.get("mods") or [])][:3]
        if owned or mods:
            body += ["", "E Q U I P M E N T", ""] + owned + mods
        staff = [str(i.get("name", "?")).title() for i in (s.get("staff") or [])][:4]
        if staff:
            body += ["", "S T A F F", ""] + [",  ".join(staff)]
        c.itemconfigure(self.p_body, text="\n".join(body))

        goal = s.get("goal") or {}
        gpct = float(goal.get("pct", 0))
        span = (PX1 - 24) - (PX0 + 30)
        c.coords(self.goal_bar, PX0 + 30, H - 172,
                 PX0 + 30 + int(span * min(1.0, gpct / 100.0)), H - 152)
        c.itemconfigure(self.goal_label, text="G O A L   ·   %s" % s.get("site", ""))
        c.itemconfigure(self.goal_text, text="%.1f%% of %s  ·  %s to go" % (
            gpct, "{:,.0f}".format(goal.get("price", 0)),
            "{:,.0f}".format(max(0.0, goal.get("price", 0) - s.get("cash", 0.0)))))


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 2023
    web = int(sys.argv[3]) if len(sys.argv) > 3 else port + 1
    root = tk.Tk()
    app = Machine(root, host, port, web)
    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()
    app.poller.stop = True


if __name__ == "__main__":
    main()
