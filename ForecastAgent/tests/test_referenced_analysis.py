"""Evidence-reference provenance and scope-gap regression checks."""
import copy
import hashlib
import json
import unittest
from ForecastAgent.analysis.referenced import evidence_packet, read_saved, parse, materialize, assess, FIELDS


class ReferencedAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.body = 'The July observation was 7.51 percent.\n' * 600
        self.bundle = {'request': {'id': '1', 'question': 'Was the August worldwide share above 8 percent?',
                                   'resolution_criteria': 'August worldwide share must be above 8 percent.', 'resolution': 1},
                       'pages': {'https://example.org': {'content': self.body, 'content_sha256': hashlib.sha256(self.body.encode()).hexdigest()}}}
        self.packet = evidence_packet(self.bundle)
        self.report = {key: 'Unknown' for key in FIELDS}
        self.report.update(reasoning_probability_yes=.4, gaps=['August observation is missing'],
            facts=[{'claim': 'July observation', 'evidence_refs': ['E0001'], 'supports': 'context'}],
            conditions=[{'condition_id': 'C1', 'requirement': 'August worldwide observation', 'evidence_refs': ['E0001'],
                         'coverage': 'partial', 'status': 'uncertain', 'observation_window': 'July only', 'gap': 'August missing'}])

    def message(self):
        return {'tool_calls': [{'function': {'name': 'record_analysis', 'arguments': json.dumps(self.report)}}]}

    def test_program_materializes_exact_spans(self):
        report = parse(self.message(), self.packet)
        for item in materialize(self.packet, report):
            self.assertEqual(self.body[item['start']:item['end']], item['text'])
        self.assertNotIn('resolution', self.packet['question'])

    def test_invented_reference_is_rejected(self):
        self.report['facts'][0]['evidence_refs'] = ['E9999']
        with self.assertRaises(ValueError):
            parse(self.message(), self.packet)

    def test_full_condition_requires_source_reference(self):
        self.report['conditions'][0].update(coverage='full', evidence_refs=[])
        with self.assertRaises(ValueError):
            parse(self.message(), self.packet)

    def test_missing_month_requires_review_without_changing_probability(self):
        result = assess(self.packet, self.report, True)
        self.assertEqual(result['status'], 'review_required')
        self.assertFalse(result['automated_use_eligible'])
        self.assertEqual(self.report['reasoning_probability_yes'], .4)

    def test_local_read_stays_bound_to_saved_body(self):
        added = read_saved(self.bundle, self.packet, {'source_id': 'S1', 'start': 12000, 'length': 2000})
        self.assertEqual(len({e['evidence_id'] for e in self.packet['evidence']}), len(self.packet['evidence']))
        for item in added:
            self.assertEqual(self.body[item['start']:item['end']], item['text'])
        self.bundle['pages']['https://example.org']['content'] += 'changed'
        with self.assertRaises(ValueError):
            read_saved(self.bundle, self.packet, {'source_id': 'S1', 'start': 0, 'length': 100})


if __name__ == '__main__':
    unittest.main()
