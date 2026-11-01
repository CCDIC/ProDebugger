"""Source integrity, exact cloud identities and incomplete-cloud import behavior."""
from copy import deepcopy
import hashlib
import json
import unittest

from prodebugger.signoz_import import import_documents, validate_signoz_source
from prodebugger.session_import import validate_session_source


TRACE = "a" * 32
OTHER = "b" * 32


def span(ident, attributes, trace=TRACE, **extra):
    return {"trace_id": trace, "span_id": ident, "parent_span_id": "f" * 16,
            "name": "openclaw.model" if "gen_ai.input.messages" in attributes else "openclaw.tool",
            "attributes": deepcopy(attributes), **extra}


def document(value, name="cloud.json"):
    return {"name": name, "text": json.dumps(value, ensure_ascii=False)}


def nested_fixture():
    code = 'const x = await tools.read({path:"/workspace/report.json"}); console.log(x);'
    call = "call_outer"
    child = "tool_call:call_outer:read:1"
    user = {"role": "user", "parts": [{"type": "text", "content": "Read /workspace/report.json and report its count."}]}
    injected = {"role": "user", "parts": [{"type": "text", "content": "<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>\nActive sessions: none"}]}
    assistant = {"role": "assistant", "parts": [{"type": "tool_call", "name": "exec", "id": call,
                                                      "arguments": {"code": code}}]}
    result = {"content": [{"type": "text", "text": "Count: 3"}], "details": {"content": "Count: 3"}}
    response = {"role": "tool", "parts": [{"type": "tool_call_response", "id": call, "response": result}]}
    final = {"role": "assistant", "parts": [{"type": "text", "content": "Count: 3"}]}
    before = span("1" * 16, {"gen_ai.input.messages": json.dumps([user, injected]),
                              "gen_ai.output.messages": json.dumps([assistant])})
    after = span("2" * 16, {"gen_ai.input.messages": json.dumps([user, assistant, response, injected]),
                             "gen_ai.output.messages": json.dumps([final])})
    inner = span("3" * 16, {"gen_ai.tool.call.id": child, "gen_ai.tool.name": "read",
                             "gen_ai.tool.call.arguments": json.dumps({"path": "/workspace/report.json"}),
                             "gen_ai.tool.call.result": json.dumps(result)}, status_code_string="Unset", has_error=False)
    return [before, after, inner], code


class SigNozImportChecks(unittest.TestCase):
    def test_real_shape_nested_exec_keeps_code_and_never_invents_cloud_span(self):
        raw, code = nested_fixture()
        documents = [document(value, str(i) + ".json") for i, value in enumerate(raw)]
        before = deepcopy(documents)
        case = import_documents(documents)[0]
        source = case["source"]
        calls = [event for event in source["session"]["events"] if event["type"] == "tool_call"]
        self.assertEqual([event["tool"] for event in calls], ["exec", "read"])
        self.assertEqual(calls[0]["arguments"]["code"], code)
        self.assertTrue(calls[0]["span_id"].startswith("analysis-call-"))
        self.assertEqual(calls[0]["span_identity_kind"], "analysis_call_id_not_cloud_span")
        self.assertEqual(calls[1]["span_id"], "3" * 16)
        self.assertEqual(calls[1]["parent_call_id"], "call_outer")
        self.assertEqual(calls[1]["invocation_layer"], "nested_tool")
        self.assertEqual(source["task"], "Read /workspace/report.json and report its count.")
        self.assertEqual(case["audit"]["coverage"]["cloud_spans"], 3)
        self.assertEqual(case["audit"]["coverage"]["represented_messages"], 5)
        self.assertTrue(case["audit"]["coverage"]["tool_join_complete"])
        self.assertFalse(case["audit"]["coverage"]["native_transcript_available"])
        self.assertTrue(all(event["original_seq"] is None for event in source["session"]["events"]))
        self.assertEqual(documents, before)
        self.assertEqual(validate_session_source(source)["validation"], "canonical_signoz_document_replay")
        primary = calls[0]["source"]
        self.assertIn("/arguments", primary["decoded_json_pointer"])
        self.assertEqual(primary["encoding"], "json_string")
        self.assertEqual(primary["document_sha256"], hashlib.sha256(documents[primary["document_index"]]["text"].encode()).hexdigest())

    def test_missing_return_and_arguments_are_unknown_and_case_is_retained(self):
        raw = span("4" * 16, {"gen_ai.tool.name": "read", "gen_ai.tool.call.id": "call-x"})
        case = import_documents([document(raw)])[0]
        events = case["source"]["session"]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "tool_call")
        self.assertIsNone(events[0]["arguments"])
        self.assertFalse(case["audit"]["coverage"]["tool_join_complete"])
        self.assertFalse(case["audit"]["coverage"]["task_recorded"])
        self.assertEqual(case["source"]["telemetry"]["task_source"]["status"], "missing")
        self.assertIsNone(case["source"]["telemetry"]["source_map"][0]["result_step"])
        kinds = {gap["kind"] for gap in case["audit"]["coverage"]["gaps"]}
        self.assertTrue({"missing_tool_arguments", "missing_tool_result", "missing_recorded_user_task"} <= kinds)
        validate_session_source(case["source"])

    def test_summary_only_trace_has_context_and_explicit_limitations(self):
        raw = {"traceID": TRACE, "spanID": "4" * 16, "operationName": "agent.run", "durationNano": 42,
               "weird_vendor_field": {"original": "still preserved"}}
        case = import_documents([document({"data": [raw]})])[0]
        self.assertEqual(case["source"]["session"]["events"][0]["type"], "trace_context")
        self.assertEqual(case["audit"]["coverage"]["tool_calls"], 0)
        self.assertIn("摘要缺内容", case["title"])
        self.assertTrue(any(gap["kind"] == "summary_export_without_content" for gap in case["audit"]["coverage"]["gaps"]))
        self.assertEqual(case["source"]["session"]["metadata"][0]["content"]["weird_vendor_field"], raw["weird_vendor_field"])
        validate_signoz_source(case["source"])

    def test_multiple_traces_never_join_same_call_id_across_traces(self):
        a = span("4" * 16, {"gen_ai.tool.call.id": "same", "gen_ai.tool.name": "read", "gen_ai.tool.call.arguments": '{"path":"a"}'})
        b = span("4" * 16, {"gen_ai.tool.call.id": "same", "gen_ai.tool.name": "read", "gen_ai.tool.call.arguments": '{"path":"b"}'}, trace=OTHER)
        cases = import_documents([document([a, b])], task="A supplied task")
        self.assertEqual(len(cases), 2)
        self.assertNotEqual(cases[0]["source"]["trajectory_id"], cases[1]["source"]["trajectory_id"])
        self.assertEqual([case["source"]["session"]["events"][0]["arguments"]["path"] for case in cases], ["a", "b"])
        self.assertTrue(all(case["source"]["telemetry"]["task_source"]["status"] == "explicit_user_override" for case in cases))
        for case in cases:
            validate_session_source(case["source"])

    def test_duplicate_records_and_conflicting_arguments_are_audited(self):
        a = span("4" * 16, {"gen_ai.tool.call.id": "one", "gen_ai.tool.name": "read",
                             "gen_ai.tool.call.arguments": '{"path":"a"}'})
        b = deepcopy(a)
        b["attributes"]["gen_ai.tool.call.arguments"] = '{"path":"b"}'
        case = import_documents([document([a, a, b])])[0]
        self.assertEqual(case["audit"]["coverage"]["tool_calls"], 1)
        self.assertTrue(case["audit"]["duplicates"])
        self.assertTrue(case["audit"]["conflicts"])
        self.assertIsNone(case["source"]["session"]["events"][0]["arguments"])
        self.assertTrue(any(gap["kind"] == "conflicting_tool_arguments" for gap in case["audit"]["coverage"]["gaps"]))
        self.assertEqual(len(case["source"]["session"]["metadata"]), 3)

    def test_otlp_resource_scope_and_value_wrappers(self):
        value = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "openclaw"}}]},
                 "scopeSpans": [{"scope": {"name": "openclaw"}, "spans": [{"traceId": TRACE, "spanId": "5" * 16,
                    "name": "tool", "attributes": [
                        {"key": "gen_ai.tool.call.id", "value": {"stringValue": "otlp-call"}},
                        {"key": "gen_ai.tool.name", "value": {"stringValue": "read"}},
                        {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": '{"path":"data.json"}'}},
                        {"key": "gen_ai.tool.call.result", "value": {"stringValue": '"done"'}}],
                    "status": {"code": 1}}]}]}]}
        case = import_documents([document(value)])[0]
        events = case["source"]["session"]["events"]
        self.assertEqual(events[0]["arguments"], {"path": "data.json"})
        self.assertEqual(events[1]["result"], "done")
        self.assertEqual(events[1]["status"], "ok")
        self.assertIn("/attributes/2/value/stringValue", events[0]["source"]["json_pointer"])
        self.assertIn("resource", case["source"]["session"]["metadata"][0]["resource_scope_context"])

    def test_jsonl_source_line_and_exact_original_file_hash(self):
        raw, _ = nested_fixture()
        text = "\n" + "\n".join(json.dumps(value) for value in raw) + "\n"
        case = import_documents([{"name": "trace.jsonl", "text": text}])[0]
        origins = [event["source"] for event in case["source"]["session"]["events"]]
        self.assertTrue(all(origin["line_number"] in (2, 3, 4) for origin in origins))
        self.assertEqual(case["audit"]["documents"][0]["sha256"], hashlib.sha256(text.encode()).hexdigest())

    def test_replay_rejects_tampered_coverage_events_and_unknown_top_level_fields(self):
        source = import_documents([document(value) for value in nested_fixture()[0]])[0]["source"]
        for mutate in (lambda value: value["session"]["coverage"].update(semantic_parse_complete=True),
                       lambda value: value["session"]["events"][0].update(text="rewritten"),
                       lambda value: value.update(ground_truth={"answer": 1}),
                       lambda value: value["session"]["raw_documents"][0].update(sha256="0" * 64)):
            changed = deepcopy(source)
            mutate(changed)
            with self.assertRaises(ValueError):
                validate_session_source(changed)

    def test_repeated_message_inside_one_snapshot_is_not_silently_deduplicated(self):
        message = {"role": "user", "content": "Same content, separate recorded positions."}
        raw = span("6" * 16, {"gen_ai.input.messages": json.dumps([message, message])})
        case = import_documents([document(raw)])[0]
        self.assertEqual(case["audit"]["coverage"]["represented_messages"], 2)
        events = case["source"]["session"]["events"]
        self.assertNotEqual(events[0]["event_id"], events[1]["event_id"])

    def test_openai_messages_and_nontext_blocks_preserve_original_content(self):
        messages = [{"role": "user", "content": "Read only report.json"},
                    {"role": "assistant", "content": [{"type": "thinking", "thinking": "Only explicitly recorded text."}],
                     "tool_calls": [{"id": "openai-call", "type": "function", "function": {"name": "read", "arguments": '{"path":"report.json"}'}}]},
                    {"role": "tool", "tool_call_id": "openai-call", "name": "read", "content": "done"}]
        raw = span("7" * 16, {"gen_ai.input.messages": json.dumps(messages)})
        case = import_documents([document(raw)])[0]
        self.assertTrue(case["audit"]["coverage"]["uninterpreted_content_blocks"])
        self.assertEqual(case["audit"]["coverage"]["tool_calls"], 1)
        self.assertTrue(case["audit"]["coverage"]["tool_join_complete"])
        result = next(event for event in case["source"]["session"]["events"] if event["type"] == "tool_result")
        self.assertEqual(result["result"], "done")
        self.assertEqual(result["status"], "unset")

    def test_invalid_input_never_executes_or_fabricates_an_empty_success(self):
        for docs in ([], [{"name": "broken", "text": "{bad}"}], [document({"logs": [{"message": "hello"}]})]):
            with self.assertRaises(ValueError):
                import_documents(docs)

    def test_vendor_aliases_merge_complementary_blocks_without_false_conflict(self):
        raw, _ = nested_fixture()
        for value in raw:
            attrs = value["attributes"]
            for direction in ("input", "output"):
                key = "gen_ai." + direction + ".messages"
                if key not in attrs:
                    continue
                converted = []
                for message in json.loads(attrs[key]):
                    content, role, extra = [], message["role"], {}
                    for block in message["parts"]:
                        if block["type"] == "text":
                            content.append({"type": "text", "text": block["content"]})
                        elif block["type"] == "tool_call":
                            content += [{"type": "reasoning", "redacted": True},
                                        {"type": "toolCall", "id": block["id"], "name": block["name"], "arguments": block["arguments"]}]
                        else:
                            role = "toolResult"
                            extra = {"toolCallId": block["id"], "toolName": "exec"}
                            content.append({"type": "text", "text": json.dumps(block["response"])})
                    converted.append({"role": role, "content": content, **extra})
                attrs["openclaw.content." + direction + "_messages"] = json.dumps(converted)
        case = import_documents([document(raw)])[0]
        self.assertEqual(case["audit"]["coverage"]["represented_messages"], 5)
        self.assertFalse(any(gap["kind"] == "conflicting_tool_result" for gap in case["audit"]["coverage"]["gaps"]))
        self.assertEqual(case["audit"]["coverage"]["tool_calls"], 2)
        self.assertTrue(case["audit"]["coverage"]["uninterpreted_content_blocks"])

    def test_constructed_input_declares_origin_without_becoming_a_real_run(self):
        raw, _ = nested_fixture()
        for value in raw:
            value["prodebugger_input_provenance"] = {"kind": "offline_constructed_copy", "executed": False,
                                                     "baseline_document_sha256": "0" * 64}
        case = import_documents([document(raw)])[0]
        self.assertEqual(case["source"]["telemetry"]["data_origin"], "constructed_variant_from_signoz_export")
        self.assertIn("未执行", case["source"]["session"]["source_label"])
        self.assertEqual(case["audit"]["source_kind"], "constructed_variant_from_signoz_export")
        validate_session_source(case["source"])

    def test_missing_response_field_is_not_a_recorded_null_return(self):
        raw = span("8" * 16, {"gen_ai.input.messages": json.dumps([
            {"role": "assistant", "parts": [{"type": "tool_call", "id": "missing", "name": "read", "arguments": {}}]},
            {"role": "tool", "parts": [{"type": "tool_call_response", "id": "missing"}]}])})
        case = import_documents([document(raw)])[0]
        self.assertFalse(case["audit"]["coverage"]["tool_join_complete"])
        self.assertFalse(any(event["type"] == "tool_result" for event in case["source"]["session"]["events"]))

    def test_unknown_boolean_status_is_not_integer_success(self):
        raw = span("9" * 16, {"gen_ai.tool.call.id": "status", "gen_ai.tool.name": "read",
                               "gen_ai.tool.call.result": '"recorded"'}, status={"code": True})
        case = import_documents([document(raw)])[0]
        result = next(event for event in case["source"]["session"]["events"] if event["type"] == "tool_result")
        self.assertEqual(result["status"], "unset")


if __name__ == "__main__":
    unittest.main()
