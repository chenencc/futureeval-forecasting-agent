"""Release collector source tools: budgets, replay, projections and failure routing."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime import source_reading as tools
from ForecastAgent.tests.test_runtime_contracts import prepared, LIVE
from ForecastAgent.tests.test_material_structure import page
from ForecastAgent.tests.test_collection import URL, call
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.runtime.tool_selection import active_tools

REQUEST={**LIVE,'source_reading_policy':tools.POLICY}


def rendered(body='A current official report with detailed publication information. '*8,partial=False):
    value=page('<main><p>'+body+'</p></main>',url=URL)
    value['capture_status']={'usable_text':True,'render_complete':not partial}
    value['body_diagnostics']={'usable_text':True,'state':'readable'}
    return value


class SourceRuntimeTests(TestCase):
    def failed_primary(self, root, request=REQUEST, url=URL):
        task = self.task(root, request)
        task.bundle['pages'].clear()
        task.bundle['source_leads'][url] = {'url':url,'origin':'question_resolution_criteria'}
        task.bundle['plan'][0]['expected_source'] = 'example'
        task.bundle['fetch_attempts'].append({'url':url,'status':'failed','channel':'http'})
        task.save()
        return task

    def test_free_browser_repair_precedes_paid_rescue_only_when_enabled(self):
        from ForecastAgent.runtime.collection_actions import next_action
        with TemporaryDirectory() as root:
            task=self.failed_primary(root)
            self.assertEqual(next_action(task)['tool'],'render_source')
            task.bundle['fetch_attempts'].append({'url':URL,'status':'reserved','channel':'source_browser'})
            self.assertEqual(next_action(task)['tool'],'extract_failed_pages')
        with TemporaryDirectory() as root:
            task=self.failed_primary(root,LIVE)
            self.assertEqual(next_action(task)['tool'],'extract_failed_pages')

    def test_failed_pdf_keeps_document_rescue_route(self):
        from ForecastAgent.runtime.collection_actions import next_action
        with TemporaryDirectory() as root:
            task=self.failed_primary(root,url='https://example.org/report.pdf')
            self.assertEqual(next_action(task)['tool'],'extract_failed_pages')
            with self.assertRaisesRegex(ValueError,'file type'):
                task.execute('render_source',{**self.args(),'url':'https://example.org/report.pdf'},'')
            self.assertEqual(task.budget()['page_fetch_remaining'],7)

    def test_batch_read_defers_paid_html_rescue_without_refetch(self):
        with TemporaryDirectory() as root, patch('ForecastAgent.runtime.retrieval.extract_basic') as extract:
            task=self.failed_primary(root)
            result=task.execute('read_sources',{'urls':[URL],'queries':[{'query':'Release','need_ids':['n']}]},'')
            self.assertEqual(result['browser_repair_candidates'][0]['url'],URL)
            self.assertIsNone(result['rescue'])
            extract.assert_not_called()
            self.assertEqual(len(task.bundle['fetch_attempts']),1)

    def test_corrupt_saved_body_rejected_before_browser(self):
        with TemporaryDirectory() as root:
            task=self.task(root)
            task.bundle['pages'][URL]['sha256']='bad'
            with self.assertRaises(ValueError): task.execute('render_source',self.args(),'')
            self.assertEqual(task.bundle['fetch_attempts'],[])

    def task(self, root, request=REQUEST):
        task=prepared(root,request)
        task.bundle['pages'][URL]=page('<main><p>Report loading.</p><iframe src="https://example.org/data"></iframe></main>',url=URL)
        task.bundle['source_leads'][URL]={'url':URL,'origin':'question_resolution_criteria'}
        task.save()
        return task

    def args(self): return {'url':URL,'need_ids':['n'],'reason':'Read critical document after its empty HTTP capture'}

    def test_disabled_profile_uses_identical_tools_and_no_browser(self):
        with TemporaryDirectory() as root:
            task=self.task(root,LIVE)
            self.assertEqual(tools.configure(task,COLLECTION_TOOLS),COLLECTION_TOOLS)
            with self.assertRaises(ValueError): task.execute('render_source',self.args(),'')
            self.assertEqual(task.budget()['page_fetch_remaining'],8)

    def test_resources_are_source_bound_and_follow_uses_shared_slot(self):
        with TemporaryDirectory() as root, patch('ForecastAgent.runtime.retrieval.cached_page',return_value=None), \
             patch('ForecastAgent.runtime.retrieval.save_cached_page'), \
             patch('ForecastAgent.runtime.retrieval.fetch_public_page',return_value=page('{"data":[{"value":2}]}','application/json','https://example.org/data')) as fetch:
            task=self.task(root)
            view=task.execute('inspect_source_structure',{'url':URL,'view':'resources'},'')
            rid=view['items'][0]['resource_id']
            args={'resource_id':rid,'need_ids':['n'],'reason':'Read embedded data from the named authority'}
            task.execute('follow_source_resource',args,'')
            task.execute('follow_source_resource',args,'')
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(task.budget()['page_fetch_remaining'],7)
            child=task.bundle['pages']['https://example.org/data']
            self.assertEqual(child['observed_resource_lineage']['parent_source_sha256'],task.bundle['pages'][URL]['sha256'])
            rows=task.execute('inspect_source_structure',{'url':'https://example.org/data','view':'data'},'')
            self.assertEqual(rows['total'],1)
            task.bundle['pages'][URL]=rendered('Changed version.')
            with self.assertRaisesRegex(ValueError,'parent changed'): task.execute('follow_source_resource',args,'')

    def test_browser_success_resume_and_raw_payload_not_in_model_view(self):
        child=rendered()
        data=page('{"data":[{"v":6.2}]}','application/json','https://example.org/api')
        data['capture_status']={'usable_text':True}
        child['data_response_capture']={'records':[{'snapshot':data}],
                                         'archived_decoded_bytes':10}
        with TemporaryDirectory() as root, patch('ForecastAgent.readers.crawl4ai.require_backend'), \
             patch('ForecastAgent.readers.crawl4ai.render_page',return_value=child) as browser:
            task=self.task(root)
            result=task.execute('render_source',self.args(),'')
            self.assertNotIn('raw_response_base64',json.dumps(result))
            self.assertIn('https://example.org/api',task.bundle['pages'])
            resumed=RetrievalTask(Path(root),REQUEST)
            result=resumed.execute('render_source',self.args(),'')
            self.assertTrue(result['no_network'])
            self.assertEqual(browser.call_count,1)
            self.assertEqual(resumed.budget()['page_fetch_remaining'],7)
            self.assertEqual(resumed.budget()['source_browser_remaining'],1)

    def test_runtime_failure_fallback_is_separately_reserved_and_not_repeated(self):
        with TemporaryDirectory() as root, patch('ForecastAgent.readers.crawl4ai.require_backend'), \
             patch('ForecastAgent.readers.crawl4ai.render_page',side_effect=TimeoutError('fixture deadline')) as first, \
             patch('ForecastAgent.readers.browser.render_page',return_value=rendered()) as fallback:
            task=self.task(root)
            task.execute('render_source',self.args(),'')
            self.assertEqual([a['status'] for a in task.bundle['fetch_attempts']],['failed','completed'])
            self.assertEqual(task.budget()['page_fetch_remaining'],6)
            self.assertEqual(task.budget()['source_browser_remaining'],0)
            task.execute('render_source',self.args(),'')
            self.assertEqual(first.call_count,1);self.assertEqual(fallback.call_count,1)

    def test_interrupted_reservation_remains_spent_and_hidden_at_cap(self):
        with TemporaryDirectory() as root, patch('ForecastAgent.readers.crawl4ai.render_page') as browser:
            task=self.task(root)
            task.bundle['fetch_attempts']=[{'url':URL,'channel':'source_browser','status':'reserved'},
                                          {'url':'https://example.org/other','channel':'source_browser','status':'failed'}]
            task.save()
            resumed=RetrievalTask(Path(root),REQUEST)
            result=resumed.execute('render_source',self.args(),'')
            self.assertTrue(result['already_attempted']);browser.assert_not_called()
            names={t['function']['name'] for t in active_tools(resumed,tools.configure(resumed,COLLECTION_TOOLS))}
            self.assertNotIn('render_source',names)

    def test_fallback_cannot_spend_last_shared_slot_twice(self):
        with TemporaryDirectory() as root, patch('ForecastAgent.readers.crawl4ai.require_backend'), \
             patch('ForecastAgent.readers.crawl4ai.render_page',side_effect=TimeoutError('deadline')), \
             patch('ForecastAgent.readers.browser.render_page') as fallback:
            task=self.task(root)
            task.bundle['fetch_attempts']=[{'url':'https://example.org/'+str(i),'status':'completed'} for i in range(7)]
            task.execute('render_source',self.args(),'')
            self.assertEqual(task.budget()['page_fetch_remaining'],0)
            self.assertEqual(len(task.bundle['fetch_attempts']),8)
            fallback.assert_not_called()

    def test_partial_dom_never_replaces_complete_http_body(self):
        with TemporaryDirectory() as root, patch('ForecastAgent.readers.crawl4ai.require_backend'), \
             patch('ForecastAgent.readers.crawl4ai.render_page',return_value=rendered('Partial result.',True)):
            task=self.task(root)
            task.bundle['pages'][URL]=rendered('Original complete report. '*8)
            original=copy.deepcopy(task.bundle['pages'][URL])
            task.execute('render_source',self.args(),'')
            self.assertEqual(task.bundle['pages'][URL]['sha256'],original['sha256'])
            self.assertEqual(len(task.bundle['page_history'][URL]),1)

    def test_historical_modes_and_guessed_urls_do_not_dispatch(self):
        with TemporaryDirectory() as root, patch('ForecastAgent.readers.crawl4ai.render_page') as browser:
            task=self.task(root)
            with self.assertRaises(ValueError): task.execute('render_source',{**self.args(),'url':'https://example.org/guessed'},'')
            task.bundle['mode']='historical_strict'
            with self.assertRaises(ValueError): task.execute('render_source',self.args(),'')
            browser.assert_not_called();self.assertEqual(task.budget()['page_fetch_remaining'],8)

    def test_actual_collector_exposes_tools_and_executes_selected_inspection(self):
        with TemporaryDirectory() as root:
            task=self.task(root)
            supplied=[]
            def model(messages,key,**kwargs):
                supplied.append({t['function']['name'] for t in kwargs['tools']})
                if len(supplied)==1:
                    return call('inspect_source_structure',{'url':URL,'view':'resources'},'inspect1')
                return call('finish_collection',{'gaps':['Target-window publication remains unavailable']},'finish'+str(len(supplied)))
            with patch('ForecastAgent.runtime.retrieval.ask_ultra',side_effect=model):
                result=run_retrieval(REQUEST,Path(root),'','fixture-key')
            self.assertTrue(any('inspect_source_structure' in names for names in supplied))
            self.assertTrue(result.get('observed_resources'))
            self.assertEqual(len(result['fetch_attempts']),0)
            self.assertEqual(len(result['searches']),0)

    def test_missing_optional_backend_routes_legacy_once(self):
        with TemporaryDirectory() as root, patch('ForecastAgent.readers.crawl4ai.require_backend',side_effect=ImportError('not installed')), \
             patch('ForecastAgent.readers.browser.render_page',return_value=rendered()) as legacy:
            task=self.task(root)
            task.execute('render_source',self.args(),'')
            self.assertEqual(legacy.call_count,1)
            self.assertEqual(task.bundle['fetch_attempts'][0]['backend'],'release_playwright')
            self.assertEqual(task.budget()['page_fetch_remaining'],7)

    def test_response_error_is_archived_without_entering_usable_pages(self):
        value=rendered()
        error=page('{"error":"No available observations for the station and period"}','application/json','https://example.org/error')
        error['capture_status']={'usable_text':False}
        value['data_response_capture']={'records':[{'snapshot':error}]}
        with TemporaryDirectory() as root, patch('ForecastAgent.readers.crawl4ai.require_backend'), \
             patch('ForecastAgent.readers.crawl4ai.render_page',return_value=value):
            task=self.task(root)
            task.execute('render_source',self.args(),'')
            self.assertNotIn(error['url'],task.bundle['pages'])
            self.assertEqual(task.bundle['failed_captures'][-1]['page']['sha256'],error['sha256'])

    def test_bad_parent_hash_rejected_without_follow_up(self):
        with TemporaryDirectory() as root, patch('ForecastAgent.runtime.retrieval.fetch_public_page') as fetch:
            task=self.task(root)
            view=task.execute('inspect_source_structure',{'url':URL,'view':'resources'},'')
            task.bundle['pages'][URL]['sha256']='bad'
            with self.assertRaises(ValueError):
                task.execute('follow_source_resource',{'resource_id':view['items'][0]['resource_id'],
                    'need_ids':['n'],'reason':'Follow saved data address'},'')
            fetch.assert_not_called();self.assertEqual(task.budget()['page_fetch_remaining'],8)
