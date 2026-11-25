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
import time

import content as C

PROMPT = "OPR> "
LOG_KEEP = 2000
INT_KEYS = {"start_cores", "start_lines", "demand_ceiling", "fault_roll_every"}


# --------------------------------------------------------------------------
# world
# --------------------------------------------------------------------------
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
            "state, breached FROM contract ORDER BY id").fetchall()
        self.contracts = [dict(zip(("id", "company", "work", "want", "duration",
                                    "ticks_left", "fee", "penalty", "state",
                                    "breached"), r)) for r in rows]
        self.next_contract_id = (max((c["id"] for c in self.contracts), default=0) + 1)

    def save_contract(self, c):
        if self.db is None:
            return
        self.db.execute(
            "INSERT INTO contract (id, company, work, want, duration, ticks_left, fee, "
            "penalty, state, breached) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET ticks_left=excluded.ticks_left, "
            "state=excluded.state, breached=excluded.breached",
            (c["id"], c["company"], c["work"], c["want"], c["duration"],
             c["ticks_left"], c["fee"], c["penalty"], c["state"], c["breached"]))

    def drop_contract(self, cid):
        self.contracts = [c for c in self.contracts if c["id"] != cid]
        if self.db is not None:
            self.db.execute("DELETE FROM contract WHERE id=?", (cid,))

    def offers(self):
        return [c for c in self.contracts if c["state"] == "offered"]

    def active(self):
        return [c for c in self.contracts if c["state"] == "active"]

    def offer_contract(self, rng):
        b = C.BALANCE
        if len(self.offer_slots()) >= b["contract_max_pending"]:
            return
        company, work = rng.choice(C.CONTRACT_COMPANIES)
        # work is sized against what you can actually staff, not against demand:
        # demand grows during the term, so an offer pitched at capacity is a trap
        want = max(3, int(self.capacity * rng.choice((0.5, 0.6, 0.75, 0.9))))
        if want < 3:
            return
        duration = rng.choice((200, 300, 420))
        fee = round(want * b["contract_fee_per_caller"], 0)
        penalty = round(fee * b["contract_penalty_rate"], 0)
        c = {"id": self.next_contract_id, "company": company, "work": work,
             "want": want, "duration": duration, "ticks_left": duration,
             "fee": fee, "penalty": penalty, "state": "offered", "breached": 0}
        self.next_contract_id += 1
        self.contracts.append(c)
        self.save_contract(c)
        self.queue.append(C.CONTRACT_OFFER % (company, work, want, duration,
                                              fee, penalty, c["id"], c["id"]))

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
        self.mods.append({"name": name, "ticks_left": ticks,
                          "effects": effects, "good": good})
        self.queue.append(("GOOD NEWS: " if good else "BAD NEWS: ") + name
                          + " - " + ", ".join("%s %+.2f" % (k, v) for k, v in effects.items())
                          + " FOR %d TICKS" % ticks)
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
        self.owned = {r[0][3:] for r in self.db.execute(
            "SELECT name FROM upgrade WHERE name LIKE 'up:%'")}
        self.staff = {}
        for role, nm, lvl in self.db.execute("SELECT role, name, level FROM staff"):
            self.staff[role] = {"name": nm, "level": lvl}
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

    def journal(self, line):
        if self.db is not None:
            self.db.execute("INSERT INTO log (tick, line) VALUES (?,?)", (self.ticks, line))
            self.db.execute(
                "DELETE FROM log WHERE id <= (SELECT MAX(id) FROM log) - ?", (LOG_KEEP,)
            )

    # -- staff, upgrades, buffs: one effect pool ------------------------------
    def effects(self):
        eff = {k: (1.0 if k.endswith("_mult") else 0.0) for k in C.EFFECT_KEYS}
        eff["queue_rate"] = C.BALANCE["queue_rate"]
        sources = []
        for role in self.staff:
            sources.append(C.ROLES[role]["effects"])
        for name in self.owned:
            sources.append(C.UPGRADES[name]["effects"])
        for m in self.mods:
            sources.append(m["effects"])
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
        return eff

    @property
    def salaries(self):
        return sum(C.ROLES[r]["salary"] for r in self.staff)

    @property
    def hardware(self):
        return C.HARDWARE[min(self.prestiges, len(C.HARDWARE) - 1)]

    @property
    def hw_name(self):
        return self.hardware["name"]

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
    def ceiling(self):
        return C.LOCATIONS[self.site][2]

    # -- derived -----------------------------------------------------------
    @property
    def working_lines(self):
        return max(0, self.lines - len(self.faults_of("line")))

    @property
    def working_cores(self):
        return max(0, self.cores - len(self.faults_of("module")))

    @property
    def capacity(self):
        raw = min(self.working_cores * self.hardware["core_users"],
                  self.working_lines * C.BALANCE["line_users"])
        return int(raw * self.effects()["capacity_mult"])

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
        raw = (self.site_rent + self.cores * self.hardware["core_cost"]
               + self.lines * b["line_cost"] + self.salaries)
        return raw * self.effects()["cost_mult"]

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
        return max(0, min(self.ceiling, d))

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
        chance = (b["fault_roll_chance"] * self.effects()["fault_mult"]
                  * self.hardware["fault_mult"])
        if self.ticks % b["fault_roll_every"] == 0 and rng.random() < chance:
            self.roll_fault(rng)

        # the world changes: buffs and debuffs arrive and expire
        if self.ticks % b["mod_every"] == 0 and rng.random() < b["mod_chance"]:
            self.roll_mod(rng)
        self.expire_mods()

        self.earned += self.revenue
        self.history.append((self.ticks, self.money, self.net))
        del self.history[:-360]

        # money and reputation events (no faults: these cost or pay, nothing to fix)
        if rng.random() < b["event_chance"]:
            weights = [w for w, *_ in C.EVENTS]
            _w, msg, dm, dr = rng.choices(C.EVENTS, weights=weights, k=1)[0]
            self.money += dm
            self.rep = max(0.0, self.rep + dr)
            self.queue.append(msg)

        # contracts: new offers appear, live jobs are scored, deadlines land
        if self.ticks % b["contract_every"] == 0 and rng.random() < b["contract_roll_chance"]:
            self.offer_contract(rng)
        self.settle_contracts()

        # being overdrawn has a clock on it
        if self.money < 0:
            self.overdraft_ticks += 1
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
                if c["breached"] > allowed:
                    self.money -= c["penalty"]
                    self.rep = max(0.0, self.rep - 1.0)
                    self.queue.append("%s: WE SHORTED THEM ON %d OF %d TICKS. PENALTY %.0f CHARGED."
                                      % (c["company"], c["breached"], c["duration"], c["penalty"]))
                    self.journal("CONTRACT BREACHED: %s PENALTY %.2f" % (c["company"], c["penalty"]))
                else:
                    self.money += c["fee"]
                    self.rep = min(20.0, self.rep + 0.5)
                    self.queue.append("%s: SERVED. FEE %.0f PAID, ACCOUNT IN GOOD STANDING."
                                      % (c["company"], c["fee"]))
                    self.journal("CONTRACT MET: %s FEE %.2f" % (c["company"], c["fee"]))
                    c["fee"] = 0
                self.drop_contract(c["id"])

    def liquidate(self):
        b = C.BALANCE
        proceeds = (self.lines * b["price_line"]
                    + self.extra_slots * b["price_slot"]) * b["liquidation_rate"]
        self.money = max(0.0, self.money + proceeds)
        self.lines = b["start_lines"]
        self.extra_slots = 0
        if self.db is not None:
            self.db.execute("DELETE FROM upgrade")
        self.subs = 0
        self.rep = 0.0
        self.overdraft_ticks = 0
        for c in list(self.contracts):
            if c["state"] == "active":
                self.drop_contract(c["id"])
        self.queue.append("THE EXCHANGE HAS DISCONNECTED US FOR NON-PAYMENT.")
        self.queue.append("LINES SOLD AT %.0f%%, DEBT WRITTEN OFF. WE STILL HAVE THE MACHINE."
                          % (100 * b["liquidation_rate"]))
        self.journal("LIQUIDATION: PROCEEDS %.2f" % proceeds)

    def roll_fault(self, rng):
        """Break one line or one module that is currently working."""
        candidates = []
        working_lines = [i + 1 for i in range(self.lines)
                         if i + 1 not in {f["n"] for f in self.faults_of("line")}]
        working_cores = [i + 1 for i in range(self.cores)
                         if i + 1 not in {f["n"] for f in self.faults_of("module")}]
        if len(working_lines) > 1:
            candidates.append("line")
        if len(working_cores) > 1:
            candidates.append("module")
        if not candidates:
            return
        kind = rng.choice(candidates)
        if kind == "line":
            n = rng.choice(working_lines)
            self.add_fault("line", n)
            self.queue.append(rng.choice(C.FAULT_LINE) % n)
        else:
            n = rng.choice(working_cores)
            self.add_fault("module", n)
            self.queue.append(rng.choice(C.FAULT_MODULE) % n)
        self.journal("FAULT: %s %d DOWN" % (kind.upper(), n))

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
    body = [
        "",
        "  SYSTEM REPORT                      TICK %d" % w.ticks,
        "  " + "-" * 52,
        "  PROCESSING MODULES      %3d   (can serve %d callers, %s generation)"
        % (w.cores, w.cores * w.hardware["core_users"], w.hw_name),
        "  DIAL-IN LINES           %3d   (can carry %d callers)" % (w.lines, w.lines),
        site_line(w),
        capacity_line(w),
        staff_line(w),
        mods_line(w),
        ("  BUILDING                %9.2f of %.0f   (%.1f%% towards the deeds)"
         % ((w.money,) + goal_progress(w))) if not w.won_tick else
        ("  BUILDING                %s - YOURS SINCE TICK %d" % (C.BUILDING_NAME, w.won_tick)),
        contract_line(w),
        overdraft_line(w),
        idle_lines_line(w),
        "  CALLERS TURNED AWAY     %3d   (busy signal, paid nothing)" % max(0, w.subs - w.served),
        "  SUBSCRIBERS ON SYSTEM   %3d   of which served: %d" % (w.subs, w.served),
        "  REPUTATION              %5.2f  %s  (%.0f%% of callers refused, demand %d)"
        % (w.rep, ("+%.3f/tick" % w.rep_trend) if w.rep_trend >= 0 else ("%.3f/tick" % w.rep_trend),
           100 * w.refused_share, int(w.demand)),
        "  " + "-" * 52,
        "  PROCESSING IN SERVICE   %3d   DIAL TONE LINES IN SERVICE  %3d"
        % (w.working_cores, w.working_lines),
        "  BATCH SLOTS             %3d   running %d, queued %d"
        % (w.slots, w.running, w.queued),
        faults_block(w),
        "  " + "-" * 52,
        "  REVENUE PER TICK        %7.3f" % w.revenue,
        "  OUTGOINGS PER TICK      %7.3f" % w.outgoings,
        "  NET PER TICK            %7.3f" % w.net,
        "  CASH                    %7.2f" % w.money,
        "",
    ]
    return "\r\n".join(x for x in body if x != "") + "\r\n"


def capacity_line(w):
    """Say which limit is the binding one: that is what the player must buy."""
    cap = w.capacity
    by_module = w.working_cores * w.hardware["core_users"]
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
    spare = w.working_lines - w.working_cores * w.hardware["core_users"]
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
    out.append("  BUY BUILDING   when you have the money")
    out.append("")
    return "\r\n".join(out) + "\r\n"


def r_staff(w):
    out = ["", "  STAFF", "  " + "-" * 52]
    if not w.staff:
        out.append("  NOBODY WORKS HERE BUT YOU.")
    for role, info in w.staff.items():
        data = C.ROLES[role]
        out.append("  %-9s %-12s %.2f/TICK   %s" % (role, info["name"], data["salary"], data["desc"]))
    out.append("  " + "-" * 52)
    out.append("  SALARIES                 %.2f/TICK" % w.salaries)
    out.append("")
    for role, data in C.ROLES.items():
        if role not in w.staff:
            out.append("  HIRE %-9s %.0f FEE      %s" % (role, data["fee"], data["desc"]))
    out.append("  FIRE <role>   let someone go")
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
    out.append("  BUY <NAME>   e.g. BUY AUTOMATIC ANSWER")
    out.append("")
    return "\r\n".join(out) + "\r\n"


def r_sites(w):
    out = ["", "  SITES", "  " + "-" * 52]
    for i, (name, _p, ceiling, base_cost) in enumerate(C.LOCATIONS):
        price = w.site_price(i)
        here = "  <== YOU ARE HERE" if i == w.site else ""
        out.append("  %d  %-28s market %3d   rent %.2f/tick   %s%s"
                   % (i, name, ceiling, base_cost * (C.BALANCE["prestige_rent_growth"] ** w.prestiges),
                      "HERE" if i == w.site else ("%.0f TO MOVE" % price if i > w.site else "outgrown"),
                      ""))
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
        out.append("      needs %-12s for %d ticks   fee %.0f   penalty %.0f"
                   % (hold, c["duration"], c["fee"], c["penalty"]))
        if state == "LIVE":
            out.append("      %d ticks left, short on %d of them"
                       % (c["ticks_left"], c["breached"]))
    live = w.active()
    out.append("")
    out.append("  LIVE JOBS %d OF %d        ACCEPT n   take the work      DROP n   turn it down"
               % (len(live), C.CONTRACT_MAX_ACTIVE))
    if live:
        out.append("  CLOSE n    call a job in early: paid for the term served, plus the bonus")
    out.append("")
    return "\r\n".join(out) + "\r\n"


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
        by_module = w.working_cores * w.hardware["core_users"]
        by_line = w.working_lines * C.BALANCE["line_users"]
        if by_line <= by_module:
            reason = "every dial-in line is carrying a caller  ->  BUY LINES n"
        else:
            reason = "the modules cannot process another caller  ->  BUY CORES n"
        lines.append("  REASON: %s" % reason)
        lines.append("  THEY HEAR A BUSY SIGNAL AND PAY NOTHING. REPUTATION FALLS.")
        lines.append("  FIX: BUY LINES n (one caller each) or BUY CORES n (eight each)")
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
        lines.append("  BUY SLOTS n   adds a slot for %.2f each" % C.BALANCE["price_slot"])
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
        rows = w.db.execute("SELECT tick, line FROM log ORDER BY id DESC LIMIT 10").fetchall()
    out = ["", "  SYSTEM JOURNAL (LAST 10)", "  " + "-" * 52]
    out += ["  %06d  %s" % (t, l) for t, l in rows] or ["  NOTHING JOURNALISED YET."]
    out.append("")
    return "\r\n".join(out) + "\r\n"


def do_buy(w, args):
    """Returns (ok, message)."""
    if len(args) < 2:
        return False, "USAGE: BUY LINES n   |   BUY CORES n"
    what = args[0].upper()
    try:
        n = int(args[1])
    except ValueError:
        return False, "QUANTITY MUST BE A WHOLE NUMBER."
    if n <= 0 or n > 100:
        return False, "QUANTITY OUT OF RANGE."
    if what in ("LINE", "LINES"):
        price, key = C.BALANCE["price_line"], "lines"
    elif what in ("CORE", "CORES", "CPU"):
        price, key = w.hardware["price_core"], "cores"
    elif what in ("SLOT", "SLOTS", "CHANNEL", "CHANNELS"):
        price, key = C.BALANCE["price_slot"], "slots"
    else:
        return False, "WE DO NOT SELL %s." % what
    total = price * n
    if w.money < total:
        return False, "INSUFFICIENT FUNDS: %s REQUIRES %.2f, CASH IS %.2f." % (what, total, w.money)
    w.money -= total
    if key == "slots":
        w.buy_slot(n)
    else:
        setattr(w, key, getattr(w, key) + n)
    w.journal("PURCHASE: %d %s FOR %.2f" % (n, what, total))
    msg = ("%d %s INSTALLED. CASH %.2f. CAPACITY NOW %d, SLOTS %d, QUEUE %d."
           % (n, what, w.money, w.capacity, w.slots, w.queued))
    if key == "lines" and w.lines > w.working_cores * w.hardware["core_users"]:
        msg += ("\r\n       NOTE: your %d module(s) can only serve %d callers, so "
                "you now have %d line(s) that cannot carry anyone.\r\n"
                "       BUY CORES 1 to put them to work."
                % (w.working_cores, w.working_cores * w.hardware["core_users"],
                   w.lines - w.working_cores * w.hardware["core_users"]))
    if key == "cores" and w.working_cores * w.hardware["core_users"] > w.working_lines:
        msg += ("\r\n       NOTE: modules can serve %d but you have %d lines. "
                "BUY LINES n to fill them."
                % (w.working_cores * w.hardware["core_users"], w.working_lines))
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
    w.staff[role] = {"name": name}
    if w.db is not None:
        w.db.execute("INSERT OR REPLACE INTO staff (role, name, level) VALUES (?,?,1)",
                     (role, name))
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
    info = C.UPGRADES.get(key)
    if info is None:
        return False, ("WE DO NOT SELL %s. TYPE UPGRADES FOR THE CATALOGUE." % name.upper())
    if w.money < info["price"]:
        return False, ("INSUFFICIENT FUNDS: %s COSTS %.2f, CASH %.2f."
                       % (key, info["price"], w.money))
    w.money -= info["price"]
    w.owned.add(key)
    if w.db is not None:
        w.db.execute("INSERT OR REPLACE INTO upgrade (name, n) VALUES (?,1)", ("up:" + key,))
    w.journal("BOUGHT UPGRADE %s FOR %.2f" % (key, info["price"]))
    return True, "%s INSTALLED. %s" % (key, info["desc"])


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
    price = w.site_price(idx)
    if w.money < price:
        return False, ("INSUFFICIENT FUNDS: MOVING TO %s COSTS %.2f, CASH %.2f."
                       % (name, price, w.money))
    w.money -= price
    w.site = idx
    w.put("site", idx)
    w.journal("MOVED TO %s FOR %.2f" % (name, price))
    return True, ("MOVED TO %s. THE MARKET HERE IS %d CALLERS WIDE, RENT %.2f/TICK."
                  % (name, ceiling, base_cost))


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
    if w.capacity < c["want"]:
        return False, ("WE CAN ONLY SERVE %d CALLERS AND THEY WANT %d. "
                       "BUY LINES OR CORES FIRST, OR TAKE THE RISK LATER."
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
    held = c["duration"] - c["ticks_left"]
    allowed = c["duration"] * C.BALANCE["contract_breach_tolerance"]
    if c["breached"] > allowed:
        w.money -= c["penalty"]
        w.rep = max(0.0, w.rep - 1.0)
        w.drop_contract(cid)
        w.journal("CONTRACT CLOSED SHORT: %s PENALTY %.2f" % (c["company"], c["penalty"]))
        return True, ("CLOSED SHORT: %s WAS LATE ON %d TICKS. PENALTY %.0f CHARGED."
                      % (c["company"], c["breached"], c["penalty"]))
    share = min(1.0, held / float(c["duration"]))
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
    w.drop_contract(cid)
    if c["state"] == "active":
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
        if n >= w.lines:
            return False, "WE NEED AT LEAST ONE LINE TO STAY IN BUSINESS."
        w.lines -= n
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
        out = C.HELP
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
    elif cmd in ("MODS", "EFFECTS"):
        out = r_mods(world)
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
    elif cmd == "REPAIR":
        async with lock:
            ok, msg = do_repair(world, parts[1:])
            world.save()
        out = ("\r\n  OK: " if ok else "\r\n  ERR: ") + msg + "\r\n\r\n"
    else:
        out = "\r\n  UNRECOGNISED COMMAND. TYPE HELP.\r\n\r\n"
        world.journal("BAD COMMAND FROM %s: %s" % (peer, line[:40]))
    return out, False

def snapshot(world):
    """Everything the web page draws, computed from the same objects the terminal uses."""
    price = C.BALANCE["building_price"]
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
        "site_index": world.site,
        "ceiling": world.ceiling,
        "cores": world.cores,
        "lines": world.lines,
        "working_cores": world.working_cores,
        "working_lines": world.working_lines,
        "capacity": world.capacity,
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
        "staff": [{"role": r, "name": i["name"],
                   "salary": C.ROLES[r]["salary"]} for r, i in world.staff.items()],
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
        "to_next": max(0, int(world.tick_seconds - (time.time() - world.last_tick))),
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


async def handle(reader, writer, world, rng, lock):
    peer = writer.get_extra_info("peername")
    world.live += 1
    try:
        writer.write(wire("\r\n".join(C.BANNER) + "\r\n"))
        writer.write(b"OPERATOR ID? ")
        await writer.drain()
        name = clean_line(await asyncio.wait_for(reader.readline(), 120))
        down = ""
        if world.faults:
            down = ("  %d FAULT%s DOWN: %s  (TYPE FAULTS)\r\n"
                    % (len(world.faults), "" if len(world.faults) == 1 else "S",
                       ", ".join(f["kind"].upper() + " %d" % f["n"] for f in world.faults)))
        writer.write(("\r\nWELCOME, %s. TICK %d. CASH %.2f. %d CALLERS ON SYSTEM.\r\n%s\r\n"
                      % ((name or "OPERATOR").upper()[:16], world.ticks, world.money,
                         world.subs, down)).encode())
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
        await asyncio.sleep(min(world.tick_seconds, 5))
        async with lock:
            n = world.advance(time.time(), rng)
            if n:
                world.save()


async def serve(port, db_path, interval, web_port=0):
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
        db.execute("CREATE TABLE IF NOT EXISTS fault(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, n INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS upgrade(name TEXT PRIMARY KEY, n INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS staff(role TEXT PRIMARY KEY, name TEXT, level INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS mod(name TEXT PRIMARY KEY, ticks_left INTEGER, "
                   "effects TEXT, good INTEGER)")
        db.execute("""CREATE TABLE IF NOT EXISTS contract(
            id INTEGER PRIMARY KEY, company TEXT, work TEXT, want INTEGER,
            duration INTEGER, ticks_left INTEGER, fee REAL, penalty REAL,
            state TEXT, breached INTEGER)""")

    world = World(db)
    world.load()
    world.load_faults()
    world.load_upgrades()
    world.load_contracts()
    world.tick_seconds = interval
    rng = random.Random()
    lock = asyncio.Lock()
    with_lock = world.advance(time.time(), rng)
    if with_lock:
        world.save()
        print("caught up %d ticks while nobody was watching" % with_lock, flush=True)

    server = await asyncio.start_server(
        lambda r, w: handle(r, w, world, rng, lock), "0.0.0.0", port)
    web_server = None
    if web_port:
        web_server = await asyncio.start_server(
            lambda r, w: web_handler(r, w, world, rng, lock), "0.0.0.0", web_port)
    ticker_task = asyncio.create_task(ticker(world, rng, lock, interval))
    print("timeshare up on port %d, state %s, tick %ds"
          % (port, db_path or "(memory only)", interval), flush=True)
    if web_server:
        print("state at http://0.0.0.0:%d/state" % web_port, flush=True)
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
        if _w.lines < _w.cores * _w.hardware["core_users"] and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])
        elif _w.money > b["price_core"] * 1.2:
            do_buy(_w, ["CORES", "1"])

    def lines(_w, _t):
        """Keep one line per caller of capacity, hoard the rest."""
        if _w.lines < _w.cores * _w.hardware["core_users"] and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])

    def balanced(_w, _t):
        """Match capacity to a margin, and only expand when demand is unmet."""
        if _w.capacity >= _w.demand and not _w.queued:
            return
        if _w.queued and _w.money > b["price_slot"] * 4:
            do_buy(_w, ["SLOTS", "1"])
            return
        if _w.lines < _w.cores * _w.hardware["core_users"] and _w.money > b["price_line"] * 4:
            do_buy(_w, ["LINES", "1"])
        elif _w.money > b["price_core"] * 4:
            do_buy(_w, ["CORES", "1"])

    def reckless(_w, _t):
        while _w.money > b["price_core"] * 1.2:
            do_buy(_w, ["CORES", "1"])

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
        if _w.lines < _w.cores * _w.hardware["core_users"] and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])
        elif _w.money > b["price_core"] * 1.2:
            do_buy(_w, ["CORES", "1"])

    def contractor(_w, _t):
        """Same buying as the engaged operator, plus it takes every offer it can staff."""
        if _w.lines < _w.cores * _w.hardware["core_users"] and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])
        elif _w.money > b["price_core"] * 1.2:
            do_buy(_w, ["CORES", "1"])
        for c in _w.offers():
            if _w.capacity >= c["want"]:
                do_accept(_w, [str(c["id"])])

    def no_slots(_w, _t):
        """Same buying as the engaged operator, but never buys a slot."""
        if _w.lines < _w.cores * _w.hardware["core_users"] and _w.money > b["price_line"] * 1.2:
            do_buy(_w, ["LINES", "1"])
        elif _w.money > b["price_core"] * 1.2:
            do_buy(_w, ["CORES", "1"])

    fn = {"idle": idle, "lines": lines, "capacity": capacity,
          "balanced": balanced, "reckless": reckless,
          "norepair": capacity, "noslots": no_slots,
          "contractor": contractor, "tycoon": tycoon}[strategy]

    def repair_if_needed():
        """Every sane operator fixes what is down; the norepair control does not."""
        if strategy == "norepair":
            return
        if w.faults and w.money > sum(repair_cost(f["kind"], w) for f in w.faults) * 1.2:
            do_repair(w, [])

    worst_net = 1e9
    cash_1day = None
    for t in range(ticks):
        if t % 30 == 0:
            fn(w, t)
        if t % 10 == 0:
            repair_if_needed()
        w.tick(rng)
        worst_net = min(worst_net, w.net)
        if t == 1439:
            cash_1day = w.money
    w.worst_net = worst_net
    w.cash_1day = cash_1day if cash_1day is not None else w.money
    return w


def run_sim(ticks, seeds):
    names = ["idle", "lines", "capacity", "balanced", "contractor",
             "tycoon", "reckless", "norepair", "noslots"]
    print("%-10s %10s %9s %8s %10s %8s %10s" %
          ("strategy", "cash@%d" % ticks, "day1", "worstNet", "served",
           "busy%", "cores/lines"))
    for name in names:
        cash = served = demand = busy = 0.0
        day1 = worst = 0.0
        cl = ""
        for s in range(seeds):
            w = sim(ticks, s * 17 + 3, name)
            cash += w.money
            served += w.served
            demand += w.demand
            day1 += w.cash_1day
            worst += w.worst_net
            busy += (1.0 if w.subs and w.served < w.subs else 0.0)
            cl = "%d/%d" % (w.cores, w.lines)
        print("%-10s %10.1f %9.1f %8.3f %10.1f %7.0f%% %10s" %
              (name, cash / seeds, day1 / seeds, worst / seeds, served / seeds,
               100 * busy / seeds, cl))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=2023)
    ap.add_argument("--db", default=os.path.expanduser("~/timeshare/timeshare.db"))
    ap.add_argument("--tick", type=int, default=TICK_SECONDS)
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
