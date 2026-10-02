"""Verify the isolated experiment cannot silently switch models or decision routes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis import free_model_trial as trial
from ForecastAgent.analysis.pilot import load


class FreeModelTrialTests(unittest.TestCase):
    def test_selected_model_must_match_backend(self):
        with patch.object(trial, 'configured_model', return_value='other:free'):
            with self.assertRaises(ValueError):
                trial.run('inputs', 'supplements', 'output', 1, 'qwen')

    def test_analysis_only_preserves_repair_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(trial, 'configured_model', return_value=trial.MODELS['qwen']), \
                    patch.object(trial.referenced, 'run') as runner:
                trial.run('inputs', 'supplements', temp, 2, 'qwen')
            args, kwargs = runner.call_args
            self.assertEqual(args[2], ['42485', '43491'])
            self.assertEqual(kwargs['mode'], 'reasoning_only')
            self.assertTrue(kwargs['audit_contract'])
            self.assertNotIn('resolution', str(kwargs['question_metadata']))
            report = load(Path(temp) / 'batch-report.json')
            self.assertFalse(report['identity']['fallback_enabled'])
            self.assertEqual(report['identity']['http_cap_per_question'], 3)
            self.assertTrue(report['no_decision_model_calls'])


if __name__ == '__main__':
    unittest.main()
