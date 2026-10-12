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
from ForecastAgent.market_repricing.monitor import discover, enroll, ingest, label, quote_turns, run as monitor_run
from ForecastAgent.market_repricing.forecast import mean, registry
from ForecastAgent.market_repricing.github_state import extract, find, ArtifactRedirect
from ForecastAgent.market_repricing.worker import ProviderCap, provider_budget, request_for, run


CLOCK = '2026-10-12T00:00:00+00:00'


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
    def test_unopened_waits(self): self.assertEqual(screen(self.m | {'tradable': False}, CLOCK, self.cfg)['status'], 'waiting_market')
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
    def test_twenty_per_shanghai_day_without_lifetime_cap(self):
        ledger = {'markets': {}, 'tasks': {}, 'daily': {}, 'identity': {'policy_sha256': digest(self.cfg)}}
        ingest(ledger, 'polymarket', [raw_poly() | {'id': str(i), 'events': [{'id': str(i)}]} for i in range(50)], self.cfg, CLOCK)
        for m in ledger['markets'].values(): m['last_quote'] = {'status': 'usable', 'received_at': CLOCK}
        enroll(ledger, self.cfg, CLOCK); self.assertEqual(len(ledger['tasks']), 20)
        # Local midnight is 16:00 UTC. Failures do not refund admissions.
        for task in ledger['tasks'].values(): task['status'] = 'blocked'
        enroll(ledger, self.cfg, '2026-10-12T15:59:59Z'); self.assertEqual(len(ledger['tasks']), 20)
        for m in ledger['markets'].values(): m['last_quote']['received_at'] = '2026-10-12T16:00:00Z'
        enroll(ledger, self.cfg, '2026-10-12T16:00:00Z'); self.assertEqual(len(ledger['tasks']), 40)
        enroll(ledger, self.cfg, '2026-10-12T16:01:00Z'); self.assertEqual(len(ledger['tasks']), 40)
        self.assertEqual(ledger['enrollment_daily'], {'2026-10-12': 20, '2026-10-13': 20})
    def test_enrollment_requires_quote_from_current_dispatch(self):
        ledger = {'markets': {}, 'tasks': {}, 'daily': {}, 'last_attempt_at': CLOCK,
                  'identity': {'policy_sha256': digest(self.cfg)}}
        ingest(ledger, 'polymarket', [raw_poly()], self.cfg, CLOCK)
        market = next(iter(ledger['markets'].values()))
        market['last_quote'] = {'status': 'usable', 'received_at': '2026-10-11T23:59:59Z'}
        enroll(ledger, self.cfg, CLOCK); self.assertFalse(ledger['tasks'])
        market['last_quote']['received_at'] = CLOCK
        enroll(ledger, self.cfg, CLOCK); self.assertEqual(len(ledger['tasks']), 1)
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
    def test_publication_24h_boundary_and_future_clock(self):
        for stamp, accepted in [('2026-10-11T00:00:00Z', True), ('2026-10-10T23:59:59Z', False),
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
        for bid, ask, size, status in [(.1, .9, 100, 'wide_spread'), (.45, .5, 1, 'shallow_book'), (.6, .5, 50, 'crossed_book')]:
            q = quote('polymarket', {'bids': [{'price': bid, 'size': size}], 'asks': [{'price': ask, 'size': size}]}, self.cfg, self.meta)
            self.assertEqual(q['status'], status); self.assertIsNone(q['midpoint'])


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


class Persistence(unittest.TestCase):
    def test_authorized_policy_migration_preserves_tasks_and_failed_reservations(self):
        cfg = policy()
        legacy = dict(cfg)
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
