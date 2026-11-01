"""Queue/evidence-boundary checks; no provider requests are sent here."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prodebugger.diagnosis_jobs import DiagnosisJobs, normal_context, read, write, sha


def source():
    return {"trajectory_id": "neutral-control", "task_type": "openclaw", "task": "read A",
            "steps": [{"t": 1, "env_text": "", "agent_text": "", "admissible": [],
                       "parts": {"action": "{}"}}],
            "telemetry": {"source_map": [{"action_step": 1, "span_id": "span1"}]},
            "ground_truth": {"injected_step": 1}, "diff": "PRIVATE-ANSWER", "title": "BAD-STEP-1"}


class DiagnosisJobsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runner = DiagnosisJobs(self.temp.name, start_worker=False)

    def test_normal_input_boundary_excludes_answers_and_model_feedback(self):
        job = self.runner.enqueue("case-a", source(), {"allowed_tools": ["read"]},
              {"ground_truth": "PRIVATE-ANSWER", "issues": [
                  {"category": "model", "explanation": "old diagnosis"},
                  {"category": "rule", "id": "r1", "explanation": "input mismatch",
                   "expected_step": 1, "diff": "PRIVATE-ANSWER"}]})
        path = Path(job["artifact_directory"])
        self.assertEqual(set(read(path / "input.json")), {"trajectory_id", "task_type", "task", "steps", "telemetry"})
        context = read(path / "context.json")
        self.assertEqual(len(context["automatic_findings"]), 1)
        text = (path / "input.json").read_text() + (path / "context.json").read_text()
        for forbidden in ("ground_truth", "PRIVATE-ANSWER", "expected_step", "BAD-STEP-1", "old diagnosis"):
            self.assertNotIn(forbidden, text)

    def test_source_identity_matches_existing_report_digest(self):
        from prodebugger.record_bridge import digest
        self.assertEqual(sha(source()), digest(source()))

    def test_openclaw_local_evidence_excludes_unlocated_rules_without_crashing(self):
        from checks.test_openclaw_profile import source as structured_source
        from prodebugger.record_bridge import build_bundle
        from prodebugger.evidence_tools import EvidenceIndex
        bundle = build_bundle(structured_source(), online=False)
        missing_positions = [e for e in bundle.evaluations if e.get("t") is None]
        self.assertTrue(missing_positions, "Regression fixture needs an actually inapplicable rule family")
        index = EvidenceIndex(bundle)
        local = index.view(scope={"mode": "local", "step": 1})
        whole = index.view(scope={"mode": "global"})
        self.assertEqual(sum(local["evaluation_counts"].values()),
                         sum(type(e.get("t")) is int and e["t"] <= 1 for e in bundle.evaluations))
        self.assertEqual(sum(whole["evaluation_counts"].values()), len(bundle.evaluations))

    def test_deduplication_and_queue_cancel_do_not_start_process(self):
        one = self.runner.enqueue("case-a", source())
        two = self.runner.enqueue("case-a", source())
        self.assertEqual(one["id"], two["id"])
        with patch("prodebugger.diagnosis_jobs.subprocess.Popen") as popen:
            cancelled = self.runner.cancel(one["id"])
            self.runner._run(one["id"])
            popen.assert_not_called()
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue((Path(one["artifact_directory"]) / "STOP").exists())

    def test_restart_retains_partial_investigation_without_claiming_success(self):
        job = self.runner.enqueue("case-a", source())
        out = Path(job["artifact_directory"]) / "run"
        write(out / "result.json", {"status": "uncertain", "root": None})
        (out / "window_trace.jsonl").write_text(json.dumps({"candidates": [{"step": 1, "status": "uncertain"}]}) + "\n")
        recovered = DiagnosisJobs(self.temp.name, start_worker=False).get(job["id"])
        self.assertEqual(recovered["status"], "interrupted")
        self.assertFalse(recovered["full_flow_completed"])
        self.assertEqual(recovered["assessments"][0]["candidates"][0]["step"], 1)

    def test_missing_model_configuration_fails_without_losing_inputs(self):
        job = self.runner.enqueue("case-a", source())
        self.runner.configuration["credential_available"] = False
        with patch("prodebugger.diagnosis_jobs.subprocess.Popen") as popen:
            self.runner._run(job["id"])
            popen.assert_not_called()
        result = self.runner.get(job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["result"])
        self.assertEqual(result["source_mapping"][0]["span_id"], "span1")

    def test_full_flow_requires_four_recorded_live_completion_phases(self):
        job = self.runner.enqueue("case-a", source())
        out = Path(job["artifact_directory"]) / "run"
        write(out / "result.json", {"status": "uncertain", "root": None})
        events = [{"phase": phase, "event": "completed", "totals": {"model_calls": i + 1}}
                  for i, phase in enumerate(("MAP", "ROUTE", "TRACE"))]
        (out / "usage.jsonl").write_text("\n".join(map(json.dumps, events)) + "\n")
        self.assertFalse(self.runner.get(job["id"])["full_flow_completed"])
        events.append({"phase": "SUBMIT", "event": "completed", "totals": {"model_calls": 4}})
        (out / "usage.jsonl").write_text("\n".join(map(json.dumps, events)) + "\n")
        self.assertTrue(self.runner.get(job["id"])["full_flow_completed"])

    def test_only_owned_active_diagnosis_process_is_cancelled(self):
        job = self.runner.enqueue("case-a", source())
        own_process = object()
        self.runner.processes[job["id"]] = own_process
        with patch.object(self.runner, "_kill") as kill:
            self.runner.cancel(job["id"])
            kill.assert_called_once_with(own_process)


if __name__ == "__main__":
    unittest.main()
