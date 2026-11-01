"""Synthetic checks for observed tool-output source metadata, not new agent runs."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from checks.test_otel_import import exported, normalized, task_spec, tool
from prodebugger.debug_tools import DebugTools
from prodebugger.openclaw_contract import evaluate
from prodebugger.otel_import import convert, save_import
from prodebugger.query_dsl import execute
from prodebugger.record_bridge import build_bundle


SOURCE_ATTRIBUTE = "openclaw.content.tool_output_source"


class OutputProvenanceChecks(unittest.TestCase):
    def test_recorded_source_is_only_on_the_response_and_error_is_preserved(self):
        spans = normalized(exported(tool(status={"code": 2}, **{SOURCE_ATTRIBUTE: "normalized_exception"})))
        original = copy.deepcopy(spans)
        source, _ = convert(spans, task_spec())
        action = json.loads(source["steps"][0]["parts"]["action"])
        feedback = json.loads(source["steps"][1]["env_text"])
        self.assertNotIn("tool_output_source", action["provenance"])
        self.assertEqual(feedback["provenance"]["tool_output_source"], "normalized_exception")
        self.assertEqual(feedback["status"], "error")
        self.assertEqual(evaluate(source)["status"], "failure")
        self.assertEqual(spans, original)

    def test_absent_source_is_not_inferred_from_error_shape_or_status(self):
        raw = tool(status={"code": 2}, result={"status": "error", "error": "File not found"})
        source, _ = convert(normalized(exported(raw)), task_spec())
        feedback = json.loads(source["steps"][1]["env_text"])
        self.assertNotIn("tool_output_source", feedback["provenance"])

    def test_other_recorded_source_values_are_preserved_without_interpretation(self):
        for value in ("future_exporter_source", "", None, {"vendor": "raw metadata"}):
            with self.subTest(value=value):
                source, _ = convert(normalized(exported(tool(**{SOURCE_ATTRIBUTE: value}))), task_spec())
                feedback = json.loads(source["steps"][1]["env_text"])
                self.assertEqual(feedback["provenance"]["tool_output_source"], value)

    def test_source_claim_does_not_fill_absent_result_or_certify_capture(self):
        raw = tool(status={"code": 2}, **{SOURCE_ATTRIBUTE: "normalized_exception"})
        del raw["attributes"]["gen_ai.tool.call.result"]
        source, audit = convert(normalized(exported(raw)), task_spec())
        feedback = json.loads(source["steps"][1]["env_text"])
        self.assertEqual(feedback["provenance"]["tool_output_source"], "normalized_exception")
        self.assertNotIn("result", feedback)
        self.assertFalse(audit["coverage"]["content_complete"])
        self.assertEqual(evaluate(source)["status"], "unknown")

    def test_origin_survives_save_compile_and_model_facing_source_query(self):
        spans = normalized(exported(tool(status={"code": 2}, **{SOURCE_ATTRIBUTE: "normalized_exception"})))
        source, audit = convert(spans, task_spec())
        bundle = build_bundle(source, online=False)
        feedback = source["steps"][1]["env_text"]
        self.assertTrue(any(f.rel == "tool_result" and f.value == feedback for f in bundle.record.F))
        queried = execute(DebugTools(bundle),
                          "SELECT step,module,start,end,text FROM SEARCH_TEXT('normalized','observation',2,2)")
        self.assertNotIn("error", queried)
        self.assertEqual([(r["step"], r["module"]) for r in queried["rows"]], [(2, "observation")])
        excerpt = queried["rows"][0]
        self.assertEqual(excerpt["text"], feedback[excerpt["start"]:excerpt["end"]])
        self.assertIn('"tool_output_source":"normalized_exception"', excerpt["text"])
        with tempfile.TemporaryDirectory() as directory:
            saved = save_import(source, audit, spans, Path(directory))
            stored = json.loads((saved / "input.json").read_text(encoding="utf-8"))
            raw = json.loads((saved / "original_spans.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, source)
            self.assertEqual(raw, [s["raw"] for s in spans])


if __name__ == "__main__":
    unittest.main()
