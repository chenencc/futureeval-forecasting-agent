"""Regression checks for quote fidelity, bounded navigation and recovery guidance."""
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.evidence.acceptance import collection_acceptance
from ForecastAgent.readers.loader import load_response
from ForecastAgent.tests.test_collection import REQUEST, PLAN, URL, page, call


class AcquisitionGuidanceTests(TestCase):
    def task(self, directory):
        task = RetrievalTask(Path(directory), REQUEST)
        task.execute('plan_evidence', PLAN, '')
        task.bundle['pages'][URL] = page()
        return task
    def test_exact_quote_rejects_ambiguity_and_preserves_pdf_page_coordinates(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            with self.assertRaisesRegex(ValueError, 'occurs'):
                task.execute('record_quote', {'url': URL, 'quote': 'α', 'need_ids': ['n']}, '')
            result = task.execute('record_quote', {'url': URL, 'document_index': 2,
                'quote': 'Second page publication.', 'need_ids': ['n']}, '')
            self.assertEqual(result['excerpt']['location']['page'], 2)
            self.assertEqual(result['excerpt']['start_char'], 0)
            self.assertFalse(collection_acceptance(task.bundle)['failures'])
            self.assertEqual(task.budget()['tavily_basic_remaining'], 3)
            with self.assertRaisesRegex(ValueError, 'absent'):
                task.execute('record_quote', {'url': URL, 'quote': 'Invented passage', 'need_ids': ['n']}, '')

    def test_literal_and_lexical_results_supply_reusable_offsets(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            for tool, query, field in [('search_saved_text', 'α', 'matches'), ('find_passages', 'SECOND PUBLICATION', 'passages')]:
                hits = task.execute(tool, {'url': URL, 'query': query}, '')[field]
                self.assertTrue(hits)
                task.execute('record_excerpt', {**hits[0]['excerpt_args'], 'need_ids': ['n']}, '')
            self.assertFalse(collection_acceptance(task.bundle)['failures'])

    def test_captured_links_are_not_required_reading_until_selected(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            extra = 'https://example.org/navigation'
            task.bundle['source_leads'][extra] = {'url': extra, 'origin': 'page_link'}
            self.assertEqual(collection_acceptance(task.bundle)['unread_source_count'], 0)
            task.execute('select_sources', {'urls': [extra], 'need_ids': ['n'], 'reason': 'Relevant linked document'}, '')
            self.assertEqual(collection_acceptance(task.bundle)['unread_urls'], [extra])
            with self.assertRaises(ValueError):
                task.execute('select_sources', {'urls': ['https://invented.org'], 'need_ids': ['n'], 'reason': 'Guess'}, '')
            rows = task.execute('list_sources', {'offset': 1, 'limit': 1}, '')
            self.assertEqual(len(rows['source_catalog']), 1)
            self.assertIsNone(rows['next_offset'])

    def test_checkpoint_persists_explicit_channel_deferrals(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            task.bundle['fetch_attempts'].append({'url': URL + '/failed', 'status': 'failed'})
            task.bundle['source_leads'][URL + '/failed'] = {'url': URL + '/failed'}
            result = task.execute('collection_checkpoint', {}, '')
            self.assertIn('extract_failed_pages', [s['tool'] for s in result['suggested_next_steps']])
            self.assertIn('collect_polymarket', [s['tool'] for s in result['suggested_next_steps']])
            task.execute('record_channel_decision', {'channel': 'polymarket_gamma', 'decision': 'deferred', 'reason': 'Reserve HTTP for primary document'}, '')
            restored = type(task)(Path(directory), REQUEST)
            self.assertNotIn('collect_polymarket', [s['tool'] for s in restored.execute('collection_checkpoint', {}, '')['suggested_next_steps']])
            self.assertEqual(restored.budget()['tavily_basic_remaining'], 3)

    @patch('ForecastAgent.runtime.retrieval.fetch_public_page', return_value=page())
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_bad_excerpt_recovers_with_quote_without_search_or_audit(self, ask, fetch):
        ask.side_effect = [call('plan_evidence', PLAN, 'p'), call('fetch_page', {'url': URL}, 'f'),
            call('record_excerpt', {'url': URL, 'start_char': 0, 'end_char': 9000, 'need_ids': ['n']}, 'bad'),
            call('record_quote', {'url': URL, 'document_index': 2, 'quote': 'Second page publication.', 'need_ids': ['n']}, 'q'),
            call('finish_collection', {'gaps': []}, 'end')]
        with TemporaryDirectory() as directory:
            bundle = run_retrieval(REQUEST, directory, '', '')
            self.assertEqual(len(bundle['excerpts']), 1)
            self.assertEqual(bundle['searches'], [])
            error = bundle['transcript'][2]['result']['data']
            self.assertEqual(error['recovery_hint']['tool'], 'search_saved_text')
            self.assertEqual(fetch.call_count, 1)
            self.assertIn('acquisition_checkpoint', bundle['result'])

    def test_feed_reader_keeps_dates_links_and_original_bytes(self):
        raw = b'<rss><channel><item><title>Release</title><link>https://example.org/release</link><pubDate>Wed, 30 Sep 2026 12:00:00 GMT</pubDate><description>Agency data release.</description></item></channel></rss>'
        response = {'url': URL, 'final_url': URL, 'content_type': 'application/rss+xml', 'raw': raw}
        result = load_response(response, retrieved_at='2026-09-30T13:00:00Z')
        self.assertEqual(base64.b64decode(result['raw_response_base64']), raw)
        self.assertEqual(result['documents'][0]['metadata']['format'], 'feed_entry')
        self.assertIn('2026', result['documents'][0]['metadata']['published_at'])
        self.assertEqual(result['links'], ['https://example.org/release'])
        response['raw'] = b'<!DOCTYPE rss [<!ENTITY x "bad">]><rss/>'
        with self.assertRaises(ValueError):
            load_response(response, retrieved_at='now')

    def test_atom_relative_links_and_whitespace_normalization(self):
        raw = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>  Release  </title><updated>2026-09-30</updated><link href="/release"/><summary>A  new\n\nrelease.</summary></entry></feed>'
        result = load_response({'url': URL, 'final_url': URL, 'content_type': 'application/atom+xml', 'raw': raw}, retrieved_at='now')
        self.assertEqual(result['links'], ['https://example.org/release'])
        self.assertIn('A new\nrelease.', result['documents'][0]['page_content'])

    def test_markdown_source_query_is_decoded_without_guessing_url(self):
        from ForecastAgent.providers.financial import source_urls
        self.assertEqual(source_urls(r'[law](https://example.org/law?a=1\&b=2&amp;c=3)'),
                         ['https://example.org/law?a=1&b=2&c=3'])

    def test_pdf_magic_dispatches_mislabeled_body_and_preserves_header(self):
        from ForecastAgent.evidence.document import Document
        raw = b'%PDF-fixture'
        with patch('ForecastAgent.readers.loader.parse_pdf', return_value=[Document('Readable page', {'page': 1, 'format': 'pdf'})]) as parser:
            result = load_response({'url': URL, 'final_url': URL, 'content_type': 'text/plain', 'raw': raw}, retrieved_at='now')
        parser.assert_called_once_with(raw, URL)
        self.assertEqual(result['declared_content_type'], 'text/plain')
        self.assertEqual(result['content_type'], 'application/pdf')
