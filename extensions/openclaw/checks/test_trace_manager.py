import json
from pathlib import Path
import tempfile
import unittest

from checks.test_query_backend import source, case_for, call, answer, submit_answer, manager_answer
from prodebugger.record_bridge import build_bundle, plain, write_json
from prodebugger.debug_tools import DebugTools
from prodebugger.debugger_agent import DebuggerAgent
from prodebugger.llm_client import ReplayClient
from prodebugger.run_experiment import DEFAULTS
from prodebugger.trace_manager import packet, commit
from prodebugger.query_trace import packet as worker_packet, query, commit as assess
from prodebugger.direct_submit import packet as submit_packet
from prodebugger.query_dsl import documentation


class ManagerChecks(unittest.TestCase):
    def setUp(self):
        self.bundle = build_bundle(source())
        self.tools = DebugTools(self.bundle)
        self.case = case_for(self.tools)
        self.tools.execute("view", {"spec": "requirements", "scope": {"mode": "global"}}, "map")
        self.tools.execute("route_cases", {"candidates": [self.case]}, "route")

    def test_manager_sees_full_action_observation_timeline_but_only_anchor_other_modules(self):
        inputs = packet(self.tools)
        present = {(e["step"], e["module"]) for e in inputs["source_inventory"]}
        self.assertTrue({(i, m) for i in (1, 2, 3) for m in ("action", "observation")} <= present)
        self.assertTrue({(3, m) for m in ("memory", "reflection", "plan")} <= present)
        self.assertNotIn((1, "memory"), present)
        self.assertNotIn((2, "plan"), present)
        self.assertFalse(inputs["source_coverage"]["all_memory_reflection_plan_supplied"])
        self.assertEqual(inputs["route_cases"][0]["candidate"]["candidate_id"], "case1")
        # Giving the planner the executable DSL does not expose earlier mental modules.
        worker = worker_packet(self.tools, self.tools.windows.windows[0])
        self.assertEqual(inputs["query_context"], worker["query_context"])
        self.assertEqual(inputs["query_language"], documentation())

    def test_one_query_expands_discovered_step_to_peer_modules_and_preserves_submit(self):
        commit(self.tools, **manager_answer())
        result = query(self.tools, "case1", """
-- Purpose: cross-check an earlier plan with its memory and reflection.
WITH claims AS (
 -- Find the decision using its own wording, without a predetermined step filter.
 SELECT step,module,text,source_kind FROM SEARCH_TEXT('decision 1','plan')
), peers AS (
 -- Retrieve related apple claims using a different literal term in peer modules.
 SELECT step,module,text,source_kind FROM SEARCH_TEXT('apple','memory')
 UNION ALL SELECT step,module,text,source_kind FROM SEARCH_TEXT('apple','reflection')
)
-- Keep peer evidence only at discovered decisions, not at every matching step.
SELECT * FROM claims
UNION ALL SELECT * FROM peers WHERE step IN (SELECT step FROM claims)
ORDER BY step,module
""")
        self.assertNotIn("error", result)
        self.assertEqual({(r["step"], r["module"]) for r in result["rows"]},
                         {(1, "plan"), (1, "memory"), (1, "reflection")})
        self.assertFalse(result["truncated"])
        verdict = answer()
        verdict["candidates"][0].update(module="reflection", output_quote="The apple is held.")
        event = assess(self.tools, verdict, "assess")
        self.assertEqual(event["candidates"][0]["module"], "reflection")
        self.assertEqual(set(event["conclusion"]), {"window_id", "candidates", "uncertainty"})
        payload = submit_packet(self.tools)
        self.assertEqual(payload["manager_guidance"]["trace_evidence"]["case1"]["rows"], result["rows"])
        self.assertEqual(payload["candidates"][0]["assessment"]["module"], "reflection")
        with self.assertRaisesRegex(ValueError, "exactly one query"):
            query(self.tools, "case1", "SELECT * FROM SEARCH_TEXT('apple','memory')")

    def test_manager_can_split_one_route_case_and_allocates_only_selected_evidence(self):
        plan = manager_answer(("origin", "verification"))
        plan["assignments"][0]["evidence"] = [{"source_id": "source:1:observation", "quote": "You pick up the apple 1"}]
        commit(self.tools, **plan)
        self.assertEqual([c["candidate_id"] for c in self.tools.windows.windows], ["origin", "verification"])
        self.assertEqual(self.tools.route_cases[0]["candidate_id"], "case1")
        worker = worker_packet(self.tools, self.tools.windows.windows[0])
        self.assertNotIn("source_inventory", worker)
        self.assertEqual(worker["manager_assignment"]["evidence"][0]["quote"], "You pick up the apple 1")
        self.assertNotIn("decision_rules", worker["manager_assignment"])
        self.assertEqual(worker["source"]["step"], 3)
        self.assertEqual(worker["query_context"]["step_range"], [1, 3])

    def test_invalid_allocation_cannot_mutate_schedule(self):
        for kind in ("invisible_source", "fabricated_quote", "unknown_agent", "duplicate_agent", "missing_unknown", "uncovered_case", "too_many_agents"):
            with self.subTest(kind=kind):
                self.setUp()
                plan = manager_answer()
                if kind == "invisible_source":
                    plan["assignments"][0]["evidence"] = [{"source_id": "source:1:memory", "quote": "I hold apple 1."}]
                elif kind == "fabricated_quote":
                    plan["assignments"][0]["evidence"][0]["quote"] = "Invented confirmation."
                elif kind == "unknown_agent":
                    plan["decision_rules"][0]["conditions"][0]["agent_id"] = "missing"
                elif kind == "duplicate_agent":
                    plan["assignments"].append(plain(plan["assignments"][0]))
                elif kind == "missing_unknown":
                    del plan["assignments"][0]["criteria"]["unknown"]
                elif kind == "uncovered_case":
                    self.tools.route_cases.append({**self.tools.route_cases[0], "candidate_id": "case2"})
                else:
                    plan = manager_answer(tuple(f"a{i}" for i in range(6)))
                before = plain(self.tools.windows.windows)
                with self.assertRaises(ValueError):
                    commit(self.tools, **plan)
                self.assertIsNone(self.tools.manager_plan)
                self.assertEqual(self.tools.windows.windows, before)

    def test_manager_can_merge_cases_without_losing_original_route_evidence(self):
        fid = next(f.fid for f in self.tools.index.facts.values() if f.t == 2 and f.slot == "mem")
        other = {**self.case, "candidate_id": "case2", "issue_step": 2, "issue_fids": [fid]}
        self.tools.phase = "ROUTE"
        self.tools.execute("route_cases", {"candidates": [self.case, other]}, "route2")
        inputs = packet(self.tools)
        self.assertEqual(len(inputs["route_cases"]), 2)
        self.assertIn("source:2:memory", {s["source_id"] for s in inputs["source_inventory"]})
        plan = manager_answer()
        plan["assignments"][0]["route_case_ids"].append("case2")
        plan["assignments"][0]["evidence"].append({"source_id": "source:2:memory", "quote": "I hold apple 1."})
        commit(self.tools, **plan)
        self.assertEqual(len(self.tools.windows.windows), 1)
        worker = worker_packet(self.tools, self.tools.windows.windows[0])
        self.assertEqual(worker["manager_assignment"]["route_case_ids"], ["case1", "case2"])
        # The manager-allocated exact memory quote can support the one-step result,
        # even though the worker's single query tests independent observation evidence.
        query(self.tools, "case1", "SELECT * FROM SEARCH_TEXT('pick up the apple','observation')")
        verdict = answer()
        verdict["candidates"][0].update(step=2, module="memory", output_quote="I hold apple 1.")
        self.assertEqual(assess(self.tools, verdict, "assess")["candidates"][0]["step"], 2)

    def test_plan_and_raw_query_evidence_go_directly_to_submit_even_for_empty_worker(self):
        plan = manager_answer(("origin", "verification"))
        commit(self.tools, **plan)
        first = query(self.tools, "origin", "SELECT * FROM SEARCH_TEXT('decision 1','plan')")
        verdict = answer(); verdict["window_id"] = "origin"
        assess(self.tools, verdict, "origin:assess")
        second = query(self.tools, "verification", "SELECT * FROM SEARCH_TEXT('search','plan')")
        self.assertIn("error", second)
        assess(self.tools, {"window_id": "verification", "candidates": [], "uncertainty": "Invalid query; the premise is unknown."}, "verification:assess")
        payload = submit_packet(self.tools)
        guide = payload["manager_guidance"]
        self.assertEqual(guide["plan"]["decision_rules"], plan["decision_rules"])
        self.assertEqual(guide["trace_evidence"]["origin"]["rows"], first["rows"])
        self.assertIn("error", guide["trace_evidence"]["verification"])
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertNotIn("is_root_cause", payload["candidates"][0]["assessment"])
        self.assertNotIn("is_root_cause", json.dumps(guide))
        with self.assertRaisesRegex(ValueError, "immutable"):
            commit(self.tools, **plan)

    def replay(self, output):
        messages = [call("map", "view", {"spec": "requirements", "scope": {"mode": "global"}}),
            call("route", "route_cases", {"candidates": [self.case]}),
            call("manager", "plan_trace", manager_answer()),
            call("query", "query_logs", {"candidate_id": "case1", "sql": "SELECT * FROM SEARCH_TEXT('decision 1','plan')"}),
            call("assess", "assess", answer()),
            {"role": "assistant", "content": json.dumps(submit_answer())}]
        write_json(output / "replay.json", messages)
        return DebuggerAgent(self.bundle, ReplayClient(output / "replay.json", output), output, DEFAULTS)

    def test_trace_correction_tracks_whether_query_opportunity_was_consumed(self):
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                arguments = {"candidate_id": "case1"}
                if committed:
                    arguments["sql"] = "SELECT * FROM RAW_STEP(1)"
                messages = [
                    call("map", "view", {"spec": "requirements", "scope": {"mode": "global"}}),
                    call("route", "route_cases", {"candidates": [self.case]}),
                    call("manager", "plan_trace", manager_answer()),
                    call("bad", "query_logs", arguments),
                ]
                if not committed:
                    messages.append(call("query", "query_logs", {"candidate_id": "case1",
                        "sql": "SELECT * FROM SEARCH_TEXT('decision 1','plan')"}))
                messages.append(call("assess", "assess", {"window_id": "case1", "candidates": [],
                    "uncertainty": "The evidence does not establish a fault."}))
                write_json(output / "replay.json", messages)
                agent = DebuggerAgent(self.bundle, ReplayClient(output / "replay.json", output), output, DEFAULTS)
                self.assertEqual(agent.run()["status"], "no_candidate")
                self.assertEqual(len(agent.tools.trace_queries), 1)
                requests = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((output / "requests").glob("*.json"))]
                if committed:
                    # SQL errors are committed query data, not an invitation to
                    # repair the SQL. The next request offers only assessment.
                    self.assertEqual(requests[4]["messages"][-1]["role"], "tool")
                    self.assertEqual([t["function"]["name"] for t in requests[4]["tools"]], ["assess"])
                else:
                    correction = json.loads(requests[4]["messages"][-1]["content"])["instruction"]
                    self.assertIn("not been committed", correction)
                    self.assertNotIn("Retrieval is closed", correction)

    def test_durable_manager_response_and_commit_do_not_duplicate_requests_after_resume(self):
        for point in ("response", "commit"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                agent = self.replay(output)
                if point == "response":
                    original = agent.client.complete
                    def interrupt(messages, schemas, *, phase):
                        message = original(messages, schemas, phase=phase)
                        if phase == "MANAGER":
                            raise KeyboardInterrupt()
                        return message
                    agent.client.complete = interrupt
                else:
                    original = agent._dispatch
                    def interrupt(call):
                        original(call)
                        if call["function"]["name"] == "plan_trace":
                            raise KeyboardInterrupt()
                    agent._dispatch = interrupt
                self.assertEqual(agent.run()["status"], "uncertain")
                resumed = DebuggerAgent(self.bundle, ReplayClient(output / "replay.json", output), output, DEFAULTS, resume=True)
                self.assertEqual(resumed.run()["status"], "diagnosed")
                self.assertEqual(resumed.client.by_phase["MANAGER"]["replay_calls"], 1)
                self.assertEqual(resumed.client.cursor, 6)
                requests = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((output / "requests").glob("*.json"))]
                worker = json.loads(requests[3]["messages"][1]["content"])
                submitted = json.loads(requests[5]["messages"][-1]["content"])
                self.assertEqual(worker["manager_assignment"]["agent_id"], "case1")
                self.assertIn("manager_guidance", submitted)
                self.assertEqual(submitted["manager_guidance"]["plan"], resumed.tools.manager_plan)


if __name__ == "__main__":
    unittest.main()
