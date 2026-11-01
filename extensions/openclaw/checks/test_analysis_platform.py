"""Local integration acceptance with synthetic unit-test data; never calls models.

Private answers used by these tests are created in TemporaryDirectory. No live
validation ground truth, capture, provider or existing user data is read here.
"""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from checks.test_openclaw_contract import source
from checks.test_safety_audit import policy
from checks.test_parsed_view import file_hashes
from prodebugger.diagnosis_jobs import DiagnosisJobs
from prodebugger.record_bridge import digest, read_json, write_json
from prodebugger.safety_workbench import Handler, SafetyHTTPServer, SafetyStore


class AnalysisPlatformChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = SafetyStore(self.folder / "data")
        config = {"model": "unit-test-only-no-provider"}
        public_config = {"configured": False, "credential_available": False, "model": config["model"]}
        with patch("prodebugger.diagnosis_jobs.model_configuration", return_value=(config, public_config)):
            self.manager = DiagnosisJobs(self.store.folder, start_worker=False)
        self.store._diagnoses = self.manager
        self.addCleanup(self.manager.close)
        self.server = SafetyHTTPServer(("127.0.0.1", 0), Handler)
        self.server.store = self.store
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, path):
        with urlopen(self.base + path, timeout=30) as response:
            return response.status, json.load(response)

    def post(self, path, body):
        request = Request(self.base + path, data=json.dumps(body).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Origin": self.base})
        with urlopen(request, timeout=30) as response:
            return response.status, json.load(response)

    def add(self, ident="unit-case-a", data=None):
        value = copy.deepcopy(data or source())
        value["trajectory_id"] = ident
        return self.store.add_source(value, policy(), title="Synthetic unit test", case_id=ident)

    def test_import_automatically_persists_rules_safety_and_original_compilation(self):
        data = source([{"action": {"arguments": {"path": "other.txt"}}}])
        status, result = self.post("/api/safety/import", {"source": data, "policy": policy(), "title": "Synthetic unit test"})
        self.assertEqual(status, 201)
        case = self.store._find(result["case"]["id"])
        self.assertEqual(result["report"]["status"], "safe")
        self.assertTrue(any(issue["rule_id"] == "OC-TASK-001" for issue in result["analysis"]["issues"]))
        folder = self.store._resolve(case["source_path"]).parent
        self.assertTrue((folder / "analysis.json").is_file())
        self.assertTrue((folder / "compiled/canonical_record.json").is_file())
        self.assertTrue((folder / "compiled/evaluations.json").is_file())
        self.assertEqual(read_json(folder / "input.json"), data)
        self.assertEqual(read_json(folder / "compiled/evaluations.json"), result["analysis"]["rule_evaluations"])

    def test_batch_validates_all_ids_before_queueing_any_case(self):
        case = self.add()
        before = file_hashes(self.store.folder)
        with patch.object(self.manager, "enqueue", wraps=self.manager.enqueue) as enqueue:
            for invalid in ([case["id"], "does-not-exist"], [case["id"], case["id"]], [], [case["id"], 3]):
                with self.subTest(invalid=invalid), self.assertRaises(HTTPError) as error:
                    self.post("/api/safety/diagnosis", {"case_ids": invalid})
                self.assertEqual(error.exception.code, 400)
            enqueue.assert_not_called()
        self.assertFalse(self.manager.jobs)
        self.assertEqual(file_hashes(self.store.folder), before)

    def test_queue_capacity_rejection_is_atomic(self):
        first, second = self.add(), self.add("unit-case-b")
        self.manager.jobs = {f"unit-pending-{i}": {"id": f"unit-pending-{i}", "case_id": f"other-{i}",
                                  "status": "queued"} for i in range(19)}
        try:
            with patch.object(self.manager, "enqueue", wraps=self.manager.enqueue) as enqueue, self.assertRaises(HTTPError) as error:
                self.post("/api/safety/diagnosis", {"case_ids": [first["id"], second["id"]]})
            self.assertEqual(error.exception.code, 400)
            enqueue.assert_not_called()
            self.assertEqual(len(self.manager.jobs), 19)
        finally:
            self.manager.jobs.clear()

    def test_batch_queue_job_details_and_cancel_keep_other_jobs_and_old_reports(self):
        first, second = self.add(), self.add("unit-case-b")
        preserved = {str(self.store._resolve(self.store._find(case["id"])[key])):
                     self.store._resolve(self.store._find(case["id"])[key]).read_bytes()
                     for case in (first, second) for key in ("source_path", "report_path")}
        with patch("prodebugger.diagnosis_jobs.subprocess.Popen") as popen:
            status, response = self.post("/api/safety/diagnosis", {"case_ids": [first["id"], second["id"]]})
            self.assertEqual(status, 202)
            ids = [job["id"] for job in response["jobs"]]
            self.assertEqual([job["queue_position"] for job in response["jobs"]], [1, 2])
            self.assertEqual(len(self.get("/api/safety/diagnosis/jobs")[1]["jobs"]), 2)
            self.assertEqual(self.get("/api/safety/diagnosis/jobs/" + ids[0])[1]["status"], "queued")
            self.assertEqual(self.post("/api/safety/diagnosis/jobs/" + ids[0] + "/cancel", {})[1]["status"], "cancelled")
            self.assertEqual(self.manager.get(ids[1])["status"], "queued")
            popen.assert_not_called()
        for filename, content in preserved.items():
            self.assertEqual(Path(filename).read_bytes(), content)

    def test_missing_configuration_fails_honestly_without_losing_automatic_analysis(self):
        case = self.add(data=source([{"action": {"arguments": {"path": "other.txt"}}}]))
        before = self.store.analysis(case["id"], include_diagnosis=False)
        _, submitted = self.post("/api/safety/diagnosis", {"case_ids": [case["id"]]})
        job_id = submitted["jobs"][0]["id"]
        with patch("prodebugger.diagnosis_jobs.subprocess.Popen") as popen:
            self.manager._run(job_id)
            popen.assert_not_called()
        _, after = self.get("/api/safety/analysis/" + case["id"])
        self.assertEqual(after["diagnosis"]["status"], "failed")
        self.assertFalse(after["diagnosis"]["full_flow_completed"])
        self.assertEqual(before["rule_evaluations"], after["rule_evaluations"])
        self.assertEqual([row for row in before["issues"] if row["category"] == "rule"],
                         [row for row in after["issues"] if row["category"] == "rule"])
        self.assertTrue(any(row["category"] == "uncertainty" for row in after["issues"]))
        self.assertEqual(self.get("/api/safety/reports/" + case["id"])[1]["status"], "safe")

    def test_all_analysis_gets_leave_existing_files_and_source_unchanged(self):
        case = self.add()
        before = file_hashes(self.store.folder)
        with patch.object(self.store, "start_demo", side_effect=AssertionError("read must not capture")), \
             patch.object(self.manager, "enqueue", side_effect=AssertionError("read must not queue models")):
            for path in ("state", "health", "analysis/" + case["id"], "graph/" + case["id"],
                         "parsed/" + case["id"], "diagnosis/jobs", "reports/" + case["id"]):
                with self.subTest(path=path):
                    self.assertEqual(self.get("/api/safety/" + path)[0], 200)
        self.assertEqual(file_hashes(self.store.folder), before)
        self.assertFalse(self.manager.jobs)

    def test_http_issue_and_graph_link_one_call_when_the_same_tool_is_used_twice(self):
        case = self.add(data=source([{}, {"action": {"arguments": {"path": "/workspace/.env"}}}]))
        analysis = self.get("/api/safety/analysis/" + case["id"])[1]
        graph = self.get("/api/safety/graph/" + case["id"])[1]
        issue = next(row for row in analysis["issues"] if row["category"] == "safety")
        self.assertEqual(issue["span_id"], "span-2")
        self.assertEqual(issue["import_steps"], [2])
        self.assertEqual(issue["node_ids"], [case["id"] + "::call:trace-1/span-2"])
        affected = [node for node in graph["nodes"] if node["visual_state"] == "risk"]
        self.assertEqual([node["id"] for node in affected], issue["node_ids"])
        self.assertEqual(next(node for node in graph["nodes"] if node["kind"] == "tool")["visual_state"], "neutral")

    def test_reaudit_cannot_reuse_a_model_error_from_an_old_policy(self):
        from checks.test_trace_analysis import TraceAnalysisChecks
        case = self.add()
        stored, data, _ = self.store._material(case["id"])
        # Explicitly synthetic model data used only to test the stale-result gate.
        simulated = TraceAnalysisChecks().model(data)
        simulated["policy_sha256"] = digest(stored["policy"])
        with patch.object(self.store, "latest_diagnosis", return_value=simulated):
            initial = self.get("/api/safety/analysis/" + case["id"])[1]
            self.assertTrue(any(row["category"] == "model" and row["state"] == "confirmed" for row in initial["issues"]))
            configured = copy.deepcopy(stored["policy"])
            configured["forbidden_paths"].append("/workspace/notes.txt")
            self.store.reaudit(case["id"], configured)
            changed = self.get("/api/safety/analysis/" + case["id"])[1]
            self.assertFalse(any(row["category"] == "model" for row in changed["issues"]))
            self.assertTrue(any("matching_diagnosis_policy" in row["missing"] for row in changed["issues"]))
            self.assertTrue(any(row["category"] == "safety" for row in changed["issues"]))

    def synthetic_suite(self):
        """Build unit-test-only private material, never read the real experiment."""
        from prodebugger.safety_audit import validate_policy
        from prodebugger.validation_evaluation import file_hash, register_suite
        folder = self.folder / "synthetic-unit-suite"
        fixed = "2000-01-01T00:00:00+00:00"
        private_marker = "UNIT_TEST_PRIVATE_ANSWER_DO_NOT_SEND_TO_MODEL"
        public_cases, answers = [], []
        for ident, constructed in (("unit-trial-a", False), ("unit-trial-b", True)):
            data = source([{"action": {"arguments": {"path": "other.txt"}}}]) if constructed else source()
            data["trajectory_id"] = ident
            case_folder = folder / "cases" / ident
            write_json(case_folder / "input.json", data)
            write_json(case_folder / "policy.json", validate_policy(policy()))
            case_id = "validation-" + ident
            public_cases.append({"id": ident, "case_id": case_id, "label": ident,
                "source_category": "synthetic_unit_test_only", "is_constructed": constructed,
                "source_path": f"cases/{ident}/input.json", "policy_path": f"cases/{ident}/policy.json",
                "source_sha256": file_hash(case_folder / "input.json")})
            answers.append({"id": ident, "case_id": case_id, "source_category": "synthetic_unit_test_only",
                "constructed": constructed, "fault_type": "target_substitution" if constructed else None,
                "injected_location": {"call_id": "trace-1/span-1", "trace_id": "trace-1", "span_id": "span-1",
                                      "import_action_step": 1, "import_result_step": 2},
                "expected_safety_rules": [], "original_task_requirement": "notes.txt",
                "semantic_changes": [{"field": "arguments.path", "before": "notes.txt", "after": "other.txt"}] if constructed else [],
                "test_only_private_marker": private_marker})
        write_json(folder / "private/ground-truth.json", {"schema": "prodebugger-fixed-injection-ground-truth/v1",
            "suite_id": "unit-suite", "fixed_at": fixed, "cases": answers})
        write_json(folder / "private/diffs.json", {"synthetic_unit_test_only": True, "private_marker": private_marker})
        manifest = {"schema": "prodebugger-localization-suite/v1", "id": "unit-suite", "title": "Synthetic unit test suite",
            "description": "This fixture is synthetic and never represents a real OpenClaw run.", "created_at": fixed,
            "source": {"kind": "synthetic_unit_test_only"}, "cases": public_cases,
            "private_answers": "private/ground-truth.json", "private_diffs": "private/diffs.json",
            "ground_truth_sha256": file_hash(folder / "private/ground-truth.json"),
            "diffs_sha256": file_hash(folder / "private/diffs.json"), "limitations": ["Synthetic unit tests only."]}
        write_json(folder / "suite.json", manifest)
        register_suite(self.store, folder / "suite.json")
        return folder, manifest, private_marker

    def terminal_suite_jobs(self, manifest):
        result = self.store.start_diagnosis({"case_ids": [row["case_id"] for row in manifest["cases"]]})
        for job in result["jobs"]:
            self.manager._update(job["id"], status="failed", error="Synthetic unit test: model intentionally unavailable.")
        return [self.manager.get(job["id"]) for job in result["jobs"]]

    def test_public_validation_never_exposes_private_truth_before_analysis_and_worker_input_is_clean(self):
        folder, manifest, marker = self.synthetic_suite()
        before = file_hashes(self.store.folder)
        status, public = self.get("/api/safety/validation")
        self.assertEqual(status, 200)
        encoded = json.dumps(public)
        for private in (marker, "semantic_changes", "private_answers", "injected_location"):
            self.assertNotIn(private, encoded)
        self.assertFalse(public["suites"][0]["reference_available"])
        with self.assertRaises(HTTPError) as error:
            self.get("/api/safety/validation/unit-suite/reference")
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(file_hashes(self.store.folder), before)
        result = self.store.start_diagnosis({"case_ids": [row["case_id"] for row in manifest["cases"]]})
        for job in result["jobs"]:
            run = Path(job["artifact_directory"])
            prompt_material = (run / "input.json").read_text() + (run / "context.json").read_text()
            for forbidden in (marker, "ground_truth", "semantic_changes", "injected_location", "private/diffs"):
                self.assertNotIn(forbidden, prompt_material)

    def test_failed_complete_batch_can_reveal_answers_but_never_count_as_model_hit(self):
        _, manifest, marker = self.synthetic_suite()
        self.terminal_suite_jobs(manifest)
        public = self.get("/api/safety/validation")[1]["suites"][0]
        self.assertTrue(public["reference_available"])
        self.assertNotIn(marker, json.dumps(public))
        reference = self.get("/api/safety/validation/unit-suite/reference")[1]
        self.assertIn(marker, json.dumps(reference["ground_truth"]))
        self.assertEqual(reference["comparison"]["summary"]["precise_hits"], 0)
        variant = next(row for row in reference["comparison"]["cases"] if row["constructed"])
        self.assertEqual(variant["outcome"], "不确定")
        self.assertFalse(variant["full_flow_completed"])
        self.assertFalse(variant["exact_call_match"])
        self.assertTrue(variant["rules"]["exact_call_match"])

    def test_reference_gate_requires_all_current_runs_same_config_and_fixed_time(self):
        _, manifest, _ = self.synthetic_suite()
        jobs = self.terminal_suite_jobs(manifest)
        first = jobs[0]
        mutations = [
            ("unfinished", {"status": "queued"}),
            ("stale_source", {"source_sha256": "stale"}),
            ("before_truth", {"created_at": "1999-01-01T00:00:00+00:00"}),
        ]
        for name, changes in mutations:
            original = copy.deepcopy(self.manager.jobs[first["id"]])
            try:
                self.manager.jobs[first["id"]].update(changes)
                with self.subTest(name=name), self.assertRaises(HTTPError) as error:
                    self.get("/api/safety/validation/unit-suite/reference")
                self.assertEqual(error.exception.code, 409)
            finally:
                self.manager.jobs[first["id"]] = original
        artifact = Path(first["artifact_directory"])
        for filename, altered in (("config.json", {"model": "unit-test-only-no-provider", "timeout": 1}),
                                  ("context.json", {"policy": {"id": "different_policy"}})):
            path = artifact / filename
            saved = path.read_bytes()
            try:
                write_json(path, altered)
                with self.subTest(filename=filename), self.assertRaises(HTTPError) as error:
                    self.get("/api/safety/validation/unit-suite/reference")
                self.assertEqual(error.exception.code, 409)
            finally:
                path.write_bytes(saved)

    def test_reference_rejects_changed_private_answer_instead_of_rewriting_expected_result(self):
        folder, manifest, _ = self.synthetic_suite()
        self.terminal_suite_jobs(manifest)
        truth_path = folder / manifest["private_answers"]
        truth_path.write_bytes(truth_path.read_bytes() + b"\n")
        with self.assertRaises(HTTPError) as error:
            self.get("/api/safety/validation/unit-suite/reference")
        self.assertEqual(error.exception.code, 409)

    def test_fixed_inputs_and_policies_cannot_be_replaced_even_by_a_matching_new_job(self):
        folder, manifest, _ = self.synthetic_suite()
        jobs = self.terminal_suite_jobs(manifest)
        item = manifest["cases"][0]
        stored = self.store._find(item["case_id"])
        paths = [folder / item["source_path"], folder / item["policy_path"],
                 self.store._resolve(stored["source_path"])]
        for path in paths:
            saved = path.read_bytes()
            old_job = copy.deepcopy(self.manager.jobs[jobs[0]["id"]])
            try:
                value = read_json(path)
                if "task" in value:
                    value["task"] += " Synthetic unit test tampering."
                    # Even a hypothetical new diagnosis matching that changed
                    # current input must not be compared to the original answer.
                    self.manager.jobs[jobs[0]["id"]]["source_sha256"] = digest(value)
                else:
                    value["version"] = "unit-test-tampered"
                write_json(path, value)
                with self.subTest(path=str(path)), self.assertRaises(HTTPError) as error:
                    self.get("/api/safety/validation/unit-suite/reference")
                self.assertEqual(error.exception.code, 409)
            finally:
                path.write_bytes(saved)
                self.manager.jobs[jobs[0]["id"]] = old_job

    def test_local_suite_registration_is_idempotent_for_valid_unnormalized_policy(self):
        from prodebugger.validation_evaluation import register_suite
        from prodebugger.safety_audit import validate_policy
        folder, manifest, _ = self.synthetic_suite()
        # Use a distinct fresh suite to exercise first and repeated registration
        # with equivalent policy lists that normalization reorders.
        for item in manifest["cases"]:
            item["case_id"] += "-unnormalized"
            path = folder / item["policy_path"]
            configured = read_json(path)
            configured["allowed_network_hosts"] = list(reversed(configured["allowed_network_hosts"]))
            write_json(path, configured)
        manifest["id"] = "unit-suite-unnormalized"
        write_json(folder / "unnormalized-suite.json", manifest)
        first = register_suite(self.store, folder / "unnormalized-suite.json")
        before = file_hashes(self.store.folder)
        second = register_suite(self.store, folder / "unnormalized-suite.json")
        self.assertEqual(first, second)
        self.assertEqual(file_hashes(self.store.folder), before)
        for item in manifest["cases"]:
            self.assertEqual(self.store._find(item["case_id"])["policy"],
                             validate_policy(read_json(folder / item["policy_path"])))


if __name__ == "__main__":
    unittest.main()
