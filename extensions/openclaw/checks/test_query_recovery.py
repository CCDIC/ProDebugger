"""Recovery must preserve evidence without turning unavailable sources into absence."""
import unittest

from checks.test_query_backend import source, case_for, manager_answer
from prodebugger.debug_tools import DebugTools
from prodebugger.record_bridge import build_bundle, compiler, plain
from prodebugger.query_dsl import execute
from prodebugger.query_trace import query
from prodebugger.trace_manager import commit, packet, submit_guidance


class RecoveryChecks(unittest.TestCase):
    def setUp(self):
        self.tools = DebugTools(build_bundle(source()))
        self.good = "SELECT step,text FROM SEARCH_TEXT('apple','memory')"
        self.bad = "SELECT step,text FROM SEARCH_TEXT('search','plan')"

    def test_short_codes_are_specific_tokens(self):
        for step, text in zip(self.tools.bundle.source['steps'], ('size 4T.', 'size 24T and 4Tall', 'size 2t; 4t')):
            step['parts']['plan'] = text
        result = execute(self.tools, "SELECT step,text FROM SEARCH_TEXT('4T','plan')")
        self.assertNotIn('error', result)
        self.assertEqual(result['returned_steps'], [1, 3])
        self.assertEqual(execute(self.tools, "SELECT step FROM SEARCH_TEXT('2T','plan')")['returned_steps'], [3])
        for needle in ('TT', '44', '4%', '4_', '*4', 'search'):
            with self.subTest(needle=needle):
                self.assertIn('error', execute(self.tools, f"SELECT * FROM SEARCH_TEXT('{needle}','plan')"))

    def test_generic_search_exception_is_exact_webshop_ui_at_known_step(self):
        self.tools.bundle.source['task_type'] = 'webshop'
        self.tools.bundle.source['steps'][0]['env_text'] = "'Search'"
        self.tools.bundle.source['steps'][1]['env_text'] = 'Search results for an apple'
        result = execute(self.tools, "SELECT step,text FROM SEARCH_TEXT('Search','observation',1,1)")
        self.assertEqual(result['rows'], [{'step': 1, 'text': "'Search'"}])
        for args in ("'Search','observation',2,2", "'Search','observation',1,2", "'Search','observation'", "'search','plan',1,1"):
            self.assertIn('error', execute(self.tools, f'SELECT * FROM SEARCH_TEXT({args})'))
        self.tools.bundle.source['task_type'] = 'alfworld'
        self.assertIn('error', execute(self.tools, "SELECT * FROM SEARCH_TEXT('Search','observation',1,1)"))

    def test_double_quote_repair_is_only_for_string_arguments(self):
        sql = 'SELECT step,text FROM SEARCH_TEXT("apple",\'memory\')'
        repaired = execute(self.tools, sql)
        self.assertEqual(repaired['query'], sql)
        self.assertEqual(repaired['rows'], execute(self.tools, self.good)['rows'])
        self.assertEqual(len(repaired['repairs']), 1)
        self.tools.bundle.source['steps'][0]['parts']['action'] = "'go to cabinet 5'"
        quoted = execute(self.tools, 'SELECT text FROM SEARCH_TEXT("\'go to cabinet 5\'",\'action\',1,1)')
        self.assertEqual(quoted['rows'], [{'text': "'go to cabinet 5'"}])
        for bad in ("SEARCH_TEXT(apple,'memory')", "SEARCH_TEXT(lower('apple'),'memory')",
                    "SEARCH_TEXT((SELECT 'apple'),'memory')", 'SOURCE("1",\'apple\')',
                    "SEARCH_TEXT('apple','memory',\"1\",3)"):
            with self.subTest(bad=bad):
                self.assertIn('error', execute(self.tools, f'SELECT * FROM {bad}'))
        # A quoted SELECT identifier still names a column; it is not normalized.
        column = execute(self.tools, 'SELECT "text" FROM SEARCH_TEXT(\'apple\',\'memory\')')
        self.assertEqual(column['repairs'], [])
        self.assertEqual(column['rows'][0]['text'], 'I hold apple 1.')

    def test_partial_union_retains_rows_columns_and_order(self):
        result = execute(self.tools, self.bad + ' UNION ALL ' + self.good + ' ORDER BY step DESC LIMIT 2')
        self.assertNotIn('error', result)
        self.assertTrue(result['partial'])
        self.assertEqual(result['columns'], ['step', 'text'])
        self.assertEqual([r['step'] for r in result['rows']], [3, 2])
        self.assertEqual(result['coverage'], 'partial_failed_branches')
        self.assertEqual(len(result['skipped_branches']), 1)
        failed = next(s for s in result['function_scopes'] if s.get('error'))
        self.assertIsNone(failed['matched_rows'])

    def test_failure_taints_cte_joins_negation_aggregates_and_set_operations(self):
        ctes = "WITH bad AS (SELECT * FROM SEARCH_TEXT('search','plan')), dependent AS (SELECT * FROM bad), good AS (SELECT * FROM SEARCH_TEXT('apple','memory')) "
        arms = [
            'SELECT count(*) AS n FROM dependent',
            'SELECT 999 AS n WHERE NOT EXISTS (SELECT 1 FROM dependent)',
            'SELECT count(*) AS n FROM good LEFT JOIN dependent ON good.step=dependent.step',
            'SELECT n FROM (SELECT 999 AS n EXCEPT SELECT step FROM dependent)',
            'SELECT n FROM (SELECT 999 AS n INTERSECT SELECT step FROM dependent)',
            'SELECT n FROM (SELECT 999 AS n UNION SELECT step FROM dependent)',
        ]
        for arm in arms:
            with self.subTest(arm=arm):
                result = execute(self.tools, ctes + arm + ' UNION ALL SELECT count(*) AS n FROM good')
                self.assertNotIn('error', result)
                self.assertEqual(result['rows'], [{'n': 3}])
                self.assertEqual(len(result['skipped_branches']), 1)
                failed = execute(self.tools, ctes + arm)
                self.assertIn('error', failed)
                self.assertEqual(failed['rows'], [])

    def test_shared_bad_dependency_suppresses_all_dependent_arms(self):
        sql = "WITH b AS (SELECT * FROM SEARCH_TEXT('search','plan')), g AS (SELECT * FROM SEARCH_TEXT('apple','memory')) SELECT count(*) AS n FROM b UNION ALL SELECT 999 AS n WHERE NOT EXISTS (SELECT 1 FROM b) UNION ALL SELECT count(*) AS n FROM g"
        result = execute(self.tools, sql)
        self.assertEqual(result['rows'], [{'n': 3}])
        self.assertEqual([b['branch'] for b in result['skipped_branches']], [1, 2])
        self.assertIn('error', execute(self.tools, self.bad + ' UNION ' + self.good))

    def test_failed_global_limit_is_not_evaluated_as_zero(self):
        sql = "WITH b AS (SELECT * FROM SEARCH_TEXT('search','plan')), g AS (SELECT * FROM SEARCH_TEXT('apple','memory')) SELECT step FROM b UNION ALL SELECT step FROM g LIMIT (SELECT count(*) FROM b)"
        result = execute(self.tools, sql)
        self.assertIn('error', result)
        self.assertEqual(result['rows'], [])
        self.assertIn('modifier depends on failed evidence', result['error']['message'])

    def test_security_failures_still_fail_whole_query(self):
        forbidden = [
            self.good + ' UNION ALL SELECT step,text FROM steps',
            self.good + ' UNION ALL SELECT step,text FROM RAW_STEP(1)',
            self.good + ' UNION ALL SELECT step,text FROM INVENTED(1)',
            self.good + '; DELETE FROM goals',
            "WITH RECURSIVE x AS (SELECT * FROM SEARCH_TEXT('apple','memory')) SELECT * FROM x",
            "SELECT load_extension('anything') FROM SEARCH_TEXT('apple','memory')",
        ]
        for sql in forbidden:
            with self.subTest(sql=sql):
                result = execute(self.tools, sql)
                self.assertIn('error', result)
                self.assertEqual(result['rows'], [])

    def test_partial_recovery_does_not_drop_later_sources_or_large_outputs(self):
        for step in self.tools.bundle.source['steps']:
            for field in ('memory', 'reflection', 'plan', 'action'):
                step['parts'][field] = 'apple ' + 'x' * 1000
            step['env_text'] = 'apple ' + 'x' * 1000
        arms = [f"SELECT step,text FROM SEARCH_TEXT('apple','{field}')" for field in ('memory', 'reflection', 'plan', 'action', 'observation')]
        result = execute(self.tools, self.bad + ' UNION ALL ' + ' UNION ALL '.join(arms))
        self.assertTrue(result['partial'])
        self.assertFalse(result['truncated'])
        self.assertEqual(result['returned_rows'], 15)
        self.assertGreater(sum(len(r['text']) for r in result['rows']), 4320)
        sql = "WITH b AS (SELECT * FROM SEARCH_TEXT('search','plan')), g AS (SELECT * FROM SEARCH_TEXT('apple','memory')) SELECT b.text FROM b UNION ALL SELECT a.text||c.text||d.text AS text FROM g a CROSS JOIN g c CROSS JOIN g d"
        bounded = execute(self.tools, sql)
        import json
        self.assertFalse(bounded['engine_truncated'])
        self.assertEqual(bounded['returned_rows'], 27)
        self.assertGreater(sum(len(json.dumps(r, ensure_ascii=False).encode()) for r in bounded['rows']), 24000)

    def test_more_than_eight_functions_can_return_all_results(self):
        result = execute(self.tools, ' UNION ALL '.join([self.good] * 9))
        self.assertNotIn('error', result)
        self.assertEqual(result['returned_rows'], 27)
        self.assertIsNone(result['limits']['function_calls'])

    def test_action_rows_and_provenance_nodes_are_not_budgeted(self):
        data = source()
        data['steps'] = plain(compiler.segment(['Nothing happens.'] * 160,
            ['<plan>Inspect countertop 1.</plan><action>look</action>'] * 160, [['look']] * 160))
        tools = DebugTools(build_bundle(data))
        result = execute(tools, "SELECT step,fid FROM TRACK_ACTION('look')")
        self.assertEqual(result['returned_rows'], 160)
        self.assertFalse(result['truncated'])
        fids = [r['fid'] for r in result['rows']]
        start = f'fact:{fids[0]}'
        tools.index.neighbours[start] = [{'source': start, 'target': f'fact:{fid}', 'type': 'basis'} for fid in fids[1:]]
        for fid in fids[1:]:
            tools.index.neighbours[f'fact:{fid}'] = []
        graph = execute(tools, f"SELECT fid,step FROM TRACE_FACT({fids[0]},'forward',1)")
        self.assertEqual(graph['returned_rows'], 160)
        self.assertFalse(graph['truncated'])
        self.assertIsNone(graph['function_scopes'][0]['limits']['max_nodes'])

    def test_one_query_and_submit_handoff_include_failure_metadata(self):
        for sql in (self.bad, self.bad + ' UNION ALL ' + self.good):
            with self.subTest(sql=sql):
                self.setUp()
                self.tools.execute('view', {'spec': 'requirements', 'scope': {'mode': 'global'}}, 'map')
                self.tools.execute('route_cases', {'candidates': [case_for(self.tools)]}, 'route')
                self.assertIn('benchmark_scope', packet(self.tools))
                commit(self.tools, **manager_answer())
                result = query(self.tools, 'case1', sql)
                handoff = submit_guidance(self.tools)['manager_guidance']['trace_evidence']['case1']
                for key in ('rows', 'partial', 'function_errors', 'repairs', 'skipped_branches'):
                    self.assertEqual(handoff[key], result[key])
                with self.assertRaisesRegex(ValueError, 'one query'):
                    query(self.tools, 'case1', self.good)


if __name__ == '__main__':
    unittest.main()
