"""Risk source lineage regressions. Synthetic fixtures; no provider or case inputs."""
import copy
import json
import unittest
from unittest.mock import patch

from checks.test_risk_jobs import bundle_two, setup_tools
from prodebugger.record_bridge import build_bundle
from prodebugger.query_dsl import execute as original_query
from prodebugger.risk_query_provenance import execute
from prodebugger.risk_references import trace_catalog


def tools_for_query():
    tools, _ = setup_tools(bundle_two())
    tools.trace_queries.clear()
    return tools


class RiskQueryProvenanceChecks(unittest.TestCase):
    def assert_unreferenced(self, result):
        self.assertNotIn("error", result, result)
        self.assertTrue(result["rows"])
        self.assertEqual(result["verified_source_fragments"], [])
        self.assertTrue(result["source_provenance"]["unverified_fields"])

    def test_hidden_tail_literal_is_not_promoted_to_a_reference(self):
        data = bundle_two().source
        call = json.loads(data["steps"][2]["parts"]["action"])
        marker = "UNSEEN-TAIL-FIXTURE-ABCD"
        call["arguments"] = {"path": "/workspace/notes.txt", "padding": "x" * 600, "tail": marker}
        data["steps"][2]["parts"]["action"] = data["steps"][2]["agent_text"] = json.dumps(call)
        tools, _ = setup_tools(build_bundle(data, online=False)); tools.trace_queries.clear()
        before = copy.deepcopy(tools.bundle.source)
        self.assertFalse(any(r["step"] == 3 for r in trace_catalog(tools, "q1")["references"]))
        raw = original_query(tools, "SELECT * FROM TRACK_ACTION('tool_call') WHERE step=3")
        self.assertNotIn(marker, json.dumps(raw["rows"]))
        result = tools._query_with_source_refs("q1", "SELECT step,'action' AS module,'" + marker + "' AS text FROM TRACK_ACTION('tool_call') WHERE step=3")
        self.assert_unreferenced(result)
        self.assertFalse(any(r["step"] == 3 for r in result["reference_catalog"]["references"]))
        self.assertEqual(tools.bundle.source, before)

    def test_matching_literal_case_coalesce_and_concat_do_not_become_recorded_text(self):
        for expression in ("'hello'", "CASE WHEN 1=1 THEN 'hello' ELSE text END",
                           "COALESCE('hello',text)", "'hello' || SUBSTR(text,1,0)"):
            with self.subTest(expression=expression):
                result = execute(tools_for_query(), "SELECT step,module," + expression + " AS text FROM SEARCH_TEXT('hello','observation')")
                self.assert_unreferenced(result)

    def test_union_literal_branch_cannot_borrow_a_filtered_out_real_branch(self):
        sql = "SELECT step,module,'hello' AS text FROM SEARCH_TEXT('hello','observation') UNION ALL SELECT step,module,text FROM SEARCH_TEXT('hello','observation') WHERE 0"
        self.assert_unreferenced(execute(tools_for_query(), sql))

    def test_self_join_same_text_cannot_borrow_another_alias_location(self):
        sql = "WITH f AS (SELECT * FROM SEARCH_TEXT('hello','observation')) SELECT a.step,a.module,SUBSTR(b.text,INSTR(b.text,'hello'),5) AS text FROM f a JOIN f b ON 1=1 WHERE a.step=4 AND b.step=2"
        result = execute(tools_for_query(), sql)
        self.assert_unreferenced(result)
        self.assertEqual(result["rows"], [{"step": 4, "module": "observation", "text": "hello"}])

    def test_source_module_and_fact_location_cannot_be_reassigned(self):
        queries = [
            "SELECT step,'action' AS module,text FROM SEARCH_TEXT('hello','observation')",
            "SELECT a.fid,b.step,b.module,b.text FROM TRACK_ACTION('tool_call') a JOIN SEARCH_TEXT('hello','observation') b ON 1=1 WHERE a.step=3 AND b.step=2",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                self.assert_unreferenced(execute(tools_for_query(), sql))
        # Original SQL validation may reject unsupported named CTE columns;
        # either way no model-authored location becomes a source reference.
        result = execute(tools_for_query(), "WITH src AS(SELECT * FROM SEARCH_TEXT('hello','observation')), x(step,module,text) AS(SELECT 4,'observation',text FROM src WHERE step=2) SELECT * FROM x")
        self.assertFalse(result.get("verified_source_fragments"))

    def test_invalid_join_branch_cannot_borrow_a_valid_but_unreturned_union_arm(self):
        sql = "WITH src AS (SELECT * FROM SEARCH_TEXT('hello','observation')) SELECT a.step,a.module,SUBSTR(b.text,INSTR(b.text,'hello'),5) AS text FROM src a JOIN src b ON a.step=4 AND b.step=2 UNION ALL SELECT step,module,SUBSTR(text,INSTR(text,'hello'),5) AS text FROM src WHERE 0"
        result = execute(tools_for_query(), sql)
        self.assert_unreferenced(result)
        self.assertEqual(result["rows"], [{"step": 4, "module": "observation", "text": "hello"}])

    def test_intersect_and_except_keep_branch_identity_separate(self):
        prefix = "WITH src AS (SELECT * FROM SEARCH_TEXT('hello','observation')) "
        first = "SELECT a.step,a.module,SUBSTR(b.text,INSTR(b.text,'hello'),5) AS text FROM src a JOIN src b ON a.step=4 AND b.step=2"
        second = "SELECT b.step,b.module,SUBSTR(a.text,INSTR(a.text,'hello'),5) AS text FROM src a JOIN src b ON "
        for operation, condition in (("INTERSECT", "b.step=4 AND a.step=2"), ("EXCEPT", "0")):
            with self.subTest(operation=operation):
                result = execute(tools_for_query(), prefix + first + " " + operation + " " + second + condition)
                self.assert_unreferenced(result)
                self.assertEqual(result["rows"], [{"step": 4, "module": "observation", "text": "hello"}])

    def test_original_source_rows_and_query_limits_are_preserved(self):
        tools = tools_for_query()
        sql = "SELECT * FROM SEARCH_TEXT('hello','observation') WHERE step=4 LIMIT 1"
        baseline = original_query(tools, sql)
        result = execute(tools, sql)
        self.assertEqual({k:v for k,v in result.items() if k not in {"verified_source_fragments", "source_provenance"}}, baseline)
        self.assertEqual(len(result["verified_source_fragments"]), 1)
        self.assertEqual(result["verified_source_fragments"][0]["step"], 4)
        self.assertNotIn("verified_source_fragments", baseline)

    def test_direct_cte_cast_and_source_substring_remain_referenceable(self):
        queries = [
            "WITH f AS (SELECT * FROM TRACK_ACTION('tool_call')) SELECT fid,step,'action' AS module,span AS text FROM f WHERE step=3",
            "SELECT fid,step,'action' AS module,CAST(value AS TEXT) AS text FROM TRACK_ACTION('tool_call') WHERE step=3",
            "SELECT step,module,SUBSTR(text,INSTR(text,'hello'),5) AS text FROM SEARCH_TEXT('hello','observation') WHERE step=4",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                result = execute(tools_for_query(), sql)
                self.assertNotIn("error", result, result)
                self.assertEqual(len(result["verified_source_fragments"]), 1, result)

    def test_real_source_and_mixed_fact_source_unions_keep_each_location(self):
        queries = [
            "SELECT step,module,text FROM SEARCH_TEXT('hello','observation') WHERE step=2 UNION ALL SELECT step,module,text FROM SEARCH_TEXT('hello','observation') WHERE step=4",
            "SELECT step,'action' AS module,span AS text FROM TRACK_ACTION('tool_call') WHERE step=3 UNION ALL SELECT step,module,text FROM SEARCH_TEXT('hello','observation') WHERE step=4",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                result = execute(tools_for_query(), sql)
                self.assertNotIn("error", result, result)
                self.assertEqual(len(result["verified_source_fragments"]), 2, result)
                self.assertEqual({p["step"] for p in result["verified_source_fragments"]}, {r["step"] for r in result["rows"]})

    def test_independent_valid_branch_survives_failed_function_without_new_query(self):
        tools = tools_for_query()
        sql = "SELECT * FROM SEARCH_TEXT('hello','observation') UNION ALL BY NAME SELECT * FROM SOURCE(999999,'missing')"
        result = tools._query_with_source_refs("q1", sql)
        self.assertNotIn("error", result, result)
        self.assertTrue(result["partial"])
        self.assertTrue(result["function_errors"])
        self.assertTrue(result["verified_source_fragments"])
        self.assertEqual(list(tools.trace_queries), ["q1"])
        with self.assertRaisesRegex(ValueError, "exactly one query"):
            tools._query_with_source_refs("q1", sql)

    def test_missing_provenance_never_falls_back_to_arbitrary_query_strings(self):
        tools = tools_for_query()
        tools.trace_queries["q1"] = {"rows": [{"step": 3, "module": "action", "text": "notes.txt"}], "returned_steps": [3]}
        self.assertFalse(any(r["step"] == 3 for r in trace_catalog(tools, "q1")["references"]))

    def test_unverified_sql_literal_does_not_enter_the_statement_parser_side_channel(self):
        data = bundle_two().source
        marker = "Private marker remains unknown."
        data["steps"][2]["parts"]["memory"] = marker
        tools, _ = setup_tools(build_bundle(data, online=False)); tools.trace_queries.clear()
        sql = "SELECT step,module,'Private marker remains unknown.' AS text FROM SEARCH_TEXT('Private marker','memory')"
        baseline = original_query(tools, sql)
        self.assertTrue(baseline["claim_fragments"])
        with patch.object(tools.claim_parser, "enrich", wraps=tools.claim_parser.enrich) as enrich:
            result = tools._query_with_source_refs("q1", sql)
        self.assert_unreferenced(result)
        self.assertEqual(result["claim_fragments"], [])
        self.assertEqual(enrich.call_args.args[0], [])
        self.assertEqual(result["source_provenance"]["excluded_claim_fragments"], 1)
        self.assertNotIn(marker, json.dumps(result["agent_statements"]))
        self.assertEqual(result["rows"], baseline["rows"])

    def test_actual_source_fragment_still_reaches_statement_parser(self):
        data = bundle_two().source
        data["steps"][2]["parts"]["memory"] = "Private marker remains unknown."
        tools, _ = setup_tools(build_bundle(data, online=False)); tools.trace_queries.clear()
        sql = "SELECT * FROM SEARCH_TEXT('Private marker','memory')"
        baseline = original_query(tools, sql)
        with patch.object(tools.claim_parser, "enrich", wraps=tools.claim_parser.enrich) as enrich:
            result = tools._query_with_source_refs("q1", sql)
        self.assertEqual(result["claim_fragments"], baseline["claim_fragments"])
        self.assertEqual(enrich.call_args.args[0], baseline["claim_fragments"])
        self.assertTrue(result["verified_source_fragments"])
        self.assertEqual(result["source_provenance"]["excluded_claim_fragments"], 0)


if __name__ == "__main__":
    unittest.main()
