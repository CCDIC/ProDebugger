"""Recorded message coverage, exact tool joins and conservative source parsing."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from prodebugger.record_bridge import digest
from prodebugger.session_import import convert_session, validate_session_source
from prodebugger.parse_audit import prepare
from profiles import openclaw


def fixture():
    task = "Read A; if unavailable read B. Report the actual content."
    calls = [{"type": "tool_call", "trace_id": "trace1", "span_id": "span" + str(n),
              "tool": "read", "arguments": {"path": path}, "provenance": {"tool_call_id": "call" + str(n)}}
             for n, path in enumerate(("A", "B"), 1)]
    results = [{"type": "tool_result", "trace_id": "trace1", "span_id": "span" + str(n),
                "tool": "read", "status": status, "provenance": {"tool_call_id": "call" + str(n)},
                "result": {"content": [{"type": "text", "text": text}], "details": {"text": text}}}
               for n, status, text in ((1, "error", "File not found: A"), (2, "unset", "42"))]
    context = {"type": "tool_context", "trace_id": "trace1"}
    steps = []
    for t, observation, action in ((1, context, calls[0]), (2, results[0], calls[1]), (3, results[1], None)):
        action_text = json.dumps(action) if action else None
        steps.append({"t": t, "env_text": json.dumps(observation), "agent_text": action_text or "",
                      "admissible": [], "parts": {"memory": None, "reflection": None, "plan": None,
                                                  "action": action_text}})
    source = {"trajectory_id": "test-session", "task_type": "openclaw", "task": task,
              "steps": steps, "telemetry": {"trace_id": "trace1", "source_map": [
                  {"trace_id": "trace1", "span_id": "span" + str(n), "action_step": n,
                   "result_step": n + 1, "tool": "read"} for n in (1, 2)]}}
    native_messages = [
        {"role": "user", "content": task},
        {"role": "assistant", "content": [{"type": "text", "text": "I will read A."},
         {"type": "toolCall", "id": "call1", "name": "read", "arguments": {"path": "A"}}],
         "stopReason": "toolUse"},
        {"role": "toolResult", "toolCallId": "call1", "toolName": "read", "isError": True,
         **deepcopy(results[0]["result"])},
        {"role": "assistant", "content": [{"type": "toolCall", "id": "call2", "name": "read",
         "arguments": {"path": "B"}}], "stopReason": "toolUse"},
        {"role": "toolResult", "toolCallId": "call2", "toolName": "read", "isError": False,
         **deepcopy(results[1]["result"])},
        {"role": "assistant", "content": [{"type": "text", "text": "B contains 42; A was missing."}],
         "stopReason": "stop"},
    ]
    events = [{"seq": 0, "event": {"type": "session", "id": "session1"}},
              {"seq": 1, "event": {"type": "thinking_level_change", "id": "metadata1", "thinkingLevel": "off"}}]
    events += [{"seq": n + 2, "event": {"type": "message", "id": "message" + str(n), "message": message}}
               for n, message in enumerate(native_messages)]
    native = {"schema": "openclaw-native-transcript/v1", "session_id": "session1", "events": events,
              "capture_complete": False, "source": {"table": "transcript_events"}}
    return source, native


class SessionImportChecks(unittest.TestCase):
    def test_all_recorded_messages_and_metadata_preserved_without_mutating_input(self):
        base, native = fixture()
        before = deepcopy((base, native))
        source, audit = convert_session(base, native)
        self.assertEqual((base, native), before)
        session = source["session"]
        self.assertEqual(session["native_transcript"], native)
        self.assertEqual(session["base_steps"], base["steps"])
        self.assertEqual([e["type"] for e in session["events"]], [
            "session_message", "session_message", "tool_call", "tool_result",
            "session_message", "tool_call", "tool_result", "session_message"])
        self.assertEqual(session["coverage"]["native_messages"], 6)
        self.assertEqual(session["coverage"]["represented_messages"], 6)
        self.assertEqual(session["coverage"]["preserved_metadata_events"], 2)
        self.assertFalse(session["coverage"]["semantic_parse_complete"])
        self.assertFalse(session["coverage"]["capture_complete_claim"])
        self.assertEqual(session["events"][-1]["stop_reason"], "stop")
        self.assertEqual(audit["output_sha256"], digest(source))
        self.assertEqual([step["t"] for step in source["steps"]], list(range(1, 9)))
        self.assertTrue(all(step["parts"][part] is None for step in source["steps"]
                            for part in ("memory", "reflection", "plan")))

    def test_distinct_call_ids_and_span_steps_remain_precise_for_same_tool(self):
        source, _ = convert_session(*fixture())
        mapping = source["telemetry"]["source_map"]
        self.assertEqual([(m["tool_call_id"], m["action_step"], m["result_step"]) for m in mapping],
                         [("call1", 3, 4), ("call2", 6, 7)])
        self.assertEqual([(m["base_action_step"], m["base_result_step"]) for m in mapping], [(1, 2), (2, 3)])
        self.assertEqual(mapping[0]["action_original_seq"], 3)
        self.assertEqual(mapping[1]["action_original_seq"], 5)

    def test_original_pointers_resolve_and_hashes_are_explicitly_scoped(self):
        source, _ = convert_session(*fixture())
        native = source["session"]["native_transcript"]
        for event in source["session"]["events"] + source["session"]["metadata"]:
            provenance = event["source"]
            current = native
            for token in provenance["json_pointer"].strip("/").split("/"):
                current = current[int(token)] if isinstance(current, list) else current[token]
            self.assertIsInstance(current, dict)
            row = next(r for r in native["events"] if r["seq"] == event["original_seq"])
            self.assertEqual(provenance["sha256"], digest(row["event"]))
            self.assertEqual(provenance["hash_scope"], "canonical_native_event")

    def test_audit_accepts_session_envelopes_without_semantic_backoff_or_invented_memory(self):
        source, _ = convert_session(*fixture())
        audited = prepare(source, online=False)
        self.assertTrue(all(unit["status"] == "template" for unit in audited["units"].values()))
        relations = [f["rel"] for unit in audited["units"].values() for f in unit["facts"]]
        self.assertEqual(relations.count("message_content"), 4)
        self.assertEqual(relations.count("tool_result"), 2)
        self.assertNotIn("planned", relations)
        self.assertNotIn("recalled_summary", relations)
        final = json.loads(source["steps"][-1]["env_text"])
        self.assertIn("B contains 42", final["text"])

    def test_session_task_call_arguments_result_and_status_mismatches_rejected(self):
        mutators = [
            lambda b, n: n.update(session_id="other"),
            lambda b, n: b["telemetry"].update(session_id="other"),
            lambda b, n: b.update(task="different task"),
            lambda b, n: n["events"][3]["event"]["message"]["content"][1].update(id="other-call"),
            lambda b, n: n["events"][3]["event"]["message"]["content"][1].update(name="write"),
            lambda b, n: n["events"][3]["event"]["message"]["content"][1].update(arguments={"path": "C"}),
            lambda b, n: n["events"][4]["event"]["message"]["content"][0].update(text="changed"),
            lambda b, n: n["events"][4]["event"]["message"].update(details={"text": "changed"}),
            lambda b, n: n["events"][4]["event"]["message"].update(isError=False),
            lambda b, n: n["events"].pop(4),
        ]
        for change in mutators:
            with self.subTest(change=mutators.index(change)):
                base, native = fixture()
                change(base, native)
                with self.assertRaises(ValueError):
                    convert_session(base, native)

    def test_unmatched_extra_calls_and_duplicate_sequence_never_silently_drop(self):
        base, native = fixture()
        native["events"][3]["event"]["message"]["content"].append(
            {"type": "toolCall", "id": "unrecorded-span", "name": "read", "arguments": {"path": "C"}})
        with self.assertRaisesRegex(ValueError, "unmatched"):
            convert_session(base, native)
        base, native = fixture()
        native["events"][-1]["seq"] = native["events"][-2]["seq"]
        with self.assertRaisesRegex(ValueError, "sequence"):
            convert_session(base, native)

    def test_type_changes_in_native_arguments_are_not_equal_json_values(self):
        base, native = fixture()
        action = json.loads(base["steps"][0]["parts"]["action"])
        action["arguments"]["extra"] = 1
        base["steps"][0]["parts"]["action"] = json.dumps(action)
        native["events"][3]["event"]["message"]["content"][1]["arguments"]["extra"] = True
        with self.assertRaisesRegex(ValueError, "arguments mismatch"):
            convert_session(base, native)

    def test_unknown_recorded_blocks_are_retained_and_reported_not_as_plans(self):
        base, native = fixture()
        block = {"type": "image", "data": "test-image-reference"}
        native["events"][3]["event"]["message"]["content"].append(block)
        source, _ = convert_session(base, native)
        self.assertIn(block, source["session"]["events"][1]["content"])
        self.assertEqual(source["session"]["coverage"]["uninterpreted_content_blocks"][0]["type"], "image")
        self.assertIsNone(source["steps"][1]["parts"]["plan"])

    def test_malformed_session_envelope_cannot_claim_different_text(self):
        source, _ = convert_session(*fixture())
        observation = json.loads(source["steps"][0]["env_text"])
        observation["text"] = "a made-up task"
        with self.assertRaisesRegex(ValueError, "exactly reflect"):
            openclaw.parse_observation(json.dumps(observation))

    def test_validation_replays_preserved_input_without_reopening_files(self):
        base, native = fixture()
        with tempfile.TemporaryDirectory() as directory:
            base_path, native_path = Path(directory) / "base.json", Path(directory) / "native.json"
            base_path.write_text(json.dumps(base), encoding="utf-8")
            native_path.write_text(json.dumps(native), encoding="utf-8")
            source, _ = convert_session(base_path, native_path, trajectory_id="alternate-id")
        before = deepcopy(source)
        audit = validate_session_source(source)
        self.assertEqual(source, before)
        self.assertEqual(audit["validation"], "canonical_source_replay")
        self.assertEqual(source["session"]["base_source"], base)

    def test_validation_rejects_derived_event_step_coverage_mapping_and_raw_tampering(self):
        mutations = [
            lambda s: s["session"]["events"].pop(),
            lambda s: s["session"]["events"][0].update(text="fabricated user task"),
            lambda s: s["session"]["events"][2].update(span_id="another-span"),
            lambda s: s["steps"][-1].update(env_text=json.dumps({"type": "tool_context", "trace_id": "trace1"})),
            lambda s: s["session"]["coverage"].update(semantic_parse_complete=True),
            lambda s: s["telemetry"]["source_map"][0].update(action_step=8),
            lambda s: s["session"]["native_transcript"]["events"][-1]["event"]["message"]["content"][0].update(text="changed raw reply"),
            lambda s: s["session"]["native_transcript"]["events"].pop(),
            lambda s: s["session"]["base_source"].update(task="new task"),
            lambda s: s.update(task="new task"),
            lambda s: s.update(facts=[{"value": "fake parsed result"}]),
            lambda s: s["session"]["base_source"].update(ground_truth={"fault": "answer"}),
            lambda s: s["session"].update(base_source_sha256="unverified"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                source, _ = convert_session(*fixture())
                mutate(source)
                with self.assertRaises(ValueError):
                    validate_session_source(source)

    def test_conversion_keeps_only_normal_base_inputs_and_validation_requires_raw_base(self):
        base, native = fixture()
        base.update(display_label="private display", ground_truth={"fault": "answer"}, facts=["fake"])
        source, _ = convert_session(base, native)
        for key in ("display_label", "ground_truth", "facts"):
            self.assertNotIn(key, source)
            self.assertNotIn(key, source["session"]["base_source"])
        validate_session_source(source)
        del source["session"]["base_source"]
        with self.assertRaisesRegex(ValueError, "preserved base_source"):
            validate_session_source(source)


if __name__ == "__main__":
    unittest.main()
