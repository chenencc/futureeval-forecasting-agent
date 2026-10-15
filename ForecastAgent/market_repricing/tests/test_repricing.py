"""Offline gates for selection, midpoint units, labels and durable budgets."""
import copy
from datetime import timedelta
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ForecastAgent.market_repricing import ENGINE_SHA
from ForecastAgent.market_repricing.common import digest, instant, now, policy, read, seal, state, verify, write
from ForecastAgent.market_repricing.venues import normalize, quote, screen
from ForecastAgent.market_repricing.monitor import admission_window, discover, enroll, ingest, label, quote_turns, run as monitor_run
from ForecastAgent.market_repricing.forecast import mean, registry
from ForecastAgent.market_repricing.github_state import extract, find, ArtifactRedirect, recover_omitted_mutexes
from ForecastAgent.market_repricing.worker import ProviderCap, provider_budget, request_for, run, pending_keys


CLOCK = '2026-10-12T00:00:00+00:00'


def previous_policy():
    cfg = policy() | {'maximum_worker_model_attempts_per_utc_day': 400}
    for field in ('maximum_candidate_age_hours', 'price_target_origin',
                  'maximum_resolution_requests_per_dispatch', 'final_refresh_interval_hours',
                  'resolved_recheck_interval_hours', 'enrollment_window_hours'):
        cfg.pop(field)
    return cfg | {'maximum_candidate_age_days': 1, 'maximum_new_tasks_per_day': 20, 'horizons_hours': [72, 168]}


def raw_poly():
    return {'id': '123', 'question': 'Will the Fed cut interest rates at its December meeting?',
            'description': 'Resolves YES if the Federal Reserve lowers the target rate on December 10, 2026. Source: federalreserve.gov.',
            'createdAt': CLOCK, 'endDate': '2026-12-10T21:00:00Z', 'active': True,
            'closed': False, 'enableOrderBook': True, 'acceptingOrders': True,
            'outcomes': '["No", "Yes"]', 'clobTokenIds': '["no_asset", "yes_asset"]',
            'events': [{'id': 'event1', 'slug': 'fed-december'}]}


class FakeHTTP:
    def __init__(self, response): self.response, self.calls = response, 0
    def get(self, *args):
        self.calls += 1
        return self.response, {'received_at': CLOCK, 'response_sha256': 'fake', 'receipt': 'fixture'}


class Selection(unittest.TestCase):
    def test_retries_rotate_behind_unattempted_tasks(self):
        ledger={'tasks':{'retry':{'status':'retry_wait','executions':1,'retry_at':None,'last_attempt_at':CLOCK},
                         'new':{'status':'queued'},'done':{'status':'completed'}}}
        self.assertEqual(pending_keys(ledger),['new','retry'])
    def test_legacy_retries_cannot_starve_new_work_or_be_starved(self):
        tasks={'r'+str(i):{'status':'retry_wait','executions':2} for i in range(3)}
        tasks.update({'n'+str(i):{'status':'queued','executions':0} for i in range(7)})
        self.assertEqual(pending_keys({'tasks':tasks})[:6],['n0','n1','r0','n2','n3','r1'])
    def test_verification_cannot_enroll_unseen_stale_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);monitor=root/'monitor';worker=root/'worker'
            source=state(monitor,'monitor',policy());source['last_attempt_at']=CLOCK
            source['tasks']={'new':{'market':normalize('polymarket',raw_poly())}}
            write(monitor/'state.json',source);seal(monitor)
            with self.assertRaisesRegex(ValueError,'Stale discovery'):
                run(worker,monitor,verification_only=True)
    def setUp(self): self.cfg = policy(); self.m = normalize('polymarket', raw_poly())
    def test_reversed_yes_identity(self): self.assertEqual(self.m['yes_token'], 'yes_asset')
    def test_crypto_broad_exclusion(self):
        for title in ['Coinbase merger', 'Bitcoin ETF ruling', 'crypto regulation', 'MicroStrategy earnings', 'Ethereum inflation']:
            with self.subTest(title=title):
                m = self.m | {'screen_text': title}
                self.assertEqual(screen(m, CLOCK, self.cfg)['reason'], 'crypto_excluded')
    def test_sports_social_exclusion(self):
        for title in ['NFL finals', 'soccer match', 'esports vote', 'president mentions', 'NBA game']:
            self.assertEqual(screen(self.m | {'screen_text': title}, CLOCK, self.cfg)['status'], 'excluded')
    def test_rules_required(self): self.assertEqual(screen(self.m | {'rules': ''}, CLOCK, self.cfg)['status'], 'waiting_rules')
    def test_composite_not_silently_single_event(self): self.assertEqual(screen(self.m | {'composite': True}, CLOCK, self.cfg)['status'], 'excluded')
    def test_unknown_domain_not_force_filled(self): self.assertEqual(screen(self.m | {'screen_text': 'unknown new question'}, CLOCK, self.cfg)['status'], 'waiting_relevance')
    def test_short_resolution_separate(self): self.assertEqual(screen(self.m | {'close_at': '2026-10-13T00:00:00Z'}, CLOCK, self.cfg)['status'], 'short_resolution_cohort')
    def test_distant_catalyst_waits(self): self.assertEqual(screen(self.m | {'close_at': '2028-11-07T00:00:00Z'}, CLOCK, self.cfg)['status'], 'waiting_catalyst')
    def test_unorderable_open_contract_still_researched(self):
        self.assertEqual(screen(self.m | {'tradable': False}, CLOCK, self.cfg)['status'], 'candidate')
        self.assertEqual(screen(self.m | {'platform_status': 'closed'}, CLOCK, self.cfg)['status'], 'waiting_market')
    def test_catalyst_not_invented(self): self.assertEqual(screen(self.m, CLOCK, self.cfg)['catalyst_status'], 'plausible_unconfirmed')
    def test_family_daily_dedup(self):
        ledger = {'markets': {}, 'tasks': {}, 'daily': {}, 'identity': {'policy_sha256': digest(self.cfg)}}
        ingest(ledger, 'polymarket', [raw_poly(), raw_poly() | {'id': '124'}], self.cfg, CLOCK)
        for m in ledger['markets'].values(): m['last_quote'] = {'status': 'usable', 'received_at': CLOCK}
        enroll(ledger, self.cfg, CLOCK); enroll(ledger, self.cfg, CLOCK)
        self.assertEqual(len(ledger['tasks']), 1); self.assertEqual(ledger['enrollment_daily']['2026-10-12'], 1)
        before = digest(ledger['tasks'])
        for market in ledger['markets'].values(): market['metadata_versions'].append({'new': 'metadata'})
        self.assertEqual(digest(ledger['tasks']), before)
    def test_fifty_per_rolling_day_without_midnight_reset_or_lifetime_cap(self):
        ledger = {'markets': {}, 'tasks': {}, 'daily': {}, 'identity': {'policy_sha256': digest(self.cfg)}}
        before_midnight = '2026-10-12T15:59:59Z'
        ingest(ledger, 'polymarket', [raw_poly() | {'id': str(i), 'createdAt': before_midnight, 'events': [{'id': str(i)}]} for i in range(70)], self.cfg, before_midnight)
        for m in ledger['markets'].values(): m['last_quote'] = {'status': 'usable', 'received_at': before_midnight}
        enroll(ledger, self.cfg, before_midnight); self.assertEqual(len(ledger['tasks']), 50)
        for task in ledger['tasks'].values(): task['status'] = 'blocked'
        enroll(ledger, self.cfg, before_midnight); self.assertEqual(len(ledger['tasks']), 50)
        for m in ledger['markets'].values(): m['last_quote']['received_at'] = '2026-10-12T16:00:00Z'
        enroll(ledger, self.cfg, '2026-10-12T16:00:00Z'); self.assertEqual(len(ledger['tasks']), 50)
        enroll(ledger, self.cfg, '2026-10-12T16:01:00Z'); self.assertEqual(len(ledger['tasks']), 50)
        self.assertEqual(ledger['enrollment_daily'], {'2026-10-12': 50, '2026-10-13': 0})
        self.assertEqual(ledger['admission_window']['remaining'], 0)
        next_day = '2026-10-13T15:59:59Z'
        ingest(ledger, 'polymarket', [raw_poly() | {'id': str(i), 'createdAt': next_day, 'events': [{'id': str(i)}]}
                                     for i in range(100, 170)], self.cfg, next_day)
        for market in ledger['markets'].values(): market['last_quote'] = {'status':'usable', 'received_at':next_day}
        enroll(ledger, self.cfg, next_day)
        self.assertEqual(len(ledger['tasks']), 100)
        self.assertEqual(ledger['admission_window']['admitted'], 50)
    def test_old_quote_is_not_relabelled_as_admission_price(self):
        ledger = {'markets': {}, 'tasks': {}, 'daily': {}, 'last_attempt_at': CLOCK,
                  'identity': {'policy_sha256': digest(self.cfg)}}
        ingest(ledger, 'polymarket', [raw_poly()], self.cfg, CLOCK)
        market = next(iter(ledger['markets'].values()))
        market['last_quote'] = {'status': 'usable', 'received_at': '2026-10-11T23:59:59Z'}
        enroll(ledger, self.cfg, CLOCK)
        self.assertEqual(len(ledger['tasks']), 1)
        observation = next(iter(ledger['tasks'].values()))['admission_quote']
        self.assertEqual(observation['status'], 'stale')
        self.assertFalse(observation['at_admission'])
        self.assertEqual(observation['observation']['received_at'], '2026-10-11T23:59:59Z')
    def test_wide_spread_book_reaches_enrollment(self):
        ledger = {'markets': {}, 'tasks': {}, 'daily': {}, 'last_attempt_at': CLOCK,
                  'identity': {'policy_sha256': digest(self.cfg)}}
        ingest(ledger, 'polymarket', [raw_poly()], self.cfg, CLOCK)
        market = next(iter(ledger['markets'].values()))
        market['last_quote'] = quote('polymarket',
            {'bids': [{'price': .02, 'size': 32}], 'asks': [{'price': .99, 'size': 100}]},
            self.cfg, {'received_at': CLOCK})
        enroll(ledger, self.cfg, CLOCK)
        frozen = next(iter(ledger['tasks'].values()))['market']['last_quote']
        self.assertEqual(frozen['quality_flags'], ['wide_spread'])
        self.assertAlmostEqual(frozen['midpoint'], .505)
    def test_rolling_window_boundary_staggered_slots_and_all_statuses(self):
        ledger = {'tasks': {str(i): {'enrolled_at': (instant(CLOCK)-timedelta(hours=23, minutes=i)).isoformat(),
                    'status': 'blocked' if i % 2 else 'completed'} for i in range(50)}}
        window = admission_window(ledger, self.cfg, CLOCK)
        self.assertEqual(window['admitted'], 50); self.assertEqual(window['remaining'], 0)
        self.assertTrue(window['cap_reached'])
        release = window['next_slot_at']
        just_before = (instant(release)-timedelta(microseconds=1)).isoformat()
        self.assertEqual(admission_window(ledger, self.cfg, just_before)['remaining'], 0)
        self.assertEqual(admission_window(ledger, self.cfg, release)['remaining'], 1)
        self.assertEqual(admission_window(ledger, self.cfg, release)['admitted'], 49)
        ledger['tasks']['51'] = {'enrolled_at': CLOCK, 'status': 'failed'}
        self.assertEqual(admission_window(ledger, self.cfg, CLOCK)['remaining'], 0)
        self.assertEqual(instant(admission_window(ledger,self.cfg,CLOCK)['next_slot_at']), instant(release)+timedelta(minutes=1))
    def test_future_or_missing_admission_receipt_fails_closed(self):
        for task in ({'enrolled_at': (instant(CLOCK)+timedelta(seconds=1)).isoformat()}, {}):
            with self.subTest(task=task), self.assertRaises((ValueError, KeyError)):
                admission_window({'tasks': {'bad':task}}, self.cfg, CLOCK)
    def test_larger_window_keeps_thirty_existing_slots_and_admits_only_twenty_more(self):
        ledger = {'markets': {}, 'tasks': {}, 'daily': {}, 'identity': {'policy_sha256': digest(self.cfg)}}
        old = self.cfg | {'maximum_new_tasks_per_day': 30}
        ingest(ledger, 'polymarket', [raw_poly() | {'id': str(i), 'events': [{'id': str(i)}]}
                                    for i in range(70)], self.cfg, CLOCK)
        for market in ledger['markets'].values(): market['last_quote'] = {'status':'usable', 'received_at':CLOCK}
        enroll(ledger, old, CLOCK)
        self.assertEqual(len(ledger['tasks']), 30)
        before = copy.deepcopy(ledger['tasks'])
        enroll(ledger, self.cfg, CLOCK)
        self.assertEqual(len(ledger['tasks']), 50)
        self.assertEqual({key:ledger['tasks'][key] for key in before}, before)
        self.assertEqual(ledger['admission_window']['remaining'], 0)
    def test_large_cohort_quote_turns_prioritize_due_labels_and_reserve_new_entries(self):
        ledger = {'markets': {}, 'tasks': {}}
        worker = {'tasks': {}}
        for index in range(140):
            key = 'old:' + str(index)
            ledger['markets'][key] = {'first_seen_at': CLOCK,
                                     'last_quote': {'received_at': CLOCK}}
            ledger['tasks'][key] = {}
            worker['tasks'][key] = {'status': 'queued'}
        urgent = 'old:139'
        worker['tasks'][urgent] = {'forecast': {'anchor_at': CLOCK}}
        new = ['new:' + str(i) for i in range(20)]
        for key in new: ledger['markets'][key] = {'first_seen_at': CLOCK}
        turns = quote_turns(ledger, worker, new, self.cfg, '2026-10-14T23:45:00Z')
        self.assertEqual(turns[0], urgent)
        self.assertEqual(len(turns), 40); self.assertEqual(len(set(turns)), 40)
        self.assertEqual(len(set(turns) & set(new)), 20)
    def test_partial_pagination_not_complete(self):
        cfg = self.cfg | {'maximum_discovery_pages_per_venue': 1}
        rows, audit = discover(FakeHTTP([raw_poly()] * 100), 'polymarket', cfg, CLOCK)
        self.assertEqual(len(rows), 100); self.assertFalse(audit['complete'])
    def test_request_internal_not_platform_id(self):
        request = request_for(self.m)
        self.assertTrue(request['synthetic_compatibility_id']); self.assertTrue(request['id'].isdigit())
        self.assertEqual(request['market_research_key'], self.m['key'])
    def test_creation_two_hour_boundary_and_future_clock(self):
        for stamp, accepted in [('2026-10-11T22:00:00Z', True), ('2026-10-11T21:59:59Z', False),
                                ('2026-10-12T00:00:01Z', False)]:
            self.assertEqual(screen(self.m | {'created_at': stamp}, CLOCK, self.cfg)['status'] == 'candidate', accepted)
    def test_long_trade_close_not_a_long_resolution(self):
        hint = {'at': '2026-10-13T00:00:00Z', 'role': 'platform_expected_expiration'}
        self.assertEqual(screen(self.m | {'resolution_hint': hint}, CLOCK, self.cfg)['reason'], 'expected_resolution_within_seven_days')
    def test_resolution_seven_day_boundary_and_unknown(self):
        hint = {'at': '2026-10-19T00:00:00Z', 'role': 'platform_expected_expiration'}
        self.assertEqual(screen(self.m | {'resolution_hint': hint}, CLOCK, self.cfg)['status'], 'candidate')
        hint['at'] = '2026-10-18T23:59:59Z'
        self.assertEqual(screen(self.m | {'resolution_hint': hint}, CLOCK, self.cfg)['status'], 'short_resolution_cohort')
        self.assertEqual(screen(self.m | {'resolution_hint': {'at': None}}, CLOCK, self.cfg)['status'], 'waiting_rules')
    def test_title_rule_timezone_binding_uses_platform_year_and_dst(self):
        rule = 'Resolves Yes if the event occurs before the specified date, 11:59 PM ET. Otherwise No.'
        for title, close in [('Will an indictment occur by October 31?', '2026-11-01T03:59:00Z'),
                             ('Will an indictment occur by December 31?', '2027-01-01T04:59:00Z')]:
            market = normalize('polymarket', raw_poly() | {'question': title, 'description': rule, 'endDate': close})
            self.assertEqual(market['resolution_hint']['role'], 'bound_title_rule_deadline')
            self.assertTrue(market['resolution_hint']['title_rule_metadata_agree'])
        for close in ['2026-11-02T03:59:00Z', '2026-11-01T03:00:00Z']:
            market = normalize('polymarket', raw_poly() | {'question': 'Will an indictment occur by October 31?', 'description': rule, 'endDate': close})
            self.assertIsNone(market['resolution_hint']['at'])
        market = normalize('polymarket', raw_poly() | {'question': 'Will an indictment occur by October 31?',
                            'description': 'No explicit deadline delegation.', 'endDate': '2026-11-01T03:59:00Z'})
        self.assertIsNone(market['resolution_hint']['at'])


class Books(unittest.TestCase):
    def setUp(self): self.cfg = policy(); self.meta = {'received_at': CLOCK}
    def test_poly_best_prices_not_first_array_item(self):
        raw = {'bids': [{'price': '.3', 'size': '30'}, {'price': '.45', 'size': '25'}],
               'asks': [{'price': '.7', 'size': '30'}, {'price': '.50', 'size': '30'}]}
        q = quote('polymarket', raw, self.cfg, self.meta)
        self.assertAlmostEqual(q['midpoint'], .475); self.assertEqual(q['status'], 'usable')
        self.assertEqual(q['yes_bids_top10'], [{'price': .45, 'size': 25}, {'price': .3, 'size': 30}])
        self.assertEqual(q['yes_asks_top10'], [{'price': .5, 'size': 30}, {'price': .7, 'size': 30}])
        self.assertEqual(q['received_at'], CLOCK)
    def test_kalshi_complementary_ask_size(self):
        q = quote('kalshi', {'orderbook_fp': {'yes_dollars': [['.45', '30']], 'no_dollars': [['.50', '23']]}}, self.cfg, self.meta)
        self.assertAlmostEqual(q['midpoint'], .475); self.assertEqual(q['yes_ask_size'], 23)
    def test_ambiguous_cents_fail_closed(self):
        with self.assertRaises(ValueError): quote('kalshi', {'orderbook': {'yes': [[45, 30]], 'no': [[50, 30]]}}, self.cfg, self.meta)
    def test_absent_side(self): self.assertEqual(quote('polymarket', {'bids': []}, self.cfg, self.meta)['status'], 'missing_two_sided_book')
    def test_zero_depth(self):
        raw = {'bids': [{'price': .4, 'size': 0}], 'asks': [{'price': .45, 'size': 50}]}
        self.assertEqual(quote('polymarket', raw, self.cfg, self.meta)['status'], 'missing_depth')
    def test_shell_wide_shallow_crossed(self):
        for bid, ask, size, status in [(.45, .5, 1, 'shallow_book'), (.6, .5, 50, 'crossed_book')]:
            q = quote('polymarket', {'bids': [{'price': bid, 'size': size}], 'asks': [{'price': ask, 'size': size}]}, self.cfg, self.meta)
            self.assertEqual(q['status'], status); self.assertIsNone(q['midpoint'])
    def test_wide_spread_is_research_usable_with_explicit_quality(self):
        raw = {'bids': [{'price': '.02', 'size': '32'}],
               'asks': [{'price': '.99', 'size': '100'}]}
        q = quote('polymarket', raw, self.cfg, self.meta)
        self.assertEqual(q['status'], 'usable')
        self.assertEqual(q['quality_flags'], ['wide_spread'])
        self.assertAlmostEqual(q['midpoint'], .505)
        self.assertFalse(q['midpoint_is_executable_price'])
        strict = quote('polymarket', raw, self.cfg | {'allow_wide_spread': False}, self.meta)
        self.assertEqual(strict['status'], 'wide_spread'); self.assertIsNone(strict['midpoint'])
        raw['bids'][0]['size'] = '1'
        self.assertEqual(quote('polymarket', raw, self.cfg, self.meta)['status'], 'shallow_book')


class Labels(unittest.TestCase):
    def setUp(self): self.cfg = policy(); self.task = {'forecast': {'anchor_at': CLOCK}}
    def test_no_late_quote(self):
        q = {'status': 'usable', 'midpoint': .9, 'received_at': '2026-10-15T00:01:00Z'}
        result = label(self.task, {}, [q], '2026-10-15T00:02:00Z', self.cfg)
        self.assertEqual(result['72']['status'], 'missing_window_quote')
    def test_latest_in_window(self):
        qs = [{'status': 'usable', 'midpoint': p, 'received_at': t} for p, t in [(.4, '2026-10-14T23:00:00Z'), (.6, '2026-10-14T23:45:00Z'), (.7, '2026-10-14T23:55:00Z')]]
        self.assertEqual(label(self.task, {}, qs, '2026-10-15T00:01:00Z', self.cfg)['72']['label'], .7)
    def test_early_resolution_separate(self):
        market = {'terminal_result': 'yes', 'terminal_seen_at': '2026-10-14T00:00:00Z'}
        self.assertEqual(label(self.task, market, [], '2026-10-15T00:01:00Z', self.cfg)['72']['status'], 'early_resolution')
    def test_rules_changed_retained(self):
        market = {'rule_drift_seen_at': '2026-10-14T00:00:00Z'}
        self.assertEqual(label(self.task, market, [], '2026-10-15T00:01:00Z', self.cfg)['72']['status'], 'rules_changed')
    def test_not_due(self): self.assertEqual(label(self.task, {}, [], CLOCK, self.cfg), {})
    def test_wide_spread_label_preserves_quality_and_observed_midpoint(self):
        q = quote('polymarket', {'bids': [{'price': .02, 'size': 100}],
                  'asks': [{'price': .99, 'size': 100}]}, self.cfg,
                  {'received_at': '2026-10-14T23:55:00Z'})
        result = label(self.task, {}, [q], '2026-10-15T00:01:00Z', self.cfg)['72']
        self.assertEqual(result['status'], 'quoted')
        self.assertAlmostEqual(result['label'], .505)
        self.assertEqual(result['quote']['quality_flags'], ['wide_spread'])


class Persistence(unittest.TestCase):
    def test_only_exact_omitted_mutex_bytes_can_be_recovered(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = state(root, 'worker', policy())
            write(Path(root) / 'state.json', ledger)
            marker = Path(root) / 'tasks' / ('a' * 64) / 'development-intelligence' / '.lock.recovery'
            marker.parent.mkdir(parents=True); marker.write_bytes(b'0')
            seal(root); original_inventory = (Path(root) / 'inventory.json').read_bytes()
            marker.unlink()
            repaired = recover_omitted_mutexes(root)
            self.assertEqual(len(repaired), 1); self.assertEqual(marker.read_bytes(), b'0')
            self.assertEqual((Path(root) / 'inventory.json').read_bytes(), original_inventory)
            verify(root)
            (Path(root) / 'state.json').unlink()
            with self.assertRaises(ValueError): recover_omitted_mutexes(root)
        with tempfile.TemporaryDirectory() as root:
            ledger = state(root, 'worker', policy()); write(Path(root) / 'state.json', ledger)
            marker = Path(root) / 'tasks' / ('a' * 64) / 'development-intelligence' / '.lock.recovery'
            marker.parent.mkdir(parents=True); marker.write_bytes(b'unknown')
            seal(root); marker.unlink()
            with self.assertRaises(ValueError): recover_omitted_mutexes(root)
    def test_relative_worker_root_is_absolute_at_collector_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            source = state(Path(root) / 'monitor', 'monitor', policy())
            source['last_attempt_at'] = now(); m = normalize('polymarket', raw_poly())
            source['tasks'][m['key']] = {'key': m['key'], 'market': m, 'status': 'queued'}
            write(Path(root) / 'monitor' / 'state.json', source); seal(Path(root) / 'monitor')
            seen = []
            def collect(task, folder):
                seen.append(folder.is_absolute())
                raise RuntimeError('Fixture stops before networking')
            previous = Path.cwd()
            try:
                os.chdir(root)
                run('worker', 'monitor', collector=collect)
            finally:
                os.chdir(previous)
            self.assertEqual(seen, [True])
    def test_authorized_policy_migration_preserves_tasks_and_failed_reservations(self):
        cfg = policy()
        legacy = previous_policy()
        legacy.pop('allow_wide_spread')
        legacy.pop('enrollment_day_timezone'); legacy.pop('maximum_new_tasks_per_day')
        legacy.update(maximum_active_families=3, maximum_new_tasks_per_utc_day=3)
        self.assertEqual(digest(legacy), '642119b33adddb5331834c728c5ae43a0a4e22f742e104891e388b7e47ae96df')
        with tempfile.TemporaryDirectory() as root:
            ledger = state(root, 'worker', legacy)
            ledger['tasks']['old'] = {'executions': 2, 'status': 'retry_wait'}
            ledger['provider_reservations'] = [{'status': 'failed', 'kind': 'model'}]
            ledger['daily'] = {'2026-10-12': 3}
            write(Path(root) / 'state.json', ledger)
            migrated = state(root, 'worker', cfg)
            for name in ('tasks', 'provider_reservations', 'daily'):
                self.assertEqual(migrated[name], ledger[name])
            self.assertFalse(migrated['policy_migrations'][0]['budget_reset'])
            self.assertEqual(migrated['identity']['policy_sha256'], digest(cfg))
            # Increasing a provider budget is outside this authorized migration.
            with self.assertRaises(ValueError):
                state(root, 'worker', cfg | {'maximum_worker_model_attempts_per_task': 61})
    def test_wide_spread_policy_migration_preserves_daily_admissions_and_reservations(self):
        cfg = policy(); old = previous_policy(); old.pop('allow_wide_spread')
        self.assertEqual(digest(old), '31ef70793256bce9ca689e0d51911e05fe8288fa1aedffd8661487c81d423825')
        with tempfile.TemporaryDirectory() as root:
            ledger = state(root, 'monitor', old)
            ledger['tasks']['old'] = {'enrolled_at': CLOCK, 'status': 'queued'}
            ledger['enrollment_daily'] = {'2026-10-12': 1}
            ledger['provider_reservations'] = [{'status': 'failed', 'kind': 'model'}]
            write(Path(root) / 'state.json', ledger)
            migrated = state(root, 'monitor', cfg)
            for field in ('tasks', 'enrollment_daily', 'provider_reservations'):
                self.assertEqual(migrated[field], ledger[field])
            self.assertFalse(migrated['policy_migrations'][0]['budget_reset'])
    def test_exact_calendar_to_rolling_migration_preserves_budget_and_receipts(self):
        cfg = policy(); old = dict(cfg, maximum_new_tasks_per_day=30, maximum_worker_model_attempts_per_utc_day=400); old.pop('enrollment_window_hours')
        self.assertEqual(digest(old), 'd72c87273d613240e98f8e6f0fc1022e4c462db0c5b6c022bdf7a4e55054d7c2')
        for role in ('monitor','worker'):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as root:
                ledger = state(root,role,old)
                ledger.update(tasks={'old':{'enrolled_at':CLOCK,'status':'blocked','executions':3,
                    'policy_sha256':digest(old)}},enrollment_daily={'2026-10-12':30},
                    provider_reservations=[{'kind':'search','status':'failed'}])
                write(Path(root)/'state.json',ledger)
                migrated = state(root,role,cfg)
                for field in ('tasks','enrollment_daily','provider_reservations','daily'):
                    self.assertEqual(migrated[field],ledger[field])
                self.assertFalse(migrated['policy_migrations'][-1]['budget_reset'])
                for bad in (cfg | {'enrollment_window_hours':25},cfg | {'maximum_new_tasks_per_day':31},
                            cfg | {'maximum_worker_model_attempts_per_task':61}):
                    with self.assertRaises(ValueError): state(root,role,bad)
    def test_exact_thirty_to_fifty_migration_preserves_pending_and_terminal_tasks(self):
        cfg = policy(); old = cfg | {'maximum_new_tasks_per_day':30, 'maximum_worker_model_attempts_per_utc_day':400}
        self.assertEqual(digest(old), '7c60c698059b659a0e3111f49592dbf0d00daecda14e4a1976d49bf27346b0c1')
        for role in ('monitor','worker'):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as root:
                ledger = state(root,role,old)
                ledger.update(tasks={status:{'status':status,'executions':2,'enrolled_at':CLOCK,
                    'policy_sha256':digest(old),'pipeline_commit':'1'*40,'forecast':{'probability':.4}}
                    for status in ('queued','running','retry_wait','completed','baseline_only','blocked')},
                    provider_reservations=[{'kind':'model','status':'failed'}],
                    daily={'2026-10-12':99}, enrollment_daily={'2026-10-12':30})
                write(Path(root)/'state.json',ledger); raw=(Path(root)/'state.json').read_bytes()
                for bad in (cfg | {'maximum_new_tasks_per_day':51}, cfg | {'enrollment_window_hours':25},
                            cfg | {'maximum_worker_model_attempts_per_task':61}):
                    with self.assertRaises(ValueError): state(root,role,bad)
                    self.assertEqual((Path(root)/'state.json').read_bytes(),raw)
                migrated=state(root,role,cfg)
                for field in ('tasks','provider_reservations','daily','enrollment_daily'):
                    self.assertEqual(migrated[field],ledger[field])
                self.assertFalse(migrated['policy_migrations'][-1]['budget_reset'])
                self.assertEqual((Path(root)/'state.json').read_bytes(),raw)
                write(Path(root)/'state.json',migrated)
                self.assertEqual(state(root,role,cfg),migrated)
    def test_artifact_redirect_drops_github_token_and_rejects_untrusted_host(self):
        from urllib.request import Request
        request = Request('https://api.github.com/repos/example/repo/actions/artifacts/1/zip',
                          headers={'Authorization': 'Bearer fixture-only', 'Accept': 'application/vnd.github+json'})
        redirect = ArtifactRedirect()
        response = redirect.redirect_request(request, None, 302, 'Found', {},
            'https://productionresults.blob.core.windows.net/path?sig=fixture')
        self.assertIsNone(response.get_header('Authorization'))
        same = redirect.redirect_request(request, None, 302, 'Found', {}, 'https://api.github.com/another-path')
        self.assertEqual(same.get_header('Authorization'), 'Bearer fixture-only')
        with self.assertRaises(ValueError):
            redirect.redirect_request(request, None, 302, 'Found', {}, 'https://untrusted.example/file')
    def test_monitor_replay_preserves_enrolled_snapshot(self):
        class MarketHTTP:
            calls = 0
            def get(self, url, params=None):
                self.calls += 1
                meta = {'received_at': CLOCK, 'receipt': 'fixture', 'response_sha256': 'raw-fixture'}
                if '/tags/slug/' in url:
                    return {'slug': url.rsplit('/', 1)[1], 'id': '1'}, meta
                if 'gamma-api' in url and url.endswith('/events'):
                    return [{'id': 'event1', 'title': 'Fed December meeting', 'markets': [raw_poly()]}], meta
                if 'gamma-api' in url and url.endswith('/markets'):
                    return [raw_poly()], meta
                if 'gamma-api' in url and '/markets/' in url:
                    return raw_poly(), meta
                if 'clob' in url:
                    return {'asset_id': 'yes_asset', 'bids': [{'price': '.43', 'size': '50'}],
                            'asks': [{'price': '.47', 'size': '50'}]}, meta
                return {'events': [], 'markets': []}, meta
        with tempfile.TemporaryDirectory() as root:
            report = monitor_run(root, http=MarketHTTP(), clock=CLOCK)
            before = digest(read(Path(root)/'state.json')['tasks'])
            self.assertEqual(report['queued_families'], 1)
            monitor_run(root, http=MarketHTTP(), clock=CLOCK)
            self.assertEqual(digest(read(Path(root)/'state.json')['tasks']), before)
            self.assertGreater(verify(root), 0)
    def test_published_during_scan_is_not_a_future_question(self):
        class DuringScan:
            calls = 0
            def get(self, url, params=None):
                self.calls += 1
                meta = {'received_at': '2026-10-12T00:00:30Z', 'receipt': 'fixture', 'response_sha256': 'raw'}
                raw = raw_poly() | {'createdAt': '2026-10-12T00:00:10Z'}
                if '/tags/slug/' in url: return {'slug': url.rsplit('/', 1)[1], 'id': '1'}, meta
                if 'gamma-api' in url and url.endswith('/events'): return [{'id': 'event1', 'title': 'Fed', 'markets': [raw]}], meta
                if 'gamma-api' in url and url.endswith('/markets'): return [raw], meta
                if 'gamma-api' in url and '/markets/' in url: return raw, meta
                if 'clob' in url: return {'asset_id': 'yes_asset', 'bids': [{'price': '.43', 'size': '50'}], 'asks': [{'price': '.47', 'size': '50'}]}, meta
                return {'events': [], 'markets': []}, meta
        with tempfile.TemporaryDirectory() as root:
            result = monitor_run(root, http=DuringScan(), clock=CLOCK)
            self.assertEqual(result['queued_families'], 1)
            self.assertEqual(read(Path(root)/'state.json')['enrollment_cutoff_utc'], '2026-10-12T00:00:30Z')
    def test_integrity_and_policy_drift(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = state(root, 'monitor', policy()); write(Path(root)/'state.json', ledger); seal(root)
            self.assertEqual(verify(root), 1)
            with self.assertRaises(ValueError): state(root, 'monitor', policy() | {'maximum_spread': .1})
            write(Path(root)/'state.json', {} )
            with self.assertRaises(ValueError): verify(root)
    def test_missing_ledger_refuses_reset(self):
        with tempfile.TemporaryDirectory() as root:
            write(Path(root)/'old-evidence.json', {})
            with self.assertRaises(ValueError): state(root, 'worker', policy())
    def test_zip_traversal(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z: z.writestr('../escape.json', '{}')
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError): extract(data.getvalue(), root)
    def test_restore_size_remains_bounded_before_extracting_any_files(self):
        from ForecastAgent.market_repricing import github_state
        self.assertEqual(github_state.MAX_EXPANDED_BYTES,4*1024**3)
        data = io.BytesIO()
        with zipfile.ZipFile(data,'w') as z: z.writestr('state.json','{}')
        with tempfile.TemporaryDirectory() as root, patch.object(github_state,'MAX_EXPANDED_BYTES',1):
            with self.assertRaisesRegex(ValueError,'Expanded state exceeds bound'): extract(data.getvalue(),root)
            self.assertEqual(list(Path(root).iterdir()),[])
    def test_grid_mean_not_event_probability(self):
        self.assertAlmostEqual(mean({'probabilities': {'p'+str(i): 1 if i == 6 else 0 for i in range(21)}}), .3)
        with self.assertRaises(ValueError): mean({'probabilities': {'p0': 1}})
    def test_budget_reservation_consumes_failure_and_restart(self):
        import urllib.request
        cfg = policy() | {'maximum_worker_model_attempts_per_dispatch': 1}
        ledger = {'provider_reservations': []}
        with tempfile.TemporaryDirectory() as root:
            with patch('urllib.request.urlopen', side_effect=RuntimeError('transport')):
                with provider_budget(root, ledger, 'task', cfg):
                    with self.assertRaises(RuntimeError): urllib.request.urlopen('https://openrouter.ai/api/alpha/decisions')
                with provider_budget(root, ledger, 'task', cfg):
                    with self.assertRaises(ProviderCap): urllib.request.urlopen('https://openrouter.ai/api/alpha/decisions')
            self.assertEqual(len(read(Path(root)/'state.json')['provider_reservations']), 1)
    def test_verify_worker_then_no_duplicate_inference(self):
        with tempfile.TemporaryDirectory() as base:
            monitor, worker = Path(base)/'monitor', Path(base)/'worker'
            cfg = policy(); source = state(monitor, 'monitor', cfg)
            source['last_attempt_at'] = now()
            m = normalize('polymarket', raw_poly())
            source['tasks'][m['key']] = {'key': m['key'], 'market': m, 'status': 'queued'}
            write(monitor/'state.json', source); seal(monitor)
            report = run(worker, monitor, verification_only=True)
            self.assertEqual(report['state_distribution'], {'queued': 1})
            entry = worker / 'tasks' / digest(m['key']) / 'entry-quote.json'
            before = entry.read_bytes()
            self.assertEqual(read(entry)['source_task_sha256'], digest(source['tasks'][m['key']]))
            saved = read(worker/'state.json'); saved['tasks'][m['key']]['status'] = 'completed'
            write(worker/'state.json', saved); seal(worker)
            def fail(*unused): raise AssertionError('Completed task should not collect again')
            run(worker, monitor, collector=fail, forecaster=fail)
            self.assertEqual(read(worker/'state.json')['tasks'][m['key']]['executions'], 0)
            self.assertEqual(entry.read_bytes(), before)


if __name__ == '__main__': unittest.main()
