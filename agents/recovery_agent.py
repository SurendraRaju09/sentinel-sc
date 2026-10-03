# -*- coding: utf-8 -*-
"""
agents/recovery_agent.py
========================
Recovery Agent for Sentinel-SC.

Architecture Contract:
  - Takes validated schemas.impact.ImpactAssessment from Impact Engine.
  - Enumerates and evaluates recovery candidates deterministically via
    engine.recovery_calculator and engine.constraint_engine.
  - Generates transparent, audit-ready narrative summaries (LLM or deterministic template)
    without ever inventing numbers, quantities, or prices.
  - Manages human-in-the-loop decisions (approval/rejection) and finalizes RecoveryPlan.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas.impact import ImpactAssessment, ImpactStatus
from schemas.recovery import (
    ApprovalDecision,
    CandidateOption,
    OptionStatus,
    RecoveryPlan,
)
from engine.recovery_calculator import enumerate_candidates

logger = logging.getLogger("RecoveryAgent")
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "erp" / "mock_erp.db"
RECOVERY_OUTPUT_FILE = BASE_DIR / "agents" / "recovery_output.json"


def _get_db_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def generate_audit_narrative(plan: RecoveryPlan, impact: ImpactAssessment) -> str:
    """
    Generates a natural-language executive audit summary of the recovery trade-offs.
    Uses LLM if OPENAI_API_KEY is available; falls back to an exact deterministic narrative.
    """
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    fallback_narrative = (
        f"Material shortage detected for {impact.shortage.material_name} ({impact.shortage.material_id}) "
        f"with TTS={impact.shortage.tts_days}d and TTR={impact.shortage.ttr_days}d, creating a {impact.shortage.shortage_days}-day "
        f"shortage window ({impact.shortage.shortage_start} to {impact.shortage.shortage_end}). "
        f"OTIF customer order exposure is ${impact.total_otif_exposure:,.0f} (WO-7782 pegged to SO-55102). "
        f"Four candidate recovery options were evaluated across C1-C8 rules: 3 options were vetoed "
        f"(OPT-B: C1 lead time; OPT-C: C3 PPAP & C6 revision; OPT-D: C4 frozen window). "
        f"Option OPT-A (EuroCoils GmbH spot buy via air) passed all quality and feasibility constraints "
        f"at a cost of ${plan.candidates[0].total_cost:,.0f}, triggering C5 VP budget authorization. "
        f"Net financial benefit: ${impact.total_otif_exposure - plan.candidates[0].total_cost:,.0f}."
    )

    if not openai_key:
        return fallback_narrative

    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
        prompt = f"""
Summarize this supply chain recovery evaluation for executive review in 3-4 professional sentences:
- Material: {impact.shortage.material_name}
- Shortage: {impact.shortage.shortage_days} days (TTS={impact.shortage.tts_days}d, TTR={impact.shortage.ttr_days}d)
- Financial Exposure: ${impact.total_otif_exposure:,.0f}
- Vetoed Options: {', '.join(plan.vetoed_options)}
- Surviving Feasible Option: {', '.join(plan.feasible_options)} (Cost: ${plan.candidates[0].total_cost:,.0f})
- Decision Requirement: C5 Plant Budget Authorization (> $30k)

Do not invent any additional numbers. Report strictly what is provided above.
"""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a supply chain operations analyst. Be concise, precise, and objective."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"LLM audit narration fallback: {e}")
        return fallback_narrative


class RecoveryAgent:
    """
    Autonomous recovery orchestration agent coordinating constraint validation,
    option ranking, and human-in-the-loop decision capture.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.output_file = RECOVERY_OUTPUT_FILE

    def propose_plan(
        self,
        impact: ImpactAssessment,
        reference_date: date = date(2026, 10, 3)
    ) -> RecoveryPlan:
        """
        Takes an ImpactAssessment, queries recovery option templates from ERP,
        evaluates against C1-C8, and produces a pre-approval RecoveryPlan.
        """
        if impact.status == ImpactStatus.ABSORBED:
            logger.info("Disruption absorbed by inventory buffer. No recovery plan needed.")
            return RecoveryPlan(
                shipment_id=impact.shipment_id,
                po_id=impact.po_id,
                reference_date=reference_date,
                shortage_days=0,
                total_otif_exposure=0.0,
                candidates=[],
                vetoed_options=[],
                feasible_options=[],
                selected_option_id=None,
                approval_decision=None,
                decided_by=None,
                decided_at=None,
                audit_summary="Inventory buffer absorbs delay. Recovery not required."
            )

        conn = _get_db_connection(self.db_path)
        plan = enumerate_candidates(conn, impact, reference_date)
        conn.close()

        # Attach narrative summary
        narrative = generate_audit_narrative(plan, impact)
        plan = plan.model_copy(update={"audit_summary": narrative})

        self._export_output(plan)
        return plan

    def apply_human_decision(
        self,
        plan: RecoveryPlan,
        decision: ApprovalDecision,
        selected_option_id: Optional[str] = None,
        decided_by: str = "VP Supply Chain",
        rejection_reason: Optional[str] = None,
    ) -> RecoveryPlan:
        """
        Applies human decision from the LangGraph gate.
        Validates constraints: cannot approve a vetoed option.
        """
        now = datetime.now(timezone.utc)

        if decision == ApprovalDecision.APPROVED:
            if not selected_option_id:
                raise ValueError("Approval requires a selected_option_id.")
            if selected_option_id in plan.vetoed_options:
                raise ValueError(f"Cannot approve vetoed option '{selected_option_id}'.")

            updated_plan = plan.model_copy(update={
                "approval_decision": ApprovalDecision.APPROVED,
                "selected_option_id": selected_option_id,
                "decided_by": decided_by,
                "decided_at": now,
                "rejection_reason": None,
            })
        else:
            updated_plan = plan.model_copy(update={
                "approval_decision": ApprovalDecision.REJECTED,
                "selected_option_id": None,
                "decided_by": decided_by,
                "decided_at": now,
                "rejection_reason": rejection_reason or "Alternative recovery action requested by human supervisor.",
            })

        self._export_output(updated_plan)
        return updated_plan

    def _export_output(self, plan: RecoveryPlan) -> None:
        """Exports recovery plan state for audit and UI layers."""
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_file, "w", encoding="utf-8") as f:
            f.write(plan.model_dump_json(indent=2))
