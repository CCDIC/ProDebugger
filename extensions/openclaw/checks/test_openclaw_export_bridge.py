"""Actual nested export semantics, replay protection and honest source gaps."""
from copy import deepcopy
import json
import unittest

from prodebugger.openclaw_export_bridge import convert_export
from prodebugger.session_import import validate_session_source
from prodebugger.record_bridge import build_bundle, digest


def fixture():
    sid, task, wrapper, child = "session-x", "Read only aggregate.json.", "exec-x", "tool_call:exec-x:read:1"
    result = {"content": [{"type": "text", "text": "Count: 3"}], "details": {"kind": "text", "content": "Count: 3"}}
    user = {"role": "user", "content": task}
    assistant = {"role": "assistant", "content": [{"type": "thinking", "thinking": "Recorded, never reconstructed."},
        {"type": "toolCall", "id": wrapper, "name": "exec", "arguments": {"code": "***"}}], "stopReason": "toolUse"}
    nested = {"role": "custom", "customType": "openclaw.nested-tool.v1", "content": "", "details": {
        "parentToolCallId": wrapper, "toolCallId": child, "toolName": "read", "input": {"path": "/workspace/aggregate.json"}, "result": result}}
    wrapper_result = {"role": "toolResult", "toolCallId": wrapper, "toolName": "exec", "isError": False, **deepcopy(result)}
    final = {"role": "assistant", "content": [{"type": "text", "text": "Count: 3"}], "stopReason": "stop"}
    entries, events = [], []

    def add(source, kind, data, entry_id=None):
        row = {"traceSchema": "openclaw-trajectory", "schemaVersion": 1, "sessionId": sid,
               "source": source, "type": kind, "seq": len(events) + 1, "data": deepcopy(data)}
        if entry_id:
            row["entryId"] = entry_id
            entries.append({"type": "message", "id": entry_id, "message": deepcopy(data["message"])})
        events.append(row)
    add("transcript", "user.message", {"message": user}, "user-x")
    add("transcript", "assistant.message", {"message": assistant}, "assistant-x")
    add("transcript", "tool.call", {"toolCallId": wrapper, "name": "exec", "arguments": {"code": "***"}})
    add("runtime", "tool.call", {"toolCallId": wrapper, "name": "exec", "args": {"code": "***"}})
    add("runtime", "tool.call", {"toolCallId": child, "name": "read", "args": {"path": "/workspace/aggregate.json"}})
    add("runtime", "tool.result", {"toolCallId": child, "name": "read", "success": True, "result": result})
    add("transcript", "message.custom", {"message": nested}, "nested-x")
    add("runtime", "tool.result", {"toolCallId": wrapper, "name": "exec", "success": True, "result": result})
    add("transcript", "tool.result", {"message": wrapper_result}, "result-x")
    add("transcript", "assistant.message", {"message": final}, "final-x")
    add("runtime", "session.ended", {"status": "completed"})
    raw = {"manifest": {"traceSchema": "openclaw-trajectory", "schemaVersion": 1, "sessionId": sid},
           "session_branch": {"header": {"id": sid}, "entries": entries}, "events": events,
           "prompts": {"latestSubmittedPrompt": task}}
    span = {"trace_id": "cloud-trace", "span_id": "cloud-read", "attributes": {"gen_ai.tool.call.id": child,
            "gen_ai.tool.call.arguments": json.dumps({"path": "/workspace/aggregate.json"}), "gen_ai.tool.call.result": json.dumps(result)}}
    return raw, span


class OfficialExportBridgeChecks(unittest.TestCase):
    def test_nested_runtime_calls_are_not_fabricated_model_calls(self):
        raw, span = fixture()
        before = deepcopy(raw)
        source, audit = convert_export(raw, [span])
        calls = [e for e in source["session"]["events"] if e["type"] == "tool_call"]
        self.assertEqual([e["tool"] for e in calls], ["exec", "read"])
        self.assertEqual(calls[1]["parent_call_id"], calls[0]["call_id"])
        self.assertEqual(calls[1]["invocation_layer"], "nested_tool")
        self.assertEqual(calls[1]["role"], "tool_runtime")
        self.assertEqual(len(source["steps"]), 7)
        self.assertEqual(len(source["session"]["record_map"]), len(raw["events"]))
        self.assertEqual(audit["coverage"]["cloud_verified_calls"], 1)
        self.assertEqual(raw, before)
        self.assertEqual(validate_session_source(source)["validation"], "canonical_official_export_replay")
        self.assertTrue(build_bundle(source).record.F)
        self.assertTrue(source["session"]["coverage"]["uninterpreted_content_blocks"])
        self.assertTrue(source["session"]["coverage"]["gaps"])

    def test_generated_claims_or_tampered_display_cannot_replace_replay(self):
        source, _ = convert_export(*((lambda raw, span: (raw, [span]))(*fixture())))
        for mutate in (lambda x: x["session"]["coverage"].update(semantic_parse_complete=True),
                       lambda x: x["steps"][3]["parts"].update(action="{}"),
                       lambda x: x.update(ground_truth={"answer": "read"})):
            changed = deepcopy(source)
            mutate(changed)
            with self.assertRaises(ValueError):
                validate_session_source(changed)

    def test_cloud_mismatch_does_not_inherit_verification(self):
        raw, span = fixture()
        span["attributes"]["gen_ai.tool.call.arguments"] = json.dumps({"path": "/different"})
        source, audit = convert_export(raw, [span])
        self.assertEqual(audit["coverage"]["cloud_verified_calls"], 0)
        self.assertFalse(json.loads(source["steps"][3]["parts"]["action"])["provenance"]["cloud_content_verified"])

    def test_explicit_branch_truncation_preserved_but_unrelated_conflict_rejected(self):
        raw, _ = fixture()
        raw["session_branch"]["entries"][-1] = "[Truncated]"
        source, audit = convert_export(raw)
        self.assertIn("final-x", audit["coverage"]["branch_message_crosscheck_unavailable"])
        self.assertEqual(source["session"]["official_export"]["session_branch"]["entries"][-1], "[Truncated]")
        raw["session_branch"]["entries"][0]["message"]["content"] = "Wrong user task."
        with self.assertRaisesRegex(ValueError, "message mismatch"):
            convert_export(raw)

    def test_missing_return_remains_missing_and_duplicate_identity_rejected(self):
        raw, _ = fixture()
        raw["events"] = [r for r in raw["events"] if not (r["type"] == "tool.result" and r["source"] == "runtime" and r["data"]["name"] == "read")]
        source, audit = convert_export(raw)
        self.assertFalse(audit["coverage"]["tool_join_complete"])
        self.assertTrue(any(x["kind"] == "missing_tool_result" for x in audit["coverage"]["gaps"]))
        extra = deepcopy(raw["events"][4])
        extra["seq"] = 99
        raw["events"].append(extra)
        with self.assertRaisesRegex(ValueError, "duplicate runtime"):
            convert_export(raw)

    def test_offline_provenance_survives_replay_without_leaking_ground_truth(self):
        raw, _ = fixture()
        raw["manifest"]["prodebugger_input_provenance"] = {"kind": "offline_constructed_copy", "executed": False, "baseline_sha256": digest(raw)}
        source, _ = convert_export(raw)
        self.assertEqual(source["telemetry"]["data_origin"], "constructed_variant_from_official_export")
        validate_session_source(source)
        raw["ground_truth"] = {"fault_call": "exec-x"}
        with self.assertRaises(ValueError):
            convert_export(raw)


if __name__ == "__main__":
    unittest.main()
