"""
Sentinel-SC: Mock ERP Database Initializer
===========================================
Run:  python erp/init_db.py

Reference Date  : 2026-10-03
─────────────────────────────────────────────────────────────────
Scenario A — SHORTAGE (hero path)
  Material      : STCOIL-440V (Stator Coil 440V)
  On-hand       : 400 units
  Consumption   : 80 units/day
  TTS           : 400 / 80 = 5 days
  Stockout date : 2026-10-03 + 5  = 2026-10-08
  Original ETA  : 2026-10-11
  Disruption    : +7 days (Port Klang restriction)
  Revised ETA   : 2026-10-11 + 7  = 2026-10-18
  TTR           : (2026-10-18 - 2026-10-03).days = 15 days
  Shortage wdw  : 2026-10-08 -> 2026-10-18 (10 days)
  OTIF exposure : ~$120,000

Scenario B — ABSORBED (no-action path)
  Material      : BEARING-6205 (Deep Groove Ball Bearing)
  On-hand       : 2000 units
  Consumption   : 100 units/day
  TTS           : 2000 / 100 = 20 days  (stock lasts to 2026-10-23)
  Same TTR      : 15 days
  Result        : TTS(20) >= TTR(15) -> ABSORBED, recovery_required = false

False Positive  : Unrelated news signal -> no shipment correlation -> IGNORED
─────────────────────────────────────────────────────────────────
Architecture rule: DB stores facts only. No derived verdicts.
Constraint verdicts emerge at runtime from the rule evaluator.
"""

import sqlite3
import json
import os
from pathlib import Path

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────
DB_PATH        = Path(__file__).parent / "mock_erp.db"
REFERENCE_DATE = "2026-10-03"

# ─────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────
SCHEMA = """
PRAGMA foreign_keys = ON;

-- ================================================================
-- MATERIALS
-- compatible_revisions : JSON array of BOM revisions this material
--                        is certified to work with  e.g. '["REV-D"]'
-- ================================================================
CREATE TABLE IF NOT EXISTS materials (
    material_id           TEXT    PRIMARY KEY,
    name                  TEXT    NOT NULL,
    material_type         TEXT    NOT NULL,  -- RAW / COMPONENT / SUBASSEMBLY / FINISHED_GOOD
    unit                  TEXT    NOT NULL,
    compatible_revisions  TEXT    NOT NULL DEFAULT '[]',  -- JSON array
    notes                 TEXT
);

-- ================================================================
-- SUPPLIERS
-- export_restricted : 0 = allowed, 1 = blocked (C7 check source)
-- freight_cost_air  : USD for a full PO shipment via air
-- freight_cost_sea  : USD for a full PO shipment via sea
-- Recovery calculator reads these — never invents prices.
-- ================================================================
CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id          TEXT    PRIMARY KEY,
    name                 TEXT    NOT NULL,
    country              TEXT    NOT NULL,
    lead_time_days       INTEGER NOT NULL,
    moq                  INTEGER NOT NULL,    -- Minimum Order Quantity (C2 source)
    capacity_per_month   INTEGER NOT NULL,    -- C8 source
    freight_cost_air     REAL    NOT NULL DEFAULT 0.0,
    freight_cost_sea     REAL    NOT NULL DEFAULT 0.0,
    export_restricted    INTEGER NOT NULL DEFAULT 0,  -- C7 source (0/1)
    tier                 INTEGER NOT NULL DEFAULT 1,
    approved             INTEGER NOT NULL DEFAULT 1   -- 0 = unapproved
);

-- ================================================================
-- SUPPLIER CERTIFICATIONS
-- Separate table so C3 is queryable: cert_type = 'PPAP'
-- Scoped to material_id or product_family (or both NULL = general)
-- ================================================================
CREATE TABLE IF NOT EXISTS supplier_certifications (
    cert_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id     TEXT    NOT NULL REFERENCES suppliers(supplier_id),
    material_id     TEXT    REFERENCES materials(material_id),  -- NULL = general
    cert_type       TEXT    NOT NULL,   -- PPAP / ISO9001 / IATF16949 / RoHS
    valid_until     TEXT    NOT NULL,   -- ISO-8601 date
    product_family  TEXT                -- e.g. STATOR_COILS
);

-- ================================================================
-- BOM (Bill of Materials)
-- revision : active BOM revision that this row belongs to (C6 source)
-- A child material's compatible_revisions must include this revision.
-- ================================================================
CREATE TABLE IF NOT EXISTS bom (
    bom_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_material_id TEXT    NOT NULL REFERENCES materials(material_id),
    child_material_id  TEXT    NOT NULL REFERENCES materials(material_id),
    qty_per_unit       REAL    NOT NULL,
    revision           TEXT    NOT NULL DEFAULT 'REV-A'
);

-- ================================================================
-- INVENTORY
-- tts_days is NOT stored — it is computed at runtime:
--     tts_days = on_hand_qty / daily_consumption
-- ================================================================
CREATE TABLE IF NOT EXISTS inventory (
    inventory_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id       TEXT    NOT NULL REFERENCES materials(material_id),
    on_hand_qty       REAL    NOT NULL,
    daily_consumption REAL    NOT NULL,
    safety_stock      REAL    NOT NULL DEFAULT 0.0,
    warehouse         TEXT    NOT NULL DEFAULT 'MAIN'
);

-- ================================================================
-- PURCHASE ORDERS
-- revised_eta may be NULL (no disruption) or updated after a signal
-- ================================================================
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id        TEXT PRIMARY KEY,
    supplier_id  TEXT NOT NULL REFERENCES suppliers(supplier_id),
    material_id  TEXT NOT NULL REFERENCES materials(material_id),
    qty          REAL NOT NULL,
    unit_cost    REAL NOT NULL,
    po_date      TEXT NOT NULL,
    original_eta TEXT NOT NULL,
    revised_eta  TEXT,                        -- NULL = not delayed
    status       TEXT NOT NULL DEFAULT 'IN_TRANSIT'
    -- IN_TRANSIT / DELAYED / DELIVERED / CANCELLED
);

-- ================================================================
-- SHIPMENTS
-- Linked 1-to-1 with a PO for this demo scope.
-- The signal agent correlates port/vessel -> shipment_id here.
-- ================================================================
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
    -- IN_TRANSIT / DELAYED / ARRIVED / DIVERTED
);

-- ================================================================
-- CUSTOMER ORDERS
-- penalty_per_day : contractual liquidated damages per late day (USD)
-- ================================================================
CREATE TABLE IF NOT EXISTS customer_orders (
    co_id           TEXT PRIMARY KEY,
    product_id      TEXT NOT NULL REFERENCES materials(material_id),
    customer_name   TEXT NOT NULL,
    qty             REAL NOT NULL,
    unit_price      REAL NOT NULL,
    due_date        TEXT NOT NULL,
    penalty_per_day REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'CONFIRMED'
);

-- ================================================================
-- WORK ORDERS
-- frozen_schedule : 1 = within 14-day frozen window (C4 source)
-- ================================================================
CREATE TABLE IF NOT EXISTS work_orders (
    wo_id           TEXT PRIMARY KEY,
    co_id           TEXT REFERENCES customer_orders(co_id),
    product_id      TEXT NOT NULL REFERENCES materials(material_id),
    qty             REAL NOT NULL,
    start_date      TEXT NOT NULL,
    due_date        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'SCHEDULED',
    frozen_schedule INTEGER NOT NULL DEFAULT 0  -- 0 = not frozen, 1 = frozen (C4)
);

-- ================================================================
-- WORK ORDER MATERIAL REQUIREMENTS  (Pegging table)
-- Enables direct query: which WOs need STCOIL-440V and how much?
-- BOM traversal validates and augments these rows.
-- ================================================================
CREATE TABLE IF NOT EXISTS wo_materials (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    wo_id        TEXT    NOT NULL REFERENCES work_orders(wo_id),
    material_id  TEXT    NOT NULL REFERENCES materials(material_id),
    required_qty REAL    NOT NULL
);

-- ================================================================
-- CONSTRAINT RULEBOOK  (C1–C8)
-- Stores thresholds and rule metadata ONLY.
-- No verdicts, no option-specific data.
-- The generic rule evaluator reads this table at runtime.
-- threshold_value is stored as TEXT; the evaluator casts per rule_type.
-- ================================================================
CREATE TABLE IF NOT EXISTS rules (
    rule_id          TEXT PRIMARY KEY,
    rule_name        TEXT NOT NULL,
    rule_type        TEXT NOT NULL,
    parameter        TEXT NOT NULL,         -- DB column / field path the evaluator checks
    severity         TEXT NOT NULL,         -- HARD / ROUTE / SOFT
    threshold_value  TEXT NOT NULL,         -- Numeric string or string literal
    description      TEXT
);

-- ================================================================
-- RECOVERY OPTION TEMPLATES
-- Enumerated from DB facts; LLM never invents these.
-- option_type  : SPOT_BUY / EXPEDITE / RESCHEDULE / SUBSTITUTE
-- The constraint engine evaluates each template against C1–C8.
-- ================================================================
CREATE TABLE IF NOT EXISTS recovery_option_templates (
    template_id  TEXT PRIMARY KEY,
    option_type  TEXT NOT NULL,             -- SPOT_BUY / EXPEDITE / RESCHEDULE / SUBSTITUTE
    supplier_id  TEXT REFERENCES suppliers(supplier_id),
    material_id  TEXT REFERENCES materials(material_id),
    description  TEXT NOT NULL,
    notes        TEXT
);
"""


# ─────────────────────────────────────────────────────────────────
# SEED DATA
# ─────────────────────────────────────────────────────────────────

def _seed_materials(c: sqlite3.Cursor) -> None:
    rows = [
        # ── Finished Good ──────────────────────────────────────────────
        ("APEXM-100",     "ApexX-100 Motor Drive Unit",
         "FINISHED_GOOD", "UNIT", '["REV-D"]',
         "Hero finished good. BOM revision REV-D active on production line."),

        # ── Sub-assemblies ─────────────────────────────────────────────
        ("MOTOR-ASSY-01", "Motor Assembly",
         "SUBASSEMBLY",   "UNIT", '["REV-D"]',
         "Contains STCOIL-440V (×2) and BEARING-6205 (×2)."),

        ("PCB-CTRL-01",   "Control PCB Assembly",
         "SUBASSEMBLY",   "UNIT", '["REV-C","REV-D"]',
         "Dual-revision compatible. Unrelated to STCOIL disruption."),

        ("HOUSING-01",    "Drive Housing Unit",
         "COMPONENT",     "UNIT", '["REV-D"]',
         "Mechanical enclosure. Not disrupted."),

        # ── Hero raw material (Scenario A) ────────────────────────────
        ("STCOIL-440V",   "Stator Coil 440V (Primary)",
         "RAW",           "UNIT", '["REV-D"]',
         "DISRUPTED: shipment SHIP-440V-01 delayed +7d at Port Klang. "
         "On-hand 400, consumption 80/day -> TTS = 5 days -> stockout 2026-10-08."),

        # ── Substitute (C3 + C6 will FAIL from data) ─────────────────
        ("STCOIL-415V",   "Stator Coil 415V (Substitute Candidate)",
         "RAW",           "UNIT", '["REV-C"]',
         "Voltage variant. compatible_revisions = [REV-C] only — "
         "INCOMPATIBLE with active BOM REV-D (C6 FAIL). "
         "No PPAP certification on record (C3 FAIL)."),

        # ── Scenario B material ───────────────────────────────────────
        ("BEARING-6205",  "Deep Groove Ball Bearing 6205",
         "RAW",           "UNIT", '["REV-A"]',
         "Scenario B hero: on-hand 2000, consumption 100/day -> TTS = 20 days. "
         "Same 7-day delay applies (TTR = 15 days). TTS >= TTR -> ABSORBED."),

        # ── Noise materials (must NOT appear in STCOIL-440V BOM impact) ─
        ("COPPER-WIRE-2", "Copper Wire 2mm Spool",
         "RAW",           "SPOOL", '["REV-A","REV-B"]',
         "BOM noise. BOM traversal must NOT route this into STCOIL-440V exposure."),

        ("CAPACITOR-100", "Electrolytic Capacitor 100µF",
         "RAW",           "UNIT", '["REV-A"]',
         "PCB component. BOM traversal must NOT route this into STCOIL-440V exposure."),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO materials VALUES (?,?,?,?,?,?)",
        rows
    )


def _seed_suppliers(c: sqlite3.Cursor) -> None:
    # Columns: supplier_id, name, country, lead_time_days, moq,
    #          capacity_per_month, freight_cost_air, freight_cost_sea,
    #          export_restricted, tier, approved
    #
    # C1 LOGIC (evaluated at runtime):
    #   available_days = (revised_eta - reference_date).days = 15
    #   PASS iff supplier.lead_time_days <= available_days
    #
    # SUPP-001 (ShenZen): lead 12 <= 15 -> C1 PASS  (but shipment already delayed)
    # SUPP-002 (Hanoi)  : lead 18 > 15 -> C1 FAIL  -> VETO
    # SUPP-003 (Euro)   : lead  7 <= 15 -> C1 PASS
    # SUPP-004 (FastCoil): lead 10 <= 15 -> C1 PASS  (but C3+C6 fail for 415V)
    # SUPP-005 (Omega)  : Scenario B supplier only
    rows = [
        ("SUPP-001", "ShenZen Coil Corp",   "CN", 12, 500, 2000, 45000.0, 8500.0,  0, 1, 1),
        ("SUPP-002", "Hanoi Coils Ltd",     "VN", 18, 300, 1500, 28000.0, 7200.0,  0, 2, 1),
        ("SUPP-003", "EuroCoils GmbH",      "DE",  7, 200, 1000, 30100.0, 22000.0, 0, 1, 1),
        ("SUPP-004", "FastCoil Asia Pte",   "SG", 10, 400,  800, 25500.0,  9800.0, 0, 2, 1),
        ("SUPP-005", "OmegaBearings GmbH",  "DE",  5, 100, 5000,  3200.0,  1100.0, 0, 1, 1),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO suppliers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows
    )


def _seed_certifications(c: sqlite3.Cursor) -> None:
    # cert_id is AUTOINCREMENT — pass None
    # C3 check: supplier must have cert_type = 'PPAP' for STCOIL-440V
    # SUPP-001 (ShenZen) -> PPAP present  (primary, disrupted — not a recovery option)
    # SUPP-002 (Hanoi)   -> NO PPAP       (already C1 FAIL; C3 would also fail)
    # SUPP-003 (Euro)    -> PPAP present  -> C3 PASS for OPT-A
    # SUPP-004 (FastCoil)-> NO PPAP for STCOIL-415V -> C3 FAIL for OPT-C
    rows = [
        (None, "SUPP-001", "STCOIL-440V", "PPAP",      "2027-12-31", "STATOR_COILS"),
        (None, "SUPP-001",  None,          "ISO9001",   "2027-06-30",  None),
        (None, "SUPP-001",  None,          "IATF16949", "2027-06-30",  None),
        (None, "SUPP-003", "STCOIL-440V", "PPAP",      "2027-12-31", "STATOR_COILS"),  # C3 PASS
        (None, "SUPP-003",  None,          "IATF16949", "2027-09-30",  None),
        (None, "SUPP-003",  None,          "ISO9001",   "2027-09-30",  None),
        (None, "SUPP-005",  None,          "ISO9001",   "2027-03-31",  None),
        # SUPP-002 and SUPP-004 intentionally have no PPAP for STCOIL family
    ]
    c.executemany(
        "INSERT OR REPLACE INTO supplier_certifications VALUES (?,?,?,?,?,?)",
        rows
    )


def _seed_bom(c: sqlite3.Cursor) -> None:
    # revision = active BOM revision this row belongs to
    # C6 check: child material's compatible_revisions must contain this revision
    # STCOIL-440V: compatible_revisions = ["REV-D"] -> C6 PASS for REV-D BOM rows
    # STCOIL-415V: compatible_revisions = ["REV-C"] -> C6 FAIL when checked against REV-D
    #
    # Noise paths (CAPACITOR-100, COPPER-WIRE-2) are under different BOM branches.
    # BOM traversal from STCOIL-440V upward must NOT cross into PCB-CTRL-01 branch.
    rows = [
        # parent             child             qty/unit  revision
        ("APEXM-100",     "MOTOR-ASSY-01",   1.0,      "REV-D"),
        ("APEXM-100",     "PCB-CTRL-01",     1.0,      "REV-D"),
        ("APEXM-100",     "HOUSING-01",      1.0,      "REV-D"),
        ("MOTOR-ASSY-01", "STCOIL-440V",     2.0,      "REV-D"),  # 2 coils per motor <- hero
        ("MOTOR-ASSY-01", "BEARING-6205",    2.0,      "REV-A"),  # Scenario B material
        ("MOTOR-ASSY-01", "COPPER-WIRE-2",   0.5,      "REV-A"),  # Noise — unrelated branch
        ("PCB-CTRL-01",   "CAPACITOR-100",   4.0,      "REV-D"),  # Noise — unrelated branch
    ]
    c.executemany(
        "INSERT OR REPLACE INTO bom "
        "(parent_material_id, child_material_id, qty_per_unit, revision) "
        "VALUES (?,?,?,?)",
        rows
    )


def _seed_inventory(c: sqlite3.Cursor) -> None:
    # tts_days computed at runtime: on_hand_qty / daily_consumption
    # STCOIL-440V : 400 / 80  = 5  days  -> stockout 2026-10-08
    # BEARING-6205: 2000 / 100 = 20 days -> stockout 2026-10-23 (buffer absorbs delay)
    rows = [
        # material_id     on_hand  consumption  safety_stock  warehouse
        ("STCOIL-440V",   400.0,   80.0,         80.0,        "MAIN"),
        ("STCOIL-415V",     0.0,    0.0,          0.0,        "MAIN"),
        ("BEARING-6205", 2000.0,  100.0,        200.0,        "MAIN"),
        ("PCB-CTRL-01",   120.0,   10.0,         20.0,        "MAIN"),
        ("HOUSING-01",    200.0,   15.0,         30.0,        "MAIN"),
        ("CAPACITOR-100",10000.0, 500.0,       1000.0,        "MAIN"),
        ("COPPER-WIRE-2",   50.0,   5.0,         10.0,        "MAIN"),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO inventory "
        "(material_id, on_hand_qty, daily_consumption, safety_stock, warehouse) "
        "VALUES (?,?,?,?,?)",
        rows
    )


def _seed_customer_orders(c: sqlite3.Cursor) -> None:
    # penalty_per_day: contractual liquidated damages per late day (USD)
    # Total daily exposure = 6000 + 4000 + 2000 = $12,000/day
    # Shortage window = 10 days -> maximum OTIF exposure ≈ $120,000
    # Exact figure computed by impact_math.py from due_date overlap with shortage window.
    rows = [
        # co_id          product      customer                   qty   price    due_date      penalty/day  status
        ("CO-2026-001", "APEXM-100", "Pinnacle Industrial AG",   50,  3200.0, "2026-10-20",  6000.0, "CONFIRMED"),
        ("CO-2026-002", "APEXM-100", "Meridian Automation Inc",  40,  3200.0, "2026-10-22",  4000.0, "CONFIRMED"),
        ("CO-2026-003", "APEXM-100", "Atlas Drive Systems",      30,  3200.0, "2026-10-25",  2000.0, "CONFIRMED"),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO customer_orders VALUES (?,?,?,?,?,?,?,?)",
        rows
    )


def _seed_work_orders(c: sqlite3.Cursor) -> None:
    # frozen_schedule = 1 when start_date is within 14 days of reference_date 2026-10-03
    # WO-7781: start 2026-10-06 -> 3 days away -> FROZEN
    # WO-7782: start 2026-10-05 -> 2 days away -> FROZEN
    # WO-7783: start 2026-10-12 -> 9 days away -> FROZEN (within 14-day window)
    # All three are within the 14-day window; rescheduling any of them triggers C4 VETO.
    rows = [
        # wo_id      co_id          product      qty  start        due          status       frozen
        ("WO-7781", "CO-2026-001", "APEXM-100",  50, "2026-10-06","2026-10-20","SCHEDULED",  1),
        ("WO-7782", "CO-2026-002", "APEXM-100",  40, "2026-10-05","2026-10-22","SCHEDULED",  1),
        ("WO-7783", "CO-2026-003", "APEXM-100",  30, "2026-10-12","2026-10-25","SCHEDULED",  1),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO work_orders VALUES (?,?,?,?,?,?,?,?)",
        rows
    )


def _seed_wo_materials(c: sqlite3.Cursor) -> None:
    # Pegging table — 2 STCOIL-440V per APEXM-100 unit (from BOM)
    # BOM traversal uses bom table; this table enables direct pegging queries.
    # Noise materials (CAPACITOR-100, COPPER-WIRE-2) included per BOM but must NOT
    # appear in impact assessment for STCOIL-440V — traversal scope is strictly
    # STCOIL-440V upward through MOTOR-ASSY-01 -> APEXM-100 only.
    rows = [
        # WO-7781 (50 units APEXM-100)
        ("WO-7781", "STCOIL-440V",   100.0),   # 50 × 2
        ("WO-7781", "BEARING-6205",  100.0),   # 50 × 2
        ("WO-7781", "COPPER-WIRE-2",  25.0),   # 50 × 0.5
        ("WO-7781", "CAPACITOR-100", 200.0),   # 50 × 4
        # WO-7782 (40 units APEXM-100)
        ("WO-7782", "STCOIL-440V",    80.0),   # 40 × 2
        ("WO-7782", "BEARING-6205",   80.0),
        ("WO-7782", "COPPER-WIRE-2",  20.0),
        ("WO-7782", "CAPACITOR-100", 160.0),
        # WO-7783 (30 units APEXM-100)
        ("WO-7783", "STCOIL-440V",    60.0),   # 30 × 2
        ("WO-7783", "BEARING-6205",   60.0),
        ("WO-7783", "COPPER-WIRE-2",  15.0),
        ("WO-7783", "CAPACITOR-100", 120.0),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO wo_materials (wo_id, material_id, required_qty) VALUES (?,?,?)",
        rows
    )


def _seed_purchase_orders(c: sqlite3.Cursor) -> None:
    # Scenario A: PO-2026-441 — STCOIL-440V from ShenZen, DELAYED at Port Klang
    #   Original ETA: 2026-10-11
    #   Delay:        +7 days
    #   Revised ETA:  2026-10-18
    #   TTR = (2026-10-18 - 2026-10-03).days = 15 days  <- computed at runtime
    #
    # Scenario B: PO-2026-512 — BEARING-6205 from OmegaBearings, same delay
    #   Revised ETA: 2026-10-18 -> TTR = 15 days, TTS = 20 days -> ABSORBED
    #
    # PO-2026-389: COPPER-WIRE-2 already delivered -> background noise, no impact
    rows = [
        # po_id         supplier     material      qty   unit_cost  po_date        orig_eta       rev_eta        status
        ("PO-2026-441","SUPP-001","STCOIL-440V",  800,  42.50, "2026-09-20","2026-10-11","2026-10-18","DELAYED"),
        ("PO-2026-512","SUPP-005","BEARING-6205", 1000,  8.90, "2026-09-25","2026-10-11","2026-10-18","DELAYED"),
        ("PO-2026-389","SUPP-001","COPPER-WIRE-2", 100, 15.00, "2026-09-15","2026-10-05",      None,  "DELIVERED"),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?)",
        rows
    )


def _seed_shipments(c: sqlite3.Cursor) -> None:
    # SHIP-440V-01: Signal agent correlates "Port Klang" + "MV Sentinel Star" -> this row
    # SHIP-6205-01: Scenario B shipment — same delay, different material (BEARING-6205)
    # False-positive signal references "Manila airport" / "MV Pacific Trader" ->
    #   no match in this table -> IGNORED (no correlation found)
    rows = [
        # shipment_id     po_id           vessel             loading      discharge         orig_eta       rev_eta       delay  status
        ("SHIP-440V-01","PO-2026-441","MV Sentinel Star", "Port Klang","Plant Main Port","2026-10-11","2026-10-18", 7,"DELAYED"),
        ("SHIP-6205-01","PO-2026-512","MV Nordic Blue",   "Hamburg",   "Plant Main Port","2026-10-11","2026-10-18", 7,"DELAYED"),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO shipments VALUES (?,?,?,?,?,?,?,?,?)",
        rows
    )


def _seed_rules(c: sqlite3.Cursor) -> None:
    # ════════════════════════════════════════════════════════════════
    # CONSTRAINT RULEBOOK  C1–C8
    # ────────────────────────────────────────────────────────────────
    # ARCHITECTURE INVARIANT:
    #   - threshold_value stores a fact/limit — NOT a verdict.
    #   - The generic evaluator (constraint_engine.py) reads these rows
    #     and applies them against each candidate option's DB facts.
    #   - Changing a threshold_value or a supplier DB field changes the
    #     verdict WITHOUT touching any code.
    #
    # severity:
    #   HARD  -> VETO if condition not met
    #   ROUTE -> does not veto; routes option to approval workflow (C5)
    #   SOFT  -> advisory only (none in this spec)
    #
    # C1 threshold: "15" = (revised_eta - reference_date).days
    #   The evaluator recomputes this from the live shipment's revised_eta
    #   so a different delay scenario auto-updates the constraint.
    # ════════════════════════════════════════════════════════════════
    rows = [
        (
            "C1",
            "Lead Time Feasibility",
            "MAX_LEAD_TIME_DAYS",
            "supplier.lead_time_days",
            "HARD",
            "15",
            "Supplier lead_time_days must be <= (revised_eta - reference_date).days. "
            "Evaluator computes available_days dynamically from the disrupted shipment's revised_eta."
        ),
        (
            "C2",
            "Minimum Order Quantity",
            "MIN_ORDER_QTY",
            "supplier.moq",
            "HARD",
            "800",
            "Required shortage quantity (800 units) must be >= supplier MOQ. "
            "Fails if supplier.moq > required_qty (cannot place a valid order)."
        ),
        (
            "C3",
            "PPAP Certification",
            "REQUIRED_CERT",
            "supplier_certifications.cert_type",
            "HARD",
            "PPAP",
            "Supplier must hold a valid PPAP certificate scoped to this material "
            "or product family in the supplier_certifications table."
        ),
        (
            "C4",
            "Frozen Schedule Window",
            "FROZEN_WINDOW_DAYS",
            "work_orders.frozen_schedule",
            "HARD",
            "14",
            "Work orders with start_date within 14 days of reference_date cannot be "
            "rescheduled. Evaluator checks work_orders.frozen_schedule = 1."
        ),
        (
            "C5",
            "Budget Authorization Threshold",
            "MAX_PLANT_BUDGET",
            "recovery_option.total_cost",
            "ROUTE",
            "30000",
            "Options with total_cost > $30,000 require VP Supply Chain approval. "
            "This is a ROUTE rule — it does NOT veto the option; it triggers the human gate."
        ),
        (
            "C6",
            "BOM Revision Compatibility",
            "REQUIRED_REVISION",
            "materials.compatible_revisions",
            "HARD",
            "REV-D",
            "Substitute material's compatible_revisions JSON array must include 'REV-D'. "
            "Evaluator parses the JSON array and checks membership."
        ),
        (
            "C7",
            "Export Compliance",
            "EXPORT_ALLOWED",
            "suppliers.export_restricted",
            "HARD",
            "0",
            "Supplier must not be export-restricted. "
            "Fails if suppliers.export_restricted = 1."
        ),
        (
            "C8",
            "Supplier Capacity",
            "MIN_CAPACITY",
            "suppliers.capacity_per_month",
            "HARD",
            "800",
            "Supplier capacity_per_month must be >= required shortage quantity (800 units). "
            "Fails if supplier cannot physically fulfil the order volume."
        ),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO rules VALUES (?,?,?,?,?,?,?)",
        rows
    )


def _seed_recovery_templates(c: sqlite3.Cursor) -> None:
    # ════════════════════════════════════════════════════════════════
    # RECOVERY OPTION TEMPLATES
    # Enumerated from DB supplier + material facts — LLM does NOT
    # invent these. The recovery_agent enumerates templates and the
    # constraint_engine evaluates them against C1–C8.
    #
    # OPT-A: EuroCoils GmbH spot buy — air freight
    #   C1: lead_time 7 <= 15        -> PASS
    #   C2: moq 200 <= 800           -> PASS
    #   C3: PPAP cert on record     -> PASS
    #   C4: N/A (SPOT_BUY)          -> PASS
    #   C5: $30,100 > $30,000       -> APPROVAL_REQUIRED (ROUTE rule)
    #   C6: STCOIL-440V in REV-D    -> PASS
    #   C7: export_restricted = 0   -> PASS
    #   C8: capacity 1000 >= 800     -> PASS
    #   Final: APPROVAL_REQUIRED -> Human Gate
    #
    # OPT-B: Hanoi Coils Ltd expedite — sea freight only
    #   C1: lead_time 18 > 15       -> VETO  <- hard stop
    #   (C2–C8 not evaluated after C1 VETO)
    #
    # OPT-C: FastCoil Asia — substitute STCOIL-415V
    #   C1: lead_time 10 <= 15       -> PASS
    #   C2: moq 400 <= 800           -> PASS
    #   C3: NO PPAP for STCOIL-415V -> VETO
    #   C6: compatible_revisions=[REV-C], REV-D missing -> VETO
    #   (both C3 and C6 fail — evaluator records both)
    #
    # OPT-D: Reschedule WO-7782
    #   C4: frozen_schedule = 1     -> VETO
    # ════════════════════════════════════════════════════════════════
    rows = [
        (
            "OPT-A", "SPOT_BUY", "SUPP-003", "STCOIL-440V",
            "Spot buy 800 units STCOIL-440V from EuroCoils GmbH via air freight",
            "Lead time 7 days (arrival ~2026-10-10). "
            "Air freight cost $30,100 (SUPP-003.freight_cost_air). "
            "MOQ 200; order 4×MOQ = 800. "
            "PPAP certified. REV-D compatible. Not export-restricted. "
            "Cost exceeds plant budget $30,000 -> requires VP approval."
        ),
        (
            "OPT-B", "EXPEDITE", "SUPP-002", "STCOIL-440V",
            "Expedite order from Hanoi Coils Ltd (sea freight)",
            "Lead time 18 days -> projected arrival 2026-10-21. "
            "Exceeds available window of 15 days (revised_eta 2026-10-18). "
            "Sea freight only — no air option available from this supplier. "
            "C1 VETO: 18 > 15."
        ),
        (
            "OPT-C", "SUBSTITUTE", "SUPP-004", "STCOIL-415V",
            "Substitute STCOIL-415V from FastCoil Asia Pte",
            "Voltage variant (415V vs 440V). "
            "compatible_revisions = [REV-C] — incompatible with active BOM REV-D. "
            "No PPAP certificate for STCOIL product family. "
            "C3 VETO: missing PPAP. C6 VETO: REV-D not in compatible_revisions."
        ),
        (
            "OPT-D", "RESCHEDULE", None, None,
            "Reschedule WO-7782 to start after revised ETA 2026-10-18",
            "WO-7782 start_date 2026-10-05 — within 14-day frozen schedule window. "
            "frozen_schedule = 1. Rescheduling blocked by production planning policy. "
            "C4 VETO: frozen_schedule = 1."
        ),
    ]
    c.executemany(
        "INSERT OR REPLACE INTO recovery_option_templates VALUES (?,?,?,?,?,?)",
        rows
    )


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def seed(db_path: str = str(DB_PATH)) -> None:
    """Create and seed the mock ERP SQLite database."""
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


def _verify(db_path: str = str(DB_PATH)) -> None:
    """
    Checkpoint 1: Manual verification of seeded data.
    Confirms the three DoD paths are structurally sound before the engine is built.
    Run independently or after seed().
    """
    from datetime import date

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    ref = date.fromisoformat(REFERENCE_DATE)
    print("\n" + "=" * 62)
    print("  CHECKPOINT 1 — Seed Data Verification")
    print(f"  Reference date: {REFERENCE_DATE}")
    print("=" * 62)

    # ── Scenario A ────────────────────────────────────────────────
    row = c.execute(
        "SELECT on_hand_qty, daily_consumption FROM inventory WHERE material_id = 'STCOIL-440V'"
    ).fetchone()
    tts = int(row["on_hand_qty"] / row["daily_consumption"])
    stockout = ref.toordinal() + tts
    stockout_date = date.fromordinal(ref.toordinal() + tts)

    po = c.execute(
        "SELECT original_eta, revised_eta FROM purchase_orders WHERE po_id = 'PO-2026-441'"
    ).fetchone()
    orig_eta  = date.fromisoformat(po["original_eta"])
    rev_eta   = date.fromisoformat(po["revised_eta"])
    delay     = (rev_eta - orig_eta).days
    ttr       = (rev_eta - ref).days
    shortage  = ttr - tts

    print("\n  [A] SHORTAGE PATH")
    print(f"      on_hand={row['on_hand_qty']}  consumption={row['daily_consumption']}/day")
    print(f"      TTS   = {int(row['on_hand_qty'])} / {int(row['daily_consumption'])} = {tts} days")
    print(f"      Stockout date  = {stockout_date}  (expected 2026-10-08)")
    print(f"      Original ETA   = {po['original_eta']}")
    print(f"      Delay          = +{delay} days")
    print(f"      Revised ETA    = {po['revised_eta']}  (expected 2026-10-18)")
    print(f"      TTR            = ({po['revised_eta']} - {REFERENCE_DATE}).days = {ttr}")
    print(f"      Shortage wdw   = {stockout_date} to {po['revised_eta']} ({shortage} days)")
    assert tts == 5,               f"TTS should be 5, got {tts}"
    assert str(stockout_date) == "2026-10-08", f"Stockout should be 2026-10-08, got {stockout_date}"
    assert ttr == 15,              f"TTR should be 15, got {ttr}"
    assert shortage == 10,         f"Shortage should be 10 days, got {shortage}"
    assert delay == 7,             f"Delay should be +7 days, got {delay}"
    print("      [PASS] ALL ASSERTIONS PASS")

    # ── Scenario B ────────────────────────────────────────────────
    row_b = c.execute(
        "SELECT on_hand_qty, daily_consumption FROM inventory WHERE material_id = 'BEARING-6205'"
    ).fetchone()
    tts_b = int(row_b["on_hand_qty"] / row_b["daily_consumption"])
    po_b  = c.execute(
        "SELECT revised_eta FROM purchase_orders WHERE po_id = 'PO-2026-512'"
    ).fetchone()
    ttr_b = (date.fromisoformat(po_b["revised_eta"]) - ref).days

    print(f"\n  [B] ABSORBED PATH")
    print(f"      TTS = {tts_b} days   TTR = {ttr_b} days")
    print(f"      TTS({tts_b}) >= TTR({ttr_b}) -> ABSORBED")
    assert tts_b >= ttr_b, f"Scenario B: TTS({tts_b}) should be >= TTR({ttr_b})"
    print("      [PASS] ASSERTION PASS")

    # ── False Positive ─────────────────────────────────────────────
    fp_vessel = "MV Pacific Trader"
    fp_port   = "Manila"
    match = c.execute(
        "SELECT shipment_id FROM shipments WHERE vessel_name = ? OR port_of_loading = ?",
        (fp_vessel, fp_port)
    ).fetchone()
    print(f"\n  [C] FALSE-POSITIVE PATH")
    print(f"      Lookup: vessel='{fp_vessel}' / port='{fp_port}'")
    print(f"      Match in shipments table: {match}")
    assert match is None, "False-positive vessel/port should have NO match in shipments"
    print("      [PASS] NO MATCH -> IGNORED (assertion pass)")

    # ── Rulebook ──────────────────────────────────────────────────
    rules = c.execute("SELECT rule_id, severity, threshold_value FROM rules ORDER BY rule_id").fetchall()
    print(f"\n  [D] RULEBOOK ({len(rules)} rules — no verdicts)")
    for r in rules:
        print(f"      {r['rule_id']}  severity={r['severity']}  threshold={r['threshold_value']}")
    assert len(rules) == 8, f"Expected 8 rules, got {len(rules)}"
    print("      [PASS] C1–C8 present")

    # ── Recovery Templates ─────────────────────────────────────────
    opts = c.execute("SELECT template_id, option_type FROM recovery_option_templates").fetchall()
    print(f"\n  [E] RECOVERY TEMPLATES ({len(opts)} options)")
    for o in opts:
        print(f"      {o['template_id']}  type={o['option_type']}")
    assert len(opts) == 4, f"Expected 4 templates, got {len(opts)}"
    print("      [PASS] OPT-A through OPT-D present")

    print("\n" + "=" * 62)
    print("  CHECKPOINT 1 PASSED — DB is structurally correct.")
    print("  Safe to proceed to Part 3 (schemas) and Part 2 (engine).")
    print("=" * 62 + "\n")

    conn.close()


if __name__ == "__main__":
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"[RESET] Removed existing database: {DB_PATH}")

    seed()
    print(f"[OK]    Database seeded: {DB_PATH}")

    _verify()
