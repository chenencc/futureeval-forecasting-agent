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
from ForecastAgent.market_repricing.monitor import discover, enroll, ingest, label, run as monitor_run
from ForecastAgent.market_repricing.forecast import mean, registry
from ForecastAgent.market_repricing.github_state import extract, find
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
        for m in ledger['markets'].values(): m['last_quote'] = {'status': 'usable'}
        enroll(ledger, self.cfg, CLOCK); enroll(ledger, self.cfg, CLOCK)
        self.assertEqual(len(ledger['tasks']), 1); self.assertEqual(ledger['daily']['2026-10-12'], 1)
        before = digest(ledger['tasks'])
        for market in ledger['markets'].values(): market['metadata_versions'].append({'new': 'metadata'})
        self.assertEqual(digest(ledger['tasks']), before)
    def test_daily_and_campaign_caps(self):
        ledger = {'markets': {}, 'tasks': {}, 'daily': {}, 'identity': {'policy_sha256': digest(self.cfg)}}
        ingest(ledger, 'polymarket', [raw_poly() | {'id': str(i), 'events': [{'id': str(i)}]} for i in range(10)], self.cfg, CLOCK)
        for m in ledger['markets'].values(): m['last_quote'] = {'status': 'usable'}
        enroll(ledger, self.cfg, CLOCK); self.assertEqual(len(ledger['tasks']), 5)
        enroll(ledger, self.cfg | {'maximum_active_families': 6}, '2026-10-13T00:00:00Z'); self.assertEqual(len(ledger['tasks']), 6)
    def test_partial_pagination_not_complete(self):
        cfg = self.cfg | {'maximum_discovery_pages_per_venue': 1}
        rows, audit = discover(FakeHTTP([raw_poly()] * 100), 'polymarket', cfg, CLOCK)
        self.assertEqual(len(rows), 100); self.assertFalse(audit['complete'])
    def test_request_internal_not_platform_id(self):
        request = request_for(self.m)
        self.assertTrue(request['synthetic_compatibility_id']); self.assertTrue(request['id'].isdigit())
        self.assertEqual(request['market_research_key'], self.m['key'])


class Books(unittest.TestCase):
    def setUp(self): self.cfg = policy(); self.meta = {'received_at': CLOCK}
    def test_poly_best_prices_not_first_array_item(self):
        raw = {'bids': [{'price': '.3', 'size': '30'}, {'price': '.45', 'size': '25'}],
               'asks': [{'price': '.7', 'size': '30'}, {'price': '.50', 'size': '30'}]}
        q = quote('polymarket', raw, self.cfg, self.meta)
        self.assertAlmostEqual(q['midpoint'], .475); self.assertEqual(q['status'], 'usable')
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
            saved = read(worker/'state.json'); saved['tasks'][m['key']]['status'] = 'completed'
            write(worker/'state.json', saved); seal(worker)
            def fail(*unused): raise AssertionError('Completed task should not collect again')
            run(worker, monitor, collector=fail, forecaster=fail)
            self.assertEqual(read(worker/'state.json')['tasks'][m['key']]['executions'], 0)


if __name__ == '__main__': unittest.main()
