#!/usr/bin/env python3
"""TIMESHARE - you operate a 1979 timesharing service.

One process, stdlib only, no threads, no subprocesses. State in SQLite, rendered
only when a command arrives, so it costs nothing while idle.

    python3 main.py --port 2023         # serve (laptop or pi)
    python3 main.py --sim 2000          # headless balance sweep
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sqlite3
import sys
import time

import content as C

VERSION = "0.1.0"
PROMPT = "OPR> "
LOG_KEEP = 2000
INT_KEYS = {"start_cores", "start_lines", "demand_ceiling", "fault_roll_every"}


# --------------------------------------------------------------------------
# world
# --------------------------------------------------------------------------
def new_staff(name):
    """A person on the payroll: experience, mood, pay, and any absence."""
    return {"name": name, "level": 1, "xp": 0, "morale": 0, "pay": 1.0,
            "away_until": 0, "away_why": "", "wants_raise": False}


class World:
    FIELDS = ("money", "cores", "lines", "subs", "rep", "ticks", "last_tick", "tier", "queue")

    def __init__(self, db=None):
        self.db = db
        self.money = C.BALANCE["start_money"]
        self.cores = C.BALANCE["start_cores"]
        self.lines = C.BALANCE["start_lines"]
        self.subs = 0
        self.rep = C.BALANCE["start_rep"]
        self.ticks = 0
        self.last_tick = int(time.time())
        self.tier = 0
        self.queue = []
        self.mode = "wall"         # "wall" = the clock drives it, "session" = only while someone is on
        self.faults = []          # [{"kind": "line"|"module", "n": int, "age": int}]
        self.tick_seconds = TICK_SECONDS
        self.live = 0              # sockets logged in right now (desk and browser do not count)
        self.extra_slots = 0
        self.rep_trend = 0.0
        self.refused_share = 0.0
        self.contracts = []        # offers and live jobs, see load_contracts
        self.overdraft_ticks = 0
        self.bodges = 0            # cheap repairs, each one makes the plant flakier
        self.next_contract_id = 1
        self.won_tick = 0          # 0 = still renting
        self.staff = {}            # role -> {"name": str, "level": int}
        self.mods = []             # [{"name", "ticks_left", "effects", "good"}]
        self.owned = set()         # upgrade names
        self.site = 0              # index into C.LOCATIONS
        self.earned = 0.0          # lifetime revenue, for prestige
        self.standing = 0
        self.prestiges = 0         # how many times this world has been sold          # permanent prestige points
        self.last_mod_names = []
        self.history = []          # [(tick, cash, net)] for the web chart
        self.record = 0            # most callers ever served at once
        self.seen = {}             # where the last operator left off, see mark_seen
        self.last_report = ""      # the away report printed at the last login
        self.lesson_next = len(C.LESSONS)   # index of the next teaching offer; only
                                            # a brand-new world starts at zero
        self.run = self.new_run()  # this run's record, printed if it ends in liquidation
        self.project = None        # {"name", "left", "total", "while", "then"}: one at a time
        self.price = 100           # per cent of the list rate, see PRICE
        self.accounts = {}         # company -> {"goodwill", "met", "short", "gone_until"}
        self.later = []            # follow-ups promised by past decisions: [{"at", ...}]
        self.rival = None          # the bureau across town, see RIVAL
        self.rival_next = C.RIVAL_ARRIVES   # tick the next rival opens, while there is none
        self.loan = None           # {"owed", "due"}
        self.bank_barred = 0       # tick until which the bank will not lend
        self.works = set()         # engineering works finished this cycle
        self.modules = []          # one per module: {"model": name, "born": tick installed}
        self.line_born = []        # one per line: tick installed
        self.units_dirty = True
        self.incident = None       # {"key", "left"}: a question waiting for the operator
        self.statement = ""        # the last final statement
        self.subs = int(self.demand)      # after the tycoon fields, demand reads them

    # -- faults (the only thing in the world that breaks) -------------------
    def load_faults(self):
        if self.db is None:
            return
        self.faults = [{"kind": k, "n": n, "age": 0} for k, n in
                       self.db.execute("SELECT kind, n FROM fault ORDER BY id")]

    def fault_rows(self):
        return [(f["kind"], f["n"]) for f in self.faults]

    def add_fault(self, kind, n):
        self.faults.append({"kind": kind, "n": n, "age": 0})
        if self.db is not None:
            self.db.execute("INSERT INTO fault (kind, n) VALUES (?,?)", (kind, n))

    def clear_faults(self):
        self.faults = []
        if self.db is not None:
            self.db.execute("DELETE FROM fault")

    # -- contracts ---------------------------------------------------------
    def load_contracts(self):
        if self.db is None:
            return
        rows = self.db.execute(
            "SELECT id, company, work, want, duration, ticks_left, fee, penalty, "
            "state, breached, lesson FROM contract ORDER BY id").fetchall()
        self.contracts = [dict(zip(("id", "company", "work", "want", "duration",
                                    "ticks_left", "fee", "penalty", "state",
                                    "breached", "lesson"), r)) for r in rows]
        self.next_contract_id = (max((c["id"] for c in self.contracts), default=0) + 1)

    def save_contract(self, c):
        if self.db is None:
            return
        self.db.execute(
            "INSERT INTO contract (id, company, work, want, duration, ticks_left, fee, "
            "penalty, state, breached, lesson) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET ticks_left=excluded.ticks_left, "
            "state=excluded.state, breached=excluded.breached",
            (c["id"], c["company"], c["work"], c["want"], c["duration"],
             c["ticks_left"], c["fee"], c["penalty"], c["state"], c["breached"],
             c.get("lesson")))

    def drop_contract(self, cid):
        self.contracts = [c for c in self.contracts if c["id"] != cid]
        if self.db is not None:
            self.db.execute("DELETE FROM contract WHERE id=?", (cid,))

    def offers(self):
        return [c for c in self.contracts if c["state"] == "offered"]

    def active(self):
        return [c for c in self.contracts if c["state"] == "active"]

    def offer_contract(self, rng, company=None, force=False):
        b = C.BALANCE
        if len(self.offer_slots()) >= b["contract_max_pending"] and not force:
            return
        if company is None:
            # customers who blacklisted us, or who the rival has for now, do not call
            willing = [(n, w) for n, w in C.CONTRACT_COMPANIES
                       if self.account(n)["goodwill"] > C.BLACKLIST
                       and self.account(n)["gone_until"] <= self.ticks]
            if not willing:
                return
            company = rng.choice(willing)
        company, work = company
        g = self.account(company)["goodwill"]
        # work is sized against what you can actually staff, not against demand:
        # demand grows during the term, so an offer pitched at capacity is a trap.
        # A customer who was served well comes back with more.
        size = rng.choice((0.5, 0.6, 0.75, 0.9)) * (1.0 + C.GOODWILL_SIZE * max(0, g))
        want = max(3, int(self.capacity * size))
        duration = rng.choice((200, 300, 420))
        fee = round(want * b["contract_fee_per_caller"] * (1.0 + C.GOODWILL_FEE * g), 0)
        penalty = round(fee * b["contract_penalty_rate"], 0)
        c = {"id": self.next_contract_id, "company": company, "work": work,
             "want": want, "duration": duration, "ticks_left": duration,
             "fee": fee, "penalty": penalty, "state": "offered", "breached": 0}
        self.next_contract_id += 1
        self.contracts.append(c)
        self.save_contract(c)
        self.queue.append(C.CONTRACT_OFFER % (company, work, want, duration,
                                              fee, penalty, c["id"], c["id"]))
        self.journal("%s|%s|%d|%d" % (company, work, want, c["id"]), "offer", fee)

    # -- the run's record -------------------------------------------------------
    RUN_KEYS = ("start", "peak", "peak_tick", "faults", "met", "short", "earned")

    def new_run(self):
        return {"start": self.ticks, "peak": self.money, "peak_tick": self.ticks,
                "faults": 0, "met": 0, "short": 0, "earned": 0.0}

    def load_run(self):
        self.run = self.new_run()
        for k, v in self.db.execute("SELECT name, n FROM upgrade WHERE name LIKE 'run:%'"):
            if k[4:] in self.run:
                self.run[k[4:]] = v
        row = self.db.execute("SELECT line FROM log WHERE kind='statement' "
                              "ORDER BY id DESC LIMIT 1").fetchone()
        self.statement = row[0] if row else ""

    def save_run(self):
        for k in self.RUN_KEYS:
            self.put("run:" + k, self.run[k])

    # -- long projects and incidents --------------------------------------------
    def save_pending(self):
        if self.db is None:
            return
        for kind, obj in (("project", self.project), ("incident", self.incident)):
            if obj is None:
                self.db.execute("DELETE FROM pending WHERE kind=?", (kind,))
            else:
                self.db.execute("INSERT OR REPLACE INTO pending (kind, data) VALUES (?,?)",
                                (kind, json.dumps(obj)))

    BUSINESS = ("accounts", "later", "rival", "loan")

    def save_business(self):
        """Price, customers, promises, the rival, the loan and the staff."""
        if self.db is None:
            return
        self.put("price", self.price)
        self.put("bank_barred", self.bank_barred)
        rival = self.rival if self.rival else {"next": self.rival_next}
        for kind, obj in (("accounts", self.accounts), ("later", self.later),
                          ("rival", rival), ("loan", self.loan)):
            self.db.execute("INSERT OR REPLACE INTO pending (kind, data) VALUES (?,?)",
                            (kind, json.dumps(obj)))
        for role, info in self.staff.items():
            self.db.execute("INSERT OR REPLACE INTO staff (role, name, level, data) "
                            "VALUES (?,?,?,?)", (role, info["name"], info.get("level", 1),
                                                 json.dumps(info)))

    def save_units(self):
        if self.db is None or not self.units_dirty:
            return
        self.sync_units()
        self.db.execute("DELETE FROM unit")
        self.db.executemany("INSERT INTO unit (kind, n, model, born) VALUES (?,?,?,?)",
                            [("module", i + 1, m["model"], m["born"])
                             for i, m in enumerate(self.modules)]
                            + [("line", i + 1, None, b) for i, b in enumerate(self.line_born)])
        self.units_dirty = False

    def load_units(self):
        if self.db is None:
            return
        rows = self.db.execute("SELECT kind, n, model, born FROM unit ORDER BY kind, n").fetchall()
        self.modules = [{"model": m, "born": b} for k, _n, m, b in rows if k == "module"]
        self.line_born = [b for k, _n, _m, b in rows if k == "line"]
        self.units_dirty = False
        self.sync_units()                  # a save from before ages starts everything new

    def load_pending(self):
        if self.db is None:
            return
        for kind, data in self.db.execute("SELECT kind, data FROM pending"):
            obj = json.loads(data)
            if kind == "project":
                self.project = obj
            elif kind == "incident" and any(i["key"] == obj["key"] for i in
                                            C.INCIDENTS + C.TRIGGERED_INCIDENTS):
                self.incident = obj
            elif kind == "accounts":
                self.accounts = obj or {}
            elif kind == "later":
                self.later = obj or []
            elif kind == "loan":
                self.loan = obj
            elif kind == "rival":
                if obj and "name" in obj:
                    self.rival = obj
                elif obj:
                    self.rival_next = int(obj.get("next", self.rival_next))
        if self.db.execute("SELECT 1 FROM pending WHERE kind='rival'").fetchone() is None:
            # a save from before the rival: give it the same two days' grace
            self.rival_next = self.ticks + C.RIVAL_ARRIVES

    def start_project(self, name, ticks, while_, then, offline=None):
        self.project = {"name": name, "left": int(ticks), "total": int(ticks),
                        "while": dict(while_ or {}), "then": then, "offline": offline}
        self.save_pending()
        self.journal("%s BEGAN: %s." % (name, span(ticks)), "project", ticks)

    def advance_project(self):
        if not self.project:
            return
        self.project["left"] -= 1
        if self.project["left"] > 0:
            if self.project["left"] % 10 == 0:
                self.save_pending()
            return
        p, self.project = self.project, None
        then = p.get("then") or {}
        if "site" in then:
            self.site = int(then["site"])
            self.put("site", self.site)
        if "upgrade" in then:
            self.owned.add(then["upgrade"])
            if self.db is not None:
                self.db.execute("INSERT OR REPLACE INTO upgrade (name, n) VALUES (?,1)",
                                ("up:" + then["upgrade"],))
        if then.get("mod"):
            self.add_mod(*then["mod"])
        if then.get("work"):
            self.works.add(then["work"])
            self.put("work:" + then["work"], 1)
        renew = then.get("renew")
        if renew:
            self.sync_units()
            if renew[0] == "module" and int(renew[1]) <= self.cores:
                self.modules[int(renew[1]) - 1]["born"] = self.ticks
            elif renew[0] == "lines":
                self.line_born = [self.ticks] * self.lines
            self.units_dirty = True
        self.save_pending()
        msg = "%s FINISHED." % p["name"]
        self.queue.append(msg)
        self.journal(msg, "project", p["total"])

    def incident_def(self):
        """The open incident, its text filled in with who and what it is about."""
        base = next(i for i in C.INCIDENTS + C.TRIGGERED_INCIDENTS
                    if i["key"] == self.incident["key"])
        ctx = self.incident.get("ctx")
        if not ctx:
            return base

        def f(t):
            return t.format(**ctx)
        return dict(base, title=f(base["title"]), text=f(base["text"]),
                    choices=[(w, f(d), dict(o, say=f(o.get("say", "DONE."))))
                             for w, d, o in base["choices"]])

    def raise_incident(self, key, ctx):
        """Open a decision the game itself brings up. False if one is already open."""
        if self.incident:
            return False
        inc = next(i for i in C.TRIGGERED_INCIDENTS if i["key"] == key)
        self.incident = {"key": key, "left": inc["ticks"], "ctx": ctx}
        self.save_pending()
        title = self.incident_def()["title"]
        self.queue.append("DECISION WAITING: %s. TYPE INCIDENT." % title)
        self.journal("DECISION WAITING: %s." % title, "incident", inc["ticks"])
        return True

    def roll_incident(self, rng):
        if self.incident or self.lesson_next < len(C.LESSONS):
            return
        if any(c.get("lesson") for c in self.contracts):
            return                         # let the teaching offers finish first
        last = getattr(self, "last_incident", None)
        pool = [i for i in C.INCIDENTS if i["key"] != last] or C.INCIDENTS
        inc = rng.choice(pool)
        self.incident = {"key": inc["key"], "left": inc["ticks"]}
        self.last_incident = inc["key"]
        self.save_pending()
        self.queue.append("DECISION WAITING: %s. TYPE INCIDENT." % inc["title"])
        self.journal("DECISION WAITING: %s." % inc["title"], "incident", inc["ticks"])

    def decide(self, word, by_default=False):
        """Take one of the incident's choices. Returns (ok, message)."""
        inc = self.incident_def()
        word = word.upper()
        choices = inc["choices"]
        if word.isdigit() and 1 <= int(word) <= len(choices):
            word = choices[int(word) - 1][0]
        match = [c for c in choices if c[0] == word]
        if not match:
            return False, "THE CHOICES ARE %s." % ", ".join(c[0] for c in choices)
        _w, _desc, out = match[0]
        cost = -out.get("money", 0.0)
        if cost > 0 and self.money < cost and not by_default:
            return False, "INSUFFICIENT FUNDS: THAT COSTS %.2f, CASH %.2f." % (cost, self.money)
        if out.get("project") and self.project:
            return False, ("THE ENGINEERS ARE BUSY WITH %s. CHOOSE SOMETHING ELSE, OR "
                           "WAIT FOR THEM." % self.project["name"])
        self.money += out.get("money", 0.0)
        self.rep = max(0.0, min(20.0, self.rep + out.get("rep", 0.0)))
        if out.get("mod"):
            self.add_mod(*out["mod"])
        if out.get("fault"):
            self.break_unit(out["fault"])
        if out.get("project"):
            name, ticks, while_, then_mod = out["project"]
            self.start_project(name, ticks, while_, {"mod": then_mod} if then_mod else {})
        if out.get("account"):
            self.goodwill_change(out["account"][0], out["account"][1])
        if out.get("later"):
            later = dict(out["later"])
            later["at"] = self.ticks + later.pop("ticks")
            self.later.append(later)
        role = (self.incident.get("ctx") or {}).get("role")
        if role in self.staff:
            info = self.staff[role]
            if out.get("pay"):
                info["pay"] = round(info.get("pay", 1.0) * (1.0 + out["pay"]), 3)
            if out.get("morale"):
                info["morale"] = max(-2, min(2, info.get("morale", 0) + out["morale"]))
            if out.get("leave"):
                self.staff_leaves(role, "LEFT FOR %s" % (self.rival or {}).get("name", "ANOTHER BUREAU"))
        self.incident = None
        self.save_pending()
        say = ("NOBODY ANSWERED. " if by_default else "") + out.get("say", "DONE.")
        self.queue.append(say)
        self.journal("%s: %s" % (inc["title"], say), "incident" if by_default else "op")
        return True, say

    def advance_incident(self, rng):
        b = C.BALANCE
        if self.incident:
            self.incident["left"] -= 1
            if self.incident["left"] <= 0:
                self.decide(self.incident_def()["default"], by_default=True)
            elif self.incident["left"] % 10 == 0:
                self.save_pending()
        elif self.ticks % C.INCIDENT_EVERY == 0 and rng.random() < C.INCIDENT_CHANCE:
            self.roll_incident(rng)

    def break_unit(self, kind):
        """Break one working unit of this kind, never the last one."""
        dead = {f["n"] for f in self.faults_of(kind)}
        total = self.lines if kind == "line" else self.cores
        working = [i for i in range(1, total + 1) if i not in dead]
        if len(working) < 2:
            return None
        n = working[-1]
        before = self.net
        self.add_fault(kind, n)
        self.journal("FAULT: %s %d DOWN" % (kind.upper(), n), "fault", before)
        self.run["faults"] += 1
        return n

    # -- customers who remember ---------------------------------------------------
    def account(self, company):
        return self.accounts.setdefault(company, {"goodwill": 0, "met": 0, "short": 0,
                                                  "gone_until": 0})

    def goodwill_change(self, company, delta, tally=None):
        a = self.account(company)
        before = a["goodwill"]
        a["goodwill"] = max(-3, min(3, before + delta))
        if tally:
            a[tally] += 1
        if delta < 0 and a["goodwill"] <= -1:
            # an unhappy customer talks
            self.rep = max(0.0, self.rep - C.GOODWILL_REP)
        if before > C.BLACKLIST >= a["goodwill"]:
            msg = "%s WILL NOT SEND US WORK AGAIN." % company
            self.queue.append(msg)
            self.journal(msg, "account")
        elif before < 2 <= a["goodwill"]:
            msg = "%s NOW SEND US THEIR BEST WORK." % company
            self.queue.append(msg)
            self.journal(msg, "account")

    def keep_promises(self):
        """Follow-ups that past decisions set in motion arrive when due."""
        due = [x for x in self.later if x["at"] <= self.ticks]
        if not due:
            return
        self.later = [x for x in self.later if x["at"] > self.ticks]
        for x in due:
            a = self.account(x["company"])
            a["goodwill"] = max(a["goodwill"], x.get("goodwill", 0))
            self.queue.append(x["say"])
            self.journal(x["say"], "account")
            self.offer_contract(random.Random(self.ticks), company=(x["company"], x["work"]),
                                force=True)

    # -- staff are people ---------------------------------------------------------
    def staff_level(self, xp):
        return 1 + sum(1 for t in C.STAFF_LEVEL_TICKS[1:] if xp >= t)

    def staff_leaves(self, role, why):
        who = self.staff.pop(role, None)
        if who is None:
            return
        if self.db is not None:
            self.db.execute("DELETE FROM staff WHERE role=?", (role,))
        msg = "%s %s HAS %s." % (role, who["name"], why)
        self.queue.append(msg)
        self.journal(msg, "staff")
        if self.rival and "LEFT FOR" in why:
            self.rival["strength"] = min(1.0, self.rival["strength"] + 0.05)

    def tend_staff(self, rng):
        for role, info in list(self.staff.items()):
            away = info.get("away_until", 0)
            if away and away <= self.ticks:
                why = info.get("away_why", "")
                info["away_until"], info["away_why"] = 0, ""
                if why == "TRAINING":
                    nxt = C.STAFF_LEVEL_TICKS[min(info["level"], len(C.STAFF_LEVEL_TICKS) - 1)]
                    info["xp"] = max(info["xp"], nxt)
                msg = "%s %s IS BACK%s." % (role, info["name"],
                                            " FROM THE COURSE" if why == "TRAINING" else "")
                self.queue.append(msg)
                self.journal(msg, "staff")
            if info.get("away_until", 0) > self.ticks:
                continue
            info["xp"] = info.get("xp", 0) + 1
            level = min(3, self.staff_level(info["xp"]))
            if level > info.get("level", 1):
                info["level"] = level
                info["wants_raise"] = True
                msg = "%s %s IS NOW LEVEL %d." % (role, info["name"], level)
                self.queue.append(msg)
                self.journal(msg, "staff")
        if self.ticks % C.STAFF_SICK_EVERY == 0:
            for role, info in list(self.staff.items()):
                if info.get("away_until", 0) > self.ticks:
                    continue
                if info.get("morale", 0) < 0 and rng.random() < C.STAFF_QUIT_CHANCE:
                    self.staff_leaves(role, "LEFT FOR %s" % (self.rival or {}).get(
                        "name", "ANOTHER BUREAU"))
                elif rng.random() < C.STAFF_SICK_CHANCE:
                    info["away_until"] = self.ticks + rng.randint(*C.STAFF_SICK_TICKS)
                    info["away_why"] = "SICK"
                    msg = "%s %s IS OFF SICK." % (role, info["name"])
                    self.queue.append(msg)
                    self.journal(msg, "staff")
        for role, info in self.staff.items():
            if info.get("wants_raise") and not self.incident:
                if self.raise_incident("raise", {"name": info["name"], "role": role,
                                                 "rival": (self.rival or {}).get("name", "")}):
                    info["wants_raise"] = False
                break

    # -- the rival bureau ---------------------------------------------------------
    def tend_rival(self, rng):
        if not self.rival:
            if self.ticks >= self.rival_next:
                last = getattr(self, "last_rival", None)
                pool = [r for r in C.RIVALS if r[0] != last] or C.RIVALS
                name, where = rng.choice(pool)
                self.rival = {"name": name, "where": where, "strength": 0.2,
                              "price": 90, "born": self.ticks}
                self.last_rival = name
                msg = "%s HAVE OPENED AT %s, AND CHARGE %d%% OF OUR LIST RATE." % (
                    name, where, self.rival["price"])
                self.queue.append(msg)
                self.journal(msg, "rival")
            return
        r = self.rival
        r["strength"] = min(1.0, r["strength"] + C.RIVAL_GROWTH)
        if self.rep >= C.RIVAL_REP_BEATS:
            r["strength"] -= C.RIVAL_SHRINK
        if r["strength"] <= 0.0:
            msg = "%s HAVE GONE INTO RECEIVERSHIP. THEIR CUSTOMERS ARE RINGING US." % r["name"]
            self.queue.append(msg)
            self.journal(msg, "rival")
            self.rival = None
            self.rival_next = self.ticks + 2 * C.RIVAL_ARRIVES
            return
        if (self.ticks - r["born"]) % C.RIVAL_EVERY or self.ticks == r["born"]:
            return
        move = rng.choices([m for m, _w in C.RIVAL_MOVES],
                           weights=[w for _m, w in C.RIVAL_MOVES])[0]
        msg = None
        if move == "cut" and r["price"] > 70:
            r["price"] -= 10
            msg = "%s HAVE CUT THEIR RATE TO %d%% OF OUR LIST." % (r["name"], r["price"])
        elif move == "raise" and r["price"] < 150:
            r["price"] += 10
            msg = "%s HAVE PUT THEIR RATE UP TO %d%% OF OUR LIST." % (r["name"], r["price"])
        elif move == "advert":
            r["strength"] = min(1.0, r["strength"] + 0.1)
            msg = "%s ARE ADVERTISING IN THE EVENING ARGUS." % r["name"]
        elif move == "undercut":
            offers = [c for c in self.offers() if not c.get("lesson")]
            if offers:
                c = offers[0]
                self.drop_contract(c["id"])
                msg = "%s UNDERCUT US ON THE %s JOB (OFFER %d). IT HAS GONE TO THEM." % (
                    r["name"], c["company"], c["id"])
        elif move == "poach_account":
            names = [n for n, _w in C.CONTRACT_COMPANIES]
            weak = [n for n in names if self.account(n)["goodwill"] <= 0
                    and self.account(n)["gone_until"] <= self.ticks]
            if weak:
                n = rng.choice(weak)
                self.account(n)["gone_until"] = self.ticks + 2 * 1440
                msg = "%s HAVE TAKEN %s'S WORK FOR NOW." % (r["name"], n)
        elif move == "poach_staff" and self.staff:
            role = max(self.staff, key=lambda k: (self.staff[k].get("level", 1),
                                                  -self.staff[k].get("morale", 0)))
            info = self.staff[role]
            self.raise_incident("poach", {"name": info["name"], "role": role,
                                          "rival": r["name"]})
        if msg:
            self.queue.append(msg)
            self.journal(msg, "rival")

    # -- the bank -----------------------------------------------------------------
    def plant_value(self):
        self.sync_units()
        return (sum(self.model(m["model"])["price_core"] for m in self.modules)
                + self.lines * C.BALANCE["price_line"])

    def loan_limit(self):
        if self.ticks < self.bank_barred:
            return 0.0
        trust = max(0.3, min(1.0, self.rep / 5.0))
        limit = (C.LOAN_LIMIT_BASE + C.LOAN_LIMIT_PLANT * self.plant_value()) * trust
        return max(0.0, limit - (self.loan["owed"] if self.loan else 0.0))

    def tend_loan(self):
        if self.loan and self.ticks >= self.loan["due"]:
            owed = self.loan["owed"]
            self.money -= owed
            self.loan = None
            msg = "THE LOAN FELL DUE. THE BANK TOOK %.2f." % owed
            self.queue.append(msg)
            self.journal(msg, "bank", owed)

    # -- the first offers teach the game ---------------------------------------
    def teach(self):
        """Issue the next teaching offer when the last one is settled, and pay
        a repair lesson as soon as its line is back."""
        for c in list(self.contracts):
            lesson = c.get("lesson") or ""
            if lesson.startswith("repair:"):
                n = int(lesson.split(":")[1])
                if not any(f["kind"] == "line" and f["n"] == n for f in self.faults):
                    self.pay_lesson(c)
        if self.lesson_next >= len(C.LESSONS):
            return
        if any(c.get("lesson") for c in self.contracts):
            return
        L = C.LESSONS[self.lesson_next]
        kind, want, lesson = L["teach"], L.get("want", 0), L["teach"]
        if kind == "lines":
            want = self.capacity + L["extra"]
        if kind == "repair":
            dead = {f["n"] for f in self.faults_of("line")}
            working = [i for i in range(1, self.lines + 1) if i not in dead]
            if len(working) < 2:
                return                     # never break the last line; ask later
            n = working[-1]
            self.add_fault("line", n)
            lesson = "repair:%d" % n
        c = {"id": self.next_contract_id, "company": L["company"], "work": L["work"],
             "want": want, "duration": L.get("duration", 0),
             "ticks_left": L.get("duration", 0), "fee": L["fee"], "penalty": 0.0,
             "state": "offered", "breached": 0, "lesson": lesson}
        self.next_contract_id += 1
        self.contracts.append(c)
        self.save_contract(c)
        self.lesson_next += 1
        self.put("lesson", self.lesson_next)
        self.queue.append("TELEX FROM %s: %s" % (c["company"], lesson_hint(self, c)))
        self.journal("TELEX: %s. %s" % (c["company"], lesson_hint(self, c)), "lesson", c["fee"])

    def pay_lesson(self, c):
        self.money += c["fee"]
        msg = C.LESSON_PAID.format(company=c["company"], fee=c["fee"])
        self.queue.append(msg)
        self.journal(msg, "lesson", c["fee"])
        self.drop_contract(c["id"])
        return msg

    def offer_slots(self):
        return [c for c in self.contracts if c["state"] in ("offered", "active")]

    def faults_of(self, kind):
        return [f for f in self.faults if f["kind"] == kind]

    # -- buffs and debuffs -------------------------------------------------
    def roll_mod(self, rng):
        live = {m["name"] for m in self.mods}
        pool = [m for m in C.MODS if m[0] not in live]
        if not pool:
            return
        name, ticks, effects, _weight, good = rng.choice(pool)
        self.add_mod(name, ticks, effects, good)
        self.journal(name, "good" if good else "bad", ticks)
        self.queue.append(("GOOD NEWS: " if good else "BAD NEWS: ") + name
                          + " - " + ", ".join("%s %+.2f" % (k, v) for k, v in effects.items())
                          + " FOR %d TICKS" % ticks)

    def add_mod(self, name, ticks, effects, good):
        """Put a buff or debuff in force; the same name again restarts it."""
        self.mods = [m for m in self.mods if m["name"] != name]
        self.mods.append({"name": name, "ticks_left": ticks,
                          "effects": dict(effects), "good": good})
        if self.db is not None:
            self.db.execute("INSERT OR REPLACE INTO mod (name, ticks_left, effects, good) "
                            "VALUES (?,?,?,?)",
                            (name, ticks, json.dumps(effects), 1 if good else 0))

    def expire_mods(self):
        still = []
        for m in self.mods:
            m["ticks_left"] -= 1
            if m["ticks_left"] <= 0:
                self.queue.append("%s HAS PASSED." % m["name"])
                self.journal(m["name"], "passed")
                if self.db is not None:
                    self.db.execute("DELETE FROM mod WHERE name=?", (m["name"],))
            else:
                still.append(m)
        if len(still) != len(self.mods) and self.db is not None:
            for m in still:
                self.db.execute("UPDATE mod SET ticks_left=? WHERE name=?",
                                (m["ticks_left"], m["name"]))
        self.mods = still

    # -- upgrades ----------------------------------------------------------
    def load_upgrades(self):
        if self.db is None:
            return
        row = self.db.execute("SELECT n FROM upgrade WHERE name='slots'").fetchone()
        self.extra_slots = row[0] if row else 0
        row = self.db.execute("SELECT n FROM upgrade WHERE name='building'").fetchone()
        self.won_tick = int(row[0]) if row else 0
        row = self.db.execute("SELECT n FROM upgrade WHERE name='site'").fetchone()
        self.site = int(row[0]) if row else 0
        row = self.db.execute("SELECT n FROM upgrade WHERE name='earned'").fetchone()
        self.earned = float(row[0]) if row else 0.0
        row = self.db.execute("SELECT n FROM upgrade WHERE name='standing'").fetchone()
        self.standing = int(row[0]) if row else 0
        row = self.db.execute("SELECT n FROM upgrade WHERE name='prestiges'").fetchone()
        self.prestiges = int(row[0]) if row else 0
        row = self.db.execute("SELECT n FROM upgrade WHERE name='bodges'").fetchone()
        self.bodges = int(row[0]) if row else 0
        self.load_run()
        row = self.db.execute("SELECT n FROM upgrade WHERE name='lesson'").fetchone()
        self.lesson_next = int(row[0]) if row else len(C.LESSONS)
        row = self.db.execute("SELECT n FROM upgrade WHERE name='record'").fetchone()
        # an old save has no record: start from today, not from zero
        self.record = int(row[0]) if row else min(self.subs, self.capacity)
        self.seen = {k[5:]: v for k, v in self.db.execute(
            "SELECT name, n FROM upgrade WHERE name LIKE 'seen:%'")}
        if "log" not in self.seen:
            # a save from before the away report: start counting from here, so
            # the first report is not the whole history of the world
            self.mark_seen()
        self.owned = {r[0][3:] for r in self.db.execute(
            "SELECT name FROM upgrade WHERE name LIKE 'up:%'")}
        self.staff = {}
        for role, nm, lvl, data in self.db.execute("SELECT role, name, level, data FROM staff"):
            info = new_staff(nm)
            info.update(json.loads(data) if data else {"level": lvl or 1})
            self.staff[role] = info
        row = self.db.execute("SELECT n FROM upgrade WHERE name='price'").fetchone()
        self.price = int(row[0]) if row else 100
        row = self.db.execute("SELECT n FROM upgrade WHERE name='bank_barred'").fetchone()
        self.bank_barred = int(row[0]) if row else 0
        self.works = {r[0][5:] for r in self.db.execute(
            "SELECT name FROM upgrade WHERE name LIKE 'work:%'") if r[0][5:] in C.WORKS}
        self.mods = [{"name": n, "ticks_left": int(t), "effects": json.loads(e), "good": bool(g)}
                     for n, t, e, g in self.db.execute(
                         "SELECT name, ticks_left, effects, good FROM mod")]

    def put(self, name, value):
        if self.db is None:
            return
        self.db.execute("INSERT INTO upgrade (name, n) VALUES (?,?) "
                        "ON CONFLICT(name) DO UPDATE SET n=excluded.n", (name, value))

    def reset_world(self):
        """Sell up: everything back to a fresh machine, standing kept."""
        b = C.BALANCE
        self.money = b["start_money"]
        self.cores = b["start_cores"]
        self.lines = b["start_lines"]
        self.extra_slots = 0
        self.subs = 0
        self.rep = b["start_rep"]
        self.ticks = 0
        self.last_tick = int(time.time())
        self.queue = []
        self.faults = []
        self.contracts = []
        self.staff = {}
        self.mods = []
        self.owned = set()
        self.site = 0
        self.earned = 0.0
        self.won_tick = 0
        self.overdraft_ticks = 0
        saved_prestiges = self.prestiges
        if self.db is not None:
            for table in ("fault", "contract", "staff", "mod"):
                self.db.execute("DELETE FROM %s" % table)
            self.db.execute("DELETE FROM upgrade WHERE name NOT IN ('standing', 'prestiges')")
            self.save()
        self.prestiges = saved_prestiges
        self.record = 0
        self.lesson_next = len(C.LESSONS)     # a seller knows the trade
        self.run = self.new_run()
        self.project = None
        self.incident = None
        self.save_pending()
        self.modules, self.line_born = [], []
        self.sync_units()
        self.price, self.accounts, self.later = 100, {}, []
        self.rival, self.rival_next = None, C.RIVAL_ARRIVES
        self.loan, self.works = None, set()
        self.mark_seen()
        self.subs = int(self.demand)
        self.queue.append("A NEW MACHINE ARRIVES ON MONDAY MORNING.")

    def own_building(self):
        self.won_tick = self.ticks
        if self.db is not None:
            self.db.execute("INSERT INTO upgrade (name, n) VALUES ('building', ?) "
                            "ON CONFLICT(name) DO UPDATE SET n=excluded.n", (self.ticks,))

    def buy_slot(self, n):
        self.extra_slots += n
        if self.db is not None:
            self.db.execute("INSERT INTO upgrade (name, n) VALUES ('slots', ?) "
                            "ON CONFLICT(name) DO UPDATE SET n=excluded.n", (self.extra_slots,))

    # -- persistence -------------------------------------------------------
    def load(self):
        if self.db is None:
            return
        row = self.db.execute(
            "SELECT money, cores, lines, subs, rep, ticks, last_tick, tier, queue, mode, tick_seconds "
            "FROM world WHERE id=1"
        ).fetchone()
        if row is None:
            self.save()
            self.lesson_next = 0           # a brand-new world: teach it
            self.put("lesson", 0)
            return
        (self.money, self.cores, self.lines, self.subs, self.rep,
         self.ticks, self.last_tick, self.tier) = row[:8]
        self.queue = json.loads(row[8] or "[]")
        if row[9] in ("wall", "session"):
            self.mode = row[9]
        if row[10]:
            self.tick_seconds = int(row[10])
        if not self.subs:
            self.subs = int(self.demand)
        self.rep_trend = 0.0
        self.refused_share = 0.0
        self.contracts = []        # offers and live jobs, see load_contracts
        self.overdraft_ticks = 0
        self.next_contract_id = 1
        self.won_tick = 0          # 0 = still renting
        self.staff = {}            # role -> {"name": str, "level": int}
        self.mods = []             # [{"name", "ticks_left", "effects", "good"}]
        self.owned = set()         # upgrade names
        self.site = 0              # index into C.LOCATIONS
        self.earned = 0.0          # lifetime revenue, for prestige
        self.standing = 0
        self.prestiges = 0         # how many times this world has been sold          # permanent prestige points
        self.last_mod_names = []
        self.history = []          # [(tick, cash, net)] for the web chart
        self.subs = int(self.demand)      # after the tycoon fields, demand reads them

    def save(self):
        if self.db is None:
            return
        self.db.execute(
            "INSERT INTO world (id, money, cores, lines, subs, rep, ticks, last_tick, tier, queue, mode, tick_seconds) "
            "VALUES (1,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET money=excluded.money, cores=excluded.cores, "
            "lines=excluded.lines, subs=excluded.subs, rep=excluded.rep, ticks=excluded.ticks, "
            "last_tick=excluded.last_tick, tier=excluded.tier, queue=excluded.queue, "
            "mode=excluded.mode, tick_seconds=excluded.tick_seconds",
            (self.money, self.cores, self.lines, self.subs, self.rep,
             self.ticks, self.last_tick, self.tier, json.dumps(self.queue), self.mode,
             self.tick_seconds),
        )
        self.save_run()
        self.save_units()
        self.save_business()

    def journal(self, line, kind="op", val=None):
        """One row per thing that happened, stamped with the tick, the cash and
        the net rate just after it. kind tells the away report what it was;
        "op" is anything the operator did, which the report leaves out."""
        if self.db is not None:
            self.db.execute("INSERT INTO log (tick, line, cash, net, val, kind) "
                            "VALUES (?,?,?,?,?,?)",
                            (self.ticks, line, self.money, self.net, val, kind))
            self.db.execute(
                "DELETE FROM log WHERE id <= (SELECT MAX(id) FROM log) - ?", (LOG_KEEP,)
            )

    # -- the away report -----------------------------------------------------
    def mark_seen(self):
        """Remember where the operator left off: the report starts here."""
        if self.db is None:
            return
        last = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM log").fetchone()[0]
        self.seen = {"log": last, "tick": self.ticks, "cash": round(self.money, 2),
                     "net": round(self.net, 4), "served": self.served}
        for k, v in self.seen.items():
            self.put("seen:" + k, v)

    def away_rows(self):
        if self.db is None:
            return []
        return self.db.execute(
            "SELECT tick, line, cash, net, val, kind FROM log WHERE id > ? "
            "AND kind IS NOT NULL AND kind != 'op' ORDER BY id",
            (int(self.seen.get("log", 0)),)).fetchall()

    # -- staff, upgrades, buffs: one effect pool ------------------------------
    def effects(self):
        eff = {k: (1.0 if k.endswith("_mult") else 0.0) for k in C.EFFECT_KEYS}
        eff["queue_rate"] = C.BALANCE["queue_rate"]
        sources = []
        for role in self.staff:
            sources.append(self.staff_effects(role))
        for name in self.owned:
            sources.append(C.UPGRADES[name]["effects"])
        for name in self.works:
            sources.append(C.WORKS[name]["effects"])
        if self.price != 100:
            # the price's pull on callers is applied in demand(), after the
            # market's ceiling: only what we earn per caller goes in the pool
            sources.append({"rate_mult": self.price / 100.0})
        for m in self.mods:
            sources.append(m["effects"])
        if self.project:
            sources.append(self.project["while"])
        if self.incident:
            sources.append(self.incident_def()["while"])
        if self.standing:
            worth = self.standing ** 0.6
            sources.append({"rate_mult": 1.0 + 0.06 * worth,
                            "demand_mult": 1.0 + 0.05 * worth})
        for e in sources:
            for k, v in e.items():
                if k.endswith("_mult"):
                    eff[k] *= v
                else:
                    eff[k] += v
        if self.bodges:
            eff["fault_mult"] *= 1.0 + 0.05 * self.bodges
        return eff

    @property
    def salaries(self):
        return sum(C.ROLES[r]["salary"] * i.get("pay", 1.0) for r, i in self.staff.items())

    def price_effects(self):
        p = self.price / 100.0
        if abs(p - 1.0) < 1e-9:
            return {}
        return {"rate_mult": p, "demand_mult": p ** -C.PRICE_ELASTICITY}

    def rival_take(self):
        """Share of our market the rival takes: its strength, and how our price
        compares with its own."""
        if not self.rival:
            return 0.0
        ratio = (self.price / float(self.rival["price"])) ** C.PRICE_ELASTICITY
        return max(0.0, min(C.RIVAL_MAX_TAKE, C.RIVAL_TAKE * self.rival["strength"] * ratio))

    def staff_effects(self, role):
        """A role's effects, stronger with experience; nothing while they are away."""
        info = self.staff[role]
        if info.get("away_until", 0) > self.ticks:
            return {}
        scale = 1.0 + C.STAFF_LEVEL_GAIN * (info.get("level", 1) - 1)
        out = {}
        for k, v in C.ROLES[role]["effects"].items():
            out[k] = 1.0 + (v - 1.0) * scale if k.endswith("_mult") else v * scale
        return out

    @property
    def interest(self):
        return self.loan["owed"] * C.LOAN_RATE_DAY / 1440.0 if self.loan else 0.0

    @property
    def hardware(self):
        return C.HARDWARE[min(self.prestiges, len(C.HARDWARE) - 1)]

    @property
    def hw_name(self):
        return self.hardware["name"]

    # -- the plant, unit by unit --------------------------------------------------
    def sync_units(self):
        """Keep one record per module and per line. Anything that changes the
        counts without saying which unit (old saves, tests, the sweep) gets new
        standard-issue units at the end, or loses them from the end."""
        if len(self.modules) != self.cores:
            del self.modules[self.cores:]
            while len(self.modules) < self.cores:
                self.modules.append({"model": self.hw_name, "born": self.ticks})
            self.units_dirty = True
        if len(self.line_born) != self.lines:
            del self.line_born[self.lines:]
            self.line_born += [self.ticks] * (self.lines - len(self.line_born))
            self.units_dirty = True

    def model(self, name):
        return next((h for h in C.HARDWARE if h["name"] == name), self.hardware)

    def module_model(self, n):
        self.sync_units()
        return self.model(self.modules[n - 1]["model"])

    def on_sale(self):
        """Models that can be bought now, with their price: the cycle's standard,
        and the next generation at a premium."""
        top = min(len(C.HARDWARE) - 1, self.prestiges + 1)
        out = []
        for i, h in enumerate(C.HARDWARE[:top + 1]):
            if i < min(self.prestiges, len(C.HARDWARE) - 1):
                continue                       # an older generation is not made any more
            price = h["price_core"] * (C.AHEAD_PREMIUM if i > self.prestiges else 1.0)
            out.append((h, price))
        return out

    def wear(self, born):
        age = max(0, self.ticks - born)
        return min(C.WEAR_CAP, 1.0 + age / float(C.WEAR_TICKS))

    def servicing(self):
        """The module out of the rack for service, if any."""
        if self.project and self.project.get("offline"):
            return int(self.project["offline"])
        return None

    def working_module_numbers(self):
        dead = {f["n"] for f in self.faults_of("module")}
        away = self.servicing()
        return [n for n in range(1, self.cores + 1) if n not in dead and n != away]

    @property
    def module_room(self):
        """Callers the working modules can process, model by model."""
        self.sync_units()
        return sum(self.model(self.modules[n - 1]["model"])["core_users"]
                   for n in self.working_module_numbers())

    @property
    def module_room_all(self):
        self.sync_units()
        return sum(self.model(m["model"])["core_users"] for m in self.modules)

    def fault_chance(self):
        """The odds of a breakdown at a roll: the base rate, the effects in
        force, and how worn and how good the plant is on average."""
        self.sync_units()
        b = C.BALANCE
        factors = [self.wear(self.modules[n - 1]["born"])
                   * self.model(self.modules[n - 1]["model"])["fault_mult"]
                   for n in self.working_module_numbers()]
        dead = {f["n"] for f in self.faults_of("line")}
        factors += [self.wear(born) for i, born in enumerate(self.line_born) if i + 1 not in dead]
        plant = sum(factors) / len(factors) if factors else 1.0
        return b["fault_roll_chance"] * self.effects()["fault_mult"] * plant

    @property
    def price_growth(self):
        return C.BALANCE["prestige_price_growth"] ** self.prestiges

    @property
    def goal_price(self):
        return C.BALANCE["building_price"] * self.price_growth

    def site_price(self, idx):
        base = C.LOCATIONS[idx][1]
        return base * self.price_growth if idx > 0 else 0.0

    @property
    def site_rent(self):
        return C.LOCATIONS[self.site][3] * (C.BALANCE["prestige_rent_growth"] ** self.prestiges)

    @property
    def site_name(self):
        return C.LOCATIONS[self.site][0]

    @property
    def room(self):
        """(modules, lines) this site has space for."""
        return C.SITE_ROOM[min(self.site, len(C.SITE_ROOM) - 1)]

    @property
    def ceiling(self):
        return C.LOCATIONS[self.site][2]

    # -- derived -----------------------------------------------------------
    @property
    def working_lines(self):
        return max(0, self.lines - len(self.faults_of("line")))

    @property
    def working_cores(self):
        return len(self.working_module_numbers())

    @property
    def capacity(self):
        return int(self.capacity_raw * self.effects()["capacity_mult"])

    @property
    def capacity_raw(self):
        """Capacity before anything scales it: what the lamps can show."""
        return min(self.module_room, self.working_lines * C.BALANCE["line_users"])

    def capacity_why(self):
        """What is moving capacity the way it moved, in a few words for the panel."""
        sources = []
        if self.project:
            sources.append(("MOVE" if (self.project.get("then") or {}).get("site") is not None
                            else "WORKS", self.project["while"].get("capacity_mult", 1.0)))
        for role in self.staff:
            sources.append((role, C.ROLES[role]["effects"].get("capacity_mult", 1.0)))
        for m in self.mods:
            sources.append((m["name"], m["effects"].get("capacity_mult", 1.0)))
        down = self.capacity < self.capacity_raw
        return ", ".join(name for name, mult in sources
                         if (mult < 1.0 if down else mult > 1.0))

    @property
    def served(self):
        return min(self.subs, self.capacity)

    @property
    def slots(self):
        return (self.hardware["start_slots"] + self.extra_slots
                + int(self.effects()["slots_add"]))

    @property
    def running(self):
        """Connected callers whose work is actually executing this tick."""
        return min(self.served, self.slots)

    @property
    def queued(self):
        return max(0, self.served - self.slots)

    @property
    def revenue(self):
        eff = self.effects()
        rate = C.BALANCE["rate_per_user"] * eff["rate_mult"]
        queued_rate = rate * min(1.0, eff["queue_rate"])
        return self.running * rate + self.queued * queued_rate

    @property
    def outgoings(self):
        b = C.BALANCE
        self.sync_units()
        raw = (self.site_rent + sum(self.model(m["model"])["core_cost"] for m in self.modules)
               + self.lines * b["line_cost"] + self.salaries)
        return raw * self.effects()["cost_mult"] + self.interest

    @property
    def net(self):
        return self.revenue - self.outgoings

    @property
    def demand(self):
        b = C.BALANCE
        eff = self.effects()
        raw = (b["base_demand"] + (b["demand_growth"] + eff["growth_add"]) * self.ticks
               + self.rep * b["demand_per_rep"])
        d = raw * eff["demand_mult"] + eff["demand_flat"]
        # the market is as big as the site allows; our price decides how much of
        # it will pay, and the rival takes its share of what is left. Applied
        # after the ceiling, so a dearer price always costs callers.
        market = max(0, min(self.ceiling, d))
        return market * self.price_pull() * (1.0 - self.rival_take())

    def price_pull(self):
        return (self.price / 100.0) ** -C.PRICE_ELASTICITY

    # -- the clock ---------------------------------------------------------
    def tick(self, rng):
        b = C.BALANCE
        self.ticks += 1

        # callers dial in or give up according to reputation; a live contract
        # guarantees its callers, which is the whole reason to take the work
        guaranteed = sum(c["want"] for c in self.active())
        want = int(round(self.demand + rng.uniform(-1.0, 1.0)))
        self.subs = max(0, max(want, guaranteed))

        # money
        self.money += self.revenue - self.outgoings

        # reputation: the penalty is proportional to the share of callers you
        # refuse, so being a little short is survivable and recoverable, while
        # turning away most of your callers still wrecks you. The old rule
        # deducted a flat amount for a single refused caller, which pinned rep
        # at zero forever the moment demand passed capacity by one person.
        refused = max(0, self.subs - self.served)
        share = (refused / self.subs) if self.subs else 0.0
        self.rep_trend = b["rep_gain"] - b["rep_busy_penalty"] * share
        self.rep = max(0.0, min(20.0, self.rep + self.rep_trend))
        self.refused_share = share
        if refused and self.ticks % 25 == 0:
            self.queue.append("%d CALLER%s HEARD A BUSY SIGNAL (%.0f%% OF CALLS)."
                              % (refused, "" if refused == 1 else "S", 100 * share))

        # things break. only things you actually own can break, and every one
        # of them has a command that fixes it.
        for f in self.faults:
            f["age"] += 1
        if self.faults:
            self.rep = max(0.0, self.rep - b["fault_rep_penalty"])
            if self.ticks % 25 == 0:
                self.queue.append("STILL DOWN: %s. SEE STATUS, THEN REPAIR."
                                  % ", ".join(f["kind"].upper() + " %d" % f["n"] for f in self.faults))
        # a break is rolled once every N ticks at a fixed chance, so the odds are
        # legible ("ten per cent every hundred ticks") instead of a per-tick
        # probability nobody can reason about
        if self.ticks % b["fault_roll_every"] == 0 and rng.random() < self.fault_chance():
            self.roll_fault(rng)

        # the world changes: buffs and debuffs arrive and expire
        if self.ticks % b["mod_every"] == 0 and rng.random() < b["mod_chance"]:
            self.roll_mod(rng)
        self.expire_mods()
        self.advance_project()
        self.advance_incident(rng)
        self.tend_staff(rng)
        self.tend_rival(rng)
        self.keep_promises()
        self.tend_loan()
        if self.price > C.PRICE_FAIR:          # dear, and they say so
            self.rep = max(0.0, self.rep - C.PRICE_REP * (self.price - C.PRICE_FAIR))

        self.earned += self.revenue
        self.run["earned"] += self.revenue
        if self.money > self.run["peak"]:
            self.run["peak"], self.run["peak_tick"] = self.money, self.ticks
        self.history.append((self.ticks, self.money, self.net))
        del self.history[:-360]

        # money and reputation events (no faults: these cost or pay, nothing to fix)
        if rng.random() < b["event_chance"]:
            weights = [w for w, *_ in C.EVENTS]
            _w, msg, dm, dr = rng.choices(C.EVENTS, weights=weights, k=1)[0]
            self.money += dm
            self.rep = max(0.0, self.rep + dr)
            self.queue.append(msg)
            self.journal(msg, "event", dm)

        # contracts: new offers appear, live jobs are scored, deadlines land
        if self.ticks % b["contract_every"] == 0 and rng.random() < b["contract_roll_chance"]:
            self.offer_contract(rng)
        self.settle_contracts()
        self.teach()

        # a new high for callers served at once is worth telling someone about
        if self.served > self.record:
            self.record = self.served
            self.put("record", self.record)
            self.journal("RECORD: %d CALLERS AT ONCE" % self.record, "record", self.record)

        # being overdrawn has a clock on it
        if self.money < 0:
            self.overdraft_ticks += 1
            if self.overdraft_ticks == 1:
                self.journal("OVERDRAWN", "overdrawn", self.money)
            if self.overdraft_ticks % 60 == 0:
                self.queue.append("OVERDRAWN FOR %d TICKS. AT %d THE EXCHANGE DISCONNECTS US."
                                  % (self.overdraft_ticks, b["overdraft_grace"]))
            if self.overdraft_ticks >= b["overdraft_grace"]:
                self.liquidate()
        else:
            self.overdraft_ticks = 0

    def settle_contracts(self):
        b = C.BALANCE
        for c in list(self.contracts):
            if c["state"] != "active":
                continue
            c["ticks_left"] -= 1
            if self.served < c["want"]:
                c["breached"] += 1
            if c["ticks_left"] <= 0:
                allowed = c["duration"] * b["contract_breach_tolerance"]
                if c.get("lesson"):
                    self.pay_lesson(c)         # a grant: paid however it went
                    continue
                if c["breached"] > allowed:
                    self.run["short"] += 1
                    self.goodwill_change(c["company"], -1, "short")
                    self.money -= c["penalty"]
                    self.rep = max(0.0, self.rep - 1.0)
                    self.queue.append("%s: WE SHORTED THEM ON %d OF %d TICKS. PENALTY %.0f CHARGED."
                                      % (c["company"], c["breached"], c["duration"], c["penalty"]))
                    self.journal("CONTRACT BREACHED: %s PENALTY %.2f" % (c["company"], c["penalty"]),
                                 "breach", c["penalty"])
                else:
                    self.run["met"] += 1
                    self.goodwill_change(c["company"], +1, "met")
                    self.money += c["fee"]
                    self.rep = min(20.0, self.rep + 0.5)
                    self.queue.append("%s: SERVED. FEE %.0f PAID, ACCOUNT IN GOOD STANDING."
                                      % (c["company"], c["fee"]))
                    self.journal("CONTRACT MET: %s FEE %.2f" % (c["company"], c["fee"]),
                                 "paid", c["fee"])
                    c["fee"] = 0
                self.drop_contract(c["id"])

    def liquidate(self):
        b = C.BALANCE
        proceeds = (self.lines * b["price_line"]
                    + self.extra_slots * b["price_slot"]) * b["liquidation_rate"]
        statement = final_statement(self, proceeds)
        if self.loan:
            self.journal("THE BANK WROTE OFF %.2f." % self.loan["owed"], "bank",
                         self.loan["owed"])
            self.loan = None
        self.bank_barred = self.ticks + C.LOAN_BARRED
        self.money = max(0.0, self.money + proceeds)
        self.lines = b["start_lines"]
        self.line_born = [self.ticks] * self.lines
        self.units_dirty = True
        # every line was sold; the exchange's minimum set arrives working
        self.faults = [f for f in self.faults if f["kind"] != "line"]
        if self.db is not None:
            self.db.execute("DELETE FROM fault WHERE kind='line'")
        self.extra_slots = 0
        # persist only what was sold. The old DELETE FROM upgrade also took
        # standing, prestiges, site, bodges and equipment off the save while
        # play carried on with them, so a restart silently lost them.
        self.put("slots", 0)
        self.subs = 0
        self.rep = 0.0
        self.overdraft_ticks = 0
        for c in list(self.contracts):
            if c["state"] == "active":
                self.drop_contract(c["id"])
        self.queue.append("THE EXCHANGE HAS DISCONNECTED US FOR NON-PAYMENT.")
        self.queue.append("LINES SOLD AT %.0f%%, DEBT WRITTEN OFF. WE STILL HAVE THE MACHINE."
                          % (100 * b["liquidation_rate"]))
        self.journal("LIQUIDATION: PROCEEDS %.2f" % proceeds, "liquidation", proceeds)
        self.statement = statement
        self.journal(statement, "statement")
        self.queue.append("A FINAL STATEMENT HAS BEEN PRINTED. TYPE STATEMENT.")
        self.run = self.new_run()          # the machine is still ours: a new run

    def roll_fault(self, rng):
        """Break one line or one module that is currently working."""
        before = self.net
        candidates = []
        working_lines = [i + 1 for i in range(self.lines)
                         if i + 1 not in {f["n"] for f in self.faults_of("line")}]
        working_cores = self.working_module_numbers()
        if len(working_lines) > 1:
            candidates.append("line")
        if len(working_cores) > 1:
            candidates.append("module")
        if not candidates:
            return
        self.sync_units()
        kind = rng.choice(candidates)
        # the worn ones go first: each unit is as likely to fail as it is worn
        if kind == "line":
            n = rng.choices(working_lines,
                            weights=[self.wear(self.line_born[i - 1]) for i in working_lines])[0]
            self.add_fault("line", n)
            self.queue.append(rng.choice(C.FAULT_LINE) % n)
        else:
            n = rng.choices(working_cores, weights=[
                self.wear(self.modules[i - 1]["born"])
                * self.model(self.modules[i - 1]["model"])["fault_mult"]
                for i in working_cores])[0]
            self.add_fault("module", n)
            self.queue.append(rng.choice(C.FAULT_MODULE) % n)
        self.journal("FAULT: %s %d DOWN" % (kind.upper(), n), "fault", before)
        self.run["faults"] += 1

    def advance(self, now, rng):
        """Run any ticks that elapsed while nobody was connected."""
        if self.last_tick <= 0:
            self.last_tick = now
            return 0
        if self.mode == "session" and self.live <= 0:
            self.last_tick = now        # frozen: quiet time is never owed back
            return 0
        elapsed = int((now - self.last_tick) // self.tick_seconds)
        if elapsed <= 0:
            return 0
        elapsed = min(elapsed, 60 * 24 * 30)  # a month of catch-up at most
        for _ in range(elapsed):
            self.tick(rng)
        self.last_tick += elapsed * self.tick_seconds
        return elapsed


TICK_SECONDS = 60


# --------------------------------------------------------------------------
# rendering (only ever called on a command)
# --------------------------------------------------------------------------
def r_status(w):
    """The system report, written for the paper: 49 columns and 17 lines.
    Eleven fixed lines, then the most pressing notes."""
    cap, by_mod, by_line = w.capacity, w.module_room, w.working_lines * C.BALANCE["line_users"]
    limit = ("NOTHING IN SERVICE" if cap == 0 else "LIMITED BY LINES" if by_line < by_mod
             else "LIMITED BY MODULES" if by_mod < by_line else "MATCHED")
    refused = max(0, w.subs - w.served)
    trend = w.rep_trend
    out = ["",
           "  SYSTEM REPORT%sTICK %d" % (" " * max(1, REPORT_WIDTH - 20 - len(str(w.ticks))), w.ticks),
           RULE,
           "  CASH               %10s" % "{:,.2f}".format(w.money),
           "  REVENUE PER TICK   %+10.3f" % w.revenue,
           "  OUTGOINGS PER TICK %+10.3f   NET %+.3f" % (-w.outgoings, w.net),
           "  CALLERS            %3d SERVED OF %d WANTING" % (w.served, w.subs),
           "  TURNED AWAY        %3d  (%d%% REFUSED)" % (refused, round(100 * w.refused_share)),
           "  USABLE CAPACITY    %3d  %s" % (cap, limit),
           "  PLANT              %d MODULE%s, %d LINES" % (w.cores, "" if w.cores == 1 else "S", w.lines),
           "  BATCH SLOTS        %3d  RUNNING %d, QUEUED %d" % (w.slots, w.running, w.queued),
           "  REPUTATION        %5.2f  %+.3f A TICK" % (w.rep, trend),
           RULE]
    notes = []
    if w.overdraft_ticks:
        notes.append("OVERDRAWN %d/%d TICKS, THEN WE ARE CUT OFF"
                     % (w.overdraft_ticks, C.BALANCE["overdraft_grace"]))
    if w.faults:
        down = ", ".join("%s %d" % (f["kind"].upper(), f["n"]) for f in w.faults[:3])
        notes.append("DOWN: %s%s  (FAULTS)" % (down, " +%d" % (len(w.faults) - 3)
                                               if len(w.faults) > 3 else ""))
    spare = w.working_lines - w.module_room
    if spare > 0:
        notes.append("%d LINE%s IDLE: NO MODULE TO SERVE THEM" % (spare, "" if spare == 1 else "S"))
    if w.incident:
        notes.append("DECISION WAITING: TYPE INCIDENT")
    if w.project:
        notes.append("%s: %s LEFT" % (w.project["name"][:30], short(w.project["left"])))
    if w.loan:
        notes.append("LOAN %.2f, DUE IN %s" % (w.loan["owed"], short(w.loan["due"] - w.ticks)))
    live = w.active()
    if live:
        first = min(live, key=lambda c: c["ticks_left"])
        notes.append("%d LIVE JOB%s; %s ENDS IN %s" % (len(live), "" if len(live) == 1 else "S",
                                                      first["company"][:16], short(first["ticks_left"])))
    if w.rival:
        notes.append("%s TAKE %d%%" % (w.rival["name"][:30], round(100 * w.rival_take())))
    if w.price != 100:
        notes.append("WE CHARGE %d%% OF THE LIST RATE" % w.price)
    if w.offers():
        notes.append("%d OFFER%s ON THE TELEPRINTER" % (len(w.offers()), "" if len(w.offers()) == 1 else "S"))
    room = 17 - (len(out) - 1)
    if len(notes) > room:
        notes = notes[:room - 1] + ["AND %d MORE: EFFECTS, OFFERS" % (len(notes) - room + 1)]
    out += ["  " + n for n in notes] or ["  NOTHING NEEDS YOU."]
    out.append("")
    return "\r\n".join(out) + "\r\n"


# --------------------------------------------------------------------------
# the away report: what happened while nobody was on, as a timeline
# --------------------------------------------------------------------------
REPORT_WIDTH = 49          # the paper sheet prints 51 columns; keep a margin
REPORT_EVENTS = 9          # timeline rows: with the header and the NOW line the
                           # report stays inside the sheet's 17 printed lines


def clock(t):
    """Ticks are minutes: +H:MM since the operator left."""
    h, m = divmod(max(0, int(t)), 60)
    return "+%d:%02d" % (h, m)


def span(t):
    t = max(0, int(t))
    days, rest = divmod(t, 1440)
    h, m = divmod(rest, 60)
    parts = []
    if days:
        parts.append("%d DAY%s" % (days, "" if days == 1 else "S"))
    if h:
        parts.append("%d HOUR%s" % (h, "" if h == 1 else "S"))
    if m and not days:
        parts.append("%d MINUTE%s" % (m, "" if m == 1 else "S"))
    return " ".join(parts) or "UNDER A MINUTE"


def real_time(seconds):
    """Wall-clock time, the way the window counts it down: 45S, 7M54S, 2H10M."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return "%dS" % seconds
    if seconds < 3600:
        return "%dM%02dS" % (seconds // 60, seconds % 60)
    return "%dH%02dM" % (seconds // 3600, seconds % 3600 // 60)


def both_times(w, ticks):
    """Bureau time and what that is in real time at the current tick rate."""
    return "%s OF BUREAU TIME; ABOUT %s AT %d SECOND%s A TICK" % (
        short(ticks), real_time(ticks * w.tick_seconds), w.tick_seconds,
        "" if w.tick_seconds == 1 else "S")


def short(t):
    h, m = divmod(max(0, int(t)), 60)
    return "%dH%02d" % (h, m)


def fold(prefix, text, width=REPORT_WIDTH):
    import textwrap
    return textwrap.wrap(text, width, initial_indent=prefix, break_on_hyphens=False,
                         subsequent_indent=" " * len(prefix)) or [prefix]


def away_entries(w, rows, start):
    """Turn journal rows into (priority, minute, text) entries. Lower priority
    survives a long absence; everything is printed in the order it happened."""
    now = w.ticks - start
    entries = []
    small, small_money = 0, 0.0
    record = None
    live = {(f["kind"], f["n"]) for f in w.faults}
    sold = [t - start for t, _l, _c, _n, _v, k in rows if k == "liquidation"]
    for tick, line, _cash, net, val, kind in rows:
        at = tick - start
        if kind == "fault":
            parts = line.split()                    # FAULT: MODULE 2 DOWN
            unit, n = parts[1], int(parts[2])
            what = ("LINE %d WENT DEAD." if unit == "LINE" else "MODULE %d FAILED.") % n
            before, after = (val or 0.0), (net or 0.0)
            # a disconnection later on changes the whole plant: count to there
            until = min([s for s in sold if s >= at] + [now])
            if unit == "LINE" and until < now:
                what += " SOLD OFF WITH THE REST AT THE DISCONNECTION."
                if before - after > 0.005:
                    what = what[:-1] + (", HAVING COST ABOUT %.0f."
                                        % ((before - after) * (until - at)))
            elif (unit.lower(), n) in live:
                if before - after > 0.005:
                    what += (" NET %+.2f -> %+.2f A TICK. DOWN %s AT THE LOWER "
                             "RATE, ABOUT %.0f NOT EARNED. STILL DOWN."
                             % (before, after, short(until - at), (before - after) * (until - at)))
                else:
                    what += (" NO CALLERS LOST AT THE TIME: THE SPARE %s CARRIED "
                             "THEM. STILL DOWN."
                             % ("LINES" if unit == "LINE" else "MODULES"))
            else:
                what += " SINCE CLEARED."
            entries.append((1, at, what))
        elif kind == "liquidation":
            entries.append((0, at, "THE EXCHANGE DISCONNECTED US FOR NON-PAYMENT. "
                                   "THE LINES WERE SOLD; WE KEPT THE MACHINE. "
                                   "TYPE STATEMENT FOR THE FINAL SHEET."))
        elif kind == "overdrawn":
            entries.append((1, at, "WENT OVERDRAWN."))
        elif kind == "breach":
            who = line.split(": ", 1)[-1].rsplit(" PENALTY", 1)[0]
            entries.append((1, at, "%s: WE SHORTED THEM. PENALTY %.0f." % (who, val or 0)))
        elif kind == "paid":
            who = line.split(": ", 1)[-1].rsplit(" FEE", 1)[0]
            entries.append((2, at, "%s: SERVED IN FULL. FEE %.0f PAID." % (who, val or 0)))
        elif kind in ("good", "bad"):
            entries.append((2, at, "%s: %s, FOR %s."
                            % ("GOOD NEWS" if kind == "good" else "BAD NEWS", line,
                               short(val or 0))))
        elif kind == "passed":
            entries.append((5, at, "%s PASSED." % line))
        elif kind == "offer":
            company, work, want, cid = line.split("|")
            entries.append((3, at, "TELEX: %s WANT %s CALLERS FOR %s. OFFER %s."
                            % (company, want, work, cid)))
        elif kind == "event":
            small += 1
            small_money += val or 0.0
            entries.append((4, at, line))
        elif kind == "record":
            record = (at, int(val or 0))
        elif kind in ("lesson", "incident", "project", "bank"):
            entries.append((1, at, line))
        elif kind in ("rival", "staff", "account"):
            entries.append((2, at, line))
    if record:
        entries.append((3, record[0], "BUSIEST YET: %d CALLERS AT ONCE." % record[1]))
    if small > 2:                               # everyday business, folded
        entries = [e for e in entries if e[0] != 4]
        entries.append((3, None, "ALSO %d SMALL MATTERS ON THE CONSOLE, %+.2f IN ALL."
                        % (small, small_money)))
    return entries


def final_statement(w, proceeds):
    """The run's record, printed when the exchange cuts us off. Written before
    anything is sold, so it describes the business as it stood."""
    r = w.run
    rule = "  " + "-" * (REPORT_WIDTH - 2)
    out = ["  FINAL STATEMENT%sTICK %d" % (" " * max(1, REPORT_WIDTH - 22 - len(str(w.ticks))),
                                          w.ticks), rule]
    out += fold("  ", "THE EXCHANGE HAS DISCONNECTED US FOR NON-PAYMENT. THE LINES "
                      "WENT FOR %.2f AND THE DEBT IS WRITTEN OFF." % proceeds)
    out.append("")
    out.append("  THE RUN")
    row = "    %-20s %s"
    out.append(row % ("TRADED FOR", span(w.ticks - int(r["start"]))))
    out.append(row % ("TOOK IN", "{:,.2f}".format(r["earned"])))
    out.append(row % ("HIGHEST BALANCE", "{:,.2f}".format(r["peak"])))
    out.append(row % ("MOST CALLERS", "%d AT ONCE" % w.record))
    out.append(row % ("CONTRACTS", "%d MET, %d SHORTED" % (int(r["met"]), int(r["short"]))))
    out.append(row % ("BREAKDOWNS", "%d" % int(r["faults"])))
    staff = ["%s %s" % (r, i["name"]) for r, i in sorted(w.staff.items())] or ["NOBODY"]
    out += fold(row % ("STAFF AT THE END", ""), ", ".join(staff))
    out.append(rule)
    out += fold("  ", "WE KEEP THE MACHINE: %d MODULE%s, AND %d FRESH LINES FROM THE "
                      "EXCHANGE. THE NEXT RUN STARTS NOW."
                % (w.cores, "" if w.cores == 1 else "S", C.BALANCE["start_lines"]))
    return "\r\n".join(out) + "\r\n"


def r_away(w):
    """The report printed at login. Empty when no time has passed."""
    seen = w.seen or {}
    start = int(seen.get("tick", w.ticks))
    rows = w.away_rows()
    gone = w.ticks - start
    if gone <= 0 and not rows:
        return ""
    entries = away_entries(w, rows, start)
    col = "  %%-%ds" % (len(clock(gone)) + 2)      # the time column, as wide as needed
    rule = "  " + "-" * (REPORT_WIDTH - 2)
    cash0 = float(seen.get("cash", w.money))
    out = fold("  ", "WHILE YOU WERE AWAY: %s." % span(gone))
    out.append(rule)
    out.append("  CASH %.2f -> %.2f   (%+.2f)" % (cash0, w.money, w.money - cash0))
    out += fold(col % "+0:00", "NET %+.2f A TICK, %d CALLER%s ON."
                % (float(seen.get("net", w.net)), int(seen.get("served", 0)),
                   "" if int(seen.get("served", 0)) == 1 else "S"))

    # keep the most important rows that fit, then print them in order
    budget, kept = REPORT_EVENTS, []
    for e in sorted(entries, key=lambda e: (e[0], e[1] if e[1] is not None else 1e9)):
        cost = len(fold(col % "", e[2]))
        if cost <= budget:
            kept.append(e)
            budget -= cost
    dropped = len(entries) - len(kept)
    timed = sorted((e for e in kept if e[1] is not None), key=lambda e: e[1])
    for _p, at, text in timed:
        out += fold(col % clock(at), text)
    for _p, _at, text in (e for e in kept if e[1] is None):
        out += fold(col % "", text)
    if not entries:
        out += fold(col % "", "A QUIET STRETCH. NOTHING BROKE AND THE MACHINE KEPT "
                    "EARNING." if w.money >= cash0 else
                    "A QUIET STRETCH. NOTHING BROKE, BUT THE RUNNING COSTS OUTRAN THE "
                    "CALLERS.")
    if dropped:
        out += fold(col % "", "AND %d SMALLER THING%s NOT LISTED."
                    % (dropped, "" if dropped == 1 else "S"))
    out.append(rule)
    now = ["NET %+.2f A TICK" % w.net]
    if w.faults:
        now.append("%d FAULT%s" % (len(w.faults), "" if len(w.faults) == 1 else "S"))
    if w.offers():
        now.append("%d OFFER%s WAITING" % (len(w.offers()), "" if len(w.offers()) == 1 else "S"))
    out += fold(col % "NOW", ", ".join(now) + ".")
    return "\r\n".join(out) + "\r\n"


RULE = "  " + "-" * (REPORT_WIDTH - 2)


def ok_err(ok, msg):
    return ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"


def joined(out):
    return "\r\n".join(out) + "\r\n"


# ---- your price -------------------------------------------------------------
def r_price(w):
    p = w.price / 100.0
    out = ["", "  OUR PRICE", RULE,
           "  WE CHARGE   %d%% OF THE LIST RATE" % w.price,
           "  PER CALLER  %.4f A TICK" % (C.BALANCE["rate_per_user"] * w.effects()["rate_mult"])]
    out.append("  CALLERS     %+d%% FROM THE PRICE ALONE" % round(100 * (p ** -C.PRICE_ELASTICITY - 1)))
    if w.rival:
        out.append("  RIVAL       CHARGES %d%%, TAKES %d%% OF CALLERS"
                   % (w.rival["price"], round(100 * w.rival_take())))
    if w.price > C.PRICE_FAIR:
        out.append("  OVER %d%%    REPUTATION %.3f A TICK"
                   % (C.PRICE_FAIR, -C.PRICE_REP * (w.price - C.PRICE_FAIR)))
    out.append(RULE)
    out += fold("  ", "PRICE n SETS IT, %d TO %d. DEARER: MORE PER CALLER, FEWER "
                      "CALLERS. WORTH IT WHEN MORE CALL THAN WE CAN CARRY."
                % (C.PRICE_MIN, C.PRICE_MAX))
    out.append("")
    return joined(out)


def do_price(w, args):
    try:
        n = int(args[0].rstrip("%"))
    except (IndexError, ValueError):
        return False, "PRICE n   (PER CENT OF THE LIST RATE, %d TO %d)" % (C.PRICE_MIN, C.PRICE_MAX)
    if not C.PRICE_MIN <= n <= C.PRICE_MAX:
        return False, "THE PRICE RUNS FROM %d TO %d PER CENT." % (C.PRICE_MIN, C.PRICE_MAX)
    before = w.served
    w.price = n
    w.journal("PRICE SET TO %d%%" % n)
    return True, ("WE NOW CHARGE %d%% OF THE LIST RATE. DEMAND %d CALLERS (WAS ROUGHLY %d "
                  "SERVED). NET %+.3f A TICK." % (n, int(w.demand), before, w.net))


# ---- customers ----------------------------------------------------------------
def r_accounts(w):
    out = ["", "  OUR CUSTOMERS", RULE, "  COMPANY              GOOD MET SHORT"]
    known = [n for n, _w in C.CONTRACT_COMPANIES] + sorted(
        n for n in w.accounts if n not in dict(C.CONTRACT_COMPANIES))
    for n in known:
        a = w.account(n)
        note = ""
        if a["goodwill"] <= C.BLACKLIST:
            note = "LOST"
        elif a["gone_until"] > w.ticks:
            note = "RIVAL'S"
        elif a["goodwill"] >= 2:
            note = "BEST"
        out.append("  %-20s %+3d %3d %4d  %s" % (n[:20], a["goodwill"], a["met"], a["short"], note))
    out.append(RULE)
    out += fold("  ", "SERVED IN FULL: +1. SHORTED OR DROPPED: -1, AND THEY TALK. GOODWILL "
                      "SIZES THEIR NEXT JOB AND ITS FEE. BEST: THEY SEND THEIR BEST WORK. "
                      "LOST: AT %d THEY STOP CALLING. RIVAL'S: TAKEN FOR NOW." % C.BLACKLIST)
    out.append("")
    return joined(out)


# ---- staff ----------------------------------------------------------------------
def r_staff(w):
    out = ["", "  STAFF", RULE]
    if not w.staff:
        out.append("  NOBODY WORKS HERE BUT YOU.")
    for role, info in w.staff.items():
        mood = {-2: "BITTER", -1: "SULKING", 0: "", 1: "CONTENT", 2: "LOYAL"}[
            max(-2, min(2, info.get("morale", 0)))]
        away = ""
        if info.get("away_until", 0) > w.ticks:
            away = "%s, BACK IN %s" % (info["away_why"], short(info["away_until"] - w.ticks))
        out.append("  %-9s %-11s LEVEL %d  %.2f/TICK" % (role, info["name"], info.get("level", 1),
                                                       C.ROLES[role]["salary"] * info.get("pay", 1.0)))
        detail = ", ".join(x for x in (away, mood) if x)
        if detail:
            out.append("            " + detail)
    out.append(RULE)
    out.append("  SALARIES  %.2f/TICK" % w.salaries)
    out.append("")
    for role, data in C.ROLES.items():
        if role not in w.staff:
            out.append("  HIRE %-9s %4.0f" % (role, data["fee"]))
    out.append("  FIRE <role>   TRAIN <role>: A COURSE, LEVEL UP")
    out.append("")
    return joined(out)


def do_train(w, args):
    if not args:
        return False, "TRAIN WHICH ROLE?"
    role = args[0].upper()
    if role not in w.staff:
        return False, "NOBODY WORKS HERE AS %s." % role
    info = w.staff[role]
    if info.get("away_until", 0) > w.ticks:
        return False, "%s IS AWAY (%s)." % (info["name"], info["away_why"])
    if info.get("level", 1) >= 3:
        return False, "%s KNOWS ALL THE COURSE COULD TEACH." % info["name"]
    cost = C.ROLES[role]["fee"] * C.TRAIN_COST
    if w.money < cost:
        return False, "INSUFFICIENT FUNDS: THE COURSE COSTS %.2f, CASH %.2f." % (cost, w.money)
    w.money -= cost
    info["away_until"], info["away_why"] = w.ticks + C.TRAIN_TICKS, "TRAINING"
    w.journal("SENT %s %s ON A COURSE FOR %.2f" % (role, info["name"], cost))
    return True, ("%s GOES ON A COURSE FOR %s, FOR %.2f, AND COMES BACK A LEVEL UP. "
                  "UNTIL THEN THE %s'S WORK IS NOT DONE." % (info["name"], span(C.TRAIN_TICKS),
                                                            cost, role))


# ---- engineering works -----------------------------------------------------------
def r_works(w):
    out = ["", "  ENGINEERING WORKS", RULE]
    for name, wk in C.WORKS.items():
        if name in w.works:
            state = "BUILT"
        elif w.project and (w.project.get("then") or {}).get("work") == name:
            state = "UNDER WAY"
        elif wk["needs"] and wk["needs"] not in w.works:
            state = "NEEDS " + wk["needs"]
        else:
            state = "%.0f, %s" % (wk["price"], short(wk["ticks"]))
        out.append("  %-22s %s" % (name, state))
        out += fold("      ", effect_brief(wk["effects"]))
    out.append(RULE)
    out.append("  BUILD <NAME>   one job at a time")
    out.append("")
    return joined(out)


def do_build(w, args):
    name = " ".join(args).upper()
    wk = C.WORKS.get(name)
    if wk is None:
        return False, "NO SUCH WORK. TYPE WORKS FOR THE LIST."
    if name in w.works:
        return False, "%s IS ALREADY BUILT." % name
    if wk["needs"] and wk["needs"] not in w.works:
        return False, "%s NEEDS %s FIRST." % (name, wk["needs"])
    if w.project:
        return False, ("THE ENGINEERS ARE BUSY WITH %s (%d TICKS LEFT). ONE JOB AT A TIME."
                       % (w.project["name"], w.project["left"]))
    if w.money < wk["price"]:
        return False, "INSUFFICIENT FUNDS: %s COSTS %.2f, CASH %.2f." % (name, wk["price"], w.money)
    w.money -= wk["price"]
    w.journal("PAID %.2f TO BUILD %s" % (wk["price"], name))
    w.start_project("BUILDING " + name, wk["ticks"], {}, {"work": name})
    return True, "THE ENGINEERS START ON %s: %s." % (name, span(wk["ticks"]))


# ---- the rival ----------------------------------------------------------------------
def r_rival(w):
    if not w.rival:
        return ("\r\n  NOBODY ELSE IN TOWN RUNS A BUREAU. YET.\r\n\r\n")
    r = w.rival
    out = ["", "  " + r["name"], RULE,
           "  AT          " + r["where"],
           "  OPEN FOR    " + span(w.ticks - r["born"]),
           "  THEY CHARGE %d%% OF LIST  (WE CHARGE %d%%)" % (r["price"], w.price),
           "  STRENGTH    %d%%" % round(100 * r["strength"]),
           "  THEY TAKE   %d%% OF OUR MARKET" % round(100 * w.rival_take()),
           RULE]
    out += fold("  ", "THEY TAKE MORE WHEN WE ARE DEARER, AND WITHER WHILE OUR "
                      "REPUTATION IS %.0f OR MORE. THEY UNDERCUT OPEN OFFERS, COURT "
                      "UNHAPPY CUSTOMERS, AND OFFER OUR STAFF JOBS." % C.RIVAL_REP_BEATS)
    out.append("")
    return joined(out)


# ---- the bank -------------------------------------------------------------------------
def r_bank(w):
    out = ["", "  THE BANK MANAGER", RULE]
    if w.loan:
        out.append("  WE OWE        %.2f" % w.loan["owed"])
        out.append("  INTEREST      %.3f A TICK (%.0f%% A DAY)" % (w.interest, 100 * C.LOAN_RATE_DAY))
        out.append("  DUE IN        %s" % short(max(0, w.loan["due"] - w.ticks)))
        out.append("  THEN THE BANK TAKES IT, OVERDRAWN OR NOT.")
    else:
        out.append("  WE OWE THE BANK NOTHING.")
    if w.ticks < w.bank_barred:
        out.append("  THE BANK WILL NOT LEND FOR %s." % short(w.bank_barred - w.ticks))
    else:
        out.append("  IT WOULD LEND  %.2f MORE" % w.loan_limit())
    out.append(RULE)
    out += fold("  ", "BORROW n, REPAY n (OR REPAY ALL). THE LIMIT FOLLOWS WHAT THE PLANT "
                      "COST AND OUR REPUTATION. A NEW LOAN IS DUE IN %s." % span(C.LOAN_TERM))
    out.append("")
    return joined(out)


def do_borrow(w, args):
    try:
        n = float(args[0])
    except (IndexError, ValueError):
        return False, "BORROW n"
    if n <= 0:
        return False, "THE BANK DOES NOT LEND NOTHING."
    if w.ticks < w.bank_barred:
        return False, "THE BANK WILL NOT LEND TO US FOR %s." % short(w.bank_barred - w.ticks)
    limit = w.loan_limit()
    if n > limit + 1e-6:
        return False, "THE MANAGER WILL GO TO %.2f AND NO FURTHER." % limit
    w.money += n
    if w.loan:
        w.loan["owed"] += n
    else:
        w.loan = {"owed": n, "due": w.ticks + C.LOAN_TERM}
    w.journal("BORROWED %.2f" % n)
    return True, ("BORROWED %.2f. WE OWE %.2f, AT %.3f A TICK IN INTEREST, DUE IN %s."
                  % (n, w.loan["owed"], w.interest, short(w.loan["due"] - w.ticks)))


def do_repay(w, args):
    if not w.loan:
        return False, "WE OWE THE BANK NOTHING."
    if args and args[0].upper() == "ALL":
        n = w.loan["owed"]
    else:
        try:
            n = float(args[0])
        except (IndexError, ValueError):
            return False, "REPAY n   |   REPAY ALL"
    n = min(n, w.loan["owed"])
    if n <= 0 or w.money < n:
        return False, "INSUFFICIENT FUNDS: CASH %.2f." % w.money
    w.money -= n
    w.loan["owed"] -= n
    w.journal("REPAID %.2f" % n)
    if w.loan["owed"] < 0.005:
        w.loan = None
        return True, "REPAID %.2f. WE OWE THE BANK NOTHING." % n
    return True, "REPAID %.2f. WE STILL OWE %.2f." % (n, w.loan["owed"])


def r_help(key=None):
    """The whole manual, grouped; HELP <tab> for one section; HELP INDEX for
    the tabs themselves (the window builds its tab row from that)."""
    sections = C.HELP_SECTIONS
    if key is None:
        return C.HELP
    key = key.upper()
    if key == "INDEX":
        return "TABS " + " ".join("%s=%s" % (k, t.replace(" ", "_")) for k, t, *_ in sections) + "\r\n"
    match = [x for x in sections if x[0] == key or x[1] == key or x[1] == "THE " + key]
    if not match:
        return ("\r\n  NO SUCH PART OF THE MANUAL. TRY: %s\r\n\r\n"
                % " ".join(k for k, *_ in sections))
    _k, title, intro, rows = match[0]
    out = [title, "  " + intro, ""] + ["  %-26s %s" % row for row in rows]
    return "\r\n".join(out) + "\r\n"


def plant_models(w):
    w.sync_units()
    counts = {}
    for m in w.modules:
        counts[m["model"]] = counts.get(m["model"], 0) + 1
    return ", ".join("%d %s" % (k, name) for name, k in counts.items()) or "NONE"


def r_machines(w):
    """The catalogue: what can be bought this cycle, and what each is like."""
    rule = "  " + "-" * (REPORT_WIDTH - 2)
    out = ["", "  MACHINES ON SALE", rule]
    for h, price in w.on_sale():
        ahead = price > h["price_core"] + 1e-9
        out.append("  %-11s %6.0f%s" % (h["name"], price, "  (A GENERATION AHEAD)" if ahead
                                          else "  (STANDARD ISSUE)"))
        out.append("      %d CALLERS  RUNS %.3f/TICK  BREAKS %s"
                   % (h["core_users"], h["core_cost"],
                      "AS OFTEN" if h["fault_mult"] == 1.0
                      else "%d%% LESS" % round(100 * (1 - h["fault_mult"]))))
        out += fold("      ", h["note"].upper() + ".")
    out.append("")
    out += fold("  ", "TYPE BUY TO ORDER ONE. REPLACE MODULE n <MODEL> TRADES AN OLD "
                      "MODULE IN. EVERY UNIT WEARS: TYPE PLANT.")
    out.append("")
    return "\r\n".join(out) + "\r\n"


def r_plant(w):
    """Every module and the lines: model, age, how worn, what to do about it."""
    w.sync_units()
    rule = "  " + "-" * (REPORT_WIDTH - 2)
    dead = {(f["kind"], f["n"]) for f in w.faults}
    away = w.servicing()
    out = ["", "  THE PLANT, UNIT BY UNIT", rule,
           "  UNIT    MODEL          AGE   ODDS"]
    for i, m in enumerate(w.modules, 1):
        state = ("DOWN" if ("module", i) in dead else "SERVICE" if i == away else "")
        wear = w.wear(m["born"])
        out.append("  MOD %-3d %-10s %7s  %+4d%%  %s"
                   % (i, m["model"], short(w.ticks - m["born"]), round(100 * (wear - 1)), state))
    if w.line_born:
        ages = [w.ticks - b for b in w.line_born]
        oldest = max(range(len(ages)), key=lambda i: ages[i]) + 1
        avg = sum(w.wear(b) for b in w.line_born) / len(w.line_born)
        out.append("  %-3d LINES (AVERAGE)  %7s  %+4d%%"
                   % (w.lines, short(sum(ages) / len(ages)), round(100 * (avg - 1))))
        out.append("  OLDEST: LINE %d, %s" % (oldest, short(max(ages))))
    out.append(rule)
    out.append("  NEXT BREAKDOWN ROLL: %.0f%% CHANCE" % (100 * min(1.0, w.fault_chance())))
    out.append("  SERVICE MODULE n    AN HOUR OUT, AS NEW")
    out.append("  SERVICE LINES       OVERHAUL EVERY LINE")
    out.append("  REPLACE MODULE n M  A NEW ONE NOW")
    out.append("")
    return "\r\n".join(out) + "\r\n"


def do_service(w, args):
    """SERVICE MODULE n, or SERVICE LINES. Returns (ok, message)."""
    w.sync_units()
    if not args:
        return False, "SERVICE MODULE n   |   SERVICE LINES"
    what = args[0].upper()
    if w.project:
        return False, ("THE ENGINEERS ARE BUSY WITH %s (%d TICKS LEFT). ONE JOB AT A TIME."
                       % (w.project["name"], w.project["left"]))
    mult = w.effects()["repair_mult"]
    if what in ("LINE", "LINES"):
        cost = round(w.lines * C.BALANCE["price_line"] * C.LINE_SERVICE_RATE * mult, 2)
        if w.money < cost:
            return False, "INSUFFICIENT FUNDS: THE OVERHAUL COSTS %.2f, CASH %.2f." % (cost, w.money)
        w.money -= cost
        w.journal("PAID %.2f FOR A LINE OVERHAUL" % cost)
        w.start_project("LINE OVERHAUL", C.LINE_SERVICE_TICKS, C.LINE_SERVICE_WHILE,
                        {"renew": ["lines"]})
        return True, ("THE EXCHANGE OVERHAULS ALL %d LINES FOR %.2f: %s AT %d%% CAPACITY, "
                      "THEN EVERY LINE IS AS GOOD AS NEW."
                      % (w.lines, cost, span(C.LINE_SERVICE_TICKS),
                         100 * C.LINE_SERVICE_WHILE["capacity_mult"]))
    if what not in ("MODULE", "MODULES", "CORE", "CORES") or len(args) < 2:
        return False, "SERVICE MODULE n   |   SERVICE LINES"
    try:
        n = int(args[1])
    except ValueError:
        return False, "THE NUMBER MUST BE A WHOLE NUMBER."
    if not 1 <= n <= w.cores:
        return False, "THERE IS NO MODULE %d." % n
    if any(f["kind"] == "module" and f["n"] == n for f in w.faults):
        return False, "MODULE %d IS DOWN: REPAIR IT FIRST, OR REPLACE IT." % n
    model = w.module_model(n)
    cost = round(model["price_core"] * C.SERVICE_RATE * mult, 2)
    if w.money < cost:
        return False, "INSUFFICIENT FUNDS: A SERVICE COSTS %.2f, CASH %.2f." % (cost, w.money)
    w.money -= cost
    w.journal("PAID %.2f TO SERVICE MODULE %d" % (cost, n))
    w.start_project("SERVICE MODULE %d" % n, C.SERVICE_TICKS, {},
                    {"renew": ["module", n]}, offline=n)
    return True, ("MODULE %d COMES OUT OF THE RACK FOR %s, FOR %.2f. IT GOES BACK AS GOOD "
                  "AS NEW." % (n, span(C.SERVICE_TICKS), cost))


def upgrade_candidates(w, name):
    """Modules worth upgrading to this model: lesser models, the most worn first."""
    w.sync_units()
    target = w.model(name)["core_users"]
    dead = {f["n"] for f in w.faults_of("module")}
    away = w.servicing()
    cands = [n for n in range(1, w.cores + 1)
             if w.module_model(n)["core_users"] < target and n != away]
    return sorted(cands, key=lambda n: (n not in dead, w.module_model(n)["core_users"],
                                        -w.wear(w.modules[n - 1]["born"])))


def do_upgrade_modules(w, args):
    """UPGRADE MODULES n <MODEL>: trade in the n most worn lesser modules."""
    if len(args) < 3 or args[0].upper() not in ("MODULE", "MODULES"):
        return False, "UPGRADE MODULES n <MODEL>"
    try:
        n = int(args[1])
    except ValueError:
        return False, "HOW MANY MUST BE A WHOLE NUMBER."
    name = args[2].upper()
    cands = upgrade_candidates(w, name)
    if not cands:
        return False, "NO MODULE OF OURS IS A LESSER MODEL THAN %s." % name
    done, paid, last = 0, 0.0, ""
    for m in cands[:max(0, n)]:
        cash = w.money
        ok, last = do_replace(w, ["MODULE", str(m), name])
        if not ok:
            break
        done += 1
        paid += cash - w.money
    if not done:
        return False, last
    return True, ("%d MODULE%s UPGRADED TO %s FOR %.2f AFTER TRADE-INS. CAPACITY %d.%s"
                  % (done, "" if done == 1 else "S", name, paid, w.capacity,
                     "" if done == n else " STOPPED: " + last))


def do_replace(w, args):
    """REPLACE MODULE n [MODEL]: trade a module in for a new one now."""
    w.sync_units()
    if len(args) < 2 or args[0].upper() not in ("MODULE", "CORE"):
        return False, "REPLACE MODULE n <MODEL>   (TYPE MACHINES FOR THE MODELS)"
    try:
        n = int(args[1])
    except ValueError:
        return False, "THE NUMBER MUST BE A WHOLE NUMBER."
    if not 1 <= n <= w.cores:
        return False, "THERE IS NO MODULE %d." % n
    if w.servicing() == n:
        return False, "MODULE %d IS OUT FOR SERVICE." % n
    models = {h["name"]: price for h, price in w.on_sale()}
    name = args[2].upper() if len(args) > 2 else w.hw_name
    if name not in models:
        return False, "%s IS NOT ON SALE. ON SALE: %s." % (name, ", ".join(models))
    old = w.module_model(n)
    down = any(f["kind"] == "module" and f["n"] == n for f in w.faults)
    credit = old["price_core"] * (C.BALANCE["salvage_rate"] if down else C.TRADE_IN_RATE)
    cost = round(models[name] - credit, 2)
    if w.money < cost:
        return False, ("INSUFFICIENT FUNDS: A %s COSTS %.2f LESS %.2f FOR THE OLD ONE, "
                       "CASH %.2f." % (name, models[name], credit, w.money))
    w.money -= cost
    if down:
        _drop_fault(w, "module", n)
    w.modules[n - 1] = {"model": name, "born": w.ticks}
    w.units_dirty = True
    w.journal("REPLACED MODULE %d WITH A %s FOR %.2f" % (n, name, cost))
    return True, ("MODULE %d IS NOW A NEW %s (%d CALLERS). PAID %.2f AFTER %.2f FOR THE "
                  "OLD ONE. CAPACITY %d." % (n, name, w.model(name)["core_users"], cost,
                                             credit, w.capacity))


def capacity_line(w):
    """Say which limit is the binding one: that is what the player must buy."""
    cap = w.capacity
    by_module = w.module_room
    by_line = w.working_lines * C.BALANCE["line_users"]
    if cap == 0:
        why = "NOTHING IN SERVICE"
    elif by_line < by_module:
        why = "LIMITED BY DIAL-IN LINES"
    elif by_module < by_line:
        why = "LIMITED BY PROCESSING MODULES"
    else:
        why = "LINES AND MODULES MATCHED"
    return "  USABLE CAPACITY         %3d   (%s)" % (cap, why)


def idle_lines_line(w):
    spare = w.working_lines - w.module_room
    if spare <= 0:
        return ""
    return ("  IDLE LINES              %3d   PAYING RENT FOR NOTHING. ADD A MODULE."
            % spare)


def goal_progress(w):
    price = w.goal_price
    pct = 100.0 * min(1.0, w.money / price) if price else 0.0
    return price, pct


def r_goal(w):
    price, pct = goal_progress(w)
    out = ["", "  THE GOAL", "  " + "-" * 52]
    if w.won_tick:
        out.append("  %s IS YOURS. BOUGHT ON TICK %d." % (C.BUILDING_NAME, w.won_tick))
        out.append("  THE MACHINE KEEPS RUNNING. NOTHING LEFT TO PROVE.")
        out.append("")
        return "\r\n".join(out) + "\r\n"
    out.append("  THE LANDLORD WILL SELL YOU %s FOR %.0f." % (C.BUILDING_NAME, price))
    out.append("  OWN IT AND THE RENT LINE DISAPPEARS. THE BUSINESS IS YOURS.")
    out.append("")
    out.append("  CASH                    %9.2f   of %.0f  (%.1f%%)" % (w.money, price, pct))
    if w.net > 0:
        need = max(0.0, price - w.money)
        out.append("  AT THE CURRENT NET OF %.3f/TICK, THAT IS %.0f TICKS (%.1f DAYS)"
                   % (w.net, need / w.net, need / w.net / 1440))
    else:
        out.append("  AT THE CURRENT NET OF %.3f/TICK YOU ARE NOT GETTING THERE" % w.net)
    out.append("")
    out.append("  TYPE BUY: THE BUILDING IS ON THE ORDER FORM")
    out.append("")
    return "\r\n".join(out) + "\r\n"


def r_mode(w):
    out = ["", "  OPERATING MODE", "  " + "-" * 52,
           "  %-13s %s" % (
               "WALL CLOCK" if w.mode == "wall" else "SESSION ONLY",
               "the machine runs whether or not you are dialled in"
               if w.mode == "wall" else
               "nothing moves while you are off the line"),
           "  %-13s %d" % ("LIVE OPERATORS", w.live),
           "",
           "  MODE WALL     run on the clock, even with nobody on",
           "  MODE SESSION  run only while someone is dialled in",
           ""]
    return "\r\n".join(out) + "\r\n"


def do_mode(w, args):
    """Returns (ok, message)."""
    if not args:
        return False, "USAGE: MODE WALL   |   MODE SESSION"
    want = args[0].upper()
    if want in ("WALL", "CLOCK", "REAL", "TIME"):
        want = "wall"
    elif want in ("SESSION", "PAUSE", "LIVE", "ONLINE"):
        want = "session"
    else:
        return False, "MODE IS EITHER WALL OR SESSION."
    if want == w.mode:
        return True, "ALREADY RUNNING IN %s MODE." % want.upper()
    w.mode = want
    w.last_tick = int(time.time())      # the switch never banks or owes ticks
    w.journal("MODE SET TO %s" % want.upper())
    if want == "session":
        return True, ("THE MACHINE NOW STOPS WHEN EVERYONE HANGS UP. "
                      "NO TICKS PASS WHILE THE LINES ARE QUIET.")
    return True, ("THE MACHINE NOW RUNS ON THE CLOCK. IT KEEPS GOING "
                  "WHILE NOBODY IS DIALLED IN.")


def do_tick_rate(w, args):
    """Returns (ok, message)."""
    if not args:
        return False, "USAGE: TICK n   (seconds per tick, 1 to 3600)"
    try:
        n = int(args[0])
    except ValueError:
        return False, "SECONDS MUST BE A WHOLE NUMBER."
    if not 1 <= n <= 3600:
        return False, "THE CLOCK RUNS BETWEEN 1 AND 3600 SECONDS A TICK."
    w.tick_seconds = n
    w.last_tick = int(time.time())      # the change never banks a gap
    w.journal("TICK RATE SET TO %d SECONDS" % n)
    return True, ("THE CLOCK NOW TURNS EVERY %d SECOND%s."
                  % (n, "" if n == 1 else "S"))


def r_upgrades(w):
    out = ["", "  UPGRADES", "  " + "-" * 52]
    for name, data in C.UPGRADES.items():
        if name in w.owned:
            out.append("  %-18s OWNED      %s" % (name, data["desc"]))
        else:
            out.append("  %-18s %8.0f   %s" % (name, data["price"], data["desc"]))
    out.append("")
    out.append("  TYPE BUY TO ORDER ONE")
    out.append("")
    return "\r\n".join(out) + "\r\n"


def r_sites(w):
    out = ["", "  SITES", "  " + "-" * 52]
    for i, (name, _p, ceiling, base_cost) in enumerate(C.LOCATIONS):
        price = w.site_price(i)
        mods, lines = C.SITE_ROOM[min(i, len(C.SITE_ROOM) - 1)]
        out.append("  %d  %-28s market %3d   rent %.2f/tick   %s"
                   % (i, name, ceiling, base_cost * (C.BALANCE["prestige_rent_growth"] ** w.prestiges),
                      "HERE" if i == w.site else ("%.0f TO MOVE" % price if i > w.site else "outgrown")))
        out.append("     room for %d modules and %d lines" % (mods, lines))
    out.append("")
    out.append("  MOVE <n>   e.g. MOVE 1")
    out.append("")
    return "\r\n".join(out) + "\r\n"


def r_mods(w):
    out = ["", "  IN FORCE", "  " + "-" * 52]
    if not w.mods:
        out.append("  NOTHING BUT ORDINARY WEATHER.")
    for m in w.mods:
        eff = ", ".join("%s %+.2f" % (k, v) for k, v in m["effects"].items())
        out.append("  %-24s %4d LEFT   %s" % (m["name"], m["ticks_left"], eff))
    out.append("")
    return "\r\n".join(out) + "\r\n"


def staff_line(w):
    if not w.staff:
        return ""
    return "  STAFF                   %s   (%.2f/tick in salaries)" % (
        ", ".join("%s %s" % (r, i["name"]) for r, i in w.staff.items()), w.salaries)


def mods_line(w):
    if not w.mods:
        return ""
    return "  IN FORCE                %s" % ", ".join(
        "%s (%d)" % (m["name"], m["ticks_left"]) for m in w.mods[:3])


def site_line(w):
    return "  SITE                    %s   (market %d callers, rent %.2f/tick)" % (
        w.site_name, w.ceiling, w.site_rent)


def r_offers(w):
    out = ["", "  WORK OFFERED", "  " + "-" * 52]
    pend = w.offer_slots()
    if not pend:
        out.append("  NOTHING ON THE TELEPRINTER.")
    for c in pend:
        state = "LIVE" if c["state"] == "active" else "OFFER"
        out.append("  %2d  %-22s %-18s %s" % (c["id"], c["company"], c["work"], state))
        hold = "%d callers" % c["want"]
        if not c.get("lesson"):
            out.append("      needs %-12s for %d ticks   fee %.0f   penalty %.0f"
                       % (hold, c["duration"], c["fee"], c["penalty"]))
        elif c["duration"]:                    # a grant: no penalty to print
            out.append("      needs %-12s for %d ticks" % (hold, c["duration"]))
        if state == "LIVE":
            out.append("      %d ticks left, short on %d of them"
                       % (c["ticks_left"], c["breached"]))
        a = w.accounts.get(c["company"])
        if not c.get("lesson") and a and (a["met"] or a["short"] or a["goodwill"]):
            out.append("      GOODWILL %+d: SERVED %d, SHORTED %d BEFORE"
                       % (a["goodwill"], a["met"], a["short"]))
        if c.get("lesson"):
            out.append("      " + C.LESSON_GRANT.format(fee=c["fee"]))
            out.append("      " + lesson_hint(w, c))
    live = w.active()
    out.append("")
    out.append("  LIVE JOBS %d OF %d        ACCEPT n   take the work      DROP n   turn it down"
               % (len(live), C.CONTRACT_MAX_ACTIVE))
    if live:
        out.append("  CLOSE n    call a job in early: paid for the term served, plus the bonus")
    out.append("")
    return "\r\n".join(out) + "\r\n"


# every effect key, how to say it, and whether more of it is good for us
EFFECT_LABELS = {
    "capacity_mult": ("CAPACITY", True),
    "slots_add": ("BATCH SLOTS", True),
    "demand_mult": ("DEMAND", True),
    "demand_flat": ("CALLERS", True),
    "growth_add": ("MARKET GROWTH", True),
    "rate_mult": ("PRICE PER CALLER", True),
    "queue_rate": ("QUEUED WORK PAYS", True),
    "fault_mult": ("BREAKDOWNS", False),
    "repair_mult": ("REPAIR COSTS", False),
    "cost_mult": ("RUNNING COSTS", False),
}


def effect_rows(effects, indent="      "):
    """One line per effect: + if it helps us, - if it hurts, then by how much."""
    out = []
    for key, (label, more_is_good) in EFFECT_LABELS.items():
        if key not in effects:
            continue
        v = effects[key]
        if key.endswith("_mult"):
            if abs(v - 1.0) < 1e-9:
                continue
            up, amount = v > 1.0, "%+.0f%%" % ((v - 1.0) * 100)
        elif key == "growth_add":
            up, amount = v > 0, "%+.3f/TICK" % v
        elif key == "queue_rate":
            up, amount = v > 0, "%+.0f POINTS" % (v * 100)
        else:
            up, amount = v > 0, "%+d" % v
        if not v:
            continue
        out.append("%s%s  %-18s %s" % (indent, "+" if up == more_is_good else "-",
                                       label, amount))
    return out


def effect_brief(effects):
    """The same as effect_rows, on one line: '+3 BATCH SLOTS, -15% BREAKDOWNS'."""
    parts = []
    for row in effect_rows(effects, indent=""):
        label, amount = [x.strip() for x in row[3:].split("  ") if x.strip()][:2]
        # "+3% CAPACITY" reads well; "QUEUED WORK PAYS +8 POINTS" reads the other way
        parts.append("%s %s" % ((label, amount) if " " in amount else (amount, label)))
    return ", ".join(parts)


def r_effects(w):
    """Everything pushing the business up or down, by where it comes from."""
    rule = "  " + "-" * (REPORT_WIDTH - 2)
    out = ["", "  WHAT IS PUSHING THE BUSINESS", rule,
           "  + HELPS US   - HURTS US", ""]

    def section(title, entries):
        rows = []
        for name, effects, extra in entries:
            body = effect_rows(effects) + ["      -  " + x for x in extra]
            if body:
                rows += ["    " + name] + body
        if rows:
            out.append("  " + title)
            out.extend(rows)
            out.append("")

    section("YOUR PRICE", [("%d%% OF THE LIST RATE" % w.price, w.price_effects(), [])])
    if w.rival:
        section("THE RIVAL", [("%s (%d%% OF OUR LIST)" % (w.rival["name"][:26], w.rival["price"]),
                               {"demand_mult": 1.0 - w.rival_take()}, [])])
    section("STAFF", [("%s %s, LEVEL %d%s" % (r, i["name"], i.get("level", 1),
                                             " (AWAY)" if i.get("away_until", 0) > w.ticks else ""),
                       w.staff_effects(r),
                       ["%-18s %.2f/TICK" % ("SALARY", C.ROLES[r]["salary"] * i.get("pay", 1.0))])
                      for r, i in sorted(w.staff.items())])
    section("EQUIPMENT", [(n, C.UPGRADES[n]["effects"], []) for n in sorted(w.owned)])
    section("ENGINEERING", [(n, C.WORKS[n]["effects"], []) for n in sorted(w.works)])
    if w.loan:
        section("THE BANK", [("%.2f OWED" % w.loan["owed"], {},
                              ["%-18s %.3f/TICK" % ("INTEREST", w.interest)])])
    if w.project:
        section("WORK UNDER WAY", [("%s (%s LEFT)" % (w.project["name"], short(w.project["left"])),
                                    w.project["while"], [])])
    if w.incident:
        inc = w.incident_def()
        section("WAITING ON YOU", [(inc["title"][:40], inc["while"], [])])
    section("IN FORCE", [("%s (%s LEFT)" % (m["name"], short(m["ticks_left"])), m["effects"], [])
                         for m in w.mods])
    if w.standing:
        worth = w.standing ** 0.6
        section("STANDING", [("%d POINTS FROM PAST SALES" % w.standing,
                              {"rate_mult": 1.0 + 0.06 * worth,
                               "demand_mult": 1.0 + 0.05 * worth}, [])])
    if w.bodges:
        section("BODGES", [("%d REPAIR%s HELD WITH TAPE" % (w.bodges, "" if w.bodges == 1 else "S"),
                            {"fault_mult": 1.0 + 0.05 * w.bodges}, [])])
    if len(out) == 5:
        out += ["  NOTHING: THE BUSINESS RUNS ON ITS OWN NUMBERS.", ""]

    total = w.effects()
    total["queue_rate"] -= C.BALANCE["queue_rate"]      # show the change, not the base
    total["demand_mult"] *= w.price_pull() * (1.0 - w.rival_take())
    rows = effect_rows(total, indent="    ")
    out.append(rule)
    out.append("  ALL TOGETHER")
    out += rows or ["    NO NET EFFECT."]
    if w.capacity != w.capacity_raw:
        out.append("    CAPACITY %d -> %d CALLERS" % (w.capacity_raw, w.capacity))
    out.append("")
    return "\r\n".join(out) + "\r\n"


def r_incident(w):
    if not w.incident:
        return "\r\n  NOTHING IS WAITING FOR A DECISION.\r\n\r\n"
    inc = w.incident_def()
    out = [""] + fold("  ", "DECISION: " + inc["title"] + ".")
    out.append("  " + "-" * (REPORT_WIDTH - 2))
    out += fold("  ", inc["text"])
    out.append("")
    for i, (word, desc, _o) in enumerate(inc["choices"], 1):
        out += fold("  %d  %-10s " % (i, word), desc)
    out.append("")
    out += fold("  ", "DECIDE <WORD>. LEFT UNANSWERED, IT IS %s IN %s." % (
        inc["default"], both_times(w, w.incident["left"])))
    out.append("")
    return "\r\n".join(out) + "\r\n"


def r_project(w):
    if not w.project:
        return "\r\n  THE ENGINEERS ARE FREE. NOTHING IS UNDER WAY.\r\n\r\n"
    p = w.project
    done = p["total"] - p["left"]
    bar = int(30 * done / max(1, p["total"]))
    out = ["", "  " + p["name"], "  " + "-" * (REPORT_WIDTH - 2),
           "  [%s%s]  %d OF %d" % ("#" * bar, "." * (30 - bar), done, p["total"])]
    out += fold("  ", both_times(w, p["left"]) + ".")
    out.append("")
    if p["while"].get("capacity_mult") is not None:
        out += fold("  ", "MEANWHILE THE MACHINE RUNS AT %d%% OF CAPACITY."
                    % int(100 * p["while"]["capacity_mult"]))
        out.append("")
    return "\r\n".join(out) + "\r\n"


def lesson_hint(w, c):
    lesson = c.get("lesson") or ""
    L = next((x for x in C.LESSONS if x["teach"] == lesson.split(":")[0]), None)
    if L is None:
        return ""
    return L["hint"].format(id=c["id"], want=c["want"], capacity=w.capacity,
                            line=lesson.split(":")[1] if ":" in lesson else "",
                            fee=c["fee"])


def contract_line(w):
    live = w.active()
    if not live:
        return ""
    c = live[0]
    return ("  CONTRACT                %s: %d CALLERS, %d TICKS LEFT, SHORT %d"
            % (c["company"][:18], c["want"], c["ticks_left"], c["breached"]))


def overdraft_line(w):
    if not w.overdraft_ticks:
        return ""
    b = C.BALANCE
    return ("  OVERDRAWN               %d/%d TICKS UNTIL THE EXCHANGE DISCONNECTS US"
            % (w.overdraft_ticks, b["overdraft_grace"]))


def repair_cost(kind, w=None):
    base = C.BALANCE["price_repair_line" if kind == "line" else "price_repair_module"]
    if w is None:
        return base
    return base * w.effects()["repair_mult"]


def faults_block(w):
    if not w.faults:
        return "  NOTHING IS DOWN."
    lines = []
    for f in w.faults:
        lines.append("  DOWN: %-6s %-3d  %4d TICKS   REPAIR %s %d  (%.2f)"
                     % (f["kind"].upper(), f["n"], f["age"], f["kind"].upper(),
                        f["n"], repair_cost(f["kind"])))
    return "\n".join(lines).replace("\n", "\r\n")


def r_faults(w):
    out = ["", "  FAULT SHEET", "  " + "-" * 52]
    if not w.faults:
        out.append("  NOTHING IS DOWN. THE MACHINE IS HEALTHY.")
    else:
        total = sum(repair_cost(f["kind"], w) for f in w.faults)
        for f in w.faults:
            out.append("  %-6s %-3d  DOWN %4d TICKS   REPAIR COST %.2f"
                       % (f["kind"].upper(), f["n"], f["age"], repair_cost(f["kind"])))
        out.append("  " + "-" * 52)
        out.append("  REPAIR            fix everything for %.2f" % total)
        out.append("  REPAIR LINE n     fix one line")
        out.append("  REPAIR MODULE n   fix one processing module")
    out.append("")
    return "\r\n".join(out) + "\r\n"


def r_users(w):
    """Everyone who dialled in, and - separately - everyone being refused.

    The old version cut the table at eight rows and printed "... N more", which
    hid precisely the callers with a busy signal: the ones costing reputation.
    """
    lines = ["", "  CALLERS ON SYSTEM      %d dialled in, %d served, %d refused"
             % (w.subs, w.served, max(0, w.subs - w.served)),
             "  " + "-" * 52]
    if w.subs == 0:
        lines.append("  NOBODY IS DIALED IN.")
        lines.append("")
        return "\r\n".join(lines) + "\r\n"

    shown = min(w.served, 10)
    for i in range(shown):
        lines.append("  LINE %02d  %-14s SERVED   %s" %
                     (i + 1, "ACCOUNT %04d" % (11 + i * 7),
                      C.ACCOUNT_SUFFIX[i % len(C.ACCOUNT_SUFFIX)]))
    if w.served > shown:
        lines.append("  ... %d more served" % (w.served - shown))

    refused = w.subs - w.served
    if refused > 0:
        lines.append("  " + "-" * 52)
        lines.append("  REFUSED A LINE: %d caller%s" % (refused, "" if refused == 1 else "s"))
        by_module = w.module_room
        by_line = w.working_lines * C.BALANCE["line_users"]
        if by_line <= by_module:
            reason = "every dial-in line is carrying a caller  ->  more lines"
        else:
            reason = "the modules cannot process another caller  ->  more modules"
        lines.append("  REASON: %s" % reason)
        lines.append("  THEY HEAR A BUSY SIGNAL AND PAY NOTHING. REPUTATION FALLS.")
        lines.append("  FIX: TYPE BUY. A LINE CARRIES ONE CALLER, A MODULE SEVERAL.")
    lines.append("")
    return "\r\n".join(lines) + "\r\n"


def r_jobs(w, rng):
    lines = ["", "  BATCH QUEUE          %d SLOT%s" % (w.slots, "" if w.slots == 1 else "S"),
             "  " + "-" * 52]
    shown_run = min(w.running, 12)
    for i in range(shown_run):
        lines.append("  %-12s %4d SEC   RUNNING" %
                     (rng.choice(C.JOB_NAMES), rng.randint(3, 340)))
    if w.queued:
        lines.append("  " + "-" * 52)
        lines.append("  %d JOB%s WAITING FOR A SLOT. THEY PAY %.0f%% WHILE QUEUED."
                     % (w.queued, "" if w.queued == 1 else "S",
                        100 * C.BALANCE["queue_rate"]))
        lines.append("  MORE SLOTS ARE %.2f EACH: TYPE BUY" % C.BALANCE["price_slot"])
    if w.served == 0:
        lines.append("  NOTHING IS RUNNING. NOBODY IS DIALED IN.")
    elif not w.queued:
        lines.append("  " + "-" * 52)
        lines.append("  NO QUEUE. SLOTS ARE KEEPING UP.")
    lines.append("")
    return "\r\n".join(lines) + "\r\n"


def r_news(w):
    if not w.queue:
        return "\r\n  NO CONSOLE MESSAGES.\r\n\r\n"
    out = ["", "  CONSOLE MESSAGES", "  " + "-" * 52]
    out += ["  " + m for m in w.queue[-12:]]
    out.append("")
    return "\r\n".join(out) + "\r\n"


def r_log(w):
    rows = []
    if w.db is not None:
        rows = w.db.execute("SELECT tick, line FROM log WHERE kind IS NULL OR kind != 'statement' "
                            "ORDER BY id DESC LIMIT 10").fetchall()
    out = ["", "  SYSTEM JOURNAL (LAST 10)", "  " + "-" * 52]
    out += ["  %06d  %s" % (t, l) for t, l in rows] or ["  NOTHING JOURNALISED YET."]
    out.append("")
    return "\r\n".join(out) + "\r\n"


def do_buy(w, args):
    """Returns (ok, message). BUY CORES n buys the cycle's standard module;
    BUY <MODEL> n (or BUY CORES n <MODEL>) buys any model on sale."""
    models = {h["name"]: price for h, price in w.on_sale()}
    every = {h["name"] for h in C.HARDWARE}
    if not args:
        return False, "USAGE: BUY LINES n   |   BUY CORES n   |   BUY <MODEL> n"
    what = args[0].upper()
    model = None
    if what in every:
        model, what = what, "CORES"
        args = ["CORES", args[1] if len(args) > 1 else "1"]
    elif len(args) > 2 and args[2].upper() in every:
        model = args[2].upper()
    if len(args) < 2:
        return False, "USAGE: BUY LINES n   |   BUY CORES n   |   BUY <MODEL> n"
    try:
        n = int(args[1])
    except ValueError:
        return False, "QUANTITY MUST BE A WHOLE NUMBER."
    if n <= 0 or n > 100:
        return False, "QUANTITY OUT OF RANGE."
    if model and model not in models:
        return False, ("NOBODY WILL SELL US A %s MODULE THIS CYCLE. ON SALE: %s. TYPE MACHINES."
                       % (model, ", ".join(models)))
    if what in ("LINE", "LINES"):
        price, key = C.BALANCE["price_line"], "lines"
    elif what in ("CORE", "CORES", "CPU", "MODULE", "MODULES"):
        model = model or w.hw_name
        price, key = models.get(model, w.hardware["price_core"]), "cores"
        what = "%s MODULE%s" % (model, "" if n == 1 else "S")
    elif what in ("SLOT", "SLOTS", "CHANNEL", "CHANNELS"):
        price, key = C.BALANCE["price_slot"], "slots"
    else:
        return False, "WE DO NOT SELL %s." % what
    total = price * n
    if key in ("cores", "lines"):
        room = w.room[0 if key == "cores" else 1]
        have = getattr(w, key)
        if have + n > room:
            return False, ("%s HAS ROOM FOR %d %s AND WE HAVE %d. A BIGGER SITE HAS MORE: "
                           "TYPE SITES." % (w.site_name, room,
                                            "MODULES" if key == "cores" else "LINES", have))
    if w.money < total:
        return False, "INSUFFICIENT FUNDS: %s REQUIRES %.2f, CASH IS %.2f." % (what, total, w.money)
    w.money -= total
    if key == "slots":
        w.buy_slot(n)
    else:
        w.sync_units()
        setattr(w, key, getattr(w, key) + n)
        if key == "cores":
            w.modules += [{"model": model, "born": w.ticks} for _ in range(n)]
        else:
            w.line_born += [w.ticks] * n
        w.units_dirty = True
    w.journal("PURCHASE: %d %s FOR %.2f" % (n, what, total))
    msg = ("%d %s INSTALLED. CASH %.2f. CAPACITY NOW %d, SLOTS %d, QUEUE %d."
           % (n, what, w.money, w.capacity, w.slots, w.queued))
    if key == "lines" and w.lines > w.module_room:
        msg += ("\r\n       NOTE: your %d module(s) can only serve %d callers, so "
                "you now have %d line(s) that cannot carry anyone.\r\n"
                "       A MODULE would put them to work."
                % (w.working_cores, w.module_room,
                   w.lines - w.module_room))
    if key == "cores" and w.module_room > w.working_lines:
        msg += ("\r\n       NOTE: modules can serve %d but you have %d lines. "
                "More lines would fill them."
                % (w.module_room, w.working_lines))
    return True, msg


def do_hire(w, args):
    if not args:
        return False, "HIRE WHICH ROLE? " + ", ".join(C.ROLES)
    role = args[0].upper()
    if role not in C.ROLES:
        return False, "WE DO NOT EMPLOY A %s. TRY: %s" % (role, ", ".join(C.ROLES))
    if role in w.staff:
        return False, "WE ALREADY HAVE A %s (%s)." % (role, w.staff[role]["name"])
    info = C.ROLES[role]
    if w.money < info["fee"]:
        return False, ("INSUFFICIENT FUNDS: HIRING A %s COSTS %.2f, CASH %.2f."
                       % (role, info["fee"], w.money))
    w.money -= info["fee"]
    name = C.ROLE_NAMES[(len(w.staff) * 5 + w.ticks // 97) % len(C.ROLE_NAMES)]
    w.staff[role] = new_staff(name)
    if w.db is not None:
        w.db.execute("INSERT OR REPLACE INTO staff (role, name, level, data) VALUES (?,?,1,?)",
                     (role, name, json.dumps(w.staff[role])))
    w.journal("HIRED %s %s FOR %.2f" % (role, name, info["fee"]))
    return True, ("%s %s STARTS MONDAY. SALARY %.2f/TICK. %s"
                  % (role, name, info["salary"], info["desc"]))


def do_fire(w, args):
    if not args:
        return False, "FIRE WHO? " + ", ".join(w.staff or ["NOBODY"])
    role = args[0].upper()
    if role not in w.staff:
        return False, "NOBODY CALLED %s WORKS HERE." % role
    who = w.staff.pop(role)
    if w.db is not None:
        w.db.execute("DELETE FROM staff WHERE role=?", (role,))
    return True, "%s %s HAS BEEN LET GO. THE SALARY STOPS." % (role, who["name"])


def do_buy_upgrade(w, name):
    key = name.upper()
    if key in w.owned:
        return False, "WE ALREADY HAVE %s." % key
    if w.project and (w.project.get("then") or {}).get("upgrade") == key:
        return False, "%s IS BEING FITTED NOW." % key
    info = C.UPGRADES.get(key)
    if info is None:
        return False, ("WE DO NOT SELL %s. TYPE UPGRADES FOR THE CATALOGUE." % name.upper())
    if w.project:
        return False, ("THE ENGINEERS ARE BUSY WITH %s (%d TICKS LEFT). ONE JOB AT A TIME."
                       % (w.project["name"], w.project["left"]))
    if w.money < info["price"]:
        return False, ("INSUFFICIENT FUNDS: %s COSTS %.2f, CASH %.2f."
                       % (key, info["price"], w.money))
    w.money -= info["price"]
    w.journal("BOUGHT UPGRADE %s FOR %.2f" % (key, info["price"]))
    w.start_project("FITTING " + key, C.INSTALL_TICKS, C.INSTALL_WHILE, {"upgrade": key})
    return True, ("%s ORDERED. THE ENGINEERS NEED %d TICKS TO FIT IT, WITH A PANEL "
                  "OFF MEANWHILE. %s" % (key, C.INSTALL_TICKS, info["desc"]))


def do_move(w, args):
    if not args:
        return False, "MOVE WHERE? TYPE SITES."
    try:
        idx = int(args[0])
    except ValueError:
        return False, "THE SITE NUMBER MUST BE A WHOLE NUMBER."
    if not 0 <= idx < len(C.LOCATIONS):
        return False, "NO SUCH SITE. TYPE SITES FOR THE LIST."
    if idx == w.site:
        return False, "WE ARE ALREADY AT %s." % C.LOCATIONS[idx][0]
    if idx < w.site:
        return False, "WE CANNOT GO BACKWARDS TO A SMALLER ROOM."
    name, _base_price, ceiling, base_cost = C.LOCATIONS[idx]
    if w.project:
        if (w.project.get("then") or {}).get("site") is not None:
            return False, "WE ARE ALREADY MOVING: %d TICKS LEFT." % w.project["left"]
        return False, ("THE ENGINEERS ARE BUSY WITH %s (%d TICKS LEFT). MOVE WHEN THEY "
                       "ARE DONE." % (w.project["name"], w.project["left"]))
    price = w.site_price(idx)
    if w.money < price:
        return False, ("INSUFFICIENT FUNDS: MOVING TO %s COSTS %.2f, CASH %.2f."
                       % (name, price, w.money))
    w.money -= price
    w.journal("PAID %.2f TO MOVE TO %s" % (price, name))
    w.start_project("MOVE TO " + name, C.MOVE_TICKS, C.MOVE_WHILE, {"site": idx})
    msg = ("PAID %.2f. THE MACHINE GOES IN THE VAN NOW AND IS OFF FOR %d TICKS (%s). "
           "AT %s THE MARKET IS %d CALLERS WIDE, RENT %.2f/TICK."
           % (price, C.MOVE_TICKS, span(C.MOVE_TICKS), name, ceiling, base_cost))
    if w.active():
        msg += " LIVE JOBS WILL GO SHORT WHILE WE ARE OFF."
    return True, msg


def do_prestige(w):
    if not w.won_tick:
        return False, "SELL UP? WE DO NOT OWN THE BUILDING YET. TYPE GOAL."
    points = int((w.earned / C.BALANCE["prestige_points_per"]) ** 0.5)
    if points < 1:
        return False, "THE BUSINESS IS NOT WORTH SELLING YET."
    w.standing += points
    w.prestiges += 1
    w.put("standing", w.standing)
    w.put("prestiges", w.prestiges)
    next_price = C.BALANCE["building_price"] * (C.BALANCE["prestige_price_growth"] ** w.prestiges)
    saved_prestiges = w.prestiges
    w.reset_world()
    w.prestiges = saved_prestiges
    w.put("prestiges", w.prestiges)
    worth = w.standing ** 0.6
    return True, ("SOLD. %d STANDING EARNED (NOW %d): +%.0f%% REVENUE, +%.0f%% DEMAND. "
                  "THE DEEDS NEXT TIME COST %.0f, THE RENT RISES, AND THE MACHINE BREAKS "
                  "DOWN MORE OFTEN."
                  % (points, w.standing, 6 * worth, 5 * worth, next_price))


def do_buy_building(w):
    price = w.goal_price
    if w.won_tick:
        return False, "%s IS ALREADY YOURS." % C.BUILDING_NAME
    if w.money < price:
        return False, ("NOT YET: %.0f NEEDED, CASH %.2f. TYPE GOAL FOR THE DISTANCE."
                       % (price, w.money))
    w.money -= price
    w.own_building()
    for line in C.ENDING:
        w.queue.append(line.strip())
    w.journal("BOUGHT %s FOR %.2f ON TICK %d" % (C.BUILDING_NAME, price, w.ticks))
    return True, "%s IS YOURS. CASH %.2f. THE RENT LINE IS GONE." % (C.BUILDING_NAME, w.money)


def do_accept(w, args):
    if not args:
        return False, "ACCEPT WHICH OFFER? TYPE OFFERS FOR THE NUMBERS."
    try:
        cid = int(args[0])
    except ValueError:
        return False, "THE OFFER NUMBER MUST BE A WHOLE NUMBER."
    match = [c for c in w.contracts if c["id"] == cid and c["state"] == "offered"]
    if not match:
        return False, "NO OFFER NUMBER %d. TYPE OFFERS." % cid
    c = match[0]
    lesson = c.get("lesson") or ""
    if lesson.startswith("repair:"):
        return False, ("NOTHING TO SIGN: REPAIR LINE %s (OR BODGE IT) AND THEY PAY %.0f."
                       % (lesson.split(":")[1], c["fee"]))
    if w.capacity < c["want"]:
        return False, ("WE CAN ONLY SERVE %d CALLERS AND THEY WANT %d. "
                       "TYPE BUY FOR LINES AND MODULES FIRST, OR TAKE THE RISK LATER."
                       % (w.capacity, c["want"]))
    if len(w.active()) >= C.CONTRACT_MAX_ACTIVE:
        return False, ("WE CANNOT TAKE MORE THAN %d JOBS AT ONCE. CLOSE ONE FIRST: %s"
                       % (C.CONTRACT_MAX_ACTIVE,
                          ", ".join(c["company"] for c in w.active())))
    c["state"] = "active"
    c["ticks_left"] = c["duration"]
    c["breached"] = 0
    w.save_contract(c)
    return True, ("%s ACCEPTED: %d CALLERS FOR %d TICKS. FEE %.0f, PENALTY %.0f."
                  % (c["company"], c["want"], c["duration"], c["fee"], c["penalty"]))


def do_close(w, args):
    """Call a live job in early: paid for the term served, plus a completion bonus."""
    if not args:
        return False, "CLOSE WHICH JOB? TYPE OFFERS FOR THE NUMBERS."
    try:
        cid = int(args[0])
    except ValueError:
        return False, "THE JOB NUMBER MUST BE A WHOLE NUMBER."
    match = [c for c in w.contracts if c["id"] == cid and c["state"] == "active"]
    if not match:
        return False, "NO LIVE JOB NUMBER %d. TYPE OFFERS." % cid
    c = match[0]
    if c.get("lesson"):
        return True, "CALLED IN. " + w.pay_lesson(c)
    held = c["duration"] - c["ticks_left"]
    allowed = c["duration"] * C.BALANCE["contract_breach_tolerance"]
    if c["breached"] > allowed:
        w.run["short"] += 1
        w.goodwill_change(c["company"], -1, "short")
        w.money -= c["penalty"]
        w.rep = max(0.0, w.rep - 1.0)
        w.drop_contract(cid)
        w.journal("CONTRACT CLOSED SHORT: %s PENALTY %.2f" % (c["company"], c["penalty"]))
        return True, ("CLOSED SHORT: %s WAS LATE ON %d TICKS. PENALTY %.0f CHARGED."
                      % (c["company"], c["breached"], c["penalty"]))
    share = min(1.0, held / float(c["duration"]))
    w.run["met"] += 1
    w.goodwill_change(c["company"], +1, "met")
    payout = c["fee"] * share * C.CONTRACT_EARLY_BONUS
    w.money += payout
    w.rep = min(20.0, w.rep + 0.25)
    w.drop_contract(cid)
    w.journal("CONTRACT CLOSED EARLY: %s PAID %.2f" % (c["company"], payout))
    return True, ("%s CALLED IN AFTER %d OF %d TICKS. FEE %.0f (%.0f%% OF THE TERM, "
                  "PLUS THE COMPLETION BONUS). CASH %.2f."
                  % (c["company"], held, c["duration"], payout, 100 * share, w.money))


def do_drop(w, args):
    if not args:
        return False, "DROP WHICH OFFER? TYPE OFFERS FOR THE NUMBERS."
    try:
        cid = int(args[0])
    except ValueError:
        return False, "THE OFFER NUMBER MUST BE A WHOLE NUMBER."
    match = [c for c in w.contracts if c["id"] == cid]
    if not match:
        return False, "NO OFFER NUMBER %d." % cid
    c = match[0]
    if c.get("lesson"):
        return True, "%s TURNED DOWN. THEY PAY THE GRANT ANYWAY. %s" % (
            c["company"], w.pay_lesson(c))
    w.drop_contract(cid)
    if c["state"] == "active":
        w.goodwill_change(c["company"], -1, "short")
        return True, "%s DROPPED. THAT WILL BE REMEMBERED." % c["company"]
    return True, "%s TURNED DOWN. NO HARM DONE." % c["company"]


def do_sell(w, args):
    """Dispose of capacity you cannot fill. Overbuilding was a trap with no exit."""
    if len(args) < 2:
        return False, "USAGE: SELL LINES n   |   SELL SLOTS n"
    what = args[0].upper()
    try:
        n = int(args[1])
    except ValueError:
        return False, "QUANTITY MUST BE A WHOLE NUMBER."
    if n <= 0:
        return False, "QUANTITY OUT OF RANGE."
    rate = C.BALANCE["liquidation_rate"]
    if what in ("LINE", "LINES"):
        # only working lines are sold: a dead one is worth parts, which is SCRAP
        dead = {f["n"] for f in w.faults_of("line")}
        working = [i for i in range(1, w.lines + 1) if i not in dead]
        if n >= len(working):
            return False, ("WE HAVE %d WORKING LINE%s AND NEED AT LEAST ONE. "
                           "DEAD LINES ARE SCRAPPED, NOT SOLD: SCRAP LINE n."
                           % (len(working), "" if len(working) == 1 else "S"))
        w.sync_units()
        for gone in sorted(working[-n:], reverse=True):
            del w.line_born[gone - 1]
        w.units_dirty = True
        w.lines -= n
        renumber_lines(w, working[-n:])
        proceeds = n * C.BALANCE["price_line"] * rate
    elif what in ("SLOT", "SLOTS"):
        spare = w.slots - C.BALANCE["start_slots"]
        if n > spare:
            return False, "WE ONLY HAVE %d SLOT(S) BEYOND THE BASIC SET." % max(0, spare)
        w.extra_slots -= n
        w.put("slots", w.extra_slots)
        proceeds = n * C.BALANCE["price_slot"] * rate
    else:
        return False, "WE CANNOT SELL %s. TRY LINES OR SLOTS." % what
    w.money += proceeds
    w.journal("SOLD %d %s FOR %.2f" % (n, what, proceeds))
    return True, ("%d %s DISPOSED OF FOR %.2f. CASH %.2f. CAPACITY %d, SLOTS %d."
                  % (n, what, proceeds, w.money, w.capacity, w.slots))


def renumber(w, kind, gone):
    """Lines and modules run 1..n with no gaps: close up the numbers that were
    removed, so each fault keeps pointing at the same physical unit."""
    for f in w.faults_of(kind):
        f["n"] -= sum(1 for g in gone if g < f["n"])
    if w.db is not None:
        w.db.execute("DELETE FROM fault WHERE kind=?", (kind,))
        for f in w.faults_of(kind):
            w.db.execute("INSERT INTO fault (kind, n) VALUES (?, ?)", (kind, f["n"]))


def renumber_lines(w, gone):
    renumber(w, "line", gone)


def _unit_args(w, args, verb):
    """Shared parsing for commands that name one line or one module."""
    if len(args) < 2:
        return None, None, "USAGE: %s LINE n   |   %s MODULE n" % (verb, verb)
    what = args[0].upper()
    if what in ("LINE", "LINES"):
        kind = "line"
    elif what in ("MODULE", "MODULES", "CORE", "CORES"):
        kind = "module"
    else:
        return None, None, "%s WHAT? LINE OR MODULE." % verb
    try:
        n = int(args[1])
    except ValueError:
        return None, None, "THE NUMBER MUST BE A WHOLE NUMBER."
    match = [f for f in w.faults if f["kind"] == kind and f["n"] == n]
    if not match:
        return None, None, ("%s %d IS NOT DOWN. TYPE FAULTS TO SEE WHAT IS."
                            % (what, n))
    return kind, n, None


def _drop_fault(w, kind, n):
    w.faults = [f for f in w.faults if not (f["kind"] == kind and f["n"] == n)]
    if w.db is not None:
        w.db.execute("DELETE FROM fault WHERE kind=? AND n=?", (kind, n))


def do_scrap(w, args):
    """Sell a dead unit for parts. Returns (ok, message)."""
    kind, n, err = _unit_args(w, args, "SCRAP")
    if err:
        return False, err
    b = C.BALANCE
    price = b["price_line"] if kind == "line" else w.module_model(n)["price_core"]
    proceeds = round(price * b["salvage_rate"], 2)
    w.money += proceeds
    _drop_fault(w, kind, n)
    w.sync_units()
    if kind == "line":
        del w.line_born[n - 1]
        w.lines = max(0, w.lines - 1)
    else:
        del w.modules[n - 1]
        w.cores = max(0, w.cores - 1)
    w.units_dirty = True
    renumber(w, kind, [n])
    w.journal("SCRAPPED %s %d FOR %.2f" % (kind.upper(), n, proceeds))
    return True, ("%s %d STRIPPED AND SOLD FOR %.2f. CASH %.2f. CAPACITY NOW %d."
                  % (kind.upper(), n, proceeds, w.money, w.capacity))


def do_bodge(w, args):
    """Bring a dead unit back cheaply. Returns (ok, message)."""
    kind, n, err = _unit_args(w, args, "BODGE")
    if err:
        return False, err
    b = C.BALANCE
    cost = round(repair_cost(kind, w) * b["bodge_rate"], 2)
    if w.money < cost:
        return False, ("INSUFFICIENT FUNDS: A BODGE COSTS %.2f, CASH %.2f. "
                       "SCRAP SOMETHING OR WAIT." % (cost, w.money))
    w.money -= cost
    _drop_fault(w, kind, n)
    w.bodges += 1
    if w.db is not None:
        w.db.execute("INSERT INTO upgrade (name, n) VALUES ('bodges', ?) "
                     "ON CONFLICT(name) DO UPDATE SET n=excluded.n", (w.bodges,))
    w.journal("BODGED %s %d FOR %.2f" % (kind.upper(), n, cost))
    return True, ("%s %d IS BACK, HELD TOGETHER WITH TAPE. COST %.2f. "
                  "THE PLANT NOW FAULTS %.0f%% OFTEN."
                  % (kind.upper(), n, cost, 100 * (1 + 0.05 * w.bodges) - 100))


def do_repair(w, args):
    """Fix one fault, or everything. Returns (ok, message)."""
    if not w.faults:
        return False, "NOTHING IS DOWN. NOTHING TO REPAIR."
    if not args:
        total = sum(repair_cost(f["kind"], w) for f in w.faults)
        if w.money < total:
            return False, ("INSUFFICIENT FUNDS: REPAIRING EVERYTHING COSTS %.2f, CASH %.2f."
                           % (total, w.money))
        n = len(w.faults)
        w.money -= total
        w.clear_faults()
        w.journal("REPAIRED ALL FAULTS FOR %.2f" % total)
        return True, "ALL %d FAULTS REPAIRED. CASH %.2f. CAPACITY NOW %d." % (n, w.money, w.capacity)

    what = args[0].upper()
    if what in ("LINE", "LINES"):
        kind = "line"
    elif what in ("MODULE", "MODULES", "CORE", "CORES"):
        kind = "module"
    else:
        return False, "REPAIR WHAT? TRY: REPAIR   |   REPAIR LINE 3   |   REPAIR MODULE 1"
    if len(args) < 2:
        return False, "SAY WHICH ONE: FAULTS LISTS THEM."
    try:
        n = int(args[1])
    except ValueError:
        return False, "THE NUMBER MUST BE A WHOLE NUMBER."

    match = [f for f in w.faults if f["kind"] == kind and f["n"] == n]
    if not match:
        return False, "%s %d IS NOT DOWN. TYPE FAULTS TO SEE WHAT IS." % (what, n)
    cost = repair_cost(kind, w)
    if w.money < cost:
        return False, "INSUFFICIENT FUNDS: %.2f NEEDED, CASH %.2f." % (cost, w.money)

    w.money -= cost
    w.faults.remove(match[0])
    if w.db is not None:
        w.db.execute("DELETE FROM fault WHERE rowid IN "
                     "(SELECT rowid FROM fault WHERE kind=? AND n=? LIMIT 1)", (kind, n))
    w.journal("REPAIRED %s %d FOR %.2f" % (kind.upper(), n, cost))
    return True, ("%s %d REPAIRED FOR %.2f. CASH %.2f. CAPACITY %d."
                  % (what, n, cost, w.money, w.capacity))


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------
def strip_iac(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == 255 and i + 1 < n:      # IAC
            cmd = data[i + 1]
            if cmd in (251, 252, 253, 254) and i + 2 < n:  # WILL/WONT/DO/DONT
                i += 3
            elif cmd == 255:
                out.append(255)
                i += 2
            else:
                i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


def wire(text):
    """Turn a string into socket bytes with CRLF endings.

    The terminal is in raw mode, so a bare \n moves the cursor down without
    returning it to the left margin: text written with plain newlines renders as
    a diagonal staircase. Every screen goes through here.
    """
    if not isinstance(text, str):
        return text
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("latin-1")


def clean_line(raw: bytes) -> str:
    """Turn whatever the client sent into a command line.

    Handles a dumb client that posts a backspace byte instead of editing
    locally, and drops any other control characters rather than treating them
    as part of the command.
    """
    text = strip_iac(raw).decode("latin-1")
    out = []
    for ch in text:
        if ch in ("\x7f", "\b"):
            if out:
                out.pop()
        elif ch in ("\r", "\n"):
            continue
        elif ch.isprintable() or ch == "\t":
            out.append(ch)
    return "".join(out).strip()


async def run_command(world, rng, lock, line, peer=None):
    """The one command path. The socket and the web page both call this.

    Returns (text, closing). A second front end that reimplements the commands
    is a second copy of the rules, and it forgets them silently.
    """
    out = ""
    parts = line.split()
    cmd = parts[0].upper()
    if cmd in ("BYE", "QUIT", "EXIT", "LOGOFF"):
        return "\r\nCARRIER DROPPED. 73.\r\n\r\n", True
    if cmd == "HELP" or cmd == "?":
        out = r_help(parts[1] if len(parts) > 1 else None)
    elif cmd == "STATUS":
        out = r_status(world)
    elif cmd == "TICK":
        if len(parts) > 1:
            async with lock:
                ok, msg = do_tick_rate(world, parts[1:])
                world.save()
            out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
        else:
            out = ("\r\n  THE CLOCK TURNS EVERY %d SECOND%s.\r\n"
                   "  TICK n SETS IT (1 TO 3600).\r\n\r\n"
                   % (world.tick_seconds, "" if world.tick_seconds == 1 else "S"))
    elif cmd == "MODE":
        if len(parts) > 1:
            async with lock:
                ok, msg = do_mode(world, parts[1:])
                world.save()
            out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
        else:
            out = r_mode(world)
    elif cmd in ("USERS", "WHO"):
        out = r_users(world)
    elif cmd == "JOBS":
        out = r_jobs(world, rng)
    elif cmd in ("NEWS", "MESSAGES"):
        out = r_news(world)
        world.queue = []
        world.save()
    elif cmd == "LOG":
        out = r_log(world)
    elif cmd in ("INCIDENT", "DECISION", "QUESTION"):
        out = r_incident(world)
    elif cmd in ("DECIDE", "ANSWER", "CHOOSE"):
        if not world.incident:
            out = "\r\n  ERR: NOTHING IS WAITING FOR A DECISION.\r\n\r\n"
        elif len(parts) < 2:
            out = "\r\n  ERR: DECIDE WHAT? TYPE INCIDENT FOR THE CHOICES.\r\n\r\n"
        else:
            async with lock:
                ok, msg = world.decide(parts[1])
                world.save()
            out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd in ("MACHINES", "MODELS", "CATALOGUE"):
        out = r_machines(world)
    elif cmd in ("PLANT", "AGES", "WEAR"):
        out = r_plant(world)
    elif cmd in ("SERVICE", "MAINTAIN"):
        async with lock:
            ok, msg = do_service(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd == "UPGRADE":
        async with lock:
            ok, msg = do_upgrade_modules(world, parts[1:])
            world.save()
        out = ok_err(ok, msg)
    elif cmd == "REPLACE":
        async with lock:
            ok, msg = do_replace(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd in ("PROJECT", "PROJECTS"):
        out = r_project(world)
    elif cmd in ("PRICE", "RATE", "TARIFF"):
        if len(parts) > 1:
            async with lock:
                ok, msg = do_price(world, parts[1:])
                world.save()
            out = ok_err(ok, msg)
        else:
            out = r_price(world)
    elif cmd in ("ACCOUNTS", "CUSTOMERS", "CLIENTS"):
        out = r_accounts(world)
    elif cmd == "TRAIN":
        async with lock:
            ok, msg = do_train(world, parts[1:])
            world.save()
        out = ok_err(ok, msg)
    elif cmd in ("WORKS", "ENGINEERING"):
        out = r_works(world)
    elif cmd == "BUILD":
        async with lock:
            ok, msg = do_build(world, parts[1:])
            world.save()
        out = ok_err(ok, msg)
    elif cmd in ("RIVAL", "COMPETITION", "COMPETITOR"):
        out = r_rival(world)
    elif cmd in ("BANK", "LOANS"):
        out = r_bank(world)
    elif cmd in ("BORROW", "LOAN"):
        async with lock:
            ok, msg = do_borrow(world, parts[1:])
            world.save()
        out = ok_err(ok, msg)
    elif cmd == "REPAY":
        async with lock:
            ok, msg = do_repay(world, parts[1:])
            world.save()
        out = ok_err(ok, msg)
    elif cmd in ("STATEMENT", "RECORD"):
        out = "\r\n" + (world.statement or "  NO FINAL STATEMENT: WE HAVE NEVER BEEN CUT OFF.\r\n") + "\r\n"
    elif cmd in ("AWAY", "REPORT", "OVERNIGHT"):
        out = (world.last_report or "  NO REPORT: NOBODY HAS BEEN AWAY SINCE THE MACHINE STARTED.\r\n")
        out = "\r\n" + out + "\r\n"
    elif cmd in ("STAFF", "EMPLOYEES"):
        out = r_staff(world)
    elif cmd == "HIRE":
        async with lock:
            ok, msg = do_hire(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd == "FIRE":
        async with lock:
            ok, msg = do_fire(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd in ("UPGRADES", "SHOP"):
        out = r_upgrades(world)
    elif cmd in ("SITES", "LOCATIONS"):
        out = r_sites(world)
    elif cmd == "MOVE":
        async with lock:
            ok, msg = do_move(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd == "MODS":
        out = r_mods(world)
    elif cmd in ("EFFECTS", "TRADES", "LEDGER"):
        out = r_effects(world)
    elif cmd in ("PRESTIGE", "RETIRE"):
        async with lock:
            ok, msg = do_prestige(world)
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd in ("GOAL", "TARGET", "BUILDING"):
        out = r_goal(world)
    elif cmd in ("OFFERS", "CONTRACTS", "WORK"):
        out = r_offers(world)
    elif cmd in ("ACCEPT", "TAKE"):
        async with lock:
            ok, msg = do_accept(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd in ("CLOSE", "COMMIT", "FINISH"):
        async with lock:
            ok, msg = do_close(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd == "DROP":
        async with lock:
            ok, msg = do_drop(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd in ("FAULTS", "FAULT", "DOWN"):
        out = r_faults(world)
    elif cmd == "BUY":
        if len(parts) > 1 and parts[1].upper() in ("BUILDING", "HOUSE", "NORTHGATE"):
            async with lock:
                ok, msg = do_buy_building(world)
                world.save()
            out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
            if ok:
                out += "\r\n".join(C.ENDING) + "\r\n"
        elif " ".join(parts[1:]).upper() in C.UPGRADES:
            async with lock:
                ok, msg = do_buy_upgrade(world, " ".join(parts[1:]))
                world.save()
            out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
        else:
            async with lock:
                ok, msg = do_buy(world, parts[1:])
                world.save()
            out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd == "SELL":
        async with lock:
            ok, msg = do_sell(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd == "SCRAP":
        async with lock:
            ok, msg = do_scrap(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd == "BODGE":
        async with lock:
            ok, msg = do_bodge(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    elif cmd == "REPAIR":
        async with lock:
            ok, msg = do_repair(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    else:
        out = "\r\n  UNRECOGNISED COMMAND. TYPE HELP.\r\n\r\n"
        world.journal("BAD COMMAND FROM %s: %s" % (peer, line[:40]))
    return out, False

def upgrade_offer(w, name, price):
    """What swapping lesser modules for this model costs, one at a time."""
    dead = {f["n"] for f in w.faults_of("module")}
    costs = []
    for n in upgrade_candidates(w, name):
        old = w.module_model(n)["price_core"]
        credit = old * (C.BALANCE["salvage_rate"] if n in dead else C.TRADE_IN_RATE)
        costs.append(round(price - credit, 2))
    return {"name": name, "users": w.model(name)["core_users"], "costs": costs}


def roles_sheet(w):
    """Everything the staff sheet shows: each role, and whoever holds it."""
    out = []
    for role, data in C.ROLES.items():
        row = {"role": role, "fee": data["fee"], "salary": data["salary"],
               "desc": data["desc"], "does": effect_brief(data["effects"]),
               "train_cost": data["fee"] * C.TRAIN_COST, "hired": None}
        info = w.staff.get(role)
        if info:
            level = info.get("level", 1)
            nxt = C.STAFF_LEVEL_TICKS[level] if level < len(C.STAFF_LEVEL_TICKS) else None
            row["hired"] = {"name": info["name"], "level": level, "xp": info.get("xp", 0),
                            "next": nxt, "morale": info.get("morale", 0),
                            "salary": round(data["salary"] * info.get("pay", 1.0), 3),
                            "away": info.get("away_why", "") if info.get("away_until", 0) > w.ticks else "",
                            "back_in": max(0, info.get("away_until", 0) - w.ticks),
                            "does": effect_brief(w.staff_effects(role)) if info.get("away_until", 0) <= w.ticks
                            else ""}
        out.append(row)
    return out


def bank_sheet(w):
    return {"owed": round(w.loan["owed"], 2) if w.loan else 0.0,
            "due_in": (w.loan["due"] - w.ticks) if w.loan else 0,
            "interest": round(w.interest, 4),
            "limit": round(w.loan_limit(), 2),
            "barred_for": max(0, w.bank_barred - w.ticks),
            "rate_day": C.LOAN_RATE_DAY, "term": C.LOAN_TERM}


def shop(w):
    """The order form's catalogue: everything BUY sells, at today's prices."""
    fitting = (w.project.get("then") or {}).get("upgrade") if w.project else None
    return {
        "line": C.BALANCE["price_line"],
        "slot": C.BALANCE["price_slot"],
        "extra_slots": w.extra_slots,
        "models": [{"name": h["name"], "price": round(price, 2), "users": h["core_users"],
                    "ahead": price > h["price_core"] + 1e-9}
                   for h, price in w.on_sale()],
        "upgrade_to": [upgrade_offer(w, h["name"], price) for h, price in w.on_sale()],
        "upgrades": [{"name": n, "price": u["price"], "desc": u["desc"],
                      "owned": n in w.owned, "fitting": n == fitting}
                     for n, u in C.UPGRADES.items()],
        "building": {"price": round(w.goal_price, 2), "owned": bool(w.won_tick)},
        "busy": w.project["name"] if w.project else "",
    }


def snapshot(world):
    """Everything the web page draws, computed from the same objects the terminal uses."""
    world.sync_units()
    price = world.goal_price            # grows with each prestige, as BUY BUILDING charges
    return {
        "tick": world.ticks,
        "cash": round(world.money, 2),
        "earned": round(world.earned, 1),
        "net": round(world.net, 3),
        "revenue": round(world.revenue, 3),
        "outgoings": round(world.outgoings, 3),
        "salaries": round(world.salaries, 3),
        "site": world.site_name,
        "machine": world.hw_name,
        "cycle": world.prestiges,
        "callers_per_core": world.hardware["core_users"],
        "modules": [{"model": m["model"], "users": world.model(m["model"])["core_users"],
                     "wear": round(world.wear(m["born"]), 2),
                     "age": world.ticks - m["born"]} for m in world.modules],
        "servicing": world.servicing(),
        "price": world.price,
        "pricing": {"min": C.PRICE_MIN, "max": C.PRICE_MAX, "fair": C.PRICE_FAIR,
                    "elasticity": C.PRICE_ELASTICITY},
        "mode": world.mode,
        "shop": shop(world),
        "roles": roles_sheet(world),
        "bank": bank_sheet(world),
        "rival": ({"name": world.rival["name"], "price": world.rival["price"],
                   "take": round(world.rival_take(), 3)} if world.rival else None),
        "loan": ({"owed": round(world.loan["owed"], 2), "due_in": world.loan["due"] - world.ticks}
                 if world.loan else None),
        "works": sorted(world.works),
        "site_index": world.site,
        "room": {"modules": world.room[0], "lines": world.room[1]},
        "ceiling": world.ceiling,
        "cores": world.cores,
        "lines": world.lines,
        "working_cores": world.working_cores,
        "working_lines": world.working_lines,
        "capacity": world.capacity,
        "capacity_raw": world.capacity_raw,
        "capacity_why": world.capacity_why(),
        "subs": world.subs,
        "served": world.served,
        "running": world.running,
        "queued": world.queued,
        "slots": world.slots,
        "rep": round(world.rep, 2),
        "rep_trend": round(world.rep_trend, 4),
        "refused_share": round(world.refused_share, 3),
        "demand": int(world.demand),
        "overdraft": world.overdraft_ticks,
        "won": bool(world.won_tick),
        "won_tick": world.won_tick,
        "standing": world.standing,
        "goal": {"price": price, "pct": round(100.0 * min(1.0, world.money / price), 1)},
        "faults": [{"kind": f["kind"], "n": f["n"], "age": f["age"],
                    "cost": round(repair_cost(f["kind"], world), 2)} for f in world.faults],
        "staff": [{"role": r, "name": i["name"], "level": i.get("level", 1),
                   "morale": i.get("morale", 0),
                   "away": i.get("away_why", "") if i.get("away_until", 0) > world.ticks else "",
                   "salary": round(C.ROLES[r]["salary"] * i.get("pay", 1.0), 3)}
                  for r, i in world.staff.items()],
        "mods": [{"name": m["name"], "left": m["ticks_left"], "good": m["good"]}
                 for m in world.mods],
        "offers": [{"id": c["id"], "company": c["company"], "work": c["work"],
                    "want": c["want"], "duration": c["duration"], "fee": c["fee"],
                    "penalty": c["penalty"], "state": c["state"],
                    "left": c["ticks_left"], "breached": c["breached"]}
                   for c in world.offer_slots()],
        "upgrades": sorted(world.owned),
        "history": [(t, round(m, 1), round(n, 3)) for t, m, n in world.history[-180:]],
        "news": world.queue[-14:],
        "tick_seconds": world.tick_seconds,
        "project": ({"name": world.project["name"], "left": world.project["left"],
                     "total": world.project["total"]} if world.project else None),
        "incident": ({"title": world.incident_def()["title"], "left": world.incident["left"],
                      "choices": [c[0] for c in world.incident_def()["choices"]]}
                     if world.incident else None),
        # the row id of the last final statement: a front end that sees it
        # change knows the exchange has just cut us off
        "statement": world.db.execute("SELECT COALESCE(MAX(id), 0) FROM log "
                                      "WHERE kind='statement'").fetchone()[0]
                     if world.db is not None else 0,
        "to_next": round(max(0.0, world.last_tick + world.tick_seconds - time.time()), 2),
    }




async def web_handler(reader, writer, world, rng, lock):
    """A tiny HTTP server: the page, a state snapshot, and the command endpoint."""
    try:
        request = await asyncio.wait_for(reader.readline(), 15)
        if not request:
            return
        parts = request.decode("latin-1").split()
        method = parts[0] if parts else "GET"
        path = parts[1].split("?")[0] if len(parts) > 1 else "/"
        length = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), 15)
            if not line or line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1].strip())
        body = await reader.readexactly(length) if length else b""

        if path == "/state":
            payload = json.dumps(snapshot(world)).encode()
            ctype = "application/json"
            status = "200 OK"
        elif path == "/cmd" and method == "POST":
            try:
                line = json.loads(body or b"{}").get("line", "")
            except ValueError:
                line = ""
            # run_command takes the lock itself where it needs it: taking it here
            # as well deadlocks, because asyncio locks are not reentrant
            try:
                out, closing = await asyncio.wait_for(
                    run_command(world, rng, lock, line, "web"), 10)
            except asyncio.TimeoutError:
                out, closing = "\r\n  THAT COMMAND WEDGED. TRY AGAIN.\r\n", False
            world.save()
            payload = json.dumps({"out": out.replace("\r\n", "\n"), "closing": closing}).encode()
            ctype = "application/json"
            status = "200 OK"
        else:
            # no pages are served: the board was removed on purpose. This port
            # exists for /state (and /cmd) so front ends can read the numbers.
            payload = b"this port serves /state and /cmd only\n"
            ctype, status = "text/plain", "404 Not Found"
        head = ("HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
                % (status, ctype, len(payload)))
        writer.write(head.encode() + payload)
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError, ValueError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def welcome(world, name="OPERATOR", first=True):
    """What an operator sees on logging in: the welcome line, what is down,
    and, for the first one back, the report of what happened while away."""
    report = r_away(world) if first else ""
    if report:
        world.last_report = report
    world.mark_seen()
    down = ""
    if world.faults:
        down = ("  %d FAULT%s DOWN: %s  (TYPE FAULTS)\r\n"
                % (len(world.faults), "" if len(world.faults) == 1 else "S",
                   ", ".join(f["kind"].upper() + " %d" % f["n"] for f in world.faults)))
    if world.offers():
        down += ("  %d OFFER%s ON THE TELEPRINTER  (TYPE OFFERS)\r\n"
                 % (len(world.offers()), "" if len(world.offers()) == 1 else "S"))
    text = ("\r\nWELCOME, %s. TICK %d. CASH %.2f. %d CALLERS ON SYSTEM.\r\n%s\r\n"
            % ((name or "OPERATOR").upper()[:16], world.ticks, world.money, world.subs, down))
    return text + (report + "\r\n" if report else "")


def selftest():
    """Run the engine end to end without a window: a packaged build proves it
    carries everything it needs (sqlite, the content, the commands) on a
    machine with no screen. Returns 0 when all is well."""
    import tempfile
    failures = []

    def check(name, ok):
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "selftest.db")
        s = Session(path, 1)
        check("welcome", "WELCOME" in s.welcome())
        s.world.money = 1000.0
        for line, want in (("STATUS", "SYSTEM REPORT"), ("HELP INDEX", "TABS"),
                           ("BUY LINES 2", "OK:"), ("OFFERS", "WORK OFFERED"),
                           ("EFFECTS", "PUSHING"), ("PLANT", "UNIT BY UNIT")):
            out, _closing = s.command(line)
            check(line, want in out)
        for _ in range(500):
            s.world.tick(s.rng)
        check("ticks", s.world.ticks >= 500)
        s.close()
        again = Session(path, 1)
        check("reload", again.world.lines == 10 and again.world.ticks >= 500)
        again.close()
    print("TIMESHARE %s selftest: %s" % (VERSION, "OK" if not failures else
                                           "FAILED " + ", ".join(failures)))
    return 1 if failures else 0


class _NoLock:
    """The window runs everything on one thread: nothing to lock against."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class Session:
    """The world run inside the window's own process. Nothing listens on the
    network: commands and the numbers the panel draws go straight to the engine.

    Time missed while the game was closed is caught up in slices (step), so the
    window can show it happening instead of freezing; then welcome() gives the
    away report, once."""

    CATCH_UP_CAP = 60 * 24 * 30              # a month at most, as advance() allows

    def __init__(self, db_path, interval=None):
        self.world = open_world(db_path, interval)
        self.rng = random.Random()
        self.lock = _NoLock()
        w, now = self.world, time.time()
        self.owed = 0
        if w.last_tick <= 0 or w.mode == "session":
            w.last_tick = int(now)            # new, or frozen while nobody was on
        else:
            self.owed = max(0, min(int((now - w.last_tick) // w.tick_seconds), self.CATCH_UP_CAP))
        self.owed_total = self.owed
        self.welcomed = False

    def catching_up(self):
        return self.owed > 0

    def step(self, budget=0.15):
        """Run whatever ticks are due. While catching up, work for at most
        `budget` seconds at a time, so the window stays responsive."""
        w = self.world
        if self.owed:
            end = time.time() + budget
            while self.owed and time.time() < end:
                w.tick(self.rng)
                w.last_tick += w.tick_seconds
                self.owed -= 1
            if not self.owed:
                w.save()
            return
        if w.advance(time.time(), self.rng):
            w.save()

    def welcome(self):
        self.welcomed = True
        self.world.live = 1                   # someone is at the console now
        text = welcome(self.world, "OPERATOR")
        self.world.save()
        return text

    def command(self, line):
        """One command, exactly as the socket would have run it. (text, closing)"""
        return asyncio.run(run_command(self.world, self.rng, self.lock, line, "window"))

    def snapshot(self):
        return snapshot(self.world)

    def close(self):
        w = self.world
        w.live = 0
        w.mark_seen()                          # the next report starts here
        w.save()
        if w.db is not None:
            w.db.close()


async def handle(reader, writer, world, rng, lock):
    peer = writer.get_extra_info("peername")
    world.live += 1
    try:
        writer.write(wire("\r\n".join(C.BANNER) + "\r\n"))
        writer.write(b"OPERATOR ID? ")
        await writer.drain()
        name = clean_line(await asyncio.wait_for(reader.readline(), 120))
        async with lock:
            text = welcome(world, name, first=world.live <= 1)
        writer.write(wire(text))
        writer.write(PROMPT.encode())
        await writer.drain()

        while True:
            raw = await asyncio.wait_for(reader.readline(), 900)
            if not raw:
                break
            line = clean_line(raw)
            if not line:
                writer.write(PROMPT.encode())
                await writer.drain()
                continue
            out, closing = await run_command(world, rng, lock, line, peer)
            if closing:
                writer.write(wire(out))
                await writer.drain()
                break
            writer.write(wire(out))
            writer.write(PROMPT.encode())
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        world.live = max(0, world.live - 1)
        if world.live == 0:
            world.mark_seen()          # the next report starts from this hang-up
        try:
            writer.close()
        except Exception:
            pass


async def ticker(world, rng, lock, interval):
    """Wake often enough to notice a tick boundary, never more often than that.

    The world advances from wall-clock elapsed time, so sleeping through a tick
    (or the process being down) never loses one: advance() back-fills.
    """
    while True:
        # sleep to the tick boundary, so the tick lands when /state says it will.
        # Capped at a second so a TICK change is picked up promptly.
        due = world.last_tick + world.tick_seconds - time.time()
        await asyncio.sleep(min(1.0, max(0.02, due)))
        async with lock:
            n = world.advance(time.time(), rng)
            if n:
                world.save()


def default_save():
    """Where the save lives: the operating system's own place for app data.
    A save in the old place (~/timeshare) is kept where it is."""
    old = os.path.expanduser("~/timeshare/timeshare.db")
    if os.path.exists(old):
        return old
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "Timeshare", "timeshare.db")


def open_db(db_path):
    """The save: one SQLite file, its tables made or brought up to date."""
    db = None
    if db_path:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        db = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA journal_size_limit=4194304")
        db.execute("""CREATE TABLE IF NOT EXISTS world(
            id INTEGER PRIMARY KEY CHECK (id=1),
            money REAL, cores INTEGER, lines INTEGER, subs INTEGER,
            rep REAL, ticks INTEGER, last_tick INTEGER, tier INTEGER, queue TEXT,
            mode TEXT, tick_seconds INTEGER)""")
        for column in ("mode TEXT", "tick_seconds INTEGER"):
            try:                  # databases made before the column existed
                db.execute("ALTER TABLE world ADD COLUMN " + column)
            except sqlite3.OperationalError:
                pass
        db.execute("CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY AUTOINCREMENT, tick INTEGER, line TEXT)")
        for column in ("cash REAL", "net REAL", "val REAL", "kind TEXT"):
            try:                  # the away report reads these; old saves lack them
                db.execute("ALTER TABLE log ADD COLUMN " + column)
            except sqlite3.OperationalError:
                pass
        db.execute("CREATE TABLE IF NOT EXISTS fault(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, n INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS upgrade(name TEXT PRIMARY KEY, n INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS staff(role TEXT PRIMARY KEY, name TEXT, level INTEGER)")
        try:                      # a person's experience, mood, pay and absences
            db.execute("ALTER TABLE staff ADD COLUMN data TEXT")
        except sqlite3.OperationalError:
            pass
        db.execute("CREATE TABLE IF NOT EXISTS mod(name TEXT PRIMARY KEY, ticks_left INTEGER, "
                   "effects TEXT, good INTEGER)")
        db.execute("""CREATE TABLE IF NOT EXISTS contract(
            id INTEGER PRIMARY KEY, company TEXT, work TEXT, want INTEGER,
            duration INTEGER, ticks_left INTEGER, fee REAL, penalty REAL,
            state TEXT, breached INTEGER)""")
        db.execute("CREATE TABLE IF NOT EXISTS pending(kind TEXT PRIMARY KEY, data TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS unit(kind TEXT, n INTEGER, model TEXT, "
                   "born INTEGER, PRIMARY KEY (kind, n))")
        try:                      # teaching offers; saves from before lack it
            db.execute("ALTER TABLE contract ADD COLUMN lesson TEXT")
        except sqlite3.OperationalError:
            pass

    return db


def open_world(db_path, interval=None):
    """A world loaded from the save (or kept in memory if there is none)."""
    db = open_db(db_path)
    world = World(db)
    world.load()
    world.load_faults()
    world.load_upgrades()
    world.load_contracts()
    world.load_pending()
    world.load_units()
    if interval:                 # only an explicit --tick overrides the saved rate
        world.tick_seconds = interval
    return world


async def serve(port, db_path, interval, web_port=0):
    world = open_world(db_path, interval)
    rng = random.Random()
    lock = asyncio.Lock()
    with_lock = world.advance(time.time(), rng)
    if with_lock:
        world.save()
        print("caught up %d ticks while nobody was watching" % with_lock, flush=True)

    # a developer's tool (tests, the terminal client): this machine only. The
    # game itself runs the world in its own window and listens on nothing.
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, world, rng, lock), "127.0.0.1", port)
    web_server = None
    if web_port:
        web_server = await asyncio.start_server(
            lambda r, w: web_handler(r, w, world, rng, lock), "127.0.0.1", web_port)
    ticker_task = asyncio.create_task(ticker(world, rng, lock, interval))
    print("timeshare up on port %d, state %s, tick %ds"
          % (port, db_path or "(memory only)", world.tick_seconds), flush=True)
    if web_server:
        print("state at http://127.0.0.1:%d/state" % web_port, flush=True)
    async with server:
        await server.serve_forever()


# --------------------------------------------------------------------------
# headless balance sweep
# --------------------------------------------------------------------------
def sim(ticks, seed, strategy):
    w = World(None)
    rng = random.Random(seed)
    b = C.BALANCE

    def idle(_w, _t):
        pass

    def capacity(_w, _t):
        """Buy the cheapest thing that actually adds capacity, and keep slots fed."""
        if _w.queued and _w.money > b["price_slot"] * 1.2:
            do_buy(_w, ["SLOTS", "1"])
            return
        if _w.lines < _w.module_room_all and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])
        elif _w.money > b["price_core"] * 1.2:
            do_buy(_w, ["CORES", "1"])

    def lines(_w, _t):
        """Keep one line per caller of capacity, hoard the rest."""
        if _w.lines < _w.module_room_all and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])

    def balanced(_w, _t):
        """Match capacity to a margin, and only expand when demand is unmet."""
        if _w.capacity >= _w.demand and not _w.queued:
            return
        if _w.queued and _w.money > b["price_slot"] * 4:
            do_buy(_w, ["SLOTS", "1"])
            return
        if _w.lines < _w.module_room_all and _w.money > b["price_line"] * 4:
            do_buy(_w, ["LINES", "1"])
        elif _w.money > b["price_core"] * 4:
            do_buy(_w, ["CORES", "1"])

    def reckless(_w, _t):
        while _w.money > b["price_core"] * 1.2:
            if not do_buy(_w, ["CORES", "1"])[0]:
                break                  # the room is full: nothing left to overbuy

    def tycoon(_w, _t):
        """Engaged operator who also spends on staff, kit and premises."""
        for name in ("AUTOMATIC ANSWER", "TELEMETRY" if False else "TELEX MARKETING",
                     "COOLING PLANT", "BULK LINE RENTAL", "SECOND SHIFT"):
            if name in C.UPGRADES and name not in _w.owned and _w.money > C.UPGRADES[name]["price"] * 3:
                do_buy_upgrade(_w, name)
        for role in ("OPERATOR", "CLERK", "SALES", "ENGINEER"):
            if role not in _w.staff and _w.money > C.ROLES[role]["fee"] * 4:
                do_hire(_w, [role])
        nxt = _w.site + 1
        if nxt < len(C.LOCATIONS) and _w.money > C.LOCATIONS[nxt][1] * 3:
            do_move(_w, [str(nxt)])
        if _w.queued and _w.money > b["price_slot"] * 1.2:
            do_buy(_w, ["SLOTS", "1"])
            return
        if _w.lines < _w.module_room_all and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])
        elif _w.money > b["price_core"] * 1.2:
            do_buy(_w, ["CORES", "1"])

    def contractor(_w, _t):
        """Same buying as the engaged operator, plus it takes every offer it can staff."""
        if _w.lines < _w.module_room_all and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])
        elif _w.money > b["price_core"] * 1.2:
            do_buy(_w, ["CORES", "1"])
        for c in _w.offers():
            if _w.capacity >= c["want"]:
                do_accept(_w, [str(c["id"])])

    def no_slots(_w, _t):
        """Same buying as the engaged operator, but never buys a slot."""
        if _w.lines < _w.module_room_all and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])
        elif _w.money > b["price_core"] * 1.2:
            do_buy(_w, ["CORES", "1"])

    # ---- strategies that lean on one of the later systems each, so the sweep
    # can say whether any of them is a free win or a trap
    def servicer(_w, _t):
        """The capacity operator who also keeps the plant serviced."""
        capacity(_w, _t)
        if _w.project:
            return
        _w.sync_units()
        worn = max(range(1, _w.cores + 1), key=lambda n: _w.wear(_w.modules[n - 1]["born"]),
                   default=None)
        if worn and _w.wear(_w.modules[worn - 1]["born"]) > 2.0:
            do_service(_w, ["MODULE", str(worn)])
        elif _w.line_born and sum(_w.wear(x) for x in _w.line_born) / len(_w.line_born) > 2.2:
            do_service(_w, ["LINES"])

    def set_price(_w, top):
        """Dearer while callers are being turned away; cheaper only while there
        are places standing empty. A full house at list price is left alone."""
        if _w.refused_share > 0.10 and _w.price < top:
            do_price(_w, [str(_w.price + 5)])
        elif _w.capacity > _w.subs * 1.1 and _w.price > 80:
            do_price(_w, [str(_w.price - 5)])

    def pricer(_w, _t):
        """The capacity operator who sets the price to what the market bears."""
        capacity(_w, _t)
        set_price(_w, 150)

    def borrower(_w, _t):
        """Borrows to the limit to grow faster; pays back when it can."""
        if not _w.loan and _w.loan_limit() > 50:
            do_borrow(_w, ["%.0f" % _w.loan_limit()])
        capacity(_w, _t)
        if _w.loan and _w.money > 2 * _w.loan["owed"]:
            do_repay(_w, ["ALL"])

    def upgrader(_w, _t):
        """Buys the generation ahead instead of the standard, and upgrades old modules."""
        ahead = [h["name"] for h, p in _w.on_sale() if p > h["price_core"] + 1e-9]
        if _w.queued and _w.money > b["price_slot"] * 1.2:
            do_buy(_w, ["SLOTS", "1"])
            return
        if _w.lines < _w.module_room_all and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])
        elif ahead and _w.money > 260:
            if not do_buy(_w, [ahead[0], "1"])[0] and _w.money > 400:
                do_upgrade_modules(_w, ["MODULES", "1", ahead[0]])

    def engineer(_w, _t):
        """The capacity operator who builds every engineering work it can afford twice over."""
        capacity(_w, _t)
        if _w.project:
            return
        for name, wk in C.WORKS.items():
            if name not in _w.works and (not wk["needs"] or wk["needs"] in _w.works) \
                    and _w.money > 2 * wk["price"]:
                do_build(_w, name.split())
                break

    def staffer(_w, _t):
        """The capacity operator with a full staff it trains and pays."""
        capacity(_w, _t)
        for role in ("OPERATOR", "CLERK", "SALES", "ENGINEER"):
            if role not in _w.staff and _w.money > C.ROLES[role]["fee"] * 3:
                do_hire(_w, [role])
        for role, info in _w.staff.items():
            if info.get("level", 1) < 3 and _w.money > C.ROLES[role]["fee"] * 4:
                do_train(_w, [role])
                break

    def full(_w, _t):
        """Everything a good operator does: plant, contracts, staff, works, price, service."""
        staffer(_w, _t)
        for c in _w.offers():
            if _w.capacity >= c["want"] * 1.1:
                do_accept(_w, [str(c["id"])])
        set_price(_w, 140)
        if not _w.project:
            engineer(_w, _t)
        if not _w.project:
            servicer(_w, _t)

    fn = {"idle": idle, "lines": lines, "capacity": capacity,
          "balanced": balanced, "reckless": reckless,
          "norepair": capacity, "noslots": no_slots,
          "contractor": contractor, "tycoon": tycoon,
          "servicer": servicer, "pricer": pricer, "borrower": borrower,
          "upgrader": upgrader, "engineer": engineer, "staffer": staffer,
          "full": full}[strategy]

    def repair_if_needed():
        """Every sane operator fixes what is down; the idle and norepair controls do not.
        Anyone with a decision waiting takes the first choice it can afford."""
        if strategy in ("norepair", "idle"):
            return
        if w.incident and strategy in ("full", "staffer"):
            for word, _d, _o in w.incident_def()["choices"]:
                if w.decide(word)[0]:
                    break
        if w.faults and w.money > sum(repair_cost(f["kind"], w) for f in w.faults) * 1.2:
            do_repair(w, [])

    worst_net = 1e9
    cash_1day = None
    w.liquidations = 0
    for t in range(ticks):
        # a person, not a daemon: on for eight hours a day, away for sixteen,
        # and whatever breaks overnight waits for the morning
        online = t % 1440 < SIM_ONLINE
        if online and t % 30 == 0:
            fn(w, t)
        if online and t % 10 == 0:
            repair_if_needed()
        before = w.statement
        w.tick(rng)
        if w.statement is not before:
            w.liquidations += 1
        worst_net = min(worst_net, w.net)
        if t == 1439:
            cash_1day = w.money
    w.worst_net = worst_net
    w.cash_1day = cash_1day if cash_1day is not None else w.money
    return w


def worth(w):
    """What the business is worth: cash, the plant at its resale price, less the loan."""
    w.sync_units()
    plant = (sum(w.model(m["model"])["price_core"] for m in w.modules)
             + w.lines * C.BALANCE["price_line"]) * C.BALANCE["liquidation_rate"]
    return w.money + plant - (w.loan["owed"] if w.loan else 0.0)


SIM_ONLINE = 480        # minutes a day the simulated operator is at the console

SIM_STRATEGIES = ["idle", "lines", "capacity", "balanced", "contractor", "tycoon",
                  "reckless", "norepair", "noslots", "servicer", "pricer", "borrower",
                  "upgrader", "engineer", "staffer", "full"]


def run_sim(ticks, seeds, names=None):
    print("%-10s %9s %9s %8s %8s %7s %5s %11s" %
          ("strategy", "worth", "cash", "day1", "worstNet", "served", "lost", "cores/lines"))
    rows = {}
    for name in names or SIM_STRATEGIES:
        tot = {"worth": 0.0, "cash": 0.0, "day1": 0.0, "worst": 0.0, "served": 0.0, "lost": 0}
        cl = ""
        for s in range(seeds):
            w = sim(ticks, s * 17 + 3, name)
            tot["worth"] += worth(w)
            tot["cash"] += w.money
            tot["day1"] += w.cash_1day
            tot["worst"] += w.worst_net
            tot["served"] += w.served
            tot["lost"] += w.liquidations
            cl = "%d/%d" % (w.cores, w.lines)
        avg = {k: v / float(seeds) for k, v in tot.items()}
        avg["lost"] = tot["lost"]
        rows[name] = avg
        print("%-10s %9.1f %9.1f %8.1f %8.3f %7.1f %5d %11s" %
              (name, avg["worth"], avg["cash"], avg["day1"], avg["worst"], avg["served"],
               tot["lost"], cl))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=2023)
    ap.add_argument("--db", default=default_save())
    ap.add_argument("--tick", type=int, default=None,
                    help="seconds per tick (default: the rate saved with the world)")
    ap.add_argument("--web", type=int, default=0,
                    help="port for the browser board (0 = off, e.g. --web 2024)")
    ap.add_argument("--sim", type=int, metavar="TICKS")
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a BALANCE value, e.g. --set rate_per_user=0.05")
    a = ap.parse_args()
    for kv in a.set:
        k, _, v = kv.partition("=")
        if k not in C.BALANCE:
            raise SystemExit("unknown balance key: %s" % k)
        C.BALANCE[k] = int(float(v)) if k in INT_KEYS else float(v)
    if a.sim:
        run_sim(a.sim, a.seeds)
        return
    try:
        asyncio.run(serve(a.port, a.db or None, a.tick, a.web))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
