"""Actual pinned search provider, ledger, model tools and routed transport; no network."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing.runtime import activate
from ForecastAgent.market_repricing.common import policy, read
from ForecastAgent.market_repricing.search_policy import adapter, identity, guide
from ForecastAgent.market_repricing.worker import provider_budget
from ForecastAgent.market_repricing.tests.test_search_credentials import Response, ENV, exhausted


class MixedSearchRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        activate(os.environ['MARKET_ENGINE_DIRECTORY'])

    def task(self, root, mixed=True, mode='live'):
        from ForecastAgent.runtime.retrieval import RetrievalTask
        request = {'id': '1', 'question': 'Will a government change a tax rate?',
            'resolution_criteria': 'Use the dated official announcement.', 'mode': mode,
            'pipeline': 'collection', 'acquisition_profile': 'collection_v3', 'acquisition_focus': 'raw_recall'}
        if mixed: request['market_search_profile'] = identity()
        if mode != 'live':
            request['as_of_utc'] = '2026-01-01T00:00:00Z'
            request.pop('acquisition_focus')
        task = RetrievalTask(Path(root), request)
        task.bundle['plan'] = [{'id': 'N1', 'condition': 'Dated tax policy', 'priority': 'critical',
                               'expected_source': 'Ministry'},
                              {'id': 'N2', 'condition': 'Context', 'priority': 'useful', 'expected_source': 'News'}]
        task.save()
        return task

    def args(self, depth='basic', **extra):
        return {'query': 'government tax rate official notice', 'need_ids': ['N1'],
                'reason': 'Missing the dated official rate notice', 'search_role': 'official_gap',
                'search_depth': depth, 'topic': 'general', 'include_domains': [],
                'include_domains_mode': 'prefer', 'exact_match': False, **extra}

    def send(self, sent, task=None, hits=None, fail=False):
        def transport(request, **kwargs):
            payload = json.loads(request.data); sent.append(payload)
            if task:
                saved = read(task.path)['searches'][-1]
                self.assertEqual(saved['depth'], payload['search_depth'])
                self.assertEqual(saved['status'], 'reserved')
            if fail: raise exhausted(433)
            return Response(json.dumps({'results': hits or [], 'usage': {
                'credits': 2 if payload['search_depth'] == 'advanced' else 1}, 'request_id': 'fixture'}).encode())
        return transport

    def test_two_basic_one_advanced_and_resume_no_fourth_http(self):
        from ForecastAgent.runtime.retrieval import RetrievalTask
        from ForecastAgent.providers import tavily_search
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root); sent = []
            with patch.object(tavily_search, 'urlopen', self.send(sent, task)), adapter():
                for depth in ('basic', 'basic', 'advanced'):
                    task._execute('search_tavily', self.args(depth), 'fixture')
                self.assertEqual(task.budget()['tavily_total_remaining'], 0)
                reloaded = RetrievalTask(Path(root), task.bundle['request'])
                for depth in ('basic', 'advanced'):
                    with self.assertRaises(ValueError): reloaded._execute('search_tavily', self.args(depth), 'fixture')
            self.assertEqual([row['search_depth'] for row in sent], ['basic', 'basic', 'advanced'])
            self.assertEqual(sum(row['credits_estimate'] for row in task.bundle['searches']), 4)

    def test_advanced_tool_visible_after_basic_exhaustion_and_requires_depth(self):
        from ForecastAgent.runtime import retrieval, tool_selection
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root)
            task.bundle['searches'] = [{'depth': 'basic', 'results': [], 'status': 'failed'} for _ in range(2)]
            with adapter():
                offered = tool_selection.active_tools(task, retrieval.COLLECTION_TOOLS)
                entry = next(t for t in offered if t['function']['name'] == 'search_tavily')
                self.assertEqual(entry['function']['parameters']['properties']['search_depth']['enum'], ['advanced'])
                self.assertIn('ONE optional advanced', entry['function']['description'])
                retrieval.validate(task, 'search_tavily', self.args('advanced'), offered)
                self.assertIn('tavily_advanced_remaining', guide(task))
                self.assertEqual(task.budget()['tavily_basic_remaining'], 0)
                task.bundle['searches'].append({'depth': 'advanced', 'results': []})
                self.assertFalse(any(t['function']['name'] == 'search_tavily'
                                     for t in tool_selection.active_tools(task, retrieval.COLLECTION_TOOLS)))

    def test_advanced_failure_and_interruption_consume_single_lifetime_slot(self):
        from ForecastAgent.providers import tavily_search
        from ForecastAgent.runtime.retrieval import RetrievalTask
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root); sent = []
            with patch.object(tavily_search, 'urlopen', self.send(sent, task, fail=True)), adapter():
                with self.assertRaises(RuntimeError): task._execute('search_tavily', self.args('advanced'), 'fixture')
                self.assertEqual(task.bundle['searches'][0]['status'], 'failed')
                task.bundle['searches'][0]['status'] = 'reserved'; task.save()
                reload = RetrievalTask(Path(root), task.bundle['request'])
                with self.assertRaises(ValueError): reload._execute('search_tavily', self.args('advanced'), 'fixture')
            self.assertEqual(len(sent), 1)

    def test_advanced_rejects_noncritical_or_wrong_role_before_reservation(self):
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root)
            with adapter():
                for extra in ({'need_ids': ['N2']}, {'search_role': 'crosscheck'}, {'reason': '  '}):
                    with self.subTest(extra=extra), self.assertRaises(ValueError):
                        task._execute('search_tavily', self.args('advanced', **extra), 'fixture')
            self.assertEqual(task.bundle['searches'], [])

    def test_all_returned_chunks_keep_native_filters_top10_and_raw_receipt(self):
        from ForecastAgent.providers import tavily_search
        content = 'chunk one\n' * 150 + 'chunk two\n' * 150 + 'chunk three important ending\n' * 150
        hits = [{'url': 'https://example.org/'+str(i), 'title': 'Notice', 'content': content} for i in range(12)]
        hits.insert(0, {'url': 'https://metaculus.com/questions/1', 'content': 'excluded'})
        hits.insert(2, dict(hits[1], content='duplicate must not replace accepted first content'))
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root); sent = []
            with patch.object(tavily_search, 'urlopen', self.send(sent, task, hits)), adapter():
                result = task._execute('search_tavily', self.args('advanced'), 'fixture')
            self.assertEqual(len(result['results']), 10)
            self.assertTrue(all(h['content'] == content and not h['snippet_truncated'] for h in result['results']))
            self.assertEqual(len(task.bundle['searches'][0]['raw_response']['raw_results']), 14)
            self.assertEqual(sent[0]['chunks_per_source'], 3)
            self.assertFalse(sent[0]['auto_parameters'])

    def test_native_temporal_quarantine_still_applies_to_full_chunks(self):
        from ForecastAgent.providers import tavily_search
        hits = [{'url': 'https://example.org/late', 'title': 'Notice', 'content': 'future',
                 'published_date': '2026-01-02'}]
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root, mode='historical_exploratory')
            with patch.object(tavily_search, 'urlopen', self.send([], task, hits)), adapter():
                result = task._execute('search_tavily', self.args('advanced'), 'fixture')
            self.assertEqual(result['results'], [])
            self.assertEqual(len(task.bundle['quarantine']), 1)

    def test_quota_failover_counts_two_physical_one_logical_advanced(self):
        import urllib.request
        from ForecastAgent.providers import tavily_search
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, ENV):
            task = self.task(Path(root)/'task'); sent = []; ledger = {'provider_reservations': []}
            def send(request, **kwargs):
                sent.append(request)
                self.assertEqual(read(task.path)['searches'][-1]['depth'], 'advanced')
                if len(sent) == 1: raise exhausted()
                return Response(b'{"results":[],"usage":{"credits":2}}')
            with patch('urllib.request.urlopen', side_effect=send), patch.object(tavily_search, 'urlopen', urllib.request.urlopen):
                with provider_budget(root, ledger, 'task', policy()), adapter():
                    task._execute('search_tavily', self.args('advanced'), ENV['TAVILY_API_KEY'])
            self.assertEqual(len(task.bundle['searches']), 1)
            self.assertEqual(len(ledger['provider_reservations']), 2)
            self.assertTrue(all(r['search_depth'] == 'advanced' for r in ledger['provider_reservations']))
            self.assertEqual(sent[0].data, sent[1].data)
            self.assertEqual(sent[1].get_header('Authorization'), 'Bearer '+ENV['TAVILY_API_KEY2'])

    def test_native_legacy_remains_three_basic_with_original_clipping(self):
        from ForecastAgent.providers import tavily_search
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root, mixed=False); sent = []
            with patch.object(tavily_search, 'urlopen', self.send(sent, hits=[{'url':'https://example.org/a','content':'x'*3000}])), adapter():
                for _ in range(3): result = task._execute('search_tavily', self.args(), 'fixture')
                self.assertEqual(task.budget()['tavily_basic_remaining'], 0)
            self.assertEqual([r['search_depth'] for r in sent], ['basic']*3)
            self.assertTrue(task.bundle['searches'][0]['results'][0]['snippet_truncated'])

    def test_mixed_search_keeps_eager_capture_before_next_model_turn(self):
        from ForecastAgent.providers import tavily_search
        from ForecastAgent.market_repricing.evidence_capture import capture_adapter, VERSION
        from ForecastAgent.market_repricing.tests.verify_capture_runtime import CaptureRuntime
        fixture = CaptureRuntime()
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root)
            task.bundle['request']['market_acquisition_policy'] = VERSION
            hits = [{'url': 'https://example.org/notice', 'title': 'Government tax rate notice',
                     'content': 'Ministry announced the tax rate.'}]
            with fixture.transport(), patch.object(tavily_search, 'urlopen', self.send([], task, hits)), \
                    capture_adapter(), adapter():
                task._execute('search_tavily', self.args('advanced'), 'fixture')
            self.assertIn('https://example.org/notice', task.bundle['pages'])
            self.assertTrue((Path(root)/'market-intake/first-packet.json').exists())
            self.assertEqual(len(task.bundle['searches']), 1)

    def test_internal_missing_depth_defaults_basic_and_never_upgrades(self):
        from ForecastAgent.providers import tavily_search
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root); sent = []; args = self.args(); args.pop('search_depth')
            with patch.object(tavily_search, 'urlopen', self.send(sent, task)), adapter():
                for _ in range(2): task._execute('search_tavily', args, 'fixture')
                with self.assertRaises(ValueError): task._execute('search_tavily', args, 'fixture')
                self.assertEqual(task.budget()['tavily_advanced_remaining'], 1)
            self.assertEqual([r['search_depth'] for r in sent], ['basic', 'basic'])

    def test_changed_profile_is_not_silently_migrated(self):
        with tempfile.TemporaryDirectory() as root:
            task = self.task(root); task.bundle['request']['market_search_profile']['caps']['basic'] = 9
            with adapter(), self.assertRaises(ValueError): task.budget()


if __name__ == '__main__': unittest.main()
