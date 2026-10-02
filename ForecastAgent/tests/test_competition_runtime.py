"""Operational tests for deadline scheduling, frozen inputs and crash recovery."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from ForecastAgent.competition.queue import Queue, descriptor, load, save
from ForecastAgent.competition.bridge import freeze, validate_frozen
from ForecastAgent.competition.worker import drain

NOW = '2026-10-02T10:00:00Z'


class CompetitionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.collector = self.root / 'collector'

    def snapshot(self, specs):
        directory = self.root / 'snapshot'
        rows = []
        for ident, kind, deadline, status in specs:
            question = {'id': int(ident), 'type': kind, 'status': status, 'title': f'Question {ident}',
                'resolution_criteria': 'Event occurs', 'fine_print': '', 'scheduled_close_time': deadline,
                'spot_scoring_time': deadline, 'resolution': 'yes', 'community_prediction': .9}
            post = {'id': int(ident) + 100, 'question': question}
            save(directory / f"{post['id']}.json", {'post': post, 'retrieved_at_utc': NOW})
            rows.append({'post_id': post['id'], 'question_id': int(ident), 'type': kind, 'status': status})
        save(directory / 'index.json', {'tournament': 'fall-futureeval-2026', 'retrieved_at_utc': NOW,
            'open_scan_complete': True, 'archive_cycle_complete': True, 'questions': rows})
        Queue(self.state).ingest(directory, NOW)
        return directory

    def bundle(self, ident):
        body = 'Official evidence of the event and its date.'
        value = {'request': {'question': f'Question {ident}', 'resolution_criteria': 'Event occurs',
            'fine_print': '', 'mode': 'live'}, 'result': {'status': 'complete', 'incomplete': False},
            'pages': {'https://example.org': {'content': body, 'content_sha256': hashlib.sha256(body.encode()).hexdigest(),
                                           'retrieved_at_utc': NOW}},
            'search_budget': {'reserved': 3}, 'exa_budget': {'reserved': 1}}
        save(self.collector / 'retrieval' / ident / 'bundle.json', value)
        return value

    def refresh(self, task):
        return dict(task['question'])

    def supplement(self, archive, output, ids, **kwargs):
        save(Path(output) / 'manifest.json', {'ids': ids})

    def analysis(self, root, output, ids, *args):
        ident = ids[0]
        target = Path(output) / 'tasks' / ident
        save(target / 'result.json', {'status': 'completed', 'equal_mean_probability_yes': .6,
                                     'reasoning_probability_yes': .7})
        save(target / 'routes.json', {'quality': {'status': 'review_required'}})

    def run_worker(self, **kwargs):
        return drain(self.state, self.collector, execute=True, now=NOW, refresh=self.refresh,
                     analyze=kwargs.pop('analyze', self.analysis), supplement=self.supplement, **kwargs)

    def test_all_types_visible_and_earliest_deadline_first(self):
        self.snapshot([('1', 'binary', '2026-10-03T10:00:00Z', 'open'),
            ('2', 'binary', '2026-10-02T14:00:00Z', 'open'),
            ('3', 'numeric', '2026-10-03T10:00:00Z', 'open'),
            ('4', 'binary', '2026-10-02T09:00:00Z', 'open')])
        queue = Queue(self.state)
        self.assertEqual([t['id'] for t in queue.pending(NOW)], ['2', '1'])
        self.assertEqual(queue.state['tasks']['3']['stage'], 'unsupported_type')
        self.assertEqual(queue.state['tasks']['4']['stage'], 'deadline_missed')
        self.assertNotIn('resolution', queue.state['tasks']['1']['question'])

    def test_group_child_and_spot_deadline(self):
        snapshot = self.snapshot([('1', 'binary', '2026-10-03T10:00:00Z', 'open')])
        path = snapshot / '101.json'
        value = load(path)
        question = value['post'].pop('question')
        question['spot_scoring_time'] = '2026-10-02T11:00:00Z'
        value['post']['group_of_questions'] = {'questions': [question]}
        save(path, value)
        Queue(self.state).ingest(snapshot, NOW)
        self.assertEqual(Queue(self.state).state['tasks']['1']['question']['deadline_utc'], '2026-10-02T11:00:00+00:00')

    def test_rule_change_blocks_and_preserves_attempts(self):
        snapshot = self.snapshot([('1', 'binary', '2026-10-03T10:00:00Z', 'open')])
        queue = Queue(self.state)
        queue.state['tasks']['1']['attempts'] = 2
        queue.commit()
        value = load(snapshot / '101.json')
        value['post']['question']['resolution_criteria'] = 'Different rule'
        save(snapshot / '101.json', value)
        Queue(self.state).ingest(snapshot, NOW)
        task = Queue(self.state).state['tasks']['1']
        self.assertEqual(task['stage'], 'blocked_integrity')
        self.assertEqual(task['attempts'], 2)

    def test_freeze_replay_and_tamper_detection(self):
        self.snapshot([('1', 'binary', '2026-10-03T10:00:00Z', 'open')])
        original = self.bundle('1')
        task = Queue(self.state).state['tasks']['1']
        target = self.state / 'tasks' / '1' / 'input'
        freeze(task, self.collector, target, NOW)
        freeze(task, self.collector, target, NOW)
        self.assertEqual(load(self.collector / 'retrieval/1/bundle.json'), original)
        self.assertEqual(load(target / 'tasks/1/bundle.json')['search_budget']['reserved'], 3)
        adopted = load(target / 'tasks/1/bundle.json')
        adopted['pages']['https://example.org']['content'] = 'Tampered'
        save(target / 'tasks/1/bundle.json', adopted)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            validate_frozen(target)

    def test_capture_after_deadline_rejected(self):
        self.snapshot([('1', 'binary', '2026-10-03T10:00:00Z', 'open')])
        value = self.bundle('1')
        value['pages']['https://example.org']['retrieved_at_utc'] = '2026-10-04T00:00:00Z'
        save(self.collector / 'retrieval/1/bundle.json', value)
        with self.assertRaisesRegex(ValueError, 'timestamp'):
            freeze(Queue(self.state).state['tasks']['1'], self.collector, self.state / 'tasks/1/input', NOW)

    def test_failure_does_not_block_next_and_reuses_journal(self):
        self.snapshot([('1', 'binary', '2026-10-03T10:00:00Z', 'open'),
                       ('2', 'binary', '2026-10-03T11:00:00Z', 'open')])
        self.bundle('1')
        self.bundle('2')
        def fail_first(root, output, ids, *args):
            if ids == ['1']:
                save(Path(output) / 'tasks/1/ultra-http/1.json', {'reserved': True})
                raise RuntimeError('Provider unavailable')
            self.analysis(root, output, ids, *args)
        self.run_worker(analyze=fail_first)
        queue = Queue(self.state)
        self.assertEqual(queue.state['tasks']['1']['stage'], 'retry_wait')
        self.assertEqual(queue.state['tasks']['2']['stage'], 'shadow_ready')
        journal = self.state / 'tasks/1/analysis/tasks/1/ultra-http/1.json'
        self.assertEqual(load(journal), {'reserved': True})
        queue.state['tasks']['1']['retry_at_utc'] = None
        queue.commit()
        self.run_worker()
        self.assertEqual(load(journal), {'reserved': True})
        self.assertEqual(Queue(self.state).state['tasks']['1']['stage'], 'shadow_ready')

    def test_missing_earlier_collection_does_not_starve_ready_task(self):
        self.snapshot([('1', 'binary', '2026-10-03T10:00:00Z', 'open'),
                       ('2', 'binary', '2026-10-03T11:00:00Z', 'open')])
        self.bundle('2')
        self.run_worker(limit=1)
        self.assertEqual(Queue(self.state).state['tasks']['2']['stage'], 'shadow_ready')

    def test_deadline_changed_during_analysis_preserves_output_without_candidate(self):
        self.snapshot([('1', 'binary', '2026-10-03T10:00:00Z', 'open')])
        self.bundle('1')
        def complete_then_close(root, output, ids, *args):
            self.analysis(root, output, ids, *args)
            self.refresh = lambda task: {**task['question'], 'status': 'closed'}
        drain(self.state, self.collector, execute=True, now=NOW, refresh=lambda t: self.refresh(t),
              analyze=complete_then_close, supplement=self.supplement)
        self.assertEqual(Queue(self.state).state['tasks']['1']['stage'], 'closed')
        self.assertTrue((self.state / 'tasks/1/analysis/tasks/1/result.json').exists())
        self.assertFalse((self.state / 'tasks/1/candidate.json').exists())

    def test_missing_queue_refuses_budget_restart(self):
        (self.state / 'tasks/1').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'budget reset'):
            Queue(self.state)

    def test_incomplete_scan_refused_without_mutating_queue(self):
        snapshot = self.snapshot([('1', 'binary', '2026-10-03T10:00:00Z', 'open')])
        before = (self.state / 'queue.json').read_bytes()
        value = load(snapshot / 'index.json')
        value['open_scan_complete'] = False
        save(snapshot / 'index.json', value)
        with self.assertRaises(ValueError):
            Queue(self.state).ingest(snapshot, NOW)
        self.assertEqual((self.state / 'queue.json').read_bytes(), before)

    def test_real_local_supplement_accepts_bridge_without_search(self):
        from ForecastAgent.supplement.stage import run
        from ForecastAgent.competition.bridge import archive_input
        self.snapshot([('1', 'binary', '2026-10-03T10:00:00Z', 'open')])
        original = self.bundle('1')
        target = self.state / 'tasks/1/input'
        freeze(Queue(self.state).state['tasks']['1'], self.collector, target, NOW)
        output = self.state / 'tasks/1/supplement'
        run(archive_input(target), output, ['1'], network=False)
        run(archive_input(target), output, ['1'], network=False)
        self.assertTrue((output / 'manifest.json').exists())
        self.assertEqual(load(self.collector / 'retrieval/1/bundle.json'), original)

    def test_empty_dry_worker_creates_state_directory(self):
        report = drain(self.state, self.collector, now=NOW)
        self.assertEqual(report['question_count'], 0)
        self.assertTrue((self.state / 'queue.json').exists())


if __name__ == '__main__':
    unittest.main()
