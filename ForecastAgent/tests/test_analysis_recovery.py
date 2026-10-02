"""Bounded salvage must not invent citations, certainty or model probabilities."""
import copy
import json
import unittest
from ForecastAgent.analysis.recovery import recover, task_result
from ForecastAgent.analysis.referenced import materialize, read_saved
from ForecastAgent.analysis import ensemble
from ForecastAgent.tests import test_referenced_analysis


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        test_referenced_analysis.ReferencedAnalysisTests.setUp(self)

    def message(self, report):
        return {'tool_calls': [{'function': {'name': 'record_analysis', 'arguments': json.dumps(report)}}]}

    def test_missing_reference_retains_number_but_withholds_interpretation(self):
        self.report['facts'][0]['evidence_refs'] = []
        self.report['conditions'][0]['evidence_refs'] = ['E0001', 'S1', 'E9999']
        original = copy.deepcopy(self.report)
        report, audit = recover(self.message(self.report), self.packet, 'Missing references')
        self.assertEqual(report['reasoning_probability_yes'], .4)
        self.assertEqual(self.report, original)
        self.assertEqual(report['conditions'][0]['coverage'], 'unknown')
        self.assertEqual(report['conditions'][0]['evidence_refs'], ['E0001'])
        self.assertNotIn('July observation', report['case_for_yes'])
        self.assertEqual(len(audit['quarantined_references']), 3)
        self.assertEqual(materialize(self.packet, report)[0]['text'], self.packet['evidence'][0]['text'])

    def test_nonfinite_and_no_numeric_output_cannot_be_recovered(self):
        self.report['reasoning_probability_yes'] = float('nan')
        self.assertIsNone(recover(self.message(self.report), self.packet, 'Invalid'))
        self.assertIsNone(recover({'content': 'Probably yes'}, self.packet, 'Invalid'))

    def test_identical_reads_do_not_grow_evidence_library(self):
        arguments = {'source_id': 'S1', 'start': 12000, 'length': 2000}
        first = read_saved(self.bundle, self.packet, arguments)
        size = len(self.packet['evidence'])
        second = read_saved(self.bundle, self.packet, arguments)
        self.assertEqual(first, second)
        self.assertEqual(size, len(self.packet['evidence']))

    def test_default_is_not_a_model_forecast_and_integrity_blocks_it(self):
        result = task_result('1', error='Provider unavailable')
        self.assertEqual(result['operational_probability_yes'], .5)
        self.assertIsNone(result['reasoning_probability_yes'])
        self.assertEqual(result['operational_origin'], 'uninformed_workflow_default')
        self.assertIsNone(task_result('1', error='Input hash mismatch')['operational_probability_yes'])
        self.assertFalse(result['automated_use_eligible'])

    def test_partial_and_provisional_have_separate_statuses(self):
        routes = ensemble.compare(.4)
        self.assertEqual(task_result('1', routes)['status'], 'partial')
        routes['quality'] = {'recovery_applied': True}
        self.assertEqual(task_result('1', routes)['status'], 'provisional')


if __name__ == '__main__':
    unittest.main()
