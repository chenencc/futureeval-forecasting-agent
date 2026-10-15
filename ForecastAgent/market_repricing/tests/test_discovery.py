"""Coverage, restart and failure tests; no providers or models are contacted."""
import copy
from datetime import timedelta
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.market_repricing.common import digest, instant, policy, read, state, write
from ForecastAgent.market_repricing.discovery import scan, new_window, VERSION
from ForecastAgent.market_repricing.monitor import ingest, run

CLOCK = '2026-10-15T04:00:00Z'


def contract(ident, stamp=CLOCK, venue='polymarket'):
    if venue == 'kalshi':
        return {'ticker': ident, 'created_time': stamp}
    return {'id': ident, 'createdAt': stamp}


class HTTP:
    def __init__(self, *responses):
        self.responses, self.requests, self.calls = iter(responses), [], 0

    def get(self, url, params=None):
        self.calls += 1
        self.requests.append((url, params))
        raw = next(self.responses)
        if isinstance(raw, Exception):
            raise raw
        return raw, {'received_at': CLOCK, 'response_sha256': digest(raw), 'receipt': str(self.calls)}


def poly(*rows, cursor=None):
    return {'markets': list(rows), 'next_cursor': cursor}


class Discovery(unittest.TestCase):
    def setUp(self):
        self.cfg = policy()
        self.saved, self.rows = {}, []

    def go(self, http, clock=CLOCK, venue='polymarket', limit=5, consume=None):
        return scan(http, venue, self.cfg | {'maximum_discovery_pages_per_venue': limit}, clock,
                    self.saved, consume or (lambda rows, received: self.rows.extend(rows)))

    def test_empty_page_completes_exact_window(self):
        audit = self.go(HTTP(poly()))
        self.assertTrue(audit['complete'])
        self.assertEqual(audit['covered_through'], instant(CLOCK).isoformat())
        self.assertIsNone(self.saved['active'])
        self.assertFalse(audit['platform_wide_completeness_claimed'])

    def test_page_budget_yields_then_resumes_exact_cursor_after_json_restart(self):
        http = HTTP(poly(contract('1'), cursor='opaque'))
        audit = self.go(http, limit=1)
        self.assertFalse(audit['complete'])
        self.assertEqual(audit['reason'], 'page_budget_yield')
        self.saved = json.loads(json.dumps(self.saved))
        http = HTTP(poly(contract('2', '2026-10-15T03:59:00Z')))
        self.assertTrue(self.go(http, limit=1)['complete'])
        self.assertEqual(http.requests[0][1]['after_cursor'], 'opaque')
        self.assertEqual([r['id'] for r in self.rows], ['1', '2'])

    def test_future_new_head_does_not_change_older_frozen_cursor(self):
        self.go(HTTP(poly(contract('1'), cursor='old')), limit=1)
        later = '2026-10-15T04:10:00Z'
        http = HTTP(poly(contract('new', later), cursor='head'),
                    poly(contract('2', '2026-10-15T03:59:00Z'), cursor='more'))
        audit = self.go(http, later, limit=2)
        self.assertEqual(http.requests[1][1]['after_cursor'], 'old')
        self.assertEqual(self.saved['active']['end'], instant(CLOCK).isoformat())
        self.assertFalse(audit['complete'])
        self.assertIn('new', [r['id'] for r in self.rows])
        self.assertEqual(audit['pages'][0]['role'], 'fresh_head_not_coverage')

    def test_completed_old_window_is_not_current_coverage(self):
        self.go(HTTP(poly(contract('1'), cursor='old')), limit=1)
        audit = self.go(HTTP(poly(), poly()), '2026-10-15T04:10:00Z', limit=2)
        self.assertFalse(audit['complete'])
        self.assertTrue(audit['pending_window'])
        self.assertFalse(audit['pending_cursor'])
        self.assertEqual(audit['coverage_lag_seconds'], 600)

    def test_can_finish_old_and_new_window_with_remaining_budget(self):
        self.go(HTTP(poly(contract('1'), cursor='old')), limit=1)
        audit = self.go(HTTP(poly(), poly(), poly()), '2026-10-15T04:10:00Z', limit=3)
        self.assertTrue(audit['complete'])
        self.assertEqual(len(audit['completed_windows_this_dispatch']), 2)

    def test_overlap_is_twenty_minutes_without_weakening_two_hour_age(self):
        self.go(HTTP(poly(contract('1'))))
        http = HTTP(poly())
        audit = self.go(http, '2026-10-15T04:10:00Z')
        self.assertEqual(instant(audit['window_start']), instant(CLOCK) - timedelta(minutes=20))
        self.assertEqual(self.cfg['maximum_candidate_age_hours'], 2)

    def test_failed_page_keeps_cursor_and_successful_rows(self):
        audit = self.go(HTTP(poly(contract('1'), cursor='next'), RuntimeError('fixture')))
        self.assertFalse(audit['complete'])
        self.assertEqual(self.saved['active']['cursor'], 'next')
        self.assertEqual([r['id'] for r in self.rows], ['1'])
        self.assertEqual(self.saved['active']['pages'], 1)

    def test_repeated_cursor_and_empty_continuation_never_complete(self):
        for response in (poly(cursor='repeat'), poly(contract('1'), cursor='repeat')):
            with self.subTest(response=response):
                self.saved, self.rows = {}, []
                self.go(HTTP(poly(contract('1'), cursor='repeat')), limit=1)
                audit = self.go(HTTP(response), limit=1)
                self.assertFalse(audit['complete'])
                self.assertEqual(self.saved['active']['pages'], 1)

    def test_malformed_rows_and_unordered_poly_page_fail_closed(self):
        for rows in ([{'id': 'bad'}], [contract('bad', '2026-10-15T04:00:00')],
                     [contract('1', '2026-10-15T03:59:00Z'), contract('2')]):
            with self.subTest(rows=rows):
                self.saved, self.rows = {}, []
                audit = self.go(HTTP(poly(*rows)))
                self.assertFalse(audit['complete'])
                self.assertEqual(self.saved['active']['pages'], 0)
                self.assertFalse(self.rows)

    def test_ingestion_failure_cannot_acknowledge_page(self):
        def fail(rows, receipt):
            raise ValueError('fixture failed native ingestion')
        audit = self.go(HTTP(poly(contract('1'), cursor='next')), consume=fail)
        self.assertFalse(audit['complete'])
        self.assertIsNone(self.saved['active']['cursor'])

    def test_duplicate_identity_is_ingested_once_per_window(self):
        self.go(HTTP(poly(contract('1'), contract('1'), cursor='a'),
                     poly(contract('1'), contract('2'))))
        self.assertEqual([r['id'] for r in self.rows], ['1', '2'])

    def test_creation_boundary_and_future_items_are_not_ingested(self):
        audit = self.go(HTTP(poly(contract('future', '2026-10-15T04:01:00Z'),
                                    contract('edge', '2026-10-15T02:00:00Z'),
                                    contract('old', '2026-10-15T01:59:59Z'), cursor='unused')))
        self.assertTrue(audit['complete'])
        self.assertEqual([r['id'] for r in self.rows], ['edge'])

    def test_expired_unfinished_window_is_an_explicit_historical_gap(self):
        self.go(HTTP(poly(contract('1'), cursor='old')), limit=1)
        audit = self.go(HTTP(poly()), '2026-10-15T07:00:00Z')
        self.assertTrue(audit['complete'])
        self.assertEqual(audit['historical_gap_count'], 1)
        self.assertEqual(self.saved['gaps'][0]['reason'], 'unfinished_window_expired')

    def test_outage_cannot_be_hidden_by_advancing_watermark(self):
        self.go(HTTP(poly()))
        audit = self.go(HTTP(poly()), '2026-10-15T10:00:00Z')
        self.assertTrue(audit['complete'])
        self.assertEqual(audit['historical_gap_count'], 1)
        self.assertEqual(self.saved['gaps'][0]['start'], instant(CLOCK).isoformat())

    def test_kalshi_bounds_stay_fixed_on_resume_and_cursor_is_opaque(self):
        http = HTTP({'markets': [contract('a', venue='kalshi')], 'cursor': 'opaque'})
        self.go(http, venue='kalshi', limit=1)
        before = copy.deepcopy(http.requests[0][1])
        http = HTTP({'markets': [], 'cursor': ''})
        audit = self.go(http, venue='kalshi', limit=1)
        self.assertTrue(audit['complete'])
        self.assertEqual(http.requests[0][1], before | {'cursor': 'opaque'})
        self.assertNotIn('min_close_ts', before)
        self.assertEqual(before['limit'], 1000)

    def test_unordered_kalshi_page_uses_server_window_not_assumed_sort(self):
        audit = self.go(HTTP({'markets': [contract('a', '2026-10-15T01:00:00Z', 'kalshi'),
                                           contract('b', CLOCK, 'kalshi')]}), venue='kalshi')
        self.assertTrue(audit['complete'])
        self.assertEqual([r['ticker'] for r in self.rows], ['b'])

    def test_old_parent_new_child_is_ingested_with_original_family(self):
        raw = contract('child') | {'events': [{'id': 'old-parent', 'createdAt': '2025-01-01T00:00:00Z'}]}
        ledger = {'tasks': {}, 'markets': {}}
        audit = self.go(HTTP(poly(raw)), consume=lambda rows, receipt: ingest(ledger, 'polymarket', rows, self.cfg, receipt))
        self.assertTrue(audit['complete'])
        self.assertEqual(ledger['markets']['polymarket:child']['family'], 'polymarket:old-parent')

    def test_unknown_protocol_is_not_silently_reset(self):
        self.saved = {'version': 'future-protocol'}
        with self.assertRaises(ValueError):
            self.go(HTTP(poly()))

    def test_clock_regression_does_not_rewind_completed_coverage(self):
        self.go(HTTP(poly()))
        before = copy.deepcopy(self.saved)
        with self.assertRaisesRegex(ValueError, 'regressed'):
            self.go(HTTP(poly()), '2026-10-15T03:00:00Z')
        self.assertEqual(self.saved, before)

    def test_resume_never_exceeds_page_allowance_even_with_head_failure(self):
        self.go(HTTP(poly(contract('1'), cursor='old')), limit=1)
        http = HTTP(RuntimeError('head unavailable'), poly(contract('2', '2026-10-15T03:59:00Z'), cursor='next'))
        audit = self.go(http, '2026-10-15T04:10:00Z', limit=2)
        self.assertEqual(http.calls, 2)
        self.assertEqual(self.saved['active']['cursor'], 'next')
        self.assertFalse(audit['complete'])

    def test_pending_prior_deployment_retains_native_budget_and_capture_identity(self):
        from ForecastAgent.market_repricing.deployment import validate_pending
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'worker'
            original = {'tasks': {'old': {'status': 'queued', 'pipeline_commit':
                'f154a406a9611ebd4a64fc4077bb74778659b808', 'executions': 2}},
                'provider_reservations': [{'kind': 'model', 'status': 'failed'}], 'daily': {'2026-10-15': 71}}
            write(root / 'state.json', original)
            audit = validate_pending(root, Path(folder) / 'monitor', {'pipeline_commit': 'a' * 40})
            self.assertEqual(audit['pending_checked'], 1)
            self.assertEqual(read(root / 'state.json'), original)
            self.assertFalse(audit['budget_reset'])

    def test_real_checkpoint_gate_is_read_only_for_native_state_and_inventory(self):
        from ForecastAgent.market_repricing.common import seal
        from ForecastAgent.market_repricing.tests.verify_discovery_preservation import check
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'monitor'
            ledger = state(root, 'monitor', self.cfg)
            ledger['tasks']['frozen'] = {'status': 'completed', 'enrolled_at': CLOCK, 'executions': 2}
            ledger['daily'] = {'2026-10-15': 123}
            write(root / 'state.json', ledger)
            seal(root)
            before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
            check(root, Path(folder) / 'result.json')
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}, before)
            self.assertEqual(read(Path(folder) / 'result.json')['native_tasks_preserved'], 1)

    def test_monitor_coverage_comes_from_contracts_and_preserves_terminal_tasks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ledger = state(root, 'monitor', self.cfg)
            ledger['tasks'] = {}
            before = copy.deepcopy(ledger['identity'])
            write(root / 'state.json', ledger)
            http = HTTP(poly(), {'markets': []})
            # An incomplete event seed cannot hide complete contract windows.
            with patch('ForecastAgent.market_repricing.monitor.discover_events', return_value=([], {'complete': False})):
                report = run(root, http=http, clock=CLOCK)
            self.assertTrue(report['discovery_complete'])
            self.assertEqual(report['public_http_attempts'], 2)
            saved = read(root / 'state.json')
            self.assertEqual(saved['identity'], before)
            self.assertEqual(saved['tasks'], {})
            self.assertEqual(set(saved['discovery_windows']), {'polymarket', 'kalshi'})


if __name__ == '__main__':
    unittest.main()
