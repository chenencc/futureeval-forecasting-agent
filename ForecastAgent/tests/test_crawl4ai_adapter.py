"""Data preservation, shell rejection, dependency bounds and journal recovery."""
import asyncio
import base64
import copy
import hashlib
from importlib.util import find_spec
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ForecastAgent.readers.loader import load_response
from ForecastAgent.readers.crawl4ai import enrich_html, discover_links, RequestGuard
from ForecastAgent.acquisition.nextgen import collect_sources, supplement_backend, validate_manifest


def snapshot(html, code=200):
    saved = load_response({'url': 'https://example.org/statistics', 'final_url': 'https://example.org/statistics',
        'raw': html.encode(), 'content_type': 'text/html', 'charset': 'utf-8', 'response_headers': {}},
        retrieved_at='2026-10-08T00:00:00Z', preserve_raw_on_failure=True)
    saved['navigation_http_status'] = code
    return saved


@unittest.skipUnless(find_spec('crawl4ai'), 'Install the optional Crawl4AI requirements for parser acceptance')
class SavedHTMLTests(unittest.TestCase):
    def test_full_table_rows_and_input_are_preserved_without_network(self):
        rows = ''.join(f'<tr><td>MONTH-{i}</td><td>{i}.25</td></tr>' for i in range(200))
        parent = snapshot('<html><body><h1>Inflation statistics</h1><table><thead><tr><th>Month</th><th>Value</th></tr></thead><tbody>'+rows+'</tbody></table></body></html>')
        before = copy.deepcopy(parent)
        with patch('requests.head', side_effect=AssertionError('Unexpected network')), patch('requests.get', side_effect=AssertionError('Unexpected network')):
            result = enrich_html(parent, query='inflation statistics')
        self.assertEqual(parent, before)
        self.assertEqual(result['sha256'], parent['sha256'])
        self.assertEqual(result['raw_response_base64'], parent['raw_response_base64'])
        table = result['crawl4ai']['structured_tables'][0]
        self.assertEqual(len(table['rows']), 200)
        self.assertIn('MONTH-199', table['rows'][-1])
        self.assertFalse(table['truncated'])
        self.assertEqual(result['crawl4ai']['model_calls'], 0)
        self.assertEqual(result['crawl4ai']['network_calls'], 0)

    def test_short_official_statement_is_retained(self):
        parent = snapshot('<html><body><article><h1>Official decision</h1><p>The board left the policy rate at 4.5% on October 8, 2026.</p></article></body></html>')
        result = enrich_html(parent, query='policy rate')
        self.assertIn('4.5%', result['crawl4ai']['raw_markdown'])
        self.assertTrue(result['body_diagnostics']['usable_text'])

    def test_http_error_never_becomes_readable(self):
        parent = snapshot('<html><body><article>'+('An explanatory denial page. '*20)+'</article></body></html>', 403)
        result = enrich_html(parent)
        self.assertFalse(result['body_diagnostics']['usable_text'])
        self.assertEqual(result['capture_status']['category'], 'http_error')

    def test_challenge_and_javascript_shell_remain_gaps(self):
        for text in ('Just a moment. Verify you are human.', 'Please enable javascript to view this page.',
                     'Verifying your browser before proceeding... Incident ID: 0000-0000'):
            result = enrich_html(snapshot('<html><body>'+text+'</body></html>'), query='statistics')
            self.assertFalse(result['body_diagnostics']['usable_text'])

    def test_integrity_failure_is_rejected(self):
        parent = snapshot('<html><body>Saved evidence.</body></html>')
        parent['raw_response_base64'] = base64.b64encode(b'<html>changed</html>').decode()
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            enrich_html(parent)

    def test_only_observed_same_host_detail_links_are_ranked(self):
        parent = snapshot('<html><body><article><h1>Inflation statistics</h1><a href="/inflation/october">October inflation bulletin</a><a href="https://other.org/inflation">Other source</a><a href="/login">Inflation account</a></article></body></html>')
        result = enrich_html(parent)
        candidates = discover_links(result, query='October inflation', limit=5)
        self.assertEqual([row['url'] for row in candidates], ['https://example.org/inflation/october'])
        self.assertEqual(candidates[0]['parent_dom_sha256'], parent['sha256'])
        self.assertFalse(candidates[0]['relevance_verified'])

    def test_merged_cells_preserve_the_grid_with_raw_header_warning(self):
        page = snapshot('<html><body><h1>Issuer release</h1><table><thead><tr><th>Period</th><th>Series A</th><th>Series B</th></tr></thead><tbody><tr><td rowspan="2">October</td><td>4.5</td><td>2.0</td></tr><tr><td colspan="2">Unavailable</td></tr></tbody></table></body></html>')
        result = enrich_html(page)
        table = result['crawl4ai']['structured_tables'][0]
        self.assertEqual(table['rows'], [['October', '4.5', '2.0'], ['October', 'Unavailable', 'Unavailable']])
        self.assertTrue(result['crawl4ai']['table_inventory_complete'])
        self.assertEqual(table['source_sha256'], page['sha256'])

    def test_excessive_header_expansion_is_an_explicit_gap(self):
        page = snapshot('<html><body><table><thead><tr><th colspan="999999999">Header</th></tr></thead><tbody><tr><td>Value</td></tr></tbody></table></body></html>')
        with self.assertRaisesRegex(ValueError, 'header span'):
            enrich_html(page)


class RequestGuardTests(unittest.IsolatedAsyncioTestCase):
    def route(self, url='https://example.org', method='GET', kind='document'):
        return SimpleNamespace(request=SimpleNamespace(url=url, method=method, resource_type=kind), abort=AsyncMock(), continue_=AsyncMock())

    async def test_non_read_methods_private_redirects_and_images_are_blocked(self):
        guard = RequestGuard(2, time.monotonic()+10, lambda url: url.startswith('https://example.org'))
        routes = [self.route(method='POST'), self.route(url='http://127.0.0.1/'), self.route(kind='image'), self.route()]
        for route in routes:
            await guard.route(route)
        self.assertEqual(guard.allowed, 1)
        self.assertEqual([r['reason'] for r in guard.rows], ['non_read_method', 'non_public_destination', 'unnecessary_resource', None])

    async def test_concurrent_checks_do_not_overrun_budget(self):
        guard = RequestGuard(2, time.monotonic()+10, lambda url: True)
        await asyncio.gather(*(guard.route(self.route()) for _ in range(10)))
        self.assertEqual(guard.allowed, 2)
        self.assertEqual(sum(row['reason'] == 'request_budget' for row in guard.rows), 8)

    async def test_deadline_blocks_before_dispatch(self):
        guard = RequestGuard(2, time.monotonic()-1, lambda url: True)
        await guard.route(self.route())
        self.assertEqual(guard.allowed, 0)
        self.assertEqual(guard.rows[0]['reason'], 'deadline')

    async def test_prevalidated_origin_avoids_duplicate_dns_without_admitting_credentials(self):
        check = lambda url: (_ for _ in ()).throw(AssertionError('Repeated DNS lookup'))
        guard = RequestGuard(2, time.monotonic()+10, check, validated_url='https://example.org/source')
        await guard.route(self.route(url='https://example.org/source.css'))
        await guard.route(self.route(url='https://secret@example.org/private'))
        self.assertEqual(guard.allowed, 1)
        self.assertEqual(guard.reused_destination_checks, 1)
        self.assertEqual(guard.rows[1]['reason'], 'destination_check_failed')


class LedgerTests(unittest.TestCase):
    def manifest(self):
        return {'schema': 'source_manifest_v1', 'sources': [{'url': 'https://example.org/statistics'}], 'max_pages': 1}

    def test_resume_reuses_capture_and_detects_tampering(self):
        page = snapshot('<html><body>'+('Measured source data. '*20)+'</body></html>')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('ForecastAgent.acquisition.nextgen.render_page', return_value=page) as renderer:
                report = collect_sources(self.manifest(), root)
                collect_sources(self.manifest(), root)
                self.assertEqual(renderer.call_count, 1)
            path = root/report['attempts'][0]['capture_file']
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Saved capture changed'):
                collect_sources(self.manifest(), root)

    def test_failed_attempt_consumes_page_allowance_and_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('ForecastAgent.acquisition.nextgen.render_page', side_effect=RuntimeError('Failed')) as renderer:
                report = collect_sources(self.manifest(), tmp)
                collect_sources(self.manifest(), tmp)
                self.assertEqual(renderer.call_count, 1)
                self.assertEqual(report['unused_page_allowance'], 0)
                self.assertEqual(report['counts']['failed'], 1)
                self.assertEqual(report['provider_calls'], {'models': 0, 'tavily': 0, 'exa': 0})

    def test_changed_input_or_allowance_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('ForecastAgent.acquisition.nextgen.render_page', side_effect=RuntimeError('Failed')):
                collect_sources(self.manifest(), tmp)
            changed = dict(self.manifest(), max_pages=2)
            with self.assertRaisesRegex(ValueError, 'Frozen input'):
                collect_sources(changed, tmp)

    def test_reserved_interruption_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('ForecastAgent.acquisition.nextgen.render_page', side_effect=RuntimeError('Failed')):
                collect_sources(self.manifest(), root)
            path = root/'state.json'
            state = json.loads(path.read_text())
            state['attempts'][0]['status'] = 'reserved'
            path.write_text(json.dumps(state))
            with patch('ForecastAgent.acquisition.nextgen.render_page') as renderer:
                report = collect_sources(self.manifest(), root)
                renderer.assert_not_called()
                self.assertEqual(report['counts']['reserved'], 1)

    def test_observed_details_share_the_same_total_page_allowance(self):
        page = snapshot('<html><body>'+('Published source data. '*20)+'</body></html>')
        page['crawl4ai'] = {'observed_links': [{'url': 'https://example.org/inflation', 'text': 'Inflation', 'role': 'internal'},
                                              {'url': 'https://example.org/inflation/details', 'text': 'Inflation details', 'role': 'internal'}]}
        manifest = dict(self.manifest(), max_pages=2, follow_details=True, query='inflation')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('ForecastAgent.acquisition.nextgen.render_page', return_value=page) as renderer:
                report = collect_sources(manifest, tmp)
                self.assertEqual(renderer.call_count, 2)
                self.assertEqual(len(report['unvisited_frontier']), 1)
                self.assertEqual(report['attempts'][1]['parent_dom_sha256'], page['sha256'])

    def test_script_and_historical_network_options_are_rejected(self):
        for manifest in [dict(self.manifest(), mode='historical_strict'),
                         dict(self.manifest(), sources=[{'url': 'https://example.org', 'js_code': 'alert(1)'}])]:
            with self.assertRaises(ValueError):
                validate_manifest(manifest)

    def test_pipeline_seam_restores_on_exception(self):
        from ForecastAgent.supplement import stage
        original = stage.render_page
        with self.assertRaises(RuntimeError):
            with supplement_backend('inflation'):
                self.assertIsNot(stage.render_page, original)
                raise RuntimeError('test')
        self.assertIs(stage.render_page, original)

    def test_legacy_supplement_cannot_readmit_denied_body(self):
        from ForecastAgent.supplement import stage
        from ForecastAgent.readers.quality import body_diagnostics
        page = snapshot('<html><body>'+('Access refusal explanation. '*20)+'</body></html>', 403)
        page['capture_status'] = {'usable_text': False, 'category': 'http_error'}
        with patch('ForecastAgent.acquisition.nextgen.render_page', return_value=page):
            with supplement_backend():
                result = stage.render_page(page['url'], retrieved_at='2026-10-08T00:00:00Z')
        self.assertFalse(body_diagnostics(result['content'])['usable_text'])
        self.assertEqual(result['excluded_body']['content'], page['content'])
        self.assertEqual(result['raw_response_base64'], page['raw_response_base64'])


if __name__ == '__main__':
    unittest.main()
