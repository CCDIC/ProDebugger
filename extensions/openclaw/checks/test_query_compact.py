import unittest

from checks.test_query_backend import source, case_for, manager_answer, answer
from prodebugger.debug_tools import DebugTools
from prodebugger.record_bridge import build_bundle
from prodebugger.query_dsl import execute
from prodebugger.query_simplify import simplify_sql
from prodebugger.query_trace import query, commit as assess
from prodebugger.trace_manager import commit as manage, submit_guidance
from prodebugger.direct_submit import packet as submit_packet


def sparse(rows):
    return [{key: value for key, value in row.items() if value is not None} for row in rows]


class CompactQueryChecks(unittest.TestCase):
    def setUp(self):
        self.tools = DebugTools(build_bundle(source()))

    def run_sql(self, sql):
        result = execute(self.tools, sql)
        self.assertNotIn("error", result, result.get("error"))
        return result

    def same(self, old, new):
        before, after = self.run_sql(old), self.run_sql(new)
        self.assertEqual(sparse(before["rows"]), sparse(after["rows"]))
        self.assertEqual(before["returned_steps"], after["returned_steps"])
        self.assertEqual(before["claim_fragments"], after["claim_fragments"])
        return after

    def test_batch_is_ordered_cartesian_union_all_with_duplicates(self):
        functions = [f"SEARCH_TEXT('{word}','{module}')" for word in ("apple", "apple")
                     for module in ("memory", "reflection")]
        old = " UNION ALL ".join("SELECT step,module,text FROM " + f for f in functions)
        new = "SELECT step,module,text FROM SEARCH_TEXT(('apple','apple'),('memory','reflection'))"
        result = self.same(old, new)
        self.assertEqual(result["returned_rows"], 12)
        self.assertEqual(result["row_format"], "sparse/v1")

    def test_claim_topics_and_action_relations_accept_literal_batches(self):
        self.same("SELECT * FROM FIND_CLAIMS('state','reflection') UNION ALL SELECT * FROM FIND_CLAIMS('verification','reflection')",
                  "SELECT * FROM FIND_CLAIMS(('state','verification'),'reflection')")
        self.same("SELECT fid,span FROM TRACK_ACTION('look') UNION ALL SELECT fid,span FROM TRACK_ACTION('look')",
                  "SELECT fid,span FROM TRACK_ACTION(('look','look'))")

    def test_sparse_named_union_keeps_zero_false_empty_string_and_missing_is_null(self):
        old = "SELECT step,text,0 AS zero,false AS flag,'' AS empty,NULL AS fid FROM SEARCH_TEXT('apple','memory') UNION ALL SELECT step,NULL,0,false,'',fid FROM TRACK_ACTION('look') ORDER BY step,text"
        new = "SELECT step,text,0 AS zero,false AS flag,'' AS empty FROM SEARCH_TEXT('apple','memory') UNION ALL BY NAME SELECT fid,step,0 AS zero,false AS flag,'' AS empty FROM TRACK_ACTION('look') ORDER BY step,text"
        result = self.same(old, new)
        self.assertTrue(all(row['zero'] == 0 and row['flag'] == 0 and row['empty'] == '' for row in result['rows']))
        self.assertTrue(all(not ('text' in row and 'fid' in row) for row in result['rows']))
        self.assertIn('fid', result['columns'])
        filtered = self.run_sql("WITH e AS (" + new.rsplit(' ORDER BY', 1)[0] + ") SELECT step FROM e WHERE fid IS NULL")
        self.assertEqual(filtered['returned_rows'], 3)

    def test_named_union_stars_ctes_distinct_and_branch_limit(self):
        sql = "WITH x AS (SELECT * FROM SEARCH_TEXT('apple','memory') UNION ALL BY NAME SELECT fid,step,span AS text FROM TRACK_ACTION('look')) SELECT step,text,fid FROM x ORDER BY step,text"
        result = self.run_sql(sql)
        self.assertEqual(result['returned_rows'], 6)
        self.assertEqual(len(result['claim_fragments']), 0)  # module not returned
        self.same("SELECT step,text FROM SEARCH_TEXT('apple','memory') UNION SELECT step,text FROM SEARCH_TEXT('apple','memory') ORDER BY step",
                  "SELECT step,text FROM SEARCH_TEXT('apple','memory') UNION BY NAME SELECT text,step FROM SEARCH_TEXT('apple','memory') ORDER BY step")
        self.same("SELECT step,text,NULL AS fid FROM (SELECT step,text FROM SEARCH_TEXT('apple','memory') ORDER BY step DESC LIMIT 1) UNION ALL SELECT step,NULL,fid FROM TRACK_ACTION('look') ORDER BY step DESC LIMIT 2",
                  "SELECT step,text FROM (SELECT step,text FROM SEARCH_TEXT('apple','memory') ORDER BY step DESC LIMIT 1) UNION ALL BY NAME SELECT step,fid FROM TRACK_ACTION('look') ORDER BY step DESC LIMIT 2")

    def test_named_union_supports_ctes_and_joined_sources(self):
        sql = "WITH original AS (SELECT step AS t,text AS body FROM SEARCH_TEXT('apple','memory')), joined AS (SELECT m.step,m.module,m.text FROM SEARCH_TEXT('apple','memory') m JOIN original o ON m.step=o.t) SELECT * FROM joined UNION ALL BY NAME SELECT step,fid FROM TRACK_ACTION('look')"
        self.assertEqual(self.run_sql(sql)['returned_rows'], 6)
        bad = "SELECT * FROM SEARCH_TEXT('apple','memory') m JOIN SEARCH_TEXT('apple','reflection') r ON m.step=r.step UNION ALL BY NAME SELECT step FROM TRACK_ACTION('look')"
        self.assertIn('error', execute(self.tools, bad))

    def test_unknown_batch_member_is_unavailable_not_empty_or_partial_truth(self):
        bad = "SELECT step,text FROM SEARCH_TEXT('apple',('memory','invalid_module'))"
        self.assertIn('error', execute(self.tools, bad))
        recovered = self.run_sql(bad + " UNION ALL BY NAME SELECT step,fid FROM TRACK_ACTION('look')")
        self.assertTrue(recovered['partial'])
        self.assertEqual(recovered['returned_rows'], 3)
        self.assertTrue(all('text' not in row for row in recovered['rows']))
        for use in ('SELECT count(*) AS n FROM bad', 'SELECT 99 AS n WHERE NOT EXISTS (SELECT 1 FROM bad)'):
            sql = "WITH bad AS (" + bad + ") " + use + " UNION ALL BY NAME SELECT count(*) AS n FROM TRACK_ACTION('look')"
            result = self.run_sql(sql)
            self.assertEqual(result['rows'], [{'n': 3}])
            self.assertTrue(result['partial'])

    def test_named_union_recovers_each_independent_arm(self):
        sql = "SELECT step,text FROM SEARCH_TEXT('apple','memory') UNION ALL BY NAME SELECT step,text FROM SEARCH_TEXT('search','plan') UNION ALL BY NAME SELECT step,fid FROM TRACK_ACTION('look') ORDER BY step"
        result = self.run_sql(sql)
        self.assertEqual(result['returned_rows'], 6)
        self.assertEqual(len(result['skipped_branches']), 1)

    def test_compact_syntax_does_not_relax_security_or_literal_rules(self):
        for sql in (
            "SELECT * FROM SEARCH_TEXT(('apple',lower('pear')),'memory')",
            "SELECT * FROM SEARCH_TEXT(('apple',text),'memory')",
            "SELECT * FROM SEARCH_TEXT(('apple',('pear','cup')),'memory')",
            "SELECT * FROM SEARCH_TEXT('apple','memory',(1,2),3)",
            "SELECT * FROM SEARCH_TEXT('apple','memory') UNION ALL BY NAME SELECT * FROM RAW_STEP(1)",
            "SELECT * FROM SEARCH_TEXT('apple','memory') UNION ALL BY NAME SELECT * FROM steps",
            "SELECT load_extension('x') FROM SEARCH_TEXT('apple',('memory','plan'))",
            "SELECT 2 AS step FROM SEARCH_TEXT('apple','memory') UNION ALL BY NAME SELECT step FROM TRACK_ACTION('look')",
            "SELECT step AS fid FROM SEARCH_TEXT('apple','memory') UNION ALL BY NAME SELECT fid FROM TRACK_ACTION('look')",
            "SELECT * FROM SEARCH_TEXT('apple',('memory','plan')); DELETE FROM goals",
        ):
            with self.subTest(sql=sql):
                self.assertIn('error', execute(self.tools, sql))

    def test_optional_source_fields_do_not_expand_hidden_or_ambiguous_text(self):
        sql = "SELECT step,module,text,NULL AS source_id FROM SEARCH_TEXT('apple','memory') WHERE step=1"
        result = self.run_sql(sql)
        self.assertEqual({c['step'] for c in result['claim_fragments']}, {1})
        self.assertFalse(self.run_sql("SELECT text FROM SEARCH_TEXT('apple',('memory','plan'))")['claim_fragments'])
        data = source()
        data['steps'][0]['parts']['reflection'] = 'Verification is needed. Verification is needed.'
        self.tools = DebugTools(build_bundle(data))
        query_text = "SELECT step,module,text,NULL AS source_id FROM FIND_CLAIMS('verification','reflection',1,1) LIMIT 1"
        self.assertFalse(self.run_sql(query_text)['claim_fragments'])
        precise = self.run_sql(query_text.replace('text,NULL AS source_id', 'text,start'))
        self.assertEqual(len(precise['claim_fragments']), 1)
        self.assertEqual(precise['claim_fragments'][0]['start'], 0)

    def test_sparse_early_sources_are_parsed_and_handed_off_without_schema_changes(self):
        t = self.tools
        t.execute('view', {'spec': 'requirements', 'scope': {'mode': 'global'}}, 'map')
        t.execute('route_cases', {'candidates': [case_for(t)]}, 'route')
        manage(t, **manager_answer())
        result = query(t, 'case1', "SELECT step,module,text FROM SEARCH_TEXT('decision',('plan','memory')) WHERE step=1 UNION ALL BY NAME SELECT fid,step FROM TRACK_ACTION('look') WHERE step=1")
        self.assertEqual({c['step'] for c in result['claim_fragments']}, {1})
        assess(t, answer(), 'assess')
        handoff = submit_guidance(t)['manager_guidance']['trace_evidence']['case1']
        self.assertEqual(handoff['rows'], result['rows'])
        self.assertEqual(handoff['row_format'], 'sparse/v1')
        self.assertTrue(submit_packet(t)['coverage']['trace_complete'])
        with self.assertRaisesRegex(ValueError, 'one query'):
            query(t, 'case1', "SELECT * FROM SEARCH_TEXT('apple','memory')")

    def test_converter_preserves_positional_sort_multiplicity_and_asymmetric_lists(self):
        queries = [
            "SELECT step,NULL AS fid,module,text FROM SEARCH_TEXT('apple','memory') UNION ALL SELECT step,fid,'action',span FROM TRACK_ACTION('look') ORDER BY 2,1 LIMIT 4",
            "SELECT step,module,text FROM SEARCH_TEXT('apple','memory') UNION ALL SELECT step,module,text FROM SEARCH_TEXT('countertop','memory') UNION ALL SELECT step,module,text FROM SEARCH_TEXT('apple','plan') UNION ALL SELECT step,module,text FROM SEARCH_TEXT('countertop','plan') ORDER BY step",
            "SELECT step,module,text FROM SEARCH_TEXT('apple','memory') UNION ALL SELECT step,module,text FROM SEARCH_TEXT('apple','memory')",
            "SELECT step,text,NULL AS fid FROM SEARCH_TEXT('apple','memory') UNION SELECT step,NULL,fid FROM TRACK_ACTION('look') ORDER BY step",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                self.same(sql, simplify_sql(sql))

    def test_converter_removes_cte_padding_only_when_consumers_keep_semantics(self):
        template = "WITH claims AS (SELECT source_id,step,text FROM SEARCH_TEXT('apple','memory')), actions AS (SELECT CAST(NULL AS TEXT) AS source_id,step,span AS text FROM TRACK_ACTION('look')) SELECT * FROM claims UNION ALL SELECT * FROM actions{} ORDER BY step"
        compact = simplify_sql(template.format(''))
        self.assertNotIn('NULL', compact)
        self.same(template.format(''), compact)
        # Removing a field used by the CTE consumer would invalidate this filter.
        guarded = template.format(' WHERE source_id IS NULL')
        self.assertIn('NULL AS', simplify_sql(guarded).replace('CAST(NULL AS TEXT)', 'NULL'))
        self.same(guarded, simplify_sql(guarded))


if __name__ == '__main__':
    unittest.main()
