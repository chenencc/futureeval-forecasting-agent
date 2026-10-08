"""Meaningful source-role, immutable-byte and data-response accounting checks."""
import asyncio
import base64
import hashlib
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from ForecastAgent.readers.loader import load_response
from ForecastAgent.readers.material_structure import discover_resources, source_sections, structured_rows
from ForecastAgent.readers.network_data import DataResponseObserver


def page(body, kind='text/html', url='https://example.org/report', status=200):
    raw = body.encode()
    value = load_response({'url': url, 'final_url': url, 'raw': raw, 'content_type': kind,
        'charset': 'utf-8', 'response_headers': {}}, retrieved_at='2026-10-08T00:00:00Z', preserve_raw_on_failure=True)
    value['http_status'] = status
    return value


class StructureTests(unittest.TestCase):
    def test_observed_cross_origin_iframe_and_blocked_data_are_not_invented(self):
        parent = page('<main><h1>Report</h1><iframe src="https://data.example.org/dashboard"></iframe>'
            '<iframe src="http://127.0.0.1/private"></iframe><iframe src="javascript:alert(1)"></iframe></main>')
        parent['browser_audit'] = {'requests': [{'url': 'https://api.example.org/rows?station=17',
            'method': 'GET', 'resource_type': 'xhr', 'allowed': False, 'reason': 'request_budget'}]}
        resources = discover_resources(parent)['resources']
        self.assertEqual(len(resources), 2)
        self.assertFalse(resources[0]['same_origin'])
        self.assertEqual(resources[0]['parent_source_sha256'], parent['sha256'])
        self.assertFalse(resources[1]['observations'][0]['dispatch_observed'])
        self.assertFalse(resources[0]['automatic_fetch'])

    def test_footer_year_does_not_become_article_period(self):
        parent = page('<nav><p>Procurement plan 2026</p></nav><article><h1>Law adopted</h1>'
            '<time datetime="2025-01-15">January 15, 2025</time><p>General mobilization extension.</p>'
            '</article><footer><p>Copyright 1994-2026</p></footer>')
        result = source_sections(parent)
        self.assertNotIn('2026', result['reading_projection'])
        self.assertIn('2025', result['reading_projection'])
        self.assertTrue(any(s['role'] == 'footer' for s in result['sections']))
        self.assertEqual(parent['sha256'], hashlib.sha256(base64.b64decode(parent['raw_response_base64'])).hexdigest())

    def test_layout_table_does_not_mix_article_and_footer(self):
        parent = page('<table><tr><td><article><h1>Actual bill</h1><p>Adopted in 2025.</p>'
            '<table><tr><th>Date</th><th>Stage</th></tr><tr><td>2025</td><td>Adopted</td></tr></table>'
            '</article><footer><p>Copyright 2026</p></footer></td></tr></table>')
        result = source_sections(parent)
        self.assertNotIn('2026', result['reading_projection'])
        self.assertIn('Actual bill', result['reading_projection'])
        self.assertIn('Adopted', result['reading_projection'])

    def test_tabpanel_data_is_not_rejected_as_navigation(self):
        parent = page('<main><div id="nav-tab1" role="tabpanel"><table><tr><td>2026-07-14</td>'
            '<td>Adopted</td></tr></table></div></main>')
        result = source_sections(parent)
        self.assertIn('Adopted', result['reading_projection'])
        self.assertEqual(result['sections'][0]['role'], 'content_panel')

    def test_structured_rows_bind_requested_and_reported_metadata_separately(self):
        body = json.dumps({'metadata': {'id': '17', 'name': 'Station'},
                           'data': [{'t': '2026-10-08 00:00', 'v': '6.12'}, {'t': '2026-10-08 00:06', 'v': ''}]})
        result = structured_rows(page(body, 'application/json', 'https://example.org/data?station=17&datum=MLLW&units=english'))
        self.assertEqual(result['row_count'], 2)
        self.assertEqual(result['request_parameters']['datum'], ['MLLW'])
        self.assertEqual(result['reported_metadata']['$.metadata']['id'], '17')
        self.assertNotIn('datum', result['reported_metadata']['$.metadata'])
        table = next(t for t in result['tables'] if t['path'] == '$.data')
        self.assertEqual(table['columns'], ['t', 'v'])
        self.assertEqual(table['rows'][1]['cells'][1], '')

    def test_errors_and_row_clipping_are_explicit(self):
        error = structured_rows(page('{"error":{"message":"No data for date"}}', 'application/json'))
        self.assertEqual(error['state'], 'remote_error')
        denied = structured_rows(page('{"data":[{"v":6}]}', 'application/json', status=403))
        self.assertEqual(denied['state'], 'http_error')
        result = structured_rows(page('t,v,v\n2026,1,2\n2027,3,4\n', 'text/csv'), max_rows=1)
        self.assertTrue(result['rows_truncated'])
        self.assertTrue(result['duplicate_header_names'])
        self.assertEqual(result['tables'][0]['rows'][0]['cells'], ['2026', '1', '2'])

    def test_changed_bytes_refuse_projections(self):
        parent = page('<main>Original source</main>')
        parent['raw_response_base64'] = base64.b64encode(b'changed').decode()
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            discover_resources(parent)


class ResponseTests(unittest.IsolatedAsyncioTestCase):
    def response(self, body, status=200):
        return SimpleNamespace(request=SimpleNamespace(method='GET'), headers={'content-type': 'application/json'},
            url='https://example.org/data', status=status, body=AsyncMock(return_value=body))

    def observer(self, maximum=3, byte_limit=2000000):
        from ForecastAgent.readers.crawl4ai import RequestGuard
        guard = RequestGuard(25, time.monotonic()+10, lambda url: True, validated_url='https://example.org')
        return DataResponseObserver('https://example.org/report', guard, max_responses=maximum, max_bytes=byte_limit)

    async def test_received_data_is_archived_without_another_request(self):
        observer = self.observer(maximum=1)
        response = self.response(b'{"data":[{"t":"2026-10-08","v":"6.12"}]}')
        observer.observe(response)
        observer.observe(response)
        await observer.finish()
        saved = observer.export()
        self.assertEqual(saved['network_calls_added'], 0)
        self.assertEqual(saved['overflow_responses'], 1)
        self.assertEqual(saved['records'][0]['snapshot']['structured_data']['row_count'], 1)
        self.assertEqual(response.body.await_count, 1)

    async def test_remote_error_and_large_response_never_become_rows(self):
        observer = self.observer(byte_limit=100)
        observer.observe(self.response(b'{"error":{"message":"No observations"}}'))
        observer.observe(self.response(b'x'*101))
        await observer.finish()
        saved = observer.export()
        self.assertFalse(saved['records'][0]['snapshot']['capture_status']['usable_text'])
        self.assertEqual(saved['records'][1]['status'], 'failed')
        self.assertLessEqual(saved['archived_decoded_bytes'], 100)


if __name__ == '__main__':
    unittest.main()
