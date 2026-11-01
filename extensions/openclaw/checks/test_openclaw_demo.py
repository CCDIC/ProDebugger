"""Baseline wire validation must not manufacture or repair model judgements."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from checks.test_openclaw_contract import source
from prodebugger.openclaw_demo import baseline


class BaselineChecks(unittest.TestCase):
    def run_answer(self, answer):
        with tempfile.TemporaryDirectory() as directory:
            with patch("prodebugger.openclaw_demo.LLMClient") as client:
                client.return_value.complete.return_value = {"content": json.dumps(answer)}
                client.return_value.snapshot.return_value = {"model_calls": 1}
                result = baseline(source(), Path(directory) / "baseline", {})
                self.assertEqual(client.return_value.complete.call_count, 1)
                return result

    def answer(self):
        return {"status": "diagnosed", "step": 1, "module": "action",
                "fault_class": "parameter_error", "quote": "notes.txt",
                "reasoning": "A model assertion.", "uncertainty": "Not independently verified."}

    def test_bad_quote_class_and_location_are_errors_without_retry(self):
        for fields in ({"quote": "invented.txt"}, {"step": True},
                       {"step": 2}, {"fault_class": "invented"},
                       {"reasoning": []}, {"uncertainty": ""}):
            with self.subTest(fields=fields):
                result = self.run_answer({**self.answer(), **fields})
                self.assertEqual(result["status"], "error")
                self.assertIsNone(result["root"])

    def test_baseline_semantic_mistake_is_preserved_for_evaluation(self):
        # This source has verified success. Keep the model's false diagnosis so
        # the comparison can expose it; no outcome gate silently rewrites it.
        result = self.run_answer(self.answer())
        self.assertEqual(result["status"], "diagnosed")
        self.assertEqual(result["root"]["step"], 1)

    def test_abstention_requires_null_root_fields(self):
        answer = {**self.answer(), "status": "uncertain"}
        self.assertEqual(self.run_answer(answer)["status"], "error")
        answer.update(step=None, module=None, fault_class=None, quote=None)
        result = self.run_answer(answer)
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result["root"])


if __name__ == "__main__":
    unittest.main()
