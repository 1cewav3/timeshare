"""The fiction and the tunable numbers. Engine stays generic, this file is yours.

Edit freely: names, prices, console messages. main.py reads BALANCE and the
tables below at import time.
"""

SERVICE = "NORTHGATE COMPUTER SERVICES"
MACHINE = "SYS/370-145"
LOCATION = "ROOM 2B, NORTHGATE HOUSE"
EPOCH = "SEP 1979"

BANNER = [
    "",
    "  " + SERVICE + "  ::  " + MACHINE,
    "  " + LOCATION + "  ::  TIMESHARING DIVISION",
    "",
    "  SYSTEM READY.  ALL OPERATOR COMMANDS ARE JOURNALISED.",
    "  UNAUTHORISED USE IS A BREACH OF CONTRACT SECTION 14.",
    "",
]

HELP = """\
COMMANDS
  STATUS          system report: revenue, outgoings, load
  USERS           who is dialled in and what they are costing you
  JOBS            what is running and what is queued behind it
  BUY LINES n     add dial-in lines (one caller each)
  BUY CORES n     add a processing module (eight callers each)
  BUY SLOTS n     add a batch slot: one more caller's work runs at once
  SELL LINES n    sell working lines you cannot fill (40% back)
  SELL SLOTS n    get rid of spare batch slots (40% back)
  FAULTS          what is down, how long, and what it costs to fix
  REPAIR          fix everything that is down
  REPAIR LINE n   fix one line          (REPAIR MODULE n for a module)
  STAFF           who works for you and what they cost
  HIRE <role>     take someone on      FIRE <role>     let them go
  UPGRADES        permanent equipment you can buy
  BUY <upgrade>   buy it
  SITES           locations you could run the machine from
  MOVE <n>        relocate the machine
  MODS            buffs and debuffs currently in force
  PRESTIGE        sell the business and start again, better
  GOAL            what you are working towards, and how far away it is
  MODE            which clock drives the world
  MODE WALL       the machine runs even with nobody dialled in
  MODE SESSION    the machine only runs while someone is on
  TICK n          seconds per tick (1 to 3600), saved with the world
  SCRAP LINE n    sell a dead unit for parts (20% of its price)
  BODGE LINE n    repair a dead unit at half price; each bodge adds 5% to the fault rate
  BUY BUILDING    buy the building and stop paying rent
  OFFERS          work offered to you, with a deadline and a penalty
  ACCEPT n        take offer n (up to 3 at once)
  CLOSE n         call a live job in early; it pays for the term you served
  DROP n          turn offer n down
  NEWS            console messages since you were last on
  AWAY            what happened while you were gone, again
  LOG             recent system journal
  BYE             drop carrier

Anything else is an unrecognised command and gets journalled as one.

"""

# ---- balance: measured 2026-11-17, see tools/sweep.sh for the runs ----
BALANCE = {
    "start_money": 120.0,
    "start_cores": 1,
    "start_lines": 8,
    "start_rep": 1.0,

    # capacity
    "core_users": 8,
    "line_users": 1,

    # money per tick (one tick = one minute of simulated time)
    "rate_per_user": 0.075,
    "base_cost": 0.150,
    "core_cost": 0.012,
    "line_cost": 0.010,

    # one-off prices
    "price_core": 95.0,
    "price_line": 12.0,
    "price_repair_line": 10.0,
    "price_repair_module": 45.0,
    # a bodge must cost more than a working line sells for (12 x 0.4 = 4.80),
    # or bodge-then-sell is a money machine; and a dead unit is worth less
    # than a working one
    "bodge_rate": 0.5,              # a bodge costs this share of a real repair
    "salvage_rate": 0.2,            # a dead unit sells for this share of its price
    "price_slot": 30.0,

    # batch throughput: how many callers' jobs run at once
    "start_slots": 8,
    "queue_rate": 0.5,

    # contracts: offers of work with a deadline and a penalty
    "contract_every": 150,          # ticks between offer rolls
    "contract_roll_chance": 0.5,
    "contract_max_pending": 2,
    "contract_fee_per_caller": 1.5, # fee = want x this
    "contract_penalty_rate": 2.0,  # penalty = fee x this
    "contract_breach_tolerance": 0.2,  # share of the term you may miss

    # rate at which things break
    "fault_roll_every": 100,        # ticks between fault rolls
    "fault_roll_chance": 0.15,      # 15% per roll: a break about every 670 ticks

    # random buffs and debuffs
    "mod_every": 300,               # ticks between rolls
    "mod_chance": 0.5,              # 50% per roll: something in the world shifts

    # the reason the business exists: buy the building the machine sits in
    "building_price": 25000.0,

    # every cycle the world gets dearer: the landlord has heard what you are worth
    "prestige_price_growth": 1.5,   # building and each next site cost this much more
    "prestige_rent_growth": 1.15,   # rent per tick, per cycle
    "prestige_points_per": 5000.0,  # points = sqrt(lifetime earnings / this)

    # running out of money has to hurt, or nothing is at stake
    "overdraft_grace": 360,         # ticks overdrawn before the exchange acts
    "liquidation_rate": 0.4,        # share of the sale value you keep
    "event_chance": 0.02,
    "fault_rep_penalty": 0.004,     # per tick while anything is down

    # demand and reputation
    "base_demand": 8.0,
    "demand_per_rep": 1.15,
    "demand_growth": 0.012,
    "demand_ceiling": 64,
    "rep_gain": 0.008,
    "rep_busy_penalty": 0.04,
}

# Things that happen to the business but do not break hardware: they cost or pay.
EVENTS = [
    (2, "CUSTOMER 0031 LEFT A JOB IN A LOOP AND RAN UP 41 MINUTES OF PRIME TIME. CALL LOGGED.", -9.0, -0.15),
    (2, "BILLING SENT THIS MONTH'S STATEMENTS. TWO ACCOUNTS SETTLED EARLY.", 26.0, 0.10),
    (1, "THE TELEPHONE PEOPLE RAISED THE LINE RENTAL BY 4 PER CENT. EFFECTIVE NEXT QUARTER.", 0.0, 0.0),
    (1, "A PROGRAMMER FROM THE UNIVERSITY ASKED FOR A SECOND SHIFT. REFERRED TO SALES.", 0.0, 0.05),
]

# Faults. %d is a real line or module number that you own, and REPAIR fixes it.
FAULT_LINE = [
    "LINE %d HAS GONE DEAD AT THE EXCHANGE. CALLERS REACH A RECORDING. REPAIR IT.",
    "LINE %d IS RETURNING A SUPERVISORY TONE THAT THE AUTOMATIC ANSWER REJECTS. REPAIR IT.",
]
FAULT_MODULE = [
    "PROCESSING MODULE %d FAILED ITS CHECKSUM AND IS OFFLINE. REPAIR IT.",
    "MODULE %d IS THROWING PARITY FAULTS ON EVERY JOB. REPAIR IT.",
]
JOB_NAMES = [
    "ACCTS-01", "PAYROLL-RUN", "STOCK-VAL", "FORT-CMPL", "SORT-MERGE",
    "LINEAR-REG", "MAILSHOT", "LEDGER-UPD", "STAT-ANAL", "INVOICE-BATCH",
]

ACCOUNT_SUFFIX = ["LTD", "AND CO", "HOLDINGS", "SERVICES", "PARTNERS", "ENGINEERING"]

# ---- contracts -------------------------------------------------------------
CONTRACT_COMPANIES = [
    ("BRIGHTON AND SONS", "PAYROLL"),
    ("MERIDIAN FREIGHT", "ROUTE PLANNING"),
    ("HALSTEAD INSURANCE", "ACTUARIAL BATCH"),
    ("THE BOROUGH COUNCIL", "RATES ROLL"),
    ("CROWN MILLS", "STOCK VALUATION"),
    ("PELHAM AND WEBB", "LEDGER POSTING"),
    ("NORTHFIELD DAIRY", "DELIVERY SCHEDULE"),
]

CONTRACT_OFFER = ("TELEX FROM %s (%s): THEY WANT %d CALLERS SERVED FOR %d TICKS.\n"
                  "  FEE %.0f ON COMPLETION. PENALTY %.0f IF WE SHORT THEM.\n"
                  "  ACCEPT %d   OR   DROP %d")

# ---- the end ---------------------------------------------------------------
BUILDING_NAME = "NORTHGATE HOUSE"

ENDING = [
    "",
    "  THE DEEDS ARE SIGNED. NORTHGATE HOUSE IS YOURS.",
    "",
    "  The landlord shakes your hand and asks what you will do with the room",
    "  upstairs. The telephone people send a boy to move the meter into your",
    "  name. From Monday the rent line on the statement reads zero, and the",
    "  machine keeps its own hours.",
    "",
    "  You are not a tenant any more. You are the bureau.",
]

# ---------------------------------------------------------------------------
# the tycoon layer: every effect is one of these keys, summed across sources
#   rate_mult      revenue per caller        demand_mult   callers who try
#   queue_rate     share paid while queued   fault_mult    how often things break
#   repair_mult    cost of a repair          slots_add     batch slots
#   capacity_mult  usable capacity           cost_mult     running costs
#   growth_add     extra market growth per tick
# ---------------------------------------------------------------------------
EFFECT_KEYS = ("rate_mult", "demand_mult", "demand_flat", "queue_rate",
               "fault_mult", "repair_mult", "slots_add", "capacity_mult",
               "cost_mult", "growth_add")

ROLES = {
    "OPERATOR": {
        "fee": 120.0, "salary": 0.10,
        "effects": {"fault_mult": 0.85, "repair_mult": 0.9},
        "desc": "watches the console, logs faults early, keeps the tapes clean",
    },
    "SALES": {
        "fee": 150.0, "salary": 0.12,
        "effects": {"growth_add": 0.004, "demand_mult": 1.08},
        "desc": "works the telephone, brings in accounts that would not have called",
    },
    "ENGINEER": {
        "fee": 220.0, "salary": 0.16,
        "effects": {"slots_add": 2, "capacity_mult": 1.05},
        "desc": "squeezes another channel out of the machine, fixes what the operator misses",
    },
    "CLERK": {
        "fee": 80.0, "salary": 0.06,
        "effects": {"queue_rate": 0.12, "cost_mult": 0.97},
        "desc": "chases invoices and stops the queued work being written off",
    },
}

UPGRADES = {
    "AUTOMATIC ANSWER": {
        "price": 260.0, "effects": {"queue_rate": 0.15},
        "desc": "a machine answers the line and queues the caller properly",
    },
    "COOLING PLANT": {
        "price": 420.0, "effects": {"fault_mult": 0.7},
        "desc": "the cabinet stops cooking itself in August",
    },
    "TELEX MARKETING": {
        "price": 340.0, "effects": {"growth_add": 0.006},
        "desc": "a weekly telex to every firm in the district",
    },
    "BULK LINE RENTAL": {
        "price": 500.0, "effects": {"cost_mult": 0.85},
        "desc": "the telephone people give you a tariff instead of a bill",
    },
    "SECOND SHIFT": {
        "price": 700.0, "effects": {"rate_mult": 1.12},
        "desc": "the machine runs till ten, and you charge for it",
    },
}

# name, price, demand ceiling, fixed cost per tick
LOCATIONS = [
    ("ROOM 2B, NORTHGATE HOUSE", 0.0, 64, 0.15),
    ("THE WHOLE FIRST FLOOR", 4200.0, 150, 0.55),
    ("THE OLD EXCHANGE BUILDING", 18000.0, 320, 1.60),
]

# a generation per cycle: the machine you get after each sale
HARDWARE = [
    {"name": "VALVE", "core_users": 8, "price_core": 95.0, "core_cost": 0.012,
     "fault_mult": 1.0, "start_slots": 8, "note": "hot, slow, and it eats valves"},
    {"name": "TRANSISTOR", "core_users": 12, "price_core": 130.0, "core_cost": 0.010,
     "fault_mult": 0.95, "start_slots": 10, "note": "cooler and quieter, one board per rack"},
    {"name": "INTEGRATED", "core_users": 18, "price_core": 200.0, "core_cost": 0.008,
     "fault_mult": 0.90, "start_slots": 12, "note": "a whole module on a fingernail"},
    {"name": "LSI", "core_users": 26, "price_core": 320.0, "core_cost": 0.007,
     "fault_mult": 0.85, "start_slots": 14, "note": "the machine fits in one cabinet"},
]

CONTRACT_MAX_ACTIVE = 3
CONTRACT_EARLY_BONUS = 1.15      # calling a job in early pays a little over the odds

ROLE_NAMES = ["MARCHETTI", "OKONKWO", "DALEY", "VOSS", "PEARCE", "IBRAHIM",
              "KOVACS", "SHARPE", "NGUYEN", "FAIRWEATHER", "BRENNAN", "ODA"]

# buffs and debuffs: name, ticks, effects, weight, good
MODS = [
    ("EXCHANGE STRIKE", 240, {"demand_mult": 0.75}, 3, False),
    ("TRADE BOOM", 300, {"demand_mult": 1.35, "rate_mult": 1.10}, 3, True),
    ("HEATWAVE", 200, {"fault_mult": 1.8}, 3, False),
    ("NEW COMPETITOR IN TOWN", 400, {"demand_mult": 0.85}, 2, False),
    ("GOVERNMENT CONTRACT", 350, {"demand_mult": 1.25, "cost_mult": 0.9}, 2, True),
    ("POWER CUT", 120, {"capacity_mult": 0.8}, 2, False),
    ("CHEAP COMPONENTS", 300, {"repair_mult": 0.5}, 2, True),
    ("UNION TROUBLE", 260, {"cost_mult": 1.25}, 2, False),
]
