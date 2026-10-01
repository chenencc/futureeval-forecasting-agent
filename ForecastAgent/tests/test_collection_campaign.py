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
        if not bundle['exa_searches']:
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

    def test_large_inventory_group_fits_without_shortening_reading_text(self):
        from ForecastAgent.runtime.context import fit_inventory_group, encode
        reading = {'data': {'content': 'a' * 4000}}
        inventory = {'data': {'located_material': [{'url': 'https://example.org/' + 'x' * 300,
            'passage_id': str(i), 'text': 'b' * 2000} for i in range(8)]}}
        group = [{'role': 'assistant', 'content': None},
                 {'role': 'tool', 'tool_call_id': 'r', 'content': json.dumps(reading)},
                 {'role': 'tool', 'tool_call_id': 'i', 'content': json.dumps(inventory)}]
        fit_inventory_group(group, {'r': {'name': 'read_document'}, 'i': {'name': 'read_sources'}}, 12000)
        self.assertLessEqual(len(encode(group)), 12000)
        self.assertEqual(json.loads(group[1]['content'])['data']['content'], 'a' * 4000)
        item = json.loads(group[2]['content'])['data']['located_material'][0]
        self.assertEqual(item['url'], 'https://example.org/' + 'x' * 300)

    def test_focused_repair_preserves_consumed_searches(self):
        prepare(self.root, self.fixture, 2)
        def partial(request, directory, *keys):
            bundle = self.runner(request, directory, *keys)
            bundle['result'] = {'incomplete': True}
            write(directory / 'bundle.json', bundle)
        run_batch(self.root, 1, partial)
        result = run_batch(self.root, 1, self.runner, question_id='1',
                           resume_reason='Validate inventory projection after a generic delivery repair')
        self.assertEqual(result['states'], {'acquired': 1, 'pending': 1})
        self.assertEqual(result['resources']['tavily_basic'], 2)
        self.assertFalse(read(self.root / 'campaign.json')['repair_resumptions'][0]['budget_reset'])

    def test_explicit_five_case_selection_preserves_other_queue_tasks(self):
        prepare(self.root, self.fixture)
        chosen = ['9', '2', '70', '30', '88']
        visited = []
        def runner(request, directory, *keys):
            visited.append(str(request['id']))
            return self.runner(request, directory, *keys)
        result = run_batch(self.root, 5, runner, question_ids=chosen)
        self.assertEqual(visited, chosen)
        self.assertEqual(result['states'], {'pending': 95, 'acquired': 5})
        campaign = read(self.root / 'campaign.json')
        self.assertFalse(campaign['tasks']['1']['attempts'])
        self.assertEqual(campaign['dispatch_selections'][0]['question_ids'], chosen)
        self.assertFalse((self.root / 'tasks/1').exists())

    def test_new_selection_rejects_consumed_or_duplicate_tasks(self):
        prepare(self.root, self.fixture, 3)
        run_batch(self.root, 1, self.runner, question_ids=['1'])
        with self.assertRaisesRegex(ValueError, 'untouched pending'):
            run_batch(self.root, 1, self.runner, question_ids=['1'])
        with self.assertRaisesRegex(ValueError, 'unique'):
            run_batch(self.root, 2, self.runner, question_ids=['2', '2'])
        with self.assertRaisesRegex(ValueError, 'dispatch limit'):
            run_batch(self.root, 1, self.runner, question_ids=['2', '3'])


if __name__ == '__main__':
    unittest.main()
