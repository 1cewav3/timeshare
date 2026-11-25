"""Print every screen for two fixed world states, so the Rust port can be diffed."""
import os, sys
sys.path.insert(0, os.path.expanduser("~/Documents/projects/timeshare"))
import main as M
import content as C


def rich():
    w = M.World(None)
    w.money = 1500.25
    w.cores = 3
    w.lines = 12
    w.rep = 7.5
    w.ticks = 1234
    w.subs = 40
    w.extra_slots = 2
    w.rep_trend = -0.012
    w.refused_share = 0.375
    w.overdraft_ticks = 120
    w.earned = 8400.0
    w.standing = 2
    w.prestiges = 0
    w.won_tick = 0
    w.staff = {"OPERATOR": {"name": "VOSS"}, "CLERK": {"name": "DALEY"}}
    w.owned = {"AUTOMATIC ANSWER", "COOLING PLANT"}
    w.mods = [
        {"name": "TRADE BOOM", "ticks_left": 120,
         "effects": {"demand_mult": 1.35, "rate_mult": 1.10}, "good": True},
        {"name": "HEATWAVE", "ticks_left": 40, "effects": {"fault_mult": 1.8}, "good": False},
    ]
    w.faults = [{"kind": "line", "n": 3, "age": 12}, {"kind": "module", "n": 2, "age": 5}]
    w.contracts = [
        {"id": 1, "company": "BRIGHTON AND SONS", "work": "PAYROLL", "want": 9,
         "duration": 300, "ticks_left": 300, "fee": 14.0, "penalty": 28.0,
         "state": "offered", "breached": 0},
        {"id": 2, "company": "MERIDIAN FREIGHT", "work": "ROUTE PLANNING", "want": 12,
         "duration": 200, "ticks_left": 40, "fee": 18.0, "penalty": 36.0,
         "state": "active", "breached": 3},
    ]
    w.next_contract_id = 3
    w.queue = [
        "TELEX FROM BRIGHTON AND SONS (PAYROLL): THEY WANT 9 CALLERS SERVED FOR 300 TICKS.",
        "GOOD NEWS: TRADE BOOM - demand_mult +1.35 FOR 300 TICKS",
        "LINE 3 HAS GONE DEAD AT THE EXCHANGE. CALLERS REACH A RECORDING. REPAIR IT.",
    ]
    return w


def sparse():
    return M.World(None)


def won():
    w = rich()
    w.won_tick = 900
    w.site = 1
    w.prestiges = 1
    w.standing = 5
    w.faults = []
    w.subs = 0
    w.queue = []
    w.staff = {}
    w.mods = []
    w.contracts = []
    return w


def dump(name, w):
    rng = M.random.Random(42)
    print("=== %s:status ===" % name)
    print(M.r_status(w), end="")
    print("=== %s:goal ===" % name)
    print(M.r_goal(w), end="")
    print("=== %s:staff ===" % name)
    print(M.r_staff(w), end="")
    print("=== %s:upgrades ===" % name)
    print(M.r_upgrades(w), end="")
    print("=== %s:sites ===" % name)
    print(M.r_sites(w), end="")
    print("=== %s:mods ===" % name)
    print(M.r_mods(w), end="")
    print("=== %s:offers ===" % name)
    print(M.r_offers(w), end="")
    print("=== %s:faults ===" % name)
    print(M.r_faults(w), end="")
    print("=== %s:users ===" % name)
    print(M.r_users(w), end="")
    print("=== %s:jobs ===" % name)
    print(M.r_jobs(w, rng), end="")
    print("=== %s:news ===" % name)
    print(M.r_news(w), end="")
    print("=== %s:log ===" % name)
    print(M.r_log(w), end="")
    print("=== %s:help ===" % name)
    print(C.HELP, end="")
    print("=== %s:banner ===" % name)
    print("\r\n".join(C.BANNER) + "\r\n", end="")


for name, factory in (("rich", rich), ("sparse", sparse), ("won", won)):
    dump(name, factory())
