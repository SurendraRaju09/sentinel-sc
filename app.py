# -*- coding: utf-8 -*-
"""
app.py
======
Streamlit Interactive Dashboard for Sentinel-SC.
Run:  streamlit run app.py
"""

import json
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
import streamlit as st
from langgraph.types import Command

from graph import build_graph, get_compiled_graph, SentinelState
from schemas.events import SignalStatus
from schemas.impact import ImpactStatus
from schemas.recovery import ApprovalDecision

# Page Configuration
st.set_page_config(
    page_title="Sentinel-SC | Autonomous Supply Chain Disruption Intelligence",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "erp" / "mock_erp.db"
CHECKPOINT_DB = BASE_DIR / "erp" / "graph_state.db"

# Custom Styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0px;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #64748B;
        margin-bottom: 25px;
    }
    .metric-card {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #0F172A;
    }
    .metric-label {
        font-size: 0.85rem;
        color: #64748B;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .status-badge-shortage {
        background-color: #FEE2E2;
        color: #991B1B;
        padding: 4px 10px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .status-badge-absorbed {
        background-color: #DCFCE7;
        color: #166534;
        padding: 4px 10px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .status-badge-ignored {
        background-color: #F1F5F9;
        color: #475569;
        padding: 4px 10px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-approval {
        background-color: #FEF3C7;
        color: #92400E;
        padding: 3px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.8rem;
    }
    .badge-veto {
        background-color: #FEE2E2;
        color: #991B1B;
        padding: 3px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.8rem;
    }
    .badge-pass {
        background-color: #DCFCE7;
        color: #166534;
        padding: 3px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.8rem;
    }
</style>
""", unsafe_allow_html=True)


# Initialize Session State
if "graph" not in st.session_state:
    st.session_state.graph = get_compiled_graph(CHECKPOINT_DB)

if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"demo-{uuid.uuid4().hex[:6]}"

if "pipeline_state" not in st.session_state:
    st.session_state.pipeline_state = None


# ─────────────────────────────────────────────────────────────────
# SIDEBAR CONTROLS
# ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image("https://img.icons8.com/color/96/shield.png", width=64)
    st.title("Control Tower")
    st.markdown("**Sentinel-SC Orchestrator**")
    st.caption("Deterministic Governance & Agentic Intelligence")
    st.markdown("---")

    scenario_mode = st.radio(
        "Select Demo Scenario:",
        [
            "Scenario A (Shortage & Recovery)",
            "Scenario B (Buffer Absorbs)",
            "Scenario C (False Positive / Noise)",
            "Live SerpAPI News Ingestion",
        ],
        index=0
    )

    custom_query = ""
    if scenario_mode == "Live SerpAPI News Ingestion":
        custom_query = st.text_input(
            "Search Query:",
            value="Port Klang port disruption OR delay OR strike OR weather"
        )

    ref_date = st.date_input("Scenario Reference Date", value=date(2026, 10, 3))

    st.markdown("---")
    
    if st.button("🚀 Run Pipeline", type="primary", use_container_width=True):
        st.session_state.thread_id = f"demo-{uuid.uuid4().hex[:6]}"
        config = {"configurable": {"thread_id": st.session_state.thread_id}}

        fixture_map = {
            "Scenario A (Shortage & Recovery)": str(BASE_DIR / "integrations" / "fixtures" / "port_disruption.json"),
            "Scenario B (Buffer Absorbs)": str(BASE_DIR / "integrations" / "fixtures" / "scenario_b.json"),
            "Scenario C (False Positive / Noise)": str(BASE_DIR / "integrations" / "fixtures" / "false_positive.json"),
        }

        init_state: SentinelState = {
            "headline": custom_query if scenario_mode == "Live SerpAPI News Ingestion" else None,
            "content": None,
            "fixture_path": fixture_map.get(scenario_mode),
            "use_serp": (scenario_mode == "Live SerpAPI News Ingestion"),
            "reference_date": str(ref_date),
            "disruption_event": None,
            "impact_assessment": None,
            "recovery_plan": None,
            "human_decision": None,
            "current_step": "INITIATED",
            "audit_trail": [f"Triggered from Streamlit UI ({scenario_mode})"],
        }

        with st.spinner("Executing pipeline through LangGraph..."):
            res = st.session_state.graph.invoke(init_state, config=config)
            st.session_state.pipeline_state = res
            st.rerun()

    if st.button("🔄 Reset / Clear State", use_container_width=True):
        st.session_state.pipeline_state = None
        st.session_state.thread_id = f"demo-{uuid.uuid4().hex[:6]}"
        st.rerun()

    st.markdown("---")
    st.caption("Active Session Thread:")
    st.code(st.session_state.thread_id)


# ─────────────────────────────────────────────────────────────────
# MAIN DISPLAY
# ─────────────────────────────────────────────────────────────────

st.markdown('<div class="main-header">🛡️ Sentinel-SC Supply Chain Orchestrator</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">'
    'Autonomous Maritime Signal Detection &bull; Deterministic ERP Impact Engine &bull; '
    'C1&ndash;C8 Constraint Evaluation &bull; Human-in-the-Loop Authority'
    '</div>',
    unsafe_allow_html=True
)

state = st.session_state.pipeline_state

if not state:
    st.info("👈 Select a demo scenario in the sidebar and click **Run Pipeline** to begin.")
    
    # Welcome Architecture Overview
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("### 1. Signal Intelligence")
        st.write("Ingests external news via SerpAPI or offline fixtures. Extracts port, vessel, and estimated delay without hallucinating internal IDs.")
    with col2:
        st.markdown("### 2. Deterministic Math")
        st.write("Calculates TTS, TTR, and net shortage duration. Traces BOM upward to quantify pegged customer order OTIF exposure ($120k).")
    with col3:
        st.markdown("### 3. Constraint Gate")
        st.write("Evaluates recovery candidates across C1–C8 data-driven rules. Vetoes infeasible options while preserving visibility for human review.")
    st.stop()


# ─────────────────────────────────────────────────────────────────
# SECTION 1: SIGNAL INTELLIGENCE
# ─────────────────────────────────────────────────────────────────

st.markdown("### 📡 1. Signal Intelligence Layer")
event = state.get("disruption_event")

if event:
    status_color = "green" if event["status"] == "ACTIVE" else "gray"
    col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
    
    with col1:
        st.markdown(f"**Headline:** {event['raw_headline']}")
        if event.get("port_name"):
            st.markdown(f"📍 **Port:** `{event['port_name']}` &nbsp;&nbsp;|&nbsp;&nbsp; 🚢 **Vessel:** `{event.get('vessel_name') or 'N/A'}`")
    with col2:
        st.metric("Estimated Delay", f"+{event['estimated_delay_days']} days")
    with col3:
        st.metric("Confidence", f"{event['confidence'] * 100:.0f}%")
    with col4:
        st.markdown("<br>", unsafe_allow_html=True)
        if event["status"] == "ACTIVE":
            st.markdown('<span class="status-badge-shortage">ACTIVE SIGNAL</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="status-badge-ignored">SIGNAL IGNORED</span>', unsafe_allow_html=True)

    if event["status"] == "IGNORED":
        st.warning(f"**Workflow Halted (Noise Filter):** {event.get('ignore_reason')}")
        st.stop()


# ─────────────────────────────────────────────────────────────────
# SECTION 2: DETERMINISTIC IMPACT ENGINE
# ─────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown("### 🧮 2. Deterministic Impact Assessment")
impact = state.get("impact_assessment")

if impact:
    shortage = impact["shortage"]
    is_shortage = impact["status"] == "SHORTAGE"

    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{shortage['tts_days']}d</div>
            <div class="metric-label">Time-to-Stockout (TTS)</div>
        </div>
        """, unsafe_allow_html=True)
    with m2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{shortage['ttr_days']}d</div>
            <div class="metric-label">Time-to-Recover (TTR)</div>
        </div>
        """, unsafe_allow_html=True)
    with m3:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{shortage['shortage_days']}d</div>
            <div class="metric-label">Net Shortage Window</div>
        </div>
        """, unsafe_allow_html=True)
    with m4:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">${impact['total_otif_exposure']:,.0f}</div>
            <div class="metric-label">Total OTIF Exposure</div>
        </div>
        """, unsafe_allow_html=True)
    with m5:
        badge_html = '<span class="status-badge-shortage">SHORTAGE</span>' if is_shortage else '<span class="status-badge-absorbed">ABSORBED</span>'
        st.markdown(f"""
        <div class="metric-card">
            <div style="margin-top: 5px;">{badge_html}</div>
            <div class="metric-label" style="margin-top: 12px;">Engine Verdict</div>
        </div>
        """, unsafe_allow_html=True)

    if not is_shortage:
        st.success(f"✅ **Buffer Absorbs Delay:** On-hand inventory ({shortage['on_hand_qty']} units) covers daily consumption ({shortage['daily_consumption']}/day) for {shortage['tts_days']} days, exceeding the {shortage['ttr_days']}-day recovery time. Financial exposure is **$0.00**. Recovery workflow not required.")
        st.stop()
    else:
        st.markdown(f"**Shortage Arithmetic:** On-hand stockout on `{shortage['stockout_date']}` vs Revised delivery `{shortage['revised_eta']}` &rarr; **{shortage['shortage_days']} days gap**.")
        st.info("📌 **Pegged Exposure Model:** Work order `WO-7782` (start 2026-10-14 &in; shortage window) is pegged to Customer Order `SO-55102` (Pinnacle Industrial AG) &rarr; **500 units &times; $240/unit = $120,000 exact exposure**.")


# ─────────────────────────────────────────────────────────────────
# SECTION 3: THE "MONEY SHOT" RECOVERY MATRIX
# ─────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown("### ⚖️ 3. Recovery Evaluation & Constraint Governance (The Money Shot)")
st.caption("All 4 recovery options evaluated deterministically against C1–C8 rules. Vetoed options remain fully transparent.")

plan = state.get("recovery_plan")

if plan and plan.get("candidates"):
    cols = st.columns(4)

    for idx, opt in enumerate(plan["candidates"]):
        res = opt.get("constraint_result") or {}
        status = res.get("option_status", "UNKNOWN")
        veto_rules = res.get("veto_rules", [])

        with cols[idx]:
            # Header Card
            st.markdown(f"#### {opt['option_id']}")
            st.markdown(f"**{opt['description']}**")

            # Status Badge
            if status == "APPROVAL_REQUIRED":
                st.markdown('<span class="badge-approval">⚠️ APPROVAL REQUIRED</span>', unsafe_allow_html=True)
                st.caption("Passed quality/lead-time checks &bull; Triggers VP Budget Approval (C5)")
            elif status == "VETO":
                st.markdown(f'<span class="badge-veto">❌ VETOED ({", ".join(veto_rules)})</span>', unsafe_allow_html=True)
                st.caption(f"Hard constraint violation: {', '.join(veto_rules)}")
            else:
                st.markdown('<span class="badge-pass">✅ PASS</span>', unsafe_allow_html=True)

            st.write(f"**Total Cost:** ${opt['total_cost']:,.0f}")
            if opt.get("estimated_arrival"):
                st.write(f"**Arrival:** `{opt['estimated_arrival']}`")
            st.write(f"**Coverage:** {opt['days_of_coverage']} days")

            # Expandable Rules Breakdown
            with st.expander(f"Inspect Rules ({opt['option_id']})"):
                for r in res.get("rules", []):
                    r_status = r["status"]
                    icon = "✅" if r_status == "PASS" else ("❌" if r_status == "FAIL" else "⚪")
                    st.markdown(f"**{icon} {r['rule_id']} - {r['rule_name']}**: `{r_status}`")
                    st.caption(f"{r['evidence']}")
                    st.divider()


# ─────────────────────────────────────────────────────────────────
# SECTION 4: HUMAN-IN-THE-LOOP APPROVAL GATE
# ─────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown("### 👤 4. Human-in-the-Loop Decision Gate")

current_step = state.get("current_step")
plan = state.get("recovery_plan")

if current_step == "AWAITING_HUMAN_APPROVAL":
    st.warning("⚠️ **Human Authorization Required:** Premium air freight spend for OPT-A ($30,100) exceeds local plant authority ($30,000) to protect $120,000 OTIF customer order.")

    gate_col1, gate_col2 = st.columns([2, 1])

    with gate_col1:
        approver = st.text_input("Approver Identity / Role:", value="VP Supply Chain Operations")
        selected_option = st.selectbox(
            "Select Recovery Option to Authorize:",
            options=plan.get("feasible_options", ["OPT-A"]),
            index=0
        )

    with gate_col2:
        st.markdown("<br>", unsafe_allow_html=True)
        btn_approve = st.button("✅ APPROVE RECOVERY ACTION", type="primary", use_container_width=True)
        btn_reject = st.button("❌ REJECT PROPOSAL", use_container_width=True)

    if btn_approve:
        config = {"configurable": {"thread_id": st.session_state.thread_id}}
        resume_cmd = Command(resume={
            "decision": "APPROVED",
            "selected_option_id": selected_option,
            "decided_by": approver,
        })
        with st.spinner("Authorizing recovery action and resuming LangGraph..."):
            updated_state = st.session_state.graph.invoke(resume_cmd, config=config)
            st.session_state.pipeline_state = updated_state
            st.rerun()

    if btn_reject:
        config = {"configurable": {"thread_id": st.session_state.thread_id}}
        resume_cmd = Command(resume={
            "decision": "REJECTED",
            "selected_option_id": None,
            "decided_by": approver,
            "rejection_reason": "Rejected by supervisor in Streamlit console.",
        })
        with st.spinner("Recording rejection and resuming LangGraph..."):
            updated_state = st.session_state.graph.invoke(resume_cmd, config=config)
            st.session_state.pipeline_state = updated_state
            st.rerun()

elif current_step == "EXECUTION_COMPLETE":
    plan = state.get("recovery_plan", {})
    decision = plan.get("approval_decision")

    if decision == "APPROVED":
        st.success(f"🎉 **Recovery Action Executed:** Option `{plan.get('selected_option_id')}` authorized by **{plan.get('decided_by')}** at `{plan.get('decided_at')}`.")
    else:
        st.error(f"🛑 **Recovery Action Rejected:** {plan.get('rejection_reason')}")

    if plan.get("audit_summary"):
        st.markdown("#### Executive Audit Summary (AI Narrative)")
        st.info(plan["audit_summary"])


# ─────────────────────────────────────────────────────────────────
# SECTION 5: AUDIT LOG & RAW STATE DRAWER
# ─────────────────────────────────────────────────────────────────

with st.expander("📜 System Audit Trail & State Inspection"):
    st.markdown("**LangGraph State Trail:**")
    for log in state.get("audit_trail", []):
        st.markdown(f"- `{log}`")
    st.markdown("**Complete State JSON:**")
    st.json(state)
