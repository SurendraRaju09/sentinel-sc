# -*- coding: utf-8 -*-
"""
agents/test_agents.py
=====================
Part 4 Verification Suite:
  1. SignalAgent: Scenario A (Port Klang / MV Sentinel Star -> ACTIVE)
  2. SignalAgent: Scenario B (Hamburg / MV Nordic Blue -> ACTIVE)
  3. SignalAgent: False Positive (Manila / MV Pacific Trader -> IGNORED)
  4. SignalAgent: No false-positive hub defaulting (Detroit truck strike -> IGNORED)
  5. RecoveryAgent: 3 Vetoed, 1 Feasible (OPT-A)
  6. RecoveryAgent: Rejects approval of vetoed option
  7. RecoveryAgent: Approves feasible option with audit metadata
  8. LangGraph: Scenario A end-to-end with interrupt() and SqliteSaver resume
  9. LangGraph: Scenario B routes to handle_absorbed and halts
  10. LangGraph: False positive routes to handle_ignored and halts
"""

import os
import sys
import unittest
import sqlite3
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from agents.signal_agent import SignalAgent
from agents.recovery_agent import RecoveryAgent
from schemas.events import SignalStatus
from schemas.impact import ImpactStatus
from schemas.recovery import ApprovalDecision
from engine.impact_math import run_impact
from graph import build_graph, SentinelState
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command


class TestSignalAgent(unittest.TestCase):

    def setUp(self):
        self.agent = SignalAgent()

    def test_scenario_a_fixture(self):
        fixture = BASE_DIR / "integrations" / "fixtures" / "port_disruption.json"
        event = self.agent.run_from_fixture(fixture)
        self.assertEqual(event.status, SignalStatus.ACTIVE)
        self.assertEqual(event.shipment_id, "SHIP-440V-01")
        self.assertEqual(event.po_id, "PO-2026-441")
        self.assertEqual(str(event.revised_eta), "2026-10-18")

    def test_scenario_b_fixture(self):
        fixture = BASE_DIR / "integrations" / "fixtures" / "scenario_b.json"
        event = self.agent.run_from_fixture(fixture)
        self.assertEqual(event.status, SignalStatus.ACTIVE)
        self.assertEqual(event.shipment_id, "SHIP-6205-01")
        self.assertEqual(str(event.revised_eta), "2026-10-18")

    def test_false_positive_fixture(self):
        fixture = BASE_DIR / "integrations" / "fixtures" / "false_positive.json"
        event = self.agent.run_from_fixture(fixture)
        self.assertEqual(event.status, SignalStatus.IGNORED)
        self.assertIsNone(event.shipment_id)
        self.assertIn("No in-transit shipments match", event.ignore_reason)

    def test_no_false_positive_port_klang_default(self):
        """Unrelated text must NOT default to Port Klang."""
        event = self.agent.parse_from_text(
            headline="Warehouse forklift battery supply issues in Detroit",
            content="Local manufacturing facilities reported slight logistic delays due to regional logistics."
        )
        self.assertEqual(event.status, SignalStatus.IGNORED)
        self.assertIsNone(event.port_name)


class TestRecoveryAgent(unittest.TestCase):

    def setUp(self):
        self.agent = RecoveryAgent()
        conn = sqlite3.connect(str(BASE_DIR / "erp" / "mock_erp.db"))
        conn.row_factory = sqlite3.Row
        self.impact_a = run_impact(conn, "SHIP-440V-01", date(2026, 10, 3))
        conn.close()

    def test_propose_plan(self):
        plan = self.agent.propose_plan(self.impact_a)
        self.assertEqual(len(plan.candidates), 4)
        self.assertEqual(set(plan.vetoed_options), {"OPT-B", "OPT-C", "OPT-D"})
        self.assertEqual(plan.feasible_options, ["OPT-A"])
        self.assertIsNone(plan.selected_option_id)
        self.assertIsNotNone(plan.audit_summary)

    def test_reject_vetoed_option_approval(self):
        plan = self.agent.propose_plan(self.impact_a)
        with self.assertRaises(ValueError):
            self.agent.apply_human_decision(
                plan=plan,
                decision=ApprovalDecision.APPROVED,
                selected_option_id="OPT-B",  # Vetoed!
            )

    def test_approve_feasible_option(self):
        plan = self.agent.propose_plan(self.impact_a)
        approved = self.agent.apply_human_decision(
            plan=plan,
            decision=ApprovalDecision.APPROVED,
            selected_option_id="OPT-A",
            decided_by="VP Supply Chain"
        )
        self.assertEqual(approved.approval_decision, ApprovalDecision.APPROVED)
        self.assertEqual(approved.selected_option_id, "OPT-A")
        self.assertEqual(approved.decided_by, "VP Supply Chain")
        self.assertIsNotNone(approved.decided_at)


class TestLangGraphOrchestration(unittest.TestCase):

    def setUp(self):
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.checkpointer = SqliteSaver(conn)
        self.graph = build_graph(checkpointer=self.checkpointer)

    def test_scenario_a_interrupt_and_resume(self):
        config = {"configurable": {"thread_id": "thread-scenario-a"}}
        init_state = {
            "fixture_path": str(BASE_DIR / "integrations" / "fixtures" / "port_disruption.json"),
            "reference_date": "2026-10-03",
            "audit_trail": [],
        }

        # Step 1: Run until interrupt at human gate
        state_after_gate = self.graph.invoke(init_state, config=config)
        self.assertEqual(state_after_gate["current_step"], "AWAITING_HUMAN_APPROVAL")
        self.assertIn("__interrupt__", state_after_gate)

        # Step 2: Resume with Human Decision
        resume_cmd = Command(resume={
            "decision": "APPROVED",
            "selected_option_id": "OPT-A",
            "decided_by": "VP Supply Chain Operations"
        })
        final_state = self.graph.invoke(resume_cmd, config=config)

        self.assertEqual(final_state["current_step"], "EXECUTION_COMPLETE")
        final_plan = final_state["recovery_plan"]
        self.assertEqual(final_plan["approval_decision"], "APPROVED")
        self.assertEqual(final_plan["selected_option_id"], "OPT-A")
        self.assertEqual(final_plan["decided_by"], "VP Supply Chain Operations")

    def test_scenario_b_halts_at_absorbed(self):
        config = {"configurable": {"thread_id": "thread-scenario-b"}}
        init_state = {
            "fixture_path": str(BASE_DIR / "integrations" / "fixtures" / "scenario_b.json"),
            "reference_date": "2026-10-03",
            "audit_trail": [],
        }
        res = self.graph.invoke(init_state, config=config)
        self.assertEqual(res["current_step"], "HALTED_DELAY_ABSORBED")
        self.assertIsNone(res.get("recovery_plan"))
        self.assertNotIn("__interrupt__", res)

    def test_false_positive_halts_at_ignored(self):
        config = {"configurable": {"thread_id": "thread-false-positive"}}
        init_state = {
            "fixture_path": str(BASE_DIR / "integrations" / "fixtures" / "false_positive.json"),
            "reference_date": "2026-10-03",
            "audit_trail": [],
        }
        res = self.graph.invoke(init_state, config=config)
        self.assertEqual(res["current_step"], "HALTED_SIGNAL_IGNORED")
        self.assertIsNone(res.get("impact_assessment"))
        self.assertNotIn("__interrupt__", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
