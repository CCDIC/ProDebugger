import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from checks.test_query_backend import source, case_for, manager_answer, answer
from prodebugger.record_bridge import build_bundle, plain
from prodebugger.claim_parser import ClaimParser, cards, fragment, statement, source_cards, validate_unit, decode_batch
from prodebugger.debug_tools import DebugTools
from prodebugger.query_dsl import execute
from prodebugger.query_trace import query, commit as assess
from prodebugger.trace_manager import commit as manage, packet as manager_packet
from prodebugger.direct_submit import packet as submit_packet
from prodebugger.evidence_quality import fact_quality
from prodebugger.abstract_rules import memory_claims
from prodebugger.llm_client import LLMClient, json_lines


def fixture():
    data = source()
    data['steps'][0]['parts'].update(memory='The lamp is likely located on the desk.',
        reflection='Verification is needed.', plan='Inspect the drawer to verify its contents.')
    data['steps'][1]['parts'].update(memory='The options are available.',
        reflection='Details still need confirmation before purchase.',
        plan='Buy now because all product attributes are already confirmed.')
    data['steps'][2]['parts'].update(memory='The result did not yield any relevant products.',
        reflection='The task is complete.', plan='Inspect countertop 1 at decision 3.')
    return data


class FakeParser:
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    def complete(self, messages, schemas, *, phase):
        self.calls.append(plain(messages))
        inputs = json.loads(messages[1]['content'])['units']
        units = []
        for u in inputs:
            topic = next(iter(u['topics']), 'other')
            s = statement(u['quote'], topic, modality='tentative' if 'likely' in u['quote'] else 'unknown')
            units.append({'source_id': u['source_id'], 'statements': [s], 'links': [], 'unresolved': []})
        value = {'units': units}
        if self.transform:
            value = self.transform(value)
        return {'role': 'assistant', 'content': json.dumps(value)}

    def snapshot(self):
        return {'model_calls': len(self.calls)}


class ClaimParserChecks(unittest.TestCase):
    def test_unparsed_memory_is_metadata_not_agent_amnesia(self):
        bundle = build_bundle(fixture())
        markers = [f for f in bundle.record.F if f.rel == 'memory_unparsed']
        self.assertTrue(markers)
        self.assertFalse(any(f.rel == 'recalled_nothing' for f in bundle.record.F))
        self.assertEqual(fact_quality(bundle.record, markers[0])['source_kind'], 'parser_metadata')
        self.assertFalse(any(f.rel == 'memory_unparsed' for f in memory_claims(bundle.projection)))
        self.assertTrue(any(f.rel == 'recalled_summary' and 'likely' in f.span for f in bundle.record.F))

    def test_full_template_and_unresolved_semantic_scope(self):
        data = fixture()
        data['steps'][0]['parts']['reflection'] = (
            'The task is not complete. The task is complete, unless the drawer is closed. '
            'The options are available. I looked to gain information. Unknown novel wording.')
        result = cards(data, [fragment(data, 1, 'reflection')])
        self.assertEqual(result[0]['statements'][0]['polarity'], 'negative')
        self.assertEqual(result[0]['coverage'], 'template_complete')
        self.assertTrue(all(c['coverage'] == 'unresolved' for c in result[1:]))
        self.assertFalse(any(s['topic'] == 'selection' for c in result for s in c['statements']))
        self.assertTrue(any('expected_gain' in c['topics'] for c in result))

    def test_partial_clause_cannot_remove_hidden_qualification_or_leak_context(self):
        data = fixture()
        text = 'Perhaps the task is complete, but confirmation is needed.'
        data['steps'][0]['parts']['reflection'] = text
        lo, hi = text.index('the task'), text.index(',')
        f = fragment(data, 1, 'reflection', lo, hi)
        fake = FakeParser()
        result = ClaimParser(data, client=fake).enrich([f])
        c = result['cards'][0]
        self.assertTrue(c['fragment_partial'])
        self.assertFalse(c['statements'])
        self.assertEqual(c['quote'], 'the task is complete')
        self.assertNotIn('Perhaps', json.dumps(result))
        self.assertNotIn('confirmation', json.dumps(result))
        self.assertEqual(len(fake.calls), 0)

    def test_one_call_parses_all_modules_and_overlapping_sources_once_then_reuses(self):
        data = fixture()
        all_fragments = [fragment(data, t, m) for t in (1, 2, 3) for m in ('memory', 'reflection', 'plan')]
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeParser()
            parser = ClaimParser(data, directory, client=fake)
            result = parser.enrich(all_fragments + all_fragments)
            self.assertEqual(len(fake.calls), 1)
            requested = json.loads(fake.calls[0][1]['content'])['units']
            self.assertEqual(len({u['source_id'] for u in requested}), len(requested))
            self.assertEqual({u['module'] for u in requested}, {'memory', 'reflection', 'plan'})
            self.assertEqual(parser.enrich(all_fragments), result)
            self.assertEqual(len(fake.calls), 1)
            # A different subset can reuse individual statements without another call.
            subset = parser.enrich(all_fragments[:2])
            self.assertGreater(subset['backoff']['reused_units'], 0)
            self.assertEqual(len(fake.calls), 1)
            restarted_fake = FakeParser()
            restarted = ClaimParser(data, directory, client=restarted_fake)
            self.assertEqual(restarted.enrich(all_fragments), result)
            self.assertEqual(len(restarted_fake.calls), 0)

    def test_invalid_and_missing_units_do_not_discard_valid_siblings_or_retry(self):
        data = fixture()
        fs = [fragment(data, t, 'memory') for t in (1, 2, 3)]
        def broken(value):
            value['units'][0]['statements'][0]['modality'] = 'asserted'
            value['units'].pop()
            return value
        fake = FakeParser(broken)
        parser = ClaimParser(data, client=fake)
        result = parser.enrich(fs)
        by_step = {c['step']: c for c in result['cards']}
        self.assertIn('backoff_error', by_step[1])
        self.assertEqual(by_step[1]['coverage'], 'unresolved')
        self.assertEqual(by_step[2]['coverage'], 'model_reviewed')
        self.assertIn('backoff_error', by_step[3])
        self.assertEqual(parser.enrich(fs), result)
        self.assertEqual(len(fake.calls), 1)

    def test_non_verbatim_quote_and_fabricated_relationship_rejected(self):
        data = fixture()
        for kind in ('quote', 'link'):
            def broken(value):
                unit = value['units'][0]
                if kind == 'quote':
                    unit['statements'][0]['quote'] = 'The agent selected medium.'
                else:
                    unit['links'] = [{'kind': 'stated_reason', 'from_quote': 'available',
                        'to_quote': 'selected', 'cue_quote': 'because'}]
                return value
            result = ClaimParser(data, client=FakeParser(broken)).enrich([fragment(data, 2, 'memory')])
            if kind == 'quote':
                self.assertEqual(result['cards'][0]['coverage'], 'unresolved')
                self.assertFalse(result['cards'][0]['statements'])
            else:
                self.assertEqual(result['cards'][0]['coverage'], 'partial')
                self.assertTrue(result['cards'][0]['statements'])
                self.assertFalse(result['cards'][0]['links'])

    def test_scaffolding_and_repeated_clause_content_need_no_extra_calls(self):
        data = fixture()
        for s in data['steps']:
            s['parts']['memory'] = 'Look at the history context above. No relevant items were found.'
        fake = FakeParser()
        result = ClaimParser(data, client=fake).enrich([fragment(data, t, 'memory') for t in (1, 2, 3)])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result['backoff']['requested_units'], 1)
        self.assertEqual(result['backoff']['deduplicated_units'], 2)
        self.assertEqual(result['scaffold_clauses'], 3)
        self.assertEqual({c['step'] for c in result['cards']}, {1, 2, 3})
        self.assertEqual(len({c['source_id'] for c in result['cards']}), 3)
        self.assertTrue(all('quote' not in s for c in result['cards'] for s in c['statements']))

    def test_compact_response_inherits_full_quote_and_accepts_before_condition(self):
        data = fixture()
        data['steps'][0]['parts']['reflection'] = 'Confirmation is needed before purchasing.'
        c = cards(data, [fragment(data, 1, 'reflection')])[0]
        raw = {'source_id': c['source_id'], 'statements': [statement(c['quote'], 'verification')],
            'links': [{'kind': 'condition', 'from_quote': 'Confirmation', 'to_quote': 'purchasing', 'cue_quote': 'before'}],
            'unresolved': []}
        raw['statements'][0].pop('quote')
        parsed = validate_unit(raw, c)
        self.assertEqual(parsed['coverage'], 'model_reviewed')
        self.assertEqual(len(parsed['links']), 1)
        self.assertEqual(parsed['quote'], c['quote'])

    def test_truncated_batch_only_requests_missing_units_in_smaller_groups(self):
        data = fixture()
        class Truncated(FakeParser):
            def complete(self, *args, **kwargs):
                response = super().complete(*args, **kwargs)
                if len(self.calls) == 1:
                    first = json.loads(response['content'])['units'][0]
                    response['content'] = '{"units":[' + json.dumps(first) + ',{"source_id":'
                return response
        fake = Truncated()
        fs = [fragment(data, t, 'memory') for t in (1, 2, 3)]
        parser = ClaimParser(data, client=fake)
        result = parser.enrich(fs)
        self.assertEqual(result['backoff']['mode'], 'recovered_response')
        self.assertTrue(all(c['coverage'] == 'model_reviewed' for c in result['cards']))
        self.assertEqual(result['backoff']['missing_units'], 0)
        self.assertEqual(result['backoff']['recovery_requests'], 2)
        self.assertEqual(len(fake.calls), 3)
        inputs = [json.loads(m[1]['content'])['units'] for m in fake.calls]
        self.assertEqual([len(us) for us in inputs], [3, 1, 1])
        self.assertEqual([u['step'] for us in inputs[1:] for u in us], [2, 3])
        self.assertEqual(parser.enrich(fs), result)
        self.assertEqual(len(fake.calls), 3)
        with self.assertRaises(ValueError):
            decode_batch('unrelated text {"units":[]}')

    def test_compact_wire_expands_to_source_checked_cards(self):
        data = fixture()
        data['steps'][0]['parts']['plan'] = 'Inspect the drawer. This action will reveal its contents.'
        class Compact(FakeParser):
            def complete(self, messages, schemas, *, phase):
                self.calls.append(plain(messages))
                inputs = json.loads(messages[1]['content'])['units']
                first, second = inputs
                return {'role': 'assistant', 'content': json.dumps({'units': [
                    {'i': first['i'], 's': [['verification', 'positive', 'intended', None, 'Inspect the drawer']]},
                    {'i': second['i'], 's': [['expected_gain', 'positive', 'intended', 'This action', 'reveal its contents']],
                     'r': [['This action', first['source_id'], 'Inspect the drawer']],
                     'u': [[None, 'The outcome is not yet observed.']]},
                ]})}
        fake = Compact()
        result = ClaimParser(data, client=fake).enrich([fragment(data, 1, 'plan')])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result['cards'][0]['coverage'], 'model_reviewed')
        second = result['cards'][1]
        self.assertEqual(second['coverage'], 'partial')
        self.assertEqual(second['references'][0]['antecedent_quote'], 'Inspect the drawer')
        self.assertEqual(second['unresolved'][0]['quote'], second['quote'])
        self.assertEqual(second['statements'][0]['modality'], 'intended')

    def test_repeated_truncation_is_bounded_and_cached_not_reported_complete(self):
        data = fixture()
        data['steps'][0]['parts']['memory'] = ' '.join(f'Item {i} was listed among the options.' for i in range(8))
        class NoCompleteUnits(FakeParser):
            def complete(self, *args, **kwargs):
                super().complete(*args, **kwargs)
                return {'role': 'assistant', 'content': '{"units":[{"i":0,"s":['}
        fake = NoCompleteUnits()
        parser = ClaimParser(data, client=fake)
        fs = [fragment(data, 1, 'memory')]
        result = parser.enrich(fs)
        self.assertEqual(len(fake.calls), 7)  # 8 -> 4+4 -> 2+2+2+2, then stop.
        self.assertEqual(result['backoff']['mode'], 'partial_response')
        self.assertEqual(result['backoff']['missing_units'], 8)
        self.assertTrue(all(c['coverage'] == 'unresolved' for c in result['cards']))
        self.assertTrue(all(c['backoff_error'] == 'truncation_recovery_exhausted' for c in result['cards']))
        self.assertEqual(parser.enrich(fs), result)
        self.assertEqual(len(fake.calls), 7)

    def test_finish_reason_length_recovers_missing_units_even_with_valid_json(self):
        data = fixture()
        class LengthLimited(FakeParser):
            def complete(self, *args, **kwargs):
                result = super().complete(*args, **kwargs)
                self.last_completion = {'finish_reason': 'length' if len(self.calls) == 1 else 'stop'}
                if len(self.calls) == 1:
                    value = json.loads(result['content'])
                    value['units'] = value['units'][:1]
                    result['content'] = json.dumps(value)
                return result
        fake = LengthLimited()
        result = ClaimParser(data, client=fake).enrich([fragment(data, t, 'memory') for t in (1, 2)])
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(result['backoff']['missing_units'], 0)
        self.assertEqual(result['backoff']['requests'][0]['finish_reason'], 'length')

    def test_single_oversized_unit_does_not_repeat_an_identical_request(self):
        class Truncated(FakeParser):
            def complete(self, *args, **kwargs):
                super().complete(*args, **kwargs)
                return {'role': 'assistant', 'content': '{"units":['}
        data = fixture()
        fake = Truncated()
        result = ClaimParser(data, client=fake).enrich([fragment(data, 1, 'memory')])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result['backoff']['missing_units'], 1)
        self.assertEqual(result['cards'][0]['backoff_error'], 'single_unit_response_truncated')

    def test_length_limit_before_json_header_recovers_without_discarding_sources(self):
        data = fixture()
        class NoHeader(FakeParser):
            def complete(self, *args, **kwargs):
                result = super().complete(*args, **kwargs)
                self.last_completion = {'finish_reason': 'length' if len(self.calls) == 1 else 'stop'}
                if len(self.calls) == 1:
                    result['content'] = ''
                return result
        fake = NoHeader()
        result = ClaimParser(data, client=fake).enrich([fragment(data, t, 'memory') for t in (1, 2)])
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(result['backoff']['mode'], 'recovered_response')
        self.assertEqual(result['backoff']['missing_units'], 0)
        self.assertEqual(result['backoff']['requests'][0]['returned_units'], 0)

    def test_interrupted_request_does_not_create_client_or_rebill_on_later_batch(self):
        data = fixture()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'claim_parsing'
            folder.mkdir()
            (folder / 'usage.jsonl').write_text(json.dumps({'event': 'pending', 'totals': {}}) + '\n')
            with patch('prodebugger.llm_client.LLMClient') as factory:
                parser = ClaimParser(data, directory, online=True)
                for t in (1, 2):
                    result = parser.enrich([fragment(data, t, 'memory')])
                    self.assertEqual(result['backoff']['mode'], 'unresolved_backoff')
                    self.assertIn('no durable response', result['backoff']['error'])
                factory.assert_not_called()

    def test_recovery_keeps_original_reference_context_and_never_expands_visibility(self):
        data = fixture()
        data['steps'][0]['parts']['plan'] = 'Inspect the drawer. This action will reveal its contents. Hidden later claim.'
        class Truncated(FakeParser):
            def complete(self, *args, **kwargs):
                result = super().complete(*args, **kwargs)
                if len(self.calls) == 1:
                    value = json.loads(result['content'])['units'][0]
                    result['content'] = '{"units":[' + json.dumps(value) + ',{"source_id":'
                return result
        fake = Truncated()
        text = data['steps'][0]['parts']['plan']
        ClaimParser(data, client=fake).enrich([fragment(data, 1, 'plan', 0, text.index('Hidden'))])
        self.assertEqual(len(fake.calls), 2)
        original = json.loads(fake.calls[0][1]['content'])['units'][1]
        retried = json.loads(fake.calls[1][1]['content'])['units'][0]
        self.assertEqual(original['reference_context'], retried['reference_context'])
        self.assertNotIn('Hidden', json.dumps(fake.calls))

    def test_compact_invalid_tuple_cannot_silently_change_meaning(self):
        data = fixture()
        class BadTuple(FakeParser):
            def complete(self, *args, **kwargs):
                super().complete(*args, **kwargs)
                return {'role': 'assistant', 'content': '{"units":[{"i":0,"s":[["state","positive"]]}]}'}
        result = ClaimParser(data, client=BadTuple()).enrich([fragment(data, 1, 'memory')])
        self.assertEqual(result['cards'][0]['coverage'], 'unresolved')
        self.assertFalse(result['cards'][0]['statements'])

    def test_provider_error_during_recovery_keeps_completed_units_without_retry(self):
        data = fixture()
        class Fails(FakeParser):
            def complete(self, *args, **kwargs):
                result = super().complete(*args, **kwargs)
                if len(self.calls) == 1:
                    first = json.loads(result['content'])['units'][0]
                    result['content'] = '{"units":[' + json.dumps(first) + ',{"source_id":'
                    return result
                raise RuntimeError('provider unavailable')
        fake = Fails()
        result = ClaimParser(data, client=fake).enrich([fragment(data, t, 'memory') for t in (1, 2, 3)])
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(result['cards'][0]['coverage'], 'model_reviewed')
        self.assertEqual(result['backoff']['missing_units'], 2)
        self.assertEqual(result['backoff']['mode'], 'unresolved_backoff')

    def test_completed_response_is_recovered_after_crash_without_rebilling(self):
        data = fixture()
        config = dict(model='test-model', max_tokens=128, max_turns=10, max_input_tokens=1000000,
                      max_output_tokens=10000, retries=0)
        fake = FakeParser()
        def transport(**kwargs):
            message = fake.complete(kwargs['messages'], [], phase='PARSE')
            if len(fake.calls) == 1:
                first = json.loads(message['content'])['units'][0]
                message['content'] = '{"units":[' + json.dumps(first) + ',{"source_id":'
            return SimpleNamespace(id='response-test', model='test-model',
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50,
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=7)),
                choices=[SimpleNamespace(finish_reason='length' if len(fake.calls) == 1 else 'stop',
                    native_finish_reason='max_output_tokens' if len(fake.calls) == 1 else 'stop',
                    message=SimpleNamespace(model_dump=lambda **kw: message))])
        def factory(cfg, output):
            return LLMClient(cfg, output, transport=transport)
        class ProcessStopped(BaseException):
            pass
        for crash_after in (1, 2):
            with self.subTest(crash_after=crash_after), tempfile.TemporaryDirectory() as directory, patch('prodebugger.llm_client.LLMClient', factory):
                fake = FakeParser()
                decoded = 0
                def crash_decode(content):
                    nonlocal decoded
                    decoded += 1
                    if decoded == crash_after:
                        raise ProcessStopped
                    return decode_batch(content)
                parser = ClaimParser(data, directory, config=config, online=True)
                fs = [fragment(data, t, 'memory') for t in (1, 2, 3)]
                with patch('prodebugger.claim_parser.decode_batch', side_effect=crash_decode), self.assertRaises(ProcessStopped):
                    parser.enrich(fs)
                self.assertEqual(len(fake.calls), crash_after)
                restarted = ClaimParser(data, directory, config=config, online=True)
                result = restarted.enrich(fs)
                self.assertEqual(len(fake.calls), 3)
                self.assertEqual(result['backoff']['replay_requests'], crash_after)
                self.assertEqual(result['backoff']['missing_units'], 0)
                completed = [e for e in json_lines(Path(directory) / 'claim_parsing/usage.jsonl') if e['event'] == 'completed']
                self.assertEqual(completed[0]['finish_reason'], 'length')
                self.assertEqual(completed[0]['native_finish_reason'], 'max_output_tokens')
                self.assertEqual(completed[0]['reasoning_tokens'], 7)
                self.assertNotIn('finish_reason', completed[0]['message'])
                restored = ClaimParser(data, directory, config=config, online=True)
                self.assertEqual(restored.enrich(fs), result)
                self.assertEqual(len(fake.calls), 3)

    def test_topic_discovery_and_final_sql_provenance(self):
        tools = DebugTools(build_bundle(fixture()))
        result = execute(tools, "SELECT step,module,text FROM FIND_CLAIMS('verification','reflection') WHERE step=1")
        self.assertNotIn('error', result)
        self.assertEqual({c['step'] for c in result['agent_statements']['cards']}, {1})
        self.assertNotIn('purchase', json.dumps(result['agent_statements']))
        for sql in ("SELECT source_id FROM FIND_CLAIMS('verification','reflection')",
                    "SELECT count(*) FROM FIND_CLAIMS('verification','reflection')",
                    "SELECT * FROM FIND_CLAIMS('verification','reflection') WHERE step=999",
                    "SELECT step,module,substr(text,1,8) AS text FROM FIND_CLAIMS('verification','reflection')"):
            response = execute(tools, sql)
            self.assertNotIn('error', response)
            self.assertFalse(response['agent_statements']['cards'])
        response = execute(tools, "SELECT * FROM FIND_CLAIMS('unknown','memory')")
        self.assertTrue(response.get('error') or response.get('function_errors'))

    def test_identical_text_needs_source_offsets_for_safe_alignment(self):
        data = fixture()
        data['steps'][0]['parts']['reflection'] = 'Verification is needed. Verification is needed.'
        tools = DebugTools(build_bundle(data))
        sql = "SELECT step,module,text FROM FIND_CLAIMS('verification','reflection',1,1) LIMIT 1"
        self.assertFalse(execute(tools, sql)['agent_statements']['cards'])
        result = execute(tools, sql.replace('step,module,text', '*'))
        self.assertEqual(len(result['agent_statements']['cards']), 1)
        self.assertEqual(result['agent_statements']['cards'][0]['start'], 0)

    def test_query_batches_anchor_and_selected_early_evidence_preserves_submit(self):
        tools = DebugTools(build_bundle(source()))
        tools.execute('view', {'spec': 'requirements', 'scope': {'mode': 'global'}}, 'map')
        tools.execute('route_cases', {'candidates': [case_for(tools)]}, 'route')
        manage(tools, **manager_answer())
        fake = FakeParser()
        tools.claim_parser = ClaimParser(tools.bundle.source, client=fake)
        result = query(tools, 'case1', "SELECT step,module,text FROM SEARCH_TEXT('decision','plan') WHERE step=1")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual({c['step'] for c in result['agent_statements']['cards']}, {1, 3})
        requested = json.loads(fake.calls[0][1]['content'])['units']
        self.assertNotIn(2, {u['step'] for u in requested})
        event = assess(tools, answer(), 'assess')
        self.assertEqual(set(event['conclusion']), {'window_id', 'candidates', 'uncertainty'})
        submitted = submit_packet(tools)
        trace_evidence = submitted['manager_guidance']['trace_evidence']['case1']
        self.assertEqual(trace_evidence['rows'], result['rows'])
        self.assertNotIn('agent_statements', trace_evidence)
        with self.assertRaisesRegex(ValueError, 'exactly one query'):
            query(tools, 'case1', "SELECT * FROM SEARCH_TEXT('apple','memory')")

    def test_manager_cards_cannot_expose_earlier_mental_sources(self):
        tools = DebugTools(build_bundle(fixture()))
        tools.execute('view', {'spec': 'requirements', 'scope': {'mode': 'global'}}, 'map')
        tools.execute('route_cases', {'candidates': [case_for(tools)]}, 'route')
        result = manager_packet(tools)
        self.assertEqual({c['step'] for c in result['agent_statements']['cards']}, {3})
        self.assertNotIn('likely located', json.dumps(result['agent_statements']))

    def test_source_union_can_join_visible_overlaps_but_cannot_fill_a_gap(self):
        data = fixture()
        original = data['steps'][0]['parts']['memory']
        overlap = [fragment(data, 1, 'memory', 0, 25), fragment(data, 1, 'memory', 20, len(original))]
        combined = cards(data, overlap)
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]['quote'], original)
        self.assertFalse(combined[0]['fragment_partial'])
        gap = [fragment(data, 1, 'memory', 0, 10), fragment(data, 1, 'memory', 20, len(original))]
        separate = cards(data, gap)
        self.assertEqual(len(separate), 2)
        self.assertTrue(all(c['fragment_partial'] for c in separate))
        self.assertNotIn(original[10:20], ''.join(c['quote'] for c in separate))

    def test_tool_journal_restores_enriched_result_without_reparsing(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            bundle = build_bundle(source())
            tools = DebugTools(bundle, out)
            fake = FakeParser()
            tools.claim_parser = ClaimParser(bundle.source, out, client=fake)
            tools.execute('view', {'spec': 'requirements', 'scope': {'mode': 'global'}}, 'map')
            tools.execute('route_cases', {'candidates': [case_for(tools)]}, 'route')
            tools.execute('plan_trace', manager_answer(), 'manager')
            result = tools.execute('query_logs', {'candidate_id': 'case1',
                'sql': "SELECT step,module,text FROM SEARCH_TEXT('decision 1','plan')"}, 'query')
            self.assertNotIn('error', result)
            self.assertEqual(len(fake.calls), 1)
            restored_fake = FakeParser()
            restored = DebugTools(bundle, out)
            restored.claim_parser = ClaimParser(bundle.source, out, client=restored_fake)
            restored.restore(json_lines(out / 'tool_calls.jsonl'))
            self.assertEqual(restored.trace_queries, tools.trace_queries)
            self.assertEqual(len(restored_fake.calls), 0)

    def test_cross_sentence_reference_is_parsed_in_same_batch_and_cache_scoped(self):
        data = fixture()
        data['steps'][0]['parts']['plan'] = 'Inspect the drawer. This action will reveal its contents.'
        class ReferenceParser(FakeParser):
            def complete(self, *args, **kwargs):
                response = super().complete(*args, **kwargs)
                inputs = {u['source_id']: u for u in json.loads(args[0][1]['content'])['units']}
                value = json.loads(response['content'])
                for unit in value['units']:
                    contexts = inputs[unit['source_id']].get('reference_context', [])
                    if contexts:
                        unit['references'] = [{'mention_quote': 'This action',
                            'antecedent_source_id': contexts[0]['source_id'], 'antecedent_quote': 'Inspect the drawer'}]
                response['content'] = json.dumps(value)
                return response
        fake = ReferenceParser()
        parser = ClaimParser(data, client=fake)
        result = parser.enrich([fragment(data, 1, 'plan')])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result['cards'][1]['references'][0]['antecedent_quote'], 'Inspect the drawer')
        # A narrower visibility set must NOT reuse a binding to an invisible antecedent.
        text = data['steps'][0]['parts']['plan']
        narrower = parser.enrich([fragment(data, 1, 'plan', text.index('This'), len(text))])
        self.assertEqual(len(fake.calls), 2)
        self.assertFalse(narrower['cards'][0]['references'])
        self.assertNotIn('Inspect the drawer', json.dumps(narrower))

    def test_fabricated_or_future_reference_cannot_become_evidence(self):
        data = fixture()
        data['steps'][0]['parts']['plan'] = 'This action will help. Inspect the drawer.'
        def future(value):
            value['units'][0]['references'] = [{'mention_quote': 'This action',
                'antecedent_source_id': 'claim:1:plan:23:42', 'antecedent_quote': 'Inspect the drawer'}]
            return value
        result = ClaimParser(data, client=FakeParser(future)).enrich([fragment(data, 1, 'plan')])
        self.assertEqual(result['cards'][0]['coverage'], 'partial')
        self.assertFalse(result['cards'][0]['references'])


if __name__ == '__main__':
    unittest.main()
