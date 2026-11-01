"""Explicit output contracts distinguish task evidence from tool-span status."""
import copy
import json
import unittest

from prodebugger.openclaw_contract import SCHEMA, evaluate, validate


def contract(complete=True):
    return {"schema": SCHEMA, "complete": complete, "checks": [{
        "id": "verified-notes", "tool": "read_file",
        "argument_equals": {"/path": "notes.txt"},
        "result_equals": {"/text": "expected contents"},
    }]}


def source(attempts=None, complete_capture=True):
    attempts = attempts if attempts is not None else [{}]
    context = {"type": "tool_context", "trace_id": "trace-1"}
    actions, responses = [], []
    for number, overrides in enumerate(attempts, start=1):
        action = {"type": "tool_call", "trace_id": "trace-1", "span_id": f"span-{number}",
                  "tool": "read_file", "arguments": {"path": "notes.txt"}}
        response = {"type": "tool_result", "trace_id": "trace-1", "span_id": f"span-{number}",
                    "tool": "read_file", "status": "ok", "result": {"text": "expected contents"}}
        action.update(overrides.get("action", {}))
        response.update(overrides.get("response", {}))
        actions.append(action)
        responses.append(response)
    steps = []
    for index in range(len(actions) + 1):
        action = json.dumps(actions[index]) if index < len(actions) else None
        observation = context if index == 0 else responses[index - 1]
        steps.append({"t": index + 1, "env_text": json.dumps(observation),
                      "agent_text": action or "", "admissible": [],
                      "parts": {"memory": None, "reflection": None, "plan": None, "action": action}})
    return {"trajectory_id": "openclaw-contract-check", "task_type": "openclaw",
            "task": "Obtain the expected contents of notes.txt.", "steps": steps,
            "task_contract": contract(), "telemetry": {"coverage": {
                "declared_complete": complete_capture, "content_complete": complete_capture}}}


class OpenClawContractChecks(unittest.TestCase):
    def test_complete_matching_result_is_success_with_exact_source_evidence(self):
        data = source()
        outcome = evaluate(data)
        self.assertEqual(outcome["status"], "success")
        self.assertEqual(outcome["checks"][0]["matching_steps"], [2])
        self.assertEqual(outcome["evidence"][0]["quote"], data["steps"][1]["env_text"])
        self.assertEqual(outcome["evidence"][0]["span_id"], "span-1")

    def test_complete_capture_of_wrong_result_is_failure(self):
        outcome = evaluate(source([{"response": {"result": {"text": "wrong contents"}}}]))
        self.assertEqual(outcome["status"], "failure")
        self.assertEqual(outcome["checks"][0]["state"], "unmet")
        self.assertEqual(outcome["evidence"][0]["role"], "observed_nonmatching_result")

    def test_later_recovery_satisfies_contract_despite_earlier_error(self):
        data = source([{"response": {"status": "error", "error": "transient IO failure"}}, {}])
        outcome = evaluate(data)
        self.assertEqual(outcome["status"], "success")
        self.assertEqual(outcome["checks"][0]["considered_result_steps"], [2, 3])
        self.assertEqual(outcome["checks"][0]["matching_steps"], [3])
        self.assertEqual([e["span_id"] for e in outcome["evidence"]], ["span-2"])

    def test_partial_or_unknown_capture_cannot_establish_failure_or_task_success(self):
        for attempts in ([{}], [{"response": {"result": {"text": "wrong contents"}}}]):
            with self.subTest(attempts=attempts):
                outcome = evaluate(source(attempts, complete_capture=False))
                self.assertEqual(outcome["status"], "unknown")
                self.assertTrue(any("completeness" in limitation for limitation in outcome["limitations"]))
        for missing in ("declared_complete", "content_complete"):
            data = source()
            del data["telemetry"]["coverage"][missing]
            self.assertEqual(evaluate(data)["status"], "unknown")

    def test_no_contract_and_nonexhaustive_contract_remain_unknown(self):
        data = source()
        del data["task_contract"]
        self.assertEqual(evaluate(data)["status"], "unknown")
        data = source()
        data["task_contract"]["complete"] = False
        outcome = evaluate(data)
        self.assertEqual(outcome["status"], "unknown")
        self.assertEqual(outcome["checks"][0]["state"], "satisfied")

    def test_matching_output_for_wrong_arguments_does_not_meet_task(self):
        data = source([{"action": {"arguments": {"path": "other.txt"}}}])
        outcome = evaluate(data)
        self.assertEqual(outcome["status"], "failure")
        self.assertEqual(outcome["checks"][0]["considered_result_steps"], [])

    def test_unset_span_status_can_support_explicit_matching_output_contract(self):
        outcome = evaluate(source([{"response": {"status": "unset"}}]))
        self.assertEqual(outcome["status"], "success")

    def test_explicit_error_never_becomes_success_from_matching_payload(self):
        cases = [{"status": "error"}, {"error": {"code": "EIO"}},
                 {"result": {"text": "expected contents", "isError": True}}]
        for response in cases:
            with self.subTest(response=response):
                outcome = evaluate(source([{"response": response}]))
                self.assertEqual(outcome["status"], "failure")
                self.assertEqual(outcome["checks"][0]["matching_steps"], [])
                self.assertTrue(all(e["role"] == "observed_nonmatching_result" for e in outcome["evidence"]))

    def test_blocked_execution_never_satisfies_the_output_contract(self):
        data = source([{"response": {"provenance": {"outcome": "blocked"}}}])
        self.assertEqual(evaluate(data)["status"], "failure")

    def test_wrong_span_tool_or_temporal_order_cannot_borrow_matching_output(self):
        for response in ({"span_id": "unrelated-span"}, {"tool": "other_tool"}):
            with self.subTest(response=response):
                self.assertEqual(evaluate(source([{"response": response}]))["status"], "unknown")
        data = source()
        data["steps"][1]["t"] = 1
        self.assertEqual(evaluate(data)["status"], "unknown")

    def test_invalid_envelopes_duplicates_and_missing_terminal_response_are_unknown(self):
        for key, value in (("status", None), ("status", "success"), ("span_id", "")):
            data = source()
            response = json.loads(data["steps"][1]["env_text"])
            if value is None:
                del response[key]
            else:
                response[key] = value
            data["steps"][1]["env_text"] = json.dumps(response)
            with self.subTest(key=key, value=value):
                self.assertEqual(evaluate(data)["status"], "unknown")
        duplicate = source([{}, {}])
        for step in duplicate["steps"]:
            if step["parts"]["action"]:
                action = json.loads(step["parts"]["action"])
                action["span_id"] = "span-1"
                step["parts"]["action"] = json.dumps(action)
        self.assertEqual(evaluate(duplicate)["status"], "unknown")
        duplicate = source([{}, {}])
        duplicate["steps"][2]["env_text"] = duplicate["steps"][1]["env_text"]
        self.assertEqual(evaluate(duplicate)["status"], "unknown")
        truncated = source()
        truncated["steps"].pop()
        self.assertEqual(evaluate(truncated)["status"], "unknown")

    def test_native_error_outcome_cannot_certify_matching_output(self):
        data = source([{"response": {"provenance": {"outcome": "error"}, "status": "unset"}}])
        self.assertEqual(evaluate(data)["status"], "failure")

    def test_json_pointer_reads_arrays_escaped_keys_and_boolean_values(self):
        data = source([{"response": {"result": {"items": [{"ready": True}], "a/b": {"~key": 0}}}}])
        data["task_contract"]["checks"][0]["result_equals"] = {
            "/items/0/ready": True, "/a~1b/~0key": 0}
        self.assertEqual(evaluate(data)["status"], "success")
        data["task_contract"]["checks"][0]["result_equals"]["/items/0/ready"] = 1
        self.assertEqual(evaluate(data)["status"], "failure")
        data["task_contract"]["checks"][0]["result_equals"] = {"/items/99/ready": True}
        self.assertEqual(evaluate(data)["status"], "failure")
        data["task_contract"]["checks"][0]["result_equals"] = {"/items/00/ready": True}
        self.assertEqual(evaluate(data)["status"], "failure")

    def test_nested_json_boolean_is_not_number_one(self):
        data = source([{"response": {"result": {"items": [True]}}}])
        data["task_contract"]["checks"][0]["result_equals"] = {"/items": [1]}
        self.assertEqual(evaluate(data)["status"], "failure")

    def test_malformed_and_untrusted_precomputed_outcome_do_not_pass(self):
        data = source()
        data["outcome"] = {"status": "success"}
        data["steps"][1]["env_text"] = "malformed JSON"
        self.assertEqual(evaluate(data)["status"], "unknown")
        data = source()
        data["task_contract"]["checks"][0]["result_equals"] = {}
        self.assertEqual(evaluate(data)["status"], "unknown")

    def test_non_object_action_or_observation_is_unknown_not_an_exception(self):
        for text in ("[]", "null", '"some text"', "true", "42"):
            for field in ("action", "observation"):
                data = source()
                if field == "action":
                    data["steps"][0]["parts"]["action"] = text
                else:
                    data["steps"][1]["env_text"] = text
                with self.subTest(text=text, field=field):
                    outcome = evaluate(data)
                    self.assertEqual(outcome["status"], "unknown")
                    self.assertTrue(any("Malformed structured source" in item for item in outcome["limitations"]))

    def test_arbitrary_json_provenance_does_not_crash_output_verification(self):
        for provenance in (None, [], ["raw", 1], "raw", 0, False, {"raw": [None]}):
            with self.subTest(provenance=provenance):
                outcome = evaluate(source([{"response": {"provenance": provenance}}]))
                self.assertEqual(outcome["status"], "success")
                self.assertEqual(outcome["checks"][0]["matching_steps"], [2])

    def test_non_object_telemetry_or_coverage_cannot_assert_complete_capture(self):
        for value in (None, [], ["raw"], "raw", True, 1):
            for field in ("telemetry", "coverage"):
                data = source()
                if field == "telemetry":
                    data["telemetry"] = value
                else:
                    data["telemetry"]["coverage"] = value
                with self.subTest(value=value, field=field):
                    outcome = evaluate(data)
                    self.assertEqual(outcome["status"], "unknown")
                    self.assertTrue(any("completeness" in item for item in outcome["limitations"]))

    def test_contract_rejects_ambiguous_or_unbounded_requirements(self):
        for mutate in (
            lambda value: value.update(complete="true"),
            lambda value: value.update(checks=[]),
            lambda value: value["checks"].append(copy.deepcopy(value["checks"][0])),
            lambda value: value["checks"][0].update(argument_equals={"path": "notes.txt"}),
            lambda value: value["checks"][0].update(result_equals={"/bad~escape": True}),
        ):
            value = contract()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(value)


if __name__ == "__main__":
    unittest.main()
