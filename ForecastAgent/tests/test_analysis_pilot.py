"""Offline checks for decision semantics, evidence provenance, and replay limits."""
import json
import tempfile
import unittest
from pathlib import Path

from ForecastAgent.analysis.pilot import Journal, prepare, parse_analysis, decision_state, FIELDS
from ForecastAgent.providers.decisions import probability, validate, MODEL


class AnalysisPilotTests(unittest.TestCase):
    def test_whitelist_and_exact_citations(self):
        body = 'The observation for August was 8.5 percent.'
        packet = prepare({'request': {'id': '1', 'question': 'Above eight?', 'resolution': 1, 'community_value': .9},
                          'result': {'probability': 1}, 'pages': {'https://example.org': {'content': body}}})
        self.assertNotIn('resolution', packet['question'])
        self.assertNotIn('community_value', packet['question'])
        report = {k: 'Unknown' for k in FIELDS}
        report.update(facts=[{'source_id': 'S1', 'quote': body, 'claim': 'August observation', 'supports': 'yes'}], gaps=[], ultra_probability_yes=.8)
        message = lambda: {'tool_calls': [{'function': {'name': 'record_analysis', 'arguments': json.dumps(report)}}]}
        self.assertEqual(parse_analysis(message(), packet), report)
        self.assertNotIn('ultra_probability_yes', decision_state(packet, report)['analysis'])
        report['facts'][0]['quote'] = 'An invented quotation not in the page.'
        with self.assertRaises(ValueError):
            parse_analysis(message(), packet)

    def test_nonfinite_and_boolean_probabilities_rejected(self):
        for value in [True, None, '0.5', float('nan'), float('inf'), -1, 1.01]:
            with self.assertRaises(ValueError):
                probability(value)

    def test_decision_semantics_and_identity(self):
        question = {'p': {'type': 'noul'}}
        response = {'model': MODEL, 'answers': {'p': {'type': 'noul', 'noul': .4}}}
        self.assertEqual(validate(response, question), response)
        response['answers']['p'] = {'type': 'score', 'score': .4}
        with self.assertRaises(ValueError):
            validate(response, question)
        response['model'] = 'unapproved-paid-model'
        with self.assertRaises(ValueError):
            validate(response, question)

    def test_reserved_http_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            Journal(directory, 1)('reserve', {'status': 'reserved'})
            with self.assertRaises(RuntimeError):
                Journal(directory, 1)('reserve', {'status': 'reserved'})
            self.assertEqual(len(list(Path(directory).glob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
