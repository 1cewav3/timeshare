#!/usr/bin/env python3
"""The desktop edition: a 2000s machine on your desk, with the bureau inside it.

The desktop IS the menu. Icons on the blue, double-click one, a window opens.
Everything here is a front end: it polls the running game for its numbers and
posts commands to the same handler the terminal and the browser board use, so the
window cannot disagree with the machine about a rule.

    python3 desk.py --fullscreen       # the desktop, filling the screen
    python3 desk.py                    # talks to localhost:2024
    python3 desk.py 192.168.1.144 2024 # talks to the pi
"""
import json
import sys
import time
import tkinter as tk
import urllib.error
import urllib.request

BG = "#d4d0c8"          # the grey of every 2000s window
PANEL = "#ffffff"
DESKTOP = "#3a6ea5"     # the blue behind the icons
DIM = "#5a5a5a"
NAVY = "#00007b"
UP = "#0a6b0a"
DOWN = "#a80000"
AMBER = "#7a5b00"

BODY = ("DejaVu Sans", 9)
BOLD = ("DejaVu Sans", 9, "bold")
SMALLB = ("DejaVu Sans", 8, "bold")
MONO = ("DejaVu Sans Mono", 9)
MONOB = ("DejaVu Sans Mono", 9, "bold")
SMALL = ("DejaVu Sans", 8)
TITLE = ("DejaVu Sans", 10, "bold")


def sunken(parent, **kw):
    return tk.Frame(parent, bd=2, relief="sunken", bg=PANEL, **kw)


def draw_icon(kind, c):
    """A 44x44 icon drawn with primitives, because a 2000s machine had no SVG."""
    if kind == "console":
        c.create_rectangle(2, 6, 42, 34, fill="#1f2937", outline="#0b1220", width=2)
        c.create_rectangle(7, 11, 37, 29, fill="#0b3d1f", outline="")
        c.create_text(22, 20, text=">_", fill="#4ade80", font=("DejaVu Sans Mono", 10, "bold"))
        c.create_rectangle(16, 36, 28, 38, fill="#9aa4b2", outline="")
        c.create_rectangle(11, 38, 33, 41, fill="#c7ccd4", outline="")
    elif kind == "machine":
        c.create_rectangle(8, 4, 36, 40, fill="#8d99a8", outline="#4a5563", width=2)
        c.create_rectangle(12, 8, 32, 16, fill="#2b3a55", outline="")
        for y in (21, 26, 31):
            c.create_rectangle(12, y, 32, y + 3, fill="#5c6875", outline="")
        c.create_oval(18, 35, 21, 38, fill="#4ade80", outline="")
    elif kind == "ledger":
        c.create_rectangle(4, 34, 40, 38, fill="#7b8794", outline="")
        for x, h in ((8, 12), (17, 20), (26, 8), (34, 24)):
            c.create_rectangle(x, 34 - h, x + 6, 34, fill="#3f8a52", outline="#1d4a2a")
    elif kind == "staff":
        c.create_oval(14, 4, 30, 20, fill="#f0d2b4", outline="#8a6d3b", width=2)
        c.create_rectangle(8, 22, 36, 40, fill="#2b3a55", outline="#101828", width=2)
        c.create_line(22, 22, 22, 40, fill="#101828")
    elif kind == "teleprinter":
        c.create_rectangle(4, 10, 40, 34, fill="#fdfdf6", outline="#6b7280", width=2)
        for y in (16, 21, 26):
            c.create_line(9, y, 33, y, fill="#9aa4b2")
        c.create_rectangle(10, 34, 34, 40, fill="#c7ccd4", outline="#6b7280")
    elif kind == "faults":
        c.create_polygon(22, 4, 42, 38, 2, 38, fill="#f2c14e", outline="#8a6d3b", width=2)
        c.create_text(22, 26, text="!", fill="#5a3d00", font=("DejaVu Sans", 14, "bold"))
    elif kind == "deeds":
        c.create_rectangle(6, 6, 38, 40, fill="#f6e7c8", outline="#8a6d3b", width=2)
        for y in (14, 20, 26, 32):
            c.create_line(11, y, 33, y, fill="#b99a63")
        c.create_oval(28, 30, 36, 38, fill="#a80000", outline="#5a0000")
    elif kind == "quit":
        c.create_rectangle(6, 8, 38, 38, fill="#a80000", outline="#5a0000", width=2)
        c.create_text(22, 23, text="X", fill="#ffe8e8", font=("DejaVu Sans", 14, "bold"))


ICONS = [
    ("console", "Console"),
    ("machine", "The machine"),
    ("ledger", "The ledger"),
    ("staff", "Staff"),
    ("teleprinter", "Teleprinter"),
    ("faults", "What is down"),
    ("deeds", "The deeds"),
    ("quit", "Quit"),
]


class Client:
    """Talking to the running game. No rules live here."""

    def __init__(self, host, port):
        self.base = "http://%s:%d" % (host, port)

    def state(self):
        with urllib.request.urlopen(self.base + "/state", timeout=4) as r:
            return json.loads(r.read())

    def command(self, line):
        data = json.dumps({"line": line}).encode()
        req = urllib.request.Request(self.base + "/cmd", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read()).get("out", "")


def machine_block(s):
    """The machine panel as text, so it can be checked without a display."""
    w = 22
    rows = [("site", s["site"].split(",")[0].title()),
            ("machine", "%s generation" % s.get("machine", "?").title()),
            ("usable capacity", "%d" % s["capacity"]),
            ("callers served", "%d of %d" % (s["served"], s["subs"])),
            ("batching", "%d queued, %d slots" % (s["queued"], s["slots"])),
            ("revenue/tick", "%.3f" % s["revenue"]),
            ("costs/tick", "%.3f" % s["outgoings"]),
            ("net/tick", "%+.3f" % s["net"]),
            ("reputation", "%.2f  (%+.3f)" % (s["rep"], s["rep_trend"])),
            ("cash", "%.2f" % s["cash"]),
            ("mods", ", ".join(m["name"].title() for m in s["mods"]) if s["mods"] else "none")]
    return "\n".join("%-*s %s" % (w, k, v) for k, v in rows)


class Window:
    """A grey 2000s window: title bar you can drag, close box, taskbar entry."""

    def __init__(self, app, title, w, h, x, y):
        self.app = app
        self.frame = tk.Frame(app.screen, bg=BG, bd=2, relief="raised")
        self.frame.place(x=x, y=y, width=w, height=h)
        bar = tk.Frame(self.frame, bg="#0a246a", height=20)
        bar.pack(fill="x")
        self.title = tk.Label(bar, text=title, bg="#0a246a", fg="#ffffff", font=BOLD, anchor="w")
        self.title.pack(side="left", padx=6)
        close = tk.Label(bar, text="x", bg=BG, fg="#000000", font=BOLD, width=2, bd=1, relief="raised")
        close.pack(side="right", padx=2, pady=2)
        close.bind("<Button-1>", lambda e: app.close_window(self))
        for widget in (bar, self.title):
            widget.bind("<Button-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.drag)
        self.body = tk.Frame(self.frame, bg=BG)
        self.body.pack(fill="both", expand=True, padx=4, pady=4)
        self.frame.bind("<Button-1>", lambda e: self.raise_self())
        self.x = self.y = 0

    def start_drag(self, e):
        self.x, self.y = e.x_root, e.y_root
        self.raise_self()

    def drag(self, e):
        if not hasattr(self, "x"):
            return
        dx, dy = e.x_root - self.x, e.y_root - self.y
        self.x, self.y = e.x_root, e.y_root
        self.frame.place_configure(x=self.frame.winfo_x() + dx, y=self.frame.winfo_y() + dy)

    def raise_self(self):
        self.frame.lift()


class Desk:
    def __init__(self, host, port):
        self.client = Client(host, port)
        self.root = tk.Tk()
        self.root.title("NORTHGATE - timesharing bureau")
        self.root.geometry("1280x820")
        if "--fullscreen" in sys.argv:
            self.root.attributes("-fullscreen", True)
        self.root.configure(bg=DESKTOP)
        self.screen = tk.Frame(self.root, bg=DESKTOP)
        self.screen.pack(fill="both", expand=True)
        self.windows = {}
        self.state = None
        self.stat_widgets = {}
        self.tile = False
        self.build_icons()
        self.build_taskbar()
        self.screen.bind("<Configure>", lambda e: self.reclamp())
        self.poll()
        self.root.bind("<Control-q>", lambda e: self.root.destroy())

    # ---------------------------------------------------------------- desktop
    def build_icons(self):
        holder = tk.Frame(self.screen, bg=DESKTOP)
        holder.place(x=14, y=14)
        for i, (kind, label) in enumerate(ICONS):
            cell = tk.Frame(holder, bg=DESKTOP, width=96, height=76)
            cell.grid(row=i, column=0, padx=4, pady=2, sticky="w")
            c = tk.Canvas(cell, width=44, height=44, bg=DESKTOP, highlightthickness=0)
            c.pack()
            draw_icon(kind, c)
            text = tk.Label(cell, text=label, bg=DESKTOP, fg="#ffffff", font=SMALL,
                            wraplength=92, justify="center")
            text.pack()
            for widget in (cell, c, text):
                widget.bind("<Double-Button-1>", lambda e, k=kind: self.open(k))

    def build_taskbar(self):
        bar = tk.Frame(self.root, bg=BG, bd=2, relief="raised", height=26)
        bar.pack(side="bottom", fill="x")
        tk.Label(bar, text=" NORTHGATE 79 ", bg=BG, font=BOLD, bd=1, relief="raised").pack(side="left", padx=4, pady=2)
        self.task_items = tk.Frame(bar, bg=BG)
        self.task_items.pack(side="left", fill="x", expand=True)
        self.clock = tk.Label(bar, text="", bg=BG, font=BODY, bd=1, relief="sunken", padx=8)
        self.clock.pack(side="right", padx=4)

    def reclamp(self):
        sw = self.screen.winfo_width() or 1280
        sh = self.screen.winfo_height() or 820
        for win in self.windows.values():
            if not win.frame.winfo_exists():
                continue
            w = min(win.frame.winfo_width(), max(280, sw - 140))
            h = min(win.frame.winfo_height(), max(200, sh - 60))
            x = min(win.frame.winfo_x(), max(10, sw - w - 10))
            y = min(win.frame.winfo_y(), max(10, sh - h - 10))
            win.frame.place_configure(x=x, y=y, width=w, height=h)

    def tick_clock(self):
        self.clock.configure(text=time.strftime("%H:%M"))
        self.root.after(10000, self.tick_clock)

    # ---------------------------------------------------------------- windows
    def open(self, kind):
        if kind == "quit":
            self.root.destroy()
            return
        if kind in self.windows:
            self.windows[kind].raise_self()
            return
        size = {"console": (620, 430), "machine": (470, 360), "ledger": (470, 280),
                "staff": (380, 300), "teleprinter": (440, 320), "faults": (380, 280),
                "deeds": (400, 200)}.get(kind, (380, 280))
        # the desktop window is often tiled narrower than 1280 by the compositor,
        # and a frame placed past that edge is simply not there: clamp every window
        # into what is actually on screen
        sw = self.screen.winfo_width() or 1280
        sh = self.screen.winfo_height() or 820
        size_w = min(size[0], max(280, sw - 140))
        size_h = min(size[1], max(200, sh - 60))
        size = (size_w, size_h)
        n = len(self.windows)
        if self.tile:
            x = min(120 + (n % 2) * 460, max(10, sw - size_w - 10))
            y = min(24 + (n // 2) * 250, max(10, sh - size_h - 10))
        else:
            x = min(132 + 24 * n, max(10, sw - size_w - 10))
            y = min(36 + 24 * n, max(10, sh - size_h - 10))
        win = Window(self, {"console": "Console", "machine": "The machine",
                            "ledger": "The ledger", "staff": "Staff",
                            "teleprinter": "Teleprinter", "faults": "What is down",
                            "deeds": "The deeds"}[kind],
                     size[0], size[1], x, y)
        self.windows[kind] = win
        getattr(self, "build_" + kind)(win)
        btn = tk.Button(self.task_items, text=win.title["text"], font=SMALL, bd=1, relief="raised",
                        command=win.raise_self)
        btn.pack(side="left", padx=2, pady=2)
        win.task_button = btn

    def close_window(self, win):
        for kind, w in list(self.windows.items()):
            if w is win:
                del self.windows[kind]
        if getattr(win, "task_button", None):
            win.task_button.destroy()
        win.frame.destroy()
        if self.windows_renumber:
            pass

    # ---------------------------------------------------------------- panels
    def build_console(self, win):
        log = tk.Text(win.body, height=14, bg="#0b0f14", fg="#c9d4e2", insertbackground="#ffc857",
                      font=MONO, bd=0, wrap="word", state="disabled")
        sb = tk.Scrollbar(win.body, command=log.yview)
        log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        log.pack(fill="both", expand=True)
        row = tk.Frame(win.body, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        tk.Label(row, text=">", bg=BG, font=BOLD).pack(side="left")
        entry = tk.Entry(row, font=MONO)
        entry.pack(side="left", fill="x", expand=True, padx=4)
        entry.focus_set()

        def submit(_=None):
            line = entry.get().strip()
            entry.delete(0, "end")
            if not line:
                return
            try:
                out = self.client.command(line)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                out = "the machine is not answering: %s" % exc
            log.configure(state="normal")
            for text in ("> " + line, out.strip()):
                log.insert("end", text + "\n")
            log.see("end")
            log.configure(state="disabled")
            self.refresh()

        entry.bind("<Return>", submit)
        win.entry = entry
        log.configure(state="normal")
        log.insert("end", "NORTHGATE TERMINAL READY. TYPE HELP.\n")
        log.configure(state="disabled")

    def stat_block(self, parent, key, label):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=label, bg=PANEL, fg=DIM, font=SMALL, anchor="w").pack(side="left")
        value = tk.Label(row, text="-", bg=PANEL, fg="#000000", font=MONOB, anchor="e")
        value.pack(side="right")
        self.stat_widgets[key] = value
        return value

    def build_machine(self, win):
        """One monospaced block, aligned by the font.

        Laying these out with grid or pack kept clipping when the window was
        resized: the value column would land outside the frame. A text block
        cannot do that, and it is what a machine of the period printed anyway.
        """
        self.machine_text = tk.Text(win.body, bg=PANEL, fg="#000000", font=MONO, bd=2,
                                    relief="sunken", wrap="none", height=11)
        self.machine_text.pack(fill="both", expand=True)
        self.machine_text.configure(state="disabled")

    def machine_block(self, s):
        return machine_block(s)

    def build_ledger(self, win):
        c = tk.Canvas(win.body, bg=PANEL, highlightthickness=0)
        c.pack(fill="both", expand=True)
        win.canvas = c
        c.bind("<Configure>", lambda e: self.refresh())

    def build_staff(self, win):
        win.text = self.scrolling_text(win)

    def build_teleprinter(self, win):
        win.text = self.scrolling_text(win)

    def build_faults(self, win):
        win.text = self.scrolling_text(win)

    def build_deeds(self, win):
        box = sunken(win.body, padx=8, pady=8)
        box.pack(fill="both", expand=True)
        self.goal_label = tk.Label(box, text="", bg=PANEL, font=BOLD, anchor="w")
        self.goal_label.pack(anchor="w")
        self.goal_bar = tk.Canvas(box, height=14, bg=PANEL, highlightthickness=0)
        self.goal_bar.pack(fill="x", pady=6)
        self.goal_bar.bind("<Configure>", lambda e: self.refresh())
        self.goal_sub = tk.Label(box, text="", bg=PANEL, fg=DIM, font=SMALL, anchor="w",
                                 justify="left", wraplength=300)
        self.goal_sub.pack(anchor="w")

    def scrolling_text(self, win):
        t = tk.Text(win.body, bg=PANEL, fg="#000000", font=MONO, bd=2, relief="sunken",
                    wrap="word", height=10)
        t.pack(fill="both", expand=True)
        t.configure(state="disabled")
        return t

    # ---------------------------------------------------------------- refresh
    def poll(self):
        try:
            self.state = self.client.state()
            self.refresh()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            pass
        self.root.after(1000, self.poll)

    def refresh(self):
        s = self.state
        if not s:
            return
        if getattr(self, 'machine_text', None) is not None and self.machine_text.winfo_exists():
            self.set_text(self.machine_text, [self.machine_block(s)])
        win = self.windows.get("ledger")
        if win and hasattr(win, "canvas"):
            self.draw_ledger(win.canvas, s)
        win = self.windows.get("staff")
        if win and hasattr(win, "text"):
            lines = ["%s %s   %.2f/tick" % (x["role"].title(), x["name"], x["salary"])
                     for x in s["staff"]] or ["nobody works here but you"]
            lines += ["", "salaries %.2f/tick" % s["salaries"]]
            self.set_text(win.text, lines)
        win = self.windows.get("teleprinter")
        if win and hasattr(win, "text"):
            lines = []
            for o in s["offers"]:
                state = "LIVE" if o["state"] == "active" else "OFFER"
                lines.append("%-5s %s (%s)" % (state, o["company"].title(), o["work"].title()))
                if o["state"] == "active":
                    lines.append("      %d callers, %d left, short %d" % (o["want"], o["left"], o["breached"]))
                else:
                    lines.append("      %d callers for %d ticks, fee %.0f, penalty %.0f"
                                 % (o["want"], o["duration"], o["fee"], o["penalty"]))
            self.set_text(win.text, lines or ["nothing on the teleprinter"])
        win = self.windows.get("faults")
        if win and hasattr(win, "text"):
            lines = ["%s %d down %d ticks, %.0f to fix" % (f["kind"], f["n"], f["age"], f["cost"])
                     for f in s["faults"]] or ["nothing is down"]
            self.set_text(win.text, lines)
        win = self.windows.get("deeds")
        if win and hasattr(self, "goal_label"):
            if s["won"]:
                self.goal_label.configure(text="NORTHGATE HOUSE IS YOURS", fg=UP)
            else:
                self.goal_label.configure(text="%.0f of %.0f" % (s["cash"], s["goal"]["price"]))
            self.goal_bar.delete("all")
            w = self.goal_bar.winfo_width() or 300
            self.goal_bar.create_rectangle(0, 0, w, 14, fill="#e6e6e6", outline="#808080")
            self.goal_bar.create_rectangle(0, 0, max(2, w * s["goal"]["pct"] / 100.0), 14,
                                           fill=AMBER, outline="")
            sub = ("standing %d  (+%d%% revenue, +%d%% demand)"
                   % (s["standing"], 4 * s["standing"], 3 * s["standing"])) if s["standing"] else ""
            self.goal_sub.configure(text=sub or "the landlord is waiting")

    def set_text(self, widget, lines):
        if not widget.winfo_exists():
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", "\n".join(lines))
        widget.configure(state="disabled")

    machine_text = None

    def draw_ledger(self, c, s):
        c.delete("all")
        w, h = c.winfo_width() or 400, c.winfo_height() or 220
        c.create_rectangle(2, 2, w - 2, h - 2, outline="#a0a0a0")
        pts = s.get("history") or []
        if len(pts) < 2:
            c.create_text(w // 2, h // 2, text="waiting for ticks", fill=DIM, font=SMALL)
            return
        vals = [p[1] for p in pts]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        X = lambda i: 8 + i / (len(pts) - 1) * (w - 20)
        Y = lambda v: h - 16 - (v - lo) / span * (h - 34)
        c.create_line(*[coord for i, v in enumerate(vals) for coord in (X(i), Y(v))],
                      fill=NAVY, width=2, smooth=False)
        c.create_text(8, 10, text="%.0f" % hi, anchor="w", fill=DIM, font=SMALL)
        c.create_text(8, h - 8, text="%.0f" % lo, anchor="w", fill=DIM, font=SMALL)
        c.create_text(w - 8, h - 8, text="tick %d" % s["tick"], anchor="e", fill=DIM, font=SMALL)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    host = args[0] if args else "127.0.0.1"
    port = int(args[1]) if len(args) > 1 else 2024
    desk = Desk(host, port)
    opened = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--open=")]
    if opened:
        for kind in opened:
            desk.open(kind)
    elif "--all" in sys.argv:
        desk.tile = True
        for kind, _label in ICONS:
            if kind != "quit":
                desk.open(kind)
    else:
        desk.open("console")
    desk.tick_clock()
    desk.root.mainloop()


if __name__ == "__main__":
    main()
