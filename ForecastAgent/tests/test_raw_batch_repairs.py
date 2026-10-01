"""Offline regression for acquisition defects observed in the first live batch."""
import io
import json
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from ForecastAgent.evidence.acquisition_quality import target_period, discovery_score, page_form
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.providers.tavily_search import canonical_url
from ForecastAgent.provider_health import probe
from ForecastAgent.tests.test_runtime_contracts import prepared, LIVE


class RepairTests(unittest.TestCase):
    def test_short_javascript_required_notice_is_not_readable_body(self):
        text = 'JavaScript must be enabled on your browser, otherwise content or functionality may be limited or unavailable.'
        self.assertEqual(body_diagnostics(text)['state'], 'javascript_shell')
        self.assertFalse(body_diagnostics(text)['usable_text'])
        self.assertTrue(body_diagnostics(text+'\n'+('An actual substantive article paragraph discussing internet availability. '*10))['usable_text'])

    def test_event_period_prioritizes_august_without_rejecting_september(self):
        request = {'question': 'Will the leaders call in August 2026?'}
        self.assertEqual(target_period(request)['end_exclusive'], '2026-09-01')
        self.assertGreater(discovery_score(request, 'https://news.org/2026/08/28/call', 'Leaders call'),
                           discovery_score(request, 'https://news.org/2026/09/08/call', 'Leaders call'))

    def test_boundaries_and_ambiguous_periods(self):
        request = {'question': 'Will a meeting occur before September 1, 2026?',
                   'resolution_criteria': 'After May 1, 2026 and before September 1, 2026.'}
        self.assertEqual(target_period(request)['start'], '2026-05-02')
        self.assertGreater(discovery_score(request, 'https://news.org/2026/05/04/meeting', ''),
                           discovery_score(request, 'https://news.org/2016/04/18/meeting', ''))
        request['resolution_criteria'] += ' Another clause before May 1, 2026.'
        self.assertEqual(target_period(request)['basis'], 'ambiguous')

    def test_long_menu_is_gap_but_real_article_with_menu_is_readable(self):
        menu = '\n'.join('[Menu item](https://example.org/item/'+str(i)+')' for i in range(180))
        shell = menu+'\nSelect Tags\nShowing Result\nLoading...'
        self.assertFalse(body_diagnostics(shell)['usable_text'])
        self.assertEqual(page_form('https://official.org/document?id=1', {'content': shell})['state'], 'possible_index_shell')
        article = shell+'\n'+('The foreign ministers met to discuss their cooperation and signed a joint statement on shared objectives. '*8)
        self.assertTrue(body_diagnostics(article)['usable_text'])

    @patch('ForecastAgent.runtime.retrieval.cached_page', return_value=None)
    @patch('ForecastAgent.runtime.retrieval.fetch_structured', return_value=None)
    @patch('ForecastAgent.runtime.retrieval.fetch_public_page', side_effect=RuntimeError('Offline transport sentinel'))
    def test_comparison_key_does_not_change_fetch_url(self, fetch, structured, cache):
        url = 'https://official.org/document.htm?dtl/34540/Joint+Communique'
        with TemporaryDirectory() as root:
            task = prepared(root, dict(LIVE))
            task.bundle['source_leads'][canonical_url(url)] = {'url': url, 'origin': 'question_background'}
            with self.assertRaises(RuntimeError): task.execute('fetch_page', {'url': canonical_url(url)}, '')
            self.assertEqual(fetch.call_args.args[0], url)
            self.assertEqual(task.bundle['fetch_attempts'][-1]['url'], url)

    def test_health_probe_handles_embedded_503_with_one_attempt(self):
        response = io.BytesIO(json.dumps({'error': {'code': 503, 'message': 'Service temporarily overloaded'}}).encode())
        response.status = 200
        with patch('ForecastAgent.provider_health.urlopen', return_value=response) as opener:
            report = probe('test-key', opener=opener)
        self.assertFalse(report['healthy']); self.assertEqual(report['provider_error']['code'], 503)
        self.assertEqual(opener.call_count, 1)

    @patch('ForecastAgent.runtime.retrieval.extract_basic')
    def test_extract_preserves_transport_url_and_failed_shell_snapshot(self, extract):
        url = 'https://official.org/document.htm?dtl/34540/Joint+Communique'
        shell = '\n'.join('[Menu](https://example.org/'+str(i)+')' for i in range(50))+'\nLoading...'
        extract.return_value = {'results': [{'url': url, 'raw_content': shell}]}
        with TemporaryDirectory() as root:
            task = prepared(root, dict(LIVE))
            task.bundle['source_leads'][canonical_url(url)] = {'url': url, 'origin': 'question_background'}
            task.bundle['fetch_attempts'].append({'url': url, 'status': 'failed'})
            result = task.execute('extract_failed_pages', {'urls': [canonical_url(url)],
                                  'need_ids': ['n'], 'reason': 'Rescue official document'}, '')
            self.assertEqual(extract.call_args.args[0], [url])
            self.assertEqual(result['pages'], [])
            self.assertEqual(task.bundle['failed_captures'][-1]['page']['content'], shell)
            self.assertEqual(len(task.bundle['extract_attempts']), 1)
            self.assertNotIn(canonical_url(url), task.bundle['pages'])

    def test_health_probe_requires_actual_tool_acknowledgement(self):
        response = io.BytesIO(json.dumps({'choices': [{'message': {'tool_calls': [
            {'function': {'name': 'report_health', 'arguments': '{"ready":true}'}}]}}]}).encode())
        response.status = 200
        report = probe('test-key', opener=lambda *args, **kwargs: response)
        self.assertTrue(report['healthy']); self.assertEqual(report['physical_attempts'], 1)
