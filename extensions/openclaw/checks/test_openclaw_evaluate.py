"""Synthetic offline tests of the independent captured-trace evaluator."""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from checks.test_otel_import import OTHER_TRACE, TRACE, exported, span, task_spec, tool
from prodebugger.openclaw_evaluate import SCHEMA, load_manifest, metrics, parser, run
from prodebugger.record_bridge import read_json, write_json


class OpenClawEvaluationChecks(unittest.TestCase):
    def fixture(self, folder, cases=None, origin="controlled", data=None, spec=None):
        write_json(folder / "capture.json", data if data is not None else exported())
        write_json(folder / "task.json", spec if spec is not None else task_spec())
        default = {"id": "independent-case", "export": "capture.json", "task_file": "task.json",
                   "expected_status": None, "expected_root_steps": [], "expected_module": None}
        manifest = {"schema": SCHEMA, "data_origin": origin, "cases": cases or [default],
                    "metadata": {"runtime": "test-only synthetic data"}}
        path = folder / "cases.json"
        write_json(path, manifest)
        return path, default

    def execute(self, folder, manifest, *extra):
        args = parser().parse_args(["--manifest", str(manifest), "--output-dir", str(folder / "runs"),
                                    "--run-id", "check", *extra])
        with redirect_stdout(io.StringIO()):
            status = run(args)
        result = folder / "runs" / "check"
        return status, read_json(result / "summary.json"), result

    def test_offline_generic_tool_has_no_model_calls_replay_or_task_path_assumptions(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            raw = exported()
            raw["spans"][1]["attributes"]["gen_ai.tool.name"] = "list_directory"
            spec = task_spec()
            spec["contract"]["checks"][0]["tool"] = "list_directory"
            path, _ = self.fixture(folder, data=raw, spec=spec)
            with patch("prodebugger.openclaw_evaluate.LLMClient", side_effect=AssertionError("offline model")), \
                 patch("prodebugger.openclaw_evaluate.DebuggerAgent", side_effect=AssertionError("offline diagnosis")), \
                 patch("prodebugger.openclaw_evaluate.baseline", side_effect=AssertionError("offline baseline")), \
                 patch("prodebugger.openclaw_demo.ReplayClient", side_effect=AssertionError("scripted replay")):
                status, result, output = self.execute(folder, path)
            self.assertEqual(status, 0)
            self.assertEqual(result["data_origin"], "controlled")
            row = result["cases"][0]
            self.assertEqual(row["offline_status"], "imported")
            self.assertEqual(row["task_outcome"]["status"], "success")
            self.assertTrue(row["probe"]["passed"])
            self.assertNotIn("structured", row)
            self.assertNotIn("baseline", row)
            self.assertEqual(result["metrics"]["methods"], {})
            self.assertEqual(result["metrics"]["unlabeled_cases"], 1)
            self.assertFalse((output / "replay").exists())
            self.assertFalse((output / "online").exists())
            self.assertEqual((output / row["inputs"]["export"]["stored_path"]).read_bytes(), (folder / "capture.json").read_bytes())
            self.assertEqual(len(row["inputs"]["export"]["sha256"]), 64)
            imported = read_json(output / "imported" / row["trajectory_id"] / "input.json")
            self.assertEqual(imported["telemetry"]["trace_id"], TRACE)
            self.assertFalse(any(key.startswith("expected_") for key in imported))
            report = (output / "report.md").read_text(encoding="utf-8")
            self.assertIn("unlabeled", report)
            self.assertIn("controlled", report)

    def test_mock_online_labels_never_enter_source_unlabeled_is_excluded_and_env_is_not_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path, case = self.fixture(folder, origin="real")
            labeled = {**case, "id": "LABEL_ONLY_TARGET_MISTAKE", "expected_status": "no_failure"}
            write_json(path, {"schema": SCHEMA, "data_origin": "real", "cases": [labeled, {**case, "id": "unreviewed"}]})
            env_file = folder / ".credentials"
            env_file.write_text("TEST_SECRET=do-not-copy-this-value", encoding="utf-8")
            fake_client = Mock()
            fake_client.snapshot.return_value = {"model_calls": 1, "input_tokens": 21, "output_tokens": 5}
            structured = {"status": "no_failure", "root": None, "usage": fake_client.snapshot()}
            fake_agent = Mock()
            fake_agent.run.side_effect = [copy.deepcopy(structured), copy.deepcopy(structured)]
            with patch("prodebugger.openclaw_evaluate.LLMClient", return_value=fake_client) as clients, \
                 patch("prodebugger.openclaw_evaluate.DebuggerAgent", return_value=fake_agent) as agents, \
                 patch("prodebugger.openclaw_evaluate.baseline", side_effect=[copy.deepcopy(structured), copy.deepcopy(structured)]) as baselines:
                status, result, output = self.execute(folder, path, "--online", "--model", "offline-mock",
                    "--env-file", str(env_file), "--api-key-env", "TEST_SECRET")
            self.assertEqual(status, 0)
            self.assertEqual(result["data_origin"], "real")
            for method in ("structured", "baseline"):
                self.assertTrue(result["cases"][0][method]["matches_expected"])
                self.assertIsNone(result["cases"][1][method]["matches_expected"])
                score = result["metrics"]["methods"][method]
                self.assertEqual(score["labeled_cases"], 1)
                self.assertEqual(score["matches"], 1)
                self.assertEqual(score["accuracy"], 1)
                self.assertEqual(score["usage"]["model_calls"], 2)
            for invocation in agents.call_args_list:
                source = invocation.args[0].source
                text = json.dumps(source)
                self.assertNotIn("LABEL_ONLY_TARGET_MISTAKE", text)
                self.assertNotIn("expected_status", text)
                self.assertNotIn("expected_root_steps", text)
            for invocation in baselines.call_args_list:
                self.assertNotIn("expected_status", json.dumps(invocation.args[0]))
            config = clients.call_args_list[0].args[0]
            self.assertEqual(config["env_file"], str(env_file.resolve()))
            self.assertEqual(config["max_turns"], 16)
            self.assertEqual(config["max_tool_calls"], 35)
            self.assertEqual(config["max_input_tokens"], 400000)
            self.assertEqual(config["max_output_tokens"], 24000)
            self.assertEqual(config["retries"], 0)
            self.assertNotIn("do-not-copy-this-value", (output / "summary.json").read_text(encoding="utf-8"))

    def test_missing_file_and_metadata_only_capture_are_recorded_without_stopping_later_case(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path, case = self.fixture(folder)
            write_json(folder / "metadata.json", {"spans": [span()]})
            cases = [{**case, "id": "missing", "export": "does-not-exist.json"},
                {**case, "id": "metadata-only", "export": "metadata.json", "expected_status": "not_importable"},
                {**case, "id": "later-case"}]
            write_json(path, {"schema": SCHEMA, "data_origin": "controlled", "cases": cases})
            status, result, output = self.execute(folder, path)
            self.assertEqual(status, 1)
            first, second, third = result["cases"]
            self.assertEqual(first["offline_status"], "error")
            self.assertEqual(first["failure"]["stage"], "export")
            self.assertEqual(first["failure"]["type"], "FileNotFoundError")
            self.assertEqual(second["offline_status"], "not_importable")
            self.assertTrue(second["import_matches_expected"])
            self.assertIn("No recognized OpenClaw tool", " ".join(second["failure"]["reasons"]))
            self.assertEqual(third["offline_status"], "imported")
            self.assertTrue((output / "raw" / "metadata-only" / "import_audit.json").is_file())

    def test_trace_selection_is_forwarded_and_multiple_unselected_traces_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            data = {"spans": [*exported()["spans"], *exported(trace=OTHER_TRACE)["spans"]]}
            path, case = self.fixture(folder, data=data)
            write_json(path, {"schema": SCHEMA, "data_origin": "controlled", "cases": [
                {**case, "id": "unselected", "expected_status": "not_importable"},
                {**case, "id": "selected", "trace_id": OTHER_TRACE}]})
            status, result, output = self.execute(folder, path)
            self.assertEqual(status, 0)
            self.assertEqual(result["cases"][0]["offline_status"], "not_importable")
            selected = result["cases"][1]
            self.assertEqual(selected["telemetry"]["trace_id"], OTHER_TRACE)
            raw_spans = read_json(output / "imported" / selected["trajectory_id"] / "original_spans.json")
            self.assertTrue(all(item["traceId"] == OTHER_TRACE for item in raw_spans))
            original = read_json(output / selected["inputs"]["export"]["stored_path"])
            self.assertEqual(len(original["spans"]), 4)

    def test_importable_partial_capture_keeps_unknown_contract_and_explicit_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            raw = tool(status={"code": 2, "message": "failed without output"})
            del raw["attributes"]["gen_ai.tool.call.result"]
            path, _ = self.fixture(folder, data=exported(raw))
            status, result, _ = self.execute(folder, path)
            self.assertEqual(status, 0)
            row = result["cases"][0]
            self.assertTrue(row["import_ready"])
            self.assertEqual(row["offline_status"], "imported")
            self.assertTrue(row["probe"]["passed"])
            self.assertFalse(row["telemetry"]["coverage"]["content_complete"])
            self.assertEqual(row["task_outcome"]["status"], "unknown")

    def test_malformed_export_failure_is_recorded_in_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path, _ = self.fixture(folder)
            (folder / "capture.json").write_text('{"spans":[],"spans":[]}', encoding="utf-8")
            status, result, _ = self.execute(folder, path)
            self.assertEqual(status, 1)
            row = result["cases"][0]
            self.assertEqual(row["failure"]["stage"], "decode_export")
            self.assertIn("duplicate JSON key", row["failure"]["reasons"][0])

    def test_malformed_span_does_not_interrupt_following_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path, case = self.fixture(folder)
            write_json(folder / "malformed.json", {"spans": [None]})
            write_json(path, {"schema": SCHEMA, "data_origin": "controlled", "cases": [
                {**case, "id": "malformed-span", "export": "malformed.json"},
                {**case, "id": "later-case"}]})
            status, result, _ = self.execute(folder, path)
            self.assertEqual(status, 1)
            broken, later = result["cases"]
            self.assertEqual(broken["offline_status"], "error")
            self.assertEqual(broken["failure"]["stage"], "decode_export")
            self.assertEqual(later["offline_status"], "imported")

    def test_manifest_results_cannot_be_reused_as_this_runs_diagnoses_or_usage(self):
        for online in (False, True):
            with self.subTest(online=online), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                path, case = self.fixture(folder)
                stale = {"status": "no_failure", "root": None,
                         "usage": {"model_calls": 99, "input_tokens": 12345}}
                poisoned = {**case, "export": "missing.json", "expected_status": "no_failure",
                            "structured": stale, "baseline": stale,
                            "task_outcome": {"status": "success"}, "telemetry": {"fabricated": True},
                            "probe": {"passed": True}, "offline_status": "imported"}
                write_json(path, {"schema": SCHEMA, "data_origin": "controlled", "cases": [poisoned]})
                with patch("prodebugger.openclaw_evaluate.LLMClient", side_effect=AssertionError("model")), \
                     patch("prodebugger.openclaw_evaluate.baseline", side_effect=AssertionError("model")):
                    status, result, output = self.execute(folder, path, *(["--online", "--model", "unused"] if online else []))
                self.assertEqual(status, 1)
                row = result["cases"][0]
                self.assertEqual(row["offline_status"], "error")
                for field in ("structured", "baseline", "task_outcome", "telemetry", "probe"):
                    self.assertNotIn(field, row)
                self.assertIn("未运行", (output / "report.md").read_text(encoding="utf-8"))
                self.assertEqual(result["manifest"]["cases"][0]["structured"], stale)
                if online:
                    for score in result["metrics"]["methods"].values():
                        self.assertEqual(score["accuracy"], 0)
                        self.assertEqual(score["completed_cases"], 0)
                        self.assertEqual(score["usage"]["model_calls"], 0)
                        self.assertEqual(score["usage"]["input_tokens"], 0)

    def test_otlp_jsonl_and_signoz_exports_preserve_raw_metadata(self):
        for kind in ("jsonl", "signoz"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                path, case = self.fixture(folder)
                raw = exported()
                if kind == "jsonl":
                    export_path = folder / "capture.jsonl"
                    export_path.write_text("\n".join(json.dumps(item) for item in raw["spans"]) + "\n", encoding="utf-8")
                else:
                    rows = []
                    for item in raw["spans"]:
                        rows.append({"traceID": item["traceId"], "spanID": item["spanId"], "parentSpanID": item["parentSpanId"],
                            "operationName": item["name"], "startTime": item["startTimeUnixNano"],
                            "durationNano": int(item["endTimeUnixNano"]) - int(item["startTimeUnixNano"]),
                            "serviceName": "captured-service", "tags": item["attributes"]})
                    export_path = folder / "signoz.json"
                    write_json(export_path, {"data": {"result": {"spans": rows}}})
                write_json(path, {"schema": SCHEMA, "data_origin": "controlled",
                    "cases": [{**case, "export": export_path.name}]})
                status, result, output = self.execute(folder, path)
                self.assertEqual(status, 0)
                row = result["cases"][0]
                self.assertEqual(row["task_outcome"]["status"], "success")
                self.assertEqual((output / row["inputs"]["export"]["stored_path"]).read_bytes(), export_path.read_bytes())
                spans = read_json(output / "imported" / row["trajectory_id"] / "original_spans.json")
                self.assertEqual(spans, rows if kind == "signoz" else raw["spans"])

    def test_unexpectedly_importable_label_is_a_failed_import_expectation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path, case = self.fixture(folder)
            write_json(path, {"schema": SCHEMA, "data_origin": "controlled",
                "cases": [{**case, "expected_status": "not_importable"}]})
            status, result, _ = self.execute(folder, path)
            self.assertEqual(status, 1)
            self.assertFalse(result["cases"][0]["import_matches_expected"])

    def test_online_method_setup_errors_are_recorded_and_following_cases_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path, _ = self.fixture(folder)
            with patch("prodebugger.openclaw_evaluate.LLMClient", side_effect=ValueError("credential environment variable TEST is not set")), \
                 patch("prodebugger.openclaw_evaluate.baseline", side_effect=ValueError("baseline setup failed")):
                status, result, output = self.execute(folder, path, "--online", "--model", "mock")
            self.assertEqual(status, 2)
            for method in ("structured", "baseline"):
                row = result["cases"][0][method]
                self.assertEqual(row["status"], "error")
                self.assertIsNone(row["matches_expected"])
                self.assertIsNone(result["metrics"]["methods"][method]["accuracy"])
            self.assertTrue((output / "online" / "openclaw_eval_001" / "result.json").is_file())
            self.assertTrue((output / "baseline" / "openclaw_eval_001" / "result.json").is_file())

    def test_metrics_keep_labeled_import_failures_in_denominator_and_exclude_unlabeled(self):
        rows = [
            {"expected_status": "diagnosed", "expected_root_steps": [2, 3], "expected_module": "action", "offline_status": "imported",
             "structured": {"status": "diagnosed", "root": {"step": 3, "module": "action"}, "usage": {"model_calls": 4}}},
            {"expected_status": "diagnosed", "expected_root_steps": [1], "expected_module": "action", "offline_status": "error"},
            {"expected_status": None, "offline_status": "imported", "structured": {"status": "uncertain", "root": None}},
            {"expected_status": "not_importable", "offline_status": "not_importable"},
        ]
        result = metrics(rows, True)
        self.assertEqual(result["labeled_diagnostic_cases"], 2)
        self.assertEqual(result["methods"]["structured"]["matches"], 1)
        self.assertEqual(result["methods"]["structured"]["accuracy"], 0.5)
        self.assertEqual(result["methods"]["baseline"]["accuracy"], 0)
        self.assertIsNone(rows[2]["structured"]["matches_expected"])

    def test_manifest_rejects_unsafe_ids_duplicate_ids_and_invalid_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path, case = self.fixture(folder)
            changes = [
                {"cases": [{**case, "id": "../outside"}]},
                {"cases": [case, case]},
                {"cases": [{**case, "expected_status": "diagnosed"}]},
                {"cases": [{**case, "expected_root_steps": [True]}]},
                {"cases": [{**case, "expected_status": "success"}]},
                {"cases": [{**case, "expected_module": "plan"}]},
                {"cases": [{**case, "trace_id": "invalid"}]},
                {"data_origin": "invented"}, {"schema": "openclaw-demo/v1"}, {"cases": []},
            ]
            for change in changes:
                with self.subTest(change=change):
                    write_json(path, {"schema": SCHEMA, "data_origin": "controlled", "cases": [case], **change})
                    with self.assertRaises(ValueError):
                        load_manifest(path)


if __name__ == "__main__":
    unittest.main()
