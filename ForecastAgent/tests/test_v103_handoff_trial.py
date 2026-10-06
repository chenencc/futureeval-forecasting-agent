"""Durable inference and failed-second-stage behavior of the paired pilot."""

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition import v103_handoff_trial as trial
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.tests.test_material_handoff import fixture


class HandoffTrialTests(TestCase):
    def test_public_case_entry_creates_the_directory_before_acquiring_its_lock(self):
        bundle = fixture('Original Alpha and Beta report.')
        bundle['request']['id'] = '123'
        cohort = {'cases': [{'question_id': '123', 'phase': 'regression', 'route_order': ['release', 'context']}]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'not-created-yet'
            with patch.object(trial, 'inputs', return_value=({}, cohort, {'123': bundle})), \
                 patch.object(trial.chain, 'call', return_value={'answers': {'event_yes': {'noul': .3}}}), \
                 patch.object(trial.chain, 'route', return_value=[]):
                result = trial.run_case(output, '123')
            self.assertEqual(result['arms']['release']['probability_yes'], .3)
            self.assertTrue((output / '123/comparison.json').exists())

    def test_completed_prediction_is_reused_without_reopening_a_provider_stage(self):
        bundle = fixture('Original report Alpha and Beta.\n')
        response = {'answers': {'event_yes': {'noul': .3}}}
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch.object(trial.chain, 'call', return_value=response) as provider, \
                 patch.object(trial.chain, 'route', return_value=[]):
                first = trial.run_arm(bundle, folder, 'release')
                again = trial.run_arm(bundle, folder, 'release')
            self.assertEqual(first, again)
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(first['probability_yes'], .3)

    def test_failed_second_stage_preserves_a_valid_first_probability(self):
        bundle = fixture('Alpha and Beta report.\n\n' * 800)
        response = {'answers': {'event_yes': {'noul': .25}}}
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch.object(trial.chain, 'call', side_effect=[response, RuntimeError('service unavailable')]), \
                 patch.object(trial.chain, 'route', return_value=['time_window']):
                result = trial.run_arm(bundle, folder, 'release')
            self.assertEqual(result['status'], 'completed_with_reread_failure')
            self.assertEqual(result['probability_yes'], .25)
            self.assertIn('service unavailable', result['second_error'])
            self.assertEqual(trial.sealed(folder), result)

    def test_failed_first_stage_stays_failed_and_does_not_fabricate_a_score(self):
        bundle = fixture('Original report.')
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(trial.chain, 'call', side_effect=RuntimeError('account limit')) as provider:
                result = trial.run_arm(bundle, Path(directory), 'context')
                again = trial.run_arm(bundle, Path(directory), 'context')
            self.assertIsNone(result['probability_yes'])
            self.assertEqual(result, again)
            self.assertEqual(provider.call_count, 1)

    def test_modified_prediction_is_rejected_instead_of_rerunning(self):
        bundle = fixture('Original Alpha and Beta report.')
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch.object(trial.chain, 'call', return_value={'answers': {'event_yes': {'noul': .4}}}), \
                 patch.object(trial.chain, 'route', return_value=[]):
                trial.run_arm(bundle, folder, 'release')
            result = load(folder / 'result.json')
            result['probability_yes'] = .9
            save(folder / 'result.json', result)
            with self.assertRaises(ValueError):
                trial.run_arm(bundle, folder, 'release')
