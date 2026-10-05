"""Validate original source reuse, hidden labels and immutable replay inputs."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis import acquisition_replay as trial
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.tests.test_repaired_fifty import decision


class AcquisitionReplayTests(unittest.TestCase):
    def fixture(self, root):
        bundle = {'request': {'id': '1', 'question': 'Will X occur?', 'resolution_criteria': 'X required.',
                  'fine_print': 'Exact exception.', 'resolved_to': 1, 'community_probability': .9},
                  'pages': {'https://example.org': {'content': 'Original event evidence. '*100}},
                  'result': {'analysis': 'Acquisition opinion'}, 'gaps': []}
        save(root/'inputs/tasks/1/analysis-input.json', bundle)
        save(root/'inputs/tasks/1/package.json', {'analysis_input_sha256': digest(bundle)})
        save(root/'manifest.json', {'source_run': 1, 'cases': [{'id': '1', 'bundle_sha256': digest(bundle)}],
             'chain_sha256': hashlib.sha256(Path(trial.chain.__file__).read_bytes()).hexdigest()})
        return bundle

    def test_dry_run_preserves_fine_print_without_labels_or_opinions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); original = self.fixture(root)
            with patch.object(trial.chain, 'call') as http:
                rows = trial.run(root/'inputs', root/'out', root/'manifest.json', dry_run=True)
                http.assert_not_called()
            state = load(root/'out/tasks/1/mercury/first-state.json')
            self.assertEqual(state['question']['fine_print'], 'Exact exception.')
            self.assertNotIn('resolved_to', state['question'])
            self.assertNotIn('community_probability', state['question'])
            self.assertNotIn('result', load(root/'out/tasks/1/analysis-input.json'))
            self.assertEqual(rows[0]['status'], 'prepared')
            save(root/'inputs/tasks/1/analysis-input.json', {**original, 'gaps': ['changed']})
            with self.assertRaisesRegex(ValueError, 'changed'):
                trial.run(root/'inputs', root/'out', root/'manifest.json', dry_run=True)

    def test_evaluation_checks_prediction_before_loading_labels(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.fixture(root)
            def call(state, folder):
                result = decision(.8); save(folder/'response.json', result)
                return result
            with patch.object(trial.chain, 'call', side_effect=call):
                trial.run(root/'inputs', root/'out', root/'manifest.json')
            save(root/'labels.json', {'provenance': {'test': True}, 'labels': [{'id': '1', 'resolved_to': 1}]})
            report = trial.evaluate(root/'out', root/'labels.json', root/'evaluation')
            self.assertAlmostEqual(report['metrics']['brier'], .04)
            task = root/'out/tasks/1/outcome.json'; row = load(task)
            save(task, {**row, 'probability_yes': .1})
            with self.assertRaisesRegex(ValueError, 'probability'):
                trial.evaluate(root/'out', root/'missing-labels.json', root/'evaluation')


if __name__ == '__main__':
    unittest.main()
