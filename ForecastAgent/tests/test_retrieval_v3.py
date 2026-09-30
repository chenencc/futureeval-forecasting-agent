import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.retrieval_agent import RetrievalTask, run_retrieval, MAX_TURNS

REQUEST = {'question': 'Will Agency announce a price change?', 'resolution_criteria': 'Announcement per [guide](https://example.org/guide.pdf)', 'mode': 'live', 'pipeline': 'legacy'}
PLAN = {'needs': [{'id': 'n', 'condition': 'Price announcement', 'priority': 'critical', 'expected_source': 'Agency', 'query': 'Agency announcement'}]}
URL = 'https://example.org/guide.pdf'
def call(name, args, ident='x'):
    return {'tool_calls': [{'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}


def evidence(**extra):
    return {'url': URL, 'claim': 'Agency plans an October price change', 'quote': 'Changes from 5 October 2026',
            'need_ids': ['n'], 'stance': 'neutral', 'original_source': 'Agency', 'event_time': 'unknown',
            'quality': {k: 'Direct source' for k in ['authority','directness','relevance','verifiability']}, **extra}


class ImprovedRetrievalTests(TestCase):
    @patch('ForecastAgent.retrieval_agent.fetch_public_page')
    def test_real_question_and_page_links_batch_cache_and_budget(self, fetch):
        fetch.return_value = {'content': 'Actual source text. ' * 10, 'url': URL,
                              'retrieved_at_utc': '2026-09-30T00:00:00Z', 'links': ['https://example.org/child']}
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), REQUEST)
            task.execute('plan_evidence', PLAN, '')
            result = task.execute('fetch_pages', {'urls': [URL, 'https://example.org/child', 'https://example.org/guessed']}, '')
            self.assertEqual([item['ok'] for item in result['items']], [True,True,False])
            self.assertEqual(len(task.bundle['fetch_attempts']), 2)
            self.assertEqual(len(task.bundle['searches']), 0)
            self.assertEqual(fetch.call_count, 2)
            task.execute('fetch_page', {'url': URL}, '')
            self.assertEqual(fetch.call_count, 2)
            task.save()
            restored = RetrievalTask(Path(directory), REQUEST)
            self.assertIn('https://example.org/child', restored.catalog())

    def test_partial_batch_exact_quote_and_absence_scope(self):
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), REQUEST); task.execute('plan_evidence', PLAN, '')
            task.bundle['pages'][URL] = {'content': 'Changes from 5 October 2026', 'retrieved_at_utc': '2026-09-30T00:00:00Z', 'temporal_status': 'live_capture'}
            result = task.execute('record_evidence_batch', {'items': [evidence(), evidence(claim='No announcement occurred in August'), evidence(quote='A fabricated quote never appeared')]}, '')
            self.assertEqual([i['ok'] for i in result['items']], [True,False,False])
            self.assertEqual(len(task.bundle['evidence']), 1)
            self.assertIn('exhaustive', result['items'][1]['error'])

    def test_wrong_entity_audit_removes_coverage_and_summary_assertions(self):
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), REQUEST); task.execute('plan_evidence', PLAN, '')
            task.bundle['pages'][URL] = {'content': 'Changes from 5 October 2026', 'retrieved_at_utc': '2026-09-30T00:00:00Z', 'temporal_status': 'live_capture'}
            task.execute('record_evidence', evidence(), '')
            task.execute('audit_evidence', {'reviews': [{'evidence_id':'E1','entity_matches':False,'quote_supports_claim':True,'time_valid':True,'reason':'Same-name fund, wrong subject'}]}, '')
            self.assertEqual(task.coverage()[0]['evidence_ids'], [])
            result = task.execute('finish_retrieval', {'status':'partial','gaps':['Wrong entity'],'conflicts':[],'summary':'Confident invented outcome'}, '')
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('Confident invented outcome', result['summary'])
            self.assertEqual(result['model_summary'], 'Confident invented outcome')

    def test_observation_quote_cannot_smuggle_future_dates(self):
        with TemporaryDirectory() as directory:
            task = RetrievalTask(Path(directory), {**REQUEST,'mode':'historical_exploratory','as_of_utc':'2026-08-20T00:00:00Z'})
            task.execute('plan_evidence', PLAN, '')
            task.bundle['pages'][URL] = {'content': 'Aug 31, 2026 close yield 5.25', 'capture_method':'structured_data'}
            with self.assertRaisesRegex(ValueError, 'future data'):
                task.execute('record_evidence', evidence(quote='Aug 31, 2026 close yield 5.25',claim='Yield value',event_time='2026-08-19',time_role='observation'), '')

    @patch('ForecastAgent.retrieval_agent.ask_ultra')
    @patch('ForecastAgent.retrieval_agent.fetch_public_page')
    def test_three_invalid_requests_force_finish_without_network(self, fetch, ask):
        ask.side_effect = [call('plan_evidence', PLAN, 'p'), *[call('fetch_page', {'url': f'https://example.org/invented{i}'}, str(i)) for i in range(3)],
                           call('finish_retrieval', {'status':'failed','gaps':['No valid sources read'],'conflicts':[],'summary':'Insufficient'}, 'f')]
        with TemporaryDirectory() as directory:
            bundle = run_retrieval(REQUEST, directory, '', '')
            self.assertTrue(bundle['control']['forced_close'])
            self.assertEqual(ask.call_args.kwargs['forced_tool'], 'finish_retrieval')
            self.assertFalse(bundle['result'].get('incomplete', False))
            fetch.assert_not_called()

    @patch('ForecastAgent.retrieval_agent.ask_ultra')
    def test_last_turn_is_reserved_for_explicit_finish(self, ask):
        count = 0
        def respond(messages, key, **kwargs):
            nonlocal count
            count += 1
            if kwargs.get('forced_tool') == 'plan_evidence': return call('plan_evidence', PLAN, str(count))
            if kwargs.get('forced_tool') == 'finish_retrieval': return call('finish_retrieval', {'status':'failed','gaps':['No evidence'],'conflicts':[],'summary':'Insufficient'}, str(count))
            return call('list_sources', {}, str(count))
        ask.side_effect = respond
        with TemporaryDirectory() as directory:
            result = run_retrieval(REQUEST, directory, '', '')
            self.assertLessEqual(count, MAX_TURNS)
            self.assertFalse(result['result'].get('incomplete', False))
            self.assertEqual(ask.call_args.kwargs['forced_tool'], 'finish_retrieval')
