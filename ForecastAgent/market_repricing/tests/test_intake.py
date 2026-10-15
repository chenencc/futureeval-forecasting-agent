"""New listing intake, bounded classification and non-starvation regression."""
import copy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing import intake
from ForecastAgent.market_repricing.common import digest, instant, policy, read, write
from ForecastAgent.market_repricing.monitor import discover_events, enroll, ingest, ingest_window, metadata_turns, quote_turns, run
from ForecastAgent.market_repricing.tests.test_repricing import CLOCK, raw_poly
from ForecastAgent.market_repricing.venues import normalize, refresh, screen


def unknown(ident='new'):
    return raw_poly() | {'id': ident, 'question': 'Will Acme resume supplying printed books by December 10, 2026?',
        'description': 'YES if Acme resumes supplying printed books by December 10, 2026. Otherwise NO.',
        'events': [{'id': ident}]}


def answer(choice='event', confidence=.99):
    def decide(state, questions, key, observer):
        token = observer('reserve', {'status': 'reserved', 'request': state})
        response = {'model': intake.decisions.MODEL, 'answers': {ident: {'type': 'choice',
            'choice': choice, 'confidence': confidence,
            'probabilities': {k: .98 if k == choice else .01 for k in intake.CHOICES}}
            for ident in questions}}
        observer('complete', {'status': 'received', 'response': response}, token)
        return response
    return decide


class IntakeTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict('os.environ', {'GITHUB_RUN_ID': ''})
        env.start(); self.addCleanup(env.stop)
        self.cfg = policy()
        self.ledger = {'markets': {}, 'tasks': {}, 'identity': {'policy_sha256': digest(self.cfg)}}
        ingest(self.ledger, 'polymarket', [unknown()], self.cfg, CLOCK)

    def test_unknown_short_and_distant_never_need_semantic_review(self):
        market = normalize('polymarket', unknown())
        self.assertEqual(screen(market, CLOCK, self.cfg)['status'], 'waiting_relevance')
        self.assertEqual(screen(market | {'close_at': '2026-10-13T00:00:00Z'}, CLOCK, self.cfg)['status'], 'short_resolution_cohort')
        self.assertEqual(screen(market | {'close_at': '2027-12-10T00:00:00Z'}, CLOCK, self.cfg)['status'], 'waiting_catalyst')
        self.assertEqual(screen(market | {'resolution_hint': {'at': None}}, CLOCK, self.cfg)['status'], 'waiting_rules')

    def test_complete_bound_review_enables_real_event_without_rewriting_rules(self):
        before = copy.deepcopy(self.ledger['markets']['polymarket:new'])
        with tempfile.TemporaryDirectory() as root:
            report = intake.review(root, self.ledger, self.cfg, CLOCK, answer())
            self.assertEqual(report['http_attempts'], 1)
            self.assertEqual(report['accepted_candidates'], 1)
            market = self.ledger['markets']['polymarket:new']
            self.assertEqual(screen(market, CLOCK, self.cfg)['status'], 'candidate')
            for field in ('rules', 'rules_sha256', 'created_at', 'first_seen_at'):
                self.assertEqual(market[field], before[field])
            self.assertFalse(read(Path(root)/'intake/review-state.json')['results'][next(iter(
                read(Path(root)/'intake/review-state.json')['results']))]['outcome_probability'])

    def test_cached_decision_has_no_second_http_attempt_on_restart(self):
        with tempfile.TemporaryDirectory() as root:
            intake.review(root, self.ledger, self.cfg, CLOCK, answer())
            self.ledger['markets']['polymarket:new'].pop('intake_review')
            with patch.object(intake.decisions, 'decide', side_effect=AssertionError('duplicate HTTP')):
                again = intake.review(root, self.ledger, self.cfg, CLOCK)
            self.assertEqual(again['cached_results'], 1)
            self.assertEqual(again['http_attempts'], 0)
            self.assertEqual(screen(self.ledger['markets']['polymarket:new'], CLOCK, self.cfg)['status'], 'candidate')

    def test_review_invalidation_and_hard_conditions_remain_authoritative(self):
        with tempfile.TemporaryDirectory() as root:
            intake.review(root, self.ledger, self.cfg, CLOCK, answer())
            market = self.ledger['markets']['polymarket:new']
            for field, changed in [('question', 'Different issuer'), ('rules', 'Different threshold'),
                                   ('family', 'different'), ('rules_sources', ['https://changed.example'])]:
                self.assertEqual(screen(market | {field: changed}, CLOCK, self.cfg)['status'], 'waiting_relevance')
            for change in ({'screen_text': 'Bitcoin book publisher'}, {'binary': False}, {'composite': True}):
                self.assertEqual(screen(market | change, CLOCK, self.cfg)['status'], 'excluded')
            self.assertEqual(screen(market, '2026-10-12T02:00:01Z', self.cfg)['status'], 'outside_new_question_window')
            self.assertEqual(screen(market | {'tradable': False}, CLOCK, self.cfg)['status'], 'waiting_market')
            self.assertEqual(screen(market | {'screen_text': 'Updated unrelated tags'}, CLOCK, self.cfg)['status'], 'candidate')
            self.assertEqual(screen(market | {'close_at': '2026-10-13T00:00:00Z'}, CLOCK, self.cfg)['status'], 'short_resolution_cohort')

    def test_attention_exclusion_and_uncertain_answers_do_not_admit(self):
        for choice, confidence in [('excluded', .99), ('unclear', .99), ('event', .6)]:
            with self.subTest(choice=choice, confidence=confidence), tempfile.TemporaryDirectory() as root:
                ledger = copy.deepcopy(self.ledger)
                intake.review(root, ledger, self.cfg, CLOCK, answer(choice, confidence))
                self.assertNotEqual(screen(ledger['markets']['polymarket:new'], CLOCK, self.cfg)['status'], 'candidate')

    def test_reserved_failure_cannot_reset_budget_and_retry_has_deadline(self):
        def fail(state, questions, key, observer):
            observer('reserve', {'status': 'reserved'})
            raise TimeoutError('fixture')
        with tempfile.TemporaryDirectory() as root:
            one = intake.review(root, self.ledger, self.cfg, CLOCK, fail)
            self.assertEqual(one['status'], 'failed')
            again = intake.review(root, self.ledger, self.cfg, CLOCK, fail)
            self.assertEqual(again['http_attempts'], 0)
            later = '2026-10-12T00:31:00Z'
            two = intake.review(root, self.ledger, self.cfg, later, fail)
            self.assertEqual(two['http_attempts'], 1)
            last = intake.review(root, self.ledger, self.cfg, '2026-10-12T01:02:00Z', fail)
            self.assertEqual(last['http_attempts'], 0)
            self.assertEqual(last['status'], 'lifetime_intake_cap')
            self.assertEqual(last['deferred_inputs']['lifetime_cap'], 1)
            self.assertEqual(len(read(Path(root)/'intake/review-state.json')['requests']), 2)
            self.assertFalse(self.ledger['tasks'])

    def test_missing_credential_and_oversized_rules_reserve_nothing(self):
        with tempfile.TemporaryDirectory() as root, patch.dict('os.environ', {}, clear=True):
            self.assertEqual(intake.review(root, self.ledger, self.cfg, CLOCK)['status'], 'configuration_required')
            self.ledger['markets']['polymarket:new']['rules'] = 'x' * 24001
            self.assertEqual(intake.review(root, self.ledger, self.cfg, CLOCK, answer())['status'], 'input_too_large')
            self.assertFalse((Path(root)/'intake/review-state.json').exists())

    def test_batch_dispatch_and_daily_caps_are_durable(self):
        with tempfile.TemporaryDirectory() as root:
            ingest(self.ledger, 'polymarket', [unknown(str(i)) for i in range(20)], self.cfg, CLOCK)
            first = intake.review(root, self.ledger, self.cfg, CLOCK, answer())
            self.assertEqual(first['reviewed_candidates'], 8)
            self.assertEqual(intake.review(root, self.ledger, self.cfg, CLOCK, answer())['status'], 'dispatch_intake_cap')
        with tempfile.TemporaryDirectory() as root:
            write(Path(root)/'intake/review-state.json', {'schema': intake.VERSION, 'results': {},
                'requests': [{'reserved_at': CLOCK, 'inputs': [], 'dispatch_id': str(i)} for i in range(40)]})
            self.assertEqual(intake.review(root, self.ledger, self.cfg, CLOCK, answer())['status'], 'daily_intake_cap')

    def test_same_github_dispatch_keeps_its_cap_when_clock_advances(self):
        with tempfile.TemporaryDirectory() as root, patch.dict('os.environ', {'GITHUB_RUN_ID': 'one'}):
            intake.review(root, self.ledger, self.cfg, CLOCK, answer())
            ingest(self.ledger, 'polymarket', [unknown('second')], self.cfg, CLOCK)
            self.assertEqual(intake.review(root, self.ledger, self.cfg, '2026-10-12T00:31:00Z', answer())['status'], 'dispatch_intake_cap')
            with patch.dict('os.environ', {'GITHUB_RUN_ID': 'two'}):
                self.assertEqual(intake.review(root, self.ledger, self.cfg, '2026-10-12T00:31:00Z', answer())['http_attempts'], 1)

    def test_duplicate_families_use_one_review_slot_and_existing_tasks_none(self):
        with tempfile.TemporaryDirectory() as root:
            raw = unknown('second') | {'events': [{'id': 'new'}]}
            ingest(self.ledger, 'polymarket', [raw], self.cfg, CLOCK)
            self.assertEqual(intake.review(root, self.ledger, self.cfg, CLOCK, answer())['reviewed_candidates'], 1)
            self.ledger['tasks']['already'] = {'market': {'family': 'polymarket:new'}}
            self.assertEqual(intake.review(root, self.ledger, self.cfg, '2026-10-12T00:01:00Z', answer())['http_attempts'], 0)

    def test_corrupt_or_regressed_journal_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'intake/review-state.json'
            write(path, {'schema': 'unknown'})
            with self.assertRaisesRegex(ValueError, 'Unknown intake'): intake.review(root, self.ledger, self.cfg, CLOCK, answer())
            write(path, {'schema': intake.VERSION, 'results': {}, 'requests': [{'reserved_at': '2026-10-13T00:00:00Z'}]})
            with self.assertRaisesRegex(ValueError, 'clock regressed'): intake.review(root, self.ledger, self.cfg, CLOCK, answer())

    def test_invalid_response_preserves_reservation_without_approval(self):
        with tempfile.TemporaryDirectory() as root:
            result = intake.review(root, self.ledger, self.cfg, CLOCK, lambda *args: {'model': 'other'})
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('intake_review', self.ledger['markets']['polymarket:new'])
            self.assertEqual(len(read(Path(root)/'intake/review-state.json')['requests']), 1)

    def test_missing_budget_journal_with_existing_receipts_never_bootstraps(self):
        with tempfile.TemporaryDirectory() as root:
            write(Path(root)/'intake/requests/000001.json', {'status': 'reserved'})
            with self.assertRaisesRegex(ValueError, 'refusing reset'):
                intake.review(root, self.ledger, self.cfg, CLOCK, answer())

    def test_all_raw_new_rows_have_first_seen_receipt_even_if_excluded(self):
        ledger = {'markets': {}, 'tasks': {}}
        raw = unknown('crypto') | {'question': 'Bitcoin future price'}
        ingest_window(ledger, 'polymarket', [raw, raw], self.cfg, CLOCK)
        self.assertNotIn('polymarket:crypto', ledger['markets'])
        self.assertEqual(ledger['intake']['first_seen_total'], 1)
        with tempfile.TemporaryDirectory() as root:
            result = intake.health(root, ledger, {}, CLOCK, 0)
            self.assertEqual(result['new_first_seen'], 1)
            self.assertTrue((Path(root)/result['sightings_journal']).exists())
            self.assertEqual(intake.health(root, ledger, {}, CLOCK, 0)['new_first_seen'], 0)

    def test_new_candidate_gets_metadata_and_quote_turn_during_old_label_flood(self):
        ledger = {'markets': {}, 'tasks': {}}
        worker = {'tasks': {}}
        for i in range(60):
            key = str(i)
            ledger['markets'][key] = {'first_seen_at': CLOCK}
            ledger['tasks'][key] = {}
            worker['tasks'][key] = {'forecast': {'anchor_at': CLOCK}}
        ledger['markets']['fresh'] = {'first_seen_at': CLOCK}
        clock = '2026-10-14T23:45:00Z'
        turns = quote_turns(ledger, worker, ['fresh'], self.cfg, clock)
        meta = metadata_turns(ledger, worker, turns, self.cfg, clock)
        self.assertIn('fresh', turns); self.assertIn('fresh', meta)
        self.assertEqual(len(turns), 40); self.assertEqual(len(meta), 40)
        self.assertEqual(len(set(turns)), 40); self.assertEqual(len(set(meta)), 40)

    def test_event_seed_cursor_resumes_same_frozen_query(self):
        requests, saved = [], {}
        class HTTP:
            def get(self, url, params):
                requests.append(params.copy())
                return {'events': [], 'cursor': 'next' if len(requests) == 1 else None}, {
                    'received_at': CLOCK, 'response_sha256': 'fixture'}
        cfg = self.cfg | {'maximum_discovery_pages_per_venue': 1}
        discover_events(HTTP(), 'kalshi', cfg, CLOCK, saved)
        self.assertEqual(saved['cursor'], 'next')
        discover_events(HTTP(), 'kalshi', cfg, '2026-10-12T00:10:00Z', saved)
        self.assertEqual(requests[0]['min_close_ts'], requests[1]['min_close_ts'])
        self.assertEqual(requests[1]['cursor'], 'next')
        self.assertIsNone(saved['cursor'])

    def test_coverage_and_real_pending_warnings_are_separate(self):
        with tempfile.TemporaryDirectory() as root:
            self.ledger['markets']['polymarket:new']['selection'] = {'status': 'waiting_relevance'}
            result = intake.health(root, self.ledger, {'kalshi': {'coverage_lag_seconds': 3000}},
                '2026-10-12T00:30:00Z', 0)
            self.assertEqual({a['kind'] for a in result['alerts']}, {
                'creation_coverage_lag', 'intake_pending_over_twenty_minutes'})

    def test_detail_omission_keeps_parent_family_and_bound_approval(self):
        with tempfile.TemporaryDirectory() as root:
            ingest(self.ledger, 'polymarket', [unknown() | {'events': [{'id': 'parent'}]}], self.cfg, CLOCK)
            intake.review(root, self.ledger, self.cfg, CLOCK, answer())
            market = self.ledger['markets']['polymarket:new']
            class HTTP:
                def get(self, *args):
                    raw = unknown(); raw.pop('events')
                    return raw, {'received_at': CLOCK}
            current, _ = refresh(HTTP(), market)
            self.assertEqual(screen(current, CLOCK, self.cfg, check_newness=False)['status'], 'candidate')
            market.update(current)
            self.assertEqual(market['family'], 'polymarket:parent')
            self.assertEqual(screen(market, CLOCK, self.cfg)['status'], 'candidate')

    def test_each_batch_head_explicitly_binds_its_own_candidate(self):
        ingest(self.ledger, 'polymarket', [unknown('second')], self.cfg, CLOCK)
        def verify_heads(state, questions, key, observer):
            instructions = [q['instructions'] for q in questions.values()]
            self.assertEqual(len(set(instructions)), len(questions))
            for index, candidate in state['candidates'].items():
                self.assertIn(candidate['key'], questions[index]['instructions'])
                self.assertIn(candidate['question'], questions[index]['instructions'])
                self.assertIn('candidate '+index, questions[index]['instructions'])
            return answer()(state, questions, key, observer)
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(intake.review(root, self.ledger, self.cfg, CLOCK, verify_heads)['accepted_candidates'], 2)

    def test_prompt_change_does_not_reset_input_lifetime_attempts(self):
        with tempfile.TemporaryDirectory() as root:
            intake.review(root, self.ledger, self.cfg, CLOCK, answer())
            with patch.dict(intake.REVIEW_POLICY, {'version': 'new-prompt'}):
                second = intake.review(root, self.ledger, self.cfg, '2026-10-12T00:01:00Z', answer())
                self.assertEqual(second['http_attempts'], 1)
            with patch.dict(intake.REVIEW_POLICY, {'version': 'third-prompt'}):
                self.assertEqual(intake.review(root, self.ledger, self.cfg, '2026-10-12T00:02:00Z', answer())['http_attempts'], 0)
            self.assertEqual(len(read(Path(root)/'intake/review-state.json')['requests']), 2)

    def test_unrelated_detail_cannot_replace_the_admission_identity(self):
        class HTTP:
            def get(self, *args): return unknown('other'), {'received_at': CLOCK}
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            refresh(HTTP(), self.ledger['markets']['polymarket:new'])

    def test_monitor_closes_discovery_review_fresh_book_and_admission_loop(self):
        def scan(http, venue, cfg, clock, saved, consume):
            consume([unknown() | {'events': [{'id': 'parent'}]}] if venue == 'polymarket' else [], CLOCK)
            return {'complete': True, 'errors': [], 'coverage_lag_seconds': 0, 'pages': []}
        class HTTP:
            calls = 0
            def get(self, *args):
                self.calls += 1
                raw = unknown(); raw.pop('events')
                return raw, {'received_at': CLOCK, 'response_sha256': digest(raw)}
        quote = {'status': 'usable', 'received_at': CLOCK, 'midpoint': .5}
        with tempfile.TemporaryDirectory() as root, patch(
                'ForecastAgent.market_repricing.monitor.discover_events', return_value=([], {'pages': []})), patch(
                'ForecastAgent.market_repricing.monitor.scan_creation_window', side_effect=scan), patch(
                'ForecastAgent.market_repricing.monitor.book', return_value=quote):
            report = run(root, http=HTTP(), clock=CLOCK, intake_decide=answer())
            saved = read(Path(root)/'state.json')
            self.assertEqual(len(saved['tasks']), 1)
            task = saved['tasks']['polymarket:new']
            self.assertEqual(task['market']['family'], 'polymarket:parent')
            self.assertEqual(task['market']['intake_review']['status'], 'accepted')
            self.assertEqual(task['market']['created_at'], CLOCK)
            self.assertEqual(report['intake_health']['new_admissions'], 1)
            self.assertEqual(report['intake_review']['http_attempts'], 1)
            self.assertFalse(report['no_model_calls'])
            self.assertNotIn('provider_reservations', saved)


if __name__ == '__main__':
    unittest.main()
