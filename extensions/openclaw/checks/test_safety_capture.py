"""Integration checks for real local tool execution and imported evidence."""
import json
from pathlib import Path
import tempfile
import unittest

from prodebugger.safety_demo_capture import capture_demo, _dependencies


class SafetyCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            _dependencies()
        except RuntimeError as exc:
            raise unittest.SkipTest(str(exc)) from exc
        cls.directory = tempfile.TemporaryDirectory(prefix="prodebugger-safety-capture-")
        cls.output = Path(cls.directory.name) / "run"
        try:
            cls.manifest = capture_demo(cls.output)
        except BaseException:
            cls.directory.cleanup()
            raise
        cls.receipt = json.loads((cls.output / "runtime-receipt.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_all_actual_calls_arrive_without_agent_claim(self):
        self.assertEqual(self.manifest["received_spans"], 8)
        self.assertEqual(len({case["trace_id"] for case in self.manifest["cases"]}), 8)
        self.assertTrue(self.manifest["sdk_shutdown_ack"])
        self.assertTrue(self.manifest["not_openclaw_agent_run"])
        self.assertEqual(self.manifest["model_calls"], 0)
        self.assertEqual(len(self.receipt["sink_events"]), 2)
        self.assertTrue(all(event["body"] == "DEMO_ONLY_NOT_A_SECRET" for event in self.receipt["sink_events"]))

    def test_disabled_output_stays_absent_while_error_body_is_preserved(self):
        by_scenario = {case["scenario"]: case for case in self.manifest["cases"]}
        missing = json.loads(Path(by_scenario["missing_output"]["source_path"]).read_text(encoding="utf-8"))
        complete = json.loads(Path(by_scenario["allowed_tool_error"]["source_path"]).read_text(encoding="utf-8"))
        self.assertFalse(missing["telemetry"]["coverage"]["content_complete"])
        self.assertNotIn("result", json.loads(missing["steps"][-1]["env_text"]))
        self.assertTrue(complete["telemetry"]["coverage"]["content_complete"])
        self.assertEqual(json.loads(complete["steps"][-1]["env_text"])["result"]["error"]["code"], "ENOENT")

    def test_blocked_delete_does_not_execute_or_mutate_fixtures(self):
        blocked = next(case for case in self.receipt["cases"] if case["scenario"] == "blocked_command")
        self.assertTrue(blocked["result"]["blocked"])
        self.assertFalse(blocked["result"]["executed"])
        self.assertTrue(self.manifest["fixture_hashes_unchanged"])
        self.assertTrue((self.output / "workspace" / "public.txt").exists())

    def test_no_expected_verdicts_are_inserted_into_analysis_input(self):
        for case in self.manifest["cases"]:
            value = Path(case["source_path"]).read_text(encoding="utf-8")
            self.assertNotIn("expected_verdict", value)
            self.assertNotIn("ground_truth", value)

    def test_existing_capture_is_never_overwritten(self):
        before = (self.output / "capture-manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            capture_demo(self.output)
        self.assertEqual((self.output / "capture-manifest.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
