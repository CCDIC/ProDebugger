import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from checks.test_query_backend import (
    answer, call, case_for, manager_answer, source, submit_answer,
)
from prodebugger.debugger_agent import DebuggerAgent
from prodebugger.debug_tools import DebugTools
from prodebugger.evidence_tools import ToolError
from prodebugger.llm_client import ReplayClient, json_lines
from prodebugger.record_bridge import build_bundle, plain, write_json
from prodebugger.run_experiment import DEFAULTS


class ProtocolErrorChecks(unittest.TestCase):
    def setUp(self):
        self.bundle = build_bundle(source())

    def agent(self, output, messages=()):
        write_json(output / "replay.json", list(messages) or [{"role": "assistant", "content": "Unused replay message."}])
        return DebuggerAgent(self.bundle, ReplayClient(output / "replay.json", output), output, DEFAULTS)

    def dispatch(self, agent, cid, name, arguments):
        agent._dispatch(call(cid, name, arguments)["tool_calls"][0])
        return json.loads(agent.messages[-1]["content"])

    def routed_agent(self, output):
        agent = self.agent(output)
        self.dispatch(agent, "map", "view", {"spec": "requirements", "scope": {"mode": "global"}})
        self.dispatch(agent, "route", "route_cases", {"candidates": [case_for(agent.tools)]})
        return agent

    def unchanged(self, tools):
        return plain({"phase": tools.phase, "windows": tools.windows.windows,
            "version": tools.windows.version, "manager_plan": tools.manager_plan,
            "queries": tools.trace_queries, "conclusions": tools.window_conclusions,
            "result": tools.result})

    def test_malformed_manager_json_is_journaled_once_without_executing_or_repairing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            agent = self.routed_agent(output)
            before, count = self.unchanged(agent.tools), agent.tools.call_count
            raw = json.dumps(manager_answer()) + "}"
            malformed = call("malformed", "plan_trace", manager_answer())["tool_calls"][0]
            malformed["function"]["arguments"] = raw
            handler = Mock(side_effect=AssertionError("malformed arguments reached the handler"))
            with patch.dict(agent.tools.handlers, {"plan_trace": handler}):
                agent._dispatch(malformed)
                first = json.loads(agent.messages[-1]["content"])
                agent._dispatch(malformed)
                self.assertEqual(json.loads(agent.messages[-1]["content"]), first)
            handler.assert_not_called()
            self.assertEqual(self.unchanged(agent.tools), before)
            self.assertEqual(agent.tools.call_count, count + 1)
            error = first["error"]
            self.assertEqual(error["code"], "invalid_json")
            self.assertEqual(error["json_error"]["position"], len(raw) - 1)
            self.assertIn("column", error["message"])
            self.assertIn("one valid JSON object", error["instruction"])
            self.assertIn("not executed", error["instruction"])
            context = error["context"]
            self.assertEqual(context["text"], raw[context["start"]:context["end"]])
            events = [e for e in json_lines(output / "tool_calls.jsonl") if e["tool_call_id"] == "malformed"]
            self.assertEqual([e["event"] for e in events], ["pending", "committed"])
            self.assertTrue(all(e["arguments"] == raw and e["argument_error"] == {
                k: v for k, v in error.items() if k != "retryable"} for e in events))
            with self.assertRaisesRegex(ToolError, "reused with different arguments"):
                self.dispatch(agent, "malformed", "plan_trace", manager_answer())
            self.assertEqual(agent.tools.call_count, count + 1)
            fixed = self.dispatch(agent, "corrected", "plan_trace", manager_answer())
            self.assertNotIn("error", fixed)
            self.assertIsNotNone(agent.tools.manager_plan)
            self.assertEqual(agent.tools.call_count, count + 2)

    def test_nearly_valid_finish_json_is_never_automatically_repaired(self):
        valid = json.dumps({"status": "uncertain", "reason": "Evidence is insufficient."})
        for raw in (valid + "}", valid[:-1], "```json\n" + valid + "\n```"):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                agent = self.agent(Path(directory))
                malformed = call("bad", "finish", {})["tool_calls"][0]
                malformed["function"]["arguments"] = raw
                handler = Mock(side_effect=AssertionError("finish ran"))
                with patch.dict(agent.tools.handlers, {"finish": handler}):
                    agent._dispatch(malformed)
                handler.assert_not_called()
                self.assertIsNone(agent.tools.result)
                self.assertEqual(agent.tools.call_count, 1)
                self.assertEqual(json.loads(agent.messages[-1]["content"])["error"]["code"], "invalid_json")

    def test_already_decoded_or_null_arguments_are_rejected_without_side_effects(self):
        for raw in ({"status": "uncertain", "reason": "Evidence is insufficient."}, None):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                agent = self.agent(Path(directory))
                malformed = call("bad", "finish", {})["tool_calls"][0]
                malformed["function"]["arguments"] = raw
                handler = Mock(side_effect=AssertionError("non-string arguments executed"))
                with patch.dict(agent.tools.handlers, {"finish": handler}):
                    agent._dispatch(malformed)
                handler.assert_not_called()
                error = json.loads(agent.messages[-1]["content"])["error"]
                self.assertEqual(error["code"], "invalid_json")
                self.assertIn(type(raw).__name__, error["message"])
                self.assertIn("JSON string", error["message"])
                self.assertIsNone(agent.tools.result)
                self.assertEqual(agent.tools.events[-1]["arguments"], raw)

    def test_missing_and_unexpected_nested_fields_are_named_and_correctable(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = self.routed_agent(Path(directory))
            before = self.unchanged(agent.tools)
            malformed = manager_answer()
            task = malformed["assignments"][0]
            task["question_typo"] = task.pop("question")
            error = self.dispatch(agent, "bad-fields", "plan_trace", malformed)["error"]
            self.assertEqual(error["code"], "invalid_arguments")
            self.assertIn("arguments.assignments[0]", error["message"])
            self.assertIn("missing required fields: question", error["message"])
            self.assertIn("unexpected fields: question_typo", error["message"])
            self.assertEqual(self.unchanged(agent.tools), before)
            self.assertNotIn("error", self.dispatch(agent, "fixed-fields", "plan_trace", manager_answer()))

    def test_manager_quote_error_identifies_exact_assignment_source_and_evidence(self):
        for kind in ("discontinuous", "unavailable", "too_long"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                agent = self.routed_agent(Path(directory))
                before = self.unchanged(agent.tools)
                plan = manager_answer(("origin", "verification"))
                evidence = {"source_id": "source:3:plan", "quote": "Inspect countertop 1 decision 3."}
                if kind == "unavailable":
                    evidence = {"source_id": "source:1:memory", "quote": "I hold apple 1."}
                elif kind == "too_long":
                    evidence["quote"] = "x" * 361
                plan["assignments"][1]["evidence"].append(evidence)
                error = self.dispatch(agent, "bad-quote", "plan_trace", plan)["error"]
                self.assertEqual(error["code"], "invalid_manager_plan")
                self.assertIn("arguments.assignments[1].evidence[1]", error["message"])
                self.assertIn("agent_id='verification'", error["message"])
                self.assertIn("source_id=" + repr(evidence["source_id"]), error["message"])
                self.assertIn({"discontinuous": "exact contiguous substring", "unavailable": "supplied source_inventory",
                    "too_long": "outside 1..360"}[kind], error["message"])
                self.assertEqual(self.unchanged(agent.tools), before)
                plan["assignments"][1]["evidence"][-1] = {
                    "source_id": "source:3:plan", "quote": "Inspect countertop 1 at decision 3."}
                self.assertNotIn("error", self.dispatch(agent, "fixed-quote", "plan_trace", plan))

    def test_malformed_event_replay_preserves_error_count_digest_and_no_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            agent = self.agent(output)
            malformed = call("bad", "finish", {})["tool_calls"][0]
            malformed["function"]["arguments"] = '{"status":"uncertain","reason":"no evidence"}}'
            agent._dispatch(malformed)
            events = list(json_lines(output / "tool_calls.jsonl"))
            restored = DebugTools(self.bundle, output)
            handler = Mock(side_effect=AssertionError("replaying malformed input executed finish"))
            with patch.dict(restored.handlers, {"finish": handler}):
                restored.restore(events)
            handler.assert_not_called()
            self.assertEqual(restored.events, agent.tools.events)
            self.assertEqual(restored.completed, agent.tools.completed)
            self.assertEqual(restored.call_count, 1)
            self.assertIsNone(restored.result)
            self.assertEqual(list(json_lines(output / "tool_calls.jsonl")), events)

    def test_manager_can_resend_valid_json_and_resume_after_malformed_commit(self):
        for interrupt_after_error in (False, True):
            with self.subTest(interrupt_after_error=interrupt_after_error), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                tools = DebugTools(self.bundle)
                malformed = call("bad-manager", "plan_trace", manager_answer())
                malformed["tool_calls"][0]["function"]["arguments"] += "}"
                messages = [
                    call("map", "view", {"spec": "requirements", "scope": {"mode": "global"}}),
                    call("route", "route_cases", {"candidates": [case_for(tools)]}), malformed,
                    call("manager", "plan_trace", manager_answer()),
                    call("query", "query_logs", {"candidate_id": "case1", "sql": "SELECT * FROM SEARCH_TEXT('decision 1','plan')"}),
                    call("assess", "assess", answer()),
                    {"role": "assistant", "content": json.dumps(submit_answer())},
                ]
                agent = self.agent(output, messages)
                if interrupt_after_error:
                    original = agent._dispatch
                    def interrupt(tool_call):
                        original(tool_call)
                        if tool_call["id"] == "bad-manager":
                            raise KeyboardInterrupt()
                    agent._dispatch = interrupt
                    self.assertEqual(agent.run()["status"], "uncertain")
                    self.assertIsNone(agent.tools.manager_plan)
                    self.assertFalse(agent.tools.trace_queries)
                    agent = DebuggerAgent(self.bundle, ReplayClient(output / "replay.json", output), output, DEFAULTS, resume=True)
                self.assertEqual(agent.run()["status"], "diagnosed")
                self.assertEqual(agent.tools.call_count, 6)
                self.assertEqual(agent.client.cursor, 7)
                self.assertEqual(agent.client.by_phase["MANAGER"]["replay_calls"], 2)
                committed = [e for e in json_lines(output / "tool_calls.jsonl") if e["event"] == "committed"]
                self.assertEqual(sum(e["tool_call_id"] == "bad-manager" for e in committed), 1)
                requests = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((output / "requests").glob("*.json"))]
                retry = next(r for r in requests if r["phase"] == "MANAGER" and r["messages"][-1]["role"] == "user"
                    and '"invalid_json"' in r["messages"][-1]["content"])
                self.assertIn("json_error", json.loads(retry["messages"][-1]["content"])["error"])


if __name__ == "__main__":
    unittest.main()
