# -*- coding: utf-8 -*-
"""
graph.py
========
LangGraph State Machine & Orchestrator for Sentinel-SC.

Pipeline Nodes:
  1. ingest_signal: Ingests news/fixture signal via SignalAgent.
     -> If SignalStatus == IGNORED -> routes to handle_ignored -> END
     -> If SignalStatus == ACTIVE -> routes to compute_impact
  2. compute_impact: Evaluates TTS, TTR, shortage, exposure via Impact Engine.
     -> If ImpactStatus == ABSORBED -> routes to handle_absorbed -> END
     -> If ImpactStatus == SHORTAGE -> routes to propose_recovery
  3. propose_recovery: Evaluates C1-C8 rules, ranks recovery options via RecoveryAgent.
     -> routes to human_approval_gate
  4. human_approval_gate: Uses LangGraph interrupt() to pause execution and wait
     for Human Gate input (Approve/Reject).
  5. execute_decision: Resumes after human input, applies selection, and finalizes audit log.
     -> END

State Persistence:
  Uses SqliteSaver checkpointer so the graph state survives server restarts and API roundtrips.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agents.recovery_agent import RecoveryAgent
from agents.signal_agent import SignalAgent
from engine.impact_math import run_impact
from schemas.events import DisruptionEvent, SignalStatus
from schemas.impact import ImpactAssessment, ImpactStatus
from schemas.recovery import ApprovalDecision, RecoveryPlan

logger = logging.getLogger("SentinelGraph")
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "erp" / "mock_erp.db"
CHECKPOINT_DB = BASE_DIR / "erp" / "graph_state.db"


# ─────────────────────────────────────────────────────────────────
# STATE DEFINITION
# ─────────────────────────────────────────────────────────────────

class SentinelState(TypedDict):
    # Inputs
    headline: Optional[str]
    content: Optional[str]
    fixture_path: Optional[str]
    use_serp: bool
    reference_date: str

    # Pipeline Artifacts (JSON-serializable dicts)
    disruption_event: Optional[Dict[str, Any]]
    impact_assessment: Optional[Dict[str, Any]]
    recovery_plan: Optional[Dict[str, Any]]

    # Human Gate Inputs
    human_decision: Optional[Dict[str, Any]]

    # Execution tracking
    current_step: str
    audit_trail: List[str]


# ─────────────────────────────────────────────────────────────────
# GRAPH NODES
# ─────────────────────────────────────────────────────────────────

def ingest_signal_node(state: SentinelState) -> Dict[str, Any]:
    """Node 1: Extract and correlate disruption signal."""
    agent = SignalAgent(db_path=DB_PATH)
    ref_date = date.fromisoformat(state.get("reference_date", "2026-10-03"))
    trail = list(state.get("audit_trail", []))

    if state.get("fixture_path"):
        event = agent.run_from_fixture(Path(state["fixture_path"]), reference_date=ref_date)
        trail.append(f"Ingested fixture: {state['fixture_path']}")
    elif state.get("use_serp"):
        event, tier = agent.run_from_serp(query=state.get("headline"), reference_date=ref_date)
        trail.append(f"Ingested signal via SerpAPI ({tier})")
    else:
        event = agent.parse_from_text(
            headline=state.get("headline", ""),
            content=state.get("content", ""),
            reference_date=ref_date,
        )
        trail.append(f"Ingested text signal: {event.raw_headline[:50]}...")

    trail.append(f"Signal Agent: status={event.status.value}, shipment={event.shipment_id}")

    return {
        "disruption_event": json.loads(event.model_dump_json()),
        "current_step": "SIGNAL_INGESTED" if event.status == SignalStatus.ACTIVE else "SIGNAL_IGNORED",
        "audit_trail": trail,
    }


def handle_ignored_node(state: SentinelState) -> Dict[str, Any]:
    """Node 1b: Signal has no ERP correlation. Halt workflow cleanly."""
    trail = list(state.get("audit_trail", []))
    event_dict = state.get("disruption_event") or {}
    reason = event_dict.get("ignore_reason", "No shipment matched in ERP.")
    trail.append(f"Workflow halted: {reason}")
    return {
        "current_step": "HALTED_SIGNAL_IGNORED",
        "audit_trail": trail,
    }


def compute_impact_node(state: SentinelState) -> Dict[str, Any]:
    """Node 2: Deterministic Impact Assessment."""
    event_dict = state["disruption_event"]
    shipment_id = event_dict["shipment_id"]
    ref_date = date.fromisoformat(state.get("reference_date", "2026-10-03"))
    trail = list(state.get("audit_trail", []))

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    assessment = run_impact(conn, shipment_id=shipment_id, reference_date=ref_date)
    conn.close()

    trail.append(
        f"Impact Engine: status={assessment.status.value}, TTS={assessment.shortage.tts_days}d, "
        f"TTR={assessment.shortage.ttr_days}d, exposure=${assessment.total_otif_exposure:,.0f}"
    )

    return {
        "impact_assessment": json.loads(assessment.model_dump_json()),
        "current_step": "IMPACT_COMPUTED",
        "audit_trail": trail,
    }


def handle_absorbed_node(state: SentinelState) -> Dict[str, Any]:
    """Node 2b: Disruption absorbed by inventory buffer. No recovery needed."""
    trail = list(state.get("audit_trail", []))
    trail.append("Disruption absorbed by inventory buffer (TTS >= TTR). Recovery not required.")
    return {
        "current_step": "HALTED_DELAY_ABSORBED",
        "audit_trail": trail,
    }


def propose_recovery_node(state: SentinelState) -> Dict[str, Any]:
    """Node 3: Candidate option enumeration & C1-C8 constraint validation."""
    assessment = ImpactAssessment.model_validate(state["impact_assessment"])
    agent = RecoveryAgent(db_path=DB_PATH)
    ref_date = date.fromisoformat(state.get("reference_date", "2026-10-03"))
    trail = list(state.get("audit_trail", []))

    plan = agent.propose_plan(impact=assessment, reference_date=ref_date)

    trail.append(
        f"Recovery Agent: 4 candidate options evaluated across C1-C8 rules. "
        f"Vetoed: {', '.join(plan.vetoed_options)}. Feasible: {', '.join(plan.feasible_options)}."
    )

    return {
        "recovery_plan": json.loads(plan.model_dump_json()),
        "current_step": "AWAITING_HUMAN_APPROVAL",
        "audit_trail": trail,
    }


def human_approval_gate_node(state: SentinelState) -> Dict[str, Any]:
    """
    Node 4: Human-in-the-Loop decision gate.
    Uses interrupt() to suspend graph execution and persist state to SqliteSaver.
    """
    plan_dict = state["recovery_plan"]
    prompt_payload = {
        "message": "Human authorization required for recovery action.",
        "shipment_id": plan_dict["shipment_id"],
        "feasible_options": plan_dict["feasible_options"],
        "vetoed_options": plan_dict["vetoed_options"],
        "total_exposure": plan_dict["total_otif_exposure"],
        "options": [
            {
                "option_id": c["option_id"],
                "type": c["option_type"],
                "cost": c["total_cost"],
                "status": c["constraint_result"]["option_status"] if c.get("constraint_result") else "UNKNOWN",
            }
            for c in plan_dict["candidates"]
        ],
    }

    # Suspend here until resumed via Command(resume=...)
    human_input = interrupt(prompt_payload)

    trail = list(state.get("audit_trail", []))
    decision = human_input.get("decision", "APPROVED")
    selected_id = human_input.get("selected_option_id", "OPT-A")
    decided_by = human_input.get("decided_by", "VP Supply Chain")
    trail.append(f"Human Gate: decision={decision}, option={selected_id}, approver='{decided_by}'")

    return {
        "human_decision": human_input,
        "current_step": "DECISION_RECEIVED",
        "audit_trail": trail,
    }


def execute_decision_node(state: SentinelState) -> Dict[str, Any]:
    """Node 5: Apply human decision and finalize recovery execution."""
    agent = RecoveryAgent(db_path=DB_PATH)
    plan = RecoveryPlan.model_validate(state["recovery_plan"])
    decision_dict = state.get("human_decision") or {}
    trail = list(state.get("audit_trail", []))

    dec_enum = ApprovalDecision(decision_dict.get("decision", "APPROVED"))
    sel_id = decision_dict.get("selected_option_id")
    dec_by = decision_dict.get("decided_by", "VP Supply Chain")
    rej_reason = decision_dict.get("rejection_reason")

    updated_plan = agent.apply_human_decision(
        plan=plan,
        decision=dec_enum,
        selected_option_id=sel_id,
        decided_by=dec_by,
        rejection_reason=rej_reason,
    )

    trail.append(f"Workflow Complete: Recovery Plan executed with status={updated_plan.approval_decision.value}.")

    return {
        "recovery_plan": json.loads(updated_plan.model_dump_json()),
        "current_step": "EXECUTION_COMPLETE",
        "audit_trail": trail,
    }


# ─────────────────────────────────────────────────────────────────
# CONDITIONAL ROUTING FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def route_after_signal(state: SentinelState) -> str:
    event_dict = state.get("disruption_event") or {}
    if event_dict.get("status") == SignalStatus.ACTIVE.value:
        return "compute_impact"
    return "handle_ignored"


def route_after_impact(state: SentinelState) -> str:
    impact_dict = state.get("impact_assessment") or {}
    if impact_dict.get("status") == ImpactStatus.SHORTAGE.value:
        return "propose_recovery"
    return "handle_absorbed"


# ─────────────────────────────────────────────────────────────────
# GRAPH BUILDER
# ─────────────────────────────────────────────────────────────────

def build_graph(checkpointer: Optional[SqliteSaver] = None):
    builder = StateGraph(SentinelState)

    # Add Nodes
    builder.add_node("ingest_signal", ingest_signal_node)
    builder.add_node("handle_ignored", handle_ignored_node)
    builder.add_node("compute_impact", compute_impact_node)
    builder.add_node("handle_absorbed", handle_absorbed_node)
    builder.add_node("propose_recovery", propose_recovery_node)
    builder.add_node("human_approval_gate", human_approval_gate_node)
    builder.add_node("execute_decision", execute_decision_node)

    # Add Edges
    builder.add_edge(START, "ingest_signal")

    builder.add_conditional_edges(
        "ingest_signal",
        route_after_signal,
        {
            "compute_impact": "compute_impact",
            "handle_ignored": "handle_ignored",
        }
    )
    builder.add_edge("handle_ignored", END)

    builder.add_conditional_edges(
        "compute_impact",
        route_after_impact,
        {
            "propose_recovery": "propose_recovery",
            "handle_absorbed": "handle_absorbed",
        }
    )
    builder.add_edge("handle_absorbed", END)

    builder.add_edge("propose_recovery", "human_approval_gate")
    builder.add_edge("human_approval_gate", "execute_decision")
    builder.add_edge("execute_decision", END)

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()


def get_compiled_graph(checkpoint_db_path: Path = CHECKPOINT_DB):
    """Factory creating persistent compiled graph connected to graph_state.db."""
    checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(checkpoint_db_path), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return build_graph(checkpointer=checkpointer)
