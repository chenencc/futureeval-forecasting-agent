"""Real pinned collector seams against synthetic public captures; no providers."""
import base64
import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing.common import now, digest, read, write
from ForecastAgent.market_repricing.runtime import activate
from ForecastAgent.market_repricing.evidence_capture import VERSION, eager, capture_adapter, verify_packet, recover


class CaptureRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        activate(os.environ['MARKET_ENGINE_DIRECTORY'])

    def task(self, root):
        from ForecastAgent.runtime.retrieval import RetrievalTask
        task = RetrievalTask(Path(root), {'id': '1', 'question': 'Will a government change a tax rate?',
            'resolution_criteria': 'Use the dated official announcement.', 'mode': 'live',
            'pipeline': 'collection', 'acquisition_profile': 'collection_v3', 'acquisition_focus': 'raw_recall',
            'market_acquisition_policy': VERSION})
        task.bundle['plan'] = [{'id': 'N1', 'condition': 'Current tax policy', 'priority': 'critical',
                               'expected_source': 'Official government announcement'}]
        return task

    def page(self, url, **kwargs):
        text = 'The ministry published a tax rate notice. The current rate is unchanged. Future decisions remain unknown.\n' * 12
        raw = text.encode()
        return {'url': url, 'final_url': url, 'content': text, 'content_type': 'text/html',
                'sha256': hashlib.sha256(raw).hexdigest(), 'raw_response_base64': base64.b64encode(raw).decode(),
                'retrieved_at_utc': now(), 'links': []}

    def transport(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch('ForecastAgent.runtime.retrieval.fetch_public_page', side_effect=self.page))
        stack.enter_context(patch('ForecastAgent.runtime.retrieval.fetch_structured', return_value=None))
        stack.enter_context(patch('ForecastAgent.runtime.retrieval.cached_page', return_value=None))
        stack.enter_context(patch('ForecastAgent.runtime.retrieval.save_cached_page'))
        return stack

    def test_eager_capture_uses_existing_eight_slots_and_keeps_first_packet(self):
        from ForecastAgent.runtime.retrieval import RetrievalTask
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root)
            hits = [{'url': 'https://example.org/notice/' + str(i), 'title': 'Government tax notice'} for i in range(12)]
            task.bundle['searches'] = [{'query': 'Government tax policy', 'results': hits}]
            with self.transport(), capture_adapter():
                eager(task)
                self.assertEqual(len(task.bundle['fetch_attempts']), 4)
                path = Path(root) / 'market-intake/first-packet.json'
                first = path.read_bytes()
                eager(task)
                eager(task)
                self.assertEqual(len(task.bundle['fetch_attempts']), 8)
                self.assertEqual(path.read_bytes(), first)
                self.assertEqual(len(task.bundle['pages']), 8)
                self.assertTrue(verify_packet(path)['raw_bytes_unchanged'])
                reloaded = RetrievalTask(Path(root), task.bundle['request'])
                eager(reloaded)
                self.assertEqual(len(reloaded.bundle['fetch_attempts']), 8)

    def test_search_handler_captures_before_next_model_turn(self):
        from ForecastAgent.runtime.retrieval import RetrievalTask
        native = RetrievalTask._execute
        def result(task, name, args, key):
            if name == 'search_tavily':
                task.bundle['searches'].append({'query': 'tax notice', 'results': [
                    {'url': 'https://example.org/notice', 'title': 'Government tax rate notice'}]})
                return {'results': task.bundle['searches'][-1]['results']}
            return native(task, name, args, key)
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root)
            with self.transport(), patch.object(RetrievalTask, '_execute', result), capture_adapter():
                task._execute('search_tavily', {}, None)
            self.assertIn('https://example.org/notice', task.bundle['pages'])
            self.assertTrue((Path(root) / 'market-intake/first-packet.json').exists())

    def test_failed_native_capture_is_preserved_and_not_repeated(self):
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root)
            task.bundle['searches'] = [{'results': [{'url': 'http://government.ru/docs/1', 'title': 'Tax rate'}]}]
            with self.transport(), patch('ForecastAgent.runtime.retrieval.fetch_public_page', side_effect=RuntimeError('fixture failure')):
                eager(task); eager(task)
            self.assertEqual(len(task.bundle['fetch_attempts']), 1)
            self.assertEqual(task.bundle['fetch_attempts'][0]['status'], 'failed')
            self.assertEqual(task.bundle['market_capture']['eager_events'][0]['outcomes'][0]['status'], 'failed')

    def test_declared_rule_metadata_url_uses_catalog_and_http_guard(self):
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root)
            url = 'http://government.example.org/decree/123'
            task.bundle['request']['external_rule_sources'] = [url]
            with self.transport():
                eager(task)
            self.assertIn(url, task.bundle['pages'])
            self.assertEqual(len(task.bundle['fetch_attempts']), 1)

    def test_legacy_alias_allowed_only_when_reading_capability_is_offered(self):
        from ForecastAgent.runtime import retrieval
        from ForecastAgent.tools.capabilities import get
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root)
            task.bundle['searches'] = [{'results': [{'url': 'https://example.org/a'}]}]
            tools = [get('read_sources').definition]
            with capture_adapter():
                retrieval.validate(task, 'fetch_page', {'url': 'https://example.org/a'}, tools=tools)
                with self.assertRaises(ValueError): retrieval.validate(task, 'fetch_page', {'url': 'https://unknown.org/a'}, tools=tools)
                with self.assertRaises(ValueError): retrieval.validate(task, 'fetch_page', [], tools=tools)
                with self.assertRaises(ValueError): retrieval.validate(task, 'fetch_page', {'url': 'https://example.org/a'}, tools=[get('list_sources').definition])

    def test_local_limit_normalization_preserves_declared_range(self):
        from ForecastAgent.runtime import retrieval
        from ForecastAgent.tools.capabilities import get
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root); args = {'limit': 100}
            tools = [get('inspect_research_state').definition]
            with capture_adapter():
                retrieval.validate(task, 'inspect_research_state', args, tools=tools)
            self.assertEqual(args['limit'], 4)
            event = task.bundle['market_capture']['parameter_normalizations'][-1]
            self.assertEqual(event['requested'], 100)
            self.assertEqual(event['network_calls'], 0)

    def test_short_official_data_retained_while_access_shell_and_caption_are_leads(self):
        from ForecastAgent.market_repricing.materials import quality
        blocked = {'content': 'The site is temporarily\ninaccessible from\nyour location'}
        self.assertFalse(quality(blocked, 'https://fs.knesset.gov.il/file.pdf')['usable_text'])
        caption = quality({'content': 'I am ready to get arrested.'}, 'https://facebook.com/post/1')
        self.assertTrue(caption['usable_text'])
        self.assertFalse(caption['qualifies_full_body'])
        self.assertTrue(quality({'content': '2026-10-13: policy rate unchanged at 5.1%.'}, 'https://example.gov/notice')['usable_text'])
        self.assertTrue(quality({'content': '{"rate": 5.1}', 'rows': [{'rate': 5.1}], 'content_type': 'application/json'}, 'https://example.gov/api')['usable_text'])
        article = {'content': 'The report examines why the site was inaccessible from some locations during the outage.\n' * 30}
        self.assertTrue(quality(article, 'https://news.org/story')['usable_text'])

    def test_empty_packet_replay_and_supplement_does_not_request_models(self):
        from ForecastAgent.market_repricing.worker import repair_materials
        bundle = {'request': {'id': '1', 'question': 'Will the tax rate change?', 'resolution_criteria': 'Official tax notice.',
                             'as_of_utc': now()}, 'pages': {}, 'searches': [], 'gaps': []}
        with tempfile.TemporaryDirectory() as root:
            task = {'acquisition_policy': VERSION}
            child = repair_materials(task, Path(root), bundle)
            self.assertEqual(task['analysis_stage'], 'analysis-evidence-v2')
            self.assertFalse(child['pages'])
            self.assertTrue((Path(root) / 'evidence-capture-v2/packets/first-packet.json').exists())
            before = digest(child)
            again = repair_materials(task, Path(root), bundle)
            self.assertEqual(digest(again), before)


if __name__ == '__main__':
    unittest.main()
