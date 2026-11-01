"""OpenClaw outcomes must not inherit benchmark forced-choice assumptions."""
import unittest
from unittest.mock import patch

from checks.test_query_backend import source, case_for, answer, submit_answer
from prodebugger.benchmark_scope import allowed, scope, validate_cause
from prodebugger.debug_tools import DebugTools
from prodebugger.direct_submit import normalize, outcome_context, packet, policy_for, POLICY
from prodebugger.query_trace import query, commit
from prodebugger.record_bridge import build_bundle
from prodebugger.window_trace import normalize_candidate, schema_for


class OpenClawSubmitChecks(unittest.TestCase):
    def setUp(self):
        self.tools = DebugTools(build_bundle(source()))
        # Isolate the submission policy from the independent telemetry importer.
        self.tools.bundle.source["task_type"] = "openclaw"
        self.tools.execute("view", {"spec": "requirements", "scope": {"mode": "global"}}, "map")
        self.tools.execute("route_cases", {"candidates": [case_for(self.tools)]}, "route")

    def prepare(self, status="uncertain", role=None):
        query(self.tools, "case1", "SELECT * FROM SEARCH_TEXT('decision 1','plan')")
        assessment = answer()
        assessment["candidates"][0].update(status=status, is_root_cause=role,
            fault_class="inefficient_plan" if status == "error" else None)
        commit(self.tools, assessment, "assess")

    def outcome(self, status):
        return {"status": status,
            "evidence": [{"step": 3, "module": "observation", "quote": "You are carrying: an apple 1."}]
                if status != "unknown" else [],
            "checks": [], "limitations": ["Fixture verifier boundary; not a semantic success assertion."],
            "provenance": "checked task contract"}

    def abstain(self, status="uncertain", candidates=True):
        return {"status": status, "selected_candidate_id": None, "fault_class": None,
            "output_quote": None, "reasoning": "The checked task evidence determines the outcome; the local hypothesis is separate.",
            "comparisons": [{"candidate_id": "trace-window:case1:1", "reasoning": "Insufficient unrecovered causal support."}]
                if candidates else [],
            "repair": None, "uncertainty": "Available evidence does not establish a final failure root."}

    def test_unknown_outcome_accepts_uncertain_and_cannot_force_uncertain_candidate(self):
        self.prepare()
        with patch("prodebugger.direct_submit.outcome_context", return_value=self.outcome("unknown")):
            result = normalize(self.tools, self.abstain())
            self.assertEqual(result["status"], "uncertain")
            self.assertIsNone(result["root"])
            self.assertFalse(result["accepted"])
            with self.assertRaisesRegex(ValueError, "verified task failure"):
                normalize(self.tools, submit_answer())
        with patch("prodebugger.direct_submit.outcome_context", return_value=self.outcome("failure")):
            with self.assertRaisesRegex(ValueError, "TRACE error"):
                normalize(self.tools, submit_answer())

    def test_definite_local_error_still_uncertain_when_task_outcome_unknown(self):
        self.prepare("error", True)
        with patch("prodebugger.direct_submit.outcome_context", return_value=self.outcome("unknown")):
            self.assertEqual(normalize(self.tools, self.abstain())["status"], "uncertain")
            with self.assertRaisesRegex(ValueError, "verified task failure"):
                normalize(self.tools, submit_answer())

    def test_verified_success_overrides_historical_error_and_allows_no_failure(self):
        self.prepare("error", False)
        with patch("prodebugger.direct_submit.outcome_context", return_value=self.outcome("success")):
            result = normalize(self.tools, self.abstain("no_failure"))
            self.assertEqual(result["status"], "no_failure")
            self.assertIsNone(result["root"])
            self.assertEqual(len(result["alternatives"]), 1)
            self.assertEqual(result["task_outcome"]["status"], "success")
            with self.assertRaisesRegex(ValueError, "verified task success"):
                normalize(self.tools, submit_answer())
        with patch("prodebugger.direct_submit.outcome_context", return_value=self.outcome("unknown")):
            with self.assertRaisesRegex(ValueError, "no_failure requires verified task success"):
                normalize(self.tools, self.abstain("no_failure"))

    def test_verified_failure_requires_definite_non_recovered_candidate(self):
        self.prepare("error", True)
        with patch("prodebugger.direct_submit.outcome_context", return_value=self.outcome("failure")):
            result = normalize(self.tools, submit_answer())
            self.assertEqual(result["status"], "diagnosed")
            self.assertEqual(result["root"]["step"], 1)
            self.assertTrue(result["accepted"])
        self.tools.window_conclusions["case1"]["candidates"][0]["is_root_cause"] = False
        with patch("prodebugger.direct_submit.outcome_context", return_value=self.outcome("failure")):
            self.assertIs(packet(self.tools)["candidates"][0]["assessment"]["is_root_cause"], False)
            with self.assertRaisesRegex(ValueError, "recovered/non-root"):
                normalize(self.tools, submit_answer())

    def test_empty_completed_trace_can_abstain_or_report_verified_success(self):
        query(self.tools, "case1", "SELECT * FROM SEARCH_TEXT('decision 1','plan')")
        commit(self.tools, {"window_id": "case1", "candidates": [], "uncertainty": "No supported module fault."}, "assess")
        for outcome, status in [("unknown", "uncertain"), ("failure", "uncertain"), ("success", "no_failure")]:
            with self.subTest(outcome=outcome), patch("prodebugger.direct_submit.outcome_context", return_value=self.outcome(outcome)):
                self.assertEqual(normalize(self.tools, self.abstain(status, candidates=False))["status"], status)

    def test_abstention_requires_null_root_fields_and_explicit_evidence_gap(self):
        self.prepare()
        with patch("prodebugger.direct_submit.outcome_context", return_value=self.outcome("unknown")):
            bad = self.abstain()
            bad["output_quote"] = ""
            with self.assertRaisesRegex(ValueError, "require null"):
                normalize(self.tools, bad)
            bad = self.abstain()
            bad["uncertainty"] = ""
            with self.assertRaisesRegex(ValueError, "uncertainty must be"):
                normalize(self.tools, bad)

    def test_task_specific_scope_keeps_benchmark_behavior_and_real_action_format_errors(self):
        self.assertNotIn("format_error", allowed("alfworld")["action"])
        self.assertEqual(policy_for("alfworld"), POLICY)
        self.assertIn("format_error", allowed("openclaw")["action"])
        self.assertEqual(set(allowed("openclaw")), {"memory", "reflection", "plan", "action"})
        self.assertEqual(scope("openclaw")["excluded_causes"], [])
        formatting = {"status": "error", "proposition": "Tool argument serialization was rejected."}
        with self.assertRaisesRegex(ValueError, "excluded"):
            validate_cause("action", formatting)
        validate_cause("action", formatting, "openclaw")
        candidate = answer()["candidates"][0]
        candidate.update(module="action", output_quote="look", fault_class="format_error", status="error",
            is_root_cause=True, proposition=formatting["proposition"])
        normalized = normalize_candidate(self.tools, candidate, {"lo": 1, "hi": 3})
        self.assertEqual(normalized["fault_class"], "format_error")
        self.assertIsInstance(normalized["primary_fid"], int)
        enums = schema_for("openclaw")["function"]["parameters"]["properties"]["candidates"]["items"]["properties"]["module"]["enum"]
        self.assertNotIn("system", enums)

    def test_missing_modules_and_system_cannot_be_invented(self):
        candidate = answer()["candidates"][0]
        candidate.update(module="system", fault_class="tool_execution_error", output_quote="look")
        with self.assertRaisesRegex(ValueError, "step/module"):
            normalize_candidate(self.tools, candidate, {"lo": 1, "hi": 3})
        self.tools.bundle.source["steps"][0]["parts"]["memory"] = None
        candidate.update(module="memory", fault_class=None, output_quote="I hold apple 1.")
        with self.assertRaisesRegex(ValueError, "not an exact source substring"):
            normalize_candidate(self.tools, candidate, {"lo": 1, "hi": 3})

    def test_packet_uses_verifier_and_does_not_trust_imported_outcome_label(self):
        self.prepare()
        self.tools.bundle.source["outcome"] = {"status": "success", "evidence": []}
        with patch("prodebugger.openclaw_contract.evaluate", return_value=self.outcome("unknown")) as verify:
            result = packet(self.tools)
            verify.assert_called_once_with(self.tools.bundle.source)
            self.assertEqual(result["task_outcome"]["status"], "unknown")
            self.assertNotEqual(result["policy"], POLICY)
            self.assertIn("uncertain", result["result_contract"]["allowed_statuses"])


if __name__ == "__main__":
    unittest.main()
