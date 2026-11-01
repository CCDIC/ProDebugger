"""Risk review evidence/protocol regressions. No provider requests in this suite."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from checks.test_openclaw_profile import source
from prodebugger.record_bridge import build_bundle, write_json
from prodebugger.risk_jobs import RiskReviewJobs, read
from prodebugger.risk_trace import RiskTools, commit, coverage, normalize_evidence, location, findings, checks, finding_identity
from prodebugger.risk_agent import RiskReviewAgent
from prodebugger.evidence_tools import ToolError
from prodebugger.run_experiment import DEFAULTS


POLICY = {"schema": "prodebugger-risk-policy/v1", "read_scope": {"allowed_roots": ["/workspace"]},
          "path_style": "posix", "working_directory": "/workspace",
          "tools": {"read_file": {"kind": "read", "path_arg": "path"}}}
# Contract tests need a genuinely forbidden target, not a fabricated violation
# of an allowed read. This fixture is separate from any evaluation input.
POLICY["read_scope"]["forbidden_paths"] = ["/workspace/notes.txt"]


def bundle_two():
    value = source()
    second = copy.deepcopy(value["steps"])
    for row in second:
        row["t"] += 2
        row["env_text"] = row["env_text"].replace("span-1", "span-2")
        row["agent_text"] = row["agent_text"].replace("span-1", "span-2")
        if row["parts"]["action"]:
            row["parts"]["action"] = row["parts"]["action"].replace("span-1", "span-2")
    value["steps"] += second
    return build_bundle(value, online=False)


def setup_tools(bundle):
    tools = RiskTools(bundle, risk_policy=POLICY)
    action = next(f for f in bundle.record.F if f.t == 1 and f.rel == "tool_call")
    case = {"candidate_id": "q1", "issue_step": 1, "issue_fids": [action.fid], "goal_links": [], "hypothesis": "Does read respect explicit scope?"}
    tools.windows.configure([case]); tools.route_cases = tools.windows.windows
    tools.current_trace_case = "q1"
    tools.trace_queries["q1"] = {"rows": [], "returned_steps": []}
    tools.phase = "TRACE"
    return tools, action


def answer(fid, *, step=1, missing=None):
    return {"window_id": "q1", "findings": [{"risk_type": "read_scope", "status": "confirmed",
        "import_step": step, "module": "action", "policy_ids": ["read_scope"], "explanation": "Explicit scope must be checked.",
        "evidence": [{"step": step, "module": "action", "quote": "notes.txt", "fact_ids": [fid]}],
        "counterevidence": [], "missing": missing or [], "cause_hypotheses": []}],
        "checks": [], "uncertainty": "Internal reasoning and full history are unknown."}


class RiskContractChecks(unittest.TestCase):
    def test_exact_call_identity_does_not_spread_to_repeated_tool(self):
        tools, action = setup_tools(bundle_two())
        value = commit(tools, answer(action.fid), "call-a")
        self.assertEqual(len(value["findings"]), 1)
        finding = value["findings"][0]
        self.assertEqual(finding["span_id"], "span-1")
        self.assertEqual(finding["import_step"], 1)
        self.assertEqual(finding["primary_fids"], [action.fid])
        self.assertIn(3, coverage(tools)["unassessed_steps"])

    def test_native_result_steps_map_to_the_exact_repeated_invocation(self):
        value = bundle_two().source
        value["telemetry"] = {"source_map": [
            {"trace_id": "trace-1", "span_id": "span-1", "tool_call_id": "native-one", "action_step": 1, "result_step": 2},
            {"trace_id": "trace-1", "span_id": "span-2", "tool_call_id": "native-two", "action_step": 3, "result_step": 4}]}
        value["session"] = {"schema": "openclaw-session/v1", "events": [
            {"step": 2, "type": "tool_result", "call_id": "native-one", "event_id": "e2", "original_seq": 8},
            {"step": 4, "type": "tool_result", "call_id": "native-two", "event_id": "e4", "original_seq": 12}]}
        first, second = location(value, 2, "observation"), location(value, 4, "observation")
        self.assertEqual(first["call_id"], "trace-1/span-1")
        self.assertEqual(second["call_id"], "trace-1/span-2")
        self.assertEqual(second["native_tool_call_id"], "native-two")
        self.assertEqual(second["source_mapping"]["action_step"], 3)
        self.assertEqual(second["original_seq"], 12)
        self.assertEqual(second["event_id"], "e4")

    def test_same_quote_elsewhere_is_not_visible_evidence(self):
        bundle = bundle_two(); tools, action = setup_tools(bundle)
        second = next(f for f in bundle.record.F if f.t == 3 and f.rel == "tool_call")
        with self.assertRaisesRegex(ToolError, "SAME step/module"):
            commit(tools, answer(second.fid, step=3), "call-a")

    def test_wrong_same_step_fact_module_is_rejected(self):
        tools, action = setup_tools(bundle_two())
        wrong = next(f for f in tools.bundle.record.F if f.t == 1 and f.slot == "obs")
        with self.assertRaisesRegex(ToolError, "source module"):
            commit(tools, answer(wrong.fid), "call-a")

    def test_wrong_route_anchor_reports_the_exact_fact_step(self):
        tools, action = setup_tools(bundle_two())
        tools.goal_map = {"requirements": []}
        with self.assertRaisesRegex(ToolError, "Fact 0 belongs to step 0, not issue_step 1"):
            tools._trace_window([{"candidate_id": "wrong", "issue_step": 1, "issue_fids": [0],
                                  "goal_links": [], "hypothesis": "check authorization"}])

    def test_missing_prerequisite_rejects_confirmation_without_silent_rewrite(self):
        tools, action = setup_tools(bundle_two())
        with self.assertRaisesRegex(ToolError, "confirmed proposal"):
            commit(tools, answer(action.fid, missing=["No complete approval record"]), "call-a")
        self.assertEqual(tools.risk_reviews, {})

    def test_query_evidence_can_locate_multiple_steps_without_one_root_gate(self):
        bundle = bundle_two(); tools, action = setup_tools(bundle)
        second = next(f for f in bundle.record.F if f.t == 3 and f.rel == "tool_call")
        tools.trace_queries.clear()
        tools._query_with_source_refs("q1", "SELECT * FROM SEARCH_TEXT('notes.txt','action') WHERE step=3")
        data = answer(action.fid)
        data["findings"].extend(answer(second.fid, step=3)["findings"])
        result = commit(tools, data, "call-a")
        self.assertEqual({f["span_id"] for f in result["findings"]}, {"span-1", "span-2"})
        self.assertNotIn("root", result)

    def test_same_risk_different_policy_references_is_counted_once(self):
        tools, action = setup_tools(bundle_two())
        commit(tools, answer(action.fid), "call-one")
        first = {k: v for k, v in tools.windows.windows[0].items() if k in {"candidate_id", "issue_step", "issue_fids", "goal_links", "hypothesis"}}
        second = copy.deepcopy(first); second["candidate_id"] = "q2"
        tools.windows.configure([first, second])
        tools.current_trace_case = "q2"
        tools.trace_queries["q2"] = {"rows": [], "returned_steps": []}
        data = answer(action.fid); data["window_id"] = "q2"
        data["findings"][0]["policy_ids"] = ["tool:read_file"]
        commit(tools, data, "call-two")
        merged = findings(tools)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["policy_ids"], ["read_scope", "tool:read_file"])
        self.assertEqual(merged[0]["investigator_ids"], ["q1", "q2"])
        self.assertEqual(len(merged[0]["investigator_explanations"]), 2)
        tools.risk_reviews["q2"]["findings"][0]["status"] = "suspected"
        self.assertEqual(findings(tools)[0]["status"], "insufficient_evidence")

    def test_same_checked_pair_is_deduplicated_and_disagreement_stays_unknown(self):
        tools, _ = setup_tools(bundle_two())
        one = {"policy_id": "read_scope", "step": 1, "status": "pass", "reason": "Scoped read", "missing": [], "evidence": [], "investigator_id": "q1"}
        two = {**one, "investigator_id": "q2", "status": "unknown", "missing": ["unresolved path"]}
        tools.risk_reviews = {"q1": {"checks": [one], "findings": []}, "q2": {"checks": [two], "findings": []}}
        result = checks(tools)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "unknown")
        self.assertEqual(result[0]["investigator_ids"], ["q1", "q2"])

    def test_query_catalog_maps_null_fids_without_new_evidence_or_more_queries(self):
        from prodebugger.query_trace import query
        tools, _ = setup_tools(bundle_two()); tools.trace_queries.clear()
        original, _ = setup_tools(bundle_two()); original.trace_queries.clear()
        sql = "-- Purpose: inspect matching result excerpts\nSELECT * FROM SEARCH_TEXT('hello','observation')"
        before = [(f.fid, f.t, f.rel) for f in tools.bundle.record.F]
        baseline = query(original, "q1", sql)
        result = tools._query_with_source_refs("q1", sql)
        added = {"reference_catalog", "verified_source_fragments", "source_provenance",
                 "retrieval_status", "query_attempt", "returned_row_count", "query_retry"}
        self.assertEqual({k:v for k,v in result.items() if not k.startswith("source_fact_catalog") and k not in added}, baseline)
        self.assertEqual(result["retrieval_status"], "succeeded")
        self.assertEqual(result["query_attempt"], 1)
        self.assertEqual(result["returned_row_count"], len(result["rows"]))
        self.assertFalse(result["query_retry"]["allowed"])
        self.assertTrue(all(row["fid"] is None for row in result["rows"]))
        catalog = result["source_fact_catalog"]
        self.assertEqual([(r["step"], r["module"]) for r in catalog], [(2, "observation"), (4, "observation")])
        self.assertTrue(set(f["fid"] for f in catalog[0]["facts"]).isdisjoint(f["fid"] for f in catalog[1]["facts"]))
        for row in catalog:
            for ref in row["facts"]:
                self.assertEqual(set(ref), {"fid", "rel", "slot", "side"})
                self.assertEqual(tools.index.fact(ref["fid"]).t, row["step"])
        self.assertEqual([(f.fid, f.t, f.rel) for f in tools.bundle.record.F], before)
        with self.assertRaisesRegex(ToolError, "exactly one query"):
            tools._query_with_source_refs("q1", sql)

    def test_query_catalog_only_covers_returned_pairs_and_empty_invalid_query_can_be_corrected(self):
        tools, _ = setup_tools(bundle_two()); tools.trace_queries.clear()
        sql = "-- Purpose: inspect the second result\nSELECT * FROM SEARCH_TEXT('hello','observation') WHERE step = 4"
        result = tools._query_with_source_refs("q1", sql)
        self.assertEqual([r["step"] for r in result["source_fact_catalog"]], [4])
        bad, _ = setup_tools(bundle_two()); bad.trace_queries.clear()
        result = bad._query_with_source_refs("q1", "-- Purpose: invalid discovery\nSELECT * FROM UNKNOWN_OPERATOR('hello')")
        self.assertEqual(result["source_fact_catalog"], [])
        self.assertIn("error", result)
        self.assertTrue(result["query_retry"]["allowed"])
        self.assertIsNone(result["returned_row_count"])
        corrected = bad._query_with_source_refs("q1", sql)
        self.assertEqual(corrected["retrieval_status"], "succeeded")
        self.assertEqual(corrected["query_attempt"], 2)
        self.assertEqual([r["step"] for r in corrected["source_fact_catalog"]], [4])
        self.assertEqual(len(bad.risk_query_attempts["q1"]), 2)
        self.assertIn("error", bad.risk_query_attempts["q1"][0])
        with self.assertRaisesRegex(ToolError, "exactly one query"):
            bad._query_with_source_refs("q1", sql)

    def test_same_call_action_and_result_are_one_risk_with_both_locations(self):
        tools, action = setup_tools(bundle_two()); tools.trace_queries.clear()
        tools._query_with_source_refs("q1", "-- Purpose: read exact result snippets\nSELECT * FROM SEARCH_TEXT('hello','observation')")
        obs = next(f for f in tools.bundle.record.F if f.t == 2 and f.rel == "tool_result")
        second = next(f for f in tools.bundle.record.F if f.t == 4 and f.rel == "tool_result")
        data = answer(action.fid)
        first = copy.deepcopy(data["findings"][0])
        first.update(import_step=2, module="observation", evidence=[{"step":2,"module":"observation","quote":"hello","fact_ids":[obs.fid]}])
        other = copy.deepcopy(first)
        other.update(import_step=4,evidence=[{"step":4,"module":"observation","quote":"hello","fact_ids":[second.fid]}])
        # Result comes first; action must become the primary only when assessed.
        data["findings"] = [first, data["findings"][0], other]
        commit(tools, data, "call-merge")
        merged = findings(tools)
        self.assertEqual(len(merged), 2)
        row = next(r for r in merged if r["call_id"] == "trace-1/span-1")
        self.assertEqual((row["import_step"],row["module"]), (1,"action"))
        self.assertEqual(row["primary_fids"], [action.fid])
        self.assertEqual({(r["import_step"],r["module"]) for r in row["locations"]}, {(1,"action"),(2,"observation")})
        self.assertEqual(set(row["fact_ids"]), {action.fid,obs.fid})
        self.assertEqual(len(row["investigator_explanations"]), 2)
        self.assertEqual(coverage(tools)["assessed_steps"], [1,2,4])

    def test_unmapped_messages_keep_distinct_locations_and_trajectories(self):
        row = {"call_id": None, "import_step": 2, "module": "message", "risk_type": "other"}
        self.assertNotEqual(finding_identity(row,"one"), finding_identity({**row,"import_step":3},"one"))
        self.assertNotEqual(finding_identity(row,"one"), finding_identity(row,"two"))

    def test_unsupported_pass_is_rejected_for_explicit_correction(self):
        tools, action = setup_tools(bundle_two())
        data = {"window_id": "q1", "findings": [], "checks": [{"policy_id": "read_scope", "step": 1,
            "status": "pass", "reason": "Unverified", "evidence": [], "missing": []}], "uncertainty": "Coverage limited"}
        with self.assertRaisesRegex(ToolError, "pass/risk"):
            commit(tools, data, "call-a")

    def test_read_scope_cannot_pass_on_a_tool_response_instead_of_invocation(self):
        tools, action = setup_tools(bundle_two())
        fact = next(f for f in tools.bundle.record.F if f.t == 2 and f.rel == "tool_result")
        tools.trace_queries.clear()
        tools._query_with_source_refs("q1", "SELECT * FROM SEARCH_TEXT('hello','observation') WHERE step=2")
        data = {"window_id": "q1", "findings": [], "checks": [{"policy_id": "read_scope", "step": 2,
            "status": "pass", "reason": "Response names safe content", "evidence": [{"step": 2, "module": "observation", "quote": "hello", "fact_ids": [fact.fid]}], "missing": []}], "uncertainty": "Limited"}
        with self.assertRaisesRegex(ToolError, "actual applicable tool"):
            commit(tools, data, "call-a")


class RiskJobsChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.runner = RiskReviewJobs(self.temp.name, start_worker=False)

    def test_private_answers_are_excluded_and_old_queue_untouched(self):
        prior = Path(self.temp.name) / "diagnosis-jobs/old/job.json"
        write_json(prior, {"id": "old", "status": "running"})
        value = {**source(), "ground_truth": "PRIVATE-ANSWER", "title": "PRIVATE-TITLE", "diff": "PRIVATE-DIFF"}
        job = self.runner.enqueue("neutral-a", value, POLICY, {"findings": [{"id": "r1", "risk_type": "read_scope",
            "ground_truth": "PRIVATE-ANSWER", "diff": "PRIVATE-DIFF"}], "ground_truth": "PRIVATE-ANSWER"})
        folder = Path(job["artifact_directory"])
        payload = (folder / "input.json").read_text() + (folder / "context.json").read_text()
        self.assertNotIn("PRIVATE", payload)
        self.assertEqual(read(prior)["status"], "running")
        self.assertEqual(job["mode"], "risk_review")
        self.assertEqual([s["stage"] for s in job["stages"]], ["PARSE", "MAP", "ROUTE", "MANAGER", "TRACE", "SUBMIT"])

    def test_cancel_only_owns_its_worker(self):
        job = self.runner.enqueue("neutral-a", source(), POLICY)
        owned = object(); self.runner.processes[job["id"]] = owned
        with patch.object(self.runner, "_kill") as kill:
            self.runner.cancel(job["id"])
            kill.assert_called_once_with(owned)
        self.assertEqual(self.runner.get(job["id"])["status"], "cancelled")

    def test_model_unavailable_preserves_automatic_and_input(self):
        job = self.runner.enqueue("neutral-a", source(), POLICY, {"findings": [{"id": "rule-1", "status": "confirmed"}]})
        self.runner.configuration["credential_available"] = False
        with patch("prodebugger.risk_jobs.subprocess.Popen") as popen:
            self.runner._run(job["id"]); popen.assert_not_called()
        result = self.runner.get(job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["automatic"]["findings"][0]["id"], "rule-1")

    def test_restart_keeps_partial_findings(self):
        job = self.runner.enqueue("neutral-a", source(), POLICY)
        out = Path(job["artifact_directory"]) / "run"; out.mkdir()
        (out / "risk_trace.jsonl").write_text(json.dumps({"findings": [{"id": "partial"}]}) + "\n")
        restored = RiskReviewJobs(self.temp.name, start_worker=False).get(job["id"])
        self.assertEqual(restored["status"], "interrupted")
        self.assertEqual(restored["partial_findings"][0]["id"], "partial")
        self.assertEqual(restored["result"]["status"], "partial")
        self.assertFalse(restored["full_flow_completed"])

    def test_a_model_response_is_not_a_completed_stage(self):
        job = self.runner.enqueue("neutral-a", source(), POLICY)
        out = Path(job["artifact_directory"]) / "run"; out.mkdir()
        (out / "usage.jsonl").write_text(json.dumps({"phase": "MAP", "event": "completed", "totals": {}}) + "\n")
        current = self.runner.get(job["id"])
        self.assertNotIn("MAP", current["completed_phases"])
        (out / "phase_events.jsonl").write_text(json.dumps({"from": "MAP", "to": "ROUTE"}) + "\n")
        self.assertIn("MAP", self.runner.get(job["id"])["completed_phases"])


class ProtocolClient:
    """Protocol-only fake; never presented as a model evaluation result."""
    def __init__(self, bundle, fail=False):
        self.bundle, self.n, self.fail = bundle, 0, fail
        self.recovered_message = self.recovered_phase = None
    def snapshot(self):
        return {"model_calls": 0, "test_protocol_calls": self.n}
    def complete(self, messages, schemas, *, phase):
        self.n += 1
        def tool(name, args):
            return {"role": "assistant", "tool_calls": [{"id": "test-" + str(self.n), "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}]}
        fid = next(f.fid for f in self.bundle.record.F if f.rel == "tool_call")
        catalog = None
        for message in reversed(messages):
            try:
                data = json.loads(message.get("content") or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            nested = data.get("data", {})
            catalog = data.get("reference_catalog") or (nested.get("reference_catalog") if isinstance(nested, dict) else None)
            if catalog:
                break
        action_ref = next((r["reference_id"] for r in (catalog or {}).get("references", [])
                           if r["step"] == 1 and r["module"] == "action"), None)
        if phase == "MAP":
            return tool("view", {"spec": "requirements", "scope": {"mode": "global"}})
        if phase == "ROUTE":
            return tool("route_cases", {"candidates": [{"candidate_id": "r1", "issue_step": 1,
                "issue_fids": [fid], "goal_links": [], "hypothesis": "Is the scope established?"}]})
        if phase == "MANAGER":
            return tool("plan_trace", {"assignments": [{"agent_id": "q1", "anchor_case_id": "r1", "route_case_ids": ["r1"],
                "question": "Is reading authorized or out of scope?", "search_targets": ["notes.txt"],
                "evidence": [action_ref],
                "criteria": {"supported": "Explicit forbidden path", "refuted": "Explicit allowed path", "unknown": "Missing scope"}}],
                "decision_rules": [{"rule_id": "s1", "conditions": [{"agent_id": "q1", "outcome": "unknown", "finding": "Missing complete context"}],
                    "conclusion": "Retain uncertainty", "caveat": "Do not infer approval"}], "uncertainty": "No hidden thoughts"})
        if phase == "TRACE":
            if schemas[0]["function"]["name"] == "query_logs":
                return tool("query_logs", {"candidate_id": "q1", "sql": "-- Purpose: retrieve the recorded target\nSELECT * FROM SOURCE(" + str(fid) + ", 'notes.txt')"})
            if self.fail:
                raise ToolError("model_error", "Test transport unavailable")
            return tool("assess_risks", {"window_id": "q1", "findings": [], "checks": [{"policy_id": "read_scope", "location_id": "source:1:action",
                "status": "unknown", "reason": "Protocol-only test leaves the semantic decision unassessed", "evidence_refs": [action_ref],
                "missing": ["semantic assessment not completed"], "limitations": []}], "uncertainty": "Policy scope unresolved"})
        return {"role": "assistant", "content": json.dumps({"summary": "授权范围不完整，无法确认风险。", "finding_ids": [], "uncertainty": "缺少工作目录，不能判定安全。"})}


class FullRiskProtocolChecks(unittest.TestCase):
    def run_agent(self, fail=False, client_class=ProtocolClient):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        bundle = build_bundle(source(), online=False)
        config = {**DEFAULTS, "max_turns": 20, "max_tool_calls": 30, "window_size": 5, "trace_top_p": 4}
        agent = RiskReviewAgent(bundle, client_class(bundle, fail), Path(temp.name), config,
                                context={"policy": POLICY, "automatic": {"findings": []}})
        return agent.run(), Path(temp.name)
    def test_all_phases_with_uncertain_control_and_no_forced_root(self):
        result, folder = self.run_agent()
        self.assertEqual(result["status"], "completed", result.get("stop_reason"))
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["checks"][0]["status"], "unknown")
        self.assertTrue(result["coverage"]["trace_complete"])
        phases = [json.loads(r)["to"] for r in (folder / "phase_events.jsonl").read_text().splitlines()]
        self.assertEqual(phases, ["MAP", "ROUTE", "TRACE", "SUBMIT"])
        self.assertNotIn("root", result)
    def test_model_failure_retains_automatic_and_queries(self):
        result, _ = self.run_agent(fail=True)
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(result["queries"]), 1)
        self.assertFalse(result["coverage"]["trace_complete"])
        self.assertIn("automatic", result)

    def test_one_extra_provider_brace_is_repaired_without_field_changes(self):
        class ExtraBrace(ProtocolClient):
            def complete(self, *args, **kwargs):
                response = super().complete(*args, **kwargs)
                if kwargs["phase"] == "MANAGER":
                    response["tool_calls"][0]["function"]["arguments"] += "}"
                return response
        result, folder = self.run_agent(client_class=ExtraBrace)
        self.assertEqual(result["status"], "completed")
        receipt = json.loads((folder / "wire_repairs.jsonl").read_text().splitlines()[0])
        self.assertFalse(receipt["semantic_fields_changed"])
        self.assertTrue(receipt["raw_arguments"].endswith("}}"))

    def test_second_json_object_is_never_silently_repaired(self):
        class TwoObjects(ProtocolClient):
            def complete(self, *args, **kwargs):
                response = super().complete(*args, **kwargs)
                if kwargs["phase"] == "MANAGER":
                    response["tool_calls"][0]["function"]["arguments"] += '{"extra":"discard me"}'
                return response
        result, folder = self.run_agent(client_class=TwoObjects)
        self.assertEqual(result["status"], "error")
        self.assertFalse((folder / "wire_repairs.jsonl").exists())

    def test_prematurely_closed_object_with_remaining_fields_is_not_repaired(self):
        class BrokenFields(ProtocolClient):
            def complete(self, *args, **kwargs):
                response = super().complete(*args, **kwargs)
                if kwargs["phase"] == "TRACE" and response.get("tool_calls") and response["tool_calls"][0]["function"]["name"] == "assess_risks":
                    response["tool_calls"][0]["function"]["arguments"] = '{"window_id":"q1","findings":[]}],"checks":[],"uncertainty":"remaining"}'
                return response
        result, folder = self.run_agent(client_class=BrokenFields)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["coverage"]["trace_complete"])
        self.assertFalse((folder / "wire_repairs.jsonl").exists())

    def test_route_budget_reminder_obeys_original_json_context_contract(self):
        class RetrieveFirst(ProtocolClient):
            route_count = 0
            def complete(self, messages, schemas, **kwargs):
                # DebuggerAgent._context decodes user messages as structured JSON.
                for message in messages:
                    if message.get("role") == "user":
                        json.loads(message["content"])
                if kwargs["phase"] == "ROUTE":
                    self.route_count += 1
                    if self.route_count <= 2:
                        self.n += 1
                        fid = next(f.fid for f in self.bundle.record.F if f.rel == "tool_call")
                        return {"role": "assistant", "tool_calls": [{"id": "test-" + str(self.n), "type": "function",
                            "function": {"name": "span", "arguments": json.dumps({"fid": fid, "scope": {"mode": "global"}})}}]}
                    self.assert_route_schema(schemas)
                return super().complete(messages, schemas, **kwargs)
            @staticmethod
            def assert_route_schema(schemas):
                assert [s["function"]["name"] for s in schemas] == ["route_cases"]
        result, _ = self.run_agent(client_class=RetrieveFirst)
        self.assertEqual(result["status"], "completed", result.get("stop_reason"))

    def test_trace_schema_has_exact_current_ids(self):
        class CheckSchema(ProtocolClient):
            def complete(self, messages, schemas, **kwargs):
                if kwargs["phase"] == "TRACE" and schemas[0]["function"]["name"] == "assess_risks":
                    properties = schemas[0]["function"]["parameters"]["properties"]
                    assert properties["window_id"]["enum"] == ["q1"]
                    policies = properties["checks"]["items"]["properties"]["policy_id"]["enum"]
                    assert "read_scope" in policies
                    assert "approvals" not in policies
                return super().complete(messages, schemas, **kwargs)
        result, _ = self.run_agent(client_class=CheckSchema)
        self.assertEqual(result["status"], "completed", result.get("stop_reason"))


if __name__ == "__main__":
    unittest.main()
