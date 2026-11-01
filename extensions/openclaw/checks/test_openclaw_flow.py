"""Offline integration through import, saved inputs, phase replay and run reports."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

from checks.test_otel_import import exported, normalized, task_spec, tool
from checks.test_query_backend import call, source as benchmark_source
from prodebugger.debugger_agent import DebuggerAgent
from prodebugger.direct_submit import OPENCLAW_POLICY, POLICY
from prodebugger.llm_client import ReplayClient, json_lines
from prodebugger.otel_import import convert, save_import
from prodebugger.record_bridge import build_bundle, read_json, saved_source, write_json
from prodebugger.run_experiment import (DEFAULTS, MIXED_POLICY, PENDING_POLICY,
    parser, run, update_manifest_policies)


def imported(outcome="failure", trajectory_id="synthetic-flow"):
    raw = tool(arguments={"path": "wrong.txt"} if outcome == "failure" else {"path": "notes.txt"})
    spec = task_spec(complete=outcome != "unknown")
    spans = normalized(exported(raw))
    source, audit = convert(spans, spec, trajectory_id=trajectory_id)
    return source, audit, spans


def replay_messages(bundle, *, empty=False, final_status="diagnosed"):
    source = bundle.source
    action = json.loads(source["steps"][0]["parts"]["action"])
    path = action["arguments"]["path"]
    fid = next(f.fid for f in bundle.record.F if f.slot == "act" and f.t == 1)
    case = {"candidate_id": "c1", "issue_step": 1, "issue_fids": [fid],
        "goal_links": ["req:task_contract"], "hypothesis": "Check the captured target and whether later feedback satisfies the task."}
    plan = {"assignments": [{"agent_id": "worker", "anchor_case_id": "c1", "route_case_ids": ["c1"],
        "question": "Does the target explain the unmet contract, or is there effective recovery?",
        "search_targets": ["Tool target and independently linked result"],
        "evidence": [{"source_id": "source:1:action", "quote": path}],
        "criteria": {"supported": "The target differs and no successful required result is recorded.",
            "refuted": "The required target has a verified matching result.",
            "unknown": "The capture cannot establish final task impact."}}],
        "decision_rules": [{"rule_id": "target-impact", "conditions": [{"agent_id": "worker",
            "outcome": "supported", "finding": "The wrong target remains causally relevant."}],
            "conclusion": "Compare a source-supported target error.", "caveat": "A later effective recovery defeats final failure attribution."}],
        "uncertainty": "Synthetic scripted protocol validation, not diagnostic accuracy."}
    candidates = [] if empty else [{"status": "error", "is_root_cause": True, "step": 1,
        "module": "action", "fault_class": "parameter_error", "output_quote": path,
        "proposition": "The invocation chooses a different file from the explicit task target.",
        "reasoning": "The original parameter is wrong and the checked complete contract remains unmet.",
        "immediate_effect": "The requested target's output was not obtained in this complete capture.",
        "uncertainty": "Scripted example; no hidden reasoning or final-answer judgement.",
        "requirement_links": ["req:task_contract"]}]
    final = {"status": final_status, "selected_candidate_id": None, "fault_class": None,
        "output_quote": None, "reasoning": "Checked task evidence and target-impact determine the outcome; recovery was considered.",
        "comparisons": [], "repair": None, "uncertainty": "Only the explicit tool-result contract is evaluated."}
    if candidates:
        final["comparisons"] = [{"candidate_id": "trace-window:worker:1", "reasoning": "The source target and complete contract support this candidate under target-impact."}]
    if final_status == "diagnosed":
        final.update(selected_candidate_id="trace-window:worker:1", fault_class="parameter_error",
                     output_quote=path, repair="Read notes.txt and verify its recorded contents.")
    return [call("map", "view", {"spec": "requirements", "scope": {"mode": "global"}}),
        call("route", "route_cases", {"candidates": [case]}), call("manager", "plan_trace", plan),
        call("query", "query_logs", {"candidate_id": "worker", "sql":
            "-- Purpose: compare tool targets, linked responses and recovery.\n"
            "SELECT step,module,text FROM SEARCH_TEXT('read_file',('action','observation')) ORDER BY step,module"}),
        call("assess", "assess", {"window_id": "worker", "candidates": candidates,
            "uncertainty": "Synthetic offline evidence boundary."}),
        {"role": "assistant", "content": json.dumps(final)}]


class OpenClawFlowChecks(unittest.TestCase):
    def test_saved_source_roundtrip_keeps_contract_and_trace_metadata_without_labels(self):
        source, audit, spans = imported()
        with tempfile.TemporaryDirectory() as directory:
            folder = save_import(source, audit, spans, directory)
            record = read_json(folder / "record.json")
            record.update(outcome={"status": "success"}, root={"step": 99}, benchmark_label="do not reuse")
            write_json(folder / "record.json", record)
            restored = saved_source(Path(directory), source["trajectory_id"])
            self.assertEqual(restored, source)
            self.assertNotIn("outcome", restored)
            self.assertNotIn("benchmark_label", restored)
            bundle = build_bundle(restored, online=False)
            self.assertEqual(bundle.source["telemetry"], source["telemetry"])
            self.assertEqual(bundle.source["task_contract"], source["task_contract"])
            self.assertEqual(len([f for f in bundle.record.F if f.slot == "act"]), 1)
            self.assertFalse(any(f.slot in {"mem", "refl", "plan"} for f in bundle.record.F))

    def test_full_agent_replay_returns_grounded_root_and_original_span_identity(self):
        source, _, _ = imported()
        bundle = build_bundle(source, online=False)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_json(output / "replay.json", replay_messages(bundle))
            agent = DebuggerAgent(bundle, ReplayClient(output / "replay.json", output), output, DEFAULTS)
            result = agent.run()
            self.assertEqual(result["schema"], "debugger-result/v1")
            self.assertEqual(result["policy"], OPENCLAW_POLICY)
            self.assertEqual(result["status"], "diagnosed")
            self.assertTrue(result["accepted"])
            self.assertEqual(result["task_outcome"]["status"], "failure")
            self.assertEqual(result["root"]["module"], "action")
            self.assertEqual(result["root"]["fault_class"], "parameter_error")
            self.assertEqual(result["root"]["source_spans"], source["telemetry"]["source_map"])
            self.assertEqual(result["usage"]["model_calls"], 0)
            self.assertEqual(result["usage"]["replay_calls"], 6)
            self.assertTrue(result["coverage"]["trace_complete"])
            self.assertEqual(read_json(output / "result.json"), result)
            submitted = read_json(output / "requests" / "00006.json")
            self.assertEqual(submitted["tools"], [])
            inputs = json.loads(submitted["messages"][-1]["content"])
            self.assertEqual(inputs["schema"], "direct-submit-input/v3-outcome-grounded")
            self.assertEqual(inputs["task_outcome"]["status"], "failure")
            self.assertIn("not the legacy", submitted["messages"][0]["content"])

    def test_empty_completed_trace_still_submits_unknown_or_success_and_resumes_without_requests(self):
        for outcome, status in (("unknown", "uncertain"), ("success", "no_failure")):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                source, _, _ = imported(outcome)
                bundle = build_bundle(source, online=False)
                output = Path(directory)
                write_json(output / "replay.json", replay_messages(bundle, empty=True, final_status=status))
                client = ReplayClient(output / "replay.json", output)
                result = DebuggerAgent(bundle, client, output, DEFAULTS).run()
                self.assertEqual(result["schema"], "debugger-result/v1")
                self.assertEqual(result["policy"], OPENCLAW_POLICY)
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["root"])
                self.assertIsNone(result["repair"])
                self.assertTrue(result["coverage"]["trace_complete"])
                self.assertEqual(client.by_phase["SUBMIT"]["replay_calls"], 1)
                self.assertEqual(len(json_lines(output / "direct_submit.jsonl")), 1)
                resumed_client = ReplayClient(output / "replay.json", output)
                restored = DebuggerAgent(bundle, resumed_client, output, DEFAULTS, resume=True).run()
                self.assertEqual(restored["status"], status)
                self.assertEqual(resumed_client.cursor, 6)
                self.assertEqual(len(json_lines(output / "direct_submit.jsonl")), 1)

    def test_run_manifest_and_report_follow_single_or_mixed_input_policies(self):
        for domains in (("openclaw",), ("alfworld",), ("openclaw", "alfworld")):
            with self.subTest(domains=domains), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                compiled = root / "compiled"
                ids, expected = [], {}
                for domain in domains:
                    if domain == "openclaw":
                        source, audit, spans = imported(trajectory_id="synthetic-openclaw")
                        save_import(source, audit, spans, compiled)
                        expected[source["trajectory_id"]] = OPENCLAW_POLICY
                    else:
                        source = benchmark_source()
                        source["trajectory_id"] = "synthetic-alfworld"
                        folder = compiled / source["trajectory_id"]
                        write_json(folder / "record.json", {k: v for k, v in source.items() if k != "steps"})
                        write_json(folder / "steps.json", source["steps"])
                        expected[source["trajectory_id"]] = POLICY
                    ids.append(source["trajectory_id"])
                sample = root / "sample.json"
                write_json(sample, ids)
                args = parser().parse_args(["--sample-file", str(sample), "--compiled-dir", str(compiled),
                    "--dry-run", "--output-dir", str(root / "runs"), "--run-id", "policy-check"])
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(run(args), 0)
                run_dir = root / "runs" / "policy-check"
                manifest = read_json(run_dir / "manifest.json")
                label = MIXED_POLICY if len(domains) > 1 else next(iter(expected.values()))
                self.assertEqual(manifest["submission_policy"], label)
                self.assertEqual(manifest["submission_policies"], expected)
                results = read_json(run_dir / "results.json")
                self.assertEqual({r["trajectory_id"]: r["policy"] for r in results}, expected)
                text = (run_dir / "report.md").read_text(encoding="utf-8")
                self.assertIn("Policy: " + label, text)
                self.assertTrue(all(policy in text for policy in set(expected.values())))
                if domains == ("openclaw",):
                    self.assertNotIn(POLICY, text)

    def test_unloaded_run_members_keep_policy_pending_until_all_are_known(self):
        manifest = {"trajectory_ids": ["legacy", "openclaw"], "sources": {
            "legacy": {"submission_policy": POLICY}}}
        update_manifest_policies(manifest)
        self.assertEqual(manifest["submission_policy"], PENDING_POLICY)
        update_manifest_policies(manifest, [{"trajectory_id": "openclaw", "policy": OPENCLAW_POLICY}])
        self.assertEqual(manifest["submission_policy"], MIXED_POLICY)
        self.assertEqual(manifest["submission_policies"], {"legacy": POLICY, "openclaw": OPENCLAW_POLICY})


if __name__ == "__main__":
    unittest.main()
