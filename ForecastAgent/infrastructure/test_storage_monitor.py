"""Offline acceptance for restart safety and the read-only dispatch gate."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.infrastructure.storage import split, verify

spec = importlib.util.spec_from_file_location('watch', Path(__file__).parent / 'public_monitor/watch.py')
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class Acceptance(unittest.TestCase):
    def test_checkpoint_preserves_pending_budget_and_archives_terminal_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'state'
            for ident in ('1', '2'):
                folder = root / 'tasks' / ident
                folder.mkdir(parents=True)
                (folder / 'bundle.json').write_text(json.dumps({'search_attempts': [1, 2, 3]}))
                (folder / 'submission.json').write_text('{"status":"accepted"}')
            (root / 'campaign.json').write_text(json.dumps({'schema': 'official-competition-v1',
                'tournament': 'fall-futureeval-2026', 'tasks': {
                    '1': {'stage': 'retry_wait', 'collection_executions': 3}, '2': {'stage': 'accepted'}}}))
            first = Path(directory) / 'first'
            report = split(root, first)
            self.assertFalse(report['quota_reset'])
            checkpoint = first / 'checkpoint'
            self.assertTrue((checkpoint / 'tasks/1/bundle.json').exists())
            self.assertFalse((checkpoint / 'tasks/2/bundle.json').exists())
            self.assertTrue((first / 'evidence/tasks/2/bundle.json').exists())
            self.assertTrue((checkpoint / 'tasks/2/submission.json').exists())
            verify(checkpoint)
            second = Path(directory) / 'second'
            self.assertEqual(split(checkpoint, second)['changed_files'], 0)
            (checkpoint / 'tasks/1/bundle.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                verify(checkpoint)

    def test_dispatch_gate(self):
        state = {'tasks': {'1': {'stage': 'accepted'},
                           '2': {'stage': 'retry_wait', 'retry_at_utc': '2099-01-01T00:00:00Z'}}}
        self.assertEqual(watch.decision({'1'}, state), 'idle')
        self.assertEqual(watch.decision({'3'}, state), 'new_questions')
        self.assertEqual(watch.decision({'3'}, state, active=True), 'worker_active')
        self.assertEqual(watch.decision({'1'}, state, heartbeat=True), 'checkpoint_refresh')
        state['tasks']['2']['retry_at_utc'] = '2020-01-01T00:00:00Z'
        self.assertEqual(watch.decision(set(), state), 'pending_due')

    def test_complete_pagination_and_host_guard(self):
        calls = []
        def get(url):
            calls.append(url)
            return {'results': [{'question': {'id': len(calls), 'status': 'open'}}],
                'next': '/api/posts/?offset=100' if len(calls) == 1 else None}
        self.assertEqual(watch.open_ids('unused', get), {'1', '2'})
        with self.assertRaisesRegex(ValueError, 'pagination URL'):
            watch.open_ids('unused', lambda _: {'results': [{'question': {'id': 1, 'status': 'open'}}], 'next': 'https://evil.example/api/posts/'})
        empty_calls = []
        def empty(url):
            empty_calls.append(url)
            return {'results': [], 'next': '/api/posts/?offset=100'}
        self.assertEqual(watch.open_ids('unused', empty), set())
        self.assertEqual(len(empty_calls), 1)


if __name__ == '__main__':
    unittest.main()
