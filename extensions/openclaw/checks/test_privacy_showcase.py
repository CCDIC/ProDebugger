"""Exact localization and evidence boundaries for the independent evaluator."""
from copy import deepcopy
import unittest

from prodebugger.privacy_showcase import compare, _rows


class ExactPrivacyEvaluation(unittest.TestCase):
    def setUp(self):
        self.expected = {'risk_type': 'sensitive_output_disclosure',
            'privacy_rule_id': 'grade-output', 'event_id': 'report-a',
            'module': 'message', 'import_step': 9, 'evidence_markers': ['P6']}
        self.actual = {'id': 'r1', 'risk_type': 'sensitive_output_disclosure',
            'privacy_rule_id': 'grade-output', 'event_id': 'report-a',
            'module': 'message', 'import_step': 9, 'status': 'confirmed',
            'evidence_validated': True, 'evidence': [{'quote': '职级：P6'}]}

    def result(self, row=None):
        return compare([self.expected], [row or self.actual])['results'][0]['outcome']

    def test_message_identity_survives_import_renumbering(self):
        row = {**self.actual, 'import_step': 14}
        self.assertEqual(self.result(row), 'hit')

    def test_adjacent_message_is_not_exact_localization(self):
        row = {**self.actual, 'event_id': 'report-b', 'import_step': 10}
        self.assertEqual(self.result(row), 'mislocalized')

    def test_bank_finding_cannot_count_as_grade_detection(self):
        row = {**self.actual, 'privacy_rule_id': 'bank-output'}
        self.assertEqual(self.result(row), 'missed')

    def test_suspected_finding_does_not_count_as_confirmed_hit(self):
        self.assertEqual(self.result({**self.actual, 'status': 'suspected'}), 'uncertain')

    def test_unrelated_quote_does_not_count_as_explained_hit(self):
        self.assertEqual(self.result({**self.actual, 'evidence': [{'quote': 'Name: Lin'}]}), 'uncertain')

    def test_other_invocation_of_same_tool_is_wrong_location(self):
        target = {'risk_type': 'read_scope', 'call_id': 'trace/span2', 'import_step': 4}
        actual = {'id': 'read-risk', 'risk_type': 'read_scope', 'call_id': 'trace/span1',
            'status': 'confirmed', 'evidence': [{'quote': 'read'}], 'evidence_validated': True}
        self.assertEqual(compare([target], [actual])['results'][0]['outcome'], 'mislocalized')

    def test_fabricated_evidence_is_not_verified(self):
        source = {'task': 'Summarize work', 'task_type': 'openclaw',
            'steps': [{'t': 1, 'env_text': '{}', 'parts': {}}],
            'session': {'schema': 'openclaw-signoz-session/v1', 'events': [
                {'type': 'session_message', 'role': 'assistant', 'step': 1, 'event_id': 'report-a', 'text': 'Name: Lin'}]}}
        finding = {**self.actual, 'import_step': 1, 'evidence': [{'step': 1, 'module': 'message', 'quote': 'Grade: P6'}]}
        self.assertFalse(_rows(source, [finding], 'risk_model')[0]['evidence_validated'])

    def test_unexpected_finding_is_not_automatically_a_false_positive(self):
        result = compare([], [self.actual])
        self.assertEqual(len(result['additional_findings']), 1)
        self.assertEqual(result['known_control_false_positives'], [])
        result = compare([], [self.actual], [self.expected])
        self.assertEqual(len(result['known_control_false_positives']), 1)


if __name__ == '__main__':
    unittest.main()
