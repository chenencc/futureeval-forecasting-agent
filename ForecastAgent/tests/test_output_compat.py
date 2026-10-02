"""Compatibility conversions must not modify predictions or factual evidence."""
import copy
import json
import unittest

from ForecastAgent.analysis.output_compat import normalize


class OutputCompatibilityTests(unittest.TestCase):
    def test_only_equivalent_container_and_unknown_status_change(self):
        report = {'gaps': 'Source unavailable.', 'conditions': [
            {'status': 'unknown', 'coverage': 'partial', 'evidence_refs': ['E9999']}],
            'reasoning_probability_yes': .35, 'facts': [{'claim': 'Unchanged'}]}
        raw = {'tool_calls': [{'function': {'name': 'record_analysis',
                                         'arguments': json.dumps(report)}}]}
        original = copy.deepcopy(raw)
        result, changes = normalize(raw)
        actual = json.loads(result['tool_calls'][0]['function']['arguments'])
        self.assertEqual(raw, original)
        self.assertEqual(actual['gaps'], ['Source unavailable.'])
        self.assertEqual(actual['conditions'][0]['status'], 'uncertain')
        self.assertEqual(actual['conditions'][0]['evidence_refs'], ['E9999'])
        self.assertEqual(actual['reasoning_probability_yes'], .35)
        self.assertEqual(actual['facts'], report['facts'])
        self.assertEqual(len(changes), 2)

    def test_missing_or_malformed_tool_remains_invalid(self):
        for raw in ({'content': ''}, {'tool_calls': [{'function': {
                'name': 'record_analysis', 'arguments': 'invalid'}}]}):
            self.assertEqual(normalize(raw), (raw, []))

    def test_unsupported_enum_is_not_inferred(self):
        raw = {'tool_calls': [{'function': {'name': 'record_analysis',
               'arguments': json.dumps({'conditions': [{'status': 'true'}], 'gaps': []})}}]}
        self.assertEqual(normalize(raw), (raw, []))


if __name__ == '__main__':
    unittest.main()
