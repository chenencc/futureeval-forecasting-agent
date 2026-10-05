"""Fresh question-only cohort and immutable continuation acceptance."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import save, load
from ForecastAgent.experiments import unified_acquisition_trial as trial


class UnifiedTrialTests(unittest.TestCase):
    def test_all_five_reach_v7_and_completed_resume_spends_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            original = load(trial.MANIFEST)
            calls = []
            def collect(request, folder, **kwargs):
                calls.append(request['id'])
                self.assertEqual(request['budget_profile'], 'default')
                save(folder / 'analysis-input.json', {'request': request, 'pages': {'https://example.org': {'content': 'Saved body'}}})
                return {'readable_unique_pages': 0, 'gaps': ['No bodies'], 'state': 'collected_with_gaps'}
            def review(output, key, manifest, **kwargs):
                data = load(manifest)
                self.assertEqual(data['arms'], ['v7'])
                self.assertEqual(len(data['cases']), 5)
                self.assertEqual(data['cases'][0]['plan'], original['cases'][0]['plan'])
                result = {'business_gate_passed': True, 'actual_http_attempts': 15, 'logical_decisions': 15,
                    'blocked': False, 'pending_case_ids': [],
                    'cases': [{'case_id': c['id'], 'pair_complete': True, 'arms': {'v7': {'status': 'received'}}}
                              for c in data['cases']]}
                save(output / 'result.json', result)
                return result
            with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'mock', 'FORECAST_SHARED_CACHE_ROOT': ''}), \
                    patch.object(trial, 'collect', side_effect=collect), patch.object(trial.review, 'run', side_effect=review):
                result = trial.run(root / 'first')
                self.assertTrue(result['complete'])
                self.assertEqual(len(calls), 5)
                trial.run(root / 'resumed', resume=root / 'first')
                # Production collect() caches packages; the mock counts entry calls.
                self.assertEqual(len(calls), 10)
            with self.assertRaises(ValueError):
                trial.run(root / 'first')

    def test_no_body_or_search_seed_and_identity_freeze(self):
        data = load(trial.MANIFEST)
        self.assertEqual(len(data['cases']), 5)
        for case in data['cases']:
            self.assertEqual(set(case), {'id', 'question_id', 'request', 'plan'})
            self.assertFalse({'pages', 'searches', 'resolution', 'outcome'} & set(case['request']))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.json'
            data['cases'][0]['bundle'] = {'pages': {}}
            save(path, data)
            with self.assertRaises(ValueError):
                trial.run(Path(folder) / 'output', path)


if __name__ == '__main__':
    unittest.main()
