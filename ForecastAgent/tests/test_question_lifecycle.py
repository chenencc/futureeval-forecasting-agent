"""Official status semantics and complete group discovery, without network."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.competition.lifecycle import describe
from ForecastAgent.competition.queue import descriptor, load
from ForecastAgent.monitor_tournament import snapshot_questions

NOW = '2026-10-02T08:00:00Z'


class LifecycleTests(unittest.TestCase):
    def question(self, status):
        return {'id': 7, 'type': 'binary', 'status': status,
            'open_time': '2026-10-01T14:00:00Z', 'scheduled_close_time': '2026-10-01T17:00:00Z',
            'scheduled_resolve_time': '2026-12-02T23:00:00Z', 'resolution': None}

    def test_closed_does_not_mean_resolved(self):
        result = describe({'curation_status': 'approved'}, self.question('closed'), NOW)
        self.assertEqual(result['lifecycle'], 'closed_waiting_resolution')
        self.assertFalse(result['open'])
        self.assertFalse(result['resolution_overdue'])

    def test_hidden_outcome_does_not_override_resolved_status(self):
        result = describe({}, self.question('resolved'), NOW)
        self.assertEqual(result['lifecycle'], 'resolved')

    def test_upcoming_and_unapproved_are_not_forecastable(self):
        self.assertFalse(describe({}, self.question('upcoming'), NOW)['open'])
        self.assertFalse(describe({'curation_status': 'pending'}, self.question('open'), NOW)['open'])
        self.assertFalse(describe({}, {'id': 7}, NOW)['open'])

    def test_resolution_date_is_not_submission_deadline(self):
        question = self.question('open')
        question['scheduled_close_time'] = '2026-10-02T17:00:00Z'
        question['actual_close_time'] = '2026-10-02T15:00:00Z'
        question['spot_scoring_time'] = '2026-10-02T14:00:00Z'
        result = descriptor({'id': 42}, question)
        self.assertEqual(result['submission_deadline_utc'], '2026-10-02T15:00:00+00:00')
        self.assertEqual(result['deadline_utc'], '2026-10-02T14:00:00+00:00')

    def test_overdue_resolution_is_still_closed(self):
        question = self.question('closed')
        question['scheduled_resolve_time'] = '2026-10-01T18:00:00Z'
        result = describe({}, question, NOW)
        self.assertTrue(result['resolution_overdue'])
        self.assertEqual(result['lifecycle'], 'closed_waiting_resolution')

    def test_monitor_expands_group_beyond_list_cutoff(self):
        children = [{'id': i, 'title': f'Question {i}', 'type': 'binary', 'status': 'open',
            'resolution_criteria': 'Official announcement', 'scheduled_close_time': '2030-01-01T00:00:00Z'} for i in range(1, 6)]
        partial = {'id': 42, 'title': 'Group', 'status': 'open', 'group_of_questions': {'questions': children[:3]}}
        full = {**partial, 'group_of_questions': {'questions': children}}
        with tempfile.TemporaryDirectory() as temp, patch('ForecastAgent.monitor_tournament.get_json',
                side_effect=[{'results': [partial], 'next': None}, {'results': [partial], 'next': None}, full]) as fetch:
            directory = snapshot_questions('offline-token', Path(temp))
            index = load(directory / 'index.json')
            self.assertEqual(index['question_count'], 5)
            self.assertEqual(index['lifecycle_counts']['open'], 5)
            self.assertIn('/42/', fetch.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
