"""Checks for deterministic telemetry parsing and its evidence boundaries."""
import json
import unittest
from unittest.mock import patch

from prodebugger.record_bridge import PROFILES, build_bundle
from prodebugger.parse_audit import audit, prepare, validate_facts
from profiles import openclaw


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def call(**overrides):
    return {"type": "tool_call", "trace_id": "trace-1", "span_id": "span-1",
            "tool": "read_file", "arguments": {"path": "notes.txt"}, **overrides}


def result(**overrides):
    return {"type": "tool_result", "trace_id": "trace-1", "span_id": "span-1",
            "tool": "read_file", "status": "ok", "result": {"text": "hello"}, **overrides}


def source(observation=None):
    action = encoded(call(provenance={"raw": {"tool.input": "notes.txt"}}))
    response = observation if observation is not None else encoded(result())
    return {
        "trajectory_id": "openclaw-profile-check", "task_type": "openclaw",
        "task": "Read notes.txt and verify its contents.",
        "steps": [
            {"t": 1, "env_text": encoded({"type": "tool_context", "trace_id": "trace-1"}),
             "agent_text": action, "admissible": [],
             "parts": {"memory": None, "reflection": None, "plan": None, "action": action}},
            {"t": 2, "env_text": response, "agent_text": "", "admissible": [],
             "parts": {"memory": None, "reflection": None, "plan": None, "action": None}},
        ],
    }


class OpenClawProfileChecks(unittest.TestCase):
    def test_call_parameters_and_provenance_remain_lossless(self):
        data = call(arguments={"path": "中文/notes.txt", "values": [0, False, None, ""]},
                    provenance={"spanId": "span-1", "raw": {"extra": "kept"}})
        text = encoded(data)
        fact = openclaw.parse_action(text, [])
        self.assertEqual(fact["args"], [("call", "trace-1/span-1"), ("tool", "read_file")])
        self.assertEqual(json.loads(fact["value"]), data)
        self.assertEqual(fact["span"], text)
        self.assertEqual(fact["admissibility"], "unsupported")

    def test_terminal_observation_does_not_invent_an_action_or_reasoning(self):
        self.assertIsNone(openclaw.parse_action(None, []))
        self.assertEqual(openclaw.parse_memory("I must have planned this."), [])
        self.assertIsNone(openclaw.parse_intention("I will read it.", [], []))
        with patch.dict(PROFILES, {"openclaw": openclaw}):
            bundle = build_bundle(source(), online=False)
        actions = [f for f in bundle.record.F if f.slot == "act"]
        feedback = [f for f in bundle.record.F if f.rel == "tool_result"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(feedback[0].basis, (actions[0].fid,))
        self.assertEqual(feedback[0].t, 2)
        self.assertFalse(any(f.slot in {"mem", "refl", "plan"} for f in bundle.record.F))
        self.assertTrue(all(a["status"] == "template" for a in bundle.record.parse_audits))

    def test_successful_tool_result_keeps_task_unmodeled(self):
        with patch.dict(PROFILES, {"openclaw": openclaw}):
            bundle = build_bundle(source(), online=False)
        self.assertEqual(bundle.record.goals[0].status_mode, "unmodeled")
        self.assertEqual(bundle.record.goals[0].constraints[0].expected, source()["task"])
        statuses = [f for f in bundle.record.F if f.rel == "satisfied"]
        self.assertEqual(len(statuses), 1)
        self.assertIsNone(statuses[0].value)

    def test_errors_are_preserved_without_claiming_no_side_effect(self):
        data = result(status="error", result={"partial_write": True},
                      error={"code": "EIO", "message": "write interrupted"})
        facts = openclaw.parse_observation(encoded(data), [])
        error = next(f for f in facts if f["rel"] == "tool_error")
        self.assertEqual(json.loads(error["value"]), data)
        self.assertFalse(any(f["rel"] in {"effect", "satisfied"} for f in facts))

    def test_json_provenance_is_preserved_without_requiring_a_vendor_object(self):
        for provenance in (None, [], ["raw", 1], "raw", 0, False, {"raw": [None]}):
            with self.subTest(provenance=provenance):
                action = openclaw.parse_action(encoded(call(provenance=provenance)), [])
                observation = openclaw.parse_observation(encoded(result(provenance=provenance)), [])
                self.assertEqual(json.loads(action["value"])["provenance"], provenance)
                self.assertEqual(json.loads(observation[0]["value"])["provenance"], provenance)

    def test_context_and_prior_feedback_are_linked_to_following_calls(self):
        data = source()
        next_action = encoded(call(span_id="span-2", arguments={"path": "fixed.txt"}))
        data["steps"][1]["parts"]["action"] = next_action
        data["steps"][1]["agent_text"] = next_action
        data["steps"].append({
            "t": 3, "env_text": encoded(result(span_id="span-2")), "agent_text": "",
            "admissible": [], "parts": {"memory": None, "reflection": None,
                                           "plan": None, "action": None},
        })
        with patch.dict(PROFILES, {"openclaw": openclaw}):
            bundle = build_bundle(data, online=False)
        record = bundle.record
        first, second = [f for f in record.F if f.slot == "act"]
        context = next(f for f in record.F if f.rel == "session_context")
        first_results = [f for f in record.F if f.slot == "obs" and f.t == 2]
        final_results = [f for f in record.F if f.slot == "obs" and f.t == 3]
        self.assertEqual(first.basis, (context.fid,))
        self.assertEqual(set(second.basis), {f.fid for f in first_results})
        self.assertTrue(all(f.basis == (first.fid,) for f in first_results))
        self.assertTrue(all(f.basis == (second.fid,) for f in final_results))
        self.assertTrue(record.dependence_ok())
        self.assertFalse(any(f.slot in {"mem", "refl", "plan"} for f in record.F))

    def test_missing_return_value_is_not_replaced_with_null(self):
        data = result()
        del data["result"]
        facts = openclaw.parse_observation(encoded(data), [])
        self.assertNotIn("result", json.loads(facts[0]["value"]))
        self.assertEqual(len(facts), 2)

    def test_complete_structured_schema_rejects_unknown_and_invalid_fields(self):
        cases = [call(unexpected="not silently ignored"), call(span_id=None),
                 call(tool=5), call(trace_id=""), result(status="success"),
                 {"type": "tool_result", "trace_id": "trace-1"}]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                openclaw.envelope(encoded(data))
        for text in ('{"type":"tool_context","trace_id":"a","trace_id":"b"}',
                     '{"type":"tool_context","trace_id":"a","provenance":NaN}',
                     '{"type":"tool_context","trace_id":"a","provenance":1e999}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                openclaw.envelope(text)

    def test_audit_cannot_admit_fabricated_tool_bindings(self):
        text = encoded(result())
        facts = openclaw.parse_observation(text, [])
        facts[0] = {**facts[0], "args": [("call", "another/span")]}
        with self.assertRaisesRegex(ValueError, "structured source"):
            validate_facts(facts, openclaw, text, [])
        review = audit("observation", text, [], openclaw)
        self.assertFalse(review["reasons"])

    def test_malformed_observation_is_a_gap_without_semantic_backoff(self):
        with patch.dict(PROFILES, {"openclaw": openclaw}):
            parsed = prepare(source('{"type":"tool_result"}'), online=False)
        unit = parsed["units"]["observation:2"]
        self.assertEqual(unit["status"], "unresolved")
        self.assertEqual(unit["facts"], [])
        self.assertEqual(unit["unresolved"][0]["reason"], "invalid_structured_tool_source")


if __name__ == "__main__":
    unittest.main()
