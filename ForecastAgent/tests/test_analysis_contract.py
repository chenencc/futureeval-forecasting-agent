"""Check temporal context preservation and outcome isolation for repair trials."""
import unittest

from ForecastAgent.analysis.referenced import evidence_packet
from ForecastAgent.analysis.pilot import load
from pathlib import Path


class AnalysisContractTests(unittest.TestCase):
    def test_opening_time_survives_packet_but_outcome_does_not(self):
        bundle = {'request': {'id': '1', 'question': 'Will another event occur?',
                              'background': '', 'resolution_criteria': 'Before April.',
                              'market_info_open_datetime': '2025-11-25T13:00:00+00:00',
                              'forecast_due_date': '2026-03-29', 'resolved_to': 0},
                  'pages': {'https://example.org/event': {'content': 'Historical event occurred in March 2025.'}}}
        packet = evidence_packet(bundle)
        self.assertEqual(packet['question']['market_info_open_datetime'],
                         '2025-11-25T13:00:00+00:00')
        self.assertEqual(packet['question']['forecast_due_date'], '2026-03-29')
        self.assertNotIn('resolved_to', packet['question'])

    def test_repair_metadata_contains_only_dates_for_selected_cases(self):
        root = Path(__file__).parents[1] / 'analysis'
        cohort = load(root / 'error_seven_cohort.json')
        metadata = load(root / 'error_seven_time_metadata.json')
        self.assertEqual(set(metadata), {x for batch in cohort['batches'] for x in batch})
        for value in metadata.values():
            self.assertEqual(set(value), {'market_info_open_datetime', 'forecast_due_date'})


if __name__ == '__main__':
    unittest.main()
