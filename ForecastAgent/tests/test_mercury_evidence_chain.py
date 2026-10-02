"""Verify original-text provenance, conditional routing and durable stage budgets."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import save


def bundle():
    body = 'PROPOSED ORDER dated July 1, 2026.\n' + ('A relevant signed order may not be effective before 2027.\n' * 700)
    return {'request': {'id': '1', 'question': 'Will an order be effective before 2027?',
                        'resolution_criteria': 'An entered effective order is required.'},
            'pages': {'https://example.org/order': {'content': body,
                'content_sha256': hashlib.sha256(body.encode()).hexdigest()}}, 'gaps': []}


def response(insufficient=False):
    answers = {'event_yes': {'type': 'noul', 'noul': .4},
               'material_conflict': {'type': 'noul', 'noul': 0},
               'evidence_sufficiency': {'type': 'score', 'score': 3}}
    for key in chain.CHECKS:
        answers[key] = {'probabilities': {'supported': 0 if insufficient else 1,
                                         'contradicted': 0, 'insufficient': 1 if insufficient else 0}}
    return {'answers': answers}


class EvidenceChainTests(unittest.TestCase):
    def test_high_yes_with_refuted_condition_routes_without_overriding_score(self):
        result = response(False)
        result['answers']['event_yes']['noul'] = .8
        result['answers']['time_window']['probabilities'] = {'supported': .05, 'contradicted': .88, 'insufficient': .07}
        self.assertIn('time_window', chain.route(result))
        self.assertIn('decision_condition_conflict', chain.route(result))
        self.assertEqual(result['answers']['event_yes']['noul'], .8)
        result['answers']['event_yes']['noul'] = .1
        self.assertEqual(chain.route(result), [])

    def test_full_body_and_exact_offsets(self):
        original = bundle()
        packet = chain.full_packet(original)
        self.assertGreater(max(s['end'] for s in packet['evidence']), 10000)
        state, audit = chain.select(packet)
        self.assertTrue(audit['omitted_ids'])
        self.assertLessEqual(chain.request_bytes(state), chain.FIRST_BYTES)
        self.assertTrue(any(s['start'] == 0 for s in state['evidence']))
        for span in state['evidence']:
            self.assertEqual(span['text'], original['pages'][span['url']]['content'][span['start']:span['end']])
        second, _ = chain.select(packet, state, ['event_stage'], chain.SECOND_BYTES)
        self.assertTrue(all(span in second['evidence'] for span in state['evidence']))
        self.assertGreater(len(second['evidence']), len(state['evidence']))
        self.assertNotIn('answers', second)

    def test_gate_calls_second_only_with_new_original_text(self):
        for uncertain, expected in [(False, 1), (True, 2)]:
            with tempfile.TemporaryDirectory() as root, patch.object(chain, 'call', return_value=response(uncertain)) as mocked:
                result = chain.run_task(bundle(), Path(root))
                self.assertEqual(mocked.call_count, expected)
                self.assertEqual(result['second_call_required'], uncertain)
        small = bundle()
        page = small['pages']['https://example.org/order']
        page['content'] = page['content'][:100]
        page['content_sha256'] = hashlib.sha256(page['content'].encode()).hexdigest()
        with tempfile.TemporaryDirectory() as root, patch.object(chain, 'call', return_value=response(True)) as mocked:
            result = chain.run_task(small, Path(root))
            self.assertEqual(mocked.call_count, 1)
            self.assertTrue(result['no_new_original_text'])

    def test_restart_does_not_replenish_failed_attempt(self):
        packet = chain.full_packet(bundle())
        state, _ = chain.select(packet)
        with tempfile.TemporaryDirectory() as root, patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test'}):
            folder = Path(root)
            save(folder/'http/001.json', {'status': 'reserved'})
            with self.assertRaisesRegex(RuntimeError, 'cap exhausted'):
                chain.call(state, folder)
            changed = copy.deepcopy(state)
            changed['instruction'] += ' changed'
            with self.assertRaisesRegex(ValueError, 'Frozen'):
                chain.call(changed, folder)


if __name__ == '__main__':
    unittest.main()
