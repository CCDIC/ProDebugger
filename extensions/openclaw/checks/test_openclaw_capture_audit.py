"""Synthetic offline adversarial checks; no model calls or runtime processes."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prodebugger import openclaw_capture_audit as audit
from prodebugger import openclaw_collect_gateway as gateway
from prodebugger.otel_import import convert, load_export, spans_from_export
from prodebugger.openclaw_contract import evaluate
from checks.test_otel_import import span, tool, TRACE, OTHER_TRACE


PROMPT = "Read C:/fixtures/report.txt using the read tool."
CALL = "call_real_native_id"
ARGS = {"path": "C:/fixtures/report.txt"}
RESULT = {"content": [{"type": "text", "text": "Revenue: 42\n"}],
          "details": {"kind": "text", "content": "Revenue: 42\n"}}


def native_record(case, manifest_hash="abc", prompt=PROMPT):
    messages = [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": [{"type": "toolCall", "id": CALL, "name": "read", "arguments": ARGS}],
         "stopReason": "toolUse"},
        {"role": "toolResult", "toolCallId": CALL, "toolName": "read", **RESULT, "isError": False},
        {"role": "assistant", "content": [{"type": "text", "text": "Done."}], "stopReason": "stop"},
    ]
    events = []
    for index, message in enumerate(messages):
        events.append({"seq": 10 * (index + 1), "active_position": index,
                       "event": {"id": "event-" + str(index),
                                 "parentId": None if index == 0 else "event-" + str(index - 1),
                                 "type": "message", "message": copy.deepcopy(message)}})
    record = {"schema": "openclaw-native-transcript/v1", "otlp": False, "case_id": case["id"],
              "session_id": case["session_id"], "source": {"manifest_sha256": manifest_hash}, "events": events,
              "active_events": [{"active_position": i, "event_seq": row["seq"], "message_position": i,
                                 "context_eligible": 1} for i, row in enumerate(events)],
              "active_index": {"indexed_seq": 40, "leaf_event_id": "event-3", "needs_rebuild": 0,
                               "active_event_count": 4, "active_message_count": 4}}
    return record


def fresh_record(case, manifest_hash):
    record = native_record(case, manifest_hash)
    for index, row in enumerate(record["events"]):
        row["seq"] = index + 1
        record["active_events"][index]["event_seq"] = index + 1
    record["events"].insert(0, {"seq": 0, "active_position": None,
                                 "event": {"id": case["session_id"], "type": "session"}})
    record["active_index"]["indexed_seq"] = 4
    return record


def raw_events(record):
    lines = []
    for row in record["events"]:
        raw = json.dumps(row["event"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        row.update(event_sha256=hashlib.sha256(raw).hexdigest(), event_utf8_bytes=len(raw))
        lines.append(raw)
    return b"\n".join(lines) + b"\n"


def native_session(record=None):
    case = {"id": "case-01", "session_id": "session-native-1"}
    record = record or native_record(case)
    return audit.native_session(record, raw_events(record), case, PROMPT, "abc")


def raw_capture(*tools):
    default = tool(arguments=ARGS, result=RESULT, **{"gen_ai.tool.name": "read", "gen_ai.tool.call.id": CALL})
    return {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "native"}}]},
                              "schemaUrl": "resource-schema", "unknownResourceField": {"keep": True},
                              "scopeSpans": [{"scope": {"name": "official-exporter", "version": "2026.10.5"},
                                              "schemaUrl": "scope-schema", "spans": [span(), *(tools or [default])]}]}]}


def write_run(root, *, scenario="read", shutdown="graceful_signal", gateway_exit_code=0,
              receipt=True, stdout="", stderr="", receipt_mutator=None):
    case = {"id": "case-01", "session_id": "session-native-1", "execution_status": "exited", "exit_code": 0}
    manifest = {"schema": "openclaw-evaluation/v1", "data_origin": "controlled", "cases": [case],
                "capture": "capture/traces.jsonl", "gateway_shutdown": shutdown}
    (root / "gateway.stdout.txt").write_text(stdout, encoding="utf-8")
    (root / "gateway.stderr.txt").write_text(stderr, encoding="utf-8")
    if gateway_exit_code is not None:
        manifest["gateway_exit_code"] = gateway_exit_code
    if receipt:
        shutdown_audit = gateway.audit_gateway_shutdown(root, shutdown, gateway_exit_code)
        if receipt_mutator is not None:
            receipt_mutator(shutdown_audit)
        manifest["gateway_shutdown_audit"] = shutdown_audit
    audit.write_json(root / "manifest.json", manifest)
    capture = root / "capture/traces.jsonl"
    capture.parent.mkdir()
    capture.write_text(json.dumps(raw_capture()) + "\n", encoding="utf-8")
    folder = root / "cases/case-01"
    folder.mkdir(parents=True)
    (folder / "prompt.txt").write_text(PROMPT, encoding="utf-8")
    audit.write_json(folder / "acceptance.json", {"expected_tool": "read", "expected_path": ARGS["path"],
                    "expected_text": "Revenue: 42\n", "scenario": scenario})
    record = fresh_record(case, audit.evidence(root / "manifest.json")["sha256"])
    native_folder = root / "native-transcripts/case-01"
    native_folder.mkdir(parents=True)
    (native_folder / "events.jsonl").write_bytes(raw_events(record))
    audit.write_json(native_folder / "transcript.json", record)
    return manifest


class NativeCaptureAuditChecks(unittest.TestCase):
    def assert_shutdown_incomplete(self, root, *reasons):
        before = (root / "manifest.json").read_bytes()
        result = audit.audit_capture(root / "manifest.json")
        self.assertEqual(result["audit_summary"], {"bound": 1, "complete_tool_boundaries": 0, "rejected": 0})
        self.assertEqual((root / "manifest.json").read_bytes(), before)
        folder = root / "capture-audit/cases/case-01"
        self.assertTrue((folder / "trace.json").is_file())
        detail = load_export(folder / "audit.json")
        self.assertEqual(detail["status"], "bound")
        self.assertFalse(detail["gateway_shutdown_audit"]["clean"])
        for reason in reasons:
            self.assertIn(reason, detail["gateway_shutdown_audit"]["reasons"])
            self.assertIn(reason, detail["gaps"])
        task = load_export(folder / "task.json")
        self.assertFalse(task["capture"]["complete"])
        self.assertTrue(task["contract"]["complete"])
        source, _ = convert(spans_from_export(load_export(folder / "trace.json")), task, TRACE)
        self.assertEqual(evaluate(source)["status"], "unknown")
        self.assertIsNone(result["cases"][0]["expected_status"])
        return detail

    def test_actual_session_indices_and_ids_bind_without_run_prompt(self):
        session = native_session()
        self.assertEqual(session["prompt_seq"], 10)
        bound = audit.bind_session(session, spans_from_export(raw_capture()))
        self.assertEqual(bound["trace_id"], TRACE)
        self.assertEqual(bound["comparisons"][0]["result_seq"], 30)
        self.assertEqual(bound["errors"], [])
        self.assertEqual(bound["gaps"], ["native_event_sequence_incomplete", "native_fresh_session_header_missing"])

    def test_inactive_events_are_never_used_as_task_or_tool_evidence(self):
        record = native_record({"id": "case-01", "session_id": "session-native-1"})
        record["events"].append({"seq": 50, "active_position": None,
                                 "event": {"id": "inactive", "message": {"role": "user", "content": "Different task"}}})
        record["active_index"]["indexed_seq"] = 50
        self.assertEqual(len(native_session(record)["calls"]), 1)
        record["events"][0]["event"]["message"]["content"] = "Different task"
        record["events"][-1]["event"]["message"]["content"] = PROMPT
        with self.assertRaisesRegex(ValueError, "exact_active_user"):
            native_session(record)

    def test_raw_transcript_integrity_and_session_identity_are_required(self):
        case = {"id": "case-01", "session_id": "session-native-1"}
        for mutate, message in [
            (lambda r: r.update(session_id="other"), "identity"),
            (lambda r: r["source"].update(manifest_sha256="other"), "manifest_hash"),
            (lambda r: r["active_index"].update(needs_rebuild=1), "stale"),
            (lambda r: r["active_events"][1].update(event_seq=999), "event_mismatch"),
        ]:
            with self.subTest(message=message):
                record = native_record(case)
                raw = raw_events(record)
                mutate(record)
                with self.assertRaisesRegex(ValueError, message):
                    audit.native_session(record, raw, case, PROMPT, "abc")
        record = native_record(case)
        raw = raw_events(record)
        record["events"][1]["event"]["message"]["content"][0]["arguments"]["path"] = "tampered"
        with self.assertRaisesRegex(ValueError, "integrity"):
            audit.native_session(record, raw, case, PROMPT, "abc")

    def test_duplicate_native_calls_and_missing_results_are_rejected(self):
        record = native_record({"id": "case-01", "session_id": "session-native-1"})
        record["events"][1]["event"]["message"]["content"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            native_session(record)

    def test_parent_chain_and_inactive_annotations_are_verified(self):
        for mutate, issue in [
            (lambda r: r["events"][2]["event"].update(parentId="unrelated"), "parent_chain"),
            (lambda r: r["events"][0]["event"].pop("parentId"), "parent_chain"),
            (lambda r: r["events"][1]["event"].update(id="event-0"), "event_id_duplicate"),
            (lambda r: r["active_events"][1].update(message_position=3), "message_positions"),
        ]:
            with self.subTest(issue=issue):
                record = native_record({"id": "case-01", "session_id": "session-native-1"})
                mutate(record)
                with self.assertRaisesRegex(ValueError, issue):
                    native_session(record)
        record = native_record({"id": "case-01", "session_id": "session-native-1"})
        record["events"].append({"seq": 50, "active_position": 9,
                                 "event": {"id": "inactive", "type": "custom"}})
        record["active_index"]["indexed_seq"] = 50
        with self.assertRaisesRegex(ValueError, "inactive_event_annotation"):
            native_session(record)
        record = native_record({"id": "case-01", "session_id": "session-native-1"})
        record["events"][2]["event"]["message"]["role"] = "assistant"
        with self.assertRaisesRegex(ValueError, "missing_call_or_result"):
            native_session(record)

    def test_ambiguous_extra_missing_and_cross_trace_spans_are_rejected(self):
        good = tool(arguments=ARGS, result=RESULT, **{"gen_ai.tool.name": "read", "gen_ai.tool.call.id": CALL})
        other = tool(number=3, arguments=ARGS, result=RESULT, **{"gen_ai.tool.name": "read", "gen_ai.tool.call.id": CALL})
        for rows, issue in [([good, other], "ambiguous_tool_call_span"),
                            ([other], None),
                            ([span()], "missing_tool_call_span")]:
            result = audit.bind_session(native_session(), spans_from_export(raw_capture(*rows)))
            if issue:
                self.assertIn(issue, result["errors"])
                self.assertIsNone(result["trace_id"])
        other["attributes"]["gen_ai.tool.call.id"] = "unexpected"
        result = audit.bind_session(native_session(), spans_from_export(raw_capture(good, other)))
        self.assertIn("trace_contains_unmatched_tool_calls", result["errors"])
        other["attributes"]["gen_ai.tool.call.id"] = CALL
        other["traceId"] = OTHER_TRACE
        result = audit.bind_session(native_session(), spans_from_export(raw_capture(good, other)))
        self.assertIn("ambiguous_tool_call_span", result["errors"])

    def test_matching_ids_cannot_hide_argument_result_name_or_error_mismatch(self):
        mutations = [
            ("gen_ai.tool.name", "write", "tool_name_mismatch"),
            ("gen_ai.tool.call.arguments", json.dumps({"path": "other"}), "tool_arguments_mismatch"),
            ("gen_ai.tool.call.result", json.dumps({**RESULT, "content": []}), "tool_result_content_mismatch"),
            ("openclaw.outcome", "error", "tool_error_outcome_mismatch"),
        ]
        for key, value, issue in mutations:
            raw = raw_capture()
            raw["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["attributes"][key] = value
            result = audit.bind_session(native_session(), spans_from_export(raw))
            with self.subTest(issue=issue):
                self.assertIn(issue, result["errors"])
                self.assertIsNone(result["trace_id"])

    def test_structured_error_without_execute_flag_matches_native_harness_flag(self):
        output = {"content": [{"type": "text", "text": "File unavailable."}],
                  "details": {"status": "error", "error": "ENOENT"}}
        record = native_record({"id": "case-01", "session_id": "session-native-1"})
        record["events"][2]["event"]["message"].update(output, isError=True)
        session = native_session(record)
        raw = raw_capture(tool(arguments=ARGS, result=output, status={"code": 2},
            **{"gen_ai.tool.name": "read", "gen_ai.tool.call.id": CALL, "openclaw.outcome": "error"}))
        before = copy.deepcopy(raw)
        result = audit.bind_session(session, spans_from_export(raw))
        self.assertEqual(result["trace_id"], TRACE)
        self.assertEqual(result["errors"], [])
        self.assertEqual(raw, before)
        self.assertNotIn("isError", json.loads(raw["resourceSpans"][0]["scopeSpans"][0]["spans"][1]
                                               ["attributes"]["gen_ai.tool.call.result"]))
        # Missing execute() metadata cannot excuse a contrary span outcome.
        row = raw["resourceSpans"][0]["scopeSpans"][0]["spans"][1]
        row["status"] = {"code": 0}
        row["attributes"]["openclaw.outcome"] = "success"
        result = audit.bind_session(session, spans_from_export(raw))
        self.assertIn("tool_error_outcome_mismatch", result["errors"])
        self.assertIsNone(result["trace_id"])

    def test_explicit_execute_error_flag_must_be_boolean_and_match_native(self):
        output = {"content": [{"type": "text", "text": "File unavailable."}],
                  "details": {"status": "error"}}
        record = native_record({"id": "case-01", "session_id": "session-native-1"})
        record["events"][2]["event"]["message"].update(output, isError=True)
        session = native_session(record)
        for flag in (True, False, None, "true", 1, {}):
            with self.subTest(flag=flag):
                raw = raw_capture(tool(arguments=ARGS, result={**output, "isError": flag}, status={"code": 2},
                    **{"gen_ai.tool.name": "read", "gen_ai.tool.call.id": CALL, "openclaw.outcome": "error"}))
                result = audit.bind_session(session, spans_from_export(raw))
                if flag is True:
                    self.assertEqual(result["trace_id"], TRACE)
                    self.assertEqual(result["errors"], [])
                else:
                    self.assertIn("tool_result_error_flag_mismatch", result["errors"])
                    self.assertIsNone(result["trace_id"])

    def test_distinct_calls_cannot_cross_traces_or_reverse_native_order(self):
        session = native_session()
        other_call = copy.deepcopy(session["calls"][0])
        other_call["id"] = "second-call"
        session["calls"].append(other_call)
        first = tool(number=2, arguments=ARGS, result=RESULT,
                     **{"gen_ai.tool.name": "read", "gen_ai.tool.call.id": CALL})
        second = tool(number=3, arguments=ARGS, result=RESULT,
                      **{"gen_ai.tool.name": "read", "gen_ai.tool.call.id": "second-call"})
        spans = spans_from_export(raw_capture(first, second))
        normal = audit.bind_session(session, spans)
        self.assertEqual(normal["trace_id"], TRACE)
        session["calls"].reverse()
        reversed_calls = audit.bind_session(session, spans)
        self.assertIn("native_tool_order_mismatch", reversed_calls["errors"])
        self.assertIsNone(reversed_calls["trace_id"])
        second["traceId"] = OTHER_TRACE
        cross = audit.bind_session(session, spans_from_export(raw_capture(first, second)))
        self.assertIn("tool_calls_cross_trace_or_unbound", cross["errors"])
        self.assertIsNone(cross["trace_id"])

    def test_missing_content_is_never_filled_from_transcript(self):
        raw = raw_capture()
        attrs = raw["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["attributes"]
        del attrs["gen_ai.tool.call.result"]
        before = copy.deepcopy(raw)
        result = audit.bind_session(native_session(), spans_from_export(raw))
        self.assertEqual(result["trace_id"], TRACE)
        self.assertIn("tool_result_not_captured", result["gaps"])
        self.assertEqual(raw, before)

    def test_complete_normal_read_produces_separate_contract_and_lossless_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root)
            before = (root / "manifest.json").read_bytes()
            result = audit.audit_capture(root / "manifest.json")
            self.assertEqual(result["audit_summary"], {"bound": 1, "complete_tool_boundaries": 1, "rejected": 0})
            self.assertEqual((root / "manifest.json").read_bytes(), before)
            selected = load_export(root / "capture-audit/cases/case-01/trace.json")
            self.assertEqual(selected, [raw_capture()])
            task = load_export(root / "capture-audit/cases/case-01/task.json")
            self.assertTrue(task["capture"]["complete"])
            self.assertTrue(task["contract"]["complete"])
            source, _ = convert(spans_from_export(selected), task, TRACE)
            self.assertEqual(evaluate(source)["status"], "success")
            self.assertIsNone(result["cases"][0]["expected_status"])

    def test_recovery_and_unavailable_contracts_stay_incomplete(self):
        for scenario in ("recovery", "unavailable"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_run(root, scenario=scenario)
                audit.audit_capture(root / "manifest.json")
                task = load_export(root / "capture-audit/cases/case-01/task.json")
                self.assertFalse(task["contract"]["complete"])

    def test_forced_shutdown_cannot_establish_complete_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, shutdown="forced_process_tree")
            result = audit.audit_capture(root / "manifest.json")
            self.assertEqual(result["audit_summary"]["bound"], 1)
            self.assertEqual(result["audit_summary"]["complete_tool_boundaries"], 0)
            task = load_export(root / "capture-audit/cases/case-01/task.json")
            self.assertFalse(task["capture"]["complete"])
            self.assertTrue(task["contract"]["complete"])
            source, _ = convert(spans_from_export(load_export(root / "capture-audit/cases/case-01/trace.json")), task, TRACE)
            self.assertEqual(evaluate(source)["status"], "unknown")

    def test_legacy_graceful_signal_without_gateway_exit_code_remains_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, receipt=False, gateway_exit_code=None)
            self.assert_shutdown_incomplete(root, "gateway_exit_code_unknown")

    def test_legacy_exporter_retirement_downgrades_capture_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, receipt=False, gateway_exit_code=None,
                      stderr=("[plugins] diagnostics-otel: forced retirement after 5000ms\n"
                              "[gateway] shutdown step failed: gateway close\n"))
            self.assert_shutdown_incomplete(root, "gateway_exit_code_unknown", "exporter_forced_retirement",
                                            "gateway_shutdown_step_failed")

    def test_gateway_nonzero_exit_cannot_be_hidden_by_successful_agent_rpc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_run(root, gateway_exit_code=7)
            self.assertEqual(manifest["cases"][0]["exit_code"], 0)
            self.assert_shutdown_incomplete(root, "gateway_exit_nonzero")

    def test_changed_shutdown_logs_invalidate_the_recorded_receipt(self):
        for name in ("gateway.stdout.txt", "gateway.stderr.txt"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_run(root)
                # No failure marker: the saved digest must detect this change.
                (root / name).write_text("Warning: optional UI unavailable; continuing.\n", encoding="utf-8")
                self.assert_shutdown_incomplete(root, "gateway_shutdown_receipt_mismatch")

    def test_shutdown_logs_changed_during_audit_invalidate_all_derived_evidence(self):
        mutations = [
            ("bind_session", "gateway.stderr.txt", "[plugins] diagnostics-otel: forced retirement after 5000ms\n"),
            ("inspect", "gateway.stdout.txt", "Warning: optional UI unavailable; continuing.\n"),
        ]
        for stage, name, text in mutations:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_run(root)
                original = getattr(audit, stage)

                def change_log(*args, **kwargs):
                    (root / name).write_text(text, encoding="utf-8")
                    return original(*args, **kwargs)

                with patch.object(audit, stage, side_effect=change_log):
                    detail = self.assert_shutdown_incomplete(root, "gateway_shutdown_logs_changed_during_audit")
                shutdown = detail["gateway_shutdown_audit"]
                self.assertEqual(shutdown["receipt_status"], "changed_during_audit")
                final = next(row for row in shutdown["final_logs"] if row["path"] == name)
                current = audit.evidence(root / name)
                self.assertEqual(final["sha256"], current["sha256"])
                self.assertEqual(final["bytes"], current["bytes"])
                provenance = load_export(root / "capture-audit/cases/case-01/trace.provenance.json")
                self.assertEqual(provenance["gateway_shutdown_audit"], shutdown)

    def test_missing_shutdown_logs_cannot_be_treated_as_clean(self):
        for name in ("gateway.stdout.txt", "gateway.stderr.txt"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_run(root)
                (root / name).unlink()
                self.assert_shutdown_incomplete(root, "gateway_shutdown_log_unavailable")

    def test_forged_clean_receipt_cannot_hide_exporter_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_run(root,
                stderr="[plugins] diagnostics-otel: forced retirement after 5000ms\n",
                receipt_mutator=lambda receipt: receipt.update(clean=True, reasons=[]))
            self.assertTrue(manifest["gateway_shutdown_audit"]["clean"])
            self.assert_shutdown_incomplete(root, "exporter_forced_retirement")

    def test_harmless_warnings_with_zero_exit_preserve_complete_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root, stdout="[gateway] ready\n",
                      stderr=("[plugins] diagnostics-otel: experimental API warning\n"
                              "[gateway] Warning: optional UI unavailable; continuing.\n"))
            result = audit.audit_capture(root / "manifest.json")
            self.assertEqual(result["audit_summary"], {"bound": 1, "complete_tool_boundaries": 1, "rejected": 0})
            folder = root / "capture-audit/cases/case-01"
            detail = load_export(folder / "audit.json")
            self.assertTrue(detail["gateway_shutdown_audit"]["clean"])
            self.assertEqual(detail["gateway_shutdown_audit"]["reasons"], [])
            task = load_export(folder / "task.json")
            self.assertTrue(task["capture"]["complete"])
            self.assertTrue(task["contract"]["complete"])

    def test_shared_native_call_and_trace_cannot_bind_two_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_run(root)
            second = {**manifest["cases"][0], "id": "case-02", "session_id": "session-native-2"}
            manifest["cases"].append(second)
            audit.write_json(root / "manifest.json", manifest)
            manifest_hash = audit.evidence(root / "manifest.json")["sha256"]
            for case in manifest["cases"]:
                folder = root / "cases" / case["id"]
                folder.mkdir(exist_ok=True)
                (folder / "prompt.txt").write_text(PROMPT, encoding="utf-8")
                audit.write_json(folder / "acceptance.json", {"expected_tool": "read", "expected_path": ARGS["path"],
                    "expected_text": "Revenue: 42\n", "scenario": "read"})
                native_folder = root / "native-transcripts" / case["id"]
                native_folder.mkdir(exist_ok=True)
                record = fresh_record(case, manifest_hash)
                (native_folder / "events.jsonl").write_bytes(raw_events(record))
                audit.write_json(native_folder / "transcript.json", record)
            result = audit.audit_capture(root / "manifest.json")
            self.assertEqual(result["audit_summary"]["rejected"], 2)
            for case in manifest["cases"]:
                folder = root / "capture-audit/cases" / case["id"]
                detail = load_export(folder / "audit.json")
                self.assertIn("tool_call_id_shared_between_native_sessions", detail["errors"])
                self.assertIn("trace_shared_between_cases", detail["errors"])
                self.assertFalse((folder / "trace.json").exists())

    def test_missing_native_history_cannot_assert_complete_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_run(root)
            record = native_record(manifest["cases"][0], audit.evidence(root / "manifest.json")["sha256"])
            folder = root / "native-transcripts/case-01"
            (folder / "events.jsonl").write_bytes(raw_events(record))
            audit.write_json(folder / "transcript.json", record)
            result = audit.audit_capture(root / "manifest.json")
            self.assertEqual(result["audit_summary"]["bound"], 1)
            self.assertEqual(result["audit_summary"]["complete_tool_boundaries"], 0)
            detail = load_export(root / "capture-audit/cases/case-01/audit.json")
            self.assertIn("native_event_sequence_incomplete", detail["gaps"])

    def test_missing_otlp_still_writes_audit_without_creating_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root)
            (root / "capture/traces.jsonl").unlink()
            result = audit.audit_capture(root / "manifest.json")
            self.assertEqual(result["audit_summary"]["rejected"], 1)
            self.assertFalse((root / "capture-audit/cases/case-01/trace.json").exists())
            self.assertTrue((root / "capture-audit/audit-manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
