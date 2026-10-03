# -*- coding: utf-8 -*-
"""
tests/test_api.py
=================
Integration tests for Sentinel-SC FastAPI endpoints.
"""

import sys
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from api import app

class TestSentinelAPI(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_health(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertTrue(data["database"])

    def test_scenario_a_end_to_end_via_api(self):
        # 1. Trigger Scenario A
        trigger_res = self.client.post(
            "/api/disruption/trigger",
            json={"scenario": "A", "reference_date": "2026-10-03"}
        )
        self.assertEqual(trigger_res.status_code, 200)
        data = trigger_res.json()
        thread_id = data["thread_id"]

        self.assertEqual(data["current_step"], "AWAITING_HUMAN_APPROVAL")
        self.assertTrue(data["awaiting_approval"])
        self.assertEqual(data["impact_assessment"]["status"], "SHORTAGE")
        self.assertEqual(data["impact_assessment"]["total_otif_exposure"], 120000.0)

        plan = data["recovery_plan"]
        self.assertEqual(len(plan["candidates"]), 4)
        self.assertEqual(set(plan["vetoed_options"]), {"OPT-B", "OPT-C", "OPT-D"})
        self.assertEqual(plan["feasible_options"], ["OPT-A"])

        # 2. Check Status via GET
        status_res = self.client.get(f"/api/disruption/status/{thread_id}")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.json()
        self.assertTrue(status_data["awaiting_approval"])

        # 3. Approve Option OPT-A
        approve_res = self.client.post(
            "/api/disruption/approve",
            json={
                "thread_id": thread_id,
                "decision": "APPROVED",
                "selected_option_id": "OPT-A",
                "decided_by": "VP Supply Chain Operations"
            }
        )
        self.assertEqual(approve_res.status_code, 200)
        approved_data = approve_res.json()
        self.assertEqual(approved_data["current_step"], "EXECUTION_COMPLETE")
        self.assertEqual(approved_data["recovery_plan"]["approval_decision"], "APPROVED")
        self.assertEqual(approved_data["recovery_plan"]["selected_option_id"], "OPT-A")

    def test_scenario_b_via_api(self):
        res = self.client.post(
            "/api/disruption/trigger",
            json={"scenario": "B", "reference_date": "2026-10-03"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["current_step"], "HALTED_DELAY_ABSORBED")
        self.assertFalse(data["awaiting_approval"])
        self.assertEqual(data["impact_assessment"]["status"], "ABSORBED")
        self.assertEqual(data["impact_assessment"]["total_otif_exposure"], 0.0)

    def test_scenario_c_false_positive_via_api(self):
        res = self.client.post(
            "/api/disruption/trigger",
            json={"scenario": "C", "reference_date": "2026-10-03"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["current_step"], "HALTED_SIGNAL_IGNORED")
        self.assertFalse(data["awaiting_approval"])
        self.assertIsNone(data["impact_assessment"])

    def test_erp_summary_endpoint(self):
        res = self.client.get("/api/erp/summary")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("materials", data)
        self.assertIn("suppliers", data)
        self.assertIn("rules", data)
        self.assertEqual(len(data["rules"]), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
