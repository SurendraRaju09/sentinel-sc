# -*- coding: utf-8 -*-
"""
api.py
======
FastAPI Backend for Sentinel-SC Supply Chain Disruption Orchestration.

Endpoints:
  - GET  /health                        : Health check & version
  - POST /api/disruption/trigger        : Trigger graph with fixture, text, or live SerpAPI
  - GET  /api/disruption/status/{thread}: Get current graph execution state
  - POST /api/disruption/approve        : Resume human gate with approval/rejection decision
  - GET  /api/erp/summary               : Return core ERP facts for inspection
"""

import json
import logging
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from langgraph.types import Command

from graph import build_graph, get_compiled_graph, SentinelState
from schemas.events import SignalStatus
from schemas.impact import ImpactStatus
from schemas.recovery import ApprovalDecision

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SentinelAPI")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "erp" / "mock_erp.db"
CHECKPOINT_DB = BASE_DIR / "erp" / "graph_state.db"

app = FastAPI(
    title="Sentinel-SC API",
    description="Agentic Supply Chain Disruption Intelligence & Deterministic Recovery Engine",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Persistent compiled graph
compiled_graph = get_compiled_graph(CHECKPOINT_DB)


# ─────────────────────────────────────────────────────────────────
# REQUEST / RESPONSE SCHEMAS
# ─────────────────────────────────────────────────────────────────

class TriggerRequest(BaseModel):
    scenario: Optional[str] = Field(None, description="'A' (Shortage), 'B' (Absorbed), 'C' (False Positive)")
    fixture_path: Optional[str] = Field(None, description="Path to offline JSON fixture")
    headline: Optional[str] = Field(None, description="Custom headline or search query")
    content: Optional[str] = Field(None, description="Custom article snippet or content")
    use_serp: bool = Field(False, description="Whether to query live SerpAPI Google News")
    reference_date: str = Field("2026-10-03", description="Scenario reference anchor date")
    thread_id: Optional[str] = Field(None, description="Optional custom session thread ID")


class ApprovalRequest(BaseModel):
    thread_id: str = Field(..., description="Active thread ID waiting at Human Gate")
    decision: str = Field("APPROVED", description="'APPROVED' or 'REJECTED'")
    selected_option_id: Optional[str] = Field("OPT-A", description="Selected option ID (must be feasible)")
    decided_by: str = Field("VP Supply Chain Operations", description="Authorizer role / name")
    rejection_reason: Optional[str] = Field(None, description="Reason if decision is REJECTED")


# ─────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "system": "Sentinel-SC",
        "timestamp": datetime.utcnow().isoformat(),
        "database": DB_PATH.exists(),
        "checkpointer": CHECKPOINT_DB.exists()
    }


@app.post("/api/disruption/trigger")
def trigger_pipeline(req: TriggerRequest):
    """
    Triggers the LangGraph pipeline. Runs until either completion or the human approval gate.
    """
    thread_id = req.thread_id or f"thread-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    # Determine inputs based on scenario shorthand or explicit paths
    fixture_path = req.fixture_path
    if req.scenario == "A":
        fixture_path = str(BASE_DIR / "integrations" / "fixtures" / "port_disruption.json")
    elif req.scenario == "B":
        fixture_path = str(BASE_DIR / "integrations" / "fixtures" / "scenario_b.json")
    elif req.scenario == "C":
        fixture_path = str(BASE_DIR / "integrations" / "fixtures" / "false_positive.json")

    init_state: SentinelState = {
        "headline": req.headline,
        "content": req.content,
        "fixture_path": fixture_path,
        "use_serp": req.use_serp,
        "reference_date": req.reference_date,
        "disruption_event": None,
        "impact_assessment": None,
        "recovery_plan": None,
        "human_decision": None,
        "current_step": "INITIATED",
        "audit_trail": [f"Pipeline triggered via API (thread={thread_id})"],
    }

    try:
        # Run graph until end or interrupt
        result_state = compiled_graph.invoke(init_state, config=config)
        
        is_awaiting_approval = result_state.get("current_step") == "AWAITING_HUMAN_APPROVAL"
        
        return {
            "thread_id": thread_id,
            "current_step": result_state.get("current_step"),
            "awaiting_approval": is_awaiting_approval,
            "disruption_event": result_state.get("disruption_event"),
            "impact_assessment": result_state.get("impact_assessment"),
            "recovery_plan": result_state.get("recovery_plan"),
            "audit_trail": result_state.get("audit_trail", []),
        }
    except Exception as e:
        logger.error(f"Error executing graph pipeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/disruption/status/{thread_id}")
def get_pipeline_status(thread_id: str):
    """
    Fetches persisted execution state from graph_state.db checkpointer.
    """
    config = {"configurable": {"thread_id": thread_id}}
    state_snapshot = compiled_graph.get_state(config)
    
    if not state_snapshot or not state_snapshot.values:
        raise HTTPException(status_code=404, detail=f"No state found for thread {thread_id}")

    values = state_snapshot.values
    is_awaiting = values.get("current_step") == "AWAITING_HUMAN_APPROVAL"

    return {
        "thread_id": thread_id,
        "current_step": values.get("current_step"),
        "awaiting_approval": is_awaiting,
        "next": list(state_snapshot.next),
        "disruption_event": values.get("disruption_event"),
        "impact_assessment": values.get("impact_assessment"),
        "recovery_plan": values.get("recovery_plan"),
        "audit_trail": values.get("audit_trail", []),
    }


@app.post("/api/disruption/approve")
def approve_recovery_action(req: ApprovalRequest):
    """
    Submits Human Gate approval or rejection, resuming the LangGraph workflow.
    """
    config = {"configurable": {"thread_id": req.thread_id}}
    
    state_snapshot = compiled_graph.get_state(config)
    if not state_snapshot or not state_snapshot.values:
        raise HTTPException(status_code=404, detail=f"No active state found for thread {req.thread_id}")

    resume_payload = {
        "decision": req.decision,
        "selected_option_id": req.selected_option_id,
        "decided_by": req.decided_by,
        "rejection_reason": req.rejection_reason,
    }

    try:
        # Resume the interrupted graph node
        result_state = compiled_graph.invoke(Command(resume=resume_payload), config=config)

        return {
            "thread_id": req.thread_id,
            "current_step": result_state.get("current_step"),
            "approval_status": req.decision,
            "recovery_plan": result_state.get("recovery_plan"),
            "audit_trail": result_state.get("audit_trail", []),
        }
    except Exception as e:
        logger.error(f"Error resuming graph execution: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/erp/summary")
def get_erp_summary():
    """
    Returns core database entities for the UI data viewer.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    materials = [dict(r) for r in conn.execute("SELECT * FROM materials").fetchall()]
    suppliers = [dict(r) for r in conn.execute("SELECT * FROM suppliers").fetchall()]
    inventory = [dict(r) for r in conn.execute("SELECT * FROM inventory").fetchall()]
    shipments = [dict(r) for r in conn.execute("SELECT * FROM shipments").fetchall()]
    work_orders = [dict(r) for r in conn.execute("SELECT * FROM work_orders").fetchall()]
    rules = [dict(r) for r in conn.execute("SELECT * FROM rules").fetchall()]
    conn.close()

    return {
        "materials": materials,
        "suppliers": suppliers,
        "inventory": inventory,
        "shipments": shipments,
        "work_orders": work_orders,
        "rules": rules,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
