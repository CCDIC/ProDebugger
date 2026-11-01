"""Cross-module risk UI data, import, queue boundaries and private evaluation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from checks.risk_fixtures import fixture, policy, safety_policy
from prodebugger.record_bridge import digest, write_json
from prodebugger.risk_jobs import RiskReviewJobs
from prodebugger.risk_validation import compare
from prodebugger.safety_workbench import Handler, SafetyHTTPServer, SafetyStore
from prodebugger.trace_analysis import analyze_source


class RiskPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SafetyStore(Path(self.temp.name) / "data")
        self.manager = RiskReviewJobs(self.store.folder, start_worker=False)
        self.store._risk_jobs = self.manager
        self.addCleanup(self.close)
        self.server = SafetyHTTPServer(("127.0.0.1", 0), Handler)
        self.server.store = self.store
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.p = policy()
        self.p["read_scope"]["allowed_roots"] = ["/workspace/reports"]
        self.source = fixture("platform-case", "只允许读取reports目录。", [
            ("read_file", {"path": "/workspace/reports/a.txt"}, {"text": "a"}),
            ("read_file", {"path": "/workspace/private/b.txt"}, {"text": "b"})])

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)
        self.manager.close()
        if self.store._diagnoses:
            self.store._diagnoses.close()

    def get(self, path):
        with urlopen(self.url + path, timeout=10) as response:
            return json.load(response)

    def post(self, path, data):
        request = Request(self.url + path, data=json.dumps(data).encode(),
                          headers={"Content-Type": "application/json", "Origin": self.url})
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response)

    def add(self):
        return self.store.add_source(self.source, safety_policy(), title="No private label",
                                     case_id="platform-case", risk_policy=self.p)

    def test_import_runs_rules_persists_and_returns_exact_call_risk(self):
        status, result = self.post("/api/safety/import", {"source": self.source,
            "policy": safety_policy(), "risk_policy": self.p})
        self.assertEqual(status, 201)
        cid = result["case"]["id"]
        report = self.get("/api/safety/risk/" + cid)["automatic"]
        self.assertEqual(report["status"], "unsafe")
        self.assertEqual(len(report["findings"]), 1)
        self.assertEqual(report["findings"][0]["span_id"], self.source["telemetry"]["source_map"][1]["span_id"])
        saved = self.store._find(cid)
        self.assertTrue(self.store._resolve(saved["risk_report_path"]).is_file())
        self.assertTrue((self.store.folder / "imports" / cid / "risk-policy.json").is_file())

    def test_same_tool_only_offending_call_is_marked(self):
        self.add()
        graph = self.get("/api/safety/graph/platform-case")
        calls = sorted((n for n in graph["nodes"] if n["kind"] == "call"), key=lambda n: n["index"])
        bad = self.source["telemetry"]["source_map"][1]
        for call in calls:
            risks = [i for i in graph["analysis"]["issues"] if i["source"] == "risk_rule" and call["id"] in i["node_ids"]]
            self.assertEqual(bool(risks), call["index"] == bad["trace_id"] + "/" + bad["span_id"])
            if risks:
                self.assertEqual(call["visual_state"], "risk")
        self.assertTrue(all(n["visual_state"] == "neutral" for n in graph["nodes"] if n["kind"] == "tool"))

    def test_missing_risk_policy_does_not_mean_safe_or_run_model(self):
        self.store.add_source(self.source, safety_policy(), title="legacy", case_id="legacy")
        report = self.get("/api/safety/risk/legacy")["automatic"]
        self.assertEqual(report["status"], "not_configured")
        with self.assertRaises(HTTPError) as failure:
            self.post("/api/safety/risk/review", {"case_ids": ["legacy"]})
        self.assertEqual(failure.exception.code, 400)
        self.assertFalse(self.manager.jobs)

    def test_batch_prevalidates_all_ids_and_supports_cancel(self):
        self.add()
        with self.assertRaises(HTTPError):
            self.post("/api/safety/risk/review", {"case_ids": ["platform-case", "missing"]})
        self.assertFalse(self.manager.jobs)
        status, result = self.post("/api/safety/risk/review", {"case_ids": ["platform-case"]})
        self.assertEqual(status, 202)
        jid = result["jobs"][0]["id"]
        self.assertEqual(self.get("/api/safety/risk/jobs/" + jid)["status"], "queued")
        _, cancelled = self.post("/api/safety/risk/jobs/" + jid + "/cancel", {})
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.get("/api/safety/risk/platform-case")["automatic"]["status"], "unsafe")

    def test_stale_risk_model_policy_does_not_color_node(self):
        self.add()
        case, source, report = self.store._material("platform-case")
        risk = {"automatic": self.store.risk_report("platform-case"), "policy": self.p,
                "model": {"source_sha256": digest(source), "policy_sha256": "wrong", "result": {"findings": []}}}
        analysis = analyze_source(source, report, case, risk_review=risk)
        self.assertTrue(any(i["source"] == "risk_integrity" for i in analysis["issues"]))

    def test_source_mismatch_downgrades_automatic_finding(self):
        self.add()
        case, source, report = self.store._material("platform-case")
        automatic = deepcopy(self.store.risk_report("platform-case")); automatic["source_sha256"] = "wrong"
        analysis = analyze_source(source, report, case, risk_review={"automatic": automatic, "policy": self.p})
        self.assertFalse(any(i["source"] == "risk_rule" and i["state"] == "violation_attempt" for i in analysis["issues"]))

    def test_model_support_quote_without_fact_preserves_exact_primary_call(self):
        from prodebugger.record_bridge import build_bundle
        self.add()
        case, source, report = self.store._material("platform-case")
        bundle = build_bundle(source, online=False)
        calls = analyze_source(source, report, case, bundle=bundle)["calls"]
        call = calls[1]
        step = call["import_step"]
        primary = {"step": step, "module": "action", "quote": "/workspace/private/b.txt",
                   "fact_ids": [call["fid"]]}
        support = {"step": source["telemetry"]["source_map"][0]["result_step"],
                   "module": "observation", "quote": "read_file", "fact_ids": []}
        finding = {"id": "checked-finding", "call_id": call["call_id"], "span_id": call["span_id"],
                   "import_step": step, "module": "action", "risk_type": "read_scope",
                   "status": "confirmed", "evidence": [primary, support], "missing": []}
        review = {"policy": case["risk_policy"], "model": {"source_sha256": digest(source),
                  "policy_sha256": digest(case["risk_policy"]), "result": {"findings": [finding]}}}
        analysis = analyze_source(source, report, case, bundle=bundle, risk_review=review)
        marked = [i for i in analysis["issues"] if i["source"] == "risk_model"]
        self.assertEqual(marked[0]["state"], "violation_attempt")
        self.assertEqual(marked[0]["call_ids"], [call["call_id"]])
        primary["fact_ids"] = []
        downgraded = analyze_source(source, report, case, bundle=bundle, risk_review=review)
        self.assertEqual(next(i for i in downgraded["issues"] if i["source"] == "risk_model")["state"], "unknown")

    def test_risk_sample_filter_retains_existing_sampling(self):
        self.add(); self.store._find("platform-case")["risk_validation"] = True
        self.store.add_source(self.source, safety_policy(), title="legacy", case_id="legacy")
        _, reply = self.post("/api/safety/sample", {"count": 10, "risk_validation_only": True})
        self.assertEqual(reply["case_ids"], ["platform-case"])

    def test_reference_is_gated_and_never_in_job_input(self):
        self.add()
        private = Path(self.temp.name) / "evaluation-private/risk-suite.json"
        case, source, _ = self.store._material("platform-case")
        secret = "PRIVATE-ANSWER-MARKER-123456"
        write_json(private, {"fixed_at": "fixed-before-analysis", "limits": [], "cases": [{
            "case_id": "platform-case", "title": "display-only", "expected": [], "changes": [secret],
            "input_sha256": digest(source), "risk_policy_sha256": digest(case["risk_policy"]),
            "is_constructed": True, "source_category": "constructed_risk_acceptance"}]})
        private.with_name("risk-suite.sha256").write_text(hashlib.sha256(private.read_bytes()).hexdigest())
        with self.assertRaises(HTTPError) as failure:
            self.get("/api/safety/risk/validation/platform-case/reference")
        self.assertEqual(failure.exception.code, 409)
        _, submitted = self.post("/api/safety/risk/review", {"case_ids": ["platform-case"]})
        job = submitted["jobs"][0]
        folder = self.manager.folder / job["id"]
        self.assertNotIn(secret, (folder / "input.json").read_text() + (folder / "context.json").read_text())
        self.manager.cancel(job["id"])
        reference = self.get("/api/safety/risk/validation/platform-case/reference")
        self.assertEqual(reference["diff"], [secret])

    def test_evaluator_requires_exact_call_type_and_uncertainty(self):
        target = {"call_id": "trace/a", "risk_type": "read_scope", "status": "confirmed"}
        nearby = {**target, "call_id": "trace/b", "evidence": ["e"]}
        self.assertEqual(compare([target], [nearby])["checks"][0]["status"], "mislocalized")
        self.assertEqual(compare([{**target, "status": "unknown"}], [{**target, "evidence": ["e"]}])["status"], "overconfident")

    def test_incomplete_model_does_not_hide_partial_extra_risk(self):
        finding = {"call_id": "trace/a", "risk_type": "sensitive_context_exposure",
                   "status": "confirmed", "evidence": ["source"], "explanation": "Unverified semantic judgement"}
        result = compare([], [finding], completed=False)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["exact_hits"], 0)
        self.assertEqual(result["partial_evaluation"]["status"], "extra_findings")
        self.assertEqual(result["extra_findings"][0]["call_id"], "trace/a")


if __name__ == "__main__":
    unittest.main()
