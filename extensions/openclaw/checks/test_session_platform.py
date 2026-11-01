"""Session import, source validation, state-table API and diagnosis input tests.

All data and HTTP listeners are temporary; no provider or model is contacted.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from checks.test_session_import import fixture
from prodebugger.diagnosis_jobs import DiagnosisJobs
from prodebugger.record_bridge import read_json
from prodebugger.safety_workbench import Handler, SafetyHTTPServer, SafetyStore, default_policy
from prodebugger.session_import import convert_session, validate_session_source


def session_fixture(answer="Revenue: 42"):
    base, native = fixture()
    result = json.loads(base["steps"][2]["env_text"])
    result["result"] = {"content": [{"type": "text", "text": "Revenue: 42"}],
                        "details": {"text": "Revenue: 42"}}
    base["steps"][2]["env_text"] = json.dumps(result)
    native["events"][6]["event"]["message"].update(deepcopy(result["result"]))
    native["events"][-1]["event"]["message"]["content"][0]["text"] = answer
    return base, native


class SessionPlatformChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SafetyStore(Path(self.temp.name) / "data")
        self.policy = default_policy("/workspace")
        with patch("prodebugger.diagnosis_jobs.model_configuration", return_value=(
                {"model": "unit-test-no-provider"},
                {"configured": False, "credential_available": False, "model": "unit-test-no-provider"})):
            self.manager = DiagnosisJobs(self.store.folder, start_worker=False)
        self.store._diagnoses = self.manager
        self.addCleanup(self.manager.close)
        self.server = SafetyHTTPServer(("127.0.0.1", 0), Handler)
        self.server.store = self.store
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, path):
        with urlopen(self.url + path, timeout=20) as response:
            return json.load(response)

    def post(self, path, data):
        request = Request(self.url + path, data=json.dumps(data).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Origin": self.url})
        with urlopen(request, timeout=20) as response:
            return response.status, json.load(response)

    def add(self, answer="Revenue: 42"):
        source, _ = convert_session(*session_fixture(answer))
        return self.store.add_source(source, self.policy, title="Synthetic session acceptance", case_id="session-test")

    def test_native_message_import_api_persists_all_sources_and_public_session_marker(self):
        base, native = session_fixture()
        status, result = self.post("/api/safety/import", {"source": base, "native_transcript": native,
                                                        "policy": self.policy, "title": "Synthetic test"})
        self.assertEqual(status, 201)
        cid = result["case"]["id"]
        public = next(case for case in self.get("/api/safety/state")["cases"] if case["id"] == cid)
        self.assertTrue(public["session_available"])
        self.assertEqual(public["source_category"], "openclaw_session_import")
        self.assertNotIn("source_path", public)
        stored = read_json(self.store._resolve(self.store._find(cid)["source_path"]))
        self.assertEqual(stored["session"]["native_transcript"], native)
        self.assertEqual(stored["session"]["base_source"], base)
        self.assertEqual(len(stored["steps"]), 8)
        validate_session_source(stored)

    def test_graph_exposes_state_versions_typed_pointers_and_recorded_source_locations(self):
        case = self.add()
        graph = self.get("/api/safety/graph/" + case["id"])
        artifact = graph["session_artifact"]
        self.assertTrue(artifact["available"])
        self.assertEqual(len(artifact["events"]), 8)
        self.assertEqual(artifact["coverage"]["source_coverage"]["native_messages"], 6)
        self.assertTrue(artifact["rows"])
        self.assertTrue(all(row["versions"] for row in artifact["rows"]))
        self.assertTrue({"source", "basis", "correspondence", "check"} <= {p["type"] for p in artifact["pointers"]})
        self.assertEqual(artifact["events"][-1]["source"]["json_pointer"], "/events/7/event/message")
        self.assertFalse(artifact["coverage"]["source_coverage"]["semantic_parse_complete"])
        self.assertEqual({m["module"] for m in artifact["missing_modules"]}, {"memory", "reflection", "internal_plan"})

    def test_http_rejects_derived_tampering_and_file_paths_without_creating_a_case(self):
        source, _ = convert_session(*session_fixture())
        changed = deepcopy(source)
        changed["session"]["events"][-1]["text"] = "spoofed answer"
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/safety/import", {"source": changed, "policy": self.policy})
        self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/safety/import", {"source": fixture()[0], "native_transcript": "C:/arbitrary.json", "policy": self.policy})
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.store.state()["summary"]["total"], 0)

    def test_diagnosis_queue_retains_session_evidence_and_has_no_model_execution(self):
        case = self.add()
        source = read_json(self.store._resolve(self.store._find(case["id"])["source_path"]))
        with patch("prodebugger.diagnosis_jobs.subprocess.Popen") as process:
            status, queued = self.post("/api/safety/diagnosis", {"case_ids": [case["id"]]})
            process.assert_not_called()
        self.assertEqual(status, 202)
        job = queued["jobs"][0]
        snapshot = read_json(self.manager.folder / job["id"] / "input.json")
        self.assertEqual(snapshot["session"], source["session"])
        self.assertEqual(snapshot["steps"], source["steps"])
        validate_session_source(snapshot)
        self.assertFalse({"ground_truth", "diff", "is_constructed", "title"} & set(snapshot))

    def test_final_answer_error_marks_message_not_correct_read_call(self):
        case = self.add("Revenue: 99")
        graph = self.get("/api/safety/graph/" + case["id"])
        issue = next(issue for issue in graph["analysis"]["issues"] if issue["rule_id"] == "OC-SESSION-ANSWER-001")
        self.assertEqual(issue["state"], "confirmed")
        self.assertEqual(issue["call_ids"], [])
        self.assertEqual(issue["affected_event_ids"], ["message5"])
        self.assertEqual(issue["node_ids"], [case["id"] + "::message:trace1/message5"])
        self.assertTrue(all(node["visual_state"] != "error" for node in graph["nodes"] if node["kind"] == "call"))
        self.assertTrue(any(pointer["type"] == "correspondence" for pointer in graph["session_artifact"]["pointers"]))

    def test_unsupported_final_answer_is_unknown_and_never_total_success(self):
        case = self.add("Done; everything is perfect.")
        graph = self.get("/api/safety/graph/" + case["id"])
        check = next(check for check in graph["session_artifact"]["checks"] if check["rule_id"] == "OC-SESSION-ANSWER-001")
        self.assertEqual(check["state"], "unknown")
        self.assertFalse(check["applied"])
        self.assertTrue(check["missing"])
        issue = next(issue for issue in graph["analysis"]["issues"] if issue["rule_id"] == "OC-SESSION-ANSWER-001")
        self.assertEqual(issue["category"], "uncertainty")
        node = next(node for node in graph["nodes"] if node["id"].endswith("::message:trace1/message5"))
        self.assertNotEqual(node["visual_state"], "passed")
        self.assertNotEqual(node["visual_state"], "error")

    def test_converter_derives_trace_id_when_original_telemetry_does_not_declare_it(self):
        base, native = session_fixture()
        del base["telemetry"]["trace_id"]
        source, _ = convert_session(base, native)
        self.assertEqual(source["telemetry"]["trace_id"], "trace1")
        validate_session_source(source)
        self.store.add_source(source, self.policy, title="No redundant trace header", case_id="trace-derived")


if __name__ == "__main__":
    unittest.main()
