"""Bounded risk-only SQL corrections; original diagnosis protocol stays strict."""
import json
import tempfile
import unittest
from pathlib import Path

from checks.test_risk_jobs import setup_tools, bundle_two, answer
from prodebugger.evidence_tools import ToolError
from prodebugger.query_trace import query as original_query
from prodebugger.risk_trace import MAX_RISK_QUERY_ATTEMPTS, commit, coverage


CTE_ERROR = """-- Purpose: retrieve action and source evidence.
WITH acts AS (SELECT fid,step,rel,args,value,span,source_kind,parse_status FROM TRACK_ACTION('tool_call')),
obs AS (SELECT source_id,fid,step,module,text,matched,source_kind FROM SEARCH_TEXT('hello','observation'))
SELECT step,'action' AS module,CAST(value AS TEXT) AS text,source_kind,parse_status FROM acts
UNION ALL BY NAME SELECT step,module,text,source_kind,parse_status FROM obs"""

FACT_ERROR = """-- Purpose: retrieve calls and field occurrences.
WITH acts AS (SELECT step,'action' AS module,CAST(value AS TEXT) AS text,source_kind,source_id FROM TRACK_ACTION('tool_call')),
names AS (SELECT step,module,text,source_kind,source_id FROM SEARCH_TEXT('林悦','observation'))
SELECT * FROM acts UNION ALL BY NAME SELECT * FROM names"""

VALID = """-- Purpose: retrieve recorded read targets and returned text.
SELECT step,module,text,fid,source_kind,parse_status FROM SEARCH_TEXT('notes.txt','action')
UNION ALL BY NAME SELECT step,module,text,fid,source_kind,parse_status FROM SEARCH_TEXT('hello','observation')"""


def tools():
    value, action = setup_tools(bundle_two())
    value.trace_queries.clear()
    return value, action


class RiskQueryRetryTests(unittest.TestCase):
    def test_cte_missing_projection_error_allows_bounded_correction(self):
        value, _ = tools()
        failed = value._query_with_source_refs('q1', CTE_ERROR)
        self.assertEqual(failed['error']['code'], 'invalid_query')
        self.assertIn('parse_status', failed['error']['message'])
        self.assertEqual(failed['retrieval_status'], 'failed')
        self.assertIsNone(failed['returned_row_count'])
        self.assertTrue(failed['query_retry']['allowed'])
        self.assertNotIn('q1', value.trace_queries)
        valid = value._query_with_source_refs('q1', VALID)
        self.assertEqual(valid['retrieval_status'], 'succeeded')
        self.assertTrue(valid['rows'])
        self.assertTrue(valid['verified_source_fragments'])
        self.assertFalse(valid['query_retry']['allowed'])
        self.assertEqual([r['retrieval_status'] for r in value.risk_query_attempts['q1']], ['failed','succeeded'])

    def test_fact_source_column_confusion_and_short_name_are_both_reported(self):
        value, _ = tools()
        failed = value._query_with_source_refs('q1', FACT_ERROR)
        self.assertIn('source_id', failed['error']['message'])
        self.assertTrue(failed['function_errors'])
        self.assertIn('3..120', failed['function_errors'][0]['message'])
        valid = value._query_with_source_refs('q1', VALID)
        self.assertEqual(valid['retrieval_status'], 'succeeded')
        self.assertEqual(len(value.risk_query_attempts['q1']), 2)

    def test_two_corrections_then_success_are_all_preserved_in_tool_journal(self):
        value, _ = tools()
        with tempfile.TemporaryDirectory() as tmp:
            value.output = Path(tmp)
            results = [value.execute('query_logs', {'candidate_id':'q1','sql':sql}, 'query-'+str(i))
                       for i,sql in enumerate([CTE_ERROR,FACT_ERROR,VALID])]
            self.assertTrue(all('data' in r for r in results))
            self.assertEqual(value.call_count,3)
            rows = [json.loads(s) for s in (Path(tmp)/'trace_queries.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([r['query_attempt'] for r in rows],[1,2,3])
            self.assertEqual([r['retrieval_status'] for r in rows],['failed','failed','succeeded'])
            self.assertEqual(rows[0]['query'],CTE_ERROR)
            self.assertEqual(rows[1]['query'],FACT_ERROR)
            self.assertTrue(rows[2]['verified_source_fragments'])

    def test_exhausted_errors_allow_only_limited_initial_evidence_assessment(self):
        value, action = tools()
        for _ in range(MAX_RISK_QUERY_ATTEMPTS):
            failed = value._query_with_source_refs('q1', CTE_ERROR)
        self.assertFalse(failed['query_retry']['allowed'])
        self.assertIn('q1',value.trace_queries)
        with self.assertRaisesRegex(ToolError,'limit reached'):
            value._query_with_source_refs('q1', VALID)
        result = commit(value,answer(action.fid),'assessment')
        self.assertEqual(result['retrieval_status'],'failed')
        self.assertTrue(coverage(value)['trace_complete'])
        self.assertFalse(coverage(value)['retrieval_complete'])
        self.assertEqual(coverage(value)['query_error_count'],3)
        self.assertIsNone(coverage(value)['retrieval'][0]['returned_rows'])

    def test_successful_empty_query_is_not_retryable_and_does_not_mean_safe(self):
        value, _ = tools()
        result=value._query_with_source_refs('q1',"SELECT * FROM SEARCH_TEXT('not-present-independent-marker','observation')")
        self.assertEqual(result['rows'],[])
        self.assertEqual(result['retrieval_status'],'succeeded')
        self.assertFalse(result['query_retry']['allowed'])
        self.assertFalse(value.risk_reviews)
        with self.assertRaisesRegex(ToolError,'exactly one query'):
            value._query_with_source_refs('q1',VALID)

    def test_partial_query_with_returned_rows_cannot_retrieve_again(self):
        value, _ = tools()
        result=value._query_with_source_refs('q1',"SELECT * FROM SEARCH_TEXT('hello','observation') UNION ALL BY NAME SELECT * FROM SEARCH_TEXT('林悦','message')")
        self.assertTrue(result['rows'])
        self.assertTrue(result['function_errors'])
        self.assertEqual(result['retrieval_status'],'partial')
        self.assertFalse(result['query_retry']['allowed'])
        self.assertFalse(coverage(value)['retrieval_complete'])

    def test_original_diagnosis_query_keeps_its_one_opportunity_contract(self):
        value, _ = tools()
        failed=original_query(value,'q1',CTE_ERROR)
        self.assertTrue(failed['error'])
        with self.assertRaisesRegex(ToolError,'exactly one query'):
            original_query(value,'q1',VALID)


if __name__=='__main__': unittest.main()
