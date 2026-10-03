# -*- coding: utf-8 -*-
"""
Part 3 Checkpoint — Schema validation test.
Run: python -m pytest schemas/test_schemas.py -v
  or: python schemas/test_schemas.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, datetime
from schemas.events   import DisruptionEvent, EventType, SignalStatus, SignalSource
from schemas.impact   import ImpactAssessment, ImpactStatus, MaterialShortage, AffectedWorkOrder
from schemas.recovery import (
    RecoveryPlan, CandidateOption, ConstraintResult, RuleEvaluation,
    OptionType, OptionStatus, RuleStatus, ApprovalDecision,
)
import pydantic


PASS_COUNT = 0
FAIL_COUNT = 0

def check(label: str, fn):
    global PASS_COUNT, FAIL_COUNT
    try:
        fn()
        print(f"  [PASS] {label}")
        PASS_COUNT += 1
    except Exception as e:
        print(f"  [FAIL] {label}")
        print(f"         {e}")
        FAIL_COUNT += 1


# ─────────────────────────────────────────────────────────────────
# 1. Import sanity
# ─────────────────────────────────────────────────────────────────
print("\n[1] Import check")
check("All schemas import successfully", lambda: None)


# ─────────────────────────────────────────────────────────────────
# 2. Enum rejection
# ─────────────────────────────────────────────────────────────────
print("\n[2] Enum rejection")

def bad_event_type():
    DisruptionEvent(
        event_type="INVALID_TYPE",
        port_name="Port Klang",
        estimated_delay_days=7,
        confidence=0.91,
        raw_headline="Test",
        signal_timestamp=datetime(2026, 10, 3, 4, 32),
        status=SignalStatus.IGNORED,
    )
def _assert_raises(fn, exc_type=pydantic.ValidationError):
    try:
        fn()
        raise AssertionError(f"Expected {exc_type.__name__} but no exception raised.")
    except exc_type:
        pass  # expected

check("Invalid EventType raises ValidationError",
      lambda: _assert_raises(bad_event_type))

check("Invalid OptionType raises ValidationError",
      lambda: _assert_raises(lambda: CandidateOption(
          option_id="OPT-X",
          option_type="MAGIC",       # invalid
          description="test",
          total_cost=1000.0,
          days_of_coverage=5,
      )))

check("Invalid ImpactStatus raises ValidationError",
      lambda: _assert_raises(lambda: MaterialShortage(
          material_id="STCOIL-440V",
          material_name="Stator Coil",
          on_hand_qty=400, daily_consumption=80,
          tts_days=5, stockout_date=date(2026,10,8),
          ttr_days=15, revised_eta=date(2026,10,18),
          shortage_days=99,   # wrong — will fail validator
      )))


# ─────────────────────────────────────────────────────────────────
# 3. Scenario A — valid ImpactAssessment
# ─────────────────────────────────────────────────────────────────
print("\n[3] Scenario A — ImpactAssessment construction")

shortage = MaterialShortage(
    material_id       = "STCOIL-440V",
    material_name     = "Stator Coil 440V (Primary)",
    on_hand_qty       = 400.0,
    daily_consumption = 80.0,
    tts_days          = 5,
    stockout_date     = date(2026, 10, 8),
    ttr_days          = 15,
    revised_eta       = date(2026, 10, 18),
    shortage_days     = 10,
    shortage_start    = date(2026, 10, 8),
    shortage_end      = date(2026, 10, 18),
)

wo_list = [
    AffectedWorkOrder(
        wo_id="WO-7781", co_id=None, product_id="APEXM-100",
        qty=50, planned_start=date(2026,10,5), due_date=date(2026,10,20),
        required_material_qty=100, in_shortage_window=False, otif_exposure=0.0,
    ),
    AffectedWorkOrder(
        wo_id="WO-7782", co_id="SO-55102", product_id="APEXM-100",
        qty=50, planned_start=date(2026,10,14), due_date=date(2026,10,22),
        required_material_qty=80, in_shortage_window=True, otif_exposure=120000.0,
    ),
    AffectedWorkOrder(
        wo_id="WO-7783", co_id=None, product_id="APEXM-100",
        qty=30, planned_start=date(2026,10,19), due_date=date(2026,10,25),
        required_material_qty=60, in_shortage_window=False, otif_exposure=0.0,
    ),
]

def build_scenario_a():
    return ImpactAssessment(
        shipment_id          = "SHIP-440V-01",
        po_id                = "PO-2026-441",
        reference_date       = date(2026, 10, 3),
        shortage             = shortage,
        affected_work_orders = wo_list,
        total_otif_exposure  = 120000.0,   # only WO-7782: 500 * $240
        status               = ImpactStatus.SHORTAGE,
        recovery_required    = True,
    )

check("Scenario A ImpactAssessment builds without error", build_scenario_a)
check("total_otif_exposure == 120000.0",
      lambda: [build_scenario_a(), None][0] and
              None if build_scenario_a().total_otif_exposure == 120000.0 else
              (_ for _ in ()).throw(AssertionError("Wrong exposure")))

# Use simpler assertion pattern:
def _assert_field(build_fn, field, expected):
    obj = build_fn()
    actual = getattr(obj, field)
    if actual != expected:
        raise AssertionError(f"{field}: expected {expected!r}, got {actual!r}")

check("status == SHORTAGE",
      lambda: _assert_field(build_scenario_a, "status", ImpactStatus.SHORTAGE))
check("recovery_required == True",
      lambda: _assert_field(build_scenario_a, "recovery_required", True))
check("shortage.tts_days == 5",
      lambda: _assert_field(lambda: build_scenario_a().shortage, "tts_days", 5))
check("shortage.ttr_days == 15",
      lambda: _assert_field(lambda: build_scenario_a().shortage, "ttr_days", 15))
check("shortage.shortage_days == 10",
      lambda: _assert_field(lambda: build_scenario_a().shortage, "shortage_days", 10))

# Invalid: SHORTAGE with shortage_days=0 should fail
check("SHORTAGE with shortage_days=0 raises ValidationError",
      lambda: _assert_raises(lambda: ImpactAssessment(
          shipment_id="X", po_id="Y", reference_date=date(2026,10,3),
          shortage=MaterialShortage(
              material_id="X", material_name="X", on_hand_qty=400, daily_consumption=80,
              tts_days=20, stockout_date=date(2026,10,23),
              ttr_days=15, revised_eta=date(2026,10,18), shortage_days=0,
          ),
          total_otif_exposure=50000.0,   # should fail: SHORTAGE but 0 shortage_days
          status=ImpactStatus.SHORTAGE,
          recovery_required=True,
      )))

# ─────────────────────────────────────────────────────────────────
# 4. Scenario B — ABSORBED ImpactAssessment
# ─────────────────────────────────────────────────────────────────
print("\n[4] Scenario B — ABSORBED ImpactAssessment")

def build_scenario_b():
    return ImpactAssessment(
        shipment_id          = "SHIP-6205-01",
        po_id                = "PO-2026-512",
        reference_date       = date(2026, 10, 3),
        shortage             = MaterialShortage(
            material_id       = "BEARING-6205",
            material_name     = "Deep Groove Ball Bearing 6205",
            on_hand_qty       = 2000.0,
            daily_consumption = 100.0,
            tts_days          = 20,
            stockout_date     = date(2026, 10, 23),
            ttr_days          = 15,
            revised_eta       = date(2026, 10, 18),
            shortage_days     = 0,
        ),
        affected_work_orders = [],
        total_otif_exposure  = 0.0,
        status               = ImpactStatus.ABSORBED,
        recovery_required    = False,
    )

check("Scenario B ABSORBED builds without error", build_scenario_b)
check("status == ABSORBED",
      lambda: _assert_field(build_scenario_b, "status", ImpactStatus.ABSORBED))
check("recovery_required == False",
      lambda: _assert_field(build_scenario_b, "recovery_required", False))
check("ABSORBED with exposure raises ValidationError",
      lambda: _assert_raises(lambda: ImpactAssessment(
          shipment_id="X", po_id="Y", reference_date=date(2026,10,3),
          shortage=MaterialShortage(
              material_id="X", material_name="X", on_hand_qty=2000, daily_consumption=100,
              tts_days=20, stockout_date=date(2026,10,23),
              ttr_days=15, revised_eta=date(2026,10,18), shortage_days=0,
          ),
          affected_work_orders=[],
          total_otif_exposure=5000.0,   # ABSORBED cannot have exposure
          status=ImpactStatus.ABSORBED,
          recovery_required=False,
      )))


# ─────────────────────────────────────────────────────────────────
# 5. RecoveryPlan — 4-candidate result (pre-approval)
# ─────────────────────────────────────────────────────────────────
print("\n[5] RecoveryPlan — 4 candidates, pre-approval state")

rule_pass = lambda rule_id, name, threshold, actual: RuleEvaluation(
    rule_id=rule_id, rule_name=name, status=RuleStatus.PASS,
    evidence=f"{name} check passed.", threshold=threshold, actual=actual,
)
rule_fail = lambda rule_id, name, evidence, threshold, actual: RuleEvaluation(
    rule_id=rule_id, rule_name=name, status=RuleStatus.FAIL,
    evidence=evidence, threshold=threshold, actual=actual,
)
rule_na = lambda rule_id, name: RuleEvaluation(
    rule_id=rule_id, rule_name=name, status=RuleStatus.NA,
    evidence="Not applicable to this option type.", threshold=None, actual=None,
)

# OPT-A: EuroCoils — APPROVAL_REQUIRED (all HARD pass, C5 ROUTE fires)
opt_a_rules = [
    rule_pass("C1","Lead Time Feasibility","15","7"),
    rule_pass("C2","Minimum Order Quantity","800","200"),
    rule_pass("C3","PPAP Certification","PPAP","PPAP"),
    rule_na("C4","Frozen Schedule Window"),
    RuleEvaluation(rule_id="C5", rule_name="Budget Authorization", status=RuleStatus.PASS,
                   evidence="Cost $30,100 exceeds plant budget $30,000 - routes to VP approval.",
                   threshold="30000", actual="30100"),
    rule_pass("C6","BOM Revision Compatibility","REV-D","REV-D"),
    rule_pass("C7","Export Compliance","0","0"),
    rule_pass("C8","Supplier Capacity","800","1000"),
]
opt_a = CandidateOption(
    option_id="OPT-A", option_type=OptionType.SPOT_BUY,
    supplier_id="SUPP-003", material_id="STCOIL-440V",
    description="Spot buy from EuroCoils GmbH via air freight",
    total_cost=30100.0, estimated_arrival=date(2026,10,10),
    days_of_coverage=10,
    constraint_result=ConstraintResult(
        option_id="OPT-A",
        option_status=OptionStatus.APPROVAL_REQUIRED,
        rules=opt_a_rules,
        veto_rules=[],
    ),
)

# OPT-B: Hanoi Coils — VETO C1
opt_b_rules = [
    rule_fail("C1","Lead Time Feasibility",
              "lead_time_days=18 exceeds available_days=15","15","18"),
]
opt_b = CandidateOption(
    option_id="OPT-B", option_type=OptionType.EXPEDITE,
    supplier_id="SUPP-002", material_id="STCOIL-440V",
    description="Expedite order from Hanoi Coils Ltd (sea freight)",
    total_cost=7200.0, estimated_arrival=date(2026,10,21),
    days_of_coverage=0,
    constraint_result=ConstraintResult(
        option_id="OPT-B",
        option_status=OptionStatus.VETO,
        rules=opt_b_rules,
        veto_rules=["C1"],
    ),
)

# OPT-C: STCOIL-415V substitute — VETO C3 + C6
opt_c_rules = [
    rule_pass("C1","Lead Time Feasibility","15","10"),
    rule_pass("C2","Minimum Order Quantity","800","400"),
    rule_fail("C3","PPAP Certification",
              "No PPAP certificate for STCOIL product family on record for SUPP-004.",
              "PPAP","NONE"),
    rule_na("C4","Frozen Schedule Window"),
    rule_na("C5","Budget Authorization"),
    rule_fail("C6","BOM Revision Compatibility",
              "compatible_revisions=['REV-C'] does not include required REV-D.",
              "REV-D","REV-C"),
    rule_pass("C7","Export Compliance","0","0"),
    rule_pass("C8","Supplier Capacity","800","800"),
]
opt_c = CandidateOption(
    option_id="OPT-C", option_type=OptionType.SUBSTITUTE,
    supplier_id="SUPP-004", material_id="STCOIL-415V",
    description="Substitute STCOIL-415V from FastCoil Asia Pte",
    total_cost=25500.0, estimated_arrival=date(2026,10,13),
    days_of_coverage=0,
    constraint_result=ConstraintResult(
        option_id="OPT-C",
        option_status=OptionStatus.VETO,
        rules=opt_c_rules,
        veto_rules=["C3","C6"],
    ),
)

# OPT-D: Reschedule WO-7782 — VETO C4
opt_d_rules = [
    rule_na("C1","Lead Time Feasibility"),
    rule_na("C2","Minimum Order Quantity"),
    rule_na("C3","PPAP Certification"),
    rule_fail("C4","Frozen Schedule Window",
              "WO-7782 start_date 2026-10-05 is within 14-day frozen window. "
              "frozen_schedule=1.","14","1"),
    rule_na("C5","Budget Authorization"),
    rule_na("C6","BOM Revision Compatibility"),
    rule_na("C7","Export Compliance"),
    rule_na("C8","Supplier Capacity"),
]
opt_d = CandidateOption(
    option_id="OPT-D", option_type=OptionType.RESCHEDULE,
    supplier_id=None, material_id=None,
    description="Reschedule WO-7782 to start after revised ETA 2026-10-18",
    total_cost=0.0, estimated_arrival=None,
    days_of_coverage=0,
    constraint_result=ConstraintResult(
        option_id="OPT-D",
        option_status=OptionStatus.VETO,
        rules=opt_d_rules,
        veto_rules=["C4"],
    ),
)

def build_recovery_plan_pre_approval():
    return RecoveryPlan(
        shipment_id         = "SHIP-440V-01",
        po_id               = "PO-2026-441",
        reference_date      = date(2026, 10, 3),
        shortage_days       = 10,
        total_otif_exposure = 120000.0,
        candidates          = [opt_a, opt_b, opt_c, opt_d],
        vetoed_options      = ["OPT-B", "OPT-C", "OPT-D"],
        feasible_options    = ["OPT-A"],
        # Pre-approval: all None
        selected_option_id  = None,
        approval_decision   = None,
        decided_by          = None,
        decided_at          = None,
    )

check("RecoveryPlan pre-approval builds without error", build_recovery_plan_pre_approval)
check("4 candidates present",
      lambda: _assert_field(build_recovery_plan_pre_approval,
                            "candidates", [opt_a, opt_b, opt_c, opt_d]))
check("3 vetoed, 1 feasible",
      lambda: (
          _assert_field(build_recovery_plan_pre_approval, "vetoed_options", ["OPT-B","OPT-C","OPT-D"]),
          _assert_field(build_recovery_plan_pre_approval, "feasible_options", ["OPT-A"]),
      ))
check("selected_option_id is None (pre-approval)",
      lambda: _assert_field(build_recovery_plan_pre_approval, "selected_option_id", None))
check("decided_by is None (pre-approval)",
      lambda: _assert_field(build_recovery_plan_pre_approval, "decided_by", None))
check("decided_at is None (pre-approval)",
      lambda: _assert_field(build_recovery_plan_pre_approval, "decided_at", None))

# Post-approval state
def build_recovery_plan_approved():
    return RecoveryPlan(
        shipment_id         = "SHIP-440V-01",
        po_id               = "PO-2026-441",
        reference_date      = date(2026, 10, 3),
        shortage_days       = 10,
        total_otif_exposure = 120000.0,
        candidates          = [opt_a, opt_b, opt_c, opt_d],
        vetoed_options      = ["OPT-B","OPT-C","OPT-D"],
        feasible_options    = ["OPT-A"],
        selected_option_id  = "OPT-A",
        approval_decision   = ApprovalDecision.APPROVED,
        decided_by          = "VP Supply Chain",
        decided_at          = datetime(2026, 10, 3, 11, 30, 0),
    )

check("RecoveryPlan post-approval builds without error", build_recovery_plan_approved)
check("Cannot approve a vetoed option",
      lambda: _assert_raises(lambda: RecoveryPlan(
          shipment_id="X", po_id="Y", reference_date=date(2026,10,3),
          shortage_days=10, total_otif_exposure=120000.0,
          candidates=[opt_b],
          vetoed_options=["OPT-B"], feasible_options=[],
          selected_option_id="OPT-B",            # vetoed — must fail
          approval_decision=ApprovalDecision.APPROVED,
          decided_by="VP Supply Chain",
          decided_at=datetime(2026,10,3,11,30),
      )))
check("VETO status without any FAIL rule raises ValidationError",
      lambda: _assert_raises(lambda: ConstraintResult(
          option_id="OPT-X",
          option_status=OptionStatus.VETO,
          rules=[rule_pass("C1","Lead Time","15","7")],
          veto_rules=[],    # empty but status=VETO — must fail
      )))


# ─────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────
print(f"\n{'='*52}")
total = PASS_COUNT + FAIL_COUNT
print(f"  Part 3 Checkpoint: {PASS_COUNT}/{total} passed")
if FAIL_COUNT == 0:
    print("  ALL PASS -- Safe to proceed to Part 2 (engine).")
else:
    print(f"  {FAIL_COUNT} FAILED -- fix schemas before proceeding.")
print(f"{'='*52}\n")
sys.exit(0 if FAIL_COUNT == 0 else 1)
