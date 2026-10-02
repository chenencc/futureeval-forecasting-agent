"""Offline live-operation acceptance: no external models or platform writes."""
import copy
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.competition import live, platform
from ForecastAgent.competition.queue import load, save, utc


class FakePlatform:
    def __init__(self):
        self.posts = 0
        self.private = []
        self.fail_after_accept = False
        self.document = {'id': 42, 'title': 'Will the event occur?', 'user_permission': 'forecaster',
            'question': {'id': 7, 'type': 'binary', 'status': 'open',
                'resolution_criteria': 'YES if the official announcement occurs.',
                'scheduled_close_time': (utc() + timedelta(days=1)).isoformat()}}

    def account(self):
        return {'id': platform.BOT_ID, 'api_forecasting_access': 'enabled'}

    def post(self, ident):
        return copy.deepcopy(self.document)

    def comments(self, ident):
        return self.private

    def request(self, method, endpoint, body):
        self.posts += 1
        forecast = body['forecasts'][0]
        self.document['question']['my_forecasts'] = {'latest': {
            'author_id': platform.BOT_ID, 'forecast_values': [1-forecast['probability_yes'], forecast['probability_yes']],
            'start_time': utc().timestamp()}}
        self.private.append({'id': 91, 'text': body['comments'][0]['text'],
            'author': {'id': platform.BOT_ID}, 'is_private': True})
        if self.fail_after_accept:
            raise TimeoutError('Response lost after atomic acceptance')
        return {'status': 201, 'data': {}}


class OfficialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = FakePlatform()
        self.task = {'id': '7', 'post_id': '42'}

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        folder = self.root / 'incoming'
        save(folder / '42.json', {'post': self.client.document})
        save(folder / 'index.json', {'tournament': 'fall-futureeval-2026',
            'open_scan_complete': True, 'retrieved_at_utc': utc().isoformat(), 'open_question_count': 1,
            'questions': [{'question_id': 7, 'post_id': 42, 'open': True}]})
        return folder

    def test_atomic_readback_and_no_duplicate(self):
        folder = self.root / 'delivery'
        candidate = {'question': 7, 'probability_yes': .63}
        first = platform.deliver(self.client, self.task, candidate, 'Automatic reasoning', folder, enabled=True)
        self.assertEqual(first['status'], 'accepted')
        platform.deliver(self.client, self.task, candidate, 'Automatic reasoning', folder, enabled=True)
        self.assertEqual(self.client.posts, 1)
        self.assertEqual(first['comment_id'], 91)

    def test_response_lost_reconciles_even_when_closed(self):
        self.client.fail_after_accept = True
        folder = self.root / 'delivery'
        candidate = {'question': 7, 'probability_yes': .63}
        with self.assertRaises(TimeoutError):
            platform.deliver(self.client, self.task, candidate, 'Reasoning', folder, enabled=True)
        self.client.document['question']['status'] = 'closed'
        result = platform.deliver(self.client, self.task, candidate, 'Reasoning', folder, enabled=True)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(self.client.posts, 1)

    def test_unknown_unaccepted_record_never_reposts(self):
        folder = self.root / 'delivery'
        candidate = {'question': 7, 'probability_yes': .63}
        def timeout(*args):
            raise TimeoutError('No acknowledgment')
        self.client.request = timeout
        with self.assertRaises(TimeoutError):
            platform.deliver(self.client, self.task, candidate, 'Reasoning', folder, enabled=True)
        with self.assertRaisesRegex(RuntimeError, 'duplicate POST'):
            platform.deliver(self.client, self.task, candidate, 'Reasoning', folder, enabled=True)
        self.assertEqual(load(folder / 'submission.json')['status'], 'unknown')

    def test_delivery_gate_and_frozen_candidate(self):
        folder = self.root / 'delivery'
        candidate = {'question': 7, 'probability_yes': .63}
        with self.assertRaises(ValueError):
            platform.deliver(self.client, self.task, candidate, 'Reasoning', folder)
        platform.deliver(self.client, self.task, candidate, 'Reasoning', folder, enabled=True)
        with self.assertRaisesRegex(ValueError, 'changed'):
            platform.deliver(self.client, self.task, {'question': 7, 'probability_yes': .4}, 'Reasoning', folder, enabled=True)

    def test_private_comment_is_required(self):
        candidate = {'question': 7, 'probability_yes': .63}
        folder = self.root / 'delivery'
        platform.deliver(self.client, self.task, candidate, 'Reasoning', folder, enabled=True)
        record = load(folder / 'submission.json')
        self.client.private[0]['is_private'] = False
        self.assertFalse(platform.reconcile(self.client, self.task, record))

    def test_category_and_cdf_readback(self):
        question = {'options': ['B', 'A'], 'all_options_ever': ['A', 'old', 'B'],
            'my_forecasts': {'latest': {'author_id': platform.BOT_ID, 'forecast_values': [.4, None, .6]}}}
        self.assertTrue(platform.forecast_matches(question, {'probability_yes_per_category': {'A': .4, 'B': .6}}))
        question['my_forecasts']['latest']['forecast_values'] = [0, .4, 1]
        self.assertTrue(platform.forecast_matches(question, {'continuous_cdf': [0, .4, 1]}))

    def test_complete_live_chain_and_resume(self):
        incoming = self.snapshot()
        calls = []
        def collect(request, folder):
            calls.append('collect')
            bundle = {'request': request, 'result': {'incomplete': False}}
            save(folder / 'bundle.json', bundle)
            return bundle
        def infer(bundle, folder, ident):
            calls.append('infer')
            candidate = {'payload': {'question': 7, 'probability_yes': .63}, 'comment': 'Automatic reasoning'}
            save(folder / 'candidate.json', candidate)
            return candidate
        args = dict(enabled=True, client=self.client, collect=collect,
            supplement=lambda b, f, i: b, infer=infer)
        report = live.run(self.root / 'official', incoming, **args)
        self.assertEqual(report['state_distribution'], {'accepted': 1})
        live.run(self.root / 'official', incoming, **args)
        self.assertEqual(calls, ['collect', 'infer'])
        self.assertEqual(self.client.posts, 1)

    def test_closed_question_does_not_acquire_or_submit(self):
        incoming = self.snapshot()
        self.client.document['question']['status'] = 'closed'
        report = live.run(self.root / 'official', incoming, enabled=True, client=self.client,
            collect=lambda *a: self.fail('Closed question collected'))
        self.assertEqual(report['state_distribution'], {'closed': 1})
        self.assertEqual(self.client.posts, 0)

    def test_collection_cap_preserved(self):
        incoming = self.snapshot()
        calls = []
        def collect(request, folder):
            calls.append(1)
            return {'request': request, 'result': {'incomplete': True}}
        for _ in range(4):
            live.run(self.root / 'official', incoming, enabled=True, client=self.client, collect=collect)
            path = self.root / 'official' / 'campaign.json'
            state = load(path)
            state['tasks']['7']['retry_at_utc'] = None
            save(path, state)
        self.assertEqual(len(calls), 3)
        self.assertEqual(load(path)['tasks']['7']['stage'], 'provider_blocked')

    def test_mercury_unavailable_uses_valid_reasoning_only(self):
        for mercury, expected in [(None, .98), (.2, .6)]:
            folder = self.root / str(mercury)
            source = folder / 'bundle.json'
            save(source, {'request': {'id': '7', 'question_type': 'binary'}, 'result': {}})
            def fake_run(root, output, ids, **kwargs):
                self.assertTrue(kwargs['live'])
                save(output / 'tasks' / '7' / 'result.json', {'reasoning_probability_yes': 1., 'mercury_probability_yes': mercury})
                save(output / 'tasks' / '7' / 'analysis.json', {'summary': 'Automatic evidence analysis'})
                if mercury is None:
                    raise RuntimeError('Mercury unavailable; valid reasoning retained')
            with patch('ForecastAgent.analysis.referenced.run', fake_run):
                result = live.analyze(source, folder, '7')
            self.assertAlmostEqual(result['payload']['probability_yes'], expected)

    def test_account_permission_and_rules_guard(self):
        self.task['rule_identity'] = live.rule_identity(self.client.document, self.client.document['question'])
        self.client.document['question']['resolution_criteria'] = 'Changed condition'
        with self.assertRaisesRegex(ValueError, 'changed'):
            live.current(self.client, self.task)
        with patch.object(platform.Client, 'request', return_value={'data': {'id': 1}}):
            with self.assertRaises(ValueError):
                platform.Client('offline-dummy').account()


if __name__ == '__main__':
    unittest.main()
