# -*- coding: utf-8 -*-
"""
Sentinel-SC: Mock ERP Database Initializer
===========================================
Run:  python erp/init_db.py

Reference Date  : 2026-10-03
------------------------------------------------------------------
Scenario A -- SHORTAGE (hero path)
  Material      : STCOIL-440V
  On-hand       : 400 units
  Consumption   : 80 units/day
  TTS           : floor(400/80) = 5 days
  Stockout date : 2026-10-03 + 5  = 2026-10-08
  Original ETA  : 2026-10-11
  Disruption    : +7 days (Port Klang restriction)
  Revised ETA   : 2026-10-11 + 7  = 2026-10-18
  TTR           : (2026-10-18 - 2026-10-03).days = 15 days
  Shortage wdw  : 2026-10-08 -> 2026-10-18 (10 days, INCLUSIVE end)
  Exposure model: WO.planned_start IN [shortage_start, shortage_end]
                  AND WO has customer-order peg
                  -> exposure = co.qty * co.unit_price
  Hero WO       : WO-7782, planned_start=2026-10-14, peg=SO-55102
                  -> 500 * $240 = $120,000
  Zero-exposure : WO-7781 (no peg), WO-7783 (start 2026-10-19, after window)

Scenario B -- ABSORBED
  Material      : BEARING-6205
  On-hand       : 2000, consumption 100/day -> TTS=20 days
  Same TTR=15   -> TTS(20) >= TTR(15) -> ABSORBED, exposure=$0

False Positive  : MV Pacific Trader / Manila -> no match -> IGNORED
------------------------------------------------------------------
Architecture rule: DB = facts only. No derived verdicts. No penalty_per_day.
Exposure = co.qty * co.unit_price, computed at runtime by impact_math.py.
"""

import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent / "mock_erp.db"
REFERENCE_DATE = "2026-10-03"

# =====================================================================
# SCHEMA
# =====================================================================
SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS materials (
    material_id           TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    material_type         TEXT NOT NULL,
    unit                  TEXT NOT NULL,
    compatible_revisions  TEXT NOT NULL DEFAULT '[]',
    notes                 TEXT
);

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id          TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    country              TEXT NOT NULL,
    lead_time_days       INTEGER NOT NULL,
    moq                  INTEGER NOT NULL,
    capacity_per_month   INTEGER NOT NULL,
    freight_cost_air     REAL    NOT NULL DEFAULT 0.0,
    freight_cost_sea     REAL    NOT NULL DEFAULT 0.0,
    export_restricted    INTEGER NOT NULL DEFAULT 0,
    tier                 INTEGER NOT NULL DEFAULT 1,
    approved             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS supplier_certifications (
    cert_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id     TEXT NOT NULL REFERENCES suppliers(supplier_id),
    material_id     TEXT REFERENCES materials(material_id),
    cert_type       TEXT NOT NULL,
    valid_until     TEXT NOT NULL,
    product_family  TEXT
);

CREATE TABLE IF NOT EXISTS bom (
    bom_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_material_id TEXT NOT NULL REFERENCES materials(material_id),
    child_material_id  TEXT NOT NULL REFERENCES materials(material_id),
    qty_per_unit       REAL NOT NULL,
    revision           TEXT NOT NULL DEFAULT 'REV-A'
);

CREATE TABLE IF NOT EXISTS inventory (
    inventory_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id       TEXT NOT NULL REFERENCES materials(material_id),
    on_hand_qty       REAL NOT NULL,
    daily_consumption REAL NOT NULL,
    safety_stock      REAL NOT NULL DEFAULT 0.0,
    warehouse         TEXT NOT NULL DEFAULT 'MAIN'
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id        TEXT PRIMARY KEY,
    supplier_id  TEXT NOT NULL REFERENCES suppliers(supplier_id),
    material_id  TEXT NOT NULL REFERENCES materials(material_id),
    qty          REAL NOT NULL,
    unit_cost    REAL NOT NULL,
    po_date      TEXT NOT NULL,
    original_eta TEXT NOT NULL,
    revised_eta  TEXT,
    status       TEXT NOT NULL DEFAULT 'IN_TRANSIT'
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id        TEXT PRIMARY KEY,
    po_id              TEXT NOT NULL REFERENCES purchase_orders(po_id),
    vessel_name        TEXT,
    port_of_loading    TEXT,
    port_of_discharge  TEXT,
    original_eta       TEXT NOT NULL,
    revised_eta        TEXT,
    delay_days         INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'IN_TRANSIT'
);

CREATE TABLE IF NOT EXISTS customer_orders (
    co_id           TEXT PRIMARY KEY,
    product_id      TEXT NOT NULL REFERENCES materials(material_id),
    customer_name   TEXT NOT NULL,
    qty             REAL NOT NULL,
    unit_price      REAL NOT NULL,
    due_date        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'CONFIRMED'
);

CREATE TABLE IF NOT EXISTS work_orders (
    wo_id           TEXT PRIMARY KEY,
    co_id           TEXT REFERENCES customer_orders(co_id),
    product_id      TEXT NOT NULL REFERENCES materials(material_id),
    qty             REAL NOT NULL,
    start_date      TEXT NOT NULL,
    due_date        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'SCHEDULED',
    frozen_schedule INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wo_materials (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    wo_id        TEXT NOT NULL REFERENCES work_orders(wo_id),
    material_id  TEXT NOT NULL REFERENCES materials(material_id),
    required_qty REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    rule_id          TEXT PRIMARY KEY,
    rule_name        TEXT NOT NULL,
    rule_type        TEXT NOT NULL,
    parameter        TEXT NOT NULL,
    severity         TEXT NOT NULL,
    threshold_value  TEXT NOT NULL,
    description      TEXT
);

CREATE TABLE IF NOT EXISTS recovery_option_templates (
    template_id  TEXT PRIMARY KEY,
    option_type  TEXT NOT NULL,
    supplier_id  TEXT REFERENCES suppliers(supplier_id),
    material_id  TEXT REFERENCES materials(material_id),
    target_wo_id TEXT REFERENCES work_orders(wo_id),
    freight_mode TEXT,
    description  TEXT NOT NULL,
    notes        TEXT
);
"""


# =====================================================================
# SEED FUNCTIONS
# =====================================================================

def _seed_materials(c):
    rows = [
        ("APEXM-100",     "ApexX-100 Motor Drive Unit",            "FINISHED_GOOD", "UNIT",  '["REV-D"]',     "Hero finished good."),
        ("MOTOR-ASSY-01", "Motor Assembly",                         "SUBASSEMBLY",   "UNIT",  '["REV-D"]',     None),
        ("PCB-CTRL-01",   "Control PCB Assembly",                   "SUBASSEMBLY",   "UNIT",  '["REV-C","REV-D"]', None),
        ("HOUSING-01",    "Drive Housing Unit",                     "COMPONENT",     "UNIT",  '["REV-D"]',     None),
        ("STCOIL-440V",   "Stator Coil 440V (Primary)",             "RAW",           "UNIT",  '["REV-D"]',     "Hero disrupted material. on_hand=400, consumption=80/day."),
        ("STCOIL-415V",   "Stator Coil 415V (Substitute Candidate)","RAW",           "UNIT",  '["REV-C"]',     "Voltage variant. REV-C only (C6 FAIL). No PPAP (C3 FAIL)."),
        ("BEARING-6205",  "Deep Groove Ball Bearing 6205",          "RAW",           "UNIT",  '["REV-A"]',     "Scenario B: on_hand=2000, consumption=100/day -> TTS=20 -> ABSORBED."),
        ("COPPER-WIRE-2", "Copper Wire 2mm Spool",                  "RAW",           "SPOOL", '["REV-A","REV-B"]', "Noise: BOM traversal must NOT route into STCOIL impact."),
        ("CAPACITOR-100", "Electrolytic Capacitor 100uF",           "RAW",           "UNIT",  '["REV-A"]',     "Noise: PCB branch, must NOT appear in STCOIL exposure."),
    ]
    c.executemany("INSERT OR REPLACE INTO materials VALUES (?,?,?,?,?,?)", rows)


def _seed_suppliers(c):
    # C1: available_days = (revised_eta - reference_date).days = 15
    # SUPP-002 Hanoi: lead 18 > 15 -> C1 FAIL
    # SUPP-003 Euro:  lead  7 <= 15 -> C1 PASS
    rows = [
        # id          name                   CC  lead  moq   cap    air      sea     exp_restr tier appr
        ("SUPP-001", "ShenZen Coil Corp",   "CN", 12,  500, 2000, 45000.0,  8500.0,  0, 1, 1),
        ("SUPP-002", "Hanoi Coils Ltd",     "VN", 18,  300, 1500, 28000.0,  7200.0,  0, 2, 1),
        ("SUPP-003", "EuroCoils GmbH",      "DE",  7,  200, 1000, 30100.0, 22000.0,  0, 1, 1),
        ("SUPP-004", "FastCoil Asia Pte",   "SG", 10,  400,  800, 25500.0,  9800.0,  0, 2, 1),
        ("SUPP-005", "OmegaBearings GmbH",  "DE",  5,  100, 5000,  3200.0,  1100.0,  0, 1, 1),
    ]
    c.executemany("INSERT OR REPLACE INTO suppliers VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)


def _seed_certifications(c):
    # SUPP-003 (EuroCoils): PPAP for STCOIL -> C3 PASS for OPT-A
    # SUPP-002 (Hanoi): no PPAP -> C3 FAIL (moot, C1 already vetoes)
    # SUPP-004 (FastCoil): no PPAP for STCOIL-415V -> C3 FAIL for OPT-C
    rows = [
        (None, "SUPP-001", "STCOIL-440V", "PPAP",      "2027-12-31", "STATOR_COILS"),
        (None, "SUPP-001",  None,          "ISO9001",   "2027-06-30",  None),
        (None, "SUPP-003", "STCOIL-440V", "PPAP",      "2027-12-31", "STATOR_COILS"),
        (None, "SUPP-003",  None,          "IATF16949", "2027-09-30",  None),
        (None, "SUPP-003",  None,          "ISO9001",   "2027-09-30",  None),
        (None, "SUPP-005",  None,          "ISO9001",   "2027-03-31",  None),
    ]
    c.executemany("INSERT OR REPLACE INTO supplier_certifications VALUES (?,?,?,?,?,?)", rows)


def _seed_bom(c):
    # revision = active BOM revision this row belongs to
    # C6 check: child material.compatible_revisions must contain this revision
    # STCOIL-415V: compatible_revisions=["REV-C"] does NOT contain REV-D -> C6 FAIL
    # Noise branches: CAPACITOR-100 (PCB-CTRL-01) and COPPER-WIRE-2 (MOTOR-ASSY-01)
    # must NOT appear in upward traversal from STCOIL-440V
    rows = [
        ("APEXM-100",     "MOTOR-ASSY-01",  1.0, "REV-D"),
        ("APEXM-100",     "PCB-CTRL-01",    1.0, "REV-D"),
        ("APEXM-100",     "HOUSING-01",     1.0, "REV-D"),
        ("MOTOR-ASSY-01", "STCOIL-440V",    2.0, "REV-D"),
        ("MOTOR-ASSY-01", "BEARING-6205",   2.0, "REV-A"),
        ("MOTOR-ASSY-01", "COPPER-WIRE-2",  0.5, "REV-A"),
        ("PCB-CTRL-01",   "CAPACITOR-100",  4.0, "REV-D"),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO bom (parent_material_id, child_material_id, qty_per_unit, revision) VALUES (?,?,?,?)",
        rows
    )


def _seed_inventory(c):
    # STCOIL-440V: TTS = 400/80 = 5 days (Scenario A)
    # BEARING-6205: TTS = 2000/100 = 20 days (Scenario B -- absorbs 15-day delay)
    rows = [
        ("STCOIL-440V",   400.0,   80.0,   80.0, "MAIN"),
        ("STCOIL-415V",     0.0,    0.0,    0.0, "MAIN"),
        ("BEARING-6205", 2000.0,  100.0,  200.0, "MAIN"),
        ("PCB-CTRL-01",   120.0,   10.0,   20.0, "MAIN"),
        ("HOUSING-01",    200.0,   15.0,   30.0, "MAIN"),
        ("CAPACITOR-100",10000.0, 500.0, 1000.0, "MAIN"),
        ("COPPER-WIRE-2",   50.0,   5.0,   10.0, "MAIN"),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO inventory (material_id, on_hand_qty, daily_consumption, safety_stock, warehouse) VALUES (?,?,?,?,?)",
        rows
    )


def _seed_customer_orders(c):
    # Exposure model: co.qty * co.unit_price (no penalty_per_day)
    # SO-55102 pegged to WO-7782 -> 500 * $240 = $120,000 exact exposure
    rows = [
        # co_id       product      customer                  qty   price    due_date      status
        ("SO-55102", "APEXM-100", "Pinnacle Industrial AG", 500,  240.0,  "2026-10-20", "CONFIRMED"),
    ]
    c.executemany("INSERT OR REPLACE INTO customer_orders VALUES (?,?,?,?,?,?,?)", rows)


def _seed_work_orders(c):
    # Frozen window: reference_date (2026-10-03) + 14 days = through 2026-10-17
    # WO is frozen if start_date <= 2026-10-17
    #
    # WO-7781: start 2026-10-05, no co_id peg -> exposure = $0 (no peg)
    # WO-7782: start 2026-10-14, co_id=SO-55102, IN shortage window -> $120,000
    #          start 2026-10-14 < frozen_through 2026-10-17 -> frozen_schedule=1 -> C4 fires
    # WO-7783: start 2026-10-19, no co_id peg, AFTER window end (2026-10-18) -> $0
    rows = [
        # wo_id      co_id       product       qty   start         due           status       frozen
        ("WO-7781",  None,      "APEXM-100",  50,  "2026-10-05", "2026-10-20", "SCHEDULED",  1),
        ("WO-7782",  "SO-55102","APEXM-100",  50,  "2026-10-14", "2026-10-22", "SCHEDULED",  1),
        ("WO-7783",  None,      "APEXM-100",  30,  "2026-10-19", "2026-10-25", "SCHEDULED",  0),
    ]
    c.executemany("INSERT OR REPLACE INTO work_orders VALUES (?,?,?,?,?,?,?,?)", rows)


def _seed_wo_materials(c):
    # Direct pegging table; BOM traversal validates these.
    # Noise materials included per BOM but must NOT appear in STCOIL exposure report.
    rows = [
        ("WO-7781", "STCOIL-440V",   100.0),
        ("WO-7781", "BEARING-6205",  100.0),
        ("WO-7781", "COPPER-WIRE-2",  25.0),
        ("WO-7781", "CAPACITOR-100", 200.0),
        ("WO-7782", "STCOIL-440V",    80.0),
        ("WO-7782", "BEARING-6205",   80.0),
        ("WO-7782", "COPPER-WIRE-2",  20.0),
        ("WO-7782", "CAPACITOR-100", 160.0),
        ("WO-7783", "STCOIL-440V",    60.0),
        ("WO-7783", "BEARING-6205",   60.0),
        ("WO-7783", "COPPER-WIRE-2",  15.0),
        ("WO-7783", "CAPACITOR-100", 120.0),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO wo_materials (wo_id, material_id, required_qty) VALUES (?,?,?)",
        rows
    )


def _seed_purchase_orders(c):
    rows = [
        # Scenario A: DELAYED at Port Klang, +7 days
        ("PO-2026-441", "SUPP-001", "STCOIL-440V",  800, 42.50, "2026-09-20", "2026-10-11", "2026-10-18", "DELAYED"),
        # Scenario B: same delay, BEARING-6205 -- buffer absorbs
        ("PO-2026-512", "SUPP-005", "BEARING-6205", 1000,  8.90, "2026-09-25", "2026-10-11", "2026-10-18", "DELAYED"),
        # Background delivered PO -- noise
        ("PO-2026-389", "SUPP-001", "COPPER-WIRE-2", 100, 15.00, "2026-09-15", "2026-10-05", None,         "DELIVERED"),
    ]
    c.executemany("INSERT OR REPLACE INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?)", rows)


def _seed_shipments(c):
    # SHIP-440V-01: Signal agent correlates "Port Klang" + "MV Sentinel Star" -> this row
    # False positive "MV Pacific Trader" / "Manila" -> NO match in this table -> IGNORED
    rows = [
        ("SHIP-440V-01", "PO-2026-441", "MV Sentinel Star", "Port Klang", "Plant Main Port", "2026-10-11", "2026-10-18", 7, "DELAYED"),
        ("SHIP-6205-01", "PO-2026-512", "MV Nordic Blue",   "Hamburg",    "Plant Main Port", "2026-10-11", "2026-10-18", 7, "DELAYED"),
    ]
    c.executemany("INSERT OR REPLACE INTO shipments VALUES (?,?,?,?,?,?,?,?,?)", rows)


def _seed_rules(c):
    # C1 threshold 15 = (revised_eta - reference_date).days -- evaluator recomputes dynamically
    # threshold_value = DB facts / policy. NO verdicts stored here.
    rows = [
        ("C1", "Lead Time Feasibility",      "MAX_LEAD_TIME_DAYS",  "supplier.lead_time_days",            "HARD",  "15",
         "supplier.lead_time_days must be <= (revised_eta - reference_date).days."),
        ("C2", "Minimum Order Quantity",      "MIN_ORDER_QTY",       "supplier.moq",                        "HARD",  "800",
         "supplier.moq must be <= required_shortage_qty. Fails if moq > required_qty."),
        ("C3", "PPAP Certification",          "REQUIRED_CERT",       "supplier_certifications.cert_type",   "HARD",  "PPAP",
         "Supplier must hold valid PPAP for material/product family."),
        ("C4", "Frozen Schedule Window",      "FROZEN_WINDOW_DAYS",  "work_orders.frozen_schedule",         "HARD",  "14",
         "WO with frozen_schedule=1 cannot be rescheduled. Applies to RESCHEDULE options only."),
        ("C5", "Budget Authorization",        "MAX_PLANT_BUDGET",    "recovery_option.total_cost",          "ROUTE", "30000",
         "Cost > $30,000 routes to VP Supply Chain approval. NOT a veto."),
        ("C6", "BOM Revision Compatibility",  "REQUIRED_REVISION",   "materials.compatible_revisions",      "HARD",  "REV-D",
         "Material compatible_revisions must include REV-D. Fails for STCOIL-415V (REV-C only)."),
        ("C7", "Export Compliance",           "EXPORT_ALLOWED",      "suppliers.export_restricted",         "HARD",  "0",
         "Supplier export_restricted must be 0. 1 = blocked."),
        ("C8", "Supplier Capacity",           "MIN_CAPACITY",        "suppliers.capacity_per_month",        "HARD",  "800",
         "capacity_per_month must be >= required_shortage_qty."),
    ]
    c.executemany("INSERT OR REPLACE INTO rules VALUES (?,?,?,?,?,?,?)", rows)


def _seed_recovery_templates(c):
    # target_wo_id: for RESCHEDULE options, which WO is targeted
    # freight_mode: AIR / SEA / NULL
    # Engine reads freight_mode to select freight_cost_air or freight_cost_sea from suppliers table
    rows = [
        # OPT-A: EuroCoils GmbH spot buy via AIR
        # C1: 7<=15 PASS | C2: 200<=800 PASS | C3: PPAP exists PASS
        # C4: NA (SPOT_BUY) | C5: $30,100>$30,000 -> APPROVAL_REQUIRED
        # C6: STCOIL-440V is REV-D compat PASS | C7: not restricted PASS | C8: 1000>=800 PASS
        ("OPT-A", "SPOT_BUY",   "SUPP-003", "STCOIL-440V", None,      "AIR",
         "Spot buy 800 units STCOIL-440V from EuroCoils GmbH via air freight",
         "Lead 7d, arrives ~2026-10-10. Air cost $30,100. MOQ 200 (order 4xMOQ=800). PPAP ok. REV-D compat."),

        # OPT-B: Hanoi Coils expedite via SEA
        # C1: 18>15 -> VETO (hard stop, remaining rules not evaluated)
        ("OPT-B", "EXPEDITE",   "SUPP-002", "STCOIL-440V", None,      "SEA",
         "Expedite order from Hanoi Coils Ltd (sea freight only)",
         "Lead 18d -> arrival 2026-10-21 > revised_eta 2026-10-18. Sea freight only. C1 VETO."),

        # OPT-C: FastCoil Asia substitute STCOIL-415V via AIR
        # C3: no PPAP -> VETO | C6: REV-C only, REV-D missing -> VETO
        ("OPT-C", "SUBSTITUTE", "SUPP-004", "STCOIL-415V", None,      "AIR",
         "Substitute STCOIL-415V from FastCoil Asia Pte via air freight",
         "Lead 10d. No PPAP for STCOIL family (C3 FAIL). REV-C only, REV-D missing (C6 FAIL)."),

        # OPT-D: Reschedule WO-7782 (target_wo_id = WO-7782)
        # C4: WO-7782 frozen_schedule=1, start 2026-10-14 < frozen_through 2026-10-17 -> VETO
        ("OPT-D", "RESCHEDULE", None,        None,          "WO-7782", None,
         "Reschedule WO-7782 to start after revised ETA 2026-10-18",
         "WO-7782 start_date 2026-10-14 is within 14-day frozen window (through 2026-10-17). C4 VETO."),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO recovery_option_templates VALUES (?,?,?,?,?,?,?,?)",
        rows
    )


# =====================================================================
# SEED ENTRY POINT
# =====================================================================

def seed(db_path: str = str(DB_PATH)) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    c = conn.cursor()
    _seed_materials(c)
    _seed_suppliers(c)
    _seed_certifications(c)
    _seed_bom(c)
    _seed_inventory(c)
    _seed_customer_orders(c)
    _seed_work_orders(c)
    _seed_wo_materials(c)
    _seed_purchase_orders(c)
    _seed_shipments(c)
    _seed_rules(c)
    _seed_recovery_templates(c)
    conn.commit()
    conn.close()


# =====================================================================
# CHECKPOINT 1 -- Manual verification before engine is built
# =====================================================================

def _verify(db_path: str = str(DB_PATH)) -> None:
    from datetime import date
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    ref = date.fromisoformat(REFERENCE_DATE)

    print("\n" + "=" * 62)
    print("  CHECKPOINT 1 -- Seed Data Verification")
    print(f"  Reference date: {REFERENCE_DATE}")
    print("=" * 62)

    # -- Scenario A: TTS / TTR / Shortage arithmetic --
    inv = c.execute("SELECT on_hand_qty, daily_consumption FROM inventory WHERE material_id='STCOIL-440V'").fetchone()
    tts = int(inv["on_hand_qty"] / inv["daily_consumption"])
    stockout = date.fromordinal(ref.toordinal() + tts)
    po  = c.execute("SELECT original_eta, revised_eta FROM purchase_orders WHERE po_id='PO-2026-441'").fetchone()
    orig_eta = date.fromisoformat(po["original_eta"])
    rev_eta  = date.fromisoformat(po["revised_eta"])
    delay    = (rev_eta - orig_eta).days
    ttr      = (rev_eta - ref).days
    shortage = ttr - tts

    print(f"\n  [A] SHORTAGE PATH")
    print(f"      on_hand={inv['on_hand_qty']}  consumption={inv['daily_consumption']}/day")
    print(f"      TTS    = {int(inv['on_hand_qty'])} / {int(inv['daily_consumption'])} = {tts} days")
    print(f"      Stockout date  = {stockout}  (expected 2026-10-08)")
    print(f"      Original ETA   = {po['original_eta']}")
    print(f"      Delay          = +{delay} days")
    print(f"      Revised ETA    = {po['revised_eta']}  (expected 2026-10-18)")
    print(f"      TTR            = ({po['revised_eta']} - {REFERENCE_DATE}).days = {ttr}")
    print(f"      Shortage wdw   = {stockout} to {po['revised_eta']} ({shortage} days, INCLUSIVE end)")
    assert tts == 5,                         f"TTS should be 5, got {tts}"
    assert str(stockout) == "2026-10-08",    f"Stockout should be 2026-10-08, got {stockout}"
    assert ttr == 15,                        f"TTR should be 15, got {ttr}"
    assert shortage == 10,                   f"Shortage should be 10 days, got {shortage}"
    assert delay == 7,                       f"Delay should be +7 days, got {delay}"
    print("      [PASS] ALL ASSERTIONS PASS")

    # -- Exposure model: WO-7782 pegged to SO-55102 -> 500 * 240 = 120,000 --
    wo = c.execute("SELECT co_id, start_date FROM work_orders WHERE wo_id='WO-7782'").fetchone()
    assert wo["co_id"] == "SO-55102", f"WO-7782 should be pegged to SO-55102, got {wo['co_id']}"
    start = date.fromisoformat(wo["start_date"])
    assert str(start) == "2026-10-14", f"WO-7782 start should be 2026-10-14, got {start}"
    assert date.fromisoformat("2026-10-08") <= start <= date.fromisoformat("2026-10-18"), \
        f"WO-7782 start {start} should be in shortage window"
    co = c.execute("SELECT qty, unit_price FROM customer_orders WHERE co_id='SO-55102'").fetchone()
    exposure = co["qty"] * co["unit_price"]
    print(f"\n      Exposure: {co['qty']} * ${co['unit_price']} = ${exposure:,.0f}  (expected $120,000)")
    assert exposure == 120000.0, f"Exposure should be 120000.0, got {exposure}"
    print("      [PASS] EXPOSURE ASSERTION")

    # -- WO-7781: no peg -> $0 --
    wo1 = c.execute("SELECT co_id FROM work_orders WHERE wo_id='WO-7781'").fetchone()
    assert wo1["co_id"] is None, f"WO-7781 should have no peg, got {wo1['co_id']}"
    print("      [PASS] WO-7781 has no peg -> $0 exposure")

    # -- WO-7783: starts after shortage window -> $0 --
    wo3 = c.execute("SELECT start_date FROM work_orders WHERE wo_id='WO-7783'").fetchone()
    start3 = date.fromisoformat(wo3["start_date"])
    assert start3 > date.fromisoformat("2026-10-18"), \
        f"WO-7783 start {start3} should be after shortage end 2026-10-18"
    print(f"      [PASS] WO-7783 start={start3} is after window -> $0 exposure")

    # -- Scenario B --
    inv_b = c.execute("SELECT on_hand_qty, daily_consumption FROM inventory WHERE material_id='BEARING-6205'").fetchone()
    tts_b = int(inv_b["on_hand_qty"] / inv_b["daily_consumption"])
    po_b  = c.execute("SELECT revised_eta FROM purchase_orders WHERE po_id='PO-2026-512'").fetchone()
    ttr_b = (date.fromisoformat(po_b["revised_eta"]) - ref).days
    print(f"\n  [B] ABSORBED PATH")
    print(f"      TTS={tts_b} days  TTR={ttr_b} days")
    assert tts_b >= ttr_b, f"Scenario B: TTS({tts_b}) should be >= TTR({ttr_b})"
    print("      [PASS]")

    # -- False Positive --
    match = c.execute(
        "SELECT shipment_id FROM shipments WHERE vessel_name=? OR port_of_loading=?",
        ("MV Pacific Trader", "Manila")
    ).fetchone()
    print(f"\n  [C] FALSE-POSITIVE PATH")
    assert match is None, f"False-positive should have no match, got {match}"
    print("      [PASS] No match -> IGNORED")

    # -- Rulebook --
    rules = c.execute("SELECT rule_id, severity FROM rules ORDER BY rule_id").fetchall()
    print(f"\n  [D] RULEBOOK ({len(rules)} rules -- no verdicts)")
    for r in rules:
        print(f"      {r['rule_id']}  severity={r['severity']}")
    assert len(rules) == 8, f"Expected 8 rules, got {len(rules)}"
    print("      [PASS] C1-C8 present")

    # -- Recovery templates --
    opts = c.execute("SELECT template_id, option_type, freight_mode, target_wo_id FROM recovery_option_templates").fetchall()
    print(f"\n  [E] RECOVERY TEMPLATES ({len(opts)} options)")
    for o in opts:
        print(f"      {o['template_id']}  type={o['option_type']}  freight={o['freight_mode']}  target_wo={o['target_wo_id']}")
    assert len(opts) == 4
    print("      [PASS]")

    conn.close()
    print("\n" + "=" * 62)
    print("  CHECKPOINT 1 PASSED -- Proceed to Part 3 (schemas) and Part 2 (engine).")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"[RESET] {DB_PATH}")
    seed()
    print(f"[OK]    Seeded: {DB_PATH}")
    _verify()
