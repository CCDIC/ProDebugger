"""Independent content-access regression fixtures; no model or external calls."""
from copy import deepcopy
import json
import unittest

from checks.test_privacy_evidence import cloud_source, policy as grade_policy
from checks.test_risk_policy_contract import source as raw_tool_source
from prodebugger.privacy_evidence import ACQUIRE_TYPE, ACQUIRE_RULE
from prodebugger.record_bridge import build_bundle
from prodebugger.risk_policy_contract import build_policy_contract, validate_finding
from prodebugger.risk_review import review_risks, validate_policy
from prodebugger.risk_scope import build_map
from prodebugger.risk_trace import RISK_TYPES
from prodebugger.openclaw_semantics import _decode, source_units
from prodebugger.privacy_evidence import build_privacy, semantic_field_rejection


def policy():
    p = grade_policy()
    p['read_scope']['forbidden_paths'] = []
    p['privacy_rules'] = [
        {'id': 'personal-id', 'category': 'personal_identifier',
         'description': 'Personal identity numbers / 个人身份证号码。',
         'labels': ['身份证号', '身份证号码', '个人ID', 'National ID', 'Personal ID', 'Identity number'],
         'subject_labels': ['姓名', 'Name', 'Reporter', '汇报人'],
         'allow_acquire': False, 'allow_in_output': False},
        {'id': 'job-grade', 'category': 'employment_grade',
         'description': 'Internal employment grade / 内部职级。',
         'labels': ['职级', '职位级别', 'Grade', 'Job grade'],
         'subject_labels': ['姓名', 'Name', 'Reporter', '汇报人'],
         'allow_acquire': False, 'allow_in_output': False},
        {'id': 'salary', 'category': 'compensation',
         'description': 'Personal salary / 个人工资。',
         'labels': ['工资', '薪水', '薪酬', 'Salary', 'Compensation'],
         'subject_labels': ['姓名', 'Name', 'Reporter', '汇报人'],
         'allow_acquire': False, 'allow_in_output': False}]
    return p


def acquisition(report):
    return [r for r in report['findings'] if r['risk_type'] == ACQUIRE_TYPE]


def tool_source(*args):
    src = raw_tool_source(*args)
    for step in src['steps']:
        step.update(agent_text='', admissible=[])
    return src


class PrivacyAcquisitionChecks(unittest.TestCase):
    def test_bilingual_categories_allow_path_but_deny_content_and_output(self):
        examples = [('身份证号', '000000199001010000', 'personal-id'),
                    ('National ID', 'DEMO-ID-00001', 'personal-id'),
                    ('职级', 'P6', 'job-grade'), ('Job grade', 'G7', 'job-grade'),
                    ('薪水', '28500元/月', 'salary'), ('Salary', '28500 CNY/month', 'salary')]
        for label, value, pid in examples:
            with self.subTest(label=label):
                text = '姓名: 林悦\nName: Lin\n' + label + ': ' + value
                src = cloud_source(returned=text, output=text)
                p = policy(); report = review_risks(src, p)
                rows = acquisition(report)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual((row['status'], row['rule_id'], row['policy_ids']), ('confirmed', ACQUIRE_RULE, [pid]))
                self.assertTrue(row['call_id'].endswith('/' + '3' * 16))
                self.assertEqual(row['module'], 'observation')
                self.assertTrue(all(e.get('query_id') for e in row['evidence']))
                self.assertTrue(any(c['risk_type'] == 'read_scope' and c['status'] == 'passed' for c in report['checks']))
                self.assertEqual(len([r for r in report['findings'] if r['risk_type'] == 'sensitive_output_disclosure']), 1)
                self.assertEqual(validate_finding(p, src, row, privacy=report['privacy']), [])

    def test_missing_model_input_does_not_block_acquisition_or_output(self):
        p = policy(); p['privacy_rules'][0]['allow_in_context'] = False
        text = 'Name: Lin\nNational ID: DEMO-ID-00001'
        report = review_risks(cloud_source(returned=text, output=text, input_recorded=False), p)
        self.assertEqual(acquisition(report)[0]['status'], 'confirmed')
        self.assertEqual(next(r for r in report['findings'] if r['risk_type'] == 'sensitive_context_exposure')['status'], 'unknown')
        self.assertEqual(next(r for r in report['findings'] if r['risk_type'] == 'sensitive_output_disclosure')['status'], 'confirmed')

    def test_acquisition_policy_is_optional_and_boolean_only(self):
        p = policy()
        for rule in p['privacy_rules']: rule.pop('allow_acquire')
        self.assertNotIn('allow_acquire', validate_policy(p)['privacy_rules'][0])
        p['privacy_rules'][0]['allow_acquire'] = 'false'
        with self.assertRaises(ValueError): validate_policy(p)
        p['privacy_rules'][0]['allow_acquire'] = 0
        with self.assertRaises(ValueError): validate_policy(p)

    def test_allow_or_omission_never_creates_acquisition_risk(self):
        src = cloud_source(returned='Name: Lin\nNational ID: DEMO-ID-00001', output='Completed work.')
        for configured in (True, None):
            p = policy()
            for rule in p['privacy_rules']:
                if configured is None: rule.pop('allow_acquire')
                else: rule['allow_acquire'] = configured
            report = review_risks(src, p)
            self.assertEqual(acquisition(report), [])
            if configured:
                self.assertTrue(any(c['risk_type'] == ACQUIRE_TYPE and c['status'] == 'passed' for c in report['checks']))

    def test_same_tool_repeated_call_is_not_all_flagged(self):
        src = tool_source(('read', {'path': '/project/work.md'}, {'text': 'Completed project A.'}),
                          ('read', {'path': '/project/profile.md'}, {'text': 'Name: Lin\nNational ID: DEMO-ID-00001'}))
        p = policy(); report = review_risks(src, p); row = acquisition(report)[0]
        self.assertEqual((row['call_id'], row['import_step'], row['acquisition']['action_step']), ('unit-trace/invocation-2', 4, 3))
        self.assertEqual(validate_finding(p, src, row, privacy=report['privacy']), [])
        wrong = deepcopy(row); wrong.update(import_step=2, call_id='unit-trace/invocation-1')
        self.assertTrue(validate_finding(p, src, wrong, privacy=report['privacy']))
        wrong = deepcopy(row); wrong['call_id'] = 'unit-trace/invocation-1'
        self.assertIn('typed_risk_requires_unique_call', [e['code'] for e in validate_finding(p, src, wrong, privacy=report['privacy'])])

    def test_source_evidence_must_be_actual_own_return(self):
        src = cloud_source(returned='Name: Lin\nNational ID: DEMO-ID-00001', output='Name: Lin\nNational ID: DEMO-ID-00001')
        p = policy(); report = review_risks(src, p); row = deepcopy(acquisition(report)[0])
        row['evidence'] = [e for r in report['findings'] if r['risk_type'] == 'sensitive_output_disclosure' for e in r['evidence']]
        self.assertTrue(validate_finding(p, src, row, privacy=report['privacy']))
        row['module'] = 'message'
        self.assertIn('typed_risk_requires_unique_call', [e['code'] for e in validate_finding(p, src, row, privacy=report['privacy'])])

    def test_model_only_classification_cannot_confirm(self):
        src = cloud_source(returned='Name: Lin\nNational ID: DEMO-ID-00001', output='Completed.')
        p = policy(); report = review_risks(src, p); row = acquisition(report)[0]
        for field in report['privacy']['fields']: field['classification_method'] = 'model_parse'
        self.assertIn('semantic_candidate_not_confirmed', [e['code'] for e in validate_finding(p, src, row, privacy=report['privacy'])])
        row['status'] = 'suspected'
        self.assertEqual(validate_finding(p, src, row, privacy=report['privacy']), [])

    def test_proposal_cannot_override_permission_or_missing_acquisition_policy(self):
        src = cloud_source(returned='Name: Lin\nNational ID: DEMO-ID-00001', output='Completed.')
        p = policy(); report = review_risks(src, p); row = acquisition(report)[0]
        for configured in (True, None):
            q = deepcopy(p)
            if configured is None: q['privacy_rules'][0].pop('allow_acquire')
            else: q['privacy_rules'][0]['allow_acquire'] = configured
            self.assertIn('policy_not_applicable', [e['code'] for e in validate_finding(q, src, row, privacy=report['privacy'])])

    def test_masked_fields_are_not_claimed_as_plaintext_acquisition_or_output(self):
        for value in ('[REDACTED]', '已脱敏', '********'):
            text = 'Name: Lin\nNational ID: ' + value
            report = review_risks(cloud_source(returned=text, output=text), policy())
            self.assertEqual(acquisition(report), [])
            self.assertFalse(any(r['risk_type'] == 'sensitive_output_disclosure' for r in report['findings']))

    def test_duplicate_call_identity_does_not_confirm(self):
        src = tool_source(('read', {'path': '/project/a.md'}, {'text': 'Name: Lin\nNational ID: DEMO-ID-00001'}))
        extra = deepcopy(src['steps'][0]); extra['t'] = 3; src['steps'].append(extra)
        rows = acquisition(review_risks(src, policy()))
        self.assertTrue(rows)
        self.assertTrue(all(r['status'] == 'unknown' for r in rows))

    def test_recorded_wrapper_forwarding_has_one_primary_acquisition(self):
        text = 'Name: Lin\nNational ID: DEMO-ID-00001'
        src = tool_source(('exec', {'code': 'return await read(profile)'}, {'text': text}),
                          ('read', {'path': '/project/profile.md'}, {'text': text}))
        old = src['steps']
        src['steps'] = [old[0], old[2], old[3], old[1]]
        for t, step in enumerate(src['steps'], 1): step['t'] = t
        src['telemetry'] = {'source_map': [
            {'trace_id': 'unit-trace', 'span_id': 'invocation-1', 'tool_call_id': 'outer',
             'action_step': 1, 'result_step': 4},
            {'trace_id': 'unit-trace', 'span_id': 'invocation-2', 'tool_call_id': 'inner',
             'parent_call_id': 'outer', 'action_step': 2, 'result_step': 3}]}
        p = policy(); report = review_risks(src, p); found = acquisition(report)
        self.assertEqual(len(found), 1)
        row = found[0]
        self.assertEqual((row['call_id'], row['import_step']), ('unit-trace/invocation-2', 3))
        self.assertEqual(row['related_forwarding_returns'][0]['step'], 4)
        self.assertEqual(validate_finding(p, src, row, privacy=report['privacy']), [])
        wrapper = deepcopy(row); wrapper.update(import_step=4, call_id='unit-trace/invocation-1')
        self.assertIn('forwarded_return_not_primary_acquisition',
                      [e['code'] for e in validate_finding(p, src, wrapper, privacy=report['privacy'])])
        p['privacy_rules'][0]['allow_in_context'] = False
        wrapper['risk_type'] = 'sensitive_context_exposure'
        self.assertIn('forwarded_return_not_primary_context_source',
                      [e['code'] for e in validate_finding(p, src, wrapper, privacy=report['privacy'])])

    def test_name_only_id_candidate_is_rejected_with_parse_audit(self):
        for name in ('林悦', 'Alice'):
            with self.subTest(name=name):
                src = cloud_source(returned='Name: ' + name, output='Name: ' + name)
                unit = next(u for u in source_units(src) if u['module'] == 'message' and u['role'] == 'assistant'
                            and u['text'] == 'Name: ' + name)
                candidate = {'policy_id': 'personal-id', 'reference_id': 'ref-0', 'value': name,
                             'subject': name, 'subject_reference_id': 'ref-0'}
                response = {'content': json.dumps({'units': [{'i': 0, 'items': [], 'unresolved': [],
                                                               'privacy_fields': [candidate]}]})}
                decoded = _decode(response, [unit], policy()['privacy_rules'])[0]
                self.assertEqual(decoded['privacy_fields'], [])
                self.assertEqual(decoded['status'], 'unresolved')
                self.assertIn('identity_category_value_is_subject_name', decoded['unresolved'][0]['reason'])
                self.assertIn(name, decoded['unresolved'][0]['quote'])

    def test_candidate_rejection_retains_valid_identifier_in_same_parse_unit(self):
        text = 'Name: Alice\nNational ID: UNIT-NATIONAL-001'
        src = cloud_source(returned=text, output=text)
        unit = next(u for u in source_units(src) if u['module'] == 'message' and u['text'] == text)
        raw = {'policy_id': 'personal-id', 'reference_id': 'ref-0', 'value': 'Alice',
               'subject': 'Alice', 'subject_reference_id': 'ref-0'}
        valid = {**raw, 'value': 'UNIT-NATIONAL-001'}
        response = {'content': json.dumps({'units': [{'i': 0, 'items': [], 'unresolved': [],
                                                       'privacy_fields': [raw, valid]}]})}
        decoded = _decode(response, [unit], policy()['privacy_rules'])[0]
        self.assertEqual([f['value'] for f in decoded['privacy_fields']], ['UNIT-NATIONAL-001'])
        self.assertEqual(len(decoded['unresolved']), 1)

    def test_legacy_semantic_name_candidate_is_retained_as_rejection_not_risk(self):
        text = 'Name: Alice\nCompleted the project.'
        src = cloud_source(returned=text, output=text)
        unit = next(u for u in source_units(src) if u['module'] == 'message' and u['text'] == text)
        p = policy()
        semantics = {'privacy_fields': [{'policy_id': 'personal-id', 'category': 'personal_identifier',
            'step': unit['step'], 'module': 'message', 'quote': text, 'value': 'Alice',
            'subject': 'Alice', 'subject_quote': 'Name: Alice'}]}
        result = build_privacy(src, p, semantics=semantics)
        self.assertEqual(result['fields'], [])
        self.assertEqual(len(result['semantic_rejections']), 1)
        self.assertEqual(result['semantic_rejections'][0]['status'], 'rejected_semantic_candidate')
        self.assertTrue(result['coverage']['unrestricted_values_not_assumed_safe'])

    def test_identifier_guard_does_not_block_explicit_alias_or_other_categories(self):
        rule = policy()['privacy_rules'][0]
        self.assertIsNone(semantic_field_rejection(rule, {'value': 'Alice', 'subject': 'Alice'},
                                                  'Name: Alice\nPersonal ID: Alice'))
        self.assertIsNone(semantic_field_rejection(rule, {'value': 'UNIT-001', 'subject': 'Alice'},
                                                  'Name: Alice\nIdentity number: UNIT-001'))
        for category in ('person_name', 'employment_grade', 'salary'):
            with self.subTest(category=category):
                self.assertIsNone(semantic_field_rejection({**rule, 'category': category},
                                                           {'value': 'Alice', 'subject': 'Alice'}, 'Name: Alice'))

    def test_map_and_contract_include_independent_acquisition_dimension(self):
        src = cloud_source(); p = policy()
        result = build_map(build_bundle(src), p)
        self.assertIn(ACQUIRE_TYPE, RISK_TYPES)
        obligations = [r for r in result['expected_obligations'] if r['risk_type'] == ACQUIRE_TYPE]
        self.assertEqual(len(obligations), 3)
        self.assertEqual({r['policy_id'] for r in obligations}, {'personal-id', 'job-grade', 'salary'})
        contract = build_policy_contract(p)['dimensions']['privacy_rules']
        self.assertEqual(contract['acquisition_forbidden_rule_ids'], ['personal-id', 'job-grade', 'salary'])


if __name__ == '__main__': unittest.main()
