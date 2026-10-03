# -*- coding: utf-8 -*-
"""
engine/recovery_calculator.py
==============================
Enumerates recovery option candidates from DB, evaluates each against
C1-C8 via the constraint engine, and builds a RecoveryPlan.

All costs are read from supplier freight rate columns in SQLite.
No costs are invented or hardcoded in this file.

Sort order: feasible options sorted by net_benefit = avoided_exposure - total_cost
(descending). Vetoed options retained in output for UI display.
"""

from __future__ import annotations

import sqlite3
import sys
import os
from datetime import date
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from schemas.impact import ImpactAssessment
from schemas.recovery import CandidateOption, OptionStatus, RecoveryPlan
from engine.constraint_engine import evaluate_option


def enumerate_candidates(
    conn: sqlite3.Connection,
    impact: ImpactAssessment,
    reference_date: date,
) -> RecoveryPlan:
    """
    Build a RecoveryPlan from all recovery_option_templates in DB.

    Steps:
      1. Fetch all templates.
      2. Compute required_qty and available_days from ImpactAssessment.
      3. Evaluate each template via constraint_engine.evaluate_option.
      4. Compute days_of_coverage per feasible option.
      5. Sort feasible options by net_benefit descending.
      6. Return RecoveryPlan (pre-approval state -- selected_option_id=None).

    required_qty  = shortage.shortage_days * shortage.daily_consumption
    available_days = shortage.ttr_days  (= (revised_eta - reference_date).days)
    """
    shortage       = impact.shortage
    required_qty   = shortage.shortage_days * shortage.daily_consumption
    available_days = shortage.ttr_days

    template_rows = conn.execute(
        "SELECT template_id FROM recovery_option_templates ORDER BY template_id"
    ).fetchall()

    candidates: List[CandidateOption] = []
    for (tmpl_id,) in template_rows:
        option = evaluate_option(
            conn          = conn,
            template_id   = tmpl_id,
            required_qty  = required_qty,
            available_days = available_days,
            reference_date = reference_date,
        )

        # Compute days_of_coverage for feasible options
        coverage = 0
        if (option.constraint_result and
                option.constraint_result.option_status in (OptionStatus.PASS, OptionStatus.APPROVAL_REQUIRED)
                and option.estimated_arrival is not None):
            # Days covered = max(0, shortage_end - max(arrival, shortage_start))
            shortage_start = shortage.shortage_start or shortage.stockout_date
            shortage_end   = shortage.shortage_end   or shortage.revised_eta
            arrival        = option.estimated_arrival
            if arrival <= shortage_end:
                coverage = (shortage_end - max(arrival, shortage_start)).days + 1
                coverage = max(0, coverage)

        # Pydantic models are immutable -- rebuild with coverage
        option = CandidateOption(
            option_id         = option.option_id,
            option_type       = option.option_type,
            supplier_id       = option.supplier_id,
            material_id       = option.material_id,
            description       = option.description,
            total_cost        = option.total_cost,
            estimated_arrival = option.estimated_arrival,
            days_of_coverage  = coverage,
            constraint_result = option.constraint_result,
        )
        candidates.append(option)

    # Classify
    vetoed   = [o.option_id for o in candidates
                if o.constraint_result and o.constraint_result.option_status == OptionStatus.VETO]
    feasible = [o.option_id for o in candidates
                if o.constraint_result and o.constraint_result.option_status in
                (OptionStatus.PASS, OptionStatus.APPROVAL_REQUIRED)]

    # Sort feasible by net_benefit descending
    def net_benefit(opt_id: str) -> float:
        opt = next(o for o in candidates if o.option_id == opt_id)
        return impact.total_otif_exposure - opt.total_cost

    feasible_sorted = sorted(feasible, key=net_benefit, reverse=True)

    # Re-order candidates: feasible first (sorted), then vetoed
    ordered = (
        [o for o in candidates if o.option_id in feasible_sorted] +
        [o for o in candidates if o.option_id in vetoed]
    )

    return RecoveryPlan(
        shipment_id         = impact.shipment_id,
        po_id               = impact.po_id,
        reference_date      = reference_date,
        shortage_days       = shortage.shortage_days,
        total_otif_exposure = impact.total_otif_exposure,
        candidates          = ordered,
        vetoed_options      = vetoed,
        feasible_options    = feasible_sorted,
        selected_option_id  = None,
        approval_decision   = None,
        decided_by          = None,
        decided_at          = None,
    )
