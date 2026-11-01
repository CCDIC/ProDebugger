import json
from pathlib import Path
import tempfile
import unittest

from checks.test_query_backend import source, call, case_for, answer, submit_answer, manager_answer
from prodebugger.record_bridge import build_bundle, plain, write_json
from prodebugger.debug_tools import DebugTools
from prodebugger.debugger_agent import DebuggerAgent
from prodebugger.llm_client import ReplayClient, json_lines
from prodebugger.direct_submit import normalize, packet
from prodebugger.query_trace import query, commit
from prodebugger.run_experiment import DEFAULTS


class ForcedSubmitChecks(unittest.TestCase):
    def setUp(self):
        self.bundle = build_bundle(source())
        self.tools = DebugTools(self.bundle)
        self.case = case_for(self.tools)
        self.tools.execute("view", {"spec": "requirements", "scope": {"mode": "global"}}, "map")
        self.tools.execute("route_cases", {"candidates": [self.case]}, "route")

    def test_uncertain_trace_remains_selectable_but_submit_cannot_abstain(self):
        query(self.tools, "case1", "SELECT * FROM SEARCH_TEXT('decision 1','plan')")
        commit(self.tools, answer(), "assess")
        inputs = packet(self.tools)
        self.assertEqual(inputs["schema"], "direct-submit-input/v2")
        self.assertEqual(inputs["candidates"][0]["assessment"]["status"], "uncertain")
        final = submit_answer()
        result = normalize(self.tools, final)
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result["root"]["source_trace_status"], "uncertain")
        final.update(status="uncertain", selected_candidate_id=None, fault_class=None, output_quote="", repair="")
        with self.assertRaisesRegex(ValueError, "SUBMIT status must be diagnosed"):
            normalize(self.tools, final)

    def test_multiple_trace_views_require_comparison_and_quote_identity(self):
        self.tools.windows.configure([self.case, {**self.case, "candidate_id": "case2"}])
        query(self.tools, "case1", "SELECT * FROM SEARCH_TEXT('decision 1','plan')")
        commit(self.tools, answer(), "assess1")
        query(self.tools, "case2", "SELECT * FROM SEARCH_TEXT('decision 2','plan')")
        other = answer()
        other["window_id"] = "case2"
        other["candidates"][0].update(step=2, status="error", fault_class="inefficient_plan",
                                       output_quote="Inspect countertop 1 at decision 2.")
        commit(self.tools, other, "assess2")
        final = submit_answer()
        with self.assertRaisesRegex(ValueError, "compare every TRACE suspect"):
            normalize(self.tools, final)
        final["comparisons"].append({"candidate_id": "trace-window:case2:1", "reasoning": "This may be a later consequence; error status does not prove it is the origin."})
        self.assertEqual(normalize(self.tools, final)["root"]["step"], 1)
        final["output_quote"] = other["candidates"][0]["output_quote"]
        with self.assertRaisesRegex(ValueError, "selected module's original text"):
            normalize(self.tools, final)

    def replay(self, out, assessment, final=None):
        messages = [call("map", "view", {"spec": "requirements", "scope": {"mode": "global"}}),
                    call("route", "route_cases", {"candidates": [self.case]}),
                    call("manage", "plan_trace", manager_answer()),
                    call("query", "query_logs", {"candidate_id": "case1", "sql": "SELECT * FROM SEARCH_TEXT('decision 1','plan')"}),
                    call("assess", "assess", assessment)]
        if final is not None:
            messages.append({"role": "assistant", "content": json.dumps(final)})
        write_json(out / "replay.json", messages)
        return DebuggerAgent(self.bundle, ReplayClient(out / "replay.json", out), out, DEFAULTS)

    def test_empty_trace_skips_submit_including_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            agent = self.replay(out, {"window_id": "case1", "candidates": [], "uncertainty": "No supported step."})
            self.assertEqual(agent.run()["status"], "no_candidate")
            self.assertEqual(agent.client.cursor, 5)
            self.assertEqual(agent.client.by_phase["SUBMIT"]["replay_calls"], 0)
            self.assertFalse((out / "direct_submit.jsonl").exists())
            resumed = DebuggerAgent(self.bundle, ReplayClient(out / "replay.json", out), out, DEFAULTS, resume=True)
            self.assertEqual(resumed.run()["status"], "no_candidate")
            self.assertEqual(resumed.client.cursor, 5)

    def test_invalid_submit_is_protocol_error_and_not_uncertain(self):
        for kind in ("abstention", "wrong_quote"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                final = submit_answer()
                if kind == "abstention":
                    final.update(status="uncertain", selected_candidate_id=None, fault_class=None, output_quote="", repair="")
                else:
                    final["output_quote"] = "Inspect countertop 1 at decision 2."
                out = Path(directory)
                agent = self.replay(out, answer(), final)
                result = agent.run()
                self.assertEqual(result["status"], "error")
                self.assertIsNone(result["root"])
                self.assertEqual(agent.client.by_phase["SUBMIT"]["replay_calls"], 1)
                self.assertEqual(json_lines(out / "direct_submit.jsonl"), [])

    def prepare_roles(self, specifications):
        cases = [{**self.case, "candidate_id": f"case{i}"} for i in range(1, len(specifications) + 1)]
        self.tools.windows.configure(cases)
        for i, (step, status, role) in enumerate(specifications, 1):
            cid = f"case{i}"
            query(self.tools, cid, f"SELECT * FROM SEARCH_TEXT('decision {step}.','plan')")
            assessment = answer()
            assessment["window_id"] = cid
            assessment["candidates"][0].update(step=step, status=status, is_root_cause=role,
                fault_class="inefficient_plan" if status == "error" else None,
                output_quote=f"Inspect countertop 1 at decision {step}.")
            commit(self.tools, assessment, f"assess{i}")

    def test_single_definite_nonroot_keeps_step_but_hides_role_from_submit(self):
        self.prepare_roles([(1, "error", False)])
        before = plain(self.tools.window_conclusions)
        candidate = packet(self.tools)["candidates"][0]
        self.assertEqual(candidate["step"], 1)
        self.assertEqual(candidate["assessment"]["status"], "error")
        self.assertNotIn("is_root_cause", candidate["assessment"])
        self.assertEqual(self.tools.window_conclusions, before)
        self.assertIs(before["case1"]["candidates"][0]["is_root_cause"], False)
        self.assertEqual(normalize(self.tools, submit_answer())["status"], "diagnosed")

    def test_two_definite_distinct_steps_show_roles_as_advisory_information(self):
        self.prepare_roles([(1, "error", True), (2, "error", False)])
        candidates = packet(self.tools)["candidates"]
        self.assertEqual([c["assessment"]["is_root_cause"] for c in candidates], [True, False])
        final = submit_answer()
        final["comparisons"].append({"candidate_id": "trace-window:case2:1", "reasoning": "Compared against the first source."})
        final.update(selected_candidate_id="trace-window:case2:1", output_quote="Inspect countertop 1 at decision 2.")
        self.assertEqual(normalize(self.tools, final)["root"]["step"], 2)

    def test_uncertain_or_unknown_role_plus_nonroot_hides_annotations(self):
        for status, role in [("uncertain", None), ("uncertain", True), ("uncertain", False), ("error", None)]:
            with self.subTest(status=status, role=role):
                self.setUp()
                self.prepare_roles([(1, status, role), (2, "error", False)])
                before = plain(self.tools.window_conclusions)
                self.assertTrue(all("is_root_cause" not in c["assessment"] for c in packet(self.tools)["candidates"]))
                self.assertEqual(self.tools.window_conclusions, before)

    def test_two_judgements_of_same_step_do_not_show_annotations(self):
        self.prepare_roles([(1, "error", True), (1, "error", False)])
        self.assertTrue(all("is_root_cause" not in c["assessment"] for c in packet(self.tools)["candidates"]))

    def test_third_uncertain_case_does_not_disclose_its_role(self):
        self.prepare_roles([(1, "error", True), (2, "error", False), (3, "uncertain", False)])
        candidates = packet(self.tools)["candidates"]
        self.assertIs(candidates[0]["assessment"]["is_root_cause"], True)
        self.assertIs(candidates[1]["assessment"]["is_root_cause"], False)
        self.assertNotIn("is_root_cause", candidates[2]["assessment"])

    def test_trace_role_is_required_and_boolean_or_null(self):
        query(self.tools, "case1", "SELECT * FROM SEARCH_TEXT('decision 1','plan')")
        for value in ["false", 0, 1]:
            proposal = answer()
            proposal["candidates"][0]["is_root_cause"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "true, false or null"):
                commit(self.tools, proposal, "bad")
        proposal = answer()
        proposal["candidates"][0].pop("is_root_cause")
        with self.assertRaisesRegex(ValueError, "exactly the root fields"):
            commit(self.tools, proposal, "missing")


if __name__ == "__main__":
    unittest.main()
