"""Temporal eligibility and receipt integrity are distinct from outcome accuracy."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.runtime.records import save
from ForecastAgent.research_loop.evaluate_shadow import arm, temporal


class ShadowEvaluationTests(unittest.TestCase):
    def test_missing_public_event_time_is_not_prospective(self):
        audit = temporal('2026-10-11T12:00:00Z', {}, None)
        self.assertFalse(audit['prospective_eligible'])
        self.assertEqual(audit['status'], 'first_public_outcome_unknown')

    def test_official_deadline_and_shadow_eligibility_are_separate(self):
        audit = temporal('2026-10-11T12:00:00Z', {'close_time': '2026-10-05T00:00:00Z'},
            {'provenance': {'first_public_outcome_at': '2026-10-16T00:00:00Z',
                            'first_public_outcome_reviewed': True}})
        self.assertTrue(audit['prospective_eligible'])
        self.assertFalse(audit['official_deadline_eligible'])

    def test_scheduled_resolution_does_not_supply_first_public_event(self):
        audit = temporal('2026-10-11T12:00:00Z', {'scheduled_resolve_time': '2026-10-20T00:00:00Z'},
            {'provenance': {'observed_at': '2026-10-20T00:00:00Z'}})
        self.assertFalse(audit['prospective_eligible'])

    def test_event_at_or_before_forecast_is_leakage_even_if_reviewed(self):
        for event in ('2026-10-11T12:00:00Z', '2026-10-10T12:00:00Z'):
            audit = temporal('2026-10-11T12:00:00Z', {}, {'provenance': {
                'first_public_outcome_at': event, 'first_public_outcome_reviewed': True}})
            self.assertEqual(audit['status'], 'after_public_outcome')
            self.assertFalse(audit['prospective_eligible'])

    def test_unreviewed_time_or_naive_clock_is_not_eligible(self):
        audit = temporal('2026-10-11T12:00:00Z', {}, {'provenance': {
            'first_public_outcome_at': '2026-10-16T00:00:00Z'}})
        self.assertFalse(audit['prospective_eligible'])
        self.assertEqual(temporal('2026-10-11T12:00:00', {}, None)['status'], 'invalid_time_contract')

    def test_different_request_is_rejected_before_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); prepared = {'state': {'q': 1}, 'questions': {}}
            save(root/'decision/http/001.json', {'status': 'received', 'request': {
                'model': 'wrong', 'state': prepared['state'], 'questions': {}}})
            with self.assertRaisesRegex(ValueError, 'does not match frozen'):
                arm(root, prepared, 'inception/mercury-decide:free')

    def test_changed_saved_forecast_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); prepared = {'state': {}, 'questions': {}}
            save(root/'decision/http/001.json', {'status': 'received', 'request': {
                'model': 'inception/mercury-decide:free', **prepared},
                'response': {'model': 'inception/mercury-decide:free', 'answers': {}}})
            save(root/'result.json', {'status': 'completed', 'payload': {'probability_yes': .9}})
            with patch('ForecastAgent.research_loop.evaluate_shadow.decode', return_value={
                    'payload': {'probability_yes': .2}}):
                with self.assertRaisesRegex(ValueError, 'independently decoded'):
                    arm(root, prepared, 'inception/mercury-decide:free')

    def test_missing_or_duplicate_receipt_cannot_be_an_independent_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                arm(Path(tmp), {}, 'inception/mercury-decide:free')


if __name__ == '__main__':
    unittest.main()
