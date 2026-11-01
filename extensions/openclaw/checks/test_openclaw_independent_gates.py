"""Synthetic gate checks, not new OpenClaw runs or diagnostic labels."""
import tempfile
import unittest
from pathlib import Path

from checks.test_openclaw_capture_audit import write_run
from prodebugger import openclaw_capture_audit as audit
from prodebugger.debug_tools import DebugTools
from prodebugger.openclaw_contract import evaluate
from prodebugger.otel_import import convert, load_export, spans_from_export
from prodebugger.record_bridge import build_bundle


class IndependentGatesChecks(unittest.TestCase):
    def test_capture_health_and_contract_scope_are_independent(self):
        for scenario in ("read", "recovery", "unavailable"):
            for clean in (False, True):
                with self.subTest(scenario=scenario, clean=clean), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    write_run(root, scenario=scenario,
                              shutdown="graceful_signal" if clean else "nonzero_exit",
                              gateway_exit_code=0 if clean else 1)
                    result = audit.audit_capture(root / "manifest.json")
                    folder = root / "capture-audit/cases/case-01"
                    task = load_export(folder / "task.json")
                    source, _ = convert(spans_from_export(load_export(folder / "trace.json")), task)
                    self.assertEqual(task["capture"]["complete"], clean)
                    self.assertEqual(task["contract"]["complete"], scenario == "read")
                    expected = "success" if clean and scenario == "read" else "unknown"
                    self.assertEqual(evaluate(source)["status"], expected)
                    self.assertIsNone(result["cases"][0]["expected_status"])

    def test_unmodeled_profile_does_not_block_verified_explicit_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_run(root)
            audit.audit_capture(root / "manifest.json")
            folder = root / "capture-audit/cases/case-01"
            source, _ = convert(spans_from_export(load_export(folder / "trace.json")),
                                load_export(folder / "task.json"))
            tools = DebugTools(build_bundle(source, online=False))
            result = tools._finish("no_failure", "Synthetic gate check: complete explicit tool-result contract.")
            self.assertEqual(result["status"], "no_failure")


if __name__ == "__main__":
    unittest.main()
