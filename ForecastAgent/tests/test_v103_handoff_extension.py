"""Ensure a new cohort cannot alter the prior paired engine or per-arm limits."""

from unittest import TestCase

from ForecastAgent.acquisition import v103_handoff_extension as extension
from ForecastAgent.acquisition import v103_handoff_trial as trial
from ForecastAgent.analysis.pilot import load


class ExtensionTests(TestCase):
    def test_new_cohort_binding_is_restored_even_after_failure(self):
        prior = tuple(getattr(trial, k) for k in ('COHORT', 'FIXTURE', 'PROTOCOL', 'LABELS'))
        with self.assertRaises(RuntimeError):
            with extension.experiment() as engine:
                self.assertIs(engine, trial)
                self.assertEqual(engine.COHORT, extension.COHORT)
                self.assertEqual(engine.LABELS, {'extension': extension.LABELS})
                raise RuntimeError('local test')
        self.assertEqual(prior, tuple(getattr(trial, k) for k in ('COHORT', 'FIXTURE', 'PROTOCOL', 'LABELS')))

    def test_frozen_protocol_preserves_original_sources_and_policy(self):
        old = load(extension.ORIGINAL_PROTOCOL)
        new = load(extension.PROTOCOL)
        for field in ('model', 'endpoint', 'registry_sha256', 'routing', 'fixed_analysis'):
            self.assertEqual(old[field], new[field])
        for name, expected in old['experiment_sources_sha256_lf'].items():
            self.assertEqual(new['experiment_sources_sha256_lf'][name], expected)
            self.assertEqual(trial.source_sha(trial.ROOT / name), expected)
        for field in ('physical_http_per_arm', 'physical_http_per_question', 'first_bytes', 'second_bytes'):
            self.assertEqual(old['limits'][field], new['limits'][field])
        self.assertEqual(new['limits']['campaign_http'], 40)
        old_ids = {r['question_id'] for r in load(trial.COHORT)['cases']}
        new_ids = {r['question_id'] for r in load(extension.COHORT)['cases']}
        self.assertEqual(len(new_ids), 10)
        self.assertFalse(old_ids & new_ids)
