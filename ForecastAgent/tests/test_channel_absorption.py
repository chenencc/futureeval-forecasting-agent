"""Host gates for channel discovery, native reservations and agent guidance."""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.tests.test_native_capabilities import task
from ForecastAgent.channels import official, selection
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.tools.intelligence_box import core
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.tools import capabilities

INDEX = 'https://www.govinfo.gov/rss/plaw.xml'
ORIGINAL = 'https://www.govinfo.gov/content/pkg/PLAW-119publ21/html/PLAW-119publ21.htm'
RSS = ('<rss><channel><item><title>Public law</title><link>' + ORIGINAL +
       '</link><pubDate>Fri, 09 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>').encode()
HTML = b'<html><body><h1>Public law</h1><p>' + b'Original statute provisions and effective conditions. ' * 20 + b'</p></body></html>'
DISCOVER = {'url': INDEX, 'kind': 'rss', 'need_ids': ['revenue']}


def reply(url, raw=RSS, status=200, content_type='application/xml', **extra):
    return dict(raw=raw, status=status, final_url=url, content_type=content_type,
                response_headers={}, truncated=False, **extra)


class ChannelAbsorptionTests(unittest.TestCase):
    def run_tool(self, t, name, **args):
        return t.execute(name, dict(need_ids=['revenue'], **args), '')

    def test_index_download_restore_and_parent_binding_share_native_budget(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root, graph=True)
            t.bundle['research_acquisition']['pending_map_update'] = False
            with patch.object(core, 'transport', side_effect=lambda u, *a, **kw:
                              reply(u, RSS if u == INDEX else HTML,
                                    content_type='application/xml' if u == INDEX else 'text/html')) as wire:
                c = t.execute('intelligence_discover', DISCOVER, '')
                self.assertNotIn(INDEX, t.bundle['pages'])
                self.assertFalse(t.bundle['research_acquisition']['pending_map_update'])
                lead = t.bundle['source_leads'][ORIGINAL]
                self.assertFalse(lead['full_article_body'])
                self.assertEqual(lead['parent_raw_sha256'], c['raw_sha256'])
                d = self.run_tool(t, 'intelligence_acquire_link', capture_id=c['id'], index=0)
                self.assertEqual(d['status'], 'usable')
                self.assertEqual(d['source_binding']['parent_capture_id'], c['id'])
                self.assertIn('Original statute', t.bundle['pages'][ORIGINAL]['content'])
                self.assertTrue(t.bundle['research_acquisition']['pending_map_update'])
                self.assertEqual(len(t.bundle['fetch_attempts']), 2)
                restored = RetrievalTask(Path(root), t.bundle['request'])
                cached = self.run_tool(restored, 'intelligence_acquire_link', capture_id=c['id'], index=0)
                self.assertTrue(cached['cached'])
                restored.execute('intelligence_discover', DISCOVER, '')
                self.assertEqual(wire.call_count, 2)
                self.assertEqual(len(restored.bundle['fetch_attempts']), 2)
                self.assertEqual(restored.bundle['searches'], [])
                self.assertEqual(restored.bundle['exa_searches'], [])

    def test_parent_cache_metadata_is_not_an_original_identity_change(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with patch.object(core, 'transport', side_effect=lambda u, *a, **kw: reply(u, RSS if u == INDEX else HTML, content_type='application/xml' if u == INDEX else 'text/html')):
                c = t.execute('intelligence_discover', DISCOVER, '')
                t.bundle['channel_tools']['captures'][c['id']]['cache_hit'] = True
                t.bundle['channel_tools']['captures'][c['id']]['budget'] = {'ephemeral': True}
                self.assertEqual(self.run_tool(t, 'intelligence_acquire_link', capture_id=c['id'], index=0)['status'], 'usable')

    def test_parent_record_tampering_is_rejected_before_request(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with patch.object(core, 'transport', side_effect=lambda u, *a, **kw: reply(u)) as wire:
                c = t.execute('intelligence_discover', DISCOVER, '')
                t.bundle['channel_tools']['captures'][c['id']]['records'][0]['url'] = ORIGINAL + '?injected=1'
                with self.assertRaisesRegex(ValueError, 'differs'):
                    self.run_tool(t, 'intelligence_acquire_link', capture_id=c['id'], index=0)
                self.assertEqual(wire.call_count, 1)
                self.assertEqual(len(t.bundle['fetch_attempts']), 1)

    def test_download_projection_interruption_recovers_without_http(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with patch.object(core, 'transport', side_effect=lambda u, *a, **kw: reply(u, RSS if u == INDEX else HTML, content_type='application/xml' if u == INDEX else 'text/html')) as wire:
                c = t.execute('intelligence_discover', DISCOVER, '')
                with patch.object(official, '_capture_page', side_effect=RuntimeError('projection interruption')):
                    with self.assertRaisesRegex(RuntimeError, 'interruption'):
                        self.run_tool(t, 'intelligence_acquire_link', capture_id=c['id'], index=0)
                restored = RetrievalTask(Path(root), t.bundle['request'])
                d = self.run_tool(restored, 'intelligence_acquire_link', capture_id=c['id'], index=0)
                self.assertTrue(d['cached'])
                self.assertIn(ORIGINAL, restored.bundle['pages'])
                self.assertEqual(wire.call_count, 2)
                self.assertTrue(restored.bundle['channel_tools']['operations'][-1]['recovered_without_http'])

    def test_failed_download_is_preserved_and_replayed(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with patch.object(core, 'transport', side_effect=lambda u, *a, **kw: reply(u, RSS if u == INDEX else b'blocked', status=200 if u == INDEX else 403)) as wire:
                c = t.execute('intelligence_discover', DISCOVER, '')
                d = self.run_tool(t, 'intelligence_acquire_link', capture_id=c['id'], index=0)
                self.assertEqual(d['status'], 'failed')
                self.assertNotIn(ORIGINAL, t.bundle['pages'])
                self.assertIn(d['id'], t.bundle['channel_raw_captures'])
                self.assertEqual(self.run_tool(t, 'intelligence_acquire_link', capture_id=c['id'], index=0)['status'], 'failed')
                self.assertEqual(wire.call_count, 2)

    def test_discovery_admission_rejects_invented_urls_and_need_ids(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with patch.object(core, 'transport') as wire:
                for args in ({**DISCOVER, 'url': 'https://www.govinfo.gov/invented.xml'},
                             {**DISCOVER, 'need_ids': ['invented']}):
                    with self.assertRaises(ValueError):
                        t.execute('intelligence_discover', args, '')
                wire.assert_not_called()
                self.assertEqual(t.bundle['fetch_attempts'], [])

    def test_native_fetch_cap_blocks_link_download_without_second_allowance(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            t.fetch_limit = 1
            with patch.object(core, 'transport', side_effect=lambda u, *a, **kw: reply(u)) as wire:
                c = t.execute('intelligence_discover', DISCOVER, '')
                d = self.run_tool(t, 'intelligence_acquire_link', capture_id=c['id'], index=0)
                self.assertEqual(d['status'], 'failed')
                self.assertEqual(wire.call_count, 1)
                self.assertEqual(len(t.bundle['fetch_attempts']), 1)

    def test_gdelt_is_only_leads_and_obeys_persistent_cooldown(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            raw = json.dumps({'articles': [{'url': ORIGINAL, 'title': 'News lead', 'seendate': '20261009T120000Z'}]}).encode()
            with patch.object(core, 'transport', side_effect=lambda u, *a, **kw: reply(u, raw, content_type='application/json')) as wire:
                args = {'source_id': 'gdelt_news', 'parameters': {'query': 'issuer revenue'}}
                c = self.run_tool(t, 'intelligence_fetch', **args)
                self.assertEqual(c['status'], 'usable')
                self.assertNotIn(c['request_url'], t.bundle['pages'])
                self.assertFalse(t.bundle['source_leads'][ORIGINAL]['full_article_body'])
                self.assertEqual(selection.availability(t, 'gdelt_news')['status'], 'cooldown')
                self.assertTrue(self.run_tool(t, 'intelligence_fetch', **args)['cached'])
                with self.assertRaisesRegex(ValueError, 'cooldown'):
                    self.run_tool(t, 'intelligence_fetch', source_id='gdelt_news', parameters={'query': 'different query'})
                self.assertEqual(wire.call_count, 1)

    def test_retry_after_is_recorded_and_blocks_new_requests(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with patch.object(core, 'transport', side_effect=lambda u, *a, **kw: reply(u, b'too many requests', 429, retry_after='120')):
                c = self.run_tool(t, 'intelligence_fetch', source_id='gdelt_news', parameters={'query': 'issuer revenue'})
                self.assertEqual(c['status'], 'failed')
                ready = datetime.fromisoformat(selection.availability(t, 'gdelt_news')['next_request_at_utc'])
                self.assertGreater((ready - datetime.now(timezone.utc)).total_seconds(), 110)
                self.assertEqual(len(t.bundle['fetch_attempts']), 1)

    def test_govinfo_feed_is_index_but_package_text_is_readable(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with patch.object(core, 'transport', side_effect=lambda u, *a, **kw: reply(u, RSS if 'rss/' in u else HTML, content_type='application/xml' if 'rss/' in u else 'text/html')) as wire:
                c = self.run_tool(t, 'intelligence_fetch', source_id='govinfo_feed', parameters={'collection': 'PLAW'})
                self.assertEqual(c['status'], 'usable')
                self.assertNotIn(INDEX, t.bundle['pages'])
                d = self.run_tool(t, 'intelligence_fetch', source_id='govinfo_text', parameters={'package': 'PLAW-119publ21'})
                self.assertEqual(d['source_binding']['package_id'], 'PLAW-119publ21')
                self.assertIn(ORIGINAL, t.bundle['pages'])
                self.assertEqual(wire.call_count, 2)

    def test_bls_and_dbnomics_native_identity_and_missing_values_survive(self):
        cases = [
            ('bls_series', {'series': 'CUUR0000SA0'}, {'status': 'REQUEST_SUCCEEDED', 'Results': {'series': [{'seriesID': 'CUUR0000SA0', 'data': [{'year': '2026', 'period': 'M13', 'value': '125', 'footnotes': [{'text': 'Annual'}]}]}]}}),
            ('dbnomics_series', {'provider': 'INSEE', 'dataset': 'CPI', 'series': 'INDEX'}, {'series': {'docs': [{'provider_code': 'INSEE', 'dataset_code': 'CPI', 'series_code': 'INDEX', 'period': ['2026-08', '2026-09'], 'value': [125, None], 'units': 'index'}]}})]
        for source, params, payload in cases:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as root:
                t = task(root)
                with patch.object(core, 'transport', side_effect=lambda u, *a, **kw: reply(u, json.dumps(payload).encode(), content_type='application/json')):
                    c = self.run_tool(t, 'intelligence_fetch', source_id=source, parameters=params)
                self.assertEqual(c['status'], 'usable')
                self.assertEqual(c['parameters'], params)
                p = t.bundle['pages'][c['request_url']]
                self.assertEqual(p['rows'], c['records'])
                if source == 'bls_series':
                    self.assertEqual(p['rows'][0]['period'], 'M13')
                    self.assertEqual(p['rows'][0]['footnotes'][0]['text'], 'Annual')
                else:
                    self.assertIsNone(p['rows'][1]['value'])

    def test_catalog_and_menu_offer_only_usable_prerequisites(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            t = task(root)
            c = t.execute('intelligence_catalog', {}, '')
            self.assertEqual(len(c['sources']), 23)
            self.assertTrue(c['native_usage']['discovery_requires_original_download'])
            configured = capabilities.configure(t, COLLECTION_TOOLS)
            tools = selection.filter_tools(t, configured)
            by_name = {tool['function']['name']: tool for tool in tools}
            self.assertNotIn('intelligence_acquire_link', by_name)
            source_ids = by_name['intelligence_fetch']['function']['parameters']['properties']['source_id']['enum']
            self.assertNotIn('congress_bill', source_ids)
            self.assertNotIn('sec_concept', source_ids)
            self.assertIn('bls_series', source_ids)
            t.fetch_limit = 0
            self.assertFalse(any(x['function']['name'] in {'intelligence_fetch', 'intelligence_read', 'intelligence_discover'} for x in selection.filter_tools(t, configured)))

    def test_historical_mode_prevents_current_index_capture(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root, mode='historical_strict')
            with patch.object(core, 'transport') as wire:
                with self.assertRaisesRegex(ValueError, 'live mode'):
                    t.execute('intelligence_discover', DISCOVER, '')
                wire.assert_not_called()

    def test_unparsed_office_download_retains_raw_and_never_creates_a_body(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            document = ORIGINAL + '.docx'
            rss = RSS.replace(ORIGINAL.encode(), document.encode())
            with patch.object(core, 'transport', side_effect=lambda u, *a, **kw:
                    reply(u, rss if u == INDEX else b'PK\x03\x04office-original',
                          content_type='application/xml' if u == INDEX else
                          'application/vnd.openxmlformats-officedocument.wordprocessingml.document')):
                c = t.execute('intelligence_discover', DISCOVER, '')
                d = self.run_tool(t, 'intelligence_acquire_link', capture_id=c['id'], index=0)
                self.assertEqual(d['status'], 'captured_unparsed')
                self.assertFalse(d['quality']['readable_document'])
                self.assertIn(d['id'], t.bundle['channel_raw_captures'])
                self.assertNotIn(document, t.bundle['pages'])

    def test_transport_failure_also_sets_gdelt_cooldown(self):
        with tempfile.TemporaryDirectory() as root:
            t = task(root)
            with patch.object(core, 'transport', side_effect=TimeoutError('timeout')) as wire:
                c = self.run_tool(t, 'intelligence_fetch', source_id='gdelt_news', parameters={'query': 'issuer revenue'})
                self.assertEqual(c['status'], 'failed')
                self.assertEqual(selection.availability(t, 'gdelt_news')['status'], 'cooldown')
                with self.assertRaisesRegex(ValueError, 'cooldown'):
                    self.run_tool(t, 'intelligence_fetch', source_id='gdelt_news', parameters={'query': 'new query'})
                self.assertEqual(wire.call_count, 1)


if __name__ == '__main__':
    unittest.main()
