import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.agent import ForecastAgent
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.tools.channels import channel_catalog, tool_result
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.readers import saved

URL = 'https://example.org/source'
REQUEST = {'question': 'Will Agency publish?', 'resolution_criteria': 'See https://example.org/source', 'mode': 'live'}
PLAN = {'needs': [{'id': 'n', 'condition': 'Publication material', 'priority': 'critical',
                  'expected_source': 'Agency', 'query': 'Agency publication'}]}


def page():
    text = 'First page: α and a publication. ' * 10
    raw = text.encode()
    return {'url': URL, 'content': text, 'retrieved_at_utc': '2026-09-30T00:00:00Z',
            'sha256': hashlib.sha256(raw).hexdigest(), 'raw_response_base64': base64.b64encode(raw).decode(),
            'temporal_status': 'live_capture', 'content_truncated': False,
            'documents': [{'page_content': text, 'metadata': {'format': 'pdf', 'page': 1}},
                          {'page_content': 'Second page publication.', 'metadata': {'format': 'pdf', 'page': 2}}]}


def call(name, args, ident):
    return {'tool_calls': [{'id': ident, 'function': {'name': name, 'arguments': json.dumps(args)}, 'type': 'function'}]}


class CollectionTests(TestCase):
    def task(self, directory):
        task = RetrievalTask(Path(directory), REQUEST)
        task.execute('plan_evidence', PLAN, '')
        task.bundle['pages'][URL] = page()
        return task

    def test_raw_capture_finishes_without_excerpts_evidence_or_audit(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            result = task.execute('finish_collection', {'gaps': ['Other sources unread']}, '')
            self.assertEqual(result['status'], 'collected')
            self.assertFalse(result['truth_verified'])
            package = json.loads((Path(directory) / 'intelligence.json').read_text(encoding='utf-8'))
            self.assertEqual(package['pages'][URL]['raw_response_base64'], page()['raw_response_base64'])
            self.assertEqual(package['excerpts'], [])
            self.assertEqual(package['resources']['tavily_basic_attempts'], 0)
            self.assertNotIn('probability', package)

    def test_legacy_ledger_retains_budget_and_pipeline(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            task.bundle.pop('pipeline')
            task.bundle['searches'] = [{'status': 'failed', 'results': []}] * 3
            task.save()
            restored = RetrievalTask(Path(directory), REQUEST)
            self.assertEqual(restored.bundle['pipeline'], 'legacy')
            self.assertEqual(restored.budget()['tavily_basic_remaining'], 0)
            with self.assertRaises(ValueError):
                RetrievalTask(Path(directory), {**REQUEST, 'pipeline': 'collection'})

    def test_catalog_is_frozen_and_analysis_tools_are_blocked(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            task.save()
            with patch('ForecastAgent.runtime.retrieval.channel_catalog', return_value={'version': 999}):
                restored = RetrievalTask(Path(directory), REQUEST)
            self.assertEqual(restored.execute('list_channels', {}, '')['version'], 2)
            for name in ['audit_evidence', 'record_evidence', 'finish_retrieval']:
                with self.assertRaisesRegex(ValueError, 'unavailable'):
                    restored.execute(name, {}, '')
            self.assertNotIn('audit_evidence', [t['function']['name'] for t in COLLECTION_TOOLS])
            catalog = channel_catalog()
            self.assertEqual(next(c for c in catalog['channels'] if c['id'] == 'polymarket_gamma')['availability'], 'implemented')

    def test_local_read_search_excerpt_preserve_unicode_positions_and_budgets(self):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            before = task.budget()
            with patch('ForecastAgent.runtime.retrieval.fetch_public_page', side_effect=AssertionError('Network forbidden')):
                hits = task.execute('search_saved_text', {'query': 'α', 'limit': 1}, '')
                hit = hits['matches'][0]
                args = {'url': URL, 'document_index': hit['document_index'], 'start_char': hit['match_start'],
                        'end_char': hit['match_end'], 'need_ids': ['n']}
                excerpt = task.execute('record_excerpt', args, '')['excerpt']
                self.assertEqual(excerpt['text'], 'α')
                self.assertEqual(excerpt['location']['page'], 1)
                self.assertFalse(excerpt['truth_verified'])
                self.assertTrue(task.execute('record_excerpt', args, '')['cached'])
                self.assertEqual(len(task.bundle['excerpts']), 1)
                view = task.execute('read_document', {'url': URL, 'document_index': 2, 'max_chars': 6}, '')
                self.assertEqual(view['content'], 'Second')
                self.assertEqual(view['next_start'], 6)
            self.assertEqual(task.budget(), before)

    def test_local_pagination_truncation_rows_and_invalid_coordinates(self):
        pages = {URL: page()}
        pages[URL]['documents_truncated'] = True
        inventory = saved.list_documents(pages, {'limit': 1})
        self.assertEqual(inventory['next_offset'], 1)
        self.assertTrue(inventory['documents'][0]['source_truncated'])
        self.assertEqual(saved.list_documents(pages, {'offset': 1})['documents'][0]['metadata']['page'], 2)
        first = saved.search_saved_text(pages, {'query': 'publication', 'limit': 1})
        second = saved.search_saved_text(pages, {'query': 'publication', 'limit': 1, 'offset': first['next_offset']})
        self.assertGreater(second['matches'][0]['match_start'], first['matches'][0]['match_start'])
        self.assertEqual(saved.search_saved_text(pages, {'query': 'PUBLICATION'})['total_matches'], 0)
        pages[URL]['documents'] = [{'page_content': '{"value":5}', 'metadata': {'format': 'csv', 'row': 3}}]
        self.assertEqual(saved.read_document(pages, {'url': URL, 'document_index': 1})['location']['row'], 3)
        for args in [{'url': URL, 'document_index': 0}, {'url': URL, 'start_char': True},
                     {'url': 'https://unknown.org/'}, {'url': URL, 'max_chars': 18001}]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                saved.read_document(pages, args)

    def test_export_existing_ledger_is_offline_and_does_not_modify_it(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); location = root / 'snapshots' / 'one'
            location.mkdir(parents=True)
            task = self.task(location); task.save()
            before = (location / 'bundle.json').read_bytes()
            with patch('ForecastAgent.runtime.retrieval.ask_ultra', side_effect=AssertionError('No model')):
                output = ForecastAgent(root).export('snapshots/one')
            self.assertTrue(Path(output['path']).exists())
            self.assertEqual((location / 'bundle.json').read_bytes(), before)
            with self.assertRaises(ValueError):
                ForecastAgent(root).export('../outside')

    def test_result_envelopes_success_partial_and_failure(self):
        budget = {'tavily_basic_remaining': 3}
        self.assertTrue(tool_result('read_document', {'content': 'a'}, budget)['ok'])
        mixed = tool_result('fetch_pages', {'items': [{'ok': True}, {'ok': False}]}, budget)
        self.assertEqual(mixed['status'], 'partial')
        self.assertEqual(tool_result('fetch_pages', {'items': [{'ok': False}]}, budget)['status'], 'failed')
        self.assertEqual(tool_result('fetch_page', {}, budget, error='failed')['error'], 'failed')

    @patch('ForecastAgent.runtime.retrieval.search_batch', side_effect=RuntimeError('Unavailable'))
    def test_collection_failed_search_cap_survives_restart(self, search):
        with TemporaryDirectory() as directory:
            task = self.task(directory)
            args = {'query': 'Agency', 'reason': 'Find sources', 'need_ids': ['n']}
            for _ in range(3):
                with self.assertRaisesRegex(RuntimeError, 'budget consumed'):
                    task.execute('search_tavily', args, 'key')
                task = RetrievalTask(Path(directory), REQUEST)
            with self.assertRaisesRegex(ValueError, 'budget exhausted'):
                task.execute('search_tavily', args, 'key')
            self.assertEqual(search.call_count, 3)

    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_collection_force_close_never_calls_audit(self, ask):
        ask.side_effect = [call('plan_evidence', PLAN, 'p'),
                           *[call('fetch_page', {'url': 'https://unknown.org/' + str(i)}, str(i)) for i in range(3)],
                           call('finish_collection', {'gaps': ['Unavailable sources']}, 'f')]
        with TemporaryDirectory() as directory:
            result = run_retrieval(REQUEST, directory, '', '')
            self.assertEqual(ask.call_args.kwargs['forced_tool'], 'finish_collection')
            self.assertFalse(result['result'].get('incomplete', False))
            self.assertEqual(result['result']['status'], 'leads_only')

    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    @patch('ForecastAgent.runtime.retrieval.fetch_public_page', return_value=page())
    def test_agent_collection_loop_exports_and_resumes_without_new_network(self, fetch, ask):
        ask.side_effect = [call('plan_evidence', PLAN, 'p'), call('fetch_page', {'url': URL}, 'f'),
                           call('read_document', {'url': URL, 'document_index': 2}, 'r'),
                           call('finish_collection', {'gaps': []}, 'end')]
        with TemporaryDirectory() as directory:
            bundle = run_retrieval(REQUEST, directory, '', '')
            self.assertEqual(bundle['result']['status'], 'collected')
            self.assertEqual(bundle['transcript'][2]['result']['data']['location']['page'], 2)
            self.assertEqual(bundle['transcript'][2]['result']['version'], 'tool_result_v1')
            self.assertEqual(fetch.call_count, 1)
            self.assertNotIn('documents', bundle['transcript'][1]['result']['data'])
            calls = ask.call_count
            restored = run_retrieval(REQUEST, directory, '', '')
            self.assertEqual(restored['result'], bundle['result'])
            self.assertEqual(ask.call_count, calls)

    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_interruption_preserves_raw_material_for_export(self, ask):
        ask.side_effect = RuntimeError('provider unavailable')
        with TemporaryDirectory() as directory:
            task = self.task(directory); task.save()
            bundle = run_retrieval(REQUEST, directory, '', '')
            self.assertTrue(bundle['result']['incomplete'])
            package = json.loads((Path(directory) / 'intelligence.json').read_text(encoding='utf-8'))
            self.assertIn(URL, package['pages'])
            self.assertEqual(package['collection_result']['status'], 'partial')
