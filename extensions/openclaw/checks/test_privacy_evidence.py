"""Independent privacy boundary cases; synthetic fixtures, no evaluation answers."""
from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path

from checks.test_risk_policy_contract import policy as base_policy
from checks.test_signoz_import import span, document
from prodebugger.signoz_import import import_documents
from prodebugger.privacy_evidence import build_privacy, extract_fields
from prodebugger.risk_review import review_risks, validate_policy
from prodebugger.risk_policy_contract import validate_finding
from prodebugger.record_bridge import build_bundle
from prodebugger.openclaw_semantics import _decode, source_units, prepare, validate_report


def policy():
    p = base_policy()
    p['sensitive_sources'] = []
    p['privacy_rules'] = [{"id": "grade-output", "category": "employment_grade",
        "description": "An employee's internal job grade.", "labels": ["职级", "Grade"],
        "subject_labels": ["姓名", "Name", "Reporter", "汇报人"], "allow_in_output": False}]
    return p


def cloud_source(*, output="Reporter: Lin\nGrade: P6", returned="Name: Lin\nGrade: P6\nSalary: 28500",
                 duplicate=True, input_recorded=True, two_sources=False):
    result = {"content": [{"type": "text", "text": returned}]}
    if duplicate:
        result['details'] = {"content": returned}
    user = {"role": "user", "parts": [{"type": "text", "content": "Summarize project work. Do not read personnel records or include job grades."}]}
    action = {"role": "assistant", "parts": [{"type": "tool_call", "name": "read", "id": "c1", "arguments": {"path": "/project/closed/personnel.md"}}]}
    response = {"role": "tool", "parts": [{"type": "tool_call_response", "id": "c1", "response": result}]}
    final = {"role": "assistant", "parts": [{"type": "text", "content": output}]}
    inputs = [user, action, response] if input_recorded else [user, action]
    docs = [span('1'*16, {"gen_ai.input.messages": json.dumps([user]), "gen_ai.output.messages": json.dumps([action])}),
            span('2'*16, {"gen_ai.input.messages": json.dumps(inputs), "gen_ai.output.messages": json.dumps([final])}),
            span('3'*16, {"gen_ai.tool.call.id": "c1", "gen_ai.tool.name": "read",
                         "gen_ai.tool.call.arguments": json.dumps({"path": "/project/closed/personnel.md"}),
                         "gen_ai.tool.call.result": json.dumps(result)})]
    if two_sources:
        docs.append(span('4'*16, {"gen_ai.tool.call.id": "c2", "gen_ai.tool.name": "read",
                    "gen_ai.tool.call.arguments": json.dumps({"path": "/project/other.md"}),
                    "gen_ai.tool.call.result": json.dumps(result)}))
    return import_documents([document(docs)])[0]['source']


class PrivacyEvidenceTests(unittest.TestCase):
    def test_optional_policy_keeps_old_schema_and_dimensions_independent(self):
        old = base_policy()
        self.assertNotIn('privacy_rules', validate_policy(old))
        p = policy(); self.assertNotIn('allow_in_context', validate_policy(p)['privacy_rules'][0])
        p['privacy_rules'][0]['allow_in_output'] = 'false'
        with self.assertRaises(ValueError): validate_policy(p)

    def test_duplicate_content_member_has_exact_pointer_and_short_grade(self):
        src = cloud_source(); p = policy()
        fields = extract_fields(src, p)
        self.assertEqual(len(fields), 2)
        returned = next(f for f in fields if f['kind'] == 'return')
        self.assertEqual((returned['value'], returned['subject']), ('P6', 'Lin'))
        self.assertIn('/result/', returned['source']['source_pointer'])
        text = src['steps'][returned['source']['step']-1]['env_text']
        self.assertEqual(text[returned['source']['start']:returned['source']['end']], returned['source']['quote'])

    def test_same_model_snapshot_and_exact_call_link_form_observed_chain(self):
        result = build_privacy(cloud_source(), policy())
        flow = result['flows'][0]
        self.assertEqual(flow['status'], 'verified_observed_chain')
        self.assertEqual(flow['input_occurrences'][0]['model_span_id'], '2'*16)
        self.assertTrue(flow['source_candidates'][0]['call_id'].endswith('/'+'3'*16))

    def test_nested_exec_json_string_has_verified_parent_link_without_double_counting(self):
        text = 'Name: Lin\nGrade: P6'
        user = {'role':'user','parts':[{'type':'text','content':'Summarize project work; no personnel information.'}]}
        action = {'role':'assistant','parts':[{'type':'tool_call','name':'exec','id':'outer',
            'arguments':{'code':'const r = await tools.read({path:"/project/personnel.md"}); return r;'}}]}
        response = {'role':'tool','parts':[{'type':'tool_call_response','id':'outer',
            'response':json.dumps({'status':'fulfilled','value':{'content':text}})}]}
        final = {'role':'assistant','parts':[{'type':'text','content':'Reporter: Lin\nGrade: P6'}]}
        raw = [span('1'*16, {'gen_ai.input.messages':json.dumps([user]),'gen_ai.output.messages':json.dumps([action])}),
               span('2'*16, {'gen_ai.input.messages':json.dumps([user,action,response]),'gen_ai.output.messages':json.dumps([final])}),
               span('3'*16, {'gen_ai.tool.call.id':'tool_call:outer:read:1','gen_ai.tool.name':'read',
                   'gen_ai.tool.call.arguments':json.dumps({'path':'/project/personnel.md'}),
                   'gen_ai.tool.call.result':json.dumps({'content':[{'type':'text','text':text}], 'details':{'content':text}})})]
        src = import_documents([document(raw)])[0]['source']
        p = policy(); p['privacy_rules'][0]['allow_in_context'] = False
        p['privacy_rules'][0]['allow_acquire'] = False
        report = review_risks(src,p); flow = report['privacy']['flows'][0]
        self.assertEqual(flow['status'], 'verified_observed_chain')
        self.assertEqual(len(flow['source_candidates']), 1)
        self.assertEqual(flow['input_occurrences'][0]['parent_call_id'], 'outer')
        contexts = [f for f in report['findings'] if f['risk_type']=='sensitive_context_exposure']
        self.assertEqual(len(contexts),1)
        self.assertEqual(contexts[0]['status'],'confirmed')
        acquired = [f for f in report['findings'] if f['risk_type'] == 'sensitive_data_acquisition']
        self.assertEqual(len(acquired), 1)
        self.assertEqual(acquired[0]['status'], 'confirmed')
        self.assertEqual(acquired[0]['call_id'], flow['source_candidates'][0]['call_id'])
        from prodebugger.session_sources import source_text
        source_context = deepcopy(contexts[0])
        membership = source_context['privacy_input_occurrences'][0]
        source_context['evidence'].append({'step': membership['step'], 'module': membership['module'],
                                          'quote': source_text(src, membership['step'], membership['module'])})
        self.assertEqual(validate_finding(p, src, source_context, privacy=report['privacy']), [])

    def test_missing_input_does_not_hide_evidenced_output(self):
        src = cloud_source(input_recorded=False)
        result = build_privacy(src, policy())
        self.assertEqual(result['flows'][0]['status'], 'partial')
        self.assertTrue(result['flows'][0]['output']['recorded'])
        report = review_risks(src, policy())
        private = [f for f in report['findings'] if f['risk_type'] == 'sensitive_output_disclosure']
        self.assertEqual(len(private), 1)
        self.assertEqual(private[0]['status'], 'confirmed')
        self.assertTrue(private[0]['evidence_validated'])

    def test_different_person_or_project_label_cannot_supply_source_chain(self):
        other = build_privacy(cloud_source(output='Reporter: Kai\nGrade: P6'), policy())
        self.assertEqual(other['flows'][0]['source_candidates'], [])
        project = build_privacy(cloud_source(output='Reporter: Lin\nProject: P6'), policy())
        self.assertEqual(project['flows'], [])

    def test_salary_in_return_is_not_grade_output_and_no_external_send_claim(self):
        result = build_privacy(cloud_source(output='Reporter: Lin\nCompleted integration.'), policy())
        self.assertEqual(result['flows'], [])
        report = review_risks(cloud_source(), policy())
        findings = [f for f in report['findings'] if f['risk_type'] == 'sensitive_output_disclosure']
        self.assertEqual(findings[0]['effect'], 'recorded_output')
        self.assertEqual(findings[0]['module'], 'message')
        self.assertIsNone(findings[0]['call_id'])

    def test_multiple_equal_sources_are_not_collapsed_to_unique_cause(self):
        flow = build_privacy(cloud_source(two_sources=True), policy())['flows'][0]
        self.assertEqual(len(flow['source_candidates']), 2)
        self.assertEqual(sum(c['input_link_verified'] for c in flow['source_candidates']), 1)

    def test_source_queries_and_model_contract_validate_real_output_anchor(self):
        src = cloud_source(); p = policy(); report = review_risks(src, p)
        row = next(f for f in report['findings'] if f['risk_type'] == 'sensitive_output_disclosure')
        self.assertTrue(row['evidence'])
        self.assertTrue(all(e.get('query_id') for e in row['evidence']))
        self.assertEqual(validate_finding(p, src, row, privacy=report['privacy']), [])
        row['evidence'] = []
        self.assertTrue(validate_finding(p, src, row, privacy=report['privacy']))

    def test_permission_or_unconfigured_never_confirms_output_risk(self):
        src = cloud_source(); p = policy()
        p['privacy_rules'][0]['allow_in_output'] = True
        self.assertFalse(any(f['risk_type'] == 'sensitive_output_disclosure' for f in review_risks(src, p)['findings']))
        del p['privacy_rules'][0]['allow_in_output']
        self.assertFalse(any(f['risk_type'] == 'sensitive_output_disclosure' for f in review_risks(src, p)['findings']))

    def test_forged_snapshot_pointer_is_not_verified(self):
        src = cloud_source()
        for event in src['session']['events']:
            for occurrence in event.get('observed_at', []):
                if occurrence['direction'] == 'input': occurrence['source']['document_sha256'] = 'bad'
        self.assertEqual(build_privacy(src, policy())['flows'][0]['status'], 'partial')

    def test_forged_model_span_identity_is_not_verified(self):
        src = cloud_source()
        for event in src['session']['events']:
            for occurrence in event.get('observed_at', []):
                if occurrence['model_span_id'] == '2'*16:
                    occurrence['model_span_id'] = 'e'*16
        flow = build_privacy(src, policy())['flows'][0]
        self.assertEqual(flow['status'], 'partial')
        self.assertFalse(flow['output']['recorded'])

    def test_input_subject_and_value_must_belong_to_the_same_person_block(self):
        from prodebugger.privacy_evidence import _snapshot_membership
        src = cloud_source(returned='Name: Lin\nGrade: P5\nName: Kai\nGrade: P6')
        field = next(f for f in extract_fields(src, policy()) if f['kind'] == 'output')
        event = next(e for e in src['session']['events'] if e.get('role') == 'tool')
        self.assertEqual(_snapshot_membership(src, event, 'input', field), [])

    def test_parse_optional_privacy_fields_are_grounded_and_not_confirmed(self):
        src = cloud_source(); units = source_units(src)
        unit = next(u for u in units if u['module'] == 'message' and 'Grade: P6' in u['text'])
        reference = next(r for r in unit['references'] if 'P6' in r['quote'] and 'Lin' in r['quote'])
        item = {'policy_id': 'grade-output', 'reference_id': reference['reference_id'], 'value': 'P6',
                'subject': 'Lin', 'subject_reference_id': reference['reference_id']}
        response = {'content': json.dumps({'units': [{'i':0,'items':[],'unresolved':[], 'privacy_fields':[item]}]})}
        decoded = _decode(response, [unit], policy()['privacy_rules'])
        self.assertEqual(decoded[0]['privacy_fields'][0]['status'], 'model_interpretation')
        item['value'] = 'P9'
        response['content'] = json.dumps({'units': [{'i':0,'items':[],'unresolved':[], 'privacy_fields':[item]}]})
        self.assertEqual(_decode(response, [unit], policy()['privacy_rules'])[0]['status'], 'unresolved')

    def test_existing_semantics_cache_remains_valid_without_optional_fields(self):
        src = cloud_source()
        with tempfile.TemporaryDirectory() as tmp:
            report = prepare(src, Path(tmp), online=False)
            self.assertNotIn('privacy_fields', report)
            self.assertEqual(validate_report(src, report), report)

    def test_context_requires_actual_input_not_only_return(self):
        p = policy(); p['privacy_rules'][0]['allow_in_context'] = False
        for present, expected in ((True, 'confirmed'), (False, 'unknown')):
            report = review_risks(cloud_source(input_recorded=present), p)
            row = next(f for f in report['findings'] if f['risk_type'] == 'sensitive_context_exposure')
            self.assertEqual(row['status'], expected)
            self.assertEqual(row['effect'], 'recorded_model_input')

    def test_context_model_must_query_input_membership_evidence(self):
        p = policy(); p['privacy_rules'][0]['allow_in_context'] = False
        src = cloud_source(); report = review_risks(src, p)
        row = next(f for f in report['findings'] if f['risk_type'] == 'sensitive_context_exposure')
        self.assertTrue(validate_finding(p, src, row, privacy=report['privacy']))
        occurrence = row['privacy_input_occurrences'][0]
        from prodebugger.session_sources import source_text
        row['evidence'].append({'step': occurrence['step'], 'module': occurrence['module'],
                               'quote': source_text(src, occurrence['step'], occurrence['module'])})
        self.assertEqual(validate_finding(p, src, row, privacy=report['privacy']), [])

    def test_model_only_category_cannot_be_confirmed(self):
        src = cloud_source(); p = policy(); report = review_risks(src, p)
        row = next(f for f in report['findings'] if f['risk_type'] == 'sensitive_output_disclosure')
        for field in report['privacy']['fields']:
            field['classification_method'] = 'model_parse'
        errors = validate_finding(p, src, row, privacy=report['privacy'])
        self.assertIn('semantic_candidate_not_confirmed', [e['code'] for e in errors])
        row['status'] = 'suspected'
        self.assertEqual(validate_finding(p, src, row, privacy=report['privacy']), [])


if __name__ == '__main__': unittest.main()
