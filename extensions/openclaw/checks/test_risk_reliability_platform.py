"""Separate suites and preserve reference rejection audit across API boundaries."""
import hashlib
from pathlib import Path

from checks.test_risk_platform import RiskPlatformTests
from prodebugger.record_bridge import digest, write_json
from prodebugger.risk_validation import suite_view, reference_view
from prodebugger.risk_jobs import REVIEW_PROTOCOL


class RiskReliabilityPlatformTests(RiskPlatformTests):
    def test_policy_identity_accepts_only_reproducible_saved_normalization(self):
        from copy import deepcopy
        from prodebugger.risk_validation import _same_policy_input
        raw = deepcopy(self.p)
        raw['tools']['http_request']['effect_methods'] = ['PUT', 'POST']
        self.store.add_source(self.source, self.p_safety(), title='Neutral case',
            case_id='normalization', risk_policy=raw)
        case = self.store._find('normalization')
        wanted = digest(raw)
        self.assertNotEqual(digest(case['risk_policy']), wanted)
        self.assertFalse(_same_policy_input(self.store,case,wanted))
        original = self.store._resolve(case['source_path']).parent/'risk-policy-source.json'
        write_json(original,raw)
        case['risk_policy_source_path'] = self.store._relative(original)
        self.assertTrue(_same_policy_input(self.store,case,wanted))
        case['risk_policy']['tools']['http_request']['effect_methods'].append('DELETE')
        self.assertFalse(_same_policy_input(self.store,case,wanted))
        case['risk_policy']['tools']['http_request']['effect_methods'].remove('DELETE')
        altered=deepcopy(raw);altered['tools']['http_request']['effect_methods'].append('PATCH')
        write_json(original,altered)
        self.assertFalse(_same_policy_input(self.store,case,wanted))

    def test_independent_suites_do_not_mix_sampling_or_private_input(self):
        self.add()
        base = self.store._find('platform-case')
        base['risk_validation'] = True
        self.store.add_source(self.source, self.p_safety(), title='Neutral case',
            case_id='neutral-b', risk_policy=self.p)
        holdout = self.store._find('neutral-b')
        holdout.update(risk_validation=True, validation_group='holdout')
        for cid, filename, suffix in [('platform-case', 'risk-suite.json', '.sha256'),
                                      ('neutral-b', 'risk-holdout.json', '.file.sha256')]:
            path = Path(self.temp.name) / 'evaluation-private' / filename
            write_json(path, {'fixed_at':'before-analysis', 'cases':[{'case_id':cid,
                'expected':[], 'changes':['PRIVATE-SUITE-CONTENT'], 'is_constructed':True,
                'source_category':'constructed_test', 'input_sha256':digest(self.source),
                'risk_policy_sha256':digest(self.store._find(cid)['risk_policy'])}]})
            path.with_suffix(suffix).write_text(hashlib.sha256(path.read_bytes()).hexdigest())
        views = suite_view(self.store)['cases']
        self.assertEqual({c['validation_group'] for c in views}, {'development','holdout'})
        for view in views:
            for key in ('automatic_evaluation', 'model_evaluation'):
                self.assertEqual(view[key]['status'], 'awaiting_analysis')
                self.assertNotIn('expected_count', view[key])
                self.assertNotIn('checks', view[key])
        for group, cid in [('development','platform-case'), ('holdout','neutral-b')]:
            result = self.store.sample({'count':20, 'risk_validation_group':group})
            self.assertEqual(result['case_ids'], [cid])
            self.assertEqual(result['filters']['risk_validation_group'], group)
        # Workbench submits the effective, validated policy. Version selection
        # must not accept a job using the pre-normalization configuration.
        job = self.manager.enqueue('neutral-b', self.source, holdout['risk_policy'])
        folder = self.manager.folder / job['id']
        self.assertNotIn('PRIVATE-SUITE-CONTENT', (folder/'input.json').read_text()+(folder/'context.json').read_text())
        with self.assertRaises(ValueError):
            reference_view(self.store, 'neutral-b')
        self.manager.cancel(job['id'])
        self.assertEqual(reference_view(self.store,'neutral-b')['fixed_at'], 'before-analysis')

    @staticmethod
    def p_safety():
        from checks.risk_fixtures import safety_policy
        return safety_policy()

    def test_model_rejections_are_auditable_and_not_added_as_findings(self):
        self.add()
        job = self.manager.enqueue('platform-case', self.source, self.store._find('platform-case')['risk_policy'])
        folder = self.manager.folder / job['id'] / 'run'
        folder.mkdir()
        (folder/'risk_rejections.jsonl').write_text('{"error":"policy prerequisite absent","proposal":{"status":"confirmed"}}\n')
        (folder/'risk_reference_resolutions.jsonl').write_text('{"status":"rejected","reference_id":"invalid"}\n')
        value = self.manager.get(job['id'])
        self.assertEqual(value['reference_audit']['rejections'], 1)
        self.assertEqual(value['reference_audit']['resolution_attempts'], 1)
        self.assertEqual(value['review_protocol'], REVIEW_PROTOCOL)
        self.assertIsNone(value.get('result'))
        self.assertTrue(value['implementation_versions'])
