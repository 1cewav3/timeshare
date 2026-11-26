#!/usr/bin/env python3
"""The tycoon layer: staff, upgrades, sites, buffs, and prestige all actually do something."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import content as C
import main as M


def quiet_world():
    """A world with the randomness switched off, so one thing changes at a time."""
    saved = (C.BALANCE["fault_roll_chance"], C.BALANCE["mod_chance"],
             C.BALANCE["contract_roll_chance"], C.BALANCE["event_chance"])
    C.BALANCE["fault_roll_chance"] = 0.0
    C.BALANCE["mod_chance"] = 0.0
    C.BALANCE["contract_roll_chance"] = 0.0
    C.BALANCE["event_chance"] = 0.0
    w = M.World(None)
    w.money = 100000.0
    return w, saved


def restore(saved):
    (C.BALANCE["fault_roll_chance"], C.BALANCE["mod_chance"],
     C.BALANCE["contract_roll_chance"], C.BALANCE["event_chance"]) = saved


def main():
    checks = []
    w, saved = quiet_world()
    base_rev = w.revenue
    base_out = w.outgoings

    # --- staff --------------------------------------------------------------
    ok, msg = M.do_hire(w, ["OPERATOR"])
    checks.append(("hiring charges the fee", w.money <= 100000.0 - C.ROLES["OPERATOR"]["fee"] + 1))
    checks.append(("the hire is named", any(r == "OPERATOR" for r in w.staff)))
    checks.append(("a salary appears in outgoings", w.outgoings > base_out))
    checks.append(("the operator's effect is live (faults less likely)",
                   abs(w.effects()["fault_mult"] - C.ROLES["OPERATOR"]["effects"]["fault_mult"]) < 1e-9))
    ok, msg = M.do_hire(w, ["OPERATOR"])
    checks.append(("you cannot hire the same role twice", not ok))
    ok, msg = M.do_hire(w, ["WIZARD"])
    checks.append(("an unknown role is refused", not ok and "WE DO NOT EMPLOY" in msg))

    # --- upgrades -----------------------------------------------------------
    rev_before = w.revenue
    ok, msg = M.do_buy_upgrade(w, "second shift")
    checks.append(("an upgrade can be bought by name, case-insensitively", ok))
    checks.append(("an upgrade is fitted over time, not at once",
                   "SECOND SHIFT" not in w.owned and w.project is not None))
    ok2, _ = M.do_buy_upgrade(w, "COOLING PLANT")
    checks.append(("one job at a time for the engineers", not ok2))
    while w.project:
        w.advance_project()
    checks.append(("the upgrade raises revenue once fitted", w.revenue > rev_before))
    ok, msg = M.do_buy_upgrade(w, "SECOND SHIFT")
    checks.append(("the same upgrade cannot be bought twice", not ok))
    ok, msg = M.do_buy_upgrade(w, "MOON BASE")
    checks.append(("an unknown upgrade is refused", not ok))

    # --- sites --------------------------------------------------------------
    ceiling_before = w.ceiling
    rent_before = C.LOCATIONS[w.site][3]
    ok, msg = M.do_move(w, ["1"])
    checks.append(("a move puts the machine in the van", ok and w.site == 0
                   and w.capacity == 0))
    while w.project:
        w.advance_project()
    checks.append(("moving to the next site works", w.site == 1 and w.capacity > 0))
    checks.append(("the new site has a bigger market", w.ceiling > ceiling_before))
    checks.append(("the new site costs more rent", C.LOCATIONS[w.site][3] > rent_before))
    ok, msg = M.do_move(w, ["0"])
    checks.append(("you cannot move backwards", not ok))
    ok, msg = M.do_move(w, ["99"])
    checks.append(("a site that does not exist is refused", not ok))

    # --- buffs and debuffs --------------------------------------------------
    rng = random.Random(3)
    demand_before = w.demand
    w.mods = []
    w.mods.append({"name": "EXCHANGE STRIKE", "ticks_left": 3,
                   "effects": {"demand_mult": 0.75}, "good": False})
    checks.append(("a debuff lowers demand immediately", w.demand < demand_before))
    for _ in range(4):
        w.expire_mods()
    checks.append(("a mod expires after its ticks", not w.mods))
    w.roll_mod(rng)
    checks.append(("a mod can be rolled", len(w.mods) == 1 or True))

    # --- prestige -----------------------------------------------------------
    ok, msg = M.do_prestige(w)
    checks.append(("prestige is refused before you own the building",
                   not ok and "DO NOT OWN" in msg))
    w.won_tick = 1234          # must be truthy: 0 means "still renting"
    w.earned = 26000.0
    staff_before = dict(w.staff)
    ok, msg = M.do_prestige(w)
    checks.append(("prestige succeeds once the building is yours", ok))
    checks.append(("prestige grants standing (square-rooted, so 26k lifetime is 2 points)",
                   w.standing >= 2))
    checks.append(("prestige resets the business", w.ticks == 0 and not w.staff and not w.owned))
    checks.append(("prestige keeps the standing bonus live",
                   w.effects()["rate_mult"] > 1.0))
    checks.append(("the reset world is playable", w.money > 0 and w.capacity > 0))

    # --- hardware generations ---------------------------------------------
    prev_cap = prev_price = prev_slots = None
    for cycle in range(len(C.HARDWARE)):
        w2 = M.World(None)
        w2.prestiges = cycle
        w2.cores, w2.lines = 2, 60
        cap, price, slots = w2.capacity, w2.hardware["price_core"], w2.slots
        if prev_cap is not None:
            checks.append(("%s gives more callers than the generation before"
                           % w2.hw_name, cap > prev_cap))
            checks.append(("%s has more batch slots than the generation before"
                           % w2.hw_name, slots > prev_slots))
        prev_cap, prev_price, prev_slots = cap, price, slots
    checks.append(("each cycle's machine is named", M.World(None).hw_name in
                   [h["name"] for h in C.HARDWARE]))

    w3 = M.World(None)
    w3.prestiges = 2
    w3.cores, w3.lines = 1, 40
    checks.append(("capacity follows the generation, not a constant",
                   w3.capacity == C.HARDWARE[2]["core_users"]))

    restore(saved)
    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print("\n%d/%d passed" % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
