# -*- coding: utf-8 -*-
"""
engine/constraint_engine.py
============================
Generic C1-C8 rule evaluator.

Architecture:
  - Reads rulebook from DB (rules table). No hardcoded thresholds.
  - Reads supplier/material/WO facts from DB. No hardcoded verdicts.
  - Changing a DB fact (e.g. EuroCoils MOQ from 200 -> 1000) changes C2
    from PASS to FAIL without touching this file.

Rule status per evaluation:
  PASS  : condition satisfied
  FAIL  : condition violated (HARD rules -> VETO; C5 ROUTE -> APPROVAL_REQUIRED)
  NA    : rule not applicable to this option type

Aggregation:
  any HARD rule FAIL  -> VETO
  C5 fires (cost > threshold) -> APPROVAL_REQUIRED  (only if no HARD fails)
  else                -> PASS

C5 is a ROUTE rule: its "fire" condition is cost > threshold, but in
RuleEvaluation it always shows status=PASS (it does not veto the option).
The approval routing is tracked separately and aggregated by the evaluator.
This is consistent with the Part 3 schema ConstraintResult validators.

NA rules do not contribute to any failure or success determination.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import os
from datetime import date
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from schemas.recovery import (
    CandidateOption,
    ConstraintResult,
    OptionStatus,
    OptionType,
    RuleEvaluation,
    RuleStatus,
)


# ─────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────

def _fetch_rules(conn: sqlite3.Connection) -> dict:
    """Return rulebook as {rule_id: row_dict}."""
    rows = conn.execute("SELECT * FROM rules ORDER BY rule_id").fetchall()
    keys = ["rule_id", "rule_name", "rule_type", "parameter", "severity", "threshold_value", "description"]
    return {r[0]: dict(zip(keys, r)) for r in rows}


def _fetch_supplier(conn: sqlite3.Connection, supplier_id: str) -> Optional[dict]:
    if not supplier_id:
        return None
    row = conn.execute("SELECT * FROM suppliers WHERE supplier_id = ?", (supplier_id,)).fetchone()
    if not row:
        return None
    keys = ["supplier_id", "name", "country", "lead_time_days", "moq",
            "capacity_per_month", "freight_cost_air", "freight_cost_sea",
            "export_restricted", "tier", "approved"]
    return dict(zip(keys, row))


def _fetch_material(conn: sqlite3.Connection, material_id: str) -> Optional[dict]:
    if not material_id:
        return None
    row = conn.execute("SELECT * FROM materials WHERE material_id = ?", (material_id,)).fetchone()
    if not row:
        return None
    keys = ["material_id", "name", "material_type", "unit", "compatible_revisions", "notes"]
    return dict(zip(keys, row))


def _has_ppap(conn: sqlite3.Connection, supplier_id: str, material_id: Optional[str]) -> bool:
    """Check if supplier holds a valid PPAP cert for the given material or product family."""
    # Check material-specific PPAP
    if material_id:
        row = conn.execute(
            """
            SELECT 1 FROM supplier_certifications
            WHERE supplier_id = ? AND cert_type = 'PPAP'
              AND (material_id = ? OR material_id IS NULL)
            LIMIT 1
            """,
            (supplier_id, material_id),
        ).fetchone()
        if row:
            return True
    # Check general PPAP
    row = conn.execute(
        "SELECT 1 FROM supplier_certifications WHERE supplier_id=? AND cert_type='PPAP' AND material_id IS NULL LIMIT 1",
        (supplier_id,),
    ).fetchone()
    return row is not None


def _compute_total_cost(supplier: Optional[dict], freight_mode: Optional[str]) -> float:
    """Read freight cost from supplier DB record based on freight_mode. Never invented."""
    if supplier is None or not freight_mode:
        return 0.0
    if freight_mode == "AIR":
        return float(supplier["freight_cost_air"])
    if freight_mode == "SEA":
        return float(supplier["freight_cost_sea"])
    return 0.0


# ─────────────────────────────────────────────────────────────────
# INDIVIDUAL RULE EVALUATORS
# Each returns RuleEvaluation. All facts come from DB dicts.
# ─────────────────────────────────────────────────────────────────

def _eval_c1(rule: dict, supplier: Optional[dict], available_days: int, option_type: str) -> RuleEvaluation:
    """C1: supplier.lead_time_days <= available_days. NA for RESCHEDULE."""
    if option_type == OptionType.RESCHEDULE or supplier is None:
        return RuleEvaluation(rule_id="C1", rule_name=rule["rule_name"],
                              status=RuleStatus.NA, evidence="Not applicable to RESCHEDULE option.",
                              threshold=rule["threshold_value"], actual=None)
    actual = int(supplier["lead_time_days"])
    if actual <= available_days:
        return RuleEvaluation(rule_id="C1", rule_name=rule["rule_name"],
                              status=RuleStatus.PASS,
                              evidence=f"lead_time_days={actual} <= available_days={available_days}.",
                              threshold=str(available_days), actual=str(actual))
    return RuleEvaluation(rule_id="C1", rule_name=rule["rule_name"],
                          status=RuleStatus.FAIL,
                          evidence=f"lead_time_days={actual} exceeds available_days={available_days}. "
                                   f"Arrival ~{available_days + (actual - available_days)} days out.",
                          threshold=str(available_days), actual=str(actual))


def _eval_c2(rule: dict, supplier: Optional[dict], required_qty: float, option_type: str) -> RuleEvaluation:
    """C2: supplier.moq <= required_qty. NA for RESCHEDULE."""
    if option_type == OptionType.RESCHEDULE or supplier is None:
        return RuleEvaluation(rule_id="C2", rule_name=rule["rule_name"],
                              status=RuleStatus.NA, evidence="Not applicable to RESCHEDULE option.",
                              threshold=rule["threshold_value"], actual=None)
    moq = int(supplier["moq"])
    if moq <= required_qty:
        return RuleEvaluation(rule_id="C2", rule_name=rule["rule_name"],
                              status=RuleStatus.PASS,
                              evidence=f"supplier.moq={moq} <= required_qty={int(required_qty)}. Can place order.",
                              threshold=str(int(required_qty)), actual=str(moq))
    return RuleEvaluation(rule_id="C2", rule_name=rule["rule_name"],
                          status=RuleStatus.FAIL,
                          evidence=f"supplier.moq={moq} > required_qty={int(required_qty)}. "
                                   f"Cannot place an order smaller than MOQ.",
                          threshold=str(int(required_qty)), actual=str(moq))


def _eval_c3(rule: dict, conn: sqlite3.Connection, supplier: Optional[dict],
             material: Optional[dict], option_type: str) -> RuleEvaluation:
    """C3: Supplier must hold PPAP cert for this material/family. NA for RESCHEDULE."""
    if option_type == OptionType.RESCHEDULE or supplier is None:
        return RuleEvaluation(rule_id="C3", rule_name=rule["rule_name"],
                              status=RuleStatus.NA, evidence="Not applicable to RESCHEDULE option.",
                              threshold=rule["threshold_value"], actual=None)
    material_id = material["material_id"] if material else None
    has = _has_ppap(conn, supplier["supplier_id"], material_id)
    if has:
        return RuleEvaluation(rule_id="C3", rule_name=rule["rule_name"],
                              status=RuleStatus.PASS,
                              evidence=f"Supplier {supplier['supplier_id']} holds valid PPAP for this material.",
                              threshold="PPAP", actual="PPAP")
    return RuleEvaluation(rule_id="C3", rule_name=rule["rule_name"],
                          status=RuleStatus.FAIL,
                          evidence=f"No PPAP certificate found for supplier {supplier['supplier_id']} "
                                   f"and material {material_id}.",
                          threshold="PPAP", actual="NONE")


def _eval_c4(rule: dict, conn: sqlite3.Connection, target_wo_id: Optional[str],
             option_type: str) -> RuleEvaluation:
    """C4: WO must not be in frozen schedule window. ONLY applies to RESCHEDULE."""
    if option_type != OptionType.RESCHEDULE:
        return RuleEvaluation(rule_id="C4", rule_name=rule["rule_name"],
                              status=RuleStatus.NA, evidence="Not applicable -- not a RESCHEDULE option.",
                              threshold=rule["threshold_value"], actual=None)
    if not target_wo_id:
        return RuleEvaluation(rule_id="C4", rule_name=rule["rule_name"],
                              status=RuleStatus.FAIL,
                              evidence="RESCHEDULE option has no target_wo_id specified.",
                              threshold=rule["threshold_value"], actual=None)
    wo = conn.execute(
        "SELECT wo_id, frozen_schedule, start_date FROM work_orders WHERE wo_id = ?",
        (target_wo_id,),
    ).fetchone()
    if wo is None:
        return RuleEvaluation(rule_id="C4", rule_name=rule["rule_name"],
                              status=RuleStatus.FAIL,
                              evidence=f"Target WO '{target_wo_id}' not found in DB.",
                              threshold=rule["threshold_value"], actual=None)
    frozen = int(wo[1])
    start  = wo[2]
    if frozen == 0:
        return RuleEvaluation(rule_id="C4", rule_name=rule["rule_name"],
                              status=RuleStatus.PASS,
                              evidence=f"{target_wo_id} start_date={start} is outside frozen window. Reschedule permitted.",
                              threshold=rule["threshold_value"], actual="0")
    return RuleEvaluation(rule_id="C4", rule_name=rule["rule_name"],
                          status=RuleStatus.FAIL,
                          evidence=f"{target_wo_id} start_date={start} is within {rule['threshold_value']}-day "
                                   f"frozen window (frozen_schedule=1). Rescheduling blocked.",
                          threshold=rule["threshold_value"], actual="1")


def _eval_c5(rule: dict, total_cost: float, option_type: str) -> Tuple[RuleEvaluation, bool]:
    """
    C5: Budget authorization. ROUTE rule -- never vetoes.
    Returns (RuleEvaluation, approval_required: bool).
    When cost > threshold: status=PASS but approval_required=True.
    NA for RESCHEDULE ($0 cost).
    """
    if option_type == OptionType.RESCHEDULE:
        return (RuleEvaluation(rule_id="C5", rule_name=rule["rule_name"],
                               status=RuleStatus.NA, evidence="RESCHEDULE has $0 cost -- no budget approval needed.",
                               threshold=rule["threshold_value"], actual="0"),
                False)
    threshold = float(rule["threshold_value"])
    if total_cost <= threshold:
        return (RuleEvaluation(rule_id="C5", rule_name=rule["rule_name"],
                               status=RuleStatus.PASS,
                               evidence=f"Total cost ${total_cost:,.0f} is within plant budget ${threshold:,.0f}.",
                               threshold=rule["threshold_value"], actual=f"{total_cost:.0f}"),
                False)
    # Cost exceeds threshold: routes to approval -- status=PASS (not a veto)
    return (RuleEvaluation(rule_id="C5", rule_name=rule["rule_name"],
                           status=RuleStatus.PASS,
                           evidence=f"Total cost ${total_cost:,.0f} exceeds plant budget ${threshold:,.0f}. "
                                    f"Routes to VP Supply Chain approval.",
                           threshold=rule["threshold_value"], actual=f"{total_cost:.0f}"),
            True)  # approval_required=True


def _eval_c6(rule: dict, material: Optional[dict], option_type: str) -> RuleEvaluation:
    """C6: Material compatible_revisions must include the required revision (REV-D). NA for RESCHEDULE."""
    if option_type == OptionType.RESCHEDULE or material is None:
        return RuleEvaluation(rule_id="C6", rule_name=rule["rule_name"],
                              status=RuleStatus.NA, evidence="Not applicable to RESCHEDULE option.",
                              threshold=rule["threshold_value"], actual=None)
    required_rev = rule["threshold_value"]
    try:
        compat = json.loads(material["compatible_revisions"])
    except (json.JSONDecodeError, TypeError):
        compat = []
    if required_rev in compat:
        return RuleEvaluation(rule_id="C6", rule_name=rule["rule_name"],
                              status=RuleStatus.PASS,
                              evidence=f"Material compatible_revisions={compat} includes {required_rev}.",
                              threshold=required_rev, actual=str(compat))
    return RuleEvaluation(rule_id="C6", rule_name=rule["rule_name"],
                          status=RuleStatus.FAIL,
                          evidence=f"Material compatible_revisions={compat} does NOT include {required_rev}. "
                                   f"BOM revision incompatible.",
                          threshold=required_rev, actual=str(compat))


def _eval_c7(rule: dict, supplier: Optional[dict], option_type: str) -> RuleEvaluation:
    """C7: Supplier must not be export-restricted. NA for RESCHEDULE."""
    if option_type == OptionType.RESCHEDULE or supplier is None:
        return RuleEvaluation(rule_id="C7", rule_name=rule["rule_name"],
                              status=RuleStatus.NA, evidence="Not applicable to RESCHEDULE option.",
                              threshold=rule["threshold_value"], actual=None)
    restricted = int(supplier["export_restricted"])
    if restricted == 0:
        return RuleEvaluation(rule_id="C7", rule_name=rule["rule_name"],
                              status=RuleStatus.PASS,
                              evidence=f"Supplier {supplier['supplier_id']} is not export-restricted.",
                              threshold="0", actual="0")
    return RuleEvaluation(rule_id="C7", rule_name=rule["rule_name"],
                          status=RuleStatus.FAIL,
                          evidence=f"Supplier {supplier['supplier_id']} export_restricted=1. Procurement blocked.",
                          threshold="0", actual="1")


def _eval_c8(rule: dict, supplier: Optional[dict], required_qty: float, option_type: str) -> RuleEvaluation:
    """C8: supplier.capacity_per_month >= required_qty. NA for RESCHEDULE."""
    if option_type == OptionType.RESCHEDULE or supplier is None:
        return RuleEvaluation(rule_id="C8", rule_name=rule["rule_name"],
                              status=RuleStatus.NA, evidence="Not applicable to RESCHEDULE option.",
                              threshold=rule["threshold_value"], actual=None)
    capacity = int(supplier["capacity_per_month"])
    if capacity >= required_qty:
        return RuleEvaluation(rule_id="C8", rule_name=rule["rule_name"],
                              status=RuleStatus.PASS,
                              evidence=f"capacity_per_month={capacity} >= required_qty={int(required_qty)}.",
                              threshold=str(int(required_qty)), actual=str(capacity))
    return RuleEvaluation(rule_id="C8", rule_name=rule["rule_name"],
                          status=RuleStatus.FAIL,
                          evidence=f"capacity_per_month={capacity} < required_qty={int(required_qty)}. "
                                   f"Supplier cannot fulfil order volume.",
                          threshold=str(int(required_qty)), actual=str(capacity))


# ─────────────────────────────────────────────────────────────────
# AGGREGATOR
# ─────────────────────────────────────────────────────────────────

def _aggregate(rule_evals: List[RuleEvaluation], approval_required: bool) -> Tuple[OptionStatus, List[str]]:
    """
    Aggregate individual rule results into an option-level verdict.

    Logic:
      any HARD rule FAIL (C1/C2/C3/C4/C6/C7/C8) -> VETO
      approval_required (C5 fired, no hard fails)  -> APPROVAL_REQUIRED
      else                                          -> PASS

    NA results are invisible to this aggregator.
    """
    # HARD FAIL rules are those with status=FAIL (C5 never shows FAIL)
    hard_fails = [r.rule_id for r in rule_evals if r.status == RuleStatus.FAIL]
    if hard_fails:
        return OptionStatus.VETO, hard_fails
    if approval_required:
        return OptionStatus.APPROVAL_REQUIRED, []
    return OptionStatus.PASS, []


# ─────────────────────────────────────────────────────────────────
# PUBLIC INTERFACE
# ─────────────────────────────────────────────────────────────────

def evaluate_option(
    conn: sqlite3.Connection,
    template_id: str,
    required_qty: float,
    available_days: int,
    reference_date: date,
) -> CandidateOption:
    """
    Evaluate a recovery option template against C1-C8 rules.

    Args:
        conn          : DB connection
        template_id   : e.g. 'OPT-A'
        required_qty  : units needed to cover shortage (e.g. 800)
        available_days: (revised_eta - reference_date).days (e.g. 15)
        reference_date: scenario anchor date

    Returns:
        CandidateOption with constraint_result populated.
    """
    # Fetch template
    t = conn.execute(
        """
        SELECT template_id, option_type, supplier_id, material_id,
               target_wo_id, freight_mode, description, notes
        FROM   recovery_option_templates
        WHERE  template_id = ?
        """,
        (template_id,),
    ).fetchone()
    if t is None:
        raise ValueError(f"Template '{template_id}' not found in recovery_option_templates.")

    tmpl_id, opt_type_str, supp_id, mat_id, target_wo_id, freight_mode, desc, notes = t
    option_type = OptionType(opt_type_str)

    # Fetch DB facts
    rulebook  = _fetch_rules(conn)
    supplier  = _fetch_supplier(conn, supp_id)
    material  = _fetch_material(conn, mat_id)
    total_cost = _compute_total_cost(supplier, freight_mode)

    # Compute estimated arrival
    estimated_arrival = None
    if supplier and option_type != OptionType.RESCHEDULE:
        lead = int(supplier["lead_time_days"])
        estimated_arrival = date.fromordinal(reference_date.toordinal() + lead)

    # Evaluate C1-C8
    c5_eval, approval_required = _eval_c5(rulebook["C5"], total_cost, option_type)

    rule_evals: List[RuleEvaluation] = [
        _eval_c1(rulebook["C1"], supplier, available_days, option_type),
        _eval_c2(rulebook["C2"], supplier, required_qty, option_type),
        _eval_c3(rulebook["C3"], conn, supplier, material, option_type),
        _eval_c4(rulebook["C4"], conn, target_wo_id, option_type),
        c5_eval,
        _eval_c6(rulebook["C6"], material, option_type),
        _eval_c7(rulebook["C7"], supplier, option_type),
        _eval_c8(rulebook["C8"], supplier, required_qty, option_type),
    ]

    option_status, veto_rules = _aggregate(rule_evals, approval_required)

    constraint_result = ConstraintResult(
        option_id    = tmpl_id,
        option_status= option_status,
        rules        = rule_evals,
        veto_rules   = veto_rules,
    )

    return CandidateOption(
        option_id          = tmpl_id,
        option_type        = option_type,
        supplier_id        = supp_id,
        material_id        = mat_id,
        description        = desc,
        total_cost         = total_cost,
        estimated_arrival  = estimated_arrival,
        days_of_coverage   = 0,  # set by recovery_calculator
        constraint_result  = constraint_result,
    )
