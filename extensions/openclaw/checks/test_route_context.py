import json
from pathlib import Path
import tempfile
import unittest

from prodebugger.route_context import compact, progress
from prodebugger.record_bridge import build_bundle, plain, write_json
from prodebugger.debugger_agent import DebuggerAgent
from prodebugger.llm_client import ReplayClient
from prodebugger.run_experiment import DEFAULTS
from checks.test_query_backend import source, call, case_for, answer, submit_answer


def batch(messages, completed, ids, *, result=None, name="view", arguments=None):
    calls = []
    for cid in ids:
        args = arguments if arguments is not None else {"spec": cid, "scope": {"mode": "global"}}
        calls.extend(call(cid, name, args)["tool_calls"])
        completed[cid] = {"phase": "ROUTE", "name": name, "arguments": args,
                          "result": result or {"data": {"step": 2, "module_text": "I checked drawer 2.", "padding": "x" * 20000}}}
    messages.append({"role": "assistant", "content": None, "tool_calls": calls})
    messages.extend({"role": "tool", "tool_call_id": cid, "content": json.dumps(completed[cid]["result"])} for cid in ids)
    messages.append({"role": "user", "content": "{}"})


class RouteContextChecks(unittest.TestCase):
    def test_whole_batch_delivered_before_any_compaction(self):
        messages, completed = [], {}
        batch(messages, completed, [f"read{i}" for i in range(6)])
        original = plain(messages)
        self.assertEqual(compact(messages, completed), original)
        batch(messages, completed, ["later"], result={"data": {"span": "A new observation."}})
        projected = compact(messages, completed)
        summaries = [json.loads(m["content"]) for m in projected if m["role"] == "tool"]
        for i, summary in enumerate(summaries[:-1]):
            self.assertTrue(summary["truncated"])
            self.assertEqual(summary["evidence_preview"]["module_text"], "I checked drawer 2.")
            self.assertEqual(summary["request"]["arguments"]["spec"], f"read{i}")
            self.assertIn("text_prefix", summary["evidence_preview"]["padding"])
        self.assertEqual(messages[:len(original)], original)
        self.assertEqual(summaries[-1], completed["later"]["result"])

    def test_duplicates_reference_retained_evidence_without_mutation(self):
        messages, completed = [], {}
        args = {"spec": "step:2", "scope": {"mode": "global"}}
        batch(messages, completed, ["first"], arguments=args)
        batch(messages, completed, ["duplicate"], arguments=args)
        original = plain(messages)
        projected = compact(messages, completed)
        responses = {m["tool_call_id"]: json.loads(m["content"]) for m in projected if m["role"] == "tool"}
        self.assertEqual(responses["first"]["same_result_as"], "duplicate")
        self.assertEqual(responses["duplicate"], completed["duplicate"]["result"])
        self.assertEqual(messages, original)

    def test_progress_counts_rounds_and_errors_not_tool_count(self):
        messages, completed = [], {}
        args = {"spec": "step:2"}
        batch(messages, completed, ["a", "b", "c"], arguments=args)
        self.assertEqual(progress(messages, completed)["consecutive_rounds_without_new_evidence"], 0)
        batch(messages, completed, ["d", "e"], arguments=args)
        batch(messages, completed, ["bad"], name="span", result={"error": {"code": "invalid_id"}})
        batch(messages, completed, ["again"], arguments=args)
        self.assertEqual(progress(messages, completed)["consecutive_rounds_without_new_evidence"], 3)
        # An incomplete pending round must not reset the accumulated history.
        messages.append(call("pending", "view", {"spec": "step:1"}))
        self.assertEqual(progress(messages, completed)["consecutive_rounds_without_new_evidence"], 3)
        messages.pop()
        batch(messages, completed, ["fresh"], result={"data": {"span": "New evidence."}})
        self.assertEqual(progress(messages, completed)["consecutive_rounds_without_new_evidence"], 0)

    def test_stalled_route_can_decide_or_stops_without_retrieval(self):
        for outcome in ("route", "ignore_gate", "invalid_decision"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                out = Path(directory)
                bundle = build_bundle(source())
                # A normal MAP read, one fresh ROUTE read and three repeated rounds.
                args = {"spec": "requirements", "scope": {"mode": "global"}}
                messages = [call("map", "view", args)] + [call(f"read{i}", "view", args) for i in range(4)]
                if outcome == "route":
                    from prodebugger.debug_tools import DebugTools
                    from checks.test_query_backend import manager_answer
                    messages += [call("route", "route_cases", {"candidates": [case_for(DebugTools(bundle))]}),
                                 call("manage", "plan_trace", manager_answer()),
                                 call("query", "query_logs", {"candidate_id": "case1", "sql": "SELECT * FROM SEARCH_TEXT('decision 1','plan')"}),
                                 call("assess", "assess", answer()),
                                 {"role": "assistant", "content": json.dumps(submit_answer())}]
                elif outcome == "ignore_gate":
                    messages.append(call("forbidden_read", "view", {"spec": "step:1", "scope": {"mode": "global"}}))
                else:
                    messages.append(call("bad_route", "route_cases", {"candidates": []}))
                write_json(out / "replay.json", messages)
                agent = DebuggerAgent(bundle, ReplayClient(out / "replay.json", out), out, DEFAULTS)
                result = agent.run()
                self.assertEqual(result["status"], "diagnosed" if outcome == "route" else "uncertain")
                decision = json.loads((out / "requests/00006.json").read_text(encoding="utf-8"))
                self.assertEqual({s["function"]["name"] for s in decision["tools"]}, {"route_cases", "finish"})
                if outcome == "route":
                    self.assertEqual(result["stop_reason"], "direct TRACE-candidate comparison completed")
                else:
                    self.assertIn("decision-only", result["stop_reason"])
                    self.assertNotIn("forbidden_read", agent.tools.completed)


if __name__ == "__main__":
    unittest.main()
