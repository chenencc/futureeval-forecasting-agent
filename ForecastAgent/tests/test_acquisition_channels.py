import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.agent import ForecastAgent
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.providers.official import fetch_official, endpoint
from ForecastAgent.evidence.acceptance import collection_acceptance
from ForecastAgent.tests.test_collection import REQUEST, PLAN, URL, page

# Isolate capture integrity from the separately tested required search policy.
REQUEST = {**REQUEST, 'exa_search_policy':'optional'}


def raw_page(payload, url=URL):
    raw = json.dumps(payload).encode()
    return {'url': url, 'content': raw.decode(), 'raw_response_base64': base64.b64encode(raw).decode(),
            'sha256': hashlib.sha256(raw).hexdigest(), 'retrieved_at_utc': '2026-09-30T00:00:00Z'}


BLS = {'status': 'REQUEST_SUCCEEDED', 'Results': {'series': [{'seriesID': 'CUUR0000SA0',
        'data': [{'year': '2026', 'period': 'M08', 'value': '320.0', 'footnotes': [{'code': 'P'}]}]}]}}


def market_snapshot():
    raw = {'events': [{'title': 'Anthropic IPO', 'markets': [{'id': '1'}]}]}
    return {'searched_at': '2026-09-30T00:00:00Z', 'endpoint': 'https://gamma-api.polymarket.com/public-search',
            'raw_response': raw, 'raw_response_sha256': hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'candidates': [{'market_id': '1', 'eligible_for_edge': False}], 'pagination': {'hasMore': True}}


class AcquisitionChannelTests(TestCase):
    def task(self, directory, **extra):
        task = RetrievalTask(Path(directory), {**REQUEST, **extra})
        task.execute('plan_evidence', PLAN, '')
        return task

    def test_official_normalization_keeps_units_dates_footnotes_and_raw(self):
        capture = raw_page(BLS)
        result = fetch_official('bls_cpi', '', 1, lambda url: deepcopy(capture))
        self.assertEqual(result['rows'][0]['period'], 'M08')
        self.assertEqual(result['documents'][0]['metadata']['row'], 1)
        self.assertEqual(result['rows'][0]['footnotes'][0]['code'], 'P')
        self.assertEqual(result['raw_response_base64'], capture['raw_response_base64'])
        self.assertEqual(result['unit_provenance'], 'adapter_catalog')
        with self.assertRaisesRegex(ValueError, 'identity'):
            fetch_official('bls_unemployment', '', 1, lambda url: deepcopy(capture))
        with self.assertRaises(ValueError):
            endpoint('arbitrary_host')
        with self.assertRaises(ValueError):
            endpoint('federal_register', '')

    def test_treasury_and_federal_register_pagination_and_links(self):
        treasury = fetch_official('treasury_debt', '', 1, lambda url: raw_page({'data': [{'record_date': '2026-09-29'}], 'meta': {'total-pages': 3}}))
        self.assertTrue(treasury['pagination']['has_more'])
        fr = fetch_official('federal_register', 'AI', 1, lambda url: raw_page({'results': [{'pdf_url': 'https://www.govinfo.gov/test.pdf',
                    'publication_date': '2026-09-29', 'type': 'Proposed Rule'}], 'next_page_url': 'next', 'count': 100}))
        self.assertIn('not the official legal edition', fr['data_warning'])
        self.assertEqual(fr['documents'][0]['metadata']['row'], 1)
        self.assertEqual(fr['links'], ['https://www.govinfo.gov/test.pdf'])

    @patch('ForecastAgent.runtime.retrieval.fetch_public_page', return_value=raw_page(BLS))
    def test_official_cache_and_failures_use_shared_durable_http_cap(self, fetch):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            args = {'dataset': 'bls_cpi', 'need_ids': ['n']}
            task.execute('collect_official', args, '')
            self.assertTrue(task.execute('collect_official', args, '')['cached'])
            self.assertEqual(fetch.call_count, 1)
            task.bundle['fetch_attempts'].extend([{'status': 'failed'}] * 7)
            task.save()
            restored = RetrievalTask(Path(directory), REQUEST)
            with self.assertRaisesRegex(ValueError, 'budget exhausted'):
                restored.execute('collect_official', {'dataset': 'bls_unemployment', 'need_ids': ['n']}, '')
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(restored.budget()['tavily_basic_remaining'], 3)

    @patch('ForecastAgent.runtime.retrieval.search_markets', return_value=market_snapshot())
    def test_market_cache_refresh_raw_snapshots_and_shared_budget(self, search):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            args = {'query': 'Anthropic IPO', 'need_ids': ['n']}
            first = task.execute('collect_polymarket', args, '')
            self.assertTrue(task.execute('collect_polymarket', args, '')['cached'])
            second = task.execute('collect_polymarket', {**args, 'refresh': True}, '')
            self.assertEqual(search.call_count, 2)
            self.assertEqual(task.bundle['market_snapshots'][second['snapshot_id']]['previous_snapshot_id'], first['snapshot_id'])
            self.assertFalse(second['eligible_for_edge'])
            self.assertEqual(collection_acceptance(task.bundle)['failures'], [])
            task.execute('finish_collection', {'gaps': []}, '')
            package = json.loads((Path(directory) / 'intelligence.json').read_text())
            self.assertEqual(len(package['market_snapshots']), 2)

    @patch('ForecastAgent.runtime.retrieval.search_markets', side_effect=RuntimeError('unavailable'))
    def test_market_failure_consumes_attempt_and_historical_modes_block_network(self, search):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            args = {'query': 'Anthropic', 'need_ids': ['n']}
            with self.assertRaises(RuntimeError):
                task.execute('collect_polymarket', args, '')
            self.assertEqual(task.bundle['fetch_attempts'][0]['status'], 'failed')
            self.assertEqual(task.rescue_candidates(), [])
            self.assertEqual(task.execute('list_sources', {}, '')['extract_eligible'], [])
            for mode in ['historical_strict', 'historical_exploratory']:
                historic = self.task(Path(directory) / mode, mode=mode, as_of_utc='2026-08-20T00:00:00Z')
                with self.assertRaisesRegex(ValueError, 'historical'):
                    historic.execute('collect_polymarket', args, '')
                with self.assertRaisesRegex(ValueError, 'historical'):
                    historic.execute('collect_official', {'dataset': 'bls_cpi', 'need_ids': ['n']}, '')
            self.assertEqual(search.call_count, 1)

    @patch('ForecastAgent.runtime.retrieval.fetch_public_page')
    def test_incremental_changes_keep_old_excerpt_version_and_budget(self, fetch):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            task.bundle['pages'][URL] = page()
            task.execute('record_excerpt', {'url': URL, 'document_index': 1, 'start_char': 0, 'end_char': 20, 'need_ids': ['n']}, '')
            task.execute('finish_collection', {'gaps': []}, '')
            newer = page(); newer['content'] += ' New content.'
            raw = newer['content'].encode()
            newer['documents'][0]['page_content'] = newer['content']
            newer.update(sha256=hashlib.sha256(raw).hexdigest(), raw_response_base64=base64.b64encode(raw).decode())
            fetch.return_value = newer
            result = task.execute('refresh_sources', {'urls': [URL]}, '')
            self.assertEqual(result['items'][0]['result']['state'], 'changed')
            self.assertEqual(len(task.bundle['page_history'][URL]), 1)
            self.assertEqual(result['acceptance']['failures'], [])
            self.assertEqual(len(task.bundle['searches']), 0)
            self.assertEqual(len(task.bundle['fetch_attempts']), 0)
            self.assertEqual(len(task.bundle['update_attempts']), 1)
            self.assertEqual(task.bundle['result']['status'], 'collected')
            self.assertEqual(RetrievalTask(Path(directory), REQUEST).budget()['page_fetch_remaining'], 8)
            self.assertEqual(RetrievalTask(Path(directory), REQUEST).budget()['update_http_remaining'], 23)

    @patch('ForecastAgent.runtime.retrieval.fetch_public_page', side_effect=RuntimeError('unavailable'))
    def test_refresh_failure_preserves_saved_page_and_unknown_urls_are_refused(self, fetch):
        with TemporaryDirectory() as directory:
            task = self.task(directory); task.bundle['pages'][URL] = page()
            result = task.execute('refresh_sources', {'urls': [URL]}, '')
            self.assertFalse(result['items'][0]['ok'])
            self.assertEqual(task.bundle['pages'][URL]['sha256'], page()['sha256'])
            self.assertEqual(task.bundle['fetch_attempts'][0]['status'], 'failed')
            with self.assertRaises(ValueError):
                task.execute('refresh_sources', {'urls': ['https://unknown.org/']}, '')
            self.assertEqual(fetch.call_count, 1)

    def test_response_timing_change_is_not_an_information_change(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            first = fetch_official('bls_cpi', '', 1, lambda url: raw_page({**BLS, 'responseTime': 20}))
            second = fetch_official('bls_cpi', '', 1, lambda url: raw_page({**BLS, 'responseTime': 50}))
            task.store_page(URL, first)
            update = task.store_page(URL, second)
            self.assertEqual(update['state'], 'unchanged')
            self.assertTrue(update['raw_changed'])
            self.assertEqual(len(task.bundle['page_history'][URL]), 1)

    def test_acceptance_detects_tampering_without_fact_scoring(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory); task.bundle['pages'][URL] = page()
            task.bundle['pages'][URL]['sha256'] = 'bad'
            report = collection_acceptance(task.bundle)
            self.assertEqual(report['status'], 'failed')
            self.assertFalse(report['truth_verified'])
            self.assertIn('hash mismatch', report['failures'][0]['issue'])
            self.assertNotIn('score', report)

    def test_saved_market_rules_are_readable_without_http(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            snapshot = market_snapshot()
            snapshot['raw_response']['events'][0]['markets'][0].update(question='Anthropic IPO?', description='Public trading must begin.')
            task.bundle['market_snapshots']['M1'] = {'id': 'M1', 'snapshot': snapshot}
            listing = task.execute('read_market_snapshot', {'snapshot_id': 'M1'}, '')
            self.assertEqual(listing['contracts'][0]['market_id'], '1')
            contract = task.execute('read_market_snapshot', {'snapshot_id': 'M1', 'market_id': '1'}, '')
            self.assertEqual(contract['contract']['description'], 'Public trading must begin.')
            self.assertEqual(task.budget()['page_fetch_remaining'], 8)

    def test_parser_version_changes_do_not_break_previous_excerpt_coordinates(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory); task.store_page(URL, page())
            task.execute('record_excerpt', {'url': URL, 'document_index': 1, 'start_char': 0, 'end_char': 20, 'need_ids': ['n']}, '')
            revised = page(); revised['documents'][0]['page_content'] = 'Revised parsed layout.'
            task.store_page(URL, revised)
            self.assertEqual(len(task.bundle['page_history'][URL]), 1)
            self.assertEqual(collection_acceptance(task.bundle)['failures'], [])

    def test_operator_refresh_refuses_concurrent_ledger_and_acceptance_is_offline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); location = root / 'snapshots' / 'one'; location.mkdir(parents=True)
            task = self.task(location); task.save()
            (location / '.running.lock').write_text('busy')
            agent = ForecastAgent(root)
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                agent.refresh('snapshots/one', [URL])
            before = (location / 'bundle.json').read_bytes()
            agent.acceptance('snapshots/one')
            self.assertEqual((location / 'bundle.json').read_bytes(), before)

    def test_acceptance_reports_empty_collection_and_failed_searches(self):
        report = collection_acceptance({'searches': [{'status': 'failed', 'results': []}]})
        self.assertEqual(report['status'], 'accepted_with_gaps')
        issues = [w['issue'] for w in report['warnings']]
        self.assertIn('Failed/interrupted searches remain', issues)
        self.assertIn('No source bodies or market responses were captured', issues)

    @patch('ForecastAgent.runtime.retrieval.extract_basic', return_value={'results': [], 'failed_results': []})
    def test_market_failure_does_not_break_unrelated_extract_rescue(self, extract):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            task.bundle['fetch_attempts'] = [{'channel': 'polymarket', 'status': 'failed'}, {'url': URL, 'status': 'failed'}]
            task.execute('extract_failed_pages', {'urls': [URL], 'need_ids': ['n'], 'reason': 'Important saved source'}, '')
            self.assertEqual(extract.call_count, 1)
