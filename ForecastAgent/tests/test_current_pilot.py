"""Budget preservation and temporal-policy checks for the new-case runner."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.current_pilot import prepare, run_one
from ForecastAgent.runtime.temporal_policy import unrestricted


class CurrentPilotTests(unittest.TestCase):
    def test_prepare_is_idempotent_and_lost_consumed_state_is_rejected(self):
        fixture = Path(__file__).parents[1] / 'fixtures/current_new_five_20261001.json'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'TAVILY_API_KEY': 'test', 'OPENROUTER_API_KEY': 'test', 'EXA_API_KEY': 'test'}):
            root = Path(directory)
            prepare(root, fixture)
            path = root / 'tasks/44414/bundle.json'
            first = json.loads(path.read_text(encoding='utf-8'))
            self.assertTrue(unrestricted(first))
            self.assertEqual(first['acquisition_limits']['tavily_basic'], 3)
            self.assertEqual(first['acquisition_limits']['exa_search'], 1)
            first['searches'].append({'query': 'already consumed'})
            first['result'] = {'incomplete': False}
            path.write_text(json.dumps(first), encoding='utf-8')
            prepare(root, fixture)
            current = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(current['searches'], first['searches'])
            with patch('ForecastAgent.current_pilot.run_retrieval') as runner:
                run_one(root, '44414')
                runner.assert_not_called()
            batch_path = root / 'batch.json'
            batch = json.loads(batch_path.read_text(encoding='utf-8'))
            batch['tasks']['44414']['attempts'].append({'status': 'reserved'})
            batch_path.write_text(json.dumps(batch), encoding='utf-8')
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'Consumed task state'):
                prepare(root, fixture)


if __name__ == '__main__':
    unittest.main()
