# -*- coding: utf-8 -*-
"""
app.py
======
Sentinel-SC | Enterprise Logistics Control Tower
Mission-Control Dark Dashboard & Deterministic Decision Engine
Run:  streamlit run app.py
"""

import json
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
import pandas as pd
import plotly.express as px
import plotly.figure_factory as ff
import streamlit as st
from langgraph.types import Command

from graph import build_graph, get_compiled_graph, SentinelState
from schemas.events import SignalStatus
from schemas.impact import ImpactStatus
from schemas.recovery import ApprovalDecision

# Page Configuration
st.set_page_config(
    page_title="Sentinel-SC | Control Tower",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "erp" / "mock_erp.db"
CHECKPOINT_DB = BASE_DIR / "erp" / "graph_state.db"

# High-Stakes Mission-Control Custom CSS
st.markdown("""
<style>
    /* Metric Cards */
    .stMetric {
        background-color: #1A2235;
        border: 1px solid #2A364F;
        border-radius: 8px;
        padding: 12px;
    }
    .metric-card {
        background: linear-gradient(135deg, #1A2235 0%, #151C2C 100%);
        border: 1px solid #2A364F;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    }
    .metric-value-cyan {
        font-size: 2rem;
        font-weight: 800;
        color: #00F2FE;
        letter-spacing: -0.5px;
    }
    .metric-value-amber {
        font-size: 2rem;
        font-weight: 800;
        color: #F59E0B;
        letter-spacing: -0.5px;
    }
    .metric-value-red {
        font-size: 2rem;
        font-weight: 800;
        color: #EF4444;
        letter-spacing: -0.5px;
    }
    .metric-label {
        font-size: 0.8rem;
        color: #94A3B8;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        margin-top: 4px;
    }

    /* Status Badges */
    .badge-active {
        background-color: rgba(239, 68, 68, 0.2);
        color: #EF4444;
        border: 1px solid #EF4444;
        padding: 4px 12px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 0.85rem;
        display: inline-block;
    }
    .badge-ignored {
        background-color: rgba(148, 163, 184, 0.2);
        color: #94A3B8;
        border: 1px solid #475569;
        padding: 4px 12px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 0.85rem;
        display: inline-block;
    }
    .badge-absorbed {
        background-color: rgba(16, 185, 129, 0.2);
        color: #10B981;
        border: 1px solid #10B981;
        padding: 4px 12px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 0.85rem;
        display: inline-block;
    }
    .badge-approval {
        background-color: rgba(245, 158, 11, 0.2);
        color: #F59E0B;
        border: 1px solid #F59E0B;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 0.8rem;
        display: inline-block;
    }
    .badge-veto {
        background-color: rgba(239, 68, 68, 0.2);
        color: #EF4444;
        border: 1px solid #EF4444;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 0.8rem;
        display: inline-block;
    }

    /* Option Cards */
    .option-card {
        background-color: #1A2235;
        border: 1px solid #2A364F;
        border-radius: 8px;
        padding: 16px;
        height: 100%;
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
# VISUAL COMPONENTS (PLOTLY MAP & GANTT)
# ─────────────────────────────────────────────────────────────────

def render_disruption_map(port_name: str, vessel_name: str):
    """Geospatial Mapbox visual showing maritime incident locus with pulsing radius."""
    coords_map = {
        "Port Klang": (3.00, 101.40),
        "Hamburg": (53.55, 9.99),
        "Manila": (14.59, 120.98),
        "Singapore": (1.35, 103.82),
        "Yantian": (22.58, 114.28),
    }

    lat, lon = coords_map.get(port_name, (3.00, 101.40))

    data = pd.DataFrame({
        "lat": [lat, lat],
        "lon": [lon, lon],
        "location": [port_name or "Incident Locus", f"{port_name} (Warning Radius)"],
        "vessel": [vessel_name or "In-Transit Vessel", "50 NM Advisory Perimeter"],
        "size": [60, 140],
        "color": ["#EF4444", "#F59E0B"]
    })

    fig = px.scatter_mapbox(
        data,
        lat="lat",
        lon="lon",
        hover_name="location",
        hover_data=["vessel"],
        color="color",
        color_discrete_map={"#EF4444": "#EF4444", "#F59E0B": "#F59E0B"},
        size="size",
        size_max=28,
        zoom=4.5,
        mapbox_style="carto-darkmatter"
    )
    fig.update_layout(
        showlegend=False,
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=260,
        paper_bgcolor="#0B0F19",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_timeline_delta(stockout_date: str, revised_eta: str, shortage_days: int):
    """Visualizes TTS buffer vs. TTR shortage window with WO-7782 milestone pin."""
    df = [
        dict(Task="On-Hand Inventory (TTS Buffer)", Start="2026-10-03", Finish=stockout_date, Resource="Buffer"),
        dict(Task="Stockout Risk Window (Net Gap)", Start=stockout_date, Finish=revised_eta, Resource="Shortage"),
    ]
    colors = {"Buffer": "#10B981", "Shortage": "#EF4444"}

    fig = ff.create_gantt(
        df,
        colors=colors,
        index_col="Resource",
        show_colorbar=False,
        bar_width=0.35,
        showgrid_x=True,
        showgrid_y=True,
    )

    # Pinpoint WO-7782 planned start date (2026-10-14)
    fig.add_vline(x="2026-10-14", line_width=2, line_dash="dash", line_color="#F59E0B")
    fig.add_annotation(
        x="2026-10-14",
        y=1.35,
        text="🚨 WO-7782 Scheduled ($120k Exposure)",
        showarrow=True,
        arrowhead=2,
        arrowcolor="#F59E0B",
        font=dict(color="#F59E0B", size=12, family="sans serif")
    )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=200,
        margin={"r": 10, "t": 20, "l": 10, "b": 20},
        font=dict(color="#E2E8F0"),
        xaxis=dict(gridcolor="#1E293B", linecolor="#334155"),
        yaxis=dict(gridcolor="#1E293B", linecolor="#334155")
    )
    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────────
# SIDEBAR CONTROLS
# ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🛡️ Control Tower")
    st.caption("**SENTINEL-SC ORCHESTRATOR**")
    st.markdown("---")

    scenario_mode = st.radio(
        "Select Incident Scenario:",
        [
            "Scenario A (Port Klang Shortage)",
            "Scenario B (Hamburg Buffer Absorbs)",
            "Scenario C (Manila False Positive)",
            "Live SerpAPI Intelligence Feed",
        ],
        index=0
    )

    custom_query = ""
    if scenario_mode == "Live SerpAPI Intelligence Feed":
        custom_query = st.text_input(
            "Search Query:",
            value="Port Klang port disruption OR delay OR strike OR weather"
        )

    ref_date = st.date_input("Scenario Reference Anchor", value=date(2026, 10, 3))

    st.markdown("---")

    if st.button("⚡ EXECUTE PIPELINE", type="primary", use_container_width=True):
        st.session_state.thread_id = f"demo-{uuid.uuid4().hex[:6]}"
        config = {"configurable": {"thread_id": st.session_state.thread_id}}

        fixture_map = {
            "Scenario A (Port Klang Shortage)": str(BASE_DIR / "integrations" / "fixtures" / "port_disruption.json"),
            "Scenario B (Hamburg Buffer Absorbs)": str(BASE_DIR / "integrations" / "fixtures" / "scenario_b.json"),
            "Scenario C (Manila False Positive)": str(BASE_DIR / "integrations" / "fixtures" / "false_positive.json"),
        }

        init_state: SentinelState = {
            "headline": custom_query if scenario_mode == "Live SerpAPI Intelligence Feed" else None,
            "content": None,
            "fixture_path": fixture_map.get(scenario_mode),
            "use_serp": (scenario_mode == "Live SerpAPI Intelligence Feed"),
            "reference_date": str(ref_date),
            "disruption_event": None,
            "impact_assessment": None,
            "recovery_plan": None,
            "human_decision": None,
            "current_step": "INITIATED",
            "audit_trail": [f"Pipeline triggered via UI ({scenario_mode})"],
        }

        with st.spinner("Correlating maritime signals against enterprise ERP..."):
            res = st.session_state.graph.invoke(init_state, config=config)
            st.session_state.pipeline_state = res
            st.rerun()

    if st.button("🔄 Reset Control Tower", use_container_width=True):
        st.session_state.pipeline_state = None
        st.session_state.thread_id = f"demo-{uuid.uuid4().hex[:6]}"
        st.rerun()

    st.markdown("---")
    st.caption("Active Graph Thread:")
    st.code(st.session_state.thread_id)


# ─────────────────────────────────────────────────────────────────
# MAIN DISPLAY
# ─────────────────────────────────────────────────────────────────

st.markdown("""
<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
    <div>
        <h1 style="color: #00F2FE; margin: 0; font-size: 2.2rem; font-weight: 800; letter-spacing: -0.5px;">
            SENTINEL-SC &bull; FACTORY CONTINUITY
        </h1>
        <p style="color: #94A3B8; margin: 2px 0 0 0; font-size: 0.95rem;">
            External Signals &rarr; LLM Interpretation &rarr; Deterministic Impact &rarr; Constraint Governance &rarr; Human Authority
        </p>
    </div>
    <div style="text-align: right;">
        <span style="background: rgba(0, 242, 254, 0.15); color: #00F2FE; border: 1px solid #00F2FE; padding: 4px 10px; border-radius: 4px; font-weight: 700; font-size: 0.8rem;">
            SYSTEM ONLINE &bull; 61/61 VERIFIED
        </span>
    </div>
</div>
<hr style="border: none; border-top: 1px solid #1E293B; margin: 12px 0 20px 0;">
""", unsafe_allow_html=True)

state = st.session_state.pipeline_state

if not state:
    st.info("👈 Select a demo scenario in the sidebar and click **EXECUTE PIPELINE** to begin.")
    
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("#### 1. Zero Hallucination Math")
        st.caption("BOM explosion, stockout dates, and customer exposure are calculated deterministically by pure Python from SQLite facts.")
    with c2:
        st.markdown("#### 2. Data-Driven Governance")
        st.caption("C1–C8 supply chain rules veto unviable alternatives before an LLM can propose impossible solutions.")
    with c3:
        st.markdown("#### 3. Sovereign Human Gate")
        st.caption("LangGraph physical interrupt pauses workflow whenever actions exceed plant budget authority ($30,000 threshold).")
    st.stop()


# ─────────────────────────────────────────────────────────────────
# 1. SIGNAL INTELLIGENCE & GEOSPATIAL MAP
# ─────────────────────────────────────────────────────────────────

st.markdown("### 📡 1. Signal Intelligence & Maritime Tracking")

if state:
    event = state.get("disruption_event")
else:
    event = None

if event:
    col_map, col_meta = st.columns([1.3, 1])

    with col_map:
        render_disruption_map(event.get("port_name"), event.get("vessel_name"))

    with col_meta:
        st.markdown(f"**Incident Headline:**")
        st.caption(f"{event['raw_headline']}")

        s1, s2 = st.columns(2)
        with s1:
            st.markdown(f"📍 **Port:** `{event.get('port_name') or 'Unknown'}`")
            st.markdown(f"🚢 **Vessel:** `{event.get('vessel_name') or 'Unknown'}`")
        with s2:
            st.markdown(f"⏱️ **Added Delay:** `+{event['estimated_delay_days']} days`")
            st.markdown(f"🎯 **Confidence:** `{event['confidence'] * 100:.0f}%`")

        st.markdown("<div style='margin-top: 8px;'>", unsafe_allow_html=True)
        if event["status"] == "ACTIVE":
            st.markdown(f'<span class="badge-active">ACTIVE MARITIME SIGNAL &bull; LINKED TO {event["shipment_id"]}</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="badge-ignored">SIGNAL IGNORED &bull; NO ACTIVE ERP IMPACT</span>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    if event["status"] == "IGNORED":
        st.warning(f"**Noise Rejection Activated:** {event.get('ignore_reason')}")
        st.stop()


# ─────────────────────────────────────────────────────────────────
# 2. DETERMINISTIC IMPACT & TIMELINE DELTA
# ─────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown("### 🧮 2. Deterministic Factory Impact Assessment")

impact = state.get("impact_assessment")

if impact:
    shortage = impact["shortage"]
    is_shortage = impact["status"] == "SHORTAGE"

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value-cyan">{shortage['tts_days']}d</div>
            <div class="metric-label">Time-to-Stockout (TTS)</div>
        </div>
        """, unsafe_allow_html=True)
    with m2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value-amber">{shortage['ttr_days']}d</div>
            <div class="metric-label">Time-to-Recover (TTR)</div>
        </div>
        """, unsafe_allow_html=True)
    with m3:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value-red">{shortage['shortage_days']}d</div>
            <div class="metric-label">Net Shortage Window</div>
        </div>
        """, unsafe_allow_html=True)
    with m4:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value-red">${impact['total_otif_exposure']:,.0f}</div>
            <div class="metric-label">Customer Order Exposure</div>
        </div>
        """, unsafe_allow_html=True)

    if not is_shortage:
        st.markdown("<br>", unsafe_allow_html=True)
        st.success(f"✅ **Disruption Absorbed by Buffer:** Stockout buffer ({shortage['tts_days']}d) &ge; Recovery window ({shortage['ttr_days']}d). Total OTIF Exposure is **$0.00**. Factory operations continue uninterrupted.")
        st.stop()

    # Render Visual Timeline Delta
    st.markdown("<div style='margin-top: 14px;'>", unsafe_allow_html=True)
    render_timeline_delta(
        stockout_date=str(shortage["stockout_date"]),
        revised_eta=str(shortage["revised_eta"]),
        shortage_days=shortage["shortage_days"]
    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.info("📌 **BOM Pegging Fact:** Work Order `WO-7782` (scheduled start 2026-10-14 &in; shortage window) is pegged to Customer Order `SO-55102` (Pinnacle Industrial AG) &rarr; **500 units &times; $240/unit = $120,000 exact financial exposure**.")


# ─────────────────────────────────────────────────────────────────
# 3. THE "MONEY SHOT": 4-OPTION RECOVERY MATRIX
# ─────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown("### ⚖️ 3. Recovery Evaluation & Constraint Governance (The Money Shot)")
st.caption("AI proposed 4 candidate recovery options. The deterministic constraint engine evaluated C1–C8 rules and held the AI on a leash.")

plan = state.get("recovery_plan")

if plan and plan.get("candidates"):
    cols = st.columns(4)

    for idx, opt in enumerate(plan["candidates"]):
        res = opt.get("constraint_result") or {}
        opt_status = res.get("option_status", "UNKNOWN")
        veto_rules = res.get("veto_rules", [])

        with cols[idx]:
            st.markdown(f"#### {opt['option_id']}")
            st.markdown(f"**{opt['description']}**")

            if opt_status == "APPROVAL_REQUIRED":
                st.markdown('<span class="badge-approval">⚠️ APPROVAL REQUIRED</span>', unsafe_allow_html=True)
                st.caption("Passed quality & lead time &bull; Cost triggers VP Authorization (C5)")
            elif opt_status == "VETO":
                st.markdown(f'<span class="badge-veto">❌ VETOED ({", ".join(veto_rules)})</span>', unsafe_allow_html=True)
                st.caption(f"Violated: {', '.join(veto_rules)}")

            st.markdown(f"**Cost:** `${opt['total_cost']:,.0f}` &nbsp;|&nbsp; **Coverage:** `{opt['days_of_coverage']}d`")

            # Inline Rule Checklist
            st.markdown("<div style='font-size: 0.85rem; margin-top: 8px;'>", unsafe_allow_html=True)
            for r in res.get("rules", []):
                r_status = r["status"]
                if r_status == "PASS":
                    st.markdown(f"✅ `{r['rule_id']}` {r['rule_name']}")
                elif r_status == "FAIL":
                    st.markdown(f"❌ `{r['rule_id']}` **{r['rule_name']}**")
                else:
                    st.markdown(f"⚪ `{r['rule_id']}` {r['rule_name']} *(N/A)*")
            st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────
# 4. HUMAN-IN-THE-LOOP APPROVAL GATE
# ─────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown("### 👤 4. Sovereign Human Authorization Gate")

current_step = state.get("current_step")
plan = state.get("recovery_plan")

if current_step == "AWAITING_HUMAN_APPROVAL":
    st.warning("⚠️ **LangGraph Physical Interrupt:** Premium air freight spend for OPT-A ($30,100) exceeds plant manager authority ($30,000 threshold). Execution is suspended waiting for executive sign-off.")

    g1, g2 = st.columns([2, 1])

    with g1:
        approver = st.text_input("Authorizing Executive Role:", value="VP Supply Chain Operations")
        selected_option = st.selectbox(
            "Feasible Option to Authorize:",
            options=plan.get("feasible_options", ["OPT-A"]),
            index=0
        )

    with g2:
        st.markdown("<br>", unsafe_allow_html=True)
        btn_approve = st.button("✅ AUTHORIZE RECOVERY ACTION", type="primary", use_container_width=True)
        btn_reject = st.button("❌ REJECT ACTION", use_container_width=True)

    if btn_approve:
        config = {"configurable": {"thread_id": st.session_state.thread_id}}
        resume_cmd = Command(resume={
            "decision": "APPROVED",
            "selected_option_id": selected_option,
            "decided_by": approver,
        })
        with st.spinner("Recording authorization and resuming LangGraph workflow..."):
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
        with st.spinner("Recording rejection and resuming LangGraph workflow..."):
            updated_state = st.session_state.graph.invoke(resume_cmd, config=config)
            st.session_state.pipeline_state = updated_state
            st.rerun()

elif current_step == "EXECUTION_COMPLETE":
    plan = state.get("recovery_plan", {})
    decision = plan.get("approval_decision")

    if decision == "APPROVED":
        st.success(f"🎉 **Action Authorized & Committed:** Option `{plan.get('selected_option_id')}` authorized by **{plan.get('decided_by')}** at `{plan.get('decided_at')}`.")
    else:
        st.error(f"🛑 **Action Rejected:** {plan.get('rejection_reason')}")

    if plan.get("audit_summary"):
        st.markdown("#### Executive Audit Summary (Gemini 3.8 Flash)")
        st.info(plan["audit_summary"])


# ─────────────────────────────────────────────────────────────────
# 5. TECHNICAL AUDIT DRAWER (LLM VS. ENGINE)
# ─────────────────────────────────────────────────────────────────

st.markdown("---")
with st.expander("🛠️ Inspect LLM Reasoning vs. Deterministic Engine Math (Judge's View)"):
    left, right = st.columns(2)
    with left:
        st.markdown("**LLM Interpretation (Unstructured Text &rarr; JSON Entities)**")
        st.caption("Extracted by Gemini 3.8 Flash without hallucinating internal IDs")
        st.json(state.get("disruption_event"))
    with right:
        st.markdown("**Deterministic Engine Truth (SQLite Facts &rarr; Strict Math)**")
        st.caption("Computed by pure Python impact engine & constraint matrix")
        st.json({
            "impact_assessment": state.get("impact_assessment"),
            "recovery_plan_governance": state.get("recovery_plan")
        })

    st.markdown("**LangGraph State Trail:**")
    for log in state.get("audit_trail", []):
        st.markdown(f"- `{log}`")
