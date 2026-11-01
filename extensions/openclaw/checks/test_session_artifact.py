"""Session semantics: genuine provenance and unknowns survive presentation."""
from copy import deepcopy
import json
import unittest

from prodebugger.record_bridge import build_bundle, project
from prodebugger.session_artifact import augment_record, build_artifact
from prodebugger.session_checks import session_checks, session_findings


def session_source(attempts=None, *, final="```\nRevenue: 42\n```", speech="I'll read the file first.", contract=False):
    attempts = attempts if attempts is not None else [{}]
    events, steps = [], []
    task = "Read notes.txt and report its recorded contents."

    def append(kind, role, text="", content=None, action=None, observation=None, **extra):
        t = len(events) + 1
        event = {"event_id": "event-" + str(t), "step": t, "original_seq": t + 10,
                 "type": kind, "role": role, "text": text, "content": content if content is not None else [{"type": "text", "text": text}],
                 "trace_id": "trace-test", "source": {"json_pointer": "/events/" + str(t - 1), "sha256": "test-source-" + str(t)}, **extra}
        envelope = observation or ({"type": "session_message", "trace_id": "trace-test", "event_id": event["event_id"],
                                   "role": role, "text": text, "content": event["content"]} if kind == "session_message" else
                                  {"type": "tool_context", "trace_id": "trace-test"})
        action_text = json.dumps(action) if action else None
        steps.append({"t": t, "env_text": json.dumps(envelope), "agent_text": text or action_text or "", "admissible": [],
                      "parts": {"memory": None, "reflection": None, "plan": None, "action": action_text}})
        events.append(event)

    append("session_message", "user", task)
    for index, overrides in enumerate(attempts, 1):
        append("session_message", "assistant", speech)
        span = "span-" + str(index)
        action = {"type": "tool_call", "trace_id": "trace-test", "span_id": span, "tool": "read",
                  "arguments": {"path": "notes.txt"}}
        action.update(overrides.get("action", {}))
        result = {"type": "tool_result", "trace_id": "trace-test", "span_id": span, "tool": "read", "status": "ok",
                  "result": {"content": [{"type": "text", "text": "Revenue: 42\n"}]}}
        result.update(overrides.get("response", {}))
        if result.get("result") is None:
            result["result"] = {"content": [{"type": "text", "text": str(result.get("error", ""))}]}
        append("tool_call", "assistant", action=action, span_id=span, call_id="native-" + str(index))
        append("tool_result", "toolResult", observation=result, span_id=span, call_id="native-" + str(index))
    if final is not None:
        append("session_message", "assistant", final, stop_reason="stop")
    source = {"trajectory_id": "session-semantics-test", "task_type": "openclaw", "task": task, "steps": steps,
              "session": {"schema": "openclaw-session/v1", "session_id": "native-session-test", "events": events,
                          "coverage": {"recorded_messages_complete": True, "tool_join_complete": True}},
              "telemetry": {"trace_id": "trace-test", "coverage": {"declared_complete": True, "content_complete": True}}}
    if contract:
        source["task_contract"] = {"schema": "openclaw-tool-contract/v1", "complete": False,
            "checks": [{"id": "read-content", "tool": "read", "argument_equals": {"/path": "notes.txt"},
                        "result_equals": {"/content/0/text": "Revenue: 42\n"}}]}
    # Exercise the real lossless native importer and strict replay validator,
    # rather than trusting a hand-written session sidecar.
    from prodebugger.session_import import convert_session
    actions = [json.loads(step["parts"]["action"]) for step in steps if step["parts"].get("action")]
    outputs = [json.loads(step["env_text"]) for step in steps if json.loads(step["env_text"]).get("type") == "tool_result"]
    for index, (action, output) in enumerate(zip(actions, outputs), 1):
        action["provenance"] = {"tool_call_id": "native-" + str(index)}
        output["provenance"] = {"tool_call_id": "native-" + str(index)}
    base_steps = []
    for index in range(len(actions) + 1):
        action = json.dumps(actions[index]) if index < len(actions) else None
        observation = {"type": "tool_context", "trace_id": "trace-test"} if index == 0 else outputs[index - 1]
        base_steps.append({"t": index + 1, "env_text": json.dumps(observation), "agent_text": action or "", "admissible": [],
                           "parts": {"memory": None, "reflection": None, "plan": None, "action": action}})
    native_events, call_index = [{"seq": 0, "event": {"type": "session", "id": "native-session-test"}}], 0
    for event in events:
        if event["type"] == "tool_call":
            action = actions[call_index]
            native_events[-1]["event"]["message"]["content"].append({"type": "toolCall", "id": "native-" + str(call_index + 1),
                "name": action["tool"], "arguments": action["arguments"]})
            native_events[-1]["event"]["message"]["stopReason"] = "toolUse"
            continue
        if event["type"] == "tool_result":
            output = outputs[call_index]
            message = {"role": "toolResult", "toolCallId": "native-" + str(call_index + 1), "toolName": output["tool"],
                       "isError": output["status"] == "error", **deepcopy(output["result"])}
            call_index += 1
        else:
            message = {"role": event["role"], "content": deepcopy(event["content"])}
            if event.get("stop_reason"):
                message["stopReason"] = event["stop_reason"]
        native_events.append({"seq": event["original_seq"], "event": {"type": "message", "id": event["event_id"], "message": message}})
    base = {**source, "steps": base_steps}
    del base["session"]
    native = {"schema": "openclaw-native-transcript/v1", "session_id": "native-session-test", "events": native_events,
              "capture_complete": False, "source": {"table": "transcript_events"}}
    return convert_session(base, native)[0]


class SessionArtifactChecks(unittest.TestCase):
    def test_all_recorded_messages_preserved_without_inventing_memory(self):
        bundle = build_bundle(session_source())
        artifact = build_artifact(bundle)
        self.assertEqual(len(artifact["events"]), 5)
        self.assertFalse(any(f.slot in {"mem", "plan", "refl"} for f in bundle.record.F))
        self.assertTrue(any(f.rel == "session_explicit_plan" for f in bundle.record.F))
        self.assertTrue(all(row["relation"] != "admissibility" for row in artifact["rows"]))
        self.assertTrue(all(f.rel == "tool_call" for f in bundle.record.F if f.slot == "act"))
        self.assertLess(artifact["coverage"]["events_semantic"], artifact["coverage"]["events_preserved"])
        self.assertEqual({item["module"] for item in artifact["missing_modules"]}, {"memory", "reflection", "internal_plan"})

    def test_plain_speech_is_not_plan_or_environment_fact(self):
        bundle = build_bundle(session_source(speech="The sky is green; I remember yesterday."))
        self.assertFalse(any(f.rel == "session_explicit_plan" or f.slot == "mem" for f in bundle.record.F))
        speech = next(f for f in bundle.record.F if f.rel == "session_public_statement")
        self.assertEqual(speech.side, "mind")
        parsed_speech = [f for f in bundle.record.F if f.rel == "message_content" and f.t == speech.t]
        self.assertTrue(parsed_speech and all(f.side == "mind" for f in parsed_speech))

    def test_message_entities_reuse_original_profile_identity(self):
        bundle = build_bundle(session_source())
        messages = [e for e in bundle.record.E.values() if e.kind == "message"]
        self.assertEqual(len(messages), 3)
        self.assertTrue(all(e.index.startswith("trace-test/") for e in messages))

    def test_no_unsupported_previous_call_or_observation_basis(self):
        bundle = build_bundle(session_source([{}, {}]))
        calls = [f for f in bundle.record.F if f.rel == "tool_call"]
        self.assertTrue(all(f.basis == () for f in calls))
        results = [f for f in bundle.record.F if f.rel == "tool_result"]
        self.assertEqual([f.basis for f in results], [(f.fid,) for f in calls])
        messages = [f for f in bundle.record.F if f.rel in {"message_content", "message_role", "session_message_text"}]
        self.assertTrue(all(f.basis == () for f in messages))
        self.assertTrue(bundle.record.dependence_ok())

    def test_same_file_versions_preserve_original_record_chain_and_in_force(self):
        bundle = build_bundle(session_source([{}, {"response": {"result": {"content": [{"type": "text", "text": "Revenue: 43\n"}]}}}]))
        file = next(e for e in bundle.record.E.values() if e.kind == "file")
        chain = bundle.record.chain("session_file_content", (file,))
        self.assertEqual([f.value for f in chain], ["Revenue: 42\n", "Revenue: 43\n"])
        self.assertEqual(bundle.record.in_force("session_file_content", (file,), chain[1].t - 1).fid, chain[0].fid)
        projected_file = bundle.projection.ent(file.kind, file.index)
        self.assertEqual(bundle.projection.in_force("session_file_content", (projected_file,), chain[1].t).value, "Revenue: 43\n")
        row = next(r for r in build_artifact(bundle)["rows"] if r["relation"] == "session_file_content")
        self.assertEqual(row["versions"][0]["in_force_until"], chain[1].t - 1)

    def test_not_found_false_permission_failure_unknown_and_prior_content_invalidated(self):
        bundle = build_bundle(session_source([{}, {"response": {"status": "error", "error": "EACCES: permission denied", "result": None}},
                                               {"response": {"status": "error", "error": "File not found: notes.txt", "result": None}}]))
        exists = [f.value for f in bundle.record.F if f.rel == "session_file_exists"]
        self.assertEqual(exists, [True, None, False])
        content = [f.value for f in bundle.record.F if f.rel == "session_file_content"]
        self.assertEqual(content, ["Revenue: 42\n", None, None])
        row = next(r for r in build_artifact(bundle)["rows"] if r["relation"] == "session_file_exists")
        self.assertEqual(row["versions"][1]["status"], "unknown")

    def test_empty_successful_read_is_known_empty_not_missing(self):
        bundle = build_bundle(session_source([{"response": {"result": {"content": [{"type": "text", "text": ""}]}}}]))
        self.assertEqual([f.value for f in bundle.record.F if f.rel == "session_file_exists"], [True])
        self.assertEqual([f.value for f in bundle.record.F if f.rel == "session_file_content"], [""])

    def test_final_answer_exact_quote_has_narrow_pass_and_real_correspondence(self):
        bundle = build_bundle(session_source())
        artifact = build_artifact(bundle)
        check = next(c for c in artifact["checks"] if c["rule_id"] == "OC-SESSION-ANSWER-001")
        self.assertEqual(check["state"], "passed")
        self.assertIn("其他说明", check["explanation"])
        self.assertEqual({p["type"] for p in artifact["pointers"]}, {"source", "basis", "correspondence", "check"})

    def test_final_numeric_contradiction_is_at_answer_not_tool_call(self):
        bundle = build_bundle(session_source(final="The contents were:\n```\nRevenue: 99\n```"))
        finding = next(f for f in session_findings(bundle) if f["state"] == "confirmed")
        self.assertEqual(finding["rule_id"], "OC-SESSION-ANSWER-001")
        self.assertEqual(finding["call_ids"], [])
        self.assertEqual(finding["related_call_ids"], ["trace-test/span-1"])
        self.assertEqual(finding["affected_event_ids"], ["event-5"])
        self.assertEqual(finding["entity_ids"], ["message:trace-test/event-5"])
        self.assertTrue(all(bundle.record.fact(fid).rel == "session_final_answer" for fid in finding["primary_fids"]))

    def test_unsupported_answer_is_unknown_not_green(self):
        bundle = build_bundle(session_source(final="The organization appears financially healthy."))
        check = next(c for c in session_checks(bundle) if c["rule_id"] == "OC-SESSION-ANSWER-001")
        self.assertEqual(check["state"], "unknown")
        self.assertFalse(check["applied"])
        self.assertTrue(check["missing"])

    def test_multiple_conflicting_source_values_are_not_a_certain_contradiction(self):
        bundle = build_bundle(session_source([{}, {"action": {"arguments": {"path": "other.txt"}}, "response": {"result": {"content": [{"type": "text", "text": "Revenue: 43\n"}]}}}],
                                             final="```\nRevenue: 99\n```"))
        self.assertEqual(next(c for c in session_checks(bundle) if c["rule_id"] == "OC-SESSION-ANSWER-001")["state"], "unknown")

    def test_final_answer_compares_latest_same_target_observation(self):
        bundle = build_bundle(session_source([{}, {"response": {"result": {"content": [{"type": "text", "text": "Revenue: 43\n"}]}}}], final="Revenue: 42"))
        check = next(c for c in session_checks(bundle) if c["rule_id"] == "OC-SESSION-ANSWER-001")
        self.assertEqual(check["state"], "violated")
        self.assertEqual(len(check["primary_fids"]), 1)
        self.assertEqual(bundle.record.fact(check["primary_fids"][0]).rel, "session_final_answer")
        artifact = build_artifact(bundle)
        blamed = {pointer["to"]["fid"] for pointer in artifact["pointers"] if pointer["type"] == "check" and pointer["from"]["check_id"] == check["id"]}
        self.assertEqual(blamed, set(check["primary_fids"]))

    def test_missing_final_answer_unknown(self):
        bundle = build_bundle(session_source(final=None))
        check = next(c for c in session_checks(bundle) if c["rule_id"] == "OC-SESSION-ANSWER-001")
        self.assertEqual(check["state"], "unknown")
        self.assertEqual(check["fids"], [])

    def test_explicit_contract_reused_without_duplicate_issue(self):
        bundle = build_bundle(session_source(contract=True))
        check = next(c for c in session_checks(bundle) if c["rule_id"] == "OC-SESSION-CONTRACT-001")
        self.assertEqual(check["state"], "passed")
        self.assertFalse(any(c["rule_id"] == "OC-SESSION-CONTRACT-001" for c in session_findings(bundle)))
        fact = next(f for f in bundle.record.F if f.rel == "session_contract")
        self.assertEqual(bundle.record.session_fact_meta[fact.fid]["source"]["json_pointer"], "/task_contract")
        self.assertEqual(bundle.record.session_fact_meta[fact.fid]["source_events"], [])

    def test_augment_idempotent_and_non_session_input_unaffected(self):
        bundle = build_bundle(session_source())
        count = len(bundle.record.F)
        augment_record(bundle.record, bundle.source)
        self.assertEqual(len(bundle.record.F), count)
        other = deepcopy(bundle.source)
        del other["session"]
        old = build_bundle(other)
        self.assertFalse(getattr(old.record, "session_augmented", False))
        self.assertFalse(build_artifact(old)["available"])

    def test_inconsistent_display_sidecar_is_rejected(self):
        source = session_source()
        source["session"]["events"][-1]["text"] = "Revenue: 99"
        with self.assertRaises(ValueError):
            build_bundle(source)
        source = session_source()
        source["session"]["events"][2]["span_id"] = "someone-elses-call"
        with self.assertRaises(ValueError):
            build_bundle(source)

    def test_numeric_format_equivalence_and_quoted_counterexample_not_false_error(self):
        bundle = build_bundle(session_source(final="Revenue: 42.0"))
        self.assertEqual(next(c for c in session_checks(bundle) if c["rule_id"] == "OC-SESSION-ANSWER-001")["state"], "passed")
        bundle = build_bundle(session_source(final="This is an incorrect example, not the actual output:\n```\nRevenue: 99\n```"))
        self.assertEqual(next(c for c in session_checks(bundle) if c["rule_id"] == "OC-SESSION-ANSWER-001")["state"], "unknown")

    def test_source_and_search_return_exact_unescaped_message_and_offsets(self):
        from prodebugger.debug_tools import DebugTools
        from prodebugger.query_dsl import execute
        source = session_source(final="The recorded values are:\n```\nRevenue: 99\n```")
        tools = DebugTools(build_bundle(source))
        final = next(f for f in tools.index.facts.values() if f.rel == "session_final_answer")
        sql = "-- Purpose: inspect a recorded final answer without reconstructing memory.\nSELECT * FROM SOURCE(" + str(final.fid) + ",'Revenue: 99')"
        result = execute(tools, sql)
        self.assertNotIn("error", result)
        row = result["rows"][0]
        original = source["session"]["events"][-1]["text"]
        self.assertEqual(row["text"], original[row["start"]:row["end"]])
        self.assertEqual(row["module"], "message")
        self.assertEqual(row["source_kind"], "original_session_message")
        found = execute(tools, "-- Purpose: locate an exact answer claim.\nSELECT * FROM SEARCH_TEXT('Revenue: 99','message')")
        self.assertNotIn("error", found)
        self.assertEqual([r["step"] for r in found["rows"]], [final.t])

    def test_route_and_manager_inventory_have_public_messages_and_facts(self):
        from types import SimpleNamespace
        from prodebugger.debug_tools import DebugTools
        from prodebugger.debugger_agent import DebuggerAgent
        from prodebugger.trace_manager import sources
        bundle = build_bundle(session_source())
        tools = DebugTools(bundle)
        inventory = sources(tools)
        self.assertEqual(inventory["source:5:message"]["text"], bundle.source["session"]["events"][-1]["text"])
        timeline = DebuggerAgent._route_timeline(SimpleNamespace(bundle=bundle, tools=tools))
        self.assertTrue(timeline["steps"][-1]["module_fids"]["message"])
        self.assertEqual(timeline["steps"][-1]["recorded_message"]["role"], "assistant")
        self.assertIn("not an original Agent reasoning step", timeline["instruction"])

    def test_trace_can_assess_real_final_message_but_not_user_message(self):
        from prodebugger.debug_tools import DebugTools
        from prodebugger.window_trace import normalize_candidate, schema_for
        from prodebugger.evidence_tools import ToolError
        bundle = build_bundle(session_source(final="Revenue: 99"))
        tools = DebugTools(bundle)
        candidate = {"status": "error", "is_root_cause": None, "step": 5, "module": "message",
            "fault_class": "statement_mismatch", "output_quote": "Revenue: 99", "proposition": "The stated amount conflicts with the read output.",
            "reasoning": "The read returned Revenue: 42, but the final answer quotes Revenue: 99.",
            "immediate_effect": "The user receives an incorrect value.", "uncertainty": "The internal cause remains unknown.", "requirement_links": []}
        normalized = normalize_candidate(tools, candidate, {"lo": 1, "hi": 5})
        self.assertEqual(tools.index.fact(normalized["primary_fid"]).side, "mind")
        self.assertEqual(normalized["module"], "message")
        enums = schema_for(bundle.source)["function"]["parameters"]["properties"]["candidates"]["items"]["properties"]["module"]["enum"]
        self.assertIn("message", enums)
        candidate.update(step=1, output_quote=bundle.source["task"])
        with self.assertRaises(ToolError):
            normalize_candidate(tools, candidate, {"lo": 1, "hi": 5})
        legacy = schema_for("openclaw")["function"]["parameters"]["properties"]["candidates"]["items"]["properties"]["module"]["enum"]
        self.assertNotIn("message", legacy)

    def test_local_scope_does_not_leak_next_assistant_message_as_feedback(self):
        from prodebugger.evidence_tools import EvidenceIndex
        bundle = build_bundle(session_source())
        local = EvidenceIndex(bundle).scoped_record({"mode": "local", "step": 4})
        self.assertNotIn(5, local.source_steps)
        self.assertFalse(any(f.t == 5 for f in local.F))

    def test_query_trace_submit_keep_message_fault_and_unknown_root_separate(self):
        from prodebugger.debug_tools import DebugTools
        from prodebugger.query_trace import query, commit
        from prodebugger.direct_submit import packet, normalize
        bundle = build_bundle(session_source(final="Revenue: 99", contract=True))
        tools = DebugTools(bundle)
        final = next(f for f in tools.index.facts.values() if f.rel == "session_final_answer")
        case = {"candidate_id": "message-review", "issue_step": final.t, "issue_fids": [final.fid],
                "goal_links": [], "hypothesis": "Compare the public answer with the recorded output."}
        tools.windows.configure([case])
        tools.route_cases = [case]
        tools.current_trace_case = case["candidate_id"]
        output = next(f for f in tools.index.facts.values() if f.rel == "tool_result")
        retrieved = query(tools, case["candidate_id"], "-- Purpose: inspect recorded answer and return evidence.\nSELECT * FROM SOURCE(" + str(final.fid) + ",'Revenue: 99')\nUNION ALL\nSELECT * FROM SOURCE(" + str(output.fid) + ",'Revenue: 42')")
        self.assertNotIn("error", retrieved)
        assessment = {"status": "error", "is_root_cause": None, "step": final.t, "module": "message",
            "fault_class": "statement_mismatch", "output_quote": "Revenue: 99", "proposition": "The answer reports a different number.",
            "reasoning": "The recorded return says Revenue: 42 but the public answer quotes 99.", "immediate_effect": "Incorrect number in the user-facing answer.",
            "uncertainty": "Internal reason and full task outcome remain unknown.", "requirement_links": []}
        commit(tools, {"window_id": case["candidate_id"], "candidates": [assessment], "uncertainty": "No verified internal cause."}, "test-assess")
        submitted = packet(tools)
        self.assertEqual(submitted["local_context"]["local:5"]["module_text"]["message"], "Revenue: 99")
        self.assertIsNone(submitted["local_context"]["local:5"]["next_env_text"])
        self.assertEqual(submitted["task_outcome"]["status"], "unknown")
        candidate_id = submitted["candidates"][0]["candidate_id"]
        result = normalize(tools, {"status": "uncertain", "selected_candidate_id": None, "fault_class": None,
            "output_quote": None, "reasoning": "The local public-answer fault is retained; full task outcome is not established.",
            "comparisons": [{"candidate_id": candidate_id, "reasoning": "Recorded final message is a local error with an unresolved internal cause."}],
            "repair": None, "uncertainty": "Tool output alone cannot establish full task outcome."})
        self.assertIsNone(result["root"])
        self.assertEqual(result["alternatives"][0]["module"], "message")


if __name__ == "__main__":
    unittest.main()
