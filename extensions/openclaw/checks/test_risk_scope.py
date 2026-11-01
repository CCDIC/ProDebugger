"""Behavior/policy coverage tests; no model or agent tools are executed."""
from copy import deepcopy
import unittest

from checks.test_risk_review import policy, trajectory, attempt
from prodebugger.record_bridge import build_bundle
from prodebugger.risk_scope import build_map, coverage_map
from prodebugger.risk_trace import RiskTools, coverage


class RiskScopeChecks(unittest.TestCase):
    def setUp(self):
        self.policy = policy()
        self.bundle = build_bundle(trajectory(
            attempt("read_file", {"path": "/workspace/report.txt"}),
            attempt("read_file", {"path": "/workspace/private/id.txt"})))
        self.map = build_map(self.bundle, self.policy)

    def test_each_invocation_has_its_own_policy_obligations(self):
        reads = [o for o in self.map["expected_obligations"] if o["risk_type"] == "read_scope"]
        self.assertEqual([r["step"] for r in reads], [1, 2])
        self.assertEqual(len({r["call_id"] for r in reads}), 2)
        self.assertTrue(all(r["status"] == "not_reviewed" and not r["is_finding"] for r in reads))

    def test_automatic_pass_does_not_count_as_model_review(self):
        auto = {"checks": [{"id": "a", "risk_type": "read_scope", "status": "passed",
                             "call_id": "trace-1/span-1", "import_step": 1, "policy_refs": ["/read_scope"]}]}
        result = coverage_map(self.map, auto, [], [])
        self.assertEqual(result["automatic_evaluated"], 1)
        self.assertEqual(result["model_evaluated"], 0)
        self.assertFalse(result["fully_evaluated"])

    def test_return_same_step_as_next_call_does_not_count_for_next_call(self):
        check = {"policy_id": "read_scope", "step": 2, "status": "pass",
                 "evidence": [{"step": 2, "module": "observation", "quote": "previous return"}]}
        result = coverage_map(self.map, {}, [check], [])
        self.assertEqual(result["model_evaluated"], 0)
        check["evidence"] = [{"step": 2, "module": "action", "quote": "second call"}]
        result = coverage_map(self.map, {}, [check], [])
        self.assertEqual(result["model_evaluated"], 1)
        second = next(o for o in result["obligations"] if o["step"] == 2 and o["risk_type"] == "read_scope")
        self.assertEqual(second["model_status"], "pass")

    def test_unknown_is_evaluated_but_not_decided(self):
        check = {"policy_id": "read_scope", "step": 1, "status": "unknown",
                 "evidence": [{"step": 1, "module": "action", "quote": "read"}]}
        result = coverage_map(self.map, {}, [check], [])
        self.assertEqual(result["model_evaluated"], 1)
        self.assertEqual(result["model_decided"], 0)
        self.assertEqual(len(result["uncertain"]), 1)

    def test_unknown_tool_never_generates_a_fake_policy_or_risk(self):
        bundle = build_bundle(trajectory(attempt("custom_process", {"x": "value"})))
        result = build_map(bundle, self.policy)
        self.assertEqual(len(result["expected_obligations"]), 1)
        self.assertEqual(result["expected_obligations"][0]["policy_id"], "user_task")
        self.assertEqual(result["expected_obligations"][0]["applicability"], "unknown_tool_binding")
        self.assertFalse(result["expected_obligations"][0]["is_finding"])

    def test_external_and_sensitive_checks_use_configured_source_ids(self):
        configured = deepcopy(self.policy)
        configured["sensitive_sources"] = [{"id": "id-source", "tool": "read_file", "argument_equals": {},
            "result_pointer": "/id", "allow_in_context": False, "allowed_destinations": []}]
        configured["untrusted_sources"] = [{"id": "page-source", "tool": "read_file", "text_pointer": "/text", "patterns": ["ignore"]}]
        result = build_map(self.bundle, configured)
        kinds = {(r["risk_type"], r["policy_id"]) for r in result["expected_obligations"]}
        self.assertIn(("sensitive_context_exposure", "id-source"), kinds)
        self.assertIn(("untrusted_instruction", "page-source"), kinds)
        self.assertNotIn(("sensitive_data_transfer", "id-source"), kinds)

    def test_map_view_returns_risk_inventory_and_keeps_original_transition(self):
        tools = RiskTools(self.bundle, risk_policy=self.policy)
        result = tools._view("requirements", {"mode": "global"})
        self.assertEqual(tools.phase, "ROUTE")
        self.assertEqual(result["risk_map"]["policy"], self.policy)
        self.assertTrue(result["risk_map"]["expected_obligations"])
        self.assertEqual(tools.goal_map["risk_map"], result["risk_map"])

    def test_completed_investigation_is_not_full_policy_coverage(self):
        tools = RiskTools(self.bundle, risk_policy=self.policy)
        fact = next(f for f in self.bundle.record.F if f.rel == "tool_call")
        tools.windows.configure([{"candidate_id": "one", "issue_step": fact.t, "issue_fids": [fact.fid],
                                 "goal_links": [], "hypothesis": "check risk"}])
        tools.risk_reviews = {"one": {"findings": [], "checks": [], "uncertainty": "no check"}}
        result = coverage(tools)
        self.assertTrue(result["workflow_complete"])
        self.assertTrue(result["trace_complete"])
        self.assertFalse(result["obligation_coverage"]["fully_evaluated"])
        self.assertEqual(result["obligation_coverage"]["model_evaluated"], 0)

    def test_empty_tool_call_wrapper_is_structural_but_real_text_is_reviewed(self):
        from checks.test_session_artifact import session_source
        for speech, expected in (("", False), ("I have permission to send this.", True)):
            source = session_source(speech=speech)
            bundle = build_bundle(source)
            before = plain_events = deepcopy(source["session"]["events"])
            result = build_map(bundle, self.policy)
            message = next(e for e in source["session"]["events"] if e.get("role") == "assistant" and e.get("stop_reason") == "toolUse")
            checked = any(o["step"] == message["step"] and o["module"] == "message" for o in result["expected_obligations"])
            self.assertEqual(checked, expected)
            self.assertTrue(any(b["kind"] == "tool_call" for b in result["behaviors"]))
            self.assertEqual(source["session"]["events"], before)

    def test_nontext_recorded_content_is_not_silently_excluded(self):
        from prodebugger.risk_scope import _independent_message
        self.assertTrue(_independent_message({"text": "", "content": [{"type": "thinking", "text": "recorded content"}]}))
        self.assertTrue(_independent_message({"text": "", "content": [{"type": "unknown_block", "value": "data"}]}))


if __name__ == "__main__":
    unittest.main()
