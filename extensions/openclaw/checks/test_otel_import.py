"""Synthetic, offline checks for lossless OTLP import and coverage boundaries."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from prodebugger.openclaw_contract import SCHEMA as CONTRACT_SCHEMA, evaluate
from prodebugger.otel_import import (convert, inspect, load_export, load_task,
                                    main, save_import, spans_from_export)


TRACE = "1" * 32
OTHER_TRACE = "2" * 32
ROOT_SPAN = "0000000000000001"


def span(sid=ROOT_SPAN, *, trace=TRACE, parent="", start=1, end=1000,
         name="openclaw.run", attributes=None, status=None):
    return {"traceId": trace, "spanId": sid, "parentSpanId": parent, "name": name,
            "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
            "attributes": attributes or {}, "status": status or {"code": 0}}


def tool(number=2, *, trace=TRACE, start=100, end=200, legacy=False,
         arguments=None, result=None, content=True, status=None, **extra):
    keys = ("openclaw.toolName", "openclaw.content.tool_input", "openclaw.content.tool_output") if legacy else (
        "gen_ai.tool.name", "gen_ai.tool.call.arguments", "gen_ai.tool.call.result")
    attrs = {keys[0]: "read_file", "gen_ai.operation.name": "execute_tool",
             "gen_ai.tool.call.id": "model-call-" + str(number)}
    if content:
        attrs[keys[1]] = json.dumps(arguments if arguments is not None else {"path": "notes.txt"})
        attrs[keys[2]] = json.dumps(result if result is not None else {"text": "expected contents"})
    attrs.update(extra)
    return span(f"{number:016x}", trace=trace, parent=ROOT_SPAN, start=start, end=end,
                name="openclaw.tool.execution", attributes=attrs, status=status)


def exported(*tools, trace=TRACE):
    return {"spans": [span(trace=trace), *(tools or (tool(trace=trace),))]}


def contract():
    return {"schema": CONTRACT_SCHEMA, "complete": True, "checks": [{
        "id": "notes-read", "tool": "read_file", "argument_equals": {"/path": "notes.txt"},
        "result_equals": {"/text": "expected contents"}}]}


def task_spec(*, complete=True, with_contract=True):
    data = {"schema": "openclaw-task/v1", "task": "Obtain the expected contents of notes.txt.",
            "capture": {"complete": complete, "reason": "Synthetic fixture captures this entire tool task."}}
    if with_contract:
        data["contract"] = contract()
    return data


def normalized(data=None):
    return spans_from_export(data if data is not None else exported())


class OtelImportChecks(unittest.TestCase):
    def test_conflicting_standard_and_legacy_aliases_leave_outcome_unknown(self):
        cases = [
            {"openclaw.content.tool_output": json.dumps({"text": "wrong contents"})},
            {"openclaw.content.tool_input": json.dumps({"path": "other.txt"})},
            {"openclaw.toolName": "different_tool"},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                source, audit = convert(normalized(exported(tool(**extra))), task_spec())
                self.assertTrue(audit["ready"])
                self.assertFalse(audit["coverage"]["content_complete"])
                self.assertTrue(any("conflicting_tool_aliases" in gap["missing"]
                                    for gap in audit["coverage"]["gaps"]))
                self.assertEqual(json.loads(source["steps"][0]["parts"]["action"])["tool"], "read_file")
                self.assertEqual(evaluate(source)["status"], "unknown")
        identical = tool(**{"openclaw.content.tool_output": json.dumps({"text": "expected contents"}),
                            "openclaw.content.tool_input": json.dumps({"path": "notes.txt"}),
                            "openclaw.toolName": "read_file"})
        source, audit = convert(normalized(exported(identical)), task_spec())
        self.assertTrue(audit["coverage"]["content_complete"])
        self.assertEqual(evaluate(source)["status"], "success")

    def test_null_optional_status_is_unset_and_invalid_status_shapes_are_rejected(self):
        raw = tool()
        raw["status"] = None
        source, _ = convert(normalized(exported(raw)), task_spec())
        self.assertEqual(json.loads(source["steps"][1]["env_text"])["status"], "unset")
        self.assertEqual(evaluate(source)["status"], "success")
        for status in ([], "error", True, {"code": []}, {"code": True}, {"code": 99}):
            raw = tool()
            raw["status"] = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                normalized(exported(raw))

    def test_native_error_outcome_preserves_unset_span_but_blocks_success(self):
        raw = tool(**{"openclaw.outcome": "error"})
        source, _ = convert(normalized(exported(raw)), task_spec())
        response = json.loads(source["steps"][1]["env_text"])
        self.assertEqual(response["status"], "unset")
        self.assertIn("error", response)
        self.assertEqual(evaluate(source)["status"], "failure")

    def test_otlp_resource_scope_and_all_attribute_value_shapes_are_decoded(self):
        raw = tool()
        raw["attributes"] = [
            {"key": "gen_ai.tool.name", "value": {"stringValue": "read_file"}},
            {"key": "gen_ai.tool.call.arguments", "value": {"kvlistValue": {"values": [
                {"key": "path", "value": {"stringValue": "中文/notes.txt"}},
                {"key": "offset", "value": {"intValue": "0"}},
                {"key": "enabled", "value": {"boolValue": False}}]}}},
            {"key": "gen_ai.tool.call.result", "value": {"arrayValue": {"values": [
                {"stringValue": "line"}, {"intValue": "42"}, {"doubleValue": 1.25},
                {"boolValue": False}, {"bytesValue": "AAE="}]}}},
        ]
        data = {"resourceSpans": [{"resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "openclaw"}}]},
            "scopeSpans": [{"spans": [span(), raw]}]}]}
        spans = normalized(data)
        self.assertEqual(spans[1]["resource"], {"service.name": "openclaw"})
        source, audit = convert(spans, task_spec(with_contract=False))
        action = json.loads(source["steps"][0]["parts"]["action"])
        feedback = json.loads(source["steps"][1]["env_text"])
        self.assertEqual(action["arguments"], {"path": "中文/notes.txt", "offset": 0, "enabled": False})
        self.assertEqual(feedback["result"], ["line", 42, 1.25, False, "AAE="])
        self.assertTrue(audit["ready"])

    def test_snake_case_otlp_and_instrumentation_library_exports_are_supported(self):
        raw = tool()
        for camel, snake in (("traceId", "trace_id"), ("spanId", "span_id"),
                             ("parentSpanId", "parent_span_id"),
                             ("startTimeUnixNano", "start_time_unix_nano"),
                             ("endTimeUnixNano", "end_time_unix_nano")):
            raw[snake] = raw.pop(camel)
        raw["attributes"] = [
            {"key": "gen_ai.tool.name", "value": {"string_value": "read_file"}},
            {"key": "gen_ai.tool.call.arguments", "value": {"kvlist_value": {"values": [
                {"key": "path", "value": {"string_value": "notes.txt"}}]}}},
            {"key": "gen_ai.tool.call.result", "value": {"string_value": "plain text"}},
        ]
        data = {"resource_spans": [{"scope_spans": [{"spans": [span(), raw]}]}]}
        source, _ = convert(normalized(data), task_spec(with_contract=False))
        self.assertEqual(json.loads(source["steps"][-1]["env_text"])["result"], "plain text")
        older = {"resourceSpans": [{"instrumentationLibrarySpans": [{"spans": [span(), tool()]}]}]}
        self.assertEqual(len(normalized(older)), 2)

    def test_signoz_trace_api_rows_and_wrappers_map_tags_without_fabricating_content(self):
        # This is the shape returned by the SigNoz trace detail API, rather
        # than an OTLP resourceSpans export.  startTime is epoch nanoseconds;
        # durationNano is used only to derive the end timestamp.
        rows = [{
            "traceID": TRACE, "spanID": ROOT_SPAN, "parentSpanID": "",
            "operationName": "openclaw.run", "startTime": "1000000000",
            "durationNano": "1000", "serviceName": "openclaw",
            "tags": [{"key": "service.name", "value": {"string": "openclaw"}}],
        }, {
            "traceID": TRACE, "spanID": "0000000000000002", "parentSpanID": ROOT_SPAN,
            "operationName": "openclaw.tool.execution", "startTime": 1000000100,
            "durationNano": 100, "tags": [
                {"key": "gen_ai.operation.name", "value": {"string": "execute_tool"}},
                {"key": "gen_ai.tool.name", "value": {"string": "read_file"}},
                {"key": "gen_ai.tool.call.arguments", "value": {"string": '{"path":"notes.txt"}'}},
                {"key": "gen_ai.tool.call.result", "value": {"string": '{"text":"expected contents"}'}},
            ],
        }]
        spans = normalized({"data": {"result": {"spans": rows}}})
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[1]["start"], 1000000100)
        self.assertEqual(spans[1]["end"], 1000000200)
        self.assertEqual(spans[1]["attributes"]["gen_ai.tool.name"], "read_file")
        self.assertEqual(spans[1]["raw"]["operationName"], "openclaw.tool.execution")
        source, audit = convert(spans, task_spec())
        self.assertTrue(audit["ready"])
        self.assertEqual(evaluate(source)["status"], "success")

        # An API row containing only metadata remains unusable for semantic
        # reconstruction; the adapter must not invent arguments/results.
        metadata = rows[1].copy()
        metadata["tags"] = [{"key": "gen_ai.tool.name", "value": {"string": "read_file"}}]
        metadata_spans = normalized({"data": [rows[0], metadata]})
        audit = inspect(metadata_spans, task_spec())
        self.assertFalse(audit["ready"])
        self.assertTrue(any("tool_arguments" in gap["missing"] for gap in audit["coverage"]["gaps"]))

    def test_signoz_error_metadata_blocks_contract_without_creating_status(self):
        rows = [{
            "traceID": TRACE, "spanID": ROOT_SPAN, "parentSpanID": "",
            "operationName": "openclaw.run", "startTime": 1000000000, "durationNano": 1000,
        }, {
            "traceID": TRACE, "spanID": "0000000000000002", "parentSpanID": ROOT_SPAN,
            "operationName": "openclaw.tool.execution", "startTime": 1000000100,
            "durationNano": 100, "hasError": True, "tags": [
                {"key": "gen_ai.operation.name", "value": {"string": "execute_tool"}},
                {"key": "gen_ai.tool.name", "value": {"string": "read_file"}},
                {"key": "gen_ai.tool.call.arguments", "value": {"string": '{"path":"notes.txt"}'}},
                {"key": "gen_ai.tool.call.result", "value": {"string": '{"text":"expected contents"}'}},
            ],
        }]
        source, _ = convert(normalized({"result": {"spans": rows}}), task_spec())
        result = json.loads(source["steps"][-1]["env_text"])
        self.assertEqual(result["status"], "unset")
        self.assertIn("error", result)
        self.assertEqual(evaluate(source)["status"], "failure")

    def test_signoz_ids_and_time_fields_are_strictly_validated(self):
        base = {"traceID": TRACE, "spanID": ROOT_SPAN, "operationName": "openclaw.run",
                "startTime": 1000, "durationNano": 1}
        for mutate in (
            lambda value: value.update(traceID=1),
            lambda value: value.update(spanID="0" * 16),
            lambda value: value.update(startTime=1.5),
            lambda value: value.update(durationNano=-1),
            lambda value: value.update(durationNano="not-a-number"),
            lambda value: value.update(endTimeUnixNano=1002),
        ):
            value = dict(base)
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalized({"data": [value]})

    def test_current_signoz_snake_case_schema_keeps_api_coverage_gaps(self):
        rows = []
        for raw in exported()["spans"]:
            rows.append({"trace_id": raw["traceId"], "span_id": raw["spanId"],
                "parent_span_id": raw["parentSpanId"], "name": raw["name"],
                "timestamp": int(raw["startTimeUnixNano"]),
                "duration_nano": int(raw["endTimeUnixNano"]) - int(raw["startTimeUnixNano"]),
                "attributes": raw["attributes"], "resource": {"service.name": "openclaw"},
                "has_error": False, "status_code": 0, "status_message": ""})
        source, audit = convert(normalized({"data": {"spans": rows, "totalSpansCount": 2}}), task_spec())
        self.assertTrue(audit["coverage"]["content_complete"])
        self.assertEqual(evaluate(source)["status"], "success")
        for fields in ({"hasMissingSpans": True}, {"totalSpansCount": 3}, {"nextCursor": "page-2"}):
            with self.subTest(fields=fields):
                source, audit = convert(normalized({"data": {"spans": rows, **fields}}), task_spec())
                self.assertTrue(audit["ready"])
                self.assertFalse(audit["coverage"]["content_complete"])
                self.assertEqual(evaluate(source)["status"], "unknown")

    def test_document_lists_and_jsonl_keep_nested_export_completeness_declarations(self):
        declarations = [
            ({"hasMissingSpans": True}, "signoz_missing_spans"),
            ({"has_missing_spans": True}, "signoz_missing_spans"),
            ({"nextCursor": "page-2"}, "signoz_paginated_response"),
            ({"prev_cursor": "page-0"}, "signoz_paginated_response"),
            ({"totalSpansCount": 3}, "signoz_partial_span_list"),
        ]
        for fields, expected_gap in declarations:
            raw = {**exported(), **fields}
            for data in ([raw], {"data": [raw]}, [{"result": {"data": [raw]}}]):
                with self.subTest(fields=fields, data=data):
                    source, audit = convert(normalized(data), task_spec())
                    self.assertTrue(audit["ready"])
                    self.assertFalse(audit["coverage"]["content_complete"])
                    self.assertTrue(any(expected_gap in gap["missing"] for gap in audit["coverage"]["gaps"]))
                    self.assertEqual(evaluate(source)["status"], "unknown")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.jsonl"
            path.write_text(json.dumps({"data": {**exported(), "hasMissingSpans": True}}) + "\n",
                            encoding="utf-8")
            source, audit = convert(normalized(load_export(path)), task_spec())
            self.assertFalse(audit["coverage"]["content_complete"])
            self.assertEqual(evaluate(source)["status"], "unknown")

    def test_malformed_resource_scope_and_span_shapes_raise_value_errors(self):
        malformed = [
            {"spans": [None]}, {"spans": [42]}, {"spans": {}},
            {"resourceSpans": {}}, {"resourceSpans": [None]},
            {"resourceSpans": [{"resource": []}]},
            {"resourceSpans": [{"scopeSpans": "invalid"}]},
            {"resourceSpans": [{"scopeSpans": [None]}]},
            {"resourceSpans": [{"scopeSpans": [{"spans": {}}]}]},
            {"resourceSpans": [{"scopeSpans": [{"spans": [None]}]}]},
            {"resourceSpans": [{"instrumentationLibrarySpans": [False]}]},
        ]
        for data in malformed:
            with self.subTest(data=data), self.assertRaises(ValueError):
                normalized(data)
        for attributes in ([None], [{"key": "missing-value"}],
                           [{"key": "bad-array", "value": {"arrayValue": None}}],
                           [{"key": "bad-kvlist", "value": {"kvlistValue": None}}]):
            raw = tool()
            raw["attributes"] = attributes
            with self.subTest(attributes=attributes), self.assertRaises(ValueError):
                normalized(exported(raw))

    def test_otlp_timestamps_reject_fractional_or_noninteger_values(self):
        for key in ("startTimeUnixNano", "endTimeUnixNano"):
            for value in (100.9, 100.0, True, "100.9", [], {}):
                raw = tool()
                raw[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    normalized(exported(raw))
        raw = tool()
        raw.update(startTimeUnixNano=100, endTimeUnixNano=200)
        source, audit = convert(normalized(exported(raw)), task_spec())
        self.assertTrue(audit["coverage"]["content_complete"])
        self.assertEqual(evaluate(source)["status"], "success")

    def test_self_parent_and_parent_cycles_cannot_assert_complete_capture(self):
        self_parent = tool()
        self_parent["parentSpanId"] = self_parent["spanId"]
        first, second = tool(2, start=100, end=200), tool(3, start=200, end=300)
        first["parentSpanId"], second["parentSpanId"] = second["spanId"], first["spanId"]
        for raw, members in ((exported(self_parent), [self_parent["spanId"]]),
                             (exported(first, second), [first["spanId"], second["spanId"]])):
            with self.subTest(members=members):
                source, audit = convert(normalized(raw), task_spec())
                self.assertTrue(audit["ready"])
                self.assertFalse(audit["coverage"]["content_complete"])
                cycle = next(gap for gap in audit["coverage"]["gaps"] if "acyclic_parent_chain" in gap["missing"])
                self.assertEqual(cycle["span_ids"], members)
                self.assertEqual(evaluate(source)["status"], "unknown")

    def test_native_content_precedes_legacy_aliases_and_legacy_remains_usable(self):
        raw = tool(**{"openclaw.toolName": "wrong_alias", "openclaw.content.tool_input": '{"path":"wrong.txt"}',
                      "openclaw.content.tool_output": '{"text":"wrong alias"}'})
        source, _ = convert(normalized(exported(raw)), task_spec())
        call = json.loads(source["steps"][0]["parts"]["action"])
        self.assertEqual(call["tool"], "read_file")
        self.assertEqual(call["arguments"], {"path": "notes.txt"})
        self.assertEqual(call["provenance"]["argument_attribute"], "gen_ai.tool.call.arguments")
        self.assertEqual(evaluate(source)["status"], "unknown")
        source, _ = convert(normalized(exported(tool(legacy=True))), task_spec())
        self.assertEqual(evaluate(source)["status"], "success")
        self.assertEqual(json.loads(source["steps"][-1]["env_text"])["provenance"]["result_attribute"],
                         "openclaw.content.tool_output")

    def test_unset_is_preserved_and_never_promoted_to_error_or_task_outcome(self):
        for code in (0, "0", "STATUS_CODE_UNSET", "UNSET"):
            with self.subTest(code=code):
                source, _ = convert(normalized(exported(tool(status={"code": code}))),
                                    task_spec(with_contract=False))
                result = json.loads(source["steps"][-1]["env_text"])
                self.assertEqual(result["status"], "unset")
                self.assertNotIn("error", result)
                self.assertEqual(evaluate(source)["status"], "unknown")
        source, _ = convert(normalized(exported(tool(status={"code": 2, "message": "I/O failed"}))), task_spec())
        result = json.loads(source["steps"][-1]["env_text"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["message"], "I/O failed")
        self.assertEqual(evaluate(source)["status"], "failure")

    def test_duplicate_delivery_is_idempotent_but_conflicting_span_ids_are_rejected(self):
        data = exported()
        data["spans"].append(copy.deepcopy(data["spans"][-1]))
        spans = normalized(data)
        self.assertEqual(len(spans), 2)
        source, _ = convert(spans, task_spec())
        self.assertEqual(len(source["steps"]), 2)
        data["spans"][-1]["attributes"]["gen_ai.tool.call.result"] = '{"text":"different"}'
        with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
            normalized(data)

    def test_multiple_traces_require_explicit_selection_and_never_stitch_sessions(self):
        data = [exported(), exported(trace=OTHER_TRACE)]
        spans = normalized(data)
        audit = inspect(spans, task_spec())
        self.assertFalse(audit["ready"])
        self.assertIsNone(audit["selected_trace_id"])
        self.assertTrue(any("Multiple traces" in item for item in audit["issues"]))
        with self.assertRaisesRegex(ValueError, "Cannot import"):
            convert(spans, task_spec())
        source, audit = convert(spans, task_spec(), trace_id=OTHER_TRACE)
        self.assertEqual(audit["selected_trace_id"], OTHER_TRACE)
        self.assertEqual(source["telemetry"]["trace_id"], OTHER_TRACE)
        self.assertEqual({r["trace_id"] for r in source["telemetry"]["source_map"]}, {OTHER_TRACE})
        with self.assertRaisesRegex(ValueError, "absent"):
            inspect(spans, task_spec(), trace_id="3" * 32)

    def test_overlapping_tools_are_rejected_but_touching_intervals_stay_ordered(self):
        spans = normalized(exported(tool(2, start=100, end=300), tool(3, start=200, end=400)))
        self.assertFalse(inspect(spans, task_spec())["ready"])
        with self.assertRaisesRegex(ValueError, "Overlapping tool spans"):
            convert(spans, task_spec())
        # Input delivery order is not execution order, and a shared endpoint is valid.
        spans = normalized(exported(tool(3, start=200, end=300), tool(2, start=100, end=200)))
        source, _ = convert(spans, task_spec())
        self.assertEqual([row["span_id"] for row in source["telemetry"]["source_map"]],
                         [f"{2:016x}", f"{3:016x}"])

    def test_metadata_only_and_missing_task_do_not_reconstruct_tool_content(self):
        spans = normalized(exported(tool(content=False)))
        audit = inspect(spans, task_spec())
        self.assertFalse(audit["ready"])
        self.assertFalse(audit["coverage"]["content_complete"])
        self.assertIn("tool_arguments", audit["coverage"]["gaps"][0]["missing"])
        with self.assertRaisesRegex(ValueError, "lacks required captured"):
            convert(spans, task_spec())
        data = {"spans": [span(attributes={"gen_ai.input.messages": '[{"role":"user","content":"a task"}]'})]}
        self.assertFalse(inspect(normalized(data), task_spec())["ready"])
        audit = inspect(normalized(), load_task())
        self.assertFalse(audit["ready"])
        self.assertTrue(any("Original task text" in item for item in audit["issues"]))

    def test_explicit_error_without_result_preserves_omission_and_coverage_gap(self):
        raw = tool(status={"code": "STATUS_CODE_ERROR", "message": "failed before return"})
        del raw["attributes"]["gen_ai.tool.call.result"]
        source, audit = convert(normalized(exported(raw)), task_spec())
        self.assertTrue(audit["ready"])
        self.assertFalse(audit["coverage"]["content_complete"])
        result = json.loads(source["steps"][-1]["env_text"])
        self.assertNotIn("result", result)
        self.assertEqual(result["status"], "error")
        self.assertEqual(evaluate(source)["status"], "unknown")

    def test_terminal_response_identity_and_original_provenance_are_retained_without_mental_modules(self):
        payload = {"text": "最后返回", "values": [0, False, None, ""]}
        spans = normalized(exported(tool(2), tool(3, start=300, end=400, result=payload)))
        before = copy.deepcopy(spans)
        source, audit = convert(spans, task_spec(), trajectory_id="synthetic-terminal")
        self.assertEqual(spans, before)
        self.assertEqual(source["trajectory_id"], "synthetic-terminal")
        self.assertEqual(len(source["steps"]), 3)
        terminal = source["steps"][-1]
        feedback = json.loads(terminal["env_text"])
        self.assertEqual(feedback["result"], payload)
        self.assertEqual(feedback["trace_id"], TRACE)
        self.assertEqual(feedback["span_id"], f"{3:016x}")
        self.assertEqual(feedback["provenance"]["tool_call_id"], "model-call-3")
        self.assertEqual(feedback["provenance"]["source_digest"], spans[-1]["source_digest"])
        self.assertEqual(terminal["agent_text"], "")
        self.assertIsNone(terminal["parts"]["action"])
        self.assertEqual(source["telemetry"]["source_map"][-1]["result_step"], terminal["t"])
        for step in source["steps"]:
            self.assertTrue(all(step["parts"][module] is None for module in ("memory", "reflection", "plan")))
        self.assertTrue(audit["coverage"]["content_complete"])

    def test_no_completeness_declaration_or_task_contract_keeps_outcome_unknown(self):
        spans = normalized()
        source, audit = convert(spans, load_task(task="Obtain the expected contents of notes.txt."))
        self.assertTrue(audit["ready"])
        self.assertFalse(audit["coverage"]["declared_complete"])
        self.assertNotIn("task_contract", source)
        self.assertEqual(evaluate(source)["status"], "unknown")
        source, _ = convert(spans, task_spec(complete=False))
        self.assertEqual(evaluate(source)["status"], "unknown")
        source, _ = convert(spans, task_spec())
        self.assertEqual(evaluate(source)["status"], "success")

    def test_truncation_redaction_and_missing_parents_prevent_complete_evidence(self):
        cases = [
            tool(result={"text": "expected contents", "truncated": True}),
            tool(result={"text": "[truncated]"}),
            tool(result={"text": "[redacted]"}),
            tool(**{"openclaw.content.truncated": True}),
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                source, audit = convert(normalized(exported(raw)), task_spec())
                self.assertFalse(audit["coverage"]["content_complete"])
                self.assertEqual(evaluate(source)["status"], "unknown")
        raw = tool()
        raw["parentSpanId"] = f"{999:016x}"
        source, audit = convert(normalized(exported(raw)), task_spec())
        self.assertFalse(audit["coverage"]["content_complete"])
        self.assertTrue(any("parent_spans" in gap["missing"] for gap in audit["coverage"]["gaps"]))
        self.assertEqual(evaluate(source)["status"], "unknown")

    def test_otel_dropped_fields_and_structural_omission_markers_are_coverage_gaps(self):
        raw = tool()
        raw["droppedAttributesCount"] = 1
        source, audit = convert(normalized(exported(raw)), task_spec())
        self.assertFalse(audit["coverage"]["content_complete"])
        self.assertEqual(evaluate(source)["status"], "unknown")
        for marker in ("omittedItems", "omittedFields"):
            with self.subTest(marker=marker):
                raw = tool(result={"text": "expected contents", marker: 2})
                source, audit = convert(normalized(exported(raw)), task_spec())
                self.assertFalse(audit["coverage"]["content_complete"])
                self.assertEqual(evaluate(source)["status"], "unknown")

    def test_task_file_validates_explicit_capture_contract_and_preserves_exact_task(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.json"
            spec = task_spec()
            spec["task"] = "  原始任务与约束。\nSecond line.  "
            path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8-sig")
            loaded = load_task(path)
            source, _ = convert(normalized(), loaded)
            self.assertEqual(source["task"], spec["task"])
            self.assertEqual(source["task_contract"], spec["contract"])
            self.assertEqual(evaluate(source)["status"], "success")
            with self.assertRaisesRegex(ValueError, "not both"):
                load_task(path, "another task")
            for bad in ({**spec, "extra": "ignored?"},
                        {**spec, "capture": {"complete": "true", "reason": "invalid boolean"}},
                        {**spec, "capture": {"complete": True, "reason": ""}},
                        {**spec, "contract": {**contract(), "checks": []}}):
                with self.subTest(bad=bad):
                    path.write_text(json.dumps(bad), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_task(path)

    def test_jsonl_and_duplicate_json_keys_are_handled_without_silent_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.jsonl"
            path.write_text("\n".join(json.dumps(raw) for raw in exported()["spans"]), encoding="utf-8")
            self.assertEqual(len(normalized(load_export(path))), 2)
            path.write_text('{"traceId":"a","traceId":"b"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_export(path)
        raw = tool()
        raw["attributes"] = [{"key": "same", "value": {"stringValue": value}} for value in ("a", "b")]
        with self.assertRaisesRegex(ValueError, "duplicate OTLP attribute"):
            normalized(exported(raw))

    def test_import_output_cannot_overwrite_an_existing_capture_or_prior_import(self):
        spans = normalized()
        source, audit = convert(spans, task_spec(), trajectory_id="synthetic-preserved")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "capture.json"
            capture.write_text(json.dumps(exported()), encoding="utf-8")
            original = capture.read_bytes()
            folder = save_import(source, audit, spans, root)
            first = {p.name: p.read_bytes() for p in folder.iterdir()}
            self.assertEqual(capture.read_bytes(), original)
            self.assertEqual(json.loads((folder / "original_spans.json").read_text(encoding="utf-8")), exported()["spans"])
            self.assertEqual(json.loads((folder / "input.json").read_text(encoding="utf-8")), source)
            with self.assertRaises(FileExistsError):
                save_import(source, audit, spans, root)
            self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, first)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as rejected:
                main([str(capture), "--task", source["task"], "--trajectory-id", source["trajectory_id"],
                      "--output-dir", str(root)])
            self.assertEqual(rejected.exception.code, 2)
            self.assertEqual(capture.read_bytes(), original)
            self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, first)


if __name__ == "__main__":
    unittest.main()
