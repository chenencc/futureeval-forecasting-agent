"""Cohort integrity and incomplete-denominator controls without provider calls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import save
from ForecastAgent.research_loop import cohort_trial as trial
from ForecastAgent.research_loop import POLICY
from ForecastAgent.research_loop.state import initialize
from ForecastAgent.analysis.pilot import load, digest
from ForecastAgent.tests.test_research_loop import source_bundle


class CohortTests(unittest.TestCase):
    def row(self, root, ident='101', stream='minibench'):
        bundle = source_bundle(); bundle['request']['id'] = int(ident)
        path = root/('input-'+ident+'.json'); save(path, bundle)
        return {'id': ident, 'stream': stream, 'input': str(path),
                'input_sha256': trial.sha(path)}

    def test_preflight_freezes_unique_membership_without_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row = self.row(root)
            identity = trial.freeze({'cases': [row]}, root/'trial', 1)
            self.assertEqual(identity['cases'][0]['preflight'], 'passed')
            self.assertEqual(identity['search_calls'], 0)
            self.assertFalse(identity['submission_enabled'])
            child = load(row['input'])
            child['request']['research_state_policy'] = POLICY
            child['pipeline'] = 'collection'
            initialize(child)
            self.assertEqual(identity['cases'][0]['common_original_state_sha256'],
                digest(trial.decision.prepare(child)['baseline']))
            self.assertEqual(trial.freeze({'cases': [row]}, root/'trial', 1), identity)
            with self.assertRaisesRegex(ValueError, 'unique'):
                trial.freeze({'cases': [row, row]}, root/'other', 1)

    def test_changed_evidence_and_id_cannot_be_silently_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row = self.row(root)
            row['id'] = '102'
            with self.assertRaisesRegex(ValueError, 'does not match'):
                trial.freeze({'cases': [row]}, root/'trial', 1)
            row['id'] = '101'; Path(row['input']).write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                trial.freeze({'cases': [row]}, root/'trial', 1)

    def test_outcome_fields_block_before_any_child_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row = self.row(root)
            bundle = source_bundle(); bundle['request'].update(id=101, resolution='yes')
            save(row['input'], bundle); row['input_sha256'] = trial.sha(row['input'])
            with self.assertRaises(ValueError):
                trial.freeze({'cases': [row]}, root/'trial', 1)

    def test_failed_and_unprocessed_cases_remain_in_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [self.row(root, '101'), self.row(root, '102', 'official')]
            identity = trial.freeze({'cases': rows}, root/'trial', 1)
            case = {'id': '101', 'status': 'partial_or_failed',
                'baseline': {'status': 'completed'}, 'enriched': {'status': 'map_unavailable'}}
            for stage in ('super', 'baseline', 'enriched'):
                case[stage+'_usage'] = {'totals': {'http_attempts': 1}}
            save(root/'trial/shards/0/report.json', {'processed_cases': 1, 'cases': [case]})
            result = trial.aggregate(root/'trial', identity, finished=True, exits={0: 1})
            self.assertEqual(result['status'], 'incomplete')
            self.assertEqual(result['requested_cases'], 2)
            self.assertEqual(result['unprocessed_ids'], ['102'])
            self.assertEqual(result['streams']['official']['requested'], 1)
            self.assertEqual(result['streams']['official']['processed'], 0)
            self.assertIsNone(result['accuracy_metrics'])


if __name__ == '__main__':
    unittest.main()
