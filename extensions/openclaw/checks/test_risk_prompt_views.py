"""Evidence-preserving request views; protocol fixtures, never model evaluation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from prodebugger.record_bridge import digest
from prodebugger.risk_prompt_views import query_view, assessment_context


def catalog():
    return {"owner": "q1", "references": [
        {"reference_id": "ref-a", "step": 1, "module": "action", "quote": "abcd", "fact_ids": [2]},
        {"reference_id": "ref-b", "step": 1, "module": "action", "quote": "cdef", "fact_ids": [2]},
        {"reference_id": "ref-o", "step": 1, "module": "observation", "quote": "result", "fact_ids": [3]}],
        "locations": [{"location_id": "source:1:action", "step": 1, "module": "action"}]}


def query():
    return {"rows": [{"step": 1, "module": "action", "text": "abcdef", "quote": "abcd", "span": "cdef",
        "value": "abcdef", "rel": "tool_call", "truth": False, "status": "unsupported", "offset": 12,
        "start": 3, "end": 9, "fid": 2}], "reference_catalog": catalog(), "returned_steps": [1],
        "error": {"code": "partial_source"}, "partial": True, "truncated": True,
        "function_scopes": [{"function": "SEARCH_TEXT", "scope": "observation"}],
        "limits": {"max_rows": 10}, "source_fact_catalog_scope": "returned step/module pairs only",
        "source_fact_catalog": [{"step": 1, "module": "action", "facts": [{"fid": 2,
            "rel": "tool_call", "slot": "act", "side": "agent"}]}]}


class RiskPromptViewChecks(unittest.TestCase):
    def test_exact_text_references_preserve_structure_limits_and_original_query(self):
        original = query(); before = copy.deepcopy(original)
        result = query_view(original)
        self.assertEqual(original, before)
        self.assertEqual(result["rows"][0]["text_reference_ids"], ["ref-a", "ref-b"])
        self.assertEqual(result["rows"][0]["quote_reference_ids"], ["ref-a"])
        self.assertEqual(result["rows"][0]["span_reference_ids"], ["ref-b"])
        for field in ("value", "rel", "truth", "status", "offset", "start", "end", "fid", "step", "module"):
            self.assertEqual(result["rows"][0][field], original["rows"][0][field])
        for field in ("error", "partial", "truncated", "function_scopes", "limits", "source_fact_catalog",
                      "source_fact_catalog_scope", "returned_steps", "reference_catalog"):
            self.assertEqual(result[field], original[field])
        refs = {r["reference_id"]: r["quote"] for r in result["reference_catalog"]["references"]}
        self.assertEqual(refs["ref-a"] + refs["ref-b"][2:], original["rows"][0]["text"])
        self.assertEqual(result["model_view"]["original_query_digest"], digest(original))

    def test_partial_coverage_other_sources_and_unstructured_rows_are_unchanged(self):
        original = query()
        original["rows"] += [
            {"step": 1, "module": "action", "text": "abcdef plus unavailable"},
            {"step": 2, "module": "action", "text": "abcdef"},
            {"step": 1, "module": "message", "text": "abcdef"},
            [1, "action", "abcdef"],
            {"step": 1, "module": "action", "text": "abcabc"}]
        result = query_view(original)
        self.assertEqual(result["rows"][1:], original["rows"][1:])

    def test_query_stage_keeps_docs_and_assessment_logs_the_actual_transformed_view(self):
        with tempfile.TemporaryDirectory() as temp:
            initial_catalog = catalog()
            initial_catalog["references"] = initial_catalog["references"][:2]
            packet = {"query_language": "SQL DOCUMENTATION", "reference_catalog": initial_catalog,
                "source": {"step": 1, "observation": "result", "modules": {"action": "abcdef"},
                    "next_observation": "not retrieved and must remain"}}
            history = [{"role": "system", "content": "instructions"},
                {"role": "user", "content": json.dumps(packet)},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "query", "function": {"arguments": "original sql"}}]},
                {"role": "tool", "tool_call_id": "query", "content": json.dumps({"data": query()})},
                {"role": "assistant", "content": "a rejected proposal must remain"},
                {"role": "tool", "content": json.dumps({"error": {"code": "invalid_risk_submission"}})}]
            before = copy.deepcopy(history)
            tools = SimpleNamespace(trace_queries={}, output=Path(temp))
            self.assertEqual(assessment_context(tools, "q1", history), history)
            self.assertFalse((Path(temp) / "model_evidence_views.jsonl").exists())
            tools.trace_queries["q1"] = query()
            view = assessment_context(tools, "q1", history)
            self.assertEqual(history, before)
            self.assertEqual(tools.trace_queries["q1"], query())
            transformed = json.loads(view[1]["content"])
            self.assertNotIn("query_language", transformed)
            self.assertNotIn("reference_catalog", transformed)
            self.assertEqual(transformed["source"]["observation_reference_ids"], ["ref-o"])
            self.assertEqual(transformed["source"]["modules"]["action"], {"reference_ids": ["ref-a", "ref-b"]})
            self.assertEqual(transformed["source"]["next_observation"], "not retrieved and must remain")
            self.assertEqual(view[2], history[2])
            self.assertEqual(view[4:], history[4:])
            receipt = json.loads((Path(temp) / "model_evidence_views.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(receipt["view"], view)
            self.assertEqual(receipt["original_history_digest"], digest(history))
            self.assertEqual(receipt["model_view_digest"], digest(view))
            self.assertEqual(receipt["preserved_query_digest"], digest(query()))

    def test_initial_catalog_is_retained_if_query_catalog_is_not_a_complete_superset(self):
        initial = catalog()
        initial["references"].append({"reference_id": "ref-unreturned", "step": 1,
            "module": "message", "quote": "original evidence", "fact_ids": []})
        history = [{"role": "user", "content": json.dumps({"reference_catalog": initial,
            "query_language": "SQL documentation", "source": {"step": 1}})}]
        tools = SimpleNamespace(trace_queries={"q1": query()}, output=None)
        value = json.loads(assessment_context(tools, "q1", history)[0]["content"])
        self.assertEqual(value["reference_catalog"], initial)


if __name__ == "__main__":
    unittest.main()
