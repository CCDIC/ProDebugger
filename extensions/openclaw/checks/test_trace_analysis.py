"""Analysis boundaries: exact calls, explicit prerequisites, independent sources."""
import copy
import json
import unittest

from checks.test_openclaw_contract import source
from checks.test_safety_audit import capture, policy
from checks.test_parsed_view import CASE
from prodebugger.record_bridge import build_bundle, digest, plain
from prodebugger.safety_audit import audit_source
from prodebugger.trace_analysis import analyze_source
from prodebugger.trace_explorer import graph_source


def analyze(data, diagnosis=None):
    configured = policy()
    report = audit_source(data, configured)
    return graph_source(data, report, {**CASE, "policy": configured}, diagnosis=diagnosis)


def call(data, number=1):
    return next(node for node in data["nodes"] if node["entity_id"] == f"call:trace-1/span-{number}")


class TraceAnalysisChecks(unittest.TestCase):
    def test_reuses_original_rules_without_missing_mental_module_violations(self):
        data = source()
        graph = analyze(data)
        result = graph["analysis"]
        self.assertEqual(result["rule_evaluations"], plain(build_bundle(data).evaluations))
        self.assertEqual(len([row for row in result["rule_applicability"] if row["source"] == "reused_abstract_rules"]), 7)
        self.assertFalse(any(row["category"] == "rule" for row in result["issues"]))
        self.assertTrue(all(row["state"] == "unknown" for row in result["rule_evaluations"]))
        self.assertEqual(call(graph)["visual_state"], "passed")

    def test_task_target_error_inside_policy_is_red_but_not_safety_risk(self):
        data = source([{"action": {"arguments": {"path": "other.txt"}}}])
        before = digest(data)
        graph = analyze(data)
        self.assertEqual(graph["report"]["status"], "safe")
        self.assertEqual(call(graph)["visual_state"], "error")
        issue = next(row for row in graph["analysis"]["issues"] if row["rule_id"] == "OC-TASK-001")
        self.assertEqual(issue["call_id"], "trace-1/span-1")
        self.assertEqual(issue["import_steps"], [1])
        self.assertTrue(any(row.get("query_id") for row in issue["evidence"]))
        self.assertFalse(graph["analysis"]["root_candidates"])
        self.assertEqual(digest(data), before)

    def test_rule_and_safety_are_separate_and_both_badges_survive(self):
        graph = analyze(source([{"action": {"arguments": {"path": "/workspace/.env"}}}]))
        node = call(graph)
        self.assertEqual(node["visual_state"], "error")
        self.assertEqual({row["category"] for row in node["badges"]}, {"rule", "safety"})
        self.assertEqual(graph["analysis"]["summary"]["finding_count"], 2)
        self.assertEqual(graph["analysis"]["summary"]["affected_call_count"], 1)
        self.assertEqual(graph["analysis"]["summary"]["affected_trajectory_count"], 1)

    def test_same_tool_has_precise_call_and_neutral_shared_tool(self):
        graph = analyze(source([{}, {"action": {"arguments": {"path": "/workspace/.env"}}}]))
        self.assertEqual(call(graph)["visual_state"], "passed")
        self.assertEqual(call(graph, 2)["visual_state"], "risk")
        tool = next(node for node in graph["nodes"] if node["kind"] == "tool")
        self.assertEqual(tool["visual_state"], "neutral")
        self.assertFalse(tool["issue_ids"])
        safety = next(row for row in graph["analysis"]["issues"] if row["category"] == "safety")
        self.assertEqual(safety["node_ids"], [call(graph, 2)["id"]])

    def test_error_result_is_runtime_observation_not_automatic_decision_error(self):
        graph = analyze(source([{"response": {"status": "error", "error": "ENOENT"}}]))
        issues = graph["analysis"]["issues"]
        self.assertEqual([row["category"] for row in issues], ["runtime"])
        self.assertEqual(call(graph)["visual_state"], "passed")
        self.assertTrue(any(row["category"] == "runtime" for row in call(graph)["badges"]))

    def test_incomplete_capture_and_missing_results_never_get_task_violation(self):
        data = source([{"action": {"arguments": {"path": "other.txt"}}}], complete_capture=False)
        graph = analyze(data)
        self.assertFalse(any(row["rule_id"] == "OC-TASK-001" for row in graph["analysis"]["issues"]))
        # Global capture limits remain a banner; the recorded local path check
        # is still applicable. A missing return below is a local unknown.
        self.assertEqual(call(graph)["visual_state"], "passed")
        self.assertTrue(any(row['source'] == 'capture_coverage' for row in graph['analysis']['issues']))
        data["telemetry"]["coverage"].update(declared_complete=True, content_complete=True)
        response = json.loads(data["steps"][1]["env_text"])
        del response["result"]
        data["steps"][1]["env_text"] = json.dumps(response)
        graph = analyze(data)
        self.assertEqual(call(graph)["visual_state"], "unknown")
        self.assertFalse(any(row["rule_id"] == "OC-TASK-001" for row in graph["analysis"]["issues"]))

    def test_recovery_or_no_explicit_contract_does_not_trigger_narrow_extension(self):
        for data in (source([{"action": {"arguments": {"path": "other.txt"}}}, {}]), source()):
            if len(data["steps"]) == 2:
                del data["task_contract"]
            graph = analyze(data)
            self.assertFalse(any(row["rule_id"] == "OC-TASK-001" for row in graph["analysis"]["issues"]))

    def model(self, data, status="error"):
        bundle = build_bundle(data)
        action = next(f for f in bundle.snapshot()["facts"] if f["rel"] == "tool_call")
        candidate = {"status": status, "is_root_cause": None, "step": 1, "module": "action",
            "fault_class": "wrong_tool_arguments", "output_quote": '"path": "notes.txt"',
            "proposition": "test model hypothesis", "reasoning": "test evidence explanation", "uncertainty": "causal role unknown",
            "primary_fid": action["fid"], "evidence_for": [action["fid"]], "evidence_against": []}
        return {"id": "test-only-synthetic-job", "source_sha256": digest(data), "policy_sha256": digest(policy()), "status": "succeeded",
                "assessments": [{"window_id": "w1", "candidates": [candidate]}],
                "result": {"status": "uncertain", "root": None, "uncertainty": "not enough task evidence"}}

    def test_model_suspicion_is_not_red_and_failed_diagnosis_preserves_checks(self):
        data = source()
        job = self.model(data, "uncertain")
        graph = analyze(data, job)
        self.assertEqual(call(graph)["visual_state"], "unknown")
        self.assertFalse(graph["analysis"]["root_candidates"][0]["proven"])
        job.update(status="failed", error="simulated transport failure for unit test", result=None, assessments=[])
        graph = analyze(data, job)
        self.assertTrue(graph["analysis"]["rule_evaluations"])
        self.assertEqual(graph["report"]["status"], "safe")
        self.assertTrue(any(row["category"] == "uncertainty" for row in graph["analysis"]["issues"]))

    def test_model_error_requires_current_primary_fact_quote_and_module(self):
        data = source()
        job = self.model(data)
        graph = analyze(data, job)
        self.assertEqual(call(graph)["visual_state"], "error")
        self.assertEqual(len(graph["analysis"]["root_candidates"]), 1)
        for change in ({"primary_fid": 999}, {"output_quote": "fabricated"}, {"module": "memory"}):
            changed = copy.deepcopy(job)
            changed["assessments"][0]["candidates"][0].update(change)
            with self.subTest(change=change):
                self.assertNotEqual(call(analyze(data, changed))["visual_state"], "error")
        job["source_sha256"] = "stale"
        self.assertFalse(any(row["category"] == "model" for row in analyze(data, job)["analysis"]["issues"]))

    def test_submitted_root_selection_is_retained_as_unproven_candidate(self):
        data = source()
        job = self.model(data)
        job["result"] = {"status": "diagnosed", "root": {"root_id": "trace-window:w1:1"}}
        roots = analyze(data, job)["analysis"]["root_candidates"]
        self.assertTrue(roots[0]["selected"])
        self.assertFalse(roots[0]["proven"])


if __name__ == "__main__":
    unittest.main()
