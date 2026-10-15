"""Settlement stops future polling without discarding unfinished history."""
import json
import unittest

from ForecastAgent.market_store.cli import compact_checkpoint
from ForecastAgent.market_store.collector import collect
from ForecastAgent.market_store.store import append
import test_store as fixtures
from test_store import AT, KEY, FakeHTTP


NOW = 1792029600


class SettlementHTTP(FakeHTTP):
    def __init__(self, *, resolved=True, fail=False):
        super().__init__(fail=fail)
        self.resolved = resolved

    def get(self, url, params=None):
        if 'clob.polymarket.com/markets/' in url:
            self.requests += 1
            return {
                'condition_id': 'c', 'closed': True,
                'tokens': [
                    {'outcome': 'Yes', 'token_id': '123456', 'winner': self.resolved},
                    {'outcome': 'No', 'token_id': '2', 'winner': False},
                ],
            }, {'sha256': 'settlement', 'received_at_epoch': NOW}
        raw, receipt = super().get(url, params)
        if 'gamma-api' in url:
            raw.update(closed=True, outcomePrices=['1', '0'])
        return raw, receipt


class ResolvedHistoryTests(unittest.TestCase):
    setUp = fixtures.StoreTests.setUp
    tearDown = fixtures.StoreTests.tearDown

    def test_finalized_market_makes_zero_http_requests(self):
        with self.db:
            append(self.db, 'cursor', KEY, {
                'status': 'finalized', 'scanned_until': NOW, 'observed_at': AT,
            }, AT)
        http = SettlementHTTP()
        report = collect(self.db, self.tmp.name, 'next', now=NOW + 7200, http=http)
        self.assertEqual(http.requests, 0)
        self.assertEqual(report['nonfinalized_markets'], 0)
        self.assertEqual(report['processed'], [])

    def test_explicit_settlement_saves_final_history_then_stops_after_compaction(self):
        report = collect(self.db, self.tmp.name, 'first', now=NOW, http=SettlementHTTP())
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['processed'][0]['status'], 'finalized')
        self.assertGreater(self.db.execute('SELECT count(*) FROM price_points').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT result FROM latest_venue').fetchone()[0], 'yes')
        compact_checkpoint(self.db)
        http = SettlementHTTP()
        collect(self.db, self.tmp.name, 'next', now=NOW + 7200, http=http)
        self.assertEqual(http.requests, 0)

    def test_closed_and_one_dollar_display_without_winner_keeps_polling(self):
        report = collect(self.db, self.tmp.name, 'first', now=NOW,
                         http=SettlementHTTP(resolved=False))
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['processed'][0]['status'], 'caught_up')
        venue = self.db.execute('SELECT status,result FROM latest_venue').fetchone()
        self.assertEqual(venue['status'], 'closed_awaiting_resolution')
        self.assertIsNone(venue['result'])
        http = SettlementHTTP(resolved=False)
        collect(self.db, self.tmp.name, 'next', now=NOW + 7200, http=http)
        self.assertGreater(http.requests, 0)

    def test_failed_final_history_is_retryable_not_falsely_finalized(self):
        report = collect(self.db, self.tmp.name, 'first', now=NOW,
                         http=SettlementHTTP(fail=True))
        self.assertEqual(len(report['errors']), 1)
        cursor = self.db.execute('SELECT status,scanned_until FROM cursors').fetchone()
        self.assertEqual(cursor['status'], 'retry_wait')
        self.assertIsNone(cursor['scanned_until'])
        self.assertEqual(self.db.execute('SELECT result FROM latest_venue').fetchone()[0], 'yes')
        report = collect(self.db, self.tmp.name, 'retry', now=NOW + 7200,
                         http=SettlementHTTP())
        self.assertEqual(report['processed'][0]['status'], 'finalized')

    def test_settled_market_with_old_gaps_retains_backfill_cursor(self):
        with self.db:
            self.db.execute("UPDATE markets SET created_at='2026-10-10T02:00:00Z'")
        report = collect(self.db, self.tmp.name, 'first', now=NOW, http=SettlementHTTP())
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['processed'][0]['status'], 'backfill')
        cursor = json.loads(self.db.execute('SELECT data FROM cursors').fetchone()[0])
        self.assertLess(cursor['scanned_until'], NOW)
        self.assertEqual(cursor['live_scanned_until'], NOW)


if __name__ == '__main__':
    unittest.main()
