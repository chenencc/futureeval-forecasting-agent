"""Offline regression and counterexamples for financial acquisition routing."""
import base64
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ForecastAgent.market_pulse.financial import issuer_profile, url_scope, page_scope, research_contract
from ForecastAgent.market_pulse.quality import diagnostics, page_diagnostics
from ForecastAgent.market_pulse.collection import acquisition_policy, prepare, overlay, scoped_pages
from ForecastAgent.supplement.stage import digest, save


def request(issuer='Tesla'):
    return {'id': '123', 'question': f'First quarterly revenues? ({issuer})',
        'resolution_criteria': '* Apple Q4 FY2026 ([source](https://finance.yahoo.com/calendar/earnings?symbol=AAPL))\n'
            '* Tesla Q3 FY2026 ([source](https://seekingalpha.com/symbol/TSLA/earnings))\n'
            '* AMD Q3 FY2026 ([source](https://seekingalpha.com/symbol/AMD/earnings))\n'
            'Filed with https://www.sec.gov/search-filings',
        'background': '* [Apple](https://www.alphaquery.com/stock/AAPL/earnings-history)\n'
            '* [Tesla](https://www.alphaquery.com/stock/TSLA/earnings-history)', 'fine_print': ''}


class ScopeTests(unittest.TestCase):
    def test_tickers_come_from_observed_target_links(self):
        p = issuer_profile(request())
        self.assertEqual(p['observed_symbols'], ['TSLA'])
        self.assertFalse(p['ticker_identity_verified'])
        self.assertIsNone(p['cik'])

    def test_foreign_calendar_and_ticker_excluded(self):
        p = issuer_profile(request())
        self.assertEqual(url_scope('https://finance.yahoo.com/calendar/earnings?symbol=AAPL', p), 'other_issuer')
        self.assertEqual(url_scope('https://seekingalpha.com/symbol/AMD/earnings', p), 'other_issuer')
        self.assertEqual(url_scope('https://finance.yahoo.com/quote/TSLA/', p), 'target')

    def test_unknown_independent_and_common_authority_retained(self):
        p = issuer_profile(request())
        for url in ('https://www.sec.gov/search-filings', 'https://www.reuters.com/business/story',
                    'https://www.example.com/stock/UNVERIFIED/earnings'):
            self.assertEqual(url_scope(url, p), 'unknown')
            self.assertTrue(page_scope(url, {'content': 'Independent background'}, p)['eligible_for_target'])

    def test_url_filter_does_not_establish_saved_calendar_identity(self):
        p = issuer_profile(request('Apple'))
        url = 'https://finance.yahoo.com/calendar/earnings?symbol=AAPL'
        self.assertFalse(page_scope(url, {'content': 'Earnings Calendar\nPepsiCo PEP\nNovaGold NG'}, p)['eligible_for_target'])
        self.assertTrue(page_scope(url, {'content': 'Earnings Calendar\nApple AAPL October 29'}, p)['eligible_for_target'])

    def test_label_substring_does_not_match_another_issuer(self):
        p = issuer_profile(request('Meta'))
        self.assertEqual(url_scope('https://metadata.example.com/news', p), 'unknown')

    def test_unresolved_label_retains_unknown_sources(self):
        r = request(); r['question'] = 'Unclassified economic question'
        p = issuer_profile(r)
        self.assertEqual(p['identity_status'], 'unresolved')
        self.assertEqual(url_scope('https://finance.yahoo.com/quote/AAPL/', p), 'unknown')

    def test_frontier_filters_before_truncation_and_restores_aliases(self):
        from ForecastAgent.runtime import source_frontier, gap_repair
        original = source_frontier.unread_candidates
        bundle = {'request': request(), 'source_leads': {}, 'pages': {}}
        with acquisition_policy():
            rows = source_frontier.unread_candidates(bundle, limit=20)
            self.assertTrue(rows)
            self.assertFalse(any(url_scope(r['url'], issuer_profile(request())) == 'other_issuer' for r in rows))
            self.assertIs(gap_repair.unread_candidates, source_frontier.unread_candidates)
        self.assertIs(source_frontier.unread_candidates, original)
        self.assertIs(gap_repair.unread_candidates, original)

    def test_catalog_blocks_direct_foreign_fetch_but_keeps_original_links(self):
        from ForecastAgent.runtime.retrieval import RetrievalTask
        bundle = {'request': request(), 'mode': 'live',
                  'source_leads': {'x': {'url': 'https://seekingalpha.com/symbol/AMD/earnings'}},
                  'searches': [], 'exa_searches': [], 'quarantine': [], 'pages': {}}
        task = SimpleNamespace(bundle=bundle, verified_only=False)
        with acquisition_policy():
            self.assertEqual(RetrievalTask.catalog(task), {})
        self.assertIn('x', bundle['source_leads'])


class QualityTests(unittest.TestCase):
    def test_actual_cdn_shell_rejected(self):
        text = 'Reference #18.ab30d417.1791460351.271af340\nhttps://errors.edgesuite.net/18.ab30d417.1791460351.271af340'
        self.assertFalse(diagnostics(text)['usable_text'])
        self.assertEqual(diagnostics(text)['state'], 'access_interstitial')

    def test_brief_financial_body_and_reference_number_retained(self):
        for text in ('Tesla reported quarterly GAAP diluted EPS of $0.50 on revenue of $25 billion.',
                     'Reference #18 is a filing footnote. Quarterly revenue increased 12%.'):
            self.assertTrue(diagnostics(text)['usable_text'])

    def test_flattened_financial_menu_rejected_but_short_report_retained(self):
        text = '\n'.join(['Skip to main content', 'Apple', 'Store', 'Mac', 'iPad', 'iPhone',
            '0', '+', 'Investor Relations', 'Stock Price', 'SEC Filings', 'Financial Data', 'Quarterly Earnings Reports'])
        self.assertEqual(diagnostics(text)['state'], 'navigation_shell')
        self.assertTrue(diagnostics(text+'\nRevenue was $100 billion in the quarter.')['usable_text'])

    def test_same_raw_access_rejection_cannot_be_promoted(self):
        prior = {'sha256': 'same', 'content': 'old shell', 'body_diagnostics': {'usable_text': False, 'state': 'access_interstitial'}}
        new = {'sha256': 'same', 'content': 'Reparsed body could otherwise be readable.'}
        self.assertFalse(page_diagnostics(new, prior)['usable_text'])
        new['sha256'] = 'new-version'
        self.assertTrue(page_diagnostics(new, prior)['usable_text'])

    def test_parser_gap_recovery_is_allowed(self):
        prior = {'sha256': 'same', 'content': '', 'parse_failure': {'type': 'ValueError'},
                 'body_diagnostics': {'usable_text': False, 'state': 'empty_text'}}
        self.assertTrue(page_diagnostics({'sha256': 'same', 'content': 'Quarterly revenue: $25 billion.'}, prior)['usable_text'])

    def test_reparse_keeps_capture_rejection_through_generic_recheck(self):
        from ForecastAgent.supplement import stage
        raw = b'An otherwise readable retained response.'
        page = {'url': 'https://example.com/filing', 'content_type': 'text/plain',
                'sha256': hashlib.sha256(raw).hexdigest(), 'raw_response_base64': base64.b64encode(raw).decode(),
                'capture_status': {'usable_text': False, 'category': 'http_error', 'http_status': 403}}
        with acquisition_policy():
            parsed = stage.parse_saved(page)
            self.assertFalse(parsed['body_diagnostics']['usable_text'])
            self.assertFalse(stage.body_diagnostics(parsed['content'], documents=parsed['documents'])['usable_text'])
            self.assertEqual(parsed['parent_capture_status']['http_status'], 403)
            # An unrelated fresh 200 response with the same prose must not
            # inherit this rejected response's version-specific diagnostics.
            self.assertTrue(stage.body_diagnostics(parsed['content'])['usable_text'])

    def test_bad_old_sidecar_flag_is_rechecked_without_mutating_parent(self):
        bundle = {'request': request(), 'pages': {}, 'gaps': [{'existing': 'prior gap'}]}
        original = copy.deepcopy(bundle)
        with TemporaryDirectory() as folder:
            root = Path(folder); task = root / 'tasks/123'
            page = {'url': 'https://ir.tesla.com/', 'content': 'Reference #18.x\nhttps://errors.edgesuite.net/18.x'}
            save(task / 'captures/a.json', page)
            save(task / 'supplement.json', {'task_id': '123', 'parent_bundle_json_sha256': digest(bundle),
                'captures': {page['url']: {'file': 'captures/a.json', 'json_sha256': digest(page), 'readable': True}},
                'analysis_handoff': {}, 'remaining_gaps': []})
            result = overlay(bundle, root, '123')
            self.assertEqual(result['pages'], {})
            self.assertIn(page['url'], result['financial_audit_pages'])
            self.assertEqual(result['gaps'][0], bundle['gaps'][0])
            self.assertEqual(bundle, original)
            bad = json.loads((task / 'captures/a.json').read_text()); bad['content'] = 'Tampered'
            save(task / 'captures/a.json', bad)
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                overlay(bundle, root, '123')


class GuidanceTests(unittest.TestCase):
    def test_original_rules_and_profile_hash_remain_bound(self):
        raw = request(); prepared = prepare(raw)
        for key in raw:
            self.assertEqual(prepared[key], raw[key])
        self.assertIn('collection.py', prepared['financial_acquisition_policy']['source_sha256'])
        self.assertEqual(prepare(prepared), prepared)
        prepared['financial_acquisition_policy']['source_sha256']['financial.py'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            prepare(prepared)

    def test_available_research_and_settlement_have_separate_objectives(self):
        contract = research_contract(issuer_profile(request()))
        self.assertEqual(len(contract['objectives']), 5)
        settlement = contract['objectives'][-1]['instruction']
        self.assertIn('pending_publication', settlement)
        self.assertIn('already published', contract['objectives'][0]['instruction'])
        self.assertEqual(contract['search_limits_unchanged']['tavily_basic_lifetime_max'], 3)

    def test_runtime_system_and_task_view_receive_same_contract(self):
        from ForecastAgent.runtime import guidance, task_protocol
        bundle = {'request': request(), 'request_hash': 'test', 'mode': 'live',
                  'control': {'operating_clock_utc': '2026-10-08T12:00:00Z'}}
        task = SimpleNamespace(bundle=bundle, cutoff=None)
        original = guidance.collection_system
        with patch.object(guidance, 'collection_system', return_value='Baseline instructions'):
            with acquisition_policy():
                text = guidance.collection_system(task, [])
                view = task_protocol.task_view(task)
                self.assertIn('Baseline instructions', text)
                self.assertIn('published_financial_history', text)
                self.assertEqual(view['question']['resolution_criteria'], request()['resolution_criteria'])
                self.assertEqual(view['financial_acquisition'], research_contract(issuer_profile(request())))
        self.assertIs(guidance.collection_system, original)

    def test_release_collector_runs_inside_policy_with_bound_request(self):
        from ForecastAgent.market_pulse.collection import collect
        from ForecastAgent.runtime import source_frontier
        observed = {}
        def fake(request, root):
            observed['request'] = request
            observed['rows'] = source_frontier.unread_candidates({'request': request, 'pages': {}, 'source_leads': {}})
            return {'stub': True}
        with patch('ForecastAgent.releases.v1_0_5.collect', side_effect=fake):
            self.assertEqual(collect(request(), Path('unused')), {'stub': True})
        self.assertIn('financial_acquisition_policy', observed['request'])
        self.assertFalse(any('AMD' in r['url'] for r in observed['rows']))


if __name__ == '__main__':
    unittest.main()
