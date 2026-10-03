# -*- coding: utf-8 -*-
"""
engine/impact_math.py
======================
Deterministic TTS / TTR / shortage / exposure computation.
Zero LLM involvement.

Exposure model (pegged-order value):
  For each work order found via BOM traversal:
    IF planned_start IN [shortage_start, shortage_end]  (INCLUSIVE end)
    AND work_order.co_id IS NOT NULL
    THEN exposure = customer_order.qty * customer_order.unit_price
    ELSE exposure = 0.0

Window boundary -- INCLUSIVE end:
  A WO starting exactly on shortage_end (2026-10-18) IS exposed.
  A WO starting on shortage_end + 1 (2026-10-19) is NOT exposed.

All values are read from SQLite. No hardcoded dates, quantities, or prices.
"""

from __future__ import annotations

import math
import sqlite3
import sys
import os
from datetime import date
from typing import List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from schemas.impact import (
    AffectedWorkOrder,
    ImpactAssessment,
    ImpactStatus,
    MaterialShortage,
)
from engine.bom_traversal import find_affected_work_orders

# Scenario anchor -- passed as parameter; default matches the locked seed.
_DEFAULT_REFERENCE_DATE = date(2026, 10, 3)


def compute_tts(on_hand_qty: float, daily_consumption: float) -> int:
    """
    Time-to-Stockout in days.
    tts_days = floor(on_hand_qty / daily_consumption)

    >>> compute_tts(400, 80)
    5
    >>> compute_tts(2000, 100)
    20
    """
    if daily_consumption <= 0:
        return 9999  # treat as "never stocks out"
    return math.floor(on_hand_qty / daily_consumption)


def compute_ttr(revised_eta: date, reference_date: date) -> int:
    """
    Time-to-Recover in days.
    ttr_days = (revised_eta - reference_date).days

    >>> from datetime import date
    >>> compute_ttr(date(2026, 10, 18), date(2026, 10, 3))
    15
    """
    return (revised_eta - reference_date).days


def compute_wo_exposure(
    conn: sqlite3.Connection,
    wo_id: str,
    shortage_start: date,
    shortage_end: date,
) -> tuple[float, bool, bool]:
    """
    Compute OTIF exposure for a single work order.

    Window boundary: INCLUSIVE on both ends.
    A WO with planned_start == shortage_end IS exposed.

    Returns:
        (otif_exposure, in_shortage_window, has_peg)

    Exposure = customer_order.qty * customer_order.unit_price
    if planned_start IN [shortage_start, shortage_end] AND co_id IS NOT NULL
    else 0.0
    """
    row = conn.execute(
        """
        SELECT wo.start_date, wo.co_id,
               co.qty        AS co_qty,
               co.unit_price AS co_unit_price
        FROM   work_orders wo
        LEFT JOIN customer_orders co ON wo.co_id = co.co_id
        WHERE  wo.wo_id = ?
        """,
        (wo_id,),
    ).fetchone()

    if row is None:
        return 0.0, False, False

    planned_start = date.fromisoformat(row[0])
    co_id         = row[1]
    co_qty        = row[2]
    co_unit_price = row[3]

    in_window = shortage_start <= planned_start <= shortage_end  # inclusive end
    has_peg   = co_id is not None

    if in_window and has_peg:
        exposure = float(co_qty) * float(co_unit_price)
    else:
        exposure = 0.0

    return exposure, in_window, has_peg


def run_impact(
    conn: sqlite3.Connection,
    shipment_id: str,
    reference_date: date = _DEFAULT_REFERENCE_DATE,
) -> ImpactAssessment:
    """
    Full impact assessment for a disrupted shipment.

    Steps:
      1. Resolve shipment -> PO -> material.
      2. Read inventory facts (on_hand, daily_consumption).
      3. Compute TTS, TTR, shortage_days.
      4. Determine status = SHORTAGE | ABSORBED (data-emergent, no scenario flag).
      5. If ABSORBED: return with empty WOs and $0 exposure.
      6. If SHORTAGE: traverse BOM upward for affected WOs, compute exposure per WO.

    Returns:
        ImpactAssessment (validated Pydantic model).

    Raises:
        ValueError if shipment_id or inventory not found.
    """
    # 1. Resolve shipment
    shipment = conn.execute(
        """
        SELECT s.shipment_id, s.po_id, s.revised_eta,
               p.material_id, p.qty AS po_qty
        FROM   shipments s
        JOIN   purchase_orders p ON s.po_id = p.po_id
        WHERE  s.shipment_id = ?
        """,
        (shipment_id,),
    ).fetchone()

    if shipment is None:
        raise ValueError(f"Shipment '{shipment_id}' not found in DB.")

    ship_id    = shipment[0]
    po_id      = shipment[1]
    rev_eta    = date.fromisoformat(shipment[2])
    material_id = shipment[3]

    # 2. Inventory
    inv = conn.execute(
        "SELECT on_hand_qty, daily_consumption FROM inventory WHERE material_id = ?",
        (material_id,),
    ).fetchone()

    if inv is None:
        raise ValueError(f"No inventory record for material '{material_id}'.")

    on_hand_qty       = float(inv[0])
    daily_consumption = float(inv[1])

    # 3. Material name
    mat_row = conn.execute(
        "SELECT name FROM materials WHERE material_id = ?",
        (material_id,),
    ).fetchone()
    material_name = mat_row[0] if mat_row else material_id

    # 4. Core arithmetic
    tts_days     = compute_tts(on_hand_qty, daily_consumption)
    ttr_days     = compute_ttr(rev_eta, reference_date)
    shortage_days = max(0, ttr_days - tts_days)

    stockout_date = date.fromordinal(reference_date.toordinal() + tts_days)

    # 5. Status -- data-emergent, no scenario flag
    status = ImpactStatus.SHORTAGE if ttr_days > tts_days else ImpactStatus.ABSORBED

    shortage = MaterialShortage(
        material_id       = material_id,
        material_name     = material_name,
        on_hand_qty       = on_hand_qty,
        daily_consumption = daily_consumption,
        tts_days          = tts_days,
        stockout_date     = stockout_date,
        ttr_days          = ttr_days,
        revised_eta       = rev_eta,
        shortage_days     = shortage_days,
        shortage_start    = stockout_date if status == ImpactStatus.SHORTAGE else None,
        shortage_end      = rev_eta       if status == ImpactStatus.SHORTAGE else None,
    )

    # 6. ABSORBED: no WOs, no exposure
    if status == ImpactStatus.ABSORBED:
        return ImpactAssessment(
            shipment_id          = ship_id,
            po_id                = po_id,
            reference_date       = reference_date,
            shortage             = shortage,
            affected_work_orders = [],
            total_otif_exposure  = 0.0,
            status               = status,
            recovery_required    = False,
        )

    # 7. SHORTAGE: traverse BOM, compute per-WO exposure
    shortage_start = stockout_date
    shortage_end   = rev_eta

    affected_wo_ids = find_affected_work_orders(conn, material_id)

    affected_wos: List[AffectedWorkOrder] = []
    total_exposure = 0.0

    for wo_id in affected_wo_ids:
        wo_row = conn.execute(
            """
            SELECT wo.wo_id, wo.co_id, wo.product_id, wo.qty,
                   wo.start_date, wo.due_date
            FROM   work_orders wo
            WHERE  wo.wo_id = ?
            """,
            (wo_id,),
        ).fetchone()

        if wo_row is None:
            continue

        req_row = conn.execute(
            "SELECT required_qty FROM wo_materials WHERE wo_id = ? AND material_id = ?",
            (wo_id, material_id),
        ).fetchone()
        required_material_qty = float(req_row[0]) if req_row else 0.0

        exposure, in_window, has_peg = compute_wo_exposure(
            conn, wo_id, shortage_start, shortage_end
        )
        total_exposure += exposure

        awo = AffectedWorkOrder(
            wo_id                = wo_row[0],
            co_id                = wo_row[1],
            product_id           = wo_row[2],
            qty                  = float(wo_row[3]),
            planned_start        = date.fromisoformat(wo_row[4]),
            due_date             = date.fromisoformat(wo_row[5]),
            required_material_qty= required_material_qty,
            in_shortage_window   = in_window,
            otif_exposure        = exposure,
        )
        affected_wos.append(awo)

    return ImpactAssessment(
        shipment_id          = ship_id,
        po_id                = po_id,
        reference_date       = reference_date,
        shortage             = shortage,
        affected_work_orders = affected_wos,
        total_otif_exposure  = total_exposure,
        status               = status,
        recovery_required    = True,
    )
