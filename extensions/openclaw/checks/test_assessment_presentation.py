"""Coverage presentation never silently upgrades unknowns or rewrites verdicts."""
from copy import deepcopy
import unittest

from checks.risk_fixtures import fixture, policy, safety_policy
from prodebugger.assessment_presentation import add_check_summaries, classify
from prodebugger.record_bridge import build_bundle, digest
from prodebugger.risk_review import review_risks, validate_policy
from prodebugger.safety_audit import audit_source
from prodebugger.trace_analysis import analyze_source


class AssessmentPresentationTests(unittest.TestCase):
    def setUp(self):
        self.source = fixture("presentation-checks", "仅在reports目录读取两份报告，不向外发送。", [
            ("read_file", {"path": "/workspace/reports/a.json"}, {"count": 42}),
            ("read_file", {"path": "/workspace/reports/b.json"}, {"count": 43}),
            ("exec", {"command": "print('recorded only')"}, {"text": "recorded only"})])
        self.policy = policy()
        self.policy["read_scope"]["allowed_roots"] = ["/workspace/reports"]
        self.policy["sensitive_sources"] = [{"id": "no-id-context", "tool": "read_file",
            "argument_equals": {"/path": "/workspace/reports/a.json"},
            "result_pointer": "/details/personal_id", "allow_in_context": False,
            "allowed_destinations": []}]
        self.policy = validate_policy(self.policy)
        self.safety_policy = safety_policy()
        self.report = audit_source(self.source, self.safety_policy)
        self.bundle = build_bundle(self.source, online=False)
        self.automatic = review_risks(self.source, self.policy, bundle=self.bundle, safety_report=self.report)
        self.case = {"id": self.source["trajectory_id"], "policy": self.safety_policy, "risk_policy": self.policy}
        self.review = {"policy": self.policy, "automatic": self.automatic}
        self.base = self.analyze()
        self.calls = self.base["calls"]
        self.first, self.second, self.unsupported = self.calls

    def analyze(self, model=None, automatic=None):
        return analyze_source(self.source, self.report, self.case, bundle=self.bundle,
            risk_review={"policy": self.policy, "automatic": self.automatic if automatic is None else automatic,
                         "model": model})

    def check(self, call=None, status="pass", policy_id="read_scope"):
        call = call or self.first
        return {"policy_id": policy_id, "step": call["import_step"], "status": status,
            "reason": "模型按策略核对了本次调用的路径。", "evidence": [
                {"step": call["import_step"], "module": "action", "quote": call["tool"],
                 "fact_ids": [call["fid"]]}], "missing": [], "limitations": []}

    def job(self, checks):
        return {"id": "synthetic-presentation-test-only", "status": "succeeded",
            "source_sha256": digest(self.source), "policy_sha256": digest(self.policy),
            "analysis_version": self.bundle.fingerprint,
            "result": {"checks": checks, "findings": []}}

    def model_rows(self, analysis):
        return [r for r in analysis["check_items"] if r["source"] == "risk_model"]

    def test_call_and_exact_return_share_summary_without_duplicate_check_counts(self):
        summary = self.base["check_summaries"][self.first["call_id"]]
        mapping = self.source["telemetry"]["source_map"][0]
        self.assertEqual(summary["shared_steps"], [mapping["action_step"], mapping["result_step"]])
        self.assertIs(self.base["event_assessments"][str(mapping["action_step"])],
                      self.base["event_assessments"][str(mapping["result_step"])])
        self.assertEqual(len({r["id"] for r in self.base["check_items"]}),
                         self.base["assessment_summary"]["unique_check_count"])

    def test_recorded_response_and_missing_configured_member_have_distinct_meaning(self):
        summary = self.base["check_summaries"][self.first["call_id"]]
        row = next(r for r in summary["items"] if r["display_status"] == "policy_field_unavailable")
        self.assertEqual(row["policy_fields"][0]["result_pointer"], "/details/personal_id")
        self.assertTrue(row["policy_fields"][0]["response_recorded"])
        self.assertTrue(summary["has_passed"])
        self.assertTrue(summary["has_unresolved"])
        self.assertEqual(summary["overall"], "mixed")
        self.assertFalse(any(r["display_status"] == "policy_field_unavailable" for r in
                             self.base["check_summaries"][self.second["call_id"]]["items"]))
        issue = next(i for i in self.base["issues"] if i.get("display_status") == "policy_field_unavailable")
        self.assertEqual(issue["state"], "unknown")
        self.assertIn("策略指定字段", issue["display_label"])

    def test_unsupported_unconfigured_and_rule_prerequisites_are_not_risks(self):
        unknown = self.base["check_summaries"][self.unsupported["call_id"]]
        self.assertGreater(unknown["counts"]["unsupported"], 0)
        self.assertEqual(unknown["overall"], "unsupported")
        self.assertEqual(classify({"state": "unknown", "missing": ["supported_tool_binding"]}, {}, {}),
                         "unconfigured")
        originals = [r for r in self.base["check_items"] if r["source"] == "reused_abstract_rules"]
        self.assertTrue(originals)
        self.assertTrue(any(r["display_status"] == "prerequisite_unavailable" for r in originals))
        self.assertFalse(any(r["display_status"] == "risk" for r in originals))

    def test_valid_model_pass_is_displayed_with_provenance_and_does_not_erase_unknown(self):
        job = self.job([self.check()])
        analysis = self.analyze(job)
        row = self.model_rows(analysis)[0]
        self.assertTrue(row["applied"])
        self.assertTrue(row["evidence_validated"])
        self.assertEqual(row["display_status"], "passed")
        self.assertEqual(row["job_id"], job["id"])
        self.assertEqual(row["source_sha256"], digest(self.source))
        self.assertEqual(row["analysis_version"], self.bundle.fingerprint)
        self.assertEqual(row["call_ids"], [self.first["call_id"]])
        first = analysis["check_summaries"][self.first["call_id"]]
        self.assertEqual(first["overall"], "mixed")
        self.assertGreater(first["counts"]["policy_field_unavailable"], 0)
        self.assertFalse(any(r["source"] == "risk_model" for r in
                             analysis["check_summaries"][self.second["call_id"]]["items"]))

    def test_rejected_model_checks_cannot_become_green_or_attach_to_another_call(self):
        checks = [
            {"status": "arbitrary"}, {"evidence": []}, {"missing": ["actual_required_field"]},
            {"call_id": self.second["call_id"]}, {"span_id": self.second["span_id"]},
            {"policy_id": "fabricated-policy"}, {"step": 999},
            {"evidence": [{"step": self.first["import_step"], "module": "action", "quote": "fabricated", "fact_ids": [self.first["fid"]]}]},
            {"evidence": [{"step": self.first["import_step"], "module": "message", "quote": self.first["tool"], "fact_ids": [self.first["fid"]]}]},
            {"evidence": [{"step": self.first["import_step"], "module": "action", "quote": self.first["tool"], "fact_ids": [self.second["fid"]]}]},
            {"evidence": [{"step": self.first["import_step"], "module": "action", "quote": self.first["tool"], "fact_ids": [self.first["fid"]], "start": 0, "end": 1}]},
        ]
        for mutation in checks:
            with self.subTest(mutation=mutation):
                analysis = self.analyze(self.job([{**self.check(), **mutation}]))
                row = self.model_rows(analysis)[0]
                self.assertEqual(row["display_status"], "unvalidated")
                self.assertFalse(row["applied"])
                self.assertEqual(row["call_ids"], [])
                self.assertFalse(analysis["model_check_audit"][0]["accepted"])

    def test_version_source_and_policy_integrity_gate_model_checks(self):
        for field in ("source_sha256", "policy_sha256", "analysis_version"):
            with self.subTest(field=field):
                job = self.job([self.check()]); job[field] = "stale"
                analysis = self.analyze(job)
                self.assertFalse(any(r["applied"] for r in self.model_rows(analysis)))
                self.assertEqual(analysis["rule_evaluations"], self.base["rule_evaluations"])
                if field == "analysis_version":
                    self.assertEqual(self.model_rows(analysis)[0]["analysis_version"], "stale")
                    self.assertEqual(self.model_rows(analysis)[0]["current_analysis_version"], self.bundle.fingerprint)
        job = self.job([self.check()]); job.pop("analysis_version")
        job["compilation"] = {"canonical_fingerprint": self.bundle.fingerprint}
        self.assertTrue(self.model_rows(self.analyze(job))[0]["applied"])

    def test_return_evidence_does_not_satisfy_action_scope_check(self):
        result = next(f for f in self.bundle.snapshot()["facts"] if f["rel"] == "tool_result"
            and any(a.get("kind") == "call" and a.get("index") == self.first["call_id"] for a in f["args"]))
        row = {**self.check(), "step": result["t"], "evidence": [
            {"step": result["t"], "module": "observation", "quote": self.first["tool"], "fact_ids": [result["fid"]]}]}
        analysis = self.analyze(self.job([row]))
        self.assertIn("applicable_tool_action_required", analysis["model_check_audit"][0]["reasons"])
        self.assertFalse(self.model_rows(analysis)[0]["applied"])

    def test_missing_policy_member_blocks_models_inapplicability_pass(self):
        row = self.check(policy_id="no-id-context")
        analysis = self.analyze(self.job([row]))
        checked = self.model_rows(analysis)[0]
        self.assertEqual(checked["original_status"], "pass")
        self.assertEqual(checked["display_status"], "policy_field_unavailable")
        self.assertFalse(checked["applied"])

    def test_pass_risk_disagreement_remains_visible(self):
        analysis = self.analyze(self.job([self.check(status="risk")]))
        summary = analysis["check_summaries"][self.first["call_id"]]
        self.assertTrue(summary["has_conflict"])
        self.assertTrue(summary["has_passed"])
        self.assertEqual(summary["overall"], "risk")
        self.assertEqual(summary["disagreements"][0]["policy_id"], "read_scope")

    def test_presentation_does_not_mutate_input_policy_reports_or_saved_model(self):
        job = self.job([self.check()])
        source, policy_data, report, automatic, model = map(deepcopy,
            (self.source, self.policy, self.report, self.automatic, job))
        self.analyze(job)
        self.assertEqual((self.source, self.policy, self.report, self.automatic, job),
                         (source, policy_data, report, automatic, model))

    def test_structured_capture_gaps_keep_original_details_and_do_not_break_graph(self):
        missing = [{"kind": "export_truncated", "source": {"document": "cloud-export.json"}},
                   "complete_capture_declaration"]
        analysis = deepcopy(self.base)
        analysis["issues"].append({"id": "historical-structured-gap", "category": "uncertainty",
            "source": "capture_coverage", "state": "unknown", "missing": deepcopy(missing),
            "call_ids": [], "import_steps": []})
        add_check_summaries(analysis, self.source,
            {f["fid"]: f for f in self.bundle.snapshot()["facts"]},
            {c["call_id"]: c for c in self.calls}, self.policy)
        issue = analysis["issues"][-1]
        self.assertEqual(issue["display_status"], "missing_evidence")
        self.assertEqual(issue["missing"], missing)
        self.assertEqual(classify({"state": "unknown", "missing": {"kind": "legacy_gap"}}, {}, {}),
                         "missing_evidence")


if __name__ == "__main__":
    unittest.main()
