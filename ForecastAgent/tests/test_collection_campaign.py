"""Queue, recovery and budget tests without provider calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.collection_campaign import prepare, run_batch, read, write, reconcile, inspect_task


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'campaign'
        self.fixture = Path(self.temporary.name) / 'input.json'
        self.rows = [{'id': str(i + 1), 'question': f'Question {i}', 'resolution_criteria': 'Official criteria',
                      'as_of_utc': '2026-01-01T00:00:00+00:00'} for i in range(100)]
        write(self.fixture, self.rows)
        self.environment = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test', 'TAVILY_API_KEY': 'test',
            'EXA_API_KEY': 'test', 'FORECAST_MODEL': 'nvidia/nemotron-3-ultra-550b-a55b:free'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def runner(self, request, directory, *keys):
        path = directory / 'bundle.json'
        bundle = read(path)
        bundle.setdefault('model_attempts', []).append({'status': 'received', 'usage': {'total_tokens': 100}})
        bundle['searches'].append({'status': 'completed'})
        bundle['exa_searches'].append({'status': 'completed'})
        bundle['result'] = {'incomplete': False, 'acquisition_complete': True}
        bundle['acceptance'] = {'status': 'accepted'}
        write(path, bundle)
        return bundle

    def test_100_queue_closes_in_20_dispatches_and_does_not_repeat(self):
        prepare(self.root, self.fixture)
        for _ in range(20):
            result = run_batch(self.root, 5, self.runner)
        self.assertEqual(result['states'], {'acquired': 100})
        self.assertEqual(result['resources']['model_http'], 100)
        self.assertEqual(result['resources']['tavily_basic'], 100)
        with patch('ForecastAgent.collection_campaign.RetrievalTask') as task:
            run_batch(self.root, 5, self.runner)
            task.assert_not_called()
        self.assertNotIn('as_of_utc', read(self.root / 'campaign.json')['requests']['1'])

    def test_closed_gap_is_not_quality_pass_or_implicitly_reopened(self):
        prepare(self.root, self.fixture, 1)
        def gap(request, directory, *keys):
            bundle = self.runner(request, directory, *keys)
            bundle['result']['acquisition_complete'] = False
            bundle['acceptance']['status'] = 'accepted_with_gaps'
            write(directory / 'bundle.json', bundle)
        result = run_batch(self.root, 1, gap)
        self.assertEqual(result['states'], {'closed_with_gaps': 1})
        with patch('ForecastAgent.collection_campaign.RetrievalTask') as task:
            run_batch(self.root, 1, gap)
            task.assert_not_called()

    def test_lost_consumed_state_refuses_new_budget(self):
        prepare(self.root, self.fixture, 1)
        run_batch(self.root, 1, self.runner)
        (self.root / 'tasks/1/bundle.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Consumed task state missing'):
            run_batch(self.root, 1, self.runner)

    def test_frozen_input_mutation_is_rejected(self):
        prepare(self.root, self.fixture, 1)
        campaign = read(self.root / 'campaign.json')
        campaign['requests']['1']['question'] = 'Changed question'
        write(self.root / 'campaign.json', campaign)
        with self.assertRaisesRegex(ValueError, 'integrity'):
            run_batch(self.root, 1, self.runner)

    def test_crash_reserves_execution_and_next_pending_task_runs_first(self):
        prepare(self.root, self.fixture, 2)
        def fail(*args):
            raise RuntimeError('temporary failure')
        first = run_batch(self.root, 1, fail)
        self.assertEqual(first['states'], {'incomplete': 1, 'pending': 1})
        second = run_batch(self.root, 1, self.runner)
        self.assertEqual(second['states'], {'incomplete': 1, 'acquired': 1})
        self.assertEqual(len(read(self.root / 'campaign.json')['tasks']['1']['attempts']), 1)

    def test_transport_circuit_preserves_rest_of_queue(self):
        prepare(self.root, self.fixture, 3)
        def limited(request, directory, *keys):
            bundle = read(directory / 'bundle.json')
            for i in range(2):
                name = f'limited-{i}.json'
                write(directory / name, {'http_status': 429})
                bundle.setdefault('model_attempts', []).append({'path': name, 'status': 'http_error'})
            bundle['result'] = {'incomplete': True}
            write(directory / 'bundle.json', bundle)
        result = run_batch(self.root, 3, limited)
        self.assertEqual(result['states'], {'incomplete': 1, 'pending': 2})
        self.assertTrue(result['pause_until_utc'])
        with patch('ForecastAgent.collection_campaign.RetrievalTask') as task:
            run_batch(self.root, 3, self.runner)
            task.assert_not_called()

    def test_global_ceiling_reserves_full_dispatch_room(self):
        prepare(self.root, self.fixture, 2)
        campaign = read(self.root / 'campaign.json')
        campaign['limits']['model_http_campaign'] = 16
        write(self.root / 'campaign.json', campaign)
        result = run_batch(self.root, 2, self.runner)
        self.assertEqual(result['states'], {'acquired': 1, 'pending': 1})

    def test_exceeded_search_budget_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'budget'):
            inspect_task({'searches': [{}, {}, {}, {}]})

    def test_excerpt_preview_retains_navigation_and_declares_omission(self):
        from ForecastAgent.runtime.context import excerpt_view
        excerpt = {'id': 'X1', 'url': 'https://example.org', 'need_ids': ['n'],
                   'text': 'a' * 900, 'start_char': 10, 'end_char': 910,
                   'location': {'document_index': 2}, 'source_sha256': 'original'}
        view = excerpt_view(excerpt)
        self.assertTrue(view['preview_is_truncated'])
        self.assertEqual(view['full_chars'], 900)
        self.assertEqual(view['location']['document_index'], 2)
        self.assertEqual(view['end_char'], 910)
        self.assertEqual(len(view['preview']), 800)


if __name__ == '__main__':
    unittest.main()
