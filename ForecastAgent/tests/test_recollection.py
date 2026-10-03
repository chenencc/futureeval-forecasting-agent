"""Offline frozen inputs, no-repeat and supplement recovery acceptance."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent import recollection as module
from ForecastAgent.collection_campaign import prepare, read, write


class RecollectionTests(unittest.TestCase):
    def test_frozen_120_and_no_outcomes(self):
        rows = read(module.FIXTURE)
        self.assertEqual(len(rows), 120)
        self.assertEqual(len({str(row['id']) for row in rows}), 120)
        self.assertEqual(sum('question_format' in row for row in rows), 20)
        for row in rows:
            self.assertFalse(set(row) & {'resolution', 'resolved_to', 'probability'})
        with tempfile.TemporaryDirectory() as directory:
            state = prepare(Path(directory), module.FIXTURE, 120, True)
            self.assertEqual(len(state['tasks']), 120)
            self.assertEqual(state['limits']['tavily_basic_per_task_lifetime'], 3)
            self.assertEqual(state['limits']['exa_per_task_lifetime'], 1)

    def test_completed_handoffs_are_not_repeated_and_changed_code_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def collect(root, limit):
                self.assertEqual(limit, 5)
                state = read(root / 'campaign.json')
                ident = next(iter(state['tasks']))
                state['tasks'][ident]['status'] = 'closed_with_gaps'
                write(root / 'campaign.json', state)
                folder = root / 'tasks' / ident
                folder.mkdir(parents=True, exist_ok=True)
                write(folder / 'bundle.json', {'request': state['requests'][ident]})
                write(root / 'status.json', {'states': {'closed_with_gaps': 1}})
            def repair(bundle, folder, **kwargs):
                folder.mkdir(parents=True, exist_ok=True)
                write(folder / 'analysis-input.json', bundle)
            with patch.object(module, 'run_batch', side_effect=collect), patch.object(module, 'handoff', side_effect=repair) as handoff:
                module.run(root, network=False)
                module.run(root, network=False)
                self.assertEqual(handoff.call_count, 1)
                with patch.dict('os.environ', {'GITHUB_SHA': 'changed'}):
                    with self.assertRaisesRegex(ValueError, 'Frozen experiment changed'):
                        module.run(root, network=False)


if __name__ == '__main__':
    unittest.main()
