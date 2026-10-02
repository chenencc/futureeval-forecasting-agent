"""Validate complete distributions, evidence identity and received-response recovery."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.categorical import run, distribution, fuse, parse, choice_questions
from ForecastAgent.competition.nonbinary_debug import blind_input
from ForecastAgent.competition.queue import load, save


class CategoricalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.options = ['Short', 'Long']
        self.report = {'rule_decomposition': 'Count games under the rules', 'base_rate': 'Unavailable',
            'option_analysis': [{'option': x, 'case_for': 'Evidence', 'case_against': 'Uncertain'} for x in self.options],
            'facts': [{'source_id': 'S1', 'quote': 'Fourteen games.', 'claim': 'Scheduled match length'}],
            'gaps': ['Unknown outcomes'], 'contradictions': 'None identified', 'source_independence': 'One source',
            'probabilities': {'Short': .25, 'Long': .75}}
        self.message = {'tool_calls': [{'function': {'name': 'record_categorical', 'arguments': json.dumps(self.report)}}]}
        self.bundle = {'request': {'id': '1', 'question': 'Match length?', 'question_type': 'multiple_choice',
            'options': self.options, 'resolution_criteria': 'Count the scheduled games', 'mode': 'live'},
            'pages': {'https://example.org/rules': {'content': 'Fourteen games.', 'retrieved_at_utc': '2026-10-02T00:00:00Z'}}}
        save(self.root / 'bundle.json', self.bundle)
        self.env = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test',
            'FORECAST_MODEL': 'nvidia/nemotron-3-ultra-550b-a55b:free'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def ask(self, messages, key, **kwargs):
        record = {'request': {'model': kwargs['model_route'].model()}, 'status': 'reserved'}
        token = kwargs['observer']('reserve', record)
        record.update(status='received', response={'choices': [{'message': self.message}]})
        kwargs['observer']('complete', record, token)
        return self.message

    def decision(self, state, questions, key, observer):
        self.assertNotIn('probabilities', state['analysis'])
        self.assertEqual(set(questions['event_outcome']['criteria']), {'option_0', 'option_1'})
        record = {'request': {'model': 'inception/mercury-decide:free'}, 'status': 'reserved'}
        token = observer('reserve', record)
        response = {'model': 'inception/mercury-decide:free', 'answers': {'event_outcome': {'type': 'choice',
            'choice': 'option_1', 'confidence': .8, 'probabilities': {'option_0': .2, 'option_1': .8}}}}
        record.update(status='received', response=response)
        observer('complete', record, token)
        return response

    def test_complete_exact_distribution_and_api_floor(self):
        result = fuse({'Short': 0., 'Long': 1.}, {'Short': .2, 'Long': .8}, self.options)
        self.assertEqual(result['equal_mean_probabilities'], {'Short': .1, 'Long': .9})
        self.assertAlmostEqual(sum(result['payload_preview']['probability_yes_per_category'].values()), 1)
        with self.assertRaises(ValueError):
            distribution({'Short': .5}, self.options)
        with self.assertRaises(ValueError):
            distribution({'Short': float('nan'), 'Long': .5}, self.options)

    def test_rounding_tolerance_is_explicit(self):
        result = distribution({'Short': .2, 'Long': .799}, self.options, .02)
        self.assertAlmostEqual(sum(result.values()), 1)
        with self.assertRaises(ValueError):
            distribution({'Short': .2, 'Long': .799}, self.options)

    def test_no_new_http_on_resume(self):
        output = self.root / 'analysis'
        first = run(self.root / 'bundle.json', output, ask=self.ask, decision=self.decision)
        def forbidden(*args, **kwargs):
            raise AssertionError('Replay called provider')
        second = run(self.root / 'bundle.json', output, ask=forbidden, decision=forbidden)
        self.assertEqual(first, second)
        self.assertEqual(len(list((output / 'reasoning-http').glob('*.json'))), 1)
        self.assertEqual(len(list((output / 'mercury-http').glob('*.json'))), 1)

    def test_received_transport_survives_parse_crash(self):
        output = self.root / 'analysis'
        run(self.root / 'bundle.json', output, ask=self.ask, decision=self.decision)
        (output / 'analysis.json').unlink()
        (output / 'decision-response.json').unlink()
        def forbidden(*args, **kwargs):
            raise AssertionError('Received transport should be replayed')
        result = run(self.root / 'bundle.json', output, ask=forbidden, decision=forbidden)
        self.assertEqual(result['status'], 'completed')

    def test_failed_mercury_keeps_reasoning_without_default(self):
        def failed(state, questions, key, observer):
            observer('reserve', {'request': {'model': 'inception/mercury-decide:free'}, 'status': 'reserved'})
            raise RuntimeError('Provider unavailable')
        result = run(self.root / 'bundle.json', self.root / 'analysis', ask=self.ask, decision=failed)
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['equal_mean_probabilities'])
        self.assertEqual(result['reasoning_probabilities'], self.report['probabilities'])

    def test_input_change_blocks_existing_journals(self):
        run(self.root / 'bundle.json', self.root / 'analysis', ask=self.ask, decision=self.decision)
        self.bundle['request']['options'] = ['Short', 'Different']
        save(self.root / 'bundle.json', self.bundle)
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            run(self.root / 'bundle.json', self.root / 'analysis', ask=self.ask, decision=self.decision)

    def test_missing_rules_fail_before_inference_and_labels_are_removed(self):
        post = {'id': 2, 'question': {'id': 1, 'title': 'Match length?', 'type': 'multiple_choice',
            'options': self.options, 'resolution': 'Long', 'aggregations': {'probability': .9}}}
        with self.assertRaisesRegex(ValueError, 'criteria missing'):
            blind_input(post, '1')
        post['question']['resolution_criteria'] = 'Count games'
        request = blind_input(post, '1')
        self.assertNotIn('resolution', request)
        self.assertNotIn('aggregations', request)

    def test_unquoted_facts_and_repeated_options_rejected(self):
        packet = {'evidence': [{'source_id': 'S1', 'text': 'Fourteen games.'}]}
        self.report['facts'][0]['quote'] = 'Invented quote'
        with self.assertRaisesRegex(ValueError, 'Quote'):
            parse({'tool_calls': [{'function': {'name': 'record_categorical', 'arguments': json.dumps(self.report)}}]}, packet, self.options)
        self.report['facts'][0]['quote'] = 'Fourteen games.'
        self.report['option_analysis'][1]['option'] = 'Short'
        with self.assertRaisesRegex(ValueError, 'repeated'):
            parse({'tool_calls': [{'function': {'name': 'record_categorical', 'arguments': json.dumps(self.report)}}]}, packet, self.options)

    def test_rule_reader_requires_named_section_and_excludes_later_sections(self):
        import base64
        from ForecastAgent.readers.metaculus_rules import extract_criteria
        def page(body):
            return {'raw_response_base64': base64.b64encode(body.encode()).decode()}
        source = '<html><body><h1>Match length?</h1><h2>Resolution Criteria</h2><p>Count regulation games.</p><h2>Comments</h2><p>My probability is 90%.</p></body></html>'
        self.assertEqual(extract_criteria(page(source), 'Match length?'), 'Count regulation games.')
        with self.assertRaises(ValueError):
            extract_criteria(page('<body><h1>Match length?</h1><p>Probably count fourteen games.</p></body>'), 'Match length?')


if __name__ == '__main__':
    unittest.main()
