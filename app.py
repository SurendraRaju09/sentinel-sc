# -*- coding: utf-8 -*-
"""
app.py
======
SENTINEL SC | FACTORY CONTINUITY DECISION LAYER
Autonomous Supply Chain Disruption Resolution & Recovery Orchestration Platform

Run: streamlit run app.py
"""

import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from langgraph.types import Command

from graph import get_compiled_graph, SentinelState
from schemas.events import SignalStatus
from schemas.impact import ImpactStatus
from schemas.recovery import ApprovalDecision

# ─────────────────────────────────────────────────────────────────
# PAGE CONFIGURATION
# ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SENTINEL SC | Factory Continuity Decision Layer",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "erp" / "mock_erp.db"
CHECKPOINT_DB = BASE_DIR / "erp" / "graph_state.db"


def render_html(html_str: str):
    cleaned = "\n".join(line.lstrip() for line in html_str.strip().splitlines())
    st.markdown(cleaned, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────
# ULTRA-POLISHED ENTERPRISE DARK NAVY DESIGN SYSTEM (FULL OVERRIDES)
# ─────────────────────────────────────────────────────────────────
render_html("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700;800&display=swap');

/* Hide Streamlit default header bar to avoid top clipping */
header[data-testid="stHeader"] {
    display: none !important;
}

/* Global Container Dark Overrides */
html, body, [class*="css"], .stApp, .main, [data-testid="stAppViewContainer"] {
    font-family: 'Inter', -apple-system, sans-serif !important;
    background-color: #080C14 !important;
    color: #F3F4F6 !important;
}

.block-container {
    padding-top: 0.5rem !important;
    padding-bottom: 5rem !important;
    max-width: 99% !important;
}

/* Sidebar Dark Styling */
[data-testid="stSidebar"], [data-testid="stSidebarContent"] {
    background-color: #0B0F19 !important;
    border-right: 1px solid rgba(255, 255, 255, 0.08) !important;
}

[data-testid="stSidebar"] * {
    color: #E5E7EB !important;
}

/* Streamlit Native Expander Override */
div[data-testid="stExpander"] {
    background: rgba(17, 24, 39, 0.7) !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 10px !important;
    margin-bottom: 12px !important;
}
div[data-testid="stExpander"] summary {
    color: #F9FAFB !important;
    font-weight: 700 !important;
    font-size: 0.85rem !important;
}

/* Streamlit Selectbox, Inputs & Radio Overrides */
.stTextInput > div > div > input, .stSelectbox > div > div, div[data-baseweb="select"] > div {
    border-radius: 8px !important;
    border: 1px solid rgba(255, 255, 255, 0.15) !important;
    padding: 8px 12px !important;
    font-size: 0.85rem !important;
    color: #FFFFFF !important;
    background: #111827 !important;
}
.stTextInput > div > div > input:focus, .stSelectbox > div > div:focus {
    border-color: #3B82F6 !important;
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.3) !important;
}

/* Radio button text and options */
.stRadio label {
    color: #D1D5DB !important;
    font-size: 0.83rem !important;
    font-weight: 600 !important;
}

/* Streamlit Native Tabs Styling */
.stTabs [data-baseweb="tab-list"] {
    background-color: #111827 !important;
    border-radius: 8px !important;
    padding: 4px !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    gap: 4px !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 6px !important;
    color: #9CA3AF !important;
    font-weight: 700 !important;
    font-size: 0.8rem !important;
    padding: 6px 14px !important;
    border: none !important;
}
.stTabs [aria-selected="true"] {
    background-color: #2563EB !important;
    color: #FFFFFF !important;
}

/* Dataframe Table Dark Theme */
.stDataFrame, div[data-testid="stDataFrame"] {
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 10px !important;
    background: #111827 !important;
}

/* Streamlit Button Overrides */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%) !important;
    color: #FFFFFF !important;
    border: 1px solid rgba(59, 130, 246, 0.5) !important;
    border-radius: 8px !important;
    font-weight: 700 !important;
    font-size: 0.85rem !important;
    box-shadow: 0 4px 14px rgba(37, 99, 235, 0.3) !important;
    transition: all 0.2s ease !important;
}
.stButton > button[kind="primary"]:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 6px 20px rgba(37, 99, 235, 0.5) !important;
    border-color: #60A5FA !important;
}

.stButton > button[kind="secondary"], .stButton > button {
    background: #1F2937 !important;
    color: #E5E7EB !important;
    border: 1px solid rgba(255, 255, 255, 0.12) !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.82rem !important;
    transition: all 0.2s ease !important;
}
.stButton > button[kind="secondary"]:hover, .stButton > button:hover {
    background: #374151 !important;
    color: #FFFFFF !important;
    border-color: rgba(255, 255, 255, 0.25) !important;
}

/* Brand Header Container */
.brand-shell {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
    background: rgba(17, 24, 39, 0.8);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    margin-bottom: 16px;
}
.brand-icon-box {
    background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%);
    width: 36px;
    height: 36px;
    border-radius: 9px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.2rem;
    box-shadow: 0 4px 14px rgba(37, 99, 235, 0.4);
}
.brand-title-text {
    font-weight: 900;
    font-size: 1.05rem;
    letter-spacing: 0.05em;
    color: #FFFFFF;
}
.brand-subtitle-text {
    font-size: 0.62rem;
    color: #9CA3AF;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 700;
}

/* Sidebar Nav Buttons */
.sidebar-status-footer {
    padding: 10px 14px;
    background: rgba(17, 24, 39, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    font-size: 0.72rem;
    font-weight: 700;
    color: #10B981;
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 14px;
}

/* Glass Card */
.glass-card {
    background: rgba(17, 24, 39, 0.75);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 16px 20px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
    margin-bottom: 14px;
    backdrop-filter: blur(12px);
}
.glass-header {
    font-size: 0.8rem;
    font-weight: 800;
    color: #F3F4F6;
    display: flex;
    justify-content: space-between;
    align-items: center;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-bottom: 10px;
}

/* Disruption Banner */
.disruption-hero-banner {
    background: linear-gradient(135deg, rgba(30, 27, 75, 0.85) 0%, rgba(17, 24, 39, 0.95) 100%);
    border: 1px solid rgba(239, 68, 68, 0.35);
    border-left: 5px solid #EF4444;
    border-radius: 12px;
    padding: 16px 22px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 20px;
}
.disruption-hero-title {
    font-size: 1.05rem;
    font-weight: 800;
    color: #FCA5A5;
    display: flex;
    align-items: center;
    gap: 10px;
}
.disruption-hero-sub {
    font-size: 0.82rem;
    color: #D1D5DB;
    margin-top: 6px;
    line-height: 1.5;
}
.exposure-highlight-card {
    background: rgba(239, 68, 68, 0.12);
    border: 1px solid rgba(239, 68, 68, 0.3);
    border-radius: 10px;
    padding: 12px 18px;
    text-align: right;
    min-width: 200px;
}
.exposure-highlight-lbl {
    font-size: 0.62rem;
    font-weight: 800;
    color: #FCA5A5;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.exposure-highlight-val {
    font-size: 1.55rem;
    font-weight: 900;
    color: #EF4444;
    font-family: 'JetBrains Mono', monospace;
    white-space: nowrap;
    letter-spacing: -0.02em;
    text-shadow: 0 0 16px rgba(239, 68, 68, 0.3);
}

/* KPI Tiles Dark System */
.kpi-card-dark {
    background: rgba(17, 24, 39, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 14px 16px;
    min-height: 120px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    transition: all 0.2s ease;
    box-shadow: 0 4px 16px rgba(0,0,0,0.25);
}
.kpi-card-dark:hover {
    border-color: #3B82F6;
    box-shadow: 0 0 20px rgba(59, 130, 246, 0.25);
    transform: translateY(-2px);
}
.kpi-card-dominant {
    background: linear-gradient(135deg, rgba(45, 20, 30, 0.9) 0%, rgba(17, 24, 39, 0.9) 100%);
    border: 1px solid rgba(239, 68, 68, 0.45);
    box-shadow: 0 0 24px rgba(239, 68, 68, 0.2);
}
.kpi-card-lbl {
    font-size: 0.62rem;
    font-weight: 800;
    color: #9CA3AF;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    display: flex;
    justify-content: space-between;
}
.kpi-card-val {
    font-size: 1.3rem;
    font-weight: 900;
    color: #FFFFFF;
    margin: 4px 0 2px;
    white-space: nowrap;
    font-family: 'JetBrains Mono', monospace;
}
.kpi-card-val-red {
    font-size: 1.3rem;
    font-weight: 900;
    color: #EF4444;
    margin: 4px 0 2px;
    white-space: nowrap;
    font-family: 'JetBrains Mono', monospace;
    text-shadow: 0 0 12px rgba(239, 68, 68, 0.3);
}
.kpi-card-sub {
    font-size: 0.7rem;
    color: #6B7280;
    display: flex;
    justify-content: space-between;
    white-space: nowrap;
}

/* Timeline Container */
.timeline-container {
    background: rgba(17, 24, 39, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 16px;
}

/* Recovery Matrix Grid */
.recovery-matrix-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 16px;
}
.recovery-card {
    background: rgba(17, 24, 39, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 14px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    min-height: 220px;
    transition: all 0.2s ease;
}
.recovery-card-recommended {
    border: 2px solid #3B82F6 !important;
    background: linear-gradient(135deg, rgba(30, 58, 138, 0.35) 0%, rgba(17, 24, 39, 0.95) 100%) !important;
    box-shadow: 0 0 28px rgba(59, 130, 246, 0.3) !important;
}
.recovery-card-vetoed {
    border: 1px solid rgba(239, 68, 68, 0.25);
    background: rgba(31, 41, 55, 0.35);
    opacity: 0.9;
}

/* Badges */
.pill-dark-green { background: rgba(16, 185, 129, 0.15); color: #34D399; border: 1px solid rgba(16, 185, 129, 0.3); font-weight: 700; font-size: 0.68rem; padding: 3px 8px; border-radius: 6px; }
.pill-dark-amber { background: rgba(245, 158, 11, 0.15); color: #FBBF24; border: 1px solid rgba(245, 158, 11, 0.3); font-weight: 700; font-size: 0.68rem; padding: 3px 8px; border-radius: 6px; }
.pill-dark-red   { background: rgba(239, 68, 68, 0.15);  color: #FCA5A5; border: 1px solid rgba(239, 68, 68, 0.3);  font-weight: 700; font-size: 0.68rem; padding: 3px 8px; border-radius: 6px; }
.pill-dark-blue  { background: rgba(59, 130, 246, 0.15); color: #60A5FA; border: 1px solid rgba(59, 130, 246, 0.3); font-weight: 700; font-size: 0.68rem; padding: 3px 8px; border-radius: 6px; }

/* Approval Command Panel */
.approval-command-box {
    background: linear-gradient(135deg, rgba(30, 27, 75, 0.95) 0%, rgba(17, 24, 39, 0.95) 100%);
    border: 2px solid #F59E0B;
    border-radius: 14px;
    padding: 20px 24px;
    margin-top: 18px;
    box-shadow: 0 0 32px rgba(245, 158, 11, 0.2);
}

.mono-font { font-family: 'JetBrains Mono', monospace; }
</style>
""")

# ─────────────────────────────────────────────────────────────────
# SESSION STATE INITIALIZATION
# ─────────────────────────────────────────────────────────────────
defaults = {
    "authenticated": False,
    "user_role": "VP Supply Chain Operations",
    "view_mode": "VP",
    "active_nav": "Overview",
    "show_evidence_drawer": False,
    "active_formula_modal": None,
    "show_confirm_modal": False,
    "decision_executed": None,
    "pipeline_state": None,
    "thread_id": f"demo-{uuid.uuid4().hex[:6]}",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

if "graph" not in st.session_state:
    st.session_state.graph = get_compiled_graph(CHECKPOINT_DB)


# ─────────────────────────────────────────────────────────────────
# HELPER DATA BUILDERS (sepr_api alignment)
# ─────────────────────────────────────────────────────────────────
def build_attribution_math(impact: dict | None) -> dict:
    if not impact:
        return {
            "on_hand_qty": 400, "daily_consumption": 80,
            "tts_days": 5, "tts_formula": "TTS = floor(400 / 80) = 5 Days",
            "stockout_date": "2026-10-08", "planned_arrival_date": "2026-10-11",
            "baseline_gap_days": 3,
            "baseline_explanation": "Planned arrival (Oct 11) was already 3 days after stockout (Oct 08)",
            "disruption_delay_days": 7, "total_shortage_gap_days": 10,
            "gap_equation": "Net Gap = Baseline (3d) + Storm Disruption (7d) = 10 Days",
        }

    shortage = impact.get("shortage", {})
    tts_days = shortage.get("tts_days", 5)
    ttr_days = shortage.get("ttr_days", 15)
    net_shortage = shortage.get("shortage_days", 10)
    on_hand = shortage.get("on_hand_qty", 400)
    daily = shortage.get("daily_consumption", 80)
    stockout_date = shortage.get("stockout_date", "2026-10-08")

    planned_arrival_days = 8
    baseline_gap = planned_arrival_days - tts_days
    disruption_delay = ttr_days - planned_arrival_days

    return {
        "on_hand_qty": on_hand,
        "daily_consumption": daily,
        "tts_days": tts_days,
        "tts_formula": f"TTS = floor({on_hand:.0f} / {daily:.0f}) = {tts_days} Days",
        "stockout_date": stockout_date,
        "planned_arrival_date": "2026-10-11",
        "baseline_gap_days": baseline_gap,
        "baseline_explanation": f"Planned arrival (Oct 11) was already {baseline_gap} days after stockout ({stockout_date})",
        "disruption_delay_days": disruption_delay,
        "total_shortage_gap_days": net_shortage,
        "gap_equation": f"Net Gap = Baseline ({baseline_gap}d) + Storm Disruption ({disruption_delay}d) = {net_shortage} Days",
    }


def build_verifiable_exposure(impact: dict | None) -> dict:
    if not impact or not impact.get("affected_work_orders"):
        return {
            "work_order_id": "WO-7782", "wo_planned_start": "2026-10-14",
            "wo_planned_end": "2026-10-22", "wo_duration_days": 3,
            "customer_order_id": "SO-55102", "customer_name": "Pinnacle Industrial AG",
            "so_due_date": "2026-10-20", "delayed_completion_date": "2026-10-21",
            "days_past_sla": 2, "otif_exposure_usd": 120000.0,
            "proof_narrative": "500 Units × $240 Unit Price = $120,000.00 OTIF Exposure. WO-7782 delayed completion (Oct 21) exceeds Pinnacle Industrial AG SLA (Oct 20) by 2 days.",
        }

    wos = impact.get("affected_work_orders", [])
    pegged_wo = next((w for w in wos if w.get("co_id")), wos[0] if wos else {})
    exposure = impact.get("total_otif_exposure", 120000.0)

    co_id = pegged_wo.get("co_id", "SO-55102")
    wo_id = pegged_wo.get("wo_id", "WO-7782")
    wo_start = str(pegged_wo.get("planned_start", "2026-10-14"))
    wo_end = str(pegged_wo.get("due_date", "2026-10-22"))

    try:
        _conn = sqlite3.connect(str(DB_PATH))
        _conn.row_factory = sqlite3.Row
        co_row = _conn.execute("SELECT customer_name, due_date, qty, unit_price FROM customer_orders WHERE co_id = ?", (co_id,)).fetchone()
        _conn.close()
        if co_row:
            customer_name = co_row["customer_name"]
            so_due = co_row["due_date"]
            qty = co_row["qty"]
            price = co_row["unit_price"]
            proof_narrative = f"{qty:.0f} Units × ${price:.2f} Unit Price = ${qty*price:,.2f} OTIF Exposure. WO {wo_id} delayed completion exceeds {customer_name} SLA ({so_due}) by 2 days."
            return {
                "work_order_id": wo_id, "wo_planned_start": wo_start, "wo_planned_end": wo_end,
                "wo_duration_days": 3, "customer_order_id": co_id, "customer_name": customer_name,
                "so_due_date": so_due, "delayed_completion_date": "2026-10-21", "days_past_sla": 2,
                "otif_exposure_usd": float(exposure), "proof_narrative": proof_narrative,
            }
    except Exception:
        pass

    return {
        "work_order_id": wo_id, "wo_planned_start": wo_start, "wo_planned_end": wo_end,
        "wo_duration_days": 3, "customer_order_id": co_id, "customer_name": "Pinnacle Industrial AG",
        "so_due_date": "2026-10-20", "delayed_completion_date": "2026-10-21", "days_past_sla": 2,
        "otif_exposure_usd": float(exposure), "proof_narrative": f"$120,000.00 OTIF Exposure from pegged customer order.",
    }


def build_option_rules(plan: dict | None) -> dict[str, dict]:
    if not plan or not plan.get("candidates"):
        return {}

    result = {}
    for cand in plan["candidates"]:
        opt_id = cand.get("option_id", "")
        cr = cand.get("constraint_result") or {}
        rules_list = cr.get("rules", [])
        rules_dict = {}
        for r in rules_list:
            rid = r.get("rule_id", "")
            status = r.get("status", "NA")
            evidence = r.get("evidence", "")
            threshold = r.get("threshold", "")
            actual = r.get("actual", "")
            rules_dict[rid] = {
                "pass": status == "PASS",
                "fail": status == "FAIL",
                "na": status == "NA",
                "warning": (rid == "C5" and cr.get("option_status") == "APPROVAL_REQUIRED"),
                "value": actual or "—",
                "threshold": threshold or "—",
                "reason": evidence or f"{rid} rule evaluation",
                "status": status,
            }
        result[opt_id] = rules_dict
    return result


# ─────────────────────────────────────────────────────────────────
# 0. WORKSPACE LANDING & SIGN-IN SCREEN
# ─────────────────────────────────────────────────────────────────
if not st.session_state.authenticated:
    col_hero, col_login = st.columns([1.4, 1], gap="large")

    with col_hero:
        render_html("""
<div style="background: linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(17, 24, 39, 0.95) 100%), url('https://images.unsplash.com/photo-1586528116311-ad8dd3c8310d?auto=format&fit=crop&w=1600&q=80'); background-size: cover; background-position: center; border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 16px; padding: 44px; color: white; min-height: 640px; display: flex; flex-direction: column; justify-content: space-between; box-shadow: 0 16px 48px rgba(0, 0, 0, 0.4);">
  <div>
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div style="display:flex;align-items:center;gap:12px;">
        <div class="brand-icon-box">🛡️</div>
        <div>
          <div class="brand-title-text">SENTINEL SC</div>
          <div class="brand-subtitle-text">FACTORY CONTINUITY DECISION LAYER</div>
        </div>
      </div>
      <div style="background:rgba(30,58,138,0.4);border:1px solid rgba(59,130,246,0.3);padding:4px 12px;border-radius:9999px;font-size:0.7rem;font-weight:700;color:#60A5FA;">
        ● ENTERPRISE EDITION v1.0
      </div>
    </div>
    
    <div style="margin-top:40px;">
      <div style="font-size:3.2rem;font-weight:900;line-height:1.05;margin:18px 0 14px;letter-spacing:-0.03em;background:linear-gradient(180deg, #FFFFFF 0%, #CBD5E1 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;">Turn disruption<br>into a decision.</div>
      <div style="font-size:1.05rem;color:#9CA3AF;line-height:1.5;margin-bottom:32px;">From external disruption signals to quantified factory impact and human-approved recovery.</div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(2, 1fr);gap:12px;margin-bottom:32px;">
      <div style="background:rgba(31, 41, 55, 0.6);border:1px solid rgba(255, 255, 255, 0.12);padding:12px 16px;border-radius:10px;font-size:0.78rem;font-weight:700;color:#E5E7EB;display:flex;align-items:center;gap:10px;"><span>📡 Live Disruption Intelligence</span></div>
      <div style="background:rgba(31, 41, 55, 0.6);border:1px solid rgba(255, 255, 255, 0.12);padding:12px 16px;border-radius:10px;font-size:0.78rem;font-weight:700;color:#E5E7EB;display:flex;align-items:center;gap:10px;"><span>📐 Deterministic Impact Engine</span></div>
      <div style="background:rgba(31, 41, 55, 0.6);border:1px solid rgba(255, 255, 255, 0.12);padding:12px 16px;border-radius:10px;font-size:0.78rem;font-weight:700;color:#E5E7EB;display:flex;align-items:center;gap:10px;"><span>🛡️ Constraint Governance (C1–C8)</span></div>
      <div style="background:rgba(31, 41, 55, 0.6);border:1px solid rgba(255, 255, 255, 0.12);padding:12px 16px;border-radius:10px;font-size:0.78rem;font-weight:700;color:#E5E7EB;display:flex;align-items:center;gap:10px;"><span>👤 Human Authorization Gate</span></div>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;background:rgba(17,24,39,0.9);border:1px solid rgba(255,255,255,0.08);padding:18px;border-radius:12px;">
    <div><div style="font-size:1.4rem;font-weight:900;color:#FFFFFF;">100%</div><div style="font-size:0.6rem;color:#9CA3AF;text-transform:uppercase;font-weight:700;margin-top:2px;">Deterministic Proof</div></div>
    <div><div style="font-size:1.4rem;font-weight:900;color:#34D399;">$120k</div><div style="font-size:0.6rem;color:#9CA3AF;text-transform:uppercase;font-weight:700;margin-top:2px;">Max Exposure Protect</div></div>
    <div><div style="font-size:1.4rem;font-weight:900;color:#60A5FA;">C1–C8</div><div style="font-size:0.6rem;color:#9CA3AF;text-transform:uppercase;font-weight:700;margin-top:2px;">Hard Rule Matrix</div></div>
    <div><div style="font-size:1.4rem;font-weight:900;color:#FBBF24;">VP Gate</div><div style="font-size:0.6rem;color:#9CA3AF;text-transform:uppercase;font-weight:700;margin-top:2px;">Human In The Loop</div></div>
  </div>
</div>
""")

    with col_login:
        render_html("""
<div style="background:#111827;border:1px solid rgba(255, 255, 255, 0.1);border-radius:16px;padding:36px 32px;box-shadow:0 12px 40px rgba(0, 0, 0, 0.3);">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;">
    <div style="background:#1F2937;width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:1.1rem;">🛡️</div>
    <div>
      <div style="font-weight:900;font-size:1.05rem;color:#FFFFFF;">SENTINEL SC</div>
      <div style="font-size:0.6rem;color:#9CA3AF;text-transform:uppercase;font-weight:700;">ENTERPRISE WORKSPACE GATEWAY</div>
    </div>
  </div>
  <div style="margin-bottom:20px;">
    <div style="font-size:0.65rem;font-weight:800;color:#3B82F6;letter-spacing:0.08em;text-transform:uppercase;">AUTHENTICATION</div>
    <h2 style="font-size:1.45rem;font-weight:800;color:#FFFFFF;margin:4px 0 6px;">Sign in to workspace</h2>
    <div style="font-size:0.8rem;color:#9CA3AF;">Access your factory continuity decision layer.</div>
  </div>
</div>
""")
        st.text_input("Corporate Email", value="vp.supplychain@sentinel-sc.com", key="login_email")
        st.text_input("Password", value="••••••••", type="password", key="login_pass")

        l1, l2 = st.columns(2)
        with l1:
            if st.button("Sign In →", type="primary", use_container_width=True, key="btn_signin"):
                st.session_state.authenticated = True
                st.session_state.view_mode = "VP"
                st.rerun()
        with l2:
            if st.button("Enterprise SSO →", use_container_width=True, key="btn_sso"):
                st.session_state.authenticated = True
                st.session_state.view_mode = "VP"
                st.rerun()

        render_html("<hr style='border:none;border-top:1px solid rgba(255,255,255,0.08);margin:20px 0 16px;'>")
        render_html("""
<div style="font-size:0.65rem;font-weight:800;color:#9CA3AF;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;">
DEMO PERSONA PRESETS
</div>
""")
        r1, r2 = st.columns(2)
        with r1:
            if st.button("⚡ VP Supply Chain", use_container_width=True, key="p_vp"):
                st.session_state.authenticated = True
                st.session_state.user_role = "VP Supply Chain Operations"
                st.session_state.view_mode = "VP"
                st.rerun()
            if st.button("🔄 Audit Officer", use_container_width=True, key="p_audit"):
                st.session_state.authenticated = True
                st.session_state.user_role = "Internal Audit Officer"
                st.session_state.view_mode = "PLANNER"
                st.rerun()
        with r2:
            if st.button("📊 S&OE Planner", use_container_width=True, key="p_planner"):
                st.session_state.authenticated = True
                st.session_state.user_role = "Senior S&OE Planner"
                st.session_state.view_mode = "PLANNER"
                st.rerun()
            if st.button("💳 Finance AP", use_container_width=True, key="p_finance"):
                st.session_state.authenticated = True
                st.session_state.user_role = "Finance AP Manager"
                st.session_state.view_mode = "PLANNER"
                st.rerun()

    st.stop()


# ─────────────────────────────────────────────────────────────────
# SIDEBAR APPLICATION SHELL
# ─────────────────────────────────────────────────────────────────
with st.sidebar:
    render_html("""
<div class="brand-shell">
  <div class="brand-icon-box">🛡️</div>
  <div>
    <div class="brand-title-text">SENTINEL SC</div>
    <div class="brand-subtitle-text">FACTORY CONTINUITY</div>
  </div>
</div>
""")

    st.caption("NAVIGATION MODULES")
    nav_item = st.selectbox(
        "Select Command Area:",
        [
            "📊 COMMAND / Overview",
            "⚠️ COMMAND / Disruptions",
            "⚡ COMMAND / Recovery",
            "👤 COMMAND / Decisions",
            "📡 INTELLIGENCE / Live Signals",
            "🏭 INTELLIGENCE / Supply Exposure",
            "📐 INTELLIGENCE / Impact Analysis",
            "🛡️ GOVERNANCE / Approval Queue",
            "📋 GOVERNANCE / Rulebook (C1-C8)",
            "🧾 GOVERNANCE / Audit Trail",
        ],
        index=0,
        key="nav_module_select"
    )

    st.markdown("---")
    st.markdown("#### 🎛️ Disruption Simulator")
    st.caption(f"Active Persona: **{st.session_state.user_role}**")

    scenario_mode = st.radio("Scenario Preset:", [
        "Scenario A (Shortage + Recovery)",
        "Scenario B (Buffer Absorbs)",
        "Scenario C (False Positive)",
        "Live Signal Search",
    ], index=0)

    custom_query = ""
    if scenario_mode == "Live Signal Search":
        custom_query = st.text_input("Query:", value="Port Klang port disruption OR delay OR strike OR weather")

    ref_date = st.date_input("Reference Date", value=date(2026, 10, 3))

    if st.button("⚡ EXECUTE PIPELINE", type="primary", use_container_width=True):
        st.session_state.thread_id = f"demo-{uuid.uuid4().hex[:6]}"
        config = {"configurable": {"thread_id": st.session_state.thread_id}}
        fixture_map = {
            "Scenario A (Shortage + Recovery)": str(BASE_DIR / "integrations" / "fixtures" / "port_disruption.json"),
            "Scenario B (Buffer Absorbs)": str(BASE_DIR / "integrations" / "fixtures" / "scenario_b.json"),
            "Scenario C (False Positive)": str(BASE_DIR / "integrations" / "fixtures" / "false_positive.json"),
        }
        init_state: SentinelState = {
            "headline": custom_query if scenario_mode == "Live Signal Search" else None,
            "content": None,
            "fixture_path": fixture_map.get(scenario_mode),
            "use_serp": (scenario_mode == "Live Signal Search"),
            "reference_date": str(ref_date),
            "disruption_event": None, "impact_assessment": None,
            "recovery_plan": None, "human_decision": None,
            "current_step": "INITIATED",
            "audit_trail": [f"Triggered: {scenario_mode}"],
        }
        with st.spinner("Executing Sentinel Engine..."):
            res = st.session_state.graph.invoke(init_state, config=config)
            st.session_state.pipeline_state = res
            st.session_state.decision_executed = None
            st.rerun()

    if st.button("🚪 Sign Out Workspace", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

    render_html("""
<div class="sidebar-status-footer">
  <span>●</span> SYSTEM OPERATIONAL
</div>
""")


# ─────────────────────────────────────────────────────────────────
# AUTO-LOAD DEFAULT SCENARIO A
# ─────────────────────────────────────────────────────────────────
if st.session_state.pipeline_state is None:
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    init_state: SentinelState = {
        "headline": None, "content": None,
        "fixture_path": str(BASE_DIR / "integrations" / "fixtures" / "port_disruption.json"),
        "use_serp": False, "reference_date": "2026-10-03",
        "disruption_event": None, "impact_assessment": None,
        "recovery_plan": None, "human_decision": None,
        "current_step": "INITIATED", "audit_trail": ["Auto-initialized Scenario A"],
    }
    with st.spinner("Initializing Sentinel Control Layer..."):
        st.session_state.pipeline_state = st.session_state.graph.invoke(init_state, config=config)


# ─────────────────────────────────────────────────────────────────
# PARSE ENGINE STATE
# ─────────────────────────────────────────────────────────────────
state = st.session_state.pipeline_state
event = state.get("disruption_event") if state else None
impact = state.get("impact_assessment") if state else None
plan = state.get("recovery_plan") if state else None
current_step = state.get("current_step", "INITIATED") if state else "INITIATED"

attr_math = build_attribution_math(impact)
exp_block = build_verifiable_exposure(impact)
option_rules = build_option_rules(plan)

is_detected = event is not None
is_assessed = impact is not None
is_formulated = plan is not None
is_executed = current_step == "EXECUTION_COMPLETE"
is_active = event and event.get("status") == "ACTIVE"


# ─────────────────────────────────────────────────────────────────
# 1. TOP HEADER BAR (COMPACT & UNCLIPPED)
# ─────────────────────────────────────────────────────────────────
step_cls = lambda done, active: "pill-dark-green" if done else ("pill-dark-blue" if active else "pill-dark-amber")

render_html(f"""
<div style="display:flex;justify-content:space-between;align-items:center;background:#0D1322;padding:10px 16px;border:1px solid rgba(255,255,255,0.08);border-radius:10px;margin-bottom:14px;">
  <div style="display:flex;align-items:center;gap:10px;">
    <div style="background:#2563EB;color:white;width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:900;">🛡️</div>
    <div>
      <div style="font-weight:900;font-size:1.0rem;color:#FFFFFF;letter-spacing:0.03em;">
        SENTINEL SC <span style="color:#4B5563;font-weight:400;">/</span> <span style="color:#9CA3AF;font-size:0.82rem;font-weight:600;">FACTORY CONTINUITY DECISION LAYER</span>
      </div>
      <div style="font-size:0.68rem;color:#6B7280;font-weight:600;">Deterministic Python Engine &bull; Role: <b style="color:#60A5FA;">{st.session_state.user_role}</b></div>
    </div>
  </div>

  <div style="display:flex;align-items:center;gap:8px;">
    <span class="{step_cls(is_detected, True)}">{"✓" if is_detected else "1"} Detected</span> &rarr;
    <span class="{step_cls(is_assessed, is_detected)}">{"✓" if is_assessed else "2"} Assessed</span> &rarr;
    <span class="{step_cls(is_formulated, is_assessed)}">{"✓" if is_formulated else "3"} Options</span> &rarr;
    <span class="{step_cls(is_executed, current_step == 'AWAITING_HUMAN_APPROVAL')}">{"✓" if is_executed else "4"} {"Executed" if is_executed else "Human Gate"}</span>
  </div>

  <div style="display:flex;align-items:center;gap:8px;">
    <div style="background:#111827;border:1px solid rgba(255,255,255,0.1);padding:4px 10px;border-radius:6px;font-size:0.72rem;font-weight:700;color:#60A5FA;" class="mono-font">
      🕒 {state.get('reference_date','2026-10-03')} 08:00 UTC
    </div>
  </div>
</div>
""")

# Quick Action bar
nav_c1, nav_c2, nav_c3, nav_c4 = st.columns([3, 1, 1, 1])
with nav_c2:
    if st.button(
        "👤 S&OE Planner" if st.session_state.view_mode == "VP" else "✓ S&OE Planner",
        use_container_width=True,
        type="secondary" if st.session_state.view_mode == "VP" else "primary",
        key="btn_planner_view"
    ):
        st.session_state.view_mode = "PLANNER"
        st.rerun()
with nav_c3:
    if st.button(
        "⭐ VP Approver" if st.session_state.view_mode == "PLANNER" else "✓ VP Approver",
        use_container_width=True,
        type="secondary" if st.session_state.view_mode == "PLANNER" else "primary",
        key="btn_vp_view"
    ):
        st.session_state.view_mode = "VP"
        st.rerun()
with nav_c4:
    if st.button("⚡ Evidence Drawer", use_container_width=True, key="btn_evidence_drawer"):
        st.session_state.show_evidence_drawer = not st.session_state.show_evidence_drawer
        st.rerun()


# ─────────────────────────────────────────────────────────────────
# 2. DISRUPTION HERO BANNER
# ─────────────────────────────────────────────────────────────────
if is_active:
    headline = event.get("raw_headline", "Port Klang Typhoon Squall & Container Berth Congestion")
    port_name = event.get("port_name", "Port Klang")
    vessel_name = event.get("vessel_name", "MV Sentinel Star")
    shipment_id = event.get("shipment_id", "SHIP-440V-01")
    po_id = event.get("po_id", "PO-2026-441")
    exposure_val = exp_block["otif_exposure_usd"]
    co_id = exp_block["customer_order_id"]
    customer_name = exp_block["customer_name"]

    render_html(f"""
<div class="disruption-hero-banner">
  <div>
    <div class="disruption-hero-title">
      <span class="pill-dark-red">ACTIVE</span>
      <span class="pill-dark-red">+7 DAYS DELAY</span>
      <span>DISRUPTION DETECTED — {port_name}</span>
    </div>
    <div class="disruption-hero-sub">
      "{headline}"<br>
      Port disruption detected on vessel <b>{vessel_name}</b> (Shipment {shipment_id}, PO {po_id}). Revised arrival expected to create material shortage on ApexX-100 line.
    </div>
  </div>
  <div class="exposure-highlight-card">
    <div class="exposure-highlight-lbl">CUSTOMER EXPOSURE</div>
    <div class="exposure-highlight-val">${exposure_val:,.0f}</div>
    <div style="font-size:0.72rem;color:#FCA5A5;font-weight:600;margin-top:2px;">{co_id} ({customer_name})</div>
  </div>
</div>
""")

elif event and event.get("status") == "IGNORED":
    render_html(f"""
<div class="glass-card" style="border-left:4px solid #6B7280;">
  <div style="display:flex;align-items:center;gap:10px;">
    <span class="pill-dark-amber">SIGNAL IGNORED</span>
    <span style="font-size:0.95rem;font-weight:700;color:#E5E7EB;">{event.get('raw_headline','')}</span>
  </div>
  <div style="font-size:0.82rem;color:#9CA3AF;margin-top:6px;">{event.get('ignore_reason','No matched active shipment in ERP.')}</div>
</div>
""")


# ─────────────────────────────────────────────────────────────────
# 3. IMPACT KPI SECTION (Dominant Exposure Card)
# ─────────────────────────────────────────────────────────────────
tts_days = attr_math["tts_days"]
stockout_date = attr_math["stockout_date"]
revised_dt_str = impact["shortage"]["revised_eta"] if impact else "2026-10-18"
exposure_val = exp_block["otif_exposure_usd"]
net_shortage = attr_math["total_shortage_gap_days"]
base_gap = attr_math["baseline_gap_days"]
storm_gap = attr_math["disruption_delay_days"]
so_due = exp_block["so_due_date"]

k1, k2, k3, k4, k5 = st.columns([1, 1, 1, 1.4, 1])

with k1:
    render_html(f"""
<div class="kpi-card-dark">
  <div class="kpi-card-lbl">TIME TO STOCKOUT (TTS) <span>ℹ️</span></div>
  <div class="kpi-card-val">{stockout_date}</div>
  <div class="kpi-card-sub"><span class="mono-font">TTS: {tts_days} Days</span></div>
</div>
""")
    if st.button("📐 TTS Formula", key="modal_tts", use_container_width=True):
        st.session_state.active_formula_modal = "STOCKOUT"
        st.rerun()

with k2:
    render_html(f"""
<div class="kpi-card-dark">
  <div class="kpi-card-lbl">REVISED ARRIVAL (TTR) <span>ℹ️</span></div>
  <div class="kpi-card-val">{revised_dt_str}</div>
  <div class="kpi-card-sub"><span class="mono-font">+{storm_gap}d Storm Delay</span></div>
</div>
""")
    if st.button("📐 TTR Formula", key="modal_ttr", use_container_width=True):
        st.session_state.active_formula_modal = "REVISED_ETA"
        st.rerun()

with k3:
    render_html(f"""
<div class="kpi-card-dark">
  <div class="kpi-card-lbl">NET SHORTAGE GAP <span>ℹ️</span></div>
  <div class="kpi-card-val-red">{net_shortage} Days</div>
  <div class="kpi-card-sub"><span class="mono-font">{base_gap}d Base + {storm_gap}d Storm</span></div>
</div>
""")
    if st.button("📐 Gap Proof", key="modal_gap", use_container_width=True):
        st.session_state.active_formula_modal = "GAP_ATTRIBUTION"
        st.rerun()

with k4:  # DOMINANT EXPOSURE CARD
    render_html(f"""
<div class="kpi-card-dark kpi-card-dominant">
  <div class="kpi-card-lbl" style="color:#FCA5A5;">CUSTOMER EXPOSURE (MAX AT RISK) <span>ℹ️</span></div>
  <div class="kpi-card-val-red" style="font-size:1.8rem;">${exposure_val:,.2f}</div>
  <div class="kpi-card-sub" style="color:#FCA5A5;"><span class="mono-font">SLA: {so_due} ({exp_block['customer_name'][:12]})</span></div>
</div>
""")
    if st.button("📐 Audit Proof", key="modal_exp", use_container_width=True):
        st.session_state.active_formula_modal = "EXPOSURE"
        st.rerun()

with k5:
    verdict_text = "SHORTAGE" if net_shortage > 0 else "ABSORBED"
    verdict_cls = "pill-dark-red" if net_shortage > 0 else "pill-dark-green"
    render_html(f"""
<div class="kpi-card-dark">
  <div class="kpi-card-lbl">ENGINE VERDICT</div>
  <div style="margin:8px 0 4px;"><span class="{verdict_cls}" style="font-size:1.0rem;padding:3px 10px;">{verdict_text}</span></div>
  <div class="kpi-card-sub">Action Required</div>
</div>
""")

st.write("")


# ─────────────────────────────────────────────────────────────────
# 4. FORMULA PROOF MODALS
# ─────────────────────────────────────────────────────────────────
if st.session_state.active_formula_modal:
    modal = st.session_state.active_formula_modal
    titles = {
        "STOCKOUT": "⏱ Time-To-Survive (TTS) Formula Proof",
        "REVISED_ETA": "🚢 Revised Arrival (TTR) Calculation",
        "GAP_ATTRIBUTION": "📐 Honest Gap Attribution Proof",
        "EXPOSURE": "💰 Verifiable Customer Exposure Proof",
    }
    with st.expander(f"📋 {titles.get(modal, 'Formula Proof')} — Click to Close", expanded=True):
        if modal == "STOCKOUT":
            render_html(f"""
<div style="background:#111827;border:1px solid rgba(255,255,255,0.1);padding:14px;border-radius:8px;font-family:'JetBrains Mono',monospace;color:#FCA5A5;font-weight:700;">
  {attr_math['tts_formula']}
</div>
<div style="margin-top:10px;font-size:0.85rem;color:#D1D5DB;line-height:1.6;">
On-hand inventory at reference date ({state.get('reference_date','2026-10-03')}) is <b>{attr_math['on_hand_qty']:.0f} coils</b>. At <b>{attr_math['daily_consumption']:.0f} coils/day</b> burn rate, inventory exhausts on Day {tts_days} (<b>{stockout_date}</b>).
</div>
""")
        elif modal == "REVISED_ETA":
            render_html(f"""
<div style="background:#111827;border:1px solid rgba(255,255,255,0.1);padding:14px;border-radius:8px;font-family:'JetBrains Mono',monospace;color:#FCA5A5;font-weight:700;">
  TTR = (Revised ETA − Reference Date) = ({revised_dt_str} − {state.get('reference_date','2026-10-03')}) = {attr_math['tts_days'] + net_shortage} Days
</div>
<div style="margin-top:10px;font-size:0.85rem;color:#D1D5DB;line-height:1.6;">
Original planned arrival was <b>{attr_math['planned_arrival_date']}</b>. A +{storm_gap}-day storm at Port Klang pushes arrival to <b>{revised_dt_str}</b>.
</div>
""")
        elif modal == "GAP_ATTRIBUTION":
            render_html(f"""
<div style="background:#111827;border:1px solid rgba(255,255,255,0.1);padding:14px;border-radius:8px;font-family:'JetBrains Mono',monospace;color:#FBBF24;font-weight:700;">
  {attr_math['gap_equation']}
</div>
<div style="margin-top:10px;font-size:0.85rem;color:#D1D5DB;line-height:1.6;">
<b>Baseline Gap ({base_gap}d):</b> {attr_math['baseline_explanation']}.<br>
<b>Storm Disruption ({storm_gap}d):</b> Port Klang typhoon squall added {storm_gap} days delay.<br>
<b>Honest Attribution:</b> {base_gap}d pre-existed in planning; storm is responsible for {storm_gap}d.
</div>
""")
        elif modal == "EXPOSURE":
            qty = int(exposure_val / 240) if exposure_val > 0 else 500
            render_html(f"""
<div style="background:#111827;border:1px solid rgba(255,255,255,0.1);padding:14px;border-radius:8px;font-family:'JetBrains Mono',monospace;color:#EF4444;font-weight:700;">
  {qty} Units × $240.00 Unit Price = ${exposure_val:,.2f} OTIF Exposure
</div>
<div style="margin-top:10px;font-size:0.85rem;color:#D1D5DB;line-height:1.6;">
{exp_block['proof_narrative']}<br>
WO <b>{exp_block['work_order_id']}</b> starts {exp_block['wo_planned_start']}, SLA due date: <b>{exp_block['so_due_date']}</b>.
</div>
""")
        if st.button("✕ Close Proof Panel", key="close_modal"):
            st.session_state.active_formula_modal = None
            st.rerun()


# ─────────────────────────────────────────────────────────────────
# 5. HORIZONTAL OPERATIONAL CONTROL TIMELINE
# ─────────────────────────────────────────────────────────────────
render_html(f"""
<div class="timeline-container">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
    <span style="font-size:0.7rem;font-weight:800;color:#9CA3AF;letter-spacing:0.1em;text-transform:uppercase;">
      OPERATIONAL DISRUPTION &amp; SHORTAGE TIMELINE
    </span>
    <span class="pill-dark-red">10-DAY UNMITIGATED SHORTAGE SPAN</span>
  </div>
  
  <div style="position:relative;margin:24px 12px 14px;">
    <!-- Background track -->
    <div style="position:absolute;top:10px;left:40px;right:40px;height:4px;background:#1F2937;z-index:1;border-radius:2px;"></div>
    <!-- Shortage span fill -->
    <div style="position:absolute;top:10px;left:33%;right:15%;height:4px;background:linear-gradient(90deg, #F59E0B 0%, #EF4444 100%);z-index:2;border-radius:2px;box-shadow:0 0 10px rgba(239,68,68,0.4);"></div>

    <div style="display:flex;justify-content:space-between;position:relative;z-index:3;">
      <div style="text-align:center;">
        <div style="width:22px;height:22px;border-radius:50%;background:#78350F;border:2px solid #F59E0B;margin:0 auto;box-shadow:0 0 10px rgba(245,158,11,0.5);"></div>
        <div style="font-size:0.78rem;font-weight:800;color:#FFFFFF;margin-top:8px;">OCT 03</div>
        <div style="font-size:0.65rem;color:#9CA3AF;">Reference Date</div>
      </div>

      <div style="text-align:center;">
        <div style="width:22px;height:22px;border-radius:50%;background:#7F1D1D;border:2px solid #EF4444;margin:0 auto;box-shadow:0 0 12px rgba(239,68,68,0.6);"></div>
        <div style="font-size:0.78rem;font-weight:800;color:#EF4444;margin-top:8px;">OCT 08</div>
        <div style="font-size:0.65rem;color:#FCA5A5;">Stockout Date (TTS)</div>
      </div>

      <div style="text-align:center;">
        <div style="width:22px;height:22px;border-radius:50%;background:#1F2937;border:2px solid #6B7280;margin:0 auto;"></div>
        <div style="font-size:0.78rem;font-weight:800;color:#D1D5DB;margin-top:8px;">OCT 11</div>
        <div style="font-size:0.65rem;color:#9CA3AF;">Original ETA</div>
      </div>

      <div style="text-align:center;">
        <div style="width:22px;height:22px;border-radius:50%;background:#7F1D1D;border:2px solid #EF4444;margin:0 auto;box-shadow:0 0 12px rgba(239,68,68,0.6);"></div>
        <div style="font-size:0.78rem;font-weight:800;color:#EF4444;margin-top:8px;">OCT 18</div>
        <div style="font-size:0.65rem;color:#FCA5A5;">Revised ETA (TTR)</div>
      </div>
    </div>
  </div>
</div>
""")


# ─────────────────────────────────────────────────────────────────
# 6. DIGITAL TWIN CHART + ONTOLOGY LINEAGE
# ─────────────────────────────────────────────────────────────────
col_left, col_right = st.columns([1.1, 1], gap="medium")

with col_left:
    render_html("""
<div class="glass-card">
  <div class="glass-header">
    <span>⚡ DIGITAL TWIN: INVENTORY DEPLETION &amp; RECOVERY CURVE</span>
    <span class="pill-dark-blue">SIMULATED</span>
  </div>
</div>
""")

    # Ontology Pegging Chain
    render_html(f"""
<div style="background:#111827;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:12px 16px;margin-bottom:12px;">
  <div style="font-size:0.63rem;font-weight:800;color:#9CA3AF;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;">
    ONTOLOGY PEGGING LINEAGE CHAIN
  </div>
  <div style="display:grid;grid-template-columns:1fr auto 1fr auto 1fr auto 1fr;gap:4px;align-items:center;font-size:0.75rem;">
    <div style="background:#1F2937;padding:8px;border-radius:6px;">
      <div style="font-size:0.6rem;color:#9CA3AF;">1. SHIPMENT</div>
      <div style="font-weight:800;color:#FFFFFF;" class="mono-font">{event.get('shipment_id','SHIP-440V-01') if event else 'SHIP-440V-01'}</div>
    </div>
    <div style="color:#6B7280;">→</div>
    <div style="background:#1F2937;padding:8px;border-radius:6px;">
      <div style="font-size:0.6rem;color:#9CA3AF;">2. MATERIAL</div>
      <div style="font-weight:800;color:#60A5FA;" class="mono-font">STCOIL-440V</div>
    </div>
    <div style="color:#6B7280;">→</div>
    <div style="background:#1F2937;padding:8px;border-radius:6px;">
      <div style="font-size:0.6rem;color:#9CA3AF;">3. WORK ORDER</div>
      <div style="font-weight:800;color:#FFFFFF;" class="mono-font">{exp_block['work_order_id']}</div>
    </div>
    <div style="color:#6B7280;">→</div>
    <div style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);padding:8px;border-radius:6px;">
      <div style="font-size:0.6rem;color:#FCA5A5;">4. CUSTOMER ORDER</div>
      <div style="font-weight:800;color:#EF4444;" class="mono-font">{exp_block['customer_order_id']}</div>
    </div>
  </div>
</div>
""")

    # Plotly Depletion Chart
    REF_DATE = date(2026, 10, 3)
    ON_HAND, DAILY_BURN = 400, 80
    TTS_DAYS, ORIG_ETA_DAYS, OPT_A_ARRIVE = 5, 8, 7

    revised_day = ORIG_ETA_DAYS + storm_gap
    stockout_day = TTS_DAYS
    opt_a_day = OPT_A_ARRIVE

    days = list(range(0, 22))
    dates = [(REF_DATE + timedelta(days=d)).strftime("Oct %d") for d in days]

    unmitigated = []
    opt_a_curve = []
    for d in days:
        base = max(0, ON_HAND - DAILY_BURN * d)
        unmitigated.append(base if d < revised_day else base + 900)
        opt_a_curve.append(base if d < opt_a_day else min(ON_HAND + 900, base + 900))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=unmitigated, mode="lines+markers", name="Unmitigated Shortage",
        line=dict(color="#EF4444", width=2.5, dash="dot"), marker=dict(size=4, color="#EF4444")
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=opt_a_curve, mode="lines+markers", name="OPT-A: EuroCoils Air Recovery",
        line=dict(color="#10B981", width=3), marker=dict(size=5, color="#10B981")
    ))

    fig.update_layout(
        paper_bgcolor="#111827", plot_bgcolor="#0B0F19", height=320,
        margin=dict(l=10, r=10, t=15, b=20),
        xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)", tickangle=-30, color="#9CA3AF"),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)", title="Inventory Units", color="#9CA3AF", range=[0, 1400]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="#E5E7EB")),
        font=dict(family="Inter, sans-serif")
    )
    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────────
# 7. RECOVERY DECISION MATRIX (MONEY SHOT)
# ─────────────────────────────────────────────────────────────────
with col_right:
    render_html("""
<div class="glass-card">
  <div class="glass-header">
    <span>RECOVERY DECISION MATRIX</span>
    <span class="pill-dark-green">1 FEASIBLE SURVIVOR</span>
  </div>
  <div style="font-size:0.75rem;color:#9CA3AF;margin-top:-6px;margin-bottom:12px;">
    Constraint-validated recovery paths (C1–C8 evaluation)
  </div>
</div>
""")

    # 4 Option Cards
    render_html(f"""
<div class="recovery-matrix-grid">
  <div class="recovery-card recovery-card-recommended">
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span style="background:#1D4ED8;color:#FFFFFF;font-size:0.62rem;font-weight:800;padding:2px 6px;border-radius:4px;text-transform:uppercase;">RECOMMENDED</span>
        <span style="font-size:0.75rem;font-weight:800;color:#60A5FA;">OPT-A</span>
      </div>
      <div style="font-size:0.85rem;font-weight:800;color:#FFFFFF;margin-top:8px;">EuroCoils Air Spot Buy</div>
      <div style="font-size:0.7rem;color:#9CA3AF;margin-top:4px;">Supplier: EuroCoils GmbH</div>
    </div>
    <div>
      <div style="font-size:1.25rem;font-weight:900;color:#34D399;" class="mono-font">$30,100</div>
      <div style="font-size:0.65rem;color:#FBBF24;margin-top:4px;">APPROVAL REQUIRED (C5)</div>
    </div>
  </div>

  <div class="recovery-card recovery-card-vetoed">
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span style="background:rgba(239,68,68,0.2);color:#FCA5A5;font-size:0.62rem;font-weight:800;padding:2px 6px;border-radius:4px;border:1px solid rgba(239,68,68,0.3);text-transform:uppercase;">VETOED</span>
        <span style="font-size:0.75rem;font-weight:800;color:#FCA5A5;">OPT-B</span>
      </div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;margin-top:8px;">Hanoi Coils Sea Expedite</div>
      <div style="font-size:0.7rem;color:#9CA3AF;margin-top:4px;">Supplier: Hanoi Coils</div>
    </div>
    <div>
      <div style="font-size:1.1rem;font-weight:800;color:#9CA3AF;" class="mono-font">$12,000</div>
      <div style="font-size:0.65rem;color:#EF4444;margin-top:4px;">VETO: C1 Lead time exceeds window</div>
    </div>
  </div>

  <div class="recovery-card recovery-card-vetoed">
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span style="background:rgba(239,68,68,0.2);color:#FCA5A5;font-size:0.62rem;font-weight:800;padding:2px 6px;border-radius:4px;border:1px solid rgba(239,68,68,0.3);text-transform:uppercase;">VETOED</span>
        <span style="font-size:0.75rem;font-weight:800;color:#FCA5A5;">OPT-C</span>
      </div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;margin-top:8px;">FastCoil Asia Substitute</div>
      <div style="font-size:0.7rem;color:#9CA3AF;margin-top:4px;">Supplier: FastCoil</div>
    </div>
    <div>
      <div style="font-size:1.1rem;font-weight:800;color:#9CA3AF;" class="mono-font">$18,500</div>
      <div style="font-size:0.65rem;color:#EF4444;margin-top:4px;">VETO: C3/C6 Incompatible PPAP/BOM</div>
    </div>
  </div>

  <div class="recovery-card recovery-card-vetoed">
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span style="background:rgba(239,68,68,0.2);color:#FCA5A5;font-size:0.62rem;font-weight:800;padding:2px 6px;border-radius:4px;border:1px solid rgba(239,68,68,0.3);text-transform:uppercase;">VETOED</span>
        <span style="font-size:0.75rem;font-weight:800;color:#FCA5A5;">OPT-D</span>
      </div>
      <div style="font-size:0.85rem;font-weight:800;color:#E5E7EB;margin-top:8px;">Reschedule WO-7782</div>
      <div style="font-size:0.7rem;color:#9CA3AF;margin-top:4px;">Internal Line Schedule</div>
    </div>
    <div>
      <div style="font-size:1.1rem;font-weight:800;color:#9CA3AF;" class="mono-font">$0</div>
      <div style="font-size:0.65rem;color:#EF4444;margin-top:4px;">VETO: C4 Frozen production window</div>
    </div>
  </div>
</div>
""")

    # Rule Evidence Inspection Box
    if plan and plan.get("candidates") and option_rules:
        sel_opt_id = st.selectbox("Inspect Rule Evidence For:", [c["option_id"] for c in plan["candidates"]], key="sel_opt")
        sel_rules = option_rules.get(sel_opt_id, {})
        if sel_rules:
            for rid, rdata in sel_rules.items():
                status_icon = "✓" if rdata.get("pass") else ("⚠️" if rdata.get("warning") else ("✗" if rdata.get("fail") else "—"))
                color = "#10B981" if rdata.get("pass") else ("#F59E0B" if rdata.get("warning") else ("#EF4444" if rdata.get("fail") else "#6B7280"))
                render_html(f"""
<div style="display:flex;align-items:center;justify-content:space-between;padding:6px 10px;border-bottom:1px solid rgba(255,255,255,0.06);background:rgba(17,24,39,0.5);">
  <div style="display:flex;align-items:center;gap:10px;">
    <span style="color:{color};font-weight:900;font-size:1.0rem;">{status_icon}</span>
    <span style="font-size:0.78rem;font-weight:800;color:#FFFFFF;" class="mono-font">{rid}</span>
    <span style="font-size:0.75rem;color:#9CA3AF;">{rdata.get('reason','')}</span>
  </div>
  <span style="font-size:0.7rem;color:#6B7280;" class="mono-font">Val: {rdata.get('value','—')}</span>
</div>
""")


# ─────────────────────────────────────────────────────────────────
# 8. HUMAN APPROVAL COMMAND PANEL (API INTEGRATED)
# ─────────────────────────────────────────────────────────────────
if current_step == "AWAITING_HUMAN_APPROVAL" and not st.session_state.decision_executed:
    opt_a_cost = 30100.0
    if plan and plan.get("candidates"):
        for c in plan["candidates"]:
            if c.get("option_id") == "OPT-A":
                opt_a_cost = c.get("total_cost", 30100.0)
    net_saved = exposure_val - opt_a_cost

    render_html(f"""
<div class="approval-command-box">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;">
    <div>
      <div style="display:flex;align-items:center;gap:10px;">
        <span class="pill-dark-amber">RULE C5 APPROVAL REQUIRED</span>
        <span style="font-weight:900;font-size:1.1rem;color:#FFFFFF;">HUMAN AUTHORIZATION REQUIRED</span>
      </div>
      <div style="font-size:0.82rem;color:#D1D5DB;margin-top:6px;">
        The recommended action exceeds automatic approval authority ($30,000 threshold).
      </div>
    </div>
    <div style="text-align:right;">
      <div style="font-size:0.65rem;color:#9CA3AF;font-weight:700;">REQUIRED AUTHORITY</div>
      <div style="font-size:0.9rem;font-weight:900;color:#FBBF24;">VP SUPPLY CHAIN</div>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0;background:rgba(17,24,39,0.8);padding:16px;border-radius:10px;border:1px solid rgba(255,255,255,0.08);">
    <div>
      <div style="font-size:0.65rem;color:#9CA3AF;">RECOMMENDED ACTION</div>
      <div style="font-size:0.9rem;font-weight:800;color:#FFFFFF;margin-top:2px;">EuroCoils Air Spot Buy</div>
    </div>
    <div>
      <div style="font-size:0.65rem;color:#9CA3AF;">RECOVERY PREMIUM</div>
      <div style="font-size:0.9rem;font-weight:800;color:#FCA5A5;margin-top:2px;" class="mono-font">${opt_a_cost:,.2f}</div>
    </div>
    <div>
      <div style="font-size:0.65rem;color:#9CA3AF;">CUSTOMER EXPOSURE</div>
      <div style="font-size:0.9rem;font-weight:800;color:#EF4444;margin-top:2px;" class="mono-font">${exposure_val:,.2f}</div>
    </div>
    <div>
      <div style="font-size:0.65rem;color:#9CA3AF;">NET PROTECTED VALUE</div>
      <div style="font-size:0.9rem;font-weight:800;color:#34D399;margin-top:2px;" class="mono-font">+${net_saved:,.2f}</div>
    </div>
  </div>
</div>
""")

    ab1, ab2, ab3 = st.columns([1, 1.5, 2])
    with ab1:
        if st.button("❌ Reject Action", key="gate_decline"):
            config = {"configurable": {"thread_id": st.session_state.thread_id}}
            with st.spinner("Processing rejection..."):
                st.session_state.pipeline_state = st.session_state.graph.invoke(
                    Command(resume={"decision": "REJECTED", "selected_option_id": None,
                                    "decided_by": st.session_state.user_role,
                                    "rejection_reason": "Rejected via control tower."}),
                    config=config
                )
            st.rerun()

    with ab2:
        st.button("📤 Escalate to Board", key="gate_escalate")

    with ab3:
        if st.session_state.view_mode == "VP":
            if st.button("⭐ APPROVE RECOVERY (VP Sign-Off)", type="primary", use_container_width=True, key="gate_approve_vp"):
                config = {"configurable": {"thread_id": st.session_state.thread_id}}
                resume_cmd = Command(resume={
                    "decision": "APPROVED", "selected_option_id": "OPT-A",
                    "decided_by": st.session_state.user_role,
                })
                with st.spinner("Authorizing & Submitting PO to ERP..."):
                    st.session_state.pipeline_state = st.session_state.graph.invoke(resume_cmd, config=config)
                st.session_state.decision_executed = {"status": "EXECUTED", "option": "OPT-A", "cost": opt_a_cost}
                st.rerun()
        else:
            if st.button("📬 Request VP Approval", type="primary", use_container_width=True, key="gate_request_vp"):
                st.info("✅ Notification sent to VP Supply Chain for Rule C5 sign-off.")


elif current_step == "EXECUTION_COMPLETE" or st.session_state.decision_executed:
    dec_by = plan.get("decided_by", st.session_state.user_role) if plan else st.session_state.user_role
    opted = plan.get("selected_option_id", "OPT-A") if plan else "OPT-A"
    render_html(f"""
<div style="background:rgba(16,185,129,0.15);border:1px solid rgba(16,185,129,0.4);border-radius:14px;padding:20px 28px;margin-top:20px;display:flex;justify-content:space-between;align-items:center;">
  <div>
    <div style="color:#34D399;font-weight:900;font-size:1.1rem;">🎉 RECOVERY ACTION AUTHORIZED &amp; ISSUED TO ERP</div>
    <div style="color:#E5E7EB;font-size:0.85rem;margin-top:4px;">
      Option <b>{opted} (EuroCoils Air Spot Buy)</b> approved by <b>{dec_by}</b>.<br>
      PO submitted to ERP. Estimated arrival: <b>2026-10-10</b>.
    </div>
  </div>
  <div style="text-align:right;font-weight:900;color:#34D399;font-size:1.2rem;" class="mono-font">
    Net Value Saved: +${exposure_val - 30100.0:,.2f}
  </div>
</div>
""")


elif current_step == "HALTED_DELAY_ABSORBED":
    render_html("""
<div class="glass-card" style="border-left:4px solid #10B981;">
  <span class="pill-dark-green">✅ DISRUPTION ABSORBED BY INVENTORY BUFFER</span>
  <div style="color:#D1D5DB;font-size:0.85rem;margin-top:6px;">
    TTS (Time-to-Stockout: 20d) exceeds TTR (Time-to-Recover: 15d). No recovery action required.
  </div>
</div>
""")


elif current_step == "HALTED_SIGNAL_IGNORED":
    reason = event.get("ignore_reason", "No matched shipment in ERP.") if event else "No event data."
    render_html(f"""
<div class="glass-card" style="border-left:4px solid #6B7280;">
  <span class="pill-dark-amber">SIGNAL IGNORED</span>
  <div style="font-size:0.85rem;color:#D1D5DB;margin-top:6px;">{reason}</div>
</div>
""")


# ─────────────────────────────────────────────────────────────────
# 9. EVIDENCE DRAWER & AUDIT SECTION
# ─────────────────────────────────────────────────────────────────
if st.session_state.show_evidence_drawer:
    st.markdown("---")
    render_html("""
<div class="glass-card">
  <div class="glass-header">
    <span>⚡ EVIDENCE &amp; INTELLIGENCE DRAWER</span>
    <span style="font-size:0.7rem;color:#9CA3AF;">RAW SIGNALS, AIS &amp; AUDIT LEDGER</span>
  </div>
</div>
""")

    ev_tabs = st.tabs(["📡 SERP Intelligence Signal", "⚓ AIS Vessel Data", "📋 Decision Audit Ledger"])

    with ev_tabs[0]:
        if event:
            render_html(f"""
<div style="background:#111827;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:16px;">
  <div style="font-size:0.65rem;font-weight:800;color:#9CA3AF;">RAW INTELLIGENCE SIGNAL</div>
  <div style="font-size:0.95rem;font-weight:800;color:#FFFFFF;margin-top:4px;">{event.get('raw_headline','Port Klang Typhoon')}</div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:14px;">
    <div style="background:#1F2937;padding:10px;border-radius:6px;">
      <div style="font-size:0.6rem;color:#9CA3AF;">EVENT TYPE</div>
      <div style="font-weight:800;color:#FFFFFF;">{event.get('event_type','PORT_RESTRICTION')}</div>
    </div>
    <div style="background:#1F2937;padding:10px;border-radius:6px;">
      <div style="font-size:0.6rem;color:#9CA3AF;">PORT LOCATION</div>
      <div style="font-weight:800;color:#FFFFFF;">{event.get('port_name','Port Klang')}</div>
    </div>
    <div style="background:#1F2937;padding:10px;border-radius:6px;">
      <div style="font-size:0.6rem;color:#9CA3AF;">CONFIDENCE</div>
      <div style="font-weight:800;color:#34D399;">{event.get('confidence',0.92)*100:.0f}%</div>
    </div>
  </div>
</div>
""")

    with ev_tabs[1]:
        if event and event.get("status") == "ACTIVE":
            render_html(f"""
<div style="background:#111827;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:16px;">
  <div style="font-size:0.65rem;font-weight:800;color:#9CA3AF;">AIS VESSEL TELEMETRY</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px;font-size:0.85rem;">
    <div><span style="color:#9CA3AF;">Vessel:</span> <b>{event.get('vessel_name','MV Sentinel Star')}</b></div>
    <div><span style="color:#9CA3AF;">Shipment:</span> <b class="mono-font">{event.get('shipment_id','SHIP-440V-01')}</b></div>
    <div><span style="color:#9CA3AF;">Revised ETA:</span> <b style="color:#EF4444;">{revised_dt_str}</b></div>
    <div><span style="color:#9CA3AF;">Delay:</span> <b style="color:#EF4444;">+{event.get('estimated_delay_days',7)} Days</b></div>
  </div>
</div>
""")

    with ev_tabs[2]:
        if state and state.get("audit_trail"):
            for i, item in enumerate(state["audit_trail"]):
                render_html(f"""
<div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);font-size:0.8rem;">
  <span style="color:#6B7280;font-weight:700;">{i+1}.</span>
  <span style="color:#D1D5DB;">{item}</span>
</div>
""")

    if st.button("✕ Close Drawer", key="close_evidence"):
        st.session_state.show_evidence_drawer = False
        st.rerun()


# ─────────────────────────────────────────────────────────────────
# 10. AI TRANSPARENCY SECTION
# ─────────────────────────────────────────────────────────────────
st.markdown("---")
render_html("""
<div style="background:rgba(17,24,39,0.6);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:14px 20px;display:flex;justify-content:space-between;align-items:center;">
  <div>
    <span style="font-size:0.7rem;font-weight:800;color:#60A5FA;letter-spacing:0.08em;text-transform:uppercase;">AI TRANSPARENCY &amp; GOVERNANCE</span>
    <div style="font-size:0.8rem;color:#9CA3AF;margin-top:2px;">
      <b>Gemini LLM</b> interprets raw external text signals &bull; <b>Deterministic Python Engine</b> computes TTS/TTR, BOM pegging &amp; C1–C8 rules &bull; <b>Human Executive</b> decides.
    </div>
  </div>
  <span class="pill-dark-blue">LLM + DETERMINISTIC ENGINE</span>
</div>
""")
