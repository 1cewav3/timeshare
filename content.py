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

# ---- the operator's manual ------------------------------------------------------
# Sections of the manual: a key (HELP <key>), a title, one sentence on what that
# part of the game is, and its commands. In the window each section is a tab;
# typed HELP prints them all. EVERY DAY repeats what most sessions need.
HELP_SECTIONS = [
    ("DAY", "EVERY DAY", "What most sessions need. Everything else is under the other tabs.", [
        ("STATUS", "the system report: money, load, what limits us"),
        ("BUY", "the order form: lines, modules, slots, equipment"),
        ("SETTINGS", "the settings sheet: the clock, the mode, our price"),
        ("OFFERS / ACCEPT n", "work on the teleprinter, and taking it"),
        ("FAULTS / REPAIR", "what is down, and fixing all of it"),
        ("INCIDENT / DECIDE <word>", "the question waiting, and answering it"),
        ("EFFECTS", "everything pushing the business up or down"),
        ("AWAY", "what happened while you were gone"),
        ("HELP <tab>", "one part of this manual: HELP PLANT, HELP MONEY..."),
        ("BYE", "hang up; the machine keeps running"),
    ]),
    ("PLANT", "THE PLANT", "Modules process callers and lines carry them; both wear, and both break.", [
        ("BUY", "the order form"),
        ("MACHINES", "the module models on sale, and what they do"),
        ("PLANT", "every unit: model, age, how worn"),
        ("FAULTS", "what is down, for how long, and the repair bill"),
        ("REPAIR [LINE|MODULE n]", "fix everything, or one unit"),
        ("BODGE LINE|MODULE n", "tape a dead unit up at half price; +5% breakdowns"),
        ("SCRAP LINE|MODULE n", "sell a dead unit for parts (20% back)"),
        ("SERVICE MODULE n|LINES", "worn units come back as good as new"),
        ("REPLACE MODULE n <model>", "trade a module in for a new one now"),
        ("UPGRADE MODULES n <model>", "swap the n most worn lesser ones (or on BUY)"),
        ("SELL LINES|SLOTS n", "get rid of what we cannot fill (40% back)"),
        ("USERS / JOBS", "who is dialled in; what is running and queued"),
    ]),
    ("MONEY", "MONEY", "Rent and wages go out every tick. Overdrawn too long, we are cut off.", [
        ("STATUS", "revenue, outgoings and cash, tick by tick"),
        ("PRICE [n]", "what we charge, per cent of list (or SETTINGS)"),
        ("BANK", "the bank sheet: what we owe; borrow and repay"),
        ("BORROW n / REPAY n|ALL", "the same, typed; interest every tick"),
        ("GOAL", "the building, and how far off it is"),
        ("STATEMENT", "the final statement from our last bankruptcy"),
    ]),
    ("PEOPLE", "PEOPLE", "Staff make the machine better and grow with experience. Customers remember.", [
        ("STAFF", "the staff sheet: who works here; hire, train, fire"),
        ("HIRE / FIRE <role>", "the same, typed"),
        ("TRAIN <role>", "a course: away 8 hours, back a level up"),
        ("ACCOUNTS", "our customers, and how they feel about us"),
    ]),
    ("WORK", "WORK", "Contracts guarantee callers for a term. Projects take the engineers' time.", [
        ("OFFERS", "work on the teleprinter: deadline, fee, penalty"),
        ("ACCEPT / DROP n", "take an offer, or turn it down (up to 3 live)"),
        ("CLOSE n", "call a live job in early; paid for the term served"),
        ("WORKS / BUILD <name>", "engineering projects, and starting one"),
        ("UPGRADES", "equipment the engineers can fit (order on BUY)"),
        ("PROJECT", "what the engineers are on, and the time left"),
    ]),
    ("WORLD", "THE WORLD", "Things happen while the machine runs. Some of them want an answer.", [
        ("INCIDENT / DECIDE <word>", "the question waiting; unanswered, its default"),
        ("RIVAL", "the other bureau in town, once there is one"),
        ("MODS", "good and bad turns in force, and for how long"),
        ("EFFECTS", "every trade pushing the business, + and -"),
        ("NEWS / LOG", "console messages; the system journal"),
        ("AWAY", "the report from the last time you were gone"),
    ]),
    ("BUREAU", "THE BUREAU", "Outgrow the room, own the building, sell up, start again better.", [
        ("SITES / MOVE n", "bigger premises; the machine is off in the van"),
        ("GOAL", "the building, and how far off it is"),
        ("PRESTIGE", "sell up once the building is ours; start again"),
        ("SETTINGS", "the clock and the mode (or TICK n / MODE)"),
        ("BYE", "hang up"),
    ]),
]


def _render_help(sections):
    out = ["HELP <tab> for one part: " + " ".join(k for k, *_ in sections), ""]
    for key, title, intro, rows in sections:
        if key == "DAY":
            continue                     # the full manual has every command anyway
        out.append(title)
        out.append("  " + intro)
        out += ["  %-26s %s" % row for row in rows]
        out.append("")
    out.append("Anything else is an unrecognised command and gets journalled as one.")
    return "\n".join(out) + "\n\n"


HELP = _render_help(HELP_SECTIONS)

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
        "fee": 120.0, "salary": 0.06,
        "effects": {"fault_mult": 0.75, "repair_mult": 0.8},
        "desc": "watches the console, logs faults early, keeps the tapes clean",
    },
    "SALES": {
        "fee": 150.0, "salary": 0.08,
        "effects": {"growth_add": 0.004, "demand_mult": 1.08},
        "desc": "works the telephone, brings in accounts that would not have called",
    },
    "ENGINEER": {
        "fee": 220.0, "salary": 0.11,
        "effects": {"slots_add": 2, "capacity_mult": 1.05},
        "desc": "squeezes another channel out of the machine, fixes what the operator misses",
    },
    "CLERK": {
        "fee": 80.0, "salary": 0.04,
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

# how much plant each site has room for, by LOCATIONS index: (modules, lines).
# Lines match the site's market, so the room never caps you below the callers
# who want you; a bigger site is how the plant grows past them.
SITE_ROOM = [(8, 64), (20, 150), (40, 320)]

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

# ---- the first offers teach the game ----------------------------------------
# A fresh world (not an old save, not after PRESTIGE) gets these one at a time,
# in order. Each is a grant with a lesson attached: the fee lands whether the
# player does the thing, turns it down, or ignores it until it finishes, so a
# player who already knows the game loses nothing by skipping them.
#   teach   accept  an ordinary small job
#           lines   wants `extra` more callers than we can carry when offered,
#                   so ACCEPT's own refusal points at the buy sheet
#           repair  breaks a working line; paid once that line is back up
#           close   a long job meant to be called in early with CLOSE
#   hint    printed under the offer; {id} {want} {capacity} {line} {fee} fill in
LESSONS = [
    {"teach": "accept", "company": "THE BOROUGH COUNCIL", "work": "TRIAL RUN",
     "want": 3, "duration": 60, "fee": 25.0,
     "hint": "A TRIAL. ACCEPT {id} AND KEEP {want} CALLERS ON FOR AN HOUR."},
    {"teach": "lines", "company": "NORTHFIELD DAIRY", "work": "DELIVERY SCHEDULE",
     "extra": 4, "duration": 120, "fee": 60.0,
     "hint": "WE CARRY {capacity}, THEY WANT {want}. A LINE CARRIES ONE CALLER "
             "AND A MODULE FEEDS EIGHT LINES. TYPE BUY: LINES, AND A MODULE WHEN THE "
             "LINES OUTRUN IT."},
    {"teach": "repair", "company": "THE TELEPHONE PEOPLE", "work": "CALL-OUT REFUND",
     "fee": 15.0,
     "hint": "LINE {line} HAS GONE DEAD. REPAIR LINE {line} AND THEY REFUND "
             "THE CALL-OUT."},
    {"teach": "close", "company": "CROWN MILLS", "work": "STOCK VALUATION",
     "want": 3, "duration": 600, "fee": 40.0,
     "hint": "A LONG JOB. ACCEPT {id}, THEN CLOSE {id} WHENEVER YOU LIKE: "
             "THE GRANT IS PAID IN FULL."},
]
LESSON_GRANT = "A GRANT: {fee:.0f} PAID WHETHER WE TAKE IT OR NOT."
LESSON_PAID = "{company}: GRANT OF {fee:.0f} PAID."

# ---- long projects ------------------------------------------------------------
# Some things take time. While a project runs its `while` effects apply; when
# it finishes, its result lands. One project at a time: the engineers are busy.
MOVE_TICKS = 240             # the machine is in the van: no capacity at all
MOVE_WHILE = {"capacity_mult": 0.0}
INSTALL_TICKS = 90           # an upgrade is fitted over this many ticks
INSTALL_WHILE = {"capacity_mult": 0.9}   # the engineers have a panel off

# ---- incidents: the machine asks the operator a question ---------------------
# One open at a time, rolled every `incident_every` ticks once the teaching
# offers are done. While it waits, `while` applies (that is why it matters);
# after `ticks` the first choice listed as `default` is taken for you.
# An outcome may hold: money, rep, mod (name, ticks, effects, good),
# fault ("line" or "module"), project (name, ticks, while, then-mod).
INCIDENT_EVERY = 400
INCIDENT_CHANCE = 0.5

INCIDENTS = [
    {"key": "payroll", "title": "CROWN MILLS RAN THEIR PAYROLL TWICE",
     "text": "THEIR NIGHT OPERATOR RAN THE PAYROLL JOB TWICE AND THEY WANT THE "
             "TIME BACK. THEIR CLERK RINGS EVERY HOUR UNTIL WE ANSWER.",
     "while": {"demand_mult": 0.95}, "ticks": 120, "default": "REFUSE",
     "choices": [
         ("REFUND", "give them the time back", {"money": -20.0, "rep": 0.6,
                                               "account": ("CROWN MILLS", 2),
                                               "say": "REFUNDED. CROWN MILLS TELL THEIR FRIENDS."}),
         ("SPLIT", "meet them halfway", {"money": -10.0, "rep": 0.2,
                                         "say": "SPLIT THE DIFFERENCE. NOBODY IS DELIGHTED."}),
         ("REFUSE", "their operator, their problem", {"rep": -0.8,
                                                     "account": ("CROWN MILLS", -2),
                                                     "say": "REFUSED. CROWN MILLS TELL THEIR FRIENDS."}),
     ]},
    {"key": "fan", "title": "A COOLING FAN IS SCREAMING",
     "text": "THE FAN IN THE MODULE CABINET HAS STARTED TO SCREAM. THE ENGINEER "
             "CAN SWAP IT NOW, WITH HALF THE MACHINE OFF FOR AN HOUR, OR WE RUN "
             "IT AND HOPE.",
     "while": {"fault_mult": 1.5}, "ticks": 120, "default": "RUN",
     "choices": [
         ("SWAP", "an hour at half capacity, 15 for the part",
          {"money": -15.0, "project": ("FAN SWAP", 60, {"capacity_mult": 0.5}, None),
           "say": "THE ENGINEER HAS THE CABINET OPEN."}),
         ("RUN", "leave it and listen", {"mod": ("SCREAMING FAN", 600, {"fault_mult": 2.0}, False),
                                        "say": "WE RUN IT. EVERYONE IN THE ROOM WEARS EAR DEFENDERS."}),
     ]},
    {"key": "student", "title": "A STUDENT OFFERS A BETTER BILLING PROGRAM",
     "text": "A STUDENT FROM THE POLYTECHNIC SAYS HE CAN REWRITE OUR BILLING "
             "PROGRAM SO IT CATCHES THE MINUTES WE MISS. HE WANTS 40.",
     "while": {}, "ticks": 180, "default": "DECLINE",
     "choices": [
         ("HIRE", "40 for the work", {"money": -40.0,
                                      "mod": ("NEW BILLING PROGRAM", 1440, {"rate_mult": 1.06}, True),
                                      "later": {"ticks": 2880, "company": "POLYTECHNIC SOFTWARE",
                                                "work": "PAYROLL PACKAGE", "goodwill": 2,
                                                "say": "THE STUDENT HAS STARTED A FIRM, AND "
                                                       "WANTS US TO RUN ITS FIRST CLIENT."},
                                      "say": "THE NEW BILLING RUN CATCHES MINUTES WE NEVER KNEW WE LOST."}),
         ("DECLINE", "the old program works", {"say": "HE GOES TO WORK FOR THE COMPETITION."}),
     ]},
    {"key": "reroute", "title": "THE TELEPHONE PEOPLE WANT TO RE-ROUTE US",
     "text": "THE EXCHANGE WANTS TO MOVE OUR LINES ONTO THE NEW SWITCH. TWO HOURS "
             "OF REDUCED SERVICE, AND A CHEAPER TARIFF AFTERWARDS.",
     "while": {}, "ticks": 240, "default": "DECLINE",
     "choices": [
         ("AGREE", "two hours at 60%, cheaper after",
          {"project": ("RE-ROUTING", 120, {"capacity_mult": 0.6},
                       ("NEW SWITCH", 2880, {"cost_mult": 0.92}, True)),
           "say": "THE EXCHANGE ENGINEERS ARE ON THE ROOF."}),
         ("DECLINE", "keep the old switch", {"say": "WE STAY ON THE OLD SWITCH."}),
     ]},
    {"key": "argus", "title": "THE EVENING ARGUS WANTS A TOUR",
     "text": "A REPORTER FROM THE EVENING ARGUS WANTS TO SEE THE MACHINE ROOM "
             "FOR A PIECE ON THE COMPUTER AGE.",
     "while": {}, "ticks": 150, "default": "REFUSE",
     "choices": [
         ("SHOW", "let them in", {"mod": ("IN THE PAPER", 600, {"demand_mult": 1.15}, True),
                                  "rep": 0.3, "say": "WE ARE ON PAGE FOUR, WITH A PHOTOGRAPH."}),
         ("REFUSE", "the room is a mess", {"say": "THE PIECE RUNS WITHOUT US."}),
     ]},
    {"key": "ceiling", "title": "WATER IS COMING THROUGH THE CEILING",
     "text": "THERE IS WATER COMING THROUGH THE CEILING ABOVE THE LINE RACK. "
             "A PLUMBER WILL COST REAL MONEY; A TARPAULIN WILL NOT.",
     "while": {"fault_mult": 1.6}, "ticks": 90, "default": "IGNORE",
     "choices": [
         ("PLUMBER", "35, and it is fixed", {"money": -35.0, "say": "THE PLUMBER FINDS A CRACKED TANK."}),
         ("TARPAULIN", "5, and it mostly holds", {"money": -5.0,
                                                 "mod": ("TARPAULIN", 400, {"fault_mult": 1.3}, False),
                                                 "say": "THE TARPAULIN SAGS BUT HOLDS."}),
         ("IGNORE", "it will stop", {"fault": "line", "say": "IT DID NOT STOP. A LINE IS DEAD."}),
     ]},
    {"key": "debtor", "title": "ACCOUNT 0047 HAS NOT PAID IN THREE MONTHS",
     "text": "CUSTOMER 0047 OWES THREE MONTHS AND STILL DIALS IN EVERY MORNING.",
     "while": {"rate_mult": 0.97}, "ticks": 200, "default": "RIDE",
     "choices": [
         ("CUT", "cut them off", {"rep": -0.2, "say": "0047 IS CUT OFF. THE LINE IS FREE."}),
         ("CHASE", "send the clerk round", {"money": 18.0, "rep": -0.3,
                                           "say": "THE CLERK COMES BACK WITH 18 AND A BLACK EYE."}),
         ("RIDE", "let it ride", {"mod": ("BAD DEBTS", 600, {"rate_mult": 0.97}, False),
                                  "say": "0047 KEEPS DIALLING. SO DO HIS FRIENDS."}),
     ]},
]

# ---- the plant ages, and better machines can be bought -------------------------
# Every module and line has an age. A unit's breakdown odds rise by its base
# again every WEAR_TICKS (three days), up to WEAR_CAP times; a breakdown picks
# worn units first. SERVICE makes a unit as good as new; REPLACE swaps a module
# for a new one, of any model on sale.
WEAR_TICKS = 4320
WEAR_CAP = 4.0

SERVICE_TICKS = 60            # a module is out of the rack while it is serviced
SERVICE_RATE = 0.25           # ...for this share of its model's price
LINE_SERVICE_TICKS = 120      # the exchange overhauls every line at once
LINE_SERVICE_RATE = 0.3       # ...for this share of a line's price, per line
LINE_SERVICE_WHILE = {"capacity_mult": 0.85}
TRADE_IN_RATE = 0.4           # a working module is worth this much towards a new one

# HARDWARE (above) is the standard issue for each cycle. Any model up to one
# generation past your cycle's standard is on sale; the one ahead costs more.
AHEAD_PREMIUM = 1.3

# ---- your price --------------------------------------------------------------
# PRICE n sets what we charge, in per cent of the list rate. Callers fall away
# as it rises (demand x price ** -ELASTICITY); above FAIR they also grumble, and
# reputation slips by PRICE_REP a tick for every point over.
PRICE_MIN, PRICE_MAX = 50, 200
PRICE_ELASTICITY = 1.3
PRICE_FAIR = 125
PRICE_REP = 0.0004

# ---- customers who remember -----------------------------------------------------
# Every company that sends work has goodwill, from -3 to +3. Served in full: +1.
# Shorted or dropped mid-job: -1, and at -1 or worse they tell their friends.
# Goodwill sizes their next job and its fee; at BLACKLIST they stop calling.
GOODWILL_FEE = 0.15           # fee x (1 + this x goodwill)
GOODWILL_SIZE = 0.2           # a returning customer wants this much more per point
GOODWILL_REP = 0.5            # extra reputation lost when an unhappy one talks
BLACKLIST = -2

# ---- staff are people -----------------------------------------------------------
# Experience brings levels; each level above the first adds STAFF_LEVEL_GAIN of a
# role's effect again. A level-up brings a request for a raise (a decision).
# Refused, a person sulks; a sulking person may leave. People fall ill.
STAFF_LEVEL_TICKS = [0, 1440, 4320]     # experience for levels 1, 2 and 3
STAFF_LEVEL_GAIN = 0.5
STAFF_RAISE = 0.3                       # a raise adds this share to a salary
STAFF_SICK_EVERY = 60                   # ticks between sickness rolls
STAFF_SICK_CHANCE = 0.006               # per person per roll: about once in a week
STAFF_SICK_TICKS = (180, 600)
STAFF_QUIT_CHANCE = 0.03                # per roll, for someone sulking
TRAIN_TICKS = 480                       # a course takes them away this long
TRAIN_COST = 1.0                        # ...for this many times the hiring fee

# ---- engineering works ------------------------------------------------------------
# Built by the engineers, one job at a time (BUILD <name>), and kept for the
# rest of the cycle. `needs` is a work that must be finished first.
WORKS = {
    "JOB SCHEDULER": {
        "price": 280.0, "ticks": 360, "needs": None,
        "effects": {"slots_add": 3, "queue_rate": 0.08},
        "desc": "a program that packs batch jobs tighter into the slots",
    },
    "LINE SCANNER": {
        "price": 300.0, "ticks": 480, "needs": None,
        "effects": {"fault_mult": 0.8},
        "desc": "tests every line at three in the morning and logs the weak ones",
    },
    "MODEM POOL": {
        "price": 450.0, "ticks": 720, "needs": None,
        "effects": {"demand_flat": 6},
        "desc": "callers who got an engaged tone are held for the next free line",
    },
    "BILLING TO THE SECOND": {
        "price": 400.0, "ticks": 480, "needs": "JOB SCHEDULER",
        "effects": {"rate_mult": 1.05},
        "desc": "the accounting run charges for every second, not every minute",
    },
    "REMOTE DIAGNOSTICS": {
        "price": 300.0, "ticks": 600, "needs": "LINE SCANNER",
        "effects": {"repair_mult": 0.6, "fault_mult": 0.95},
        "desc": "the engineer finds the fault from the console before going upstairs",
    },
    "SECOND POWER FEED": {
        "price": 700.0, "ticks": 960, "needs": "REMOTE DIAGNOSTICS",
        "effects": {"capacity_mult": 1.05, "fault_mult": 0.9},
        "desc": "a second cable from the substation; the machine stops browning out",
    },
}

# ---- the rival bureau ---------------------------------------------------------------
# Opens RIVAL_ARRIVES ticks into a run. Takes up to RIVAL_TAKE of our market at
# full strength and equal prices, more if we are dearer. Grows by itself, shrinks
# while our reputation is high, and goes bust if it shrinks to nothing.
RIVALS = [
    ("DATALINK BUREAU LTD", "THE CORN EXCHANGE"),
    ("MERCIA COMPUTING", "STATION ROAD"),
    ("ACORN DATA SERVICES", "THE OLD MILL"),
]
RIVAL_ARRIVES = 2880
RIVAL_EVERY = 360             # ticks between its moves
RIVAL_TAKE = 0.25
RIVAL_MAX_TAKE = 0.6
RIVAL_GROWTH = 0.0003         # strength a tick, 0 to 1
RIVAL_SHRINK = 0.0005         # a tick, while our reputation is at RIVAL_REP_BEATS or more
RIVAL_REP_BEATS = 10.0
RIVAL_MOVES = [               # move, weight
    ("cut", 3), ("raise", 2), ("advert", 2), ("undercut", 2),
    ("poach_account", 2), ("poach_staff", 1),
]

# ---- the bank -------------------------------------------------------------------------
# BORROW n up to a limit; interest is paid out of cash every tick; after
# LOAN_TERM the bank takes what it is owed, overdrawn or not. Cut off by the
# exchange, we are written off and the bank will not lend again for a while.
LOAN_RATE_DAY = 0.02          # interest per day on what is owed
LOAN_TERM = 14400             # ten days
LOAN_LIMIT_BASE = 200.0
LOAN_LIMIT_PLANT = 0.5        # plus this share of what the plant cost
LOAN_BARRED = 4320            # ticks after a liquidation before the bank will talk

# Decisions the game raises itself, never rolled at random. {name}, {role} and
# {rival} are filled in. Outcomes may also hold: pay (a raise, as a share of
# salary), morale (+1 or -1), leave (they go).
TRIGGERED_INCIDENTS = [
    {"key": "raise", "title": "{name} WANTS A RAISE",
     "text": "{role} {name} HAS LEARNT THE MACHINE AND KNOWS IT. A RAISE OF A "
             "THIRD, OR {name} STARTS LOOKING AT THE SITUATIONS VACANT.",
     "while": {}, "ticks": 240, "default": "REFUSE",
     "choices": [
         ("PAY", "a third more salary", {"pay": 0.3, "morale": 1,
                                         "say": "{name} IS PLEASED, AND SAYS SO."}),
         ("REFUSE", "not this year", {"morale": -1,
                                      "say": "{name} SAYS NOTHING, WHICH IS WORSE."}),
     ]},
    {"key": "poach", "title": "{rival} OFFER {name} A JOB",
     "text": "{rival} HAVE OFFERED {role} {name} MORE MONEY TO WORK FOR THEM. "
             "{name} WOULD STAY FOR A MATCH.",
     "while": {}, "ticks": 120, "default": "LET",
     "choices": [
         ("MATCH", "two fifths more salary", {"pay": 0.4, "morale": 1,
                                              "say": "{name} STAYS. {rival} ARE ANNOYED."}),
         ("LET", "let {name} go", {"leave": True,
                                   "say": "{name} CLEARS THE DESK ON FRIDAY."}),
     ]},
]
