"""A vendor redaction placeholder must not duplicate a public output message."""
import json
import unittest

from prodebugger.signoz_import import import_documents


class MessageCopies(unittest.TestCase):
    def source(self, vendor):
        raw = {'trace_id': 'a' * 32, 'span_id': 'b' * 16, 'name': 'openclaw.model.call',
            'attributes': {'gen_ai.input.messages': json.dumps([{'role': 'user', 'parts': [{'type': 'text', 'content': 'Write report'}]}]),
                'gen_ai.output.messages': json.dumps([{'role': 'assistant', 'parts': [{'type': 'text', 'content': 'Reporter: Lin'}], 'finish_reason': 'stop'}]),
                'openclaw.content.output_messages': json.dumps([{'role': 'assistant', 'content': vendor, 'stopReason': 'stop'}])}}
        return import_documents([{'name': 'model.json', 'text': json.dumps(raw)}])[0]['source']

    def test_redacted_marker_preserved_without_duplicate_report(self):
        source = self.source([{'type': 'reasoning', 'redacted': True}, {'type': 'text', 'text': 'Reporter: Lin'}])
        outputs = [e for e in source['session']['events'] if e['type'] == 'session_message' and e.get('role') == 'assistant']
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]['text'], 'Reporter: Lin')
        self.assertEqual(len(outputs[0]['observed_at']), 2)
        self.assertTrue(any(b.get('redacted') for b in outputs[0]['content']))

    def test_distinct_recorded_reasoning_is_not_silently_removed(self):
        source = self.source([{'type': 'reasoning', 'redacted': True, 'text': 'Recorded extra content'},
                              {'type': 'text', 'text': 'Reporter: Lin'}])
        outputs = [e for e in source['session']['events'] if e['type'] == 'session_message' and e.get('role') == 'assistant']
        self.assertEqual(len(outputs), 2)
        self.assertTrue(any('Recorded extra content' in json.dumps(e['content']) for e in outputs))


if __name__ == '__main__': unittest.main()
