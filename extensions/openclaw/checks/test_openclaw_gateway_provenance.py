"""No-model regression checks for trace identity and original OTLP grouping."""
import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from checks.test_openclaw_collect_gateway import save_capture
from checks.test_otel_import import TRACE, OTHER_TRACE, normalized, span, tool
from prodebugger import openclaw_collect_gateway as gateway
from prodebugger.otel_import import load_export, spans_from_export


def resource_group(spans, *, service="native-openclaw", scope="diagnostics-otel", key="scopeSpans"):
    return {"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}],
                         "droppedAttributesCount": 0},
            "schemaUrl": "https://example.test/resource-schema",
            key: [{"scope": {"name": scope, "version": "2026.10.5", "attributes": [
                {"key": "capture.origin", "value": {"stringValue": "native"}}]},
                "schemaUrl": "https://example.test/scope-schema", "spans": spans}]}


class GatewayProvenanceChecks(unittest.TestCase):
    def cases(self, root, count=1):
        return gateway.prepare_cases(root, gateway.scenarios()[:count])

    def run_span(self, root, case, **kwargs):
        prompt = (root / "cases" / case["id"] / "prompt.txt").read_text(encoding="utf-8")
        return span(attributes={"input.value": prompt}, **kwargs)

    def test_native_resources_scopes_and_metadata_survive_trace_filter_across_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = self.cases(root)
            native_run = self.run_span(root, cases[0])
            native_tool = tool()
            # Identical timing supplies no identity link to this other task.
            other = tool(number=3, trace=OTHER_TRACE)
            group = resource_group([native_run, other])
            group["scopeSpans"].append({"scope": {"name": "native-tool-recorder", "version": "v2"},
                                        "schemaUrl": "tool-schema", "spans": [native_tool]})
            group["scopeSpans"].append({"scope": {"name": "unrelated"}, "spans": [span(trace=OTHER_TRACE)]})
            first = {"resourceSpans": [group, resource_group([other], service="unrelated-service")],
                     "captureMetadata": {"batch": 1}}
            # A native child outside the client's wall times still belongs to
            # the same trace. Filtering must not silently trim its boundary.
            later = span(sid="0000000000000004", parent=native_run["spanId"], start=5000, end=6000,
                         name="openclaw.model.call", attributes={"native": "preserved"})
            second = {"resourceSpans": [resource_group([later], service="native-second-resource", scope="native-model-recorder")],
                      "captureMetadata": {"batch": 2}}
            cases[0].update(started_at="2099-01-01T00:00:00Z", finished_at="2099-01-01T00:00:01Z")
            documents = [first, second]
            original = copy.deepcopy(documents)
            save_capture(root, documents)
            raw = (root / "capture/traces.jsonl").read_bytes()
            gateway.assign_traces(root, cases, normalized(documents))
            self.assertEqual(documents, original)
            self.assertEqual((root / "capture/traces.jsonl").read_bytes(), raw)
            case = cases[0]
            self.assertEqual(case["trace_id"], TRACE)
            derived = load_export(root / case["export"])
            self.assertEqual(len(derived), 2)
            self.assertEqual(derived[0]["captureMetadata"], first["captureMetadata"])
            self.assertEqual(derived[1], second)
            selected_group = derived[0]["resourceSpans"][0]
            self.assertEqual(len(derived[0]["resourceSpans"]), 1)
            self.assertEqual(selected_group["resource"], group["resource"])
            self.assertEqual(selected_group["schemaUrl"], group["schemaUrl"])
            self.assertEqual(len(selected_group["scopeSpans"]), 2)
            self.assertEqual(selected_group["scopeSpans"][0], {**group["scopeSpans"][0], "spans": [native_run]})
            self.assertEqual(selected_group["scopeSpans"][1], group["scopeSpans"][1])
            selected = spans_from_export(derived)
            self.assertEqual({item["span_id"] for item in selected}, {native_run["spanId"], native_tool["spanId"], later["spanId"]})
            self.assertTrue(all(item["trace_id"] == TRACE for item in selected))
            self.assertEqual(selected[0]["resource"]["service.name"], "native-openclaw")
            provenance = load_export(root / case["trace_binding"]["provenance_file"])
            self.assertEqual(provenance["source_capture"]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(provenance["source_capture"]["bytes"], len(raw))
            self.assertEqual(provenance["selected_unique_span_count"], 3)
            self.assertEqual(provenance["export"]["sha256"], hashlib.sha256((root / case["export"]).read_bytes()).hexdigest())
            self.assertIn("no time slicing", provenance["boundary"])
            self.assertIsNone(case["expected_status"])

    def test_snake_case_and_legacy_scope_containers_keep_their_original_keys(self):
        for variant in ("snake", "legacy"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cases = self.cases(root)
                rows = [self.run_span(root, cases[0]), tool()]
                if variant == "snake":
                    for row in rows:
                        for original, replacement in (("traceId", "trace_id"), ("spanId", "span_id"),
                                ("parentSpanId", "parent_span_id"), ("startTimeUnixNano", "start_time_unix_nano"),
                                ("endTimeUnixNano", "end_time_unix_nano")):
                            row[replacement] = row.pop(original)
                    document = {"resource_spans": [resource_group(rows, key="scope_spans")]}
                else:
                    document = {"resourceSpans": [resource_group(rows, key="instrumentationLibrarySpans")]}
                save_capture(root, document)
                gateway.assign_traces(root, cases, normalized(document))
                self.assertEqual(load_export(root / cases[0]["export"]), [document])

    def test_duplicate_delivery_keeps_raw_batches_and_binds_whole_capture_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = self.cases(root)
            document = {"resourceSpans": [resource_group([self.run_span(root, cases[0]), tool()])]}
            documents = [document, copy.deepcopy(document)]
            save_capture(root, documents)
            gateway.assign_traces(root, cases, normalized(documents))
            case = cases[0]
            self.assertEqual(load_export(root / case["export"]), documents)
            provenance = load_export(root / case["trace_binding"]["provenance_file"])
            self.assertEqual(provenance["selected_unique_span_count"], 2)
            self.assertEqual(provenance["source_capture"]["sha256"], hashlib.sha256((root / "capture/traces.jsonl").read_bytes()).hexdigest())

    def test_shared_trace_keeps_both_cases_unknown_and_creates_no_isolated_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = self.cases(root, 2)
            rows = [self.run_span(root, case, sid=f"{number:016x}") for number, case in enumerate(cases, 1)]
            document = {"resourceSpans": [resource_group(rows)]}
            save_capture(root, document)
            gateway.assign_traces(root, cases, normalized(document))
            for case in cases:
                self.assertIsNone(case["trace_id"])
                self.assertEqual(case["export"], "capture/traces.jsonl")
                self.assertEqual(case["trace_binding"]["status"], "shared_trace_requires_task_boundary_review")
                self.assertFalse((root / "cases" / case["id"] / "trace.json").exists())
                self.assertIsNone(case["expected_status"])

    def test_missing_or_inconsistent_original_capture_cannot_create_reconstructed_export(self):
        for kind in ("missing", "different"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cases = self.cases(root)
                raw = {"spans": [self.run_span(root, cases[0]), tool()]}
                if kind == "different":
                    changed = copy.deepcopy(raw)
                    changed["spans"][1]["attributes"]["gen_ai.tool.call.arguments"] = '{"path":"different.txt"}'
                    save_capture(root, changed)
                with self.assertRaises(FileNotFoundError if kind == "missing" else ValueError):
                    gateway.assign_traces(root, cases, normalized(raw))
                self.assertFalse((root / "cases" / cases[0]["id"] / "trace.json").exists())
                self.assertIsNone(cases[0]["trace_id"])

    def test_nonzero_exit_and_wall_timeout_keep_cases_unlabeled_without_task_contracts(self):
        for mode in ("nonzero", "timeout"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory, \
                 patch.dict(os.environ, {"GATEWAY_NO_MODEL_KEY": "offline-only-placeholder"}):
                root = Path(directory)
                node, cli, plugin = root / "node", root / "cli.mjs", root / "plugin"
                node.touch()
                cli.touch()
                (root / "package.json").write_text(json.dumps({"name": "openclaw", "version": "2026.10.5"}), encoding="utf-8")
                plugin.mkdir()
                with gateway.socket.socket() as port_probe:
                    port_probe.bind(("127.0.0.1", 0))
                    port = port_probe.getsockname()[1]
                gateway_process = Mock()
                first = Mock(returncode=-1 if mode == "timeout" else 9)
                first.wait.side_effect = subprocess.TimeoutExpired("offline-client", 240) if mode == "timeout" else None
                first.wait.return_value = 9
                second = Mock(returncode=0)
                second.wait.return_value = 0
                receiver = Mock(server_address=("127.0.0.1", 4318))
                with patch.object(gateway, "make_server", return_value=receiver), \
                     patch.object(gateway, "install_official_exporter"), \
                     patch.object(gateway.subprocess, "Popen", side_effect=[gateway_process, first, second]) as launch, \
                     patch.object(gateway, "wait_ready"), \
                     patch.object(gateway, "wait_for_native_capture", return_value=True), \
                     patch.object(gateway, "shutdown_gateway", return_value="graceful_signal"), \
                     patch.object(gateway, "stop_process"), redirect_stdout(io.StringIO()):
                    status = gateway.main(["--node", str(node), "--cli", str(cli), "--plugin", str(plugin),
                        "--output-dir", str(root / "run"), "--api-key-env", "GATEWAY_NO_MODEL_KEY",
                        "--port", str(port),
                        "--cases", "case-01", "case-02"])
                self.assertEqual(status, 1)
                manifest = load_export(root / "run/manifest.json")
                self.assertEqual(manifest["status"], "completed_with_errors")
                self.assertEqual(manifest["cases"][0]["execution_status"], "wall_timeout" if mode == "timeout" else "exited")
                self.assertEqual(manifest["cases"][1]["execution_status"], "pending" if mode == "timeout" else "exited")
                self.assertEqual(launch.call_count, 2 if mode == "timeout" else 3)
                for case in manifest["cases"]:
                    self.assertIsNone(case["expected_status"])
                    self.assertEqual(case["expected_root_steps"], [])
                    self.assertIsNone(case["expected_module"])
                    task = load_export(root / "run" / case["task_file"])
                    self.assertNotIn("contract", task)
                    self.assertFalse(task["capture"]["complete"])


if __name__ == "__main__":
    unittest.main()
