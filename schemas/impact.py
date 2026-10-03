# -*- coding: utf-8 -*-
"""
schemas/impact.py
=================
Contract for the deterministic Impact Engine output.

Flow:
  DisruptionEvent (events.py)
      -> Impact Engine (pure Python, zero LLM)
      -> ImpactAssessment         <- this schema
      -> Recovery Agent / Constraint Engine

Architecture rules:
  - All numeric fields (tts_days, ttr_days, exposure) are computed
    by impact_math.py and bom_traversal.py -- never by LLM.
  - SHORTAGE vs ABSORBED emerges from:
        status = "SHORTAGE" if ttr_days > tts_days else "ABSORBED"
    No scenario flag participates in this decision.
  - recovery_required mirrors the status: True iff SHORTAGE.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


# ─────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────

class ImpactStatus(str, Enum):
    """
    SHORTAGE : ttr_days > tts_days  -> recovery workflow required
    ABSORBED : tts_days >= ttr_days -> buffer absorbs delay, monitor only
    """
    SHORTAGE = "SHORTAGE"
    ABSORBED = "ABSORBED"


# ─────────────────────────────────────────────────────────────────
# SUB-MODELS
# ─────────────────────────────────────────────────────────────────

class AffectedWorkOrder(BaseModel):
    """
    A work order impacted by the material shortage.

    Exposure model (pegged-order value):
        exposure = co.qty * co.unit_price
        IF planned_start IN [shortage_start, shortage_end] (inclusive end)
        AND co_id IS NOT NULL (WO has a customer-order peg)
        ELSE 0.0

    WO-7781: co_id=None (no peg) -> otif_exposure=0.0
    WO-7782: co_id='SO-55102', start in window -> 500 * $240 = $120,000
    WO-7783: start after window -> otif_exposure=0.0
    """
    wo_id                : str            = Field(..., description="Work order ID.")
    co_id                : Optional[str]  = Field(None, description="Linked customer order ID. None = no peg -> zero exposure.")
    product_id           : str            = Field(..., description="Finished good being produced.")
    qty                  : float          = Field(..., description="Units scheduled in this WO.")
    planned_start        : date           = Field(..., description="WO planned start date.")
    due_date             : date           = Field(..., description="WO due date.")
    required_material_qty: float          = Field(..., description="Units of disrupted material needed.")
    in_shortage_window   : bool           = Field(..., description="True if planned_start in [shortage_start, shortage_end] inclusive.")
    otif_exposure        : float          = Field(..., ge=0.0, description="co.qty * co.unit_price if in_window and has_peg, else 0.0.")


class MaterialShortage(BaseModel):
    """Shortage profile for the disrupted material."""
    material_id        : str   = Field(..., description="Disrupted material (e.g. STCOIL-440V).")
    material_name      : str   = Field(..., description="Human-readable material name.")
    on_hand_qty        : float = Field(..., description="Current on-hand inventory (units).")
    daily_consumption  : float = Field(..., description="Daily consumption rate (units/day).")
    tts_days           : int   = Field(
        ...,
        ge=0,
        description="Time-to-Stockout in days. Computed: floor(on_hand_qty / daily_consumption)."
    )
    stockout_date      : date  = Field(
        ...,
        description="Date on which stock hits zero. Computed: reference_date + tts_days."
    )
    ttr_days           : int   = Field(
        ...,
        ge=0,
        description="Time-to-Recover in days. Computed: (revised_eta - reference_date).days."
    )
    revised_eta        : date  = Field(..., description="Revised delivery date from DisruptionEvent.")
    shortage_days      : int   = Field(
        ...,
        ge=0,
        description="Net shortage duration: max(0, ttr_days - tts_days)."
    )
    shortage_start     : Optional[date] = Field(
        None,
        description="First day of stockout. Equals stockout_date when status=SHORTAGE."
    )
    shortage_end       : Optional[date] = Field(
        None,
        description="Last day of shortage. Equals revised_eta when status=SHORTAGE."
    )

    @model_validator(mode="after")
    def validate_shortage_window(self) -> "MaterialShortage":
        """shortage_days must equal ttr_days - tts_days (never negative)."""
        expected = max(0, self.ttr_days - self.tts_days)
        if self.shortage_days != expected:
            raise ValueError(
                f"shortage_days={self.shortage_days} does not match "
                f"max(0, ttr_days({self.ttr_days}) - tts_days({self.tts_days})) = {expected}."
            )
        return self


# ─────────────────────────────────────────────────────────────────
# MAIN MODEL
# ─────────────────────────────────────────────────────────────────

class ImpactAssessment(BaseModel):
    """
    Complete output of the deterministic Impact Engine.
    This is the contract between the engine and the Recovery Agent.

    Scenario A (SHORTAGE):
        tts_days=5, ttr_days=15, shortage_days=10
        status=SHORTAGE, recovery_required=True
        total_otif_exposure ~ $120,000

    Scenario B (ABSORBED):
        tts_days=20, ttr_days=15, shortage_days=0
        status=ABSORBED, recovery_required=False
        total_otif_exposure=0.0
    """

    # ── Event reference ───────────────────────────────────────────
    shipment_id        : str  = Field(..., description="Disrupted shipment ID from DisruptionEvent.")
    po_id              : str  = Field(..., description="Linked purchase order ID.")
    reference_date     : date = Field(..., description="Scenario anchor date (2026-10-03).")

    # ── Material shortage profile ─────────────────────────────────
    shortage           : MaterialShortage = Field(
        ...,
        description="Shortage arithmetic for the disrupted material."
    )

    # ── BOM / WO impact ──────────────────────────────────────────
    affected_work_orders: List[AffectedWorkOrder] = Field(
        default_factory=list,
        description="Work orders whose material requirements overlap the shortage window. "
                    "Empty list when status=ABSORBED."
    )

    # ── Financial exposure ────────────────────────────────────────
    total_otif_exposure: float = Field(
        ...,
        ge=0.0,
        description="Sum of otif_exposure across all affected_work_orders (USD). "
                    "Computed deterministically by impact_math.py. "
                    "0.0 when status=ABSORBED."
    )

    # ── Decision ─────────────────────────────────────────────────
    status             : ImpactStatus = Field(
        ...,
        description="SHORTAGE if ttr_days > tts_days, else ABSORBED. "
                    "Computed from data -- never set by a scenario flag."
    )
    recovery_required  : bool = Field(
        ...,
        description="True iff status=SHORTAGE. Mirrors status for downstream clarity."
    )

    @model_validator(mode="after")
    def status_matches_shortage_days(self) -> "ImpactAssessment":
        """Enforce data-emergent status: SHORTAGE iff shortage_days > 0."""
        sd = self.shortage.shortage_days
        if self.status == ImpactStatus.SHORTAGE and sd == 0:
            raise ValueError(
                "status=SHORTAGE requires shortage_days > 0."
            )
        if self.status == ImpactStatus.ABSORBED and sd > 0:
            raise ValueError(
                f"status=ABSORBED requires shortage_days=0, got {sd}. "
                "Check TTS/TTR arithmetic."
            )
        return self

    @model_validator(mode="after")
    def recovery_required_matches_status(self) -> "ImpactAssessment":
        """recovery_required must mirror status."""
        expected = (self.status == ImpactStatus.SHORTAGE)
        if self.recovery_required != expected:
            raise ValueError(
                f"recovery_required={self.recovery_required} does not match "
                f"status={self.status}. Must be {expected}."
            )
        return self

    @model_validator(mode="after")
    def absorbed_has_no_exposure(self) -> "ImpactAssessment":
        """ABSORBED path must not carry financial exposure."""
        if self.status == ImpactStatus.ABSORBED and self.total_otif_exposure != 0.0:
            raise ValueError(
                "status=ABSORBED must have total_otif_exposure=0.0."
            )
        return self

    @model_validator(mode="after")
    def absorbed_has_no_work_orders(self) -> "ImpactAssessment":
        """ABSORBED path must have an empty affected_work_orders list."""
        if self.status == ImpactStatus.ABSORBED and self.affected_work_orders:
            raise ValueError(
                "status=ABSORBED must have an empty affected_work_orders list."
            )
        return self

    class Config:
        use_enum_values = False
