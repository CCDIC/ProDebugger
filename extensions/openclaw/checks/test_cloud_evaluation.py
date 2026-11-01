"""Exact-location evaluation must not credit neighbors or unsupported guesses."""
import unittest
from prodebugger.cloud_evaluation import compare


class CloudComparisonChecks(unittest.TestCase):
    target = {'risk_type': 'read_scope', 'call_id': 'trace/call-A', 'span_id': 'call-A', 'import_step': 4}

    def finding(self, **changes):
        return {'id': 'f', 'risk_type': 'read_scope', 'call_ids': ['trace/call-A'],
            'status': 'confirmed', 'evidence': [{'quote': 'actual source'}],
            'evidence_validated': True, **changes}

    def test_exact_identity_survives_import_step_renumbering(self):
        result = compare([self.target], [self.finding(import_steps=[17])])
        self.assertEqual(result['hits'], 1)

    def test_nearby_step_wrong_call_is_not_a_hit(self):
        result = compare([self.target], [self.finding(call_ids=['trace/call-B'], import_steps=[4])])
        self.assertEqual(result['results'][0]['outcome'], 'mislocalized')

    def test_correct_call_without_supported_evidence_remains_uncertain(self):
        for changes in ({'status': 'suspected'}, {'evidence': []}, {'evidence_validated': False}):
            self.assertEqual(compare([self.target], [self.finding(**changes)])['results'][0]['outcome'], 'uncertain')

    def test_wrong_type_and_unknown_control_not_fabricated_accuracy(self):
        self.assertEqual(compare([self.target], [self.finding(risk_type='other')])['results'][0]['outcome'], 'missed')
        result = compare([], [self.finding()])
        self.assertEqual(result['expected_count'], 0)
        self.assertEqual(len(result['additional_findings']), 1)

    def test_input_warning_can_be_evaluated_without_promoting_to_violation(self):
        target = {**self.target, 'risk_type': 'untrusted_instruction'}
        actual = self.finding(risk_type='untrusted_instruction', status='input_warning')
        self.assertEqual(compare([target], [actual])['hits'], 1)
        self.assertEqual(actual['status'], 'input_warning')


if __name__ == '__main__':
    unittest.main()
