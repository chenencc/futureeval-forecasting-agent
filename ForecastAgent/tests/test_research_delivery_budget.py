"""Local delivery failures cannot consume provider quota or corrupt originals."""
import copy
import json
import unittest

from ForecastAgent.research_loop.delivery import fit_messages
from ForecastAgent.runtime.context import encode, ContextProjectionError


class DeliveryBudgetTests(unittest.TestCase):
    def messages(self, payload, extra=''):
        return [{'role': 'system', 'content': 'Guide'},
                {'role': 'user', 'content': encode(payload)},
                {'role': 'assistant', 'tool_calls': [{'id': 'c', 'function':
                    {'name': 'update_research_state', 'arguments': extra}}]},
                {'role': 'tool', 'tool_call_id': 'c', 'content': '{}'}]

    def test_drop_complete_repeated_group_before_source_spans(self):
        payload = {'immutable_question': {'resolution_criteria': 'Exact rules'},
                   'research_binding_frame': {'inspected_evidence': [{'evidence_id': 'r1',
                    'text': 'Original quotation', 'start': 0, 'end': 18}]}}
        before = copy.deepcopy(payload)
        messages, note = fit_messages(self.messages(payload, 'x' * 6000), payload, 1800)
        self.assertEqual(len(messages), 2)
        self.assertTrue(note['omitted_tool_group'])
        self.assertEqual(json.loads(messages[1]['content'])['immutable_question'], payload['immutable_question'])
        self.assertEqual(payload, before)
        self.assertEqual(note['delivered_evidence_ids'], ['r1'])

    def test_omissions_are_whole_spans_not_shortened_quotes(self):
        spans = [{'evidence_id': f'r{i}', 'text': 'Exact source words. ' * 75,
                  'url': 'https://example.org/source', 'body_sha256': 'a' * 64,
                  'start': i * 1500, 'end': i * 1500 + 1425} for i in range(4)]
        payload = {'immutable_question': {'resolution_criteria': 'Exact rule' * 20},
                   'research_binding_frame': {'inspected_evidence': spans, 'source_context': [spans[0]]}}
        before = copy.deepcopy(payload)
        messages, note = fit_messages(self.messages(payload), payload, 3600)
        self.assertLessEqual(len(encode(messages)), 3600)
        frame = json.loads(messages[1]['content'])['research_binding_frame']
        self.assertGreaterEqual(len(frame['inspected_evidence']), 1)
        for span in frame['inspected_evidence']:
            self.assertEqual(span, spans[int(span['evidence_id'][1:])])
        self.assertTrue(note['omitted_evidence_ids'])
        self.assertEqual(note['deduplicated_context_ids'], ['r0'])
        self.assertEqual(payload, before)

    def test_unfit_immutable_rules_raise_local_typed_failure(self):
        payload = {'immutable_question': {'resolution_criteria': 'x' * 2000}}
        with self.assertRaises(ContextProjectionError):
            fit_messages(self.messages(payload), payload, 1000)

