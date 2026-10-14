"""Creation-clock observations, old forecast preservation and settlement gates."""
import copy
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.market_repricing.common import digest, policy, read, state, write
from ForecastAgent.market_repricing.monitor import enroll, ingest, metadata_turns, save_followups
from ForecastAgent.market_repricing.tracking import creation_schedule, forecast_schedule, labels
from ForecastAgent.market_repricing.venues import normalize, final_resolution
from ForecastAgent.market_repricing.worker import forecast_policy
from ForecastAgent.market_repricing.forecast import registry
from ForecastAgent.market_repricing.tests.test_repricing import CLOCK, raw_poly, previous_policy, FakeHTTP


class CreationFollowups(unittest.TestCase):
    def setUp(self):
        self.cfg = policy()
        self.market = normalize('polymarket', raw_poly())
        self.task = {'market': self.market, 'enrolled_at': '2026-10-12T01:00:00Z', 'status': 'queued'}

    def test_deployed_policy_migrates_without_changing_existing_tasks(self):
        old = previous_policy()
        self.assertEqual(digest(old), '315c683957751c19000d0a307a52a4bc664ac682dd07bcdea4413cea578333b9')
        for role in ('worker', 'monitor'):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as root:
                saved = state(root, role, old)
                saved.update(tasks={'old': copy.deepcopy(self.task)}, provider_reservations=[{'kind': 'model'}],
                             daily={'2026-10-12': 100}, enrollment_daily={'2026-10-12': 20})
                write(Path(root) / 'state.json', saved)
                migrated = state(root, role, self.cfg)
                for key in ('tasks', 'provider_reservations', 'daily', 'enrollment_daily'):
                    self.assertEqual(saved[key], migrated[key])
                self.assertFalse(migrated['policy_migrations'][-1]['budget_reset'])
                write(Path(root) / 'state.json', migrated)
                self.assertEqual(len(state(root, role, self.cfg)['policy_migrations']), 3)

    def test_creation_origin_not_enrollment_or_anchor(self):
        result = creation_schedule(self.task)
        self.assertEqual(result['targets']['24'], '2026-10-13T00:00:00+00:00')
        self.assertEqual(result['targets']['72'], '2026-10-15T00:00:00+00:00')
        self.assertEqual(result['targets']['168'], '2026-10-19T00:00:00+00:00')
        self.assertEqual(result['origin_role'], 'market_created_at')

    def test_legacy_sealed_forecasts_keep_old_target_clock(self):
        old = {'anchor_at': '2026-10-12T02:00:00Z', 'baseline': {'72': .5, '168': .5}}
        result = forecast_schedule(old)
        self.assertEqual(result['targets']['72'], '2026-10-15T02:00:00+00:00')
        self.assertNotIn('24', result['targets'])
        self.assertEqual(forecast_policy(self.task, self.cfg)['horizons_hours'], [72, 168])

    def test_two_hour_limit_rechecked_after_scan_before_admission(self):
        ledger = {'markets': {}, 'tasks': {}, 'identity': {'policy_sha256': digest(self.cfg)}}
        ingest(ledger, 'polymarket', [raw_poly()], self.cfg, CLOCK)
        for market in ledger['markets'].values():
            market['last_quote'] = {'status': 'usable', 'received_at': '2026-10-12T02:00:01Z'}
        enroll(ledger, self.cfg, '2026-10-12T02:00:01Z')
        self.assertFalse(ledger['tasks'])
        self.assertEqual(next(iter(ledger['markets'].values()))['selection']['status'], 'outside_new_question_window')

    def test_new_enrollment_freezes_create_targets(self):
        ledger = {'markets': {}, 'tasks': {}, 'identity': {'policy_sha256': digest(self.cfg)}}
        ingest(ledger, 'polymarket', [raw_poly()], self.cfg, CLOCK)
        for market in ledger['markets'].values(): market['last_quote'] = {'status': 'usable', 'received_at': CLOCK}
        enroll(ledger, self.cfg, CLOCK)
        task = next(iter(ledger['tasks'].values()))
        self.assertEqual(task['price_schedule'], creation_schedule(task))
        self.assertEqual(forecast_policy(task, self.cfg)['price_schedule'], task['price_schedule'])
        self.assertIn('2026-10-13T00:00:00+00:00', registry({}, [24], task['price_schedule'])['price_24']['instructions'])

    def test_prices_collected_even_without_a_forecast(self):
        key = self.market['key']
        ledger = {'markets': {key: self.market}, 'tasks': {key: self.task}}
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            write(root / 'quotes' / (digest(key) + '.json'), [{'status': 'usable', 'midpoint': .6,
                  'received_at': '2026-10-12T23:59:00Z'}])
            save_followups(root, ledger, {'tasks': {}}, self.cfg, '2026-10-13T00:01:00Z')
            result = read(root / 'tracking.json')['tasks'][key]
            self.assertEqual(result['prices']['24']['label'], .6)
            self.assertEqual(result['schedule']['origin_at'], CLOCK)

    def test_late_quote_is_missing_not_a_substituted_label(self):
        qs = [{'status': 'usable', 'midpoint': .9, 'received_at': '2026-10-13T00:00:01Z'}]
        result = labels(creation_schedule(self.task), self.market, qs, '2026-10-13T00:01:00Z', self.cfg)
        self.assertEqual(result['24']['status'], 'missing_window_quote')

    def test_old_creation_observations_and_forecast_labels_remain_separate(self):
        key = self.market['key']
        ledger = {'markets': {key: self.market}, 'tasks': {key: self.task}}
        worker = {'tasks': {key: {'forecast': {'anchor_at': '2026-10-12T02:00:00Z'}}}}
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            save_followups(root, ledger, worker, self.cfg, '2026-10-15T03:00:00Z')
            self.assertEqual(read(root / 'labels' / (digest(key) + '.json'))['72']['target_at'], '2026-10-15T02:00:00+00:00')
            self.assertEqual(read(root / 'tracking.json')['tasks'][key]['prices']['72']['target_at'], '2026-10-15T00:00:00+00:00')

    def test_final_checks_continue_after_seven_days_and_rotate(self):
        key = self.market['key']
        ledger = {'markets': {key: self.market}, 'tasks': {key: self.task}}
        self.assertIn(key, metadata_turns(ledger, {}, [], self.cfg, '2026-11-01T00:00:00Z'))
        self.market['last_followup_check_at'] = '2026-11-01T00:00:00Z'
        self.assertNotIn(key, metadata_turns(ledger, {}, [], self.cfg, '2026-11-01T05:59:59Z'))
        self.assertIn(key, metadata_turns(ledger, {}, [], self.cfg, '2026-11-01T06:00:00Z'))
        self.market['terminal_result'] = 'yes'
        self.assertNotIn(key, metadata_turns(ledger, {}, [], self.cfg, '2026-11-01T23:59:59Z'))
        self.assertIn(key, metadata_turns(ledger, {}, [], self.cfg, '2026-11-02T00:00:00Z'))

    def test_urgent_prices_not_starved_by_final_metadata_refresh(self):
        key = self.market['key']
        ledger = {'markets': {key: self.market}, 'tasks': {key: self.task}}
        for i in range(60):
            old = 'old:' + str(i)
            ledger['tasks'][old] = copy.deepcopy(self.task)
            ledger['tasks'][old]['market']['created_at'] = '2026-09-01T00:00:00Z'
            ledger['markets'][old] = copy.deepcopy(self.market)
        result = metadata_turns(ledger, {}, [key], self.cfg, '2026-10-12T23:45:00Z')
        self.assertEqual(result[0], key)
        self.assertEqual(len(result), 40)
        self.assertEqual(len(set(result)), 40)

    def test_kalshi_result_requires_settled_status(self):
        raw = {'ticker': 'KXTEST', 'market_type': 'binary', 'result': 'yes'}
        self.assertIsNone(normalize('kalshi', raw | {'status': 'determined'})['terminal_result'])
        self.assertEqual(normalize('kalshi', raw | {'status': 'settled'})['terminal_result'], 'yes')

    def test_polymarket_requires_closed_explicit_single_winner(self):
        market = self.market | {'condition_id': 'condition'}
        raw = {'condition_id': 'condition', 'closed': True, 'tokens': [
            {'outcome': 'No', 'token_id': 'no_asset', 'price': 0, 'winner': False},
            {'outcome': 'Yes', 'token_id': 'yes_asset', 'price': 1, 'winner': True}]}
        self.assertEqual(final_resolution(FakeHTTP(raw), market)['result'], 'yes')
        no_winner = copy.deepcopy(raw)
        no_winner['tokens'][1]['winner'] = False
        self.assertIsNone(final_resolution(FakeHTTP(no_winner), market)['result'])
        self.assertIsNone(final_resolution(FakeHTTP(raw | {'closed': False}), market)['result'])
        self.assertIsNone(final_resolution(FakeHTTP(raw), market | {'disputed': True})['result'])
        with self.assertRaises(ValueError): final_resolution(FakeHTTP(raw | {'condition_id': 'wrong'}), market)


if __name__ == '__main__':
    unittest.main()
