# -*- coding: utf-8 -*-
"""
agents/signal_agent.py
======================
Signal Intelligence Agent for Sentinel-SC.

Architecture Contract:
  - Raw external signal (SerpAPI / News / Fixtures)
    -> Entity extraction (LLM JSON mode or Heuristic fallback)
    -> Deterministic DB correlation against shipments/purchase_orders in SQLite
    -> Validated schemas.events.DisruptionEvent

Key Invariants & Fixes Applied:
  1. No False-Positive Hub Defaulting:
     Heuristic parser does NOT default to 'Port Klang'. If no location or vessel
     is identified, location remains None, resolving cleanly to SignalStatus.IGNORED.
  2. Objective Shipment Resolution:
     Matches against shipments table via vessel_name or port_of_loading.
     No hardcoded preference for any shipment ID.
  3. Vessel Name Extraction:
     Heuristic parser extracts vessel names (e.g., 'MV Sentinel Star', 'MV Nordic Blue').
  4. Raw Delay Preservation:
     Delays are parsed directly from text without artificial clamping.
  5. Part 3 Schema Compliance:
     Returns validated schemas.events.DisruptionEvent.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas.events import DisruptionEvent, EventType, SignalSource, SignalStatus
from integrations.serp_client import fetch_disruption_news

logger = logging.getLogger("SignalAgent")
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "erp" / "mock_erp.db"
SIGNAL_OUTPUT_FILE = BASE_DIR / "agents" / "signal_output.json"


def _get_db_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Mock ERP database not found at {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────────────────────────
# ENTITY EXTRACTION: HEURISTIC & LLM
# ─────────────────────────────────────────────────────────────────

def heuristic_extract(text: str) -> Dict[str, Any]:
    """
    Deterministic rule-based extractor that parses raw text for disruption attributes.
    Does NOT default to Port Klang. If unmentioned, port_name is None.
    """
    text_lower = text.lower()

    # 1. Port detection
    port_name = None
    if "port klang" in text_lower or "klang" in text_lower:
        port_name = "Port Klang"
    elif "hamburg" in text_lower:
        port_name = "Hamburg"
    elif "yantian" in text_lower:
        port_name = "Yantian"
    elif "singapore" in text_lower:
        port_name = "Singapore"
    elif "manila" in text_lower:
        port_name = "Manila"
    elif "kaohsiung" in text_lower:
        port_name = "Kaohsiung"

    # 2. Vessel name detection (e.g., MV Sentinel Star, MV Nordic Blue, MV Pacific Trader)
    vessel_name = None
    vessel_match = re.search(
        r"\b(?:MV|M/V)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
        text,
        re.IGNORECASE
    )
    if vessel_match:
        vessel_name = f"MV {vessel_match.group(1).title()}"
    elif "sentinel star" in text_lower or "mv sentinel" in text_lower:
        vessel_name = "MV Sentinel Star"
    elif "nordic blue" in text_lower:
        vessel_name = "MV Nordic Blue"
    elif "pacific trader" in text_lower:
        vessel_name = "MV Pacific Trader"

    # 3. Disruption Type detection
    event_type = EventType.PORT_RESTRICTION
    if any(k in text_lower for k in ["typhoon", "storm", "cyclone", "flood", "weather", "squall"]):
        event_type = EventType.WEATHER_EVENT
    elif any(k in text_lower for k in ["strike", "walkout", "labor protest", "union action"]):
        event_type = EventType.STRIKE
    elif any(k in text_lower for k in ["customs", "sanction", "export control", "trade ban"]):
        event_type = EventType.CUSTOMS_HOLD
    elif any(k in text_lower for k in ["diversion", "rerouted", "divert"]):
        event_type = EventType.ROUTE_DIVERSION
    elif any(k in text_lower for k in ["delay", "vessel delay", "anchorage", "hold position"]):
        event_type = EventType.VESSEL_DELAY
    elif any(k in text_lower for k in ["congestion", "berth delay", "waiting time", "queue"]):
        event_type = EventType.PORT_RESTRICTION

    # 4. Delay days extraction: use conservative upper bound for risk planning
    delay_days = 7  # default nominal baseline
    range_match = re.search(r"(\d+)\s*(?:to|-)\s*(\d+)\s*days", text_lower)
    if range_match:
        delay_days = int(range_match.group(2))  # upper bound of delay range
    else:
        single_match = re.search(r"(\d+)\s*day[s]?", text_lower)
        if single_match:
            delay_days = int(single_match.group(1))
        elif "two weeks" in text_lower or "2 weeks" in text_lower:
            delay_days = 14
        elif "one week" in text_lower or "1 week" in text_lower:
            delay_days = 7

    confidence = 0.92 if (port_name or vessel_name) else 0.40

    return {
        "event_type": event_type,
        "port_name": port_name,
        "vessel_name": vessel_name,
        "estimated_delay_days": delay_days,
        "confidence": confidence,
    }


def llm_extract(title: str, snippet: str) -> Optional[Dict[str, Any]]:
    """
    Extracts structured disruption metadata using OpenAI if API key is set.
    Falls back gracefully if unavailable.
    """
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_key:
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
        prompt = f"""
You are an expert supply chain disruption parser.
Analyze this news item:
Title: {title}
Content: {snippet}

Extract the disruption details into JSON format:
{{
  "event_type": "PORT_RESTRICTION" | "VESSEL_DELAY" | "WEATHER_EVENT" | "STRIKE" | "CUSTOMS_HOLD" | "ROUTE_DIVERSION" | "UNKNOWN",
  "port_name": "Exact port name if mentioned, else null",
  "vessel_name": "Exact vessel name (e.g. 'MV Sentinel Star') if mentioned, else null",
  "estimated_delay_days": integer (estimated delay in days, default 7 if unspecified),
  "confidence": float between 0.0 and 1.0
}}
Return valid JSON only.
"""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a supply chain risk intelligence parser. Output valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        data["event_type"] = EventType(data["event_type"])
        return data
    except Exception as e:
        logger.warning(f"LLM extraction skipped or failed: {e}. Falling back to deterministic heuristic.")
        return None


# ─────────────────────────────────────────────────────────────────
# DETERMINISTIC ERP CORRELATION
# ─────────────────────────────────────────────────────────────────

def correlate_with_erp(
    port_name: Optional[str],
    vessel_name: Optional[str],
    delay_days: int,
    reference_date: date,
    conn: sqlite3.Connection,
) -> Tuple[Optional[str], Optional[str], Optional[date], SignalStatus, Optional[str]]:
    """
    Deterministically cross-references extracted entities with shipments in SQLite.
    Never invents shipment IDs.

    Returns:
        (shipment_id, po_id, revised_eta, status, ignore_reason)
    """
    query = """
        SELECT s.shipment_id, s.po_id, s.vessel_name, s.port_of_loading, s.original_eta, s.status
        FROM shipments s
        WHERE s.status IN ('IN_TRANSIT', 'DELAYED')
    """
    rows = conn.execute(query).fetchall()

    matched_row = None

    # Priority 1: Match by vessel name
    if vessel_name:
        v_norm = vessel_name.strip().lower()
        for r in rows:
            if r["vessel_name"] and v_norm in r["vessel_name"].lower():
                matched_row = r
                break

    # Priority 2: Match by port of loading
    if not matched_row and port_name:
        p_norm = port_name.strip().lower()
        for r in rows:
            if r["port_of_loading"] and (p_norm in r["port_of_loading"].lower() or r["port_of_loading"].lower() in p_norm):
                matched_row = r
                break

    if not matched_row:
        return (
            None,
            None,
            None,
            SignalStatus.IGNORED,
            f"No in-transit shipments match vessel '{vessel_name}' or port '{port_name}' in ERP."
        )

    shipment_id = matched_row["shipment_id"]
    po_id = matched_row["po_id"]
    orig_eta = date.fromisoformat(matched_row["original_eta"])
    revised_eta = orig_eta + timedelta(days=delay_days)

    return (
        shipment_id,
        po_id,
        revised_eta,
        SignalStatus.ACTIVE,
        None
    )


# ─────────────────────────────────────────────────────────────────
# SIGNAL AGENT CLASS
# ─────────────────────────────────────────────────────────────────

class SignalAgent:
    """
    Autonomous signal intelligence agent orchestrating news ingestion,
    entity parsing, and ERP correlation.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.output_file = SIGNAL_OUTPUT_FILE

    def parse_from_text(
        self,
        headline: str,
        content: str,
        source: SignalSource = SignalSource.NEWS_API,
        timestamp: Optional[datetime] = None,
        reference_date: date = date(2026, 10, 3),
    ) -> DisruptionEvent:
        """
        Parses raw text into a DisruptionEvent and resolves against mock_erp.db.
        """
        full_text = f"{headline} {content}"
        sig_time = timestamp or datetime.now(timezone.utc)

        # 1. Extraction: Try LLM first, fallback to Heuristic
        extracted = llm_extract(headline, content)
        if not extracted:
            extracted = heuristic_extract(full_text)

        # 2. Correlate with ERP
        conn = _get_db_connection(self.db_path)
        ship_id, po_id, rev_eta, status, reason = correlate_with_erp(
            port_name=extracted.get("port_name"),
            vessel_name=extracted.get("vessel_name"),
            delay_days=extracted["estimated_delay_days"],
            reference_date=reference_date,
            conn=conn,
        )
        conn.close()

        # 3. Construct and validate DisruptionEvent
        event = DisruptionEvent(
            event_type=extracted["event_type"],
            port_name=extracted.get("port_name"),
            vessel_name=extracted.get("vessel_name"),
            estimated_delay_days=extracted["estimated_delay_days"],
            confidence=extracted["confidence"],
            raw_headline=headline,
            source=source,
            signal_timestamp=sig_time,
            shipment_id=ship_id,
            po_id=po_id,
            revised_eta=rev_eta,
            status=status,
            ignore_reason=reason,
        )

        self._export_output(event)
        return event

    def run_from_serp(
        self,
        query: Optional[str] = None,
        force_tier: Optional[int] = None,
        reference_date: date = date(2026, 10, 3),
    ) -> Tuple[DisruptionEvent, str]:
        """
        Ingests disruption intelligence via SerpAPI (3-tier chain) and returns DisruptionEvent.
        """
        news_data, source_tier = fetch_disruption_news(query=query, force_tier=force_tier)
        results = news_data.get("news_results", [])
        if not results:
            # Fallback if no news results returned
            article = {
                "title": "General Maritime Logistics Status Report",
                "snippet": "No major marine terminal restrictions reported in operational sectors.",
                "date": "2026-10-03"
            }
        else:
            article = results[0]

        event = self.parse_from_text(
            headline=article.get("title", "Disruption News Item"),
            content=article.get("snippet", ""),
            source=SignalSource.SERP_API,
            reference_date=reference_date,
        )
        return event, source_tier

    def run_from_fixture(
        self,
        fixture_path: Path,
        reference_date: date = date(2026, 10, 3),
    ) -> DisruptionEvent:
        """
        Loads and processes a standardized NewsAPI/fixture JSON file.
        """
        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        articles = data.get("articles", [])
        if not articles:
            raise ValueError(f"Fixture {fixture_path} has no articles.")
        art = articles[0]

        return self.parse_from_text(
            headline=art.get("title", ""),
            content=f"{art.get('description', '')} {art.get('content', '')}",
            source=SignalSource.FIXTURE,
            reference_date=reference_date,
        )

    def _export_output(self, event: DisruptionEvent) -> None:
        """Persists the extracted disruption event to JSON for audit/debug."""
        payload = {
            "agent": "SignalAgent",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": event.status.value,
            "disruption_event": json.loads(event.model_dump_json()),
            "trace_log": (
                f"Signal Agent: detected [{event.event_type.value}] at {event.port_name or 'Unknown'} "
                f"delay=+{event.estimated_delay_days}d -> Status: {event.status.value} "
                f"(Shipment: {event.shipment_id or 'None'})"
            )
        }
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
