# -*- coding: utf-8 -*-
"""
schemas/events.py
=================
Contract for external disruption signals.

Flow:
  Raw external text (NewsAPI / fixture)
      -> LLM entity extraction
      -> DisruptionEvent          <- this schema
      -> deterministic DB correlation
      -> ImpactAssessment (impact.py)

Architecture rule:
  The LLM populates this model from unstructured text.
  The LLM must NEVER populate internal IDs (shipment_id, po_id) directly --
  those are resolved by the deterministic DB correlation layer using
  port_name and vessel_name as lookup keys.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ─────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    PORT_RESTRICTION  = "PORT_RESTRICTION"
    VESSEL_DELAY      = "VESSEL_DELAY"
    WEATHER_EVENT     = "WEATHER_EVENT"
    STRIKE            = "STRIKE"
    CUSTOMS_HOLD      = "CUSTOMS_HOLD"
    ROUTE_DIVERSION   = "ROUTE_DIVERSION"
    UNKNOWN           = "UNKNOWN"


class SignalStatus(str, Enum):
    """
    ACTIVE  : signal correlates to at least one internal shipment -> proceed to engine
    IGNORED : no internal correlation found -> halt, log, no recovery workflow
    """
    ACTIVE  = "ACTIVE"
    IGNORED = "IGNORED"


class SignalSource(str, Enum):
    NEWS_API    = "NEWS_API"
    OPEN_WEATHER = "OPEN_WEATHER"
    AIS         = "AIS"
    SERP_API    = "SERP_API"
    FIXTURE     = "FIXTURE"   # recorded offline payload
    MANUAL      = "MANUAL"    # manually entered


# ─────────────────────────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────────────────────────

class DisruptionEvent(BaseModel):
    """
    Structured output of the Signal Agent.

    LLM populates: event_type, port_name, vessel_name, estimated_delay_days,
                   confidence, raw_headline, source.
    DB correlation layer populates: shipment_id, po_id, revised_eta, status.
    """

    # ── LLM-extracted fields ──────────────────────────────────────
    event_type          : EventType = Field(
        ...,
        description="Classified disruption type extracted from signal text."
    )
    port_name           : Optional[str] = Field(
        None,
        description="Port name extracted from signal (e.g. 'Port Klang'). "
                    "Used as DB lookup key -- not a direct FK."
    )
    vessel_name         : Optional[str] = Field(
        None,
        description="Vessel name extracted from signal (e.g. 'MV Sentinel Star'). "
                    "Used as DB lookup key -- not a direct FK."
    )
    estimated_delay_days: int = Field(
        ...,
        ge=0,
        description="Estimated delay in days extracted from signal text."
    )
    confidence          : float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="LLM confidence score for the extracted event [0.0, 1.0]."
    )
    raw_headline        : str = Field(
        ...,
        description="Original headline or first sentence from the source article. "
                    "Preserved verbatim for audit trail and UI display."
    )
    source              : SignalSource = Field(
        SignalSource.FIXTURE,
        description="Origin of the raw signal."
    )
    signal_timestamp    : datetime = Field(
        ...,
        description="publishedAt or ingestion timestamp of the raw signal."
    )

    # ── DB-resolved fields (set by correlation layer, not LLM) ────
    shipment_id         : Optional[str] = Field(
        None,
        description="Resolved by DB lookup (vessel_name/port_name -> shipments table). "
                    "None if no correlation found."
    )
    po_id               : Optional[str] = Field(
        None,
        description="Resolved from shipment -> purchase_orders. "
                    "None if no correlation found."
    )
    revised_eta         : Optional[date] = Field(
        None,
        description="Computed as original_eta + estimated_delay_days. "
                    "Set by DB correlation layer, not LLM."
    )

    # ── Signal status (set by correlation layer) ──────────────────
    status              : SignalStatus = Field(
        SignalStatus.IGNORED,
        description="ACTIVE if DB correlation found a matching shipment; "
                    "IGNORED otherwise. Default IGNORED until correlation runs."
    )
    ignore_reason       : Optional[str] = Field(
        None,
        description="Human-readable reason when status=IGNORED "
                    "(e.g. 'No active shipment matches vessel or port')."
    )

    @model_validator(mode="after")
    def active_requires_correlation(self) -> "DisruptionEvent":
        """ACTIVE status requires shipment_id and revised_eta to be set."""
        if self.status == SignalStatus.ACTIVE:
            if not self.shipment_id:
                raise ValueError(
                    "status=ACTIVE requires shipment_id to be set by DB correlation layer."
                )
            if not self.revised_eta:
                raise ValueError(
                    "status=ACTIVE requires revised_eta to be set by DB correlation layer."
                )
        return self

    @model_validator(mode="after")
    def ignored_requires_reason(self) -> "DisruptionEvent":
        """IGNORED status should carry an explanation."""
        if self.status == SignalStatus.IGNORED and not self.ignore_reason:
            # Soft default -- not a hard error
            object.__setattr__(
                self, "ignore_reason",
                "No active shipment correlation found for extracted port/vessel."
            )
        return self

    model_config = ConfigDict(use_enum_values=False)  # keep enum instances, not raw strings
