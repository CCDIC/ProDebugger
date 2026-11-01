"""Shared analysis version, precise nested responses and raw-import regression."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from checks.test_signoz_import import nested_fixture, document
from checks.test_openclaw_semantics import FakeSemanticClient
from prodebugger.signoz_import import import_documents
from prodebugger.signoz_workbench import import_request, change_policy, unconfigured_policy
from prodebugger.safety_workbench import SafetyStore
from prodebugger.safety_audit import audit_source
from prodebugger.openclaw_semantics import prepare
from prodebugger.record_bridge import build_bundle, export_bundle, digest, write_json
from prodebugger.risk_jobs import RiskReviewJobs


class SigNozPlatformChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SafetyStore(Path(self.temp.name) / 'data')
        self.store._risk_jobs = RiskReviewJobs(self.store.folder, start_worker=False)
        self.addCleanup(self.store._risk_jobs.close)
        raw, _ = nested_fixture()
        self.documents = [document(v, str(i)+'.json') for i,v in enumerate(raw)]
        self.safety, self.policy = unconfigured_policy('/workspace')
        self.safety['tools'] = {'read': {'kind': 'read', 'path_arg': 'path'}}
        self.safety['allowed_roots'] = ['/workspace']
        self.policy['tools'] = {'read': {'kind': 'read', 'path_arg': 'path'}}
        self.policy['read_scope'] = {'allowed_roots': ['/workspace'], 'forbidden_paths': []}

    def imported(self):
        result = import_request(self.store, {'files': self.documents,
            'policy': self.safety, 'risk_policy': self.policy})
        self.assertFalse(result['errors'])
        self.assertEqual(len(result['cases']), 1)
        return result['cases'][0]['id']

    def test_import_keeps_raw_bytes_and_queries_nested_return_by_identity(self):
        cid = self.imported()
        case, source, _ = self.store._material(cid)
        self.assertEqual(case['source_category'], 'signoz_cloud_import')
        self.assertEqual([d['text'] for d in source['session']['raw_documents']],
                         [d['text'] for d in self.documents])
        report = audit_source(source, self.safety)
        for row in report['steps']:
            self.assertNotIn('missing_or_ambiguous_matching_response', row.get('gaps', []))
        self.assertEqual(self.store.semantics(cid)['report']['status'], 'deferred')
        self.assertIsNone(self.store.latest_risk_review(cid))
        self.assertTrue(self.store.graph(cid)['session_artifact']['events'])

    def test_raw_import_does_not_infer_permission_from_observed_path(self):
        result = import_request(self.store, {'files': self.documents})
        case, _, _ = self.store._material(result['cases'][0]['id'])
        self.assertIsNone(case['risk_policy']['read_scope'])
        self.assertEqual(case['risk_policy']['tools'], {})

    def test_reused_safety_flag_is_counted_once_in_library_summary(self):
        self.safety['forbidden_paths'] = ['/workspace/report.json']
        self.policy['read_scope']['forbidden_paths'] = ['/workspace/report.json']
        cid = self.imported()
        row = self.store._public_case(self.store._find(cid))
        self.assertEqual(len(row['report']['flags']), 1)
        self.assertEqual(row['risk_summary']['findings'], 1)

    def test_online_parse_replayed_for_model_rules_and_ui_then_invalidated_by_policy(self):
        cid = self.imported()
        _, source, _ = self.store._material(cid)
        structural = self.store.parsed(cid)
        manager = self.store._risk_jobs
        job = manager.enqueue(cid, source, self.policy)
        output = manager.folder / job['id'] / 'run'
        output.mkdir()
        semantic = prepare(source, output, client=FakeSemanticClient())
        compiled = build_bundle(source, output=output, semantics=semantic)
        export_bundle(compiled, output)
        write_json(output / 'compilation_receipt.json', {'canonical_fingerprint': compiled.fingerprint,
            'record_sha256': digest(compiled.snapshot())})
        manager.jobs[job['id']]['status'] = 'failed'  # successful PARSE survives later model failure
        view = self.store.semantics(cid)
        self.assertEqual(view['report']['status'], 'completed')
        self.assertEqual(view['job_id'], job['id'])
        versions = [view['analysis_version'], self.store.parsed(cid)['analysis_version'],
            self.store.graph(cid)['analysis_version'], self.store.analysis(cid)['analysis_version'],
            self.store.risk_report(cid)['analysis_version']]
        self.assertEqual(len(set(versions)), 1)
        self.assertNotEqual(versions[0], structural['analysis_version'])
        self.assertTrue(self.store.parsed(cid)['semantic_fact_metadata'])
        policy = deepcopy(self.policy)
        policy['read_scope']['forbidden_paths'] = ['/workspace/report.json']
        result = change_policy(self.store, {'case_id': cid, 'risk_policy': policy})
        self.assertIsNone(self.store.latest_risk_review(cid))
        self.assertEqual(result['semantics']['report']['status'], 'deferred')
        self.assertTrue((manager.folder / job['id'] / 'run/canonical_record.json').exists())
        self.assertEqual(result['report']['automatic']['status'], 'unsafe')

    def test_tampered_compiled_record_is_not_replayed(self):
        cid = self.imported()
        _, source, _ = self.store._material(cid)
        job = self.store._risk_jobs.enqueue(cid, source, self.policy)
        output = self.store._risk_jobs.folder / job['id'] / 'run'
        compiled = build_bundle(source, output=output)
        export_bundle(compiled, output)
        write_json(output / 'compilation_receipt.json', {'record_sha256': 'bad',
            'canonical_fingerprint': compiled.fingerprint})
        with self.assertRaisesRegex(ValueError, '重放不一致'):
            self.store.parsed(cid)


if __name__ == '__main__':
    unittest.main()
