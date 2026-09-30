import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from ForecastAgent.readers.loader import load_response
from ForecastAgent.readers.passages import rank_passages
from ForecastAgent.providers.cache import cached_page,save_cached_page,cache_path
from ForecastAgent.providers.http import download
from ForecastAgent.providers.official import endpoint,fetch_official
from ForecastAgent.runtime.context import collection_context
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.monitor_watchdog import decide
from ForecastAgent.monitor_tournament import snapshot_questions,research_saved_questions,refresh_due_ledgers
from ForecastAgent.tests.test_monitor_tournament import post
from ForecastAgent.tests.test_acquisition_channels import raw_page,BLS
from ForecastAgent.tests.test_collection import REQUEST,PLAN,URL,page


class CollectionUpgradeTests(TestCase):
    def test_long_paragraph_and_chinese_queries_keep_exact_coordinates(self):
        text='Unrelated background. '*300+'关键公告指出正式发布日期为2026年8月19日。'+' Further background.'*100
        pages={URL:{'content':text,'sha256':'hash'}}
        result=rank_passages(pages,'正式发布日期',limit=4)
        self.assertTrue(result)
        item=result[0]
        self.assertEqual(item['text'],text[item['start_char']:item['end_char']])
        self.assertIn('2026',item['text'])
        self.assertLessEqual(len(item['text']),2200)

    def test_table_rows_preserve_headers_and_raw_bytes(self):
        raw=b'<article><h1>Daily measurements</h1><p>'+b'Official daily observations. '*10+b'</p><table><tr><th>Date</th><th>USD</th></tr><tr><td>2026-08-19</td><td>123.4</td></tr></table></article>'
        result=load_response({'url':URL,'final_url':URL,'raw':raw,'content_type':'text/html'},retrieved_at='2026-09-30T00:00:00Z')
        table=next(d for d in result['documents'] if d['metadata']['format']=='html_table')
        self.assertIn('Date | USD',table['page_content'])
        self.assertIn('123.4',table['page_content'])
        self.assertEqual(base64.b64decode(result['raw_response_base64']),raw)
        self.assertEqual(result['page_date_metadata']['extraction_engine'],'trafilatura')

    def test_interstitial_and_javascript_shell_are_distinguished(self):
        from ForecastAgent.readers.quality import body_diagnostics
        self.assertEqual(body_diagnostics('Enable JavaScript to see this page')['state'],'javascript_shell')
        self.assertFalse(body_diagnostics('Verify you are human')['usable_text'])
        self.assertFalse(body_diagnostics('Your request has been flagged as potentially automated. Complete the CAPTCHA')['usable_text'])

    def test_shared_cache_is_live_ttl_checked_and_rejects_tampering(self):
        with TemporaryDirectory() as root,patch.dict('os.environ',{'FORECAST_SHARED_CACHE_ROOT':root}):
            capture=page()
            capture.update(url=URL,temporal_status='live_capture',retrieved_at_utc=datetime.now(timezone.utc).isoformat())
            save_cached_page(URL,capture)
            cached=cached_page(URL)
            self.assertEqual(cached['retrieved_at_utc'],capture['retrieved_at_utc'])
            data=json.loads(cache_path(URL).read_text());data['content']='Tampered'
            cache_path(URL).write_text(json.dumps(data))
            self.assertIsNone(cached_page(URL))
            capture['retrieved_at_utc']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
            save_cached_page(URL,capture)
            self.assertIsNone(cached_page(URL))

    @patch('ForecastAgent.runtime.retrieval.fetch_public_page')
    def test_shared_cache_reuses_body_without_new_http_or_historical_promotion(self,fetch):
        with TemporaryDirectory() as root,TemporaryDirectory() as task_root,patch.dict('os.environ',{'FORECAST_SHARED_CACHE_ROOT':root}):
            capture=page()
            capture.update(url=URL,temporal_status='live_capture',retrieved_at_utc=datetime.now(timezone.utc).isoformat())
            save_cached_page(URL,capture)
            task=RetrievalTask(Path(task_root),REQUEST);task.execute('plan_evidence',PLAN,'')
            task.bundle['source_leads'][URL]={'url':URL}
            result=task.execute('fetch_page',{'url':URL},'')
            self.assertTrue(result['cross_task_cache'])
            self.assertEqual(task.budget()['page_fetch_remaining'],8)
            fetch.assert_not_called()

    def test_conditional_304_never_manufactures_new_body(self):
        opener=Mock()
        opener.open.side_effect=HTTPError(URL,304,'Not modified',{},BytesIO())
        result=download(URL,public_check=lambda u:True,opener_factory=lambda:opener,validators={'If-None-Match':'abc'})
        self.assertTrue(result['not_modified'])
        with self.assertRaises(ValueError):
            download(URL,public_check=lambda u:True,opener_factory=lambda:opener,validators={'If-None-Match':'x\nInjected'})

    def test_304_capture_time_and_bytes_remain_original(self):
        from ForecastAgent.providers.ultra import fetch_public_page
        capture=page()
        with patch('ForecastAgent.providers.http.download',return_value={'not_modified':True}):
            result=fetch_public_page(URL,validators={'If-None-Match':'abc'},previous_page=capture)
        self.assertEqual(result['retrieved_at_utc'],capture['retrieved_at_utc'])
        self.assertEqual(result['sha256'],capture['sha256'])
        self.assertEqual(result['http_revalidation']['status'],304)

    def test_official_range_parameters_and_month_rows(self):
        url=endpoint('treasury_debt',start_date='2026-08-19',end_date='2026-09-01')
        self.assertIn('record_date%3Agte%3A2026-08-19',url)
        result=fetch_official('bls_cpi','',1,lambda u:raw_page(BLS),start_date='2026-08-19',end_date='2026-08-20')
        self.assertEqual(result['rows'][0]['period'],'M08')
        with self.assertRaises(ValueError):endpoint('bls_cpi',start_date='2020-01-01',end_date='2026-01-01')
        with self.assertRaises(ValueError):endpoint('treasury_debt',start_date='2026-09-02',end_date='2026-09-01')

    def test_context_projection_preserves_tool_pairs_and_original_messages(self):
        with TemporaryDirectory() as root:
            task=RetrievalTask(Path(root),REQUEST);task.execute('plan_evidence',PLAN,'')
            messages=[{'role':'system','content':'Collection only'}]
            for i in range(10):
                messages.extend([{'role':'assistant','tool_calls':[{'id':str(i),'type':'function','function':{'name':'read_document','arguments':'{}'}}]},
                                 {'role':'tool','tool_call_id':str(i),'content':json.dumps({'text':'a'*8000})}])
            task.bundle['messages']=messages
            original=deepcopy(messages)
            view=collection_context(task)
            ids={c['id'] for m in view for c in m.get('tool_calls',[])}
            self.assertTrue(all(m['tool_call_id'] in ids for m in view if m['role']=='tool'))
            self.assertEqual(task.bundle['messages'],original)
            self.assertLess(len(json.dumps(view)),len(json.dumps(messages))/2)

    def test_new_live_profile_has_three_searches_and_channel_plan_estimates(self):
        with TemporaryDirectory() as root:
            task=RetrievalTask(Path(root),{**REQUEST,'acquisition_profile':'collection_v3'})
            task.execute('plan_evidence',PLAN,'')
            result=task.execute('plan_channels',{'channels':[{'channel':'historical_archive','need_ids':['n'],'expected_http_attempts':2,'reason':'Preserve critical slots'}]},'')
            self.assertEqual(task.search_limit,3)
            self.assertTrue(task.optimized)
            self.assertEqual(result['http_plan']['estimated_attempts'],2)

    @patch('ForecastAgent.runtime.retrieval.fetch_public_page')
    def test_live_cache_is_not_used_by_historical_task(self,fetch):
        with TemporaryDirectory() as root,TemporaryDirectory() as directory,patch.dict('os.environ',{'FORECAST_SHARED_CACHE_ROOT':root}):
            capture=page();capture.update(url=URL,temporal_status='live_capture',retrieved_at_utc=datetime.now(timezone.utc).isoformat())
            save_cached_page(URL,capture)
            fetch.return_value=deepcopy(capture)
            task=RetrievalTask(Path(directory),{**REQUEST,'mode':'historical_exploratory','acquisition_profile':'collection_v3','as_of_utc':'2026-08-20T00:00:00Z'})
            task.execute('plan_evidence',PLAN,'');task.bundle['source_leads'][URL]={'url':URL}
            result=task.execute('fetch_page',{'url':URL},'')
            self.assertTrue(result['blocked'])
            self.assertEqual(task.budget()['page_fetch_remaining'],7)
            fetch.assert_called_once()

    @patch('ForecastAgent.runtime.retrieval.fetch_public_page')
    def test_scheduled_refresh_uses_update_budget_once_and_skips_closed_ids(self,fetch):
        with TemporaryDirectory() as root:
            directory=Path(root)/'retrieval'/'11';directory.mkdir(parents=True)
            task=RetrievalTask(directory,REQUEST);task.execute('plan_evidence',PLAN,'')
            capture=page();capture.update(url=URL,temporal_status='live_capture',capture_method='direct_http',retrieved_at_utc='2026-09-28T00:00:00Z')
            task.bundle['pages'][URL]=capture
            task.bundle['selected_sources'][URL]={'need_ids':['n']}
            task.bundle['result']={'status':'collected'};task.save()
            fetch.return_value=deepcopy(capture)
            now=datetime.now(timezone.utc)
            self.assertEqual(refresh_due_ledgers(Path(root),set(),now),[])
            report=refresh_due_ledgers(Path(root),{11},now)
            self.assertEqual(report[0]['status'],'refreshed')
            refreshed=json.loads(task.path.read_text())
            self.assertEqual(len(refreshed['update_attempts']),1)
            self.assertEqual(len(refreshed['fetch_attempts']),0)
            self.assertEqual(refresh_due_ledgers(Path(root),{11},now),[])
            fetch.assert_called_once()

    def test_watchdog_fresh_busy_stale_and_uncertain_dispatch_cooldown(self):
        now=datetime(2026,10,1,tzinfo=timezone.utc)
        self.assertEqual(decide([],now.isoformat(),now)['action'],'none')
        self.assertEqual(decide([],None,now)['action'],'dispatch')
        active=[{'id':1,'created_at':now.isoformat(),'status':'queued'}]
        self.assertEqual(decide(active,None,now)['action'],'wait')
        self.assertEqual(decide([],None,now,last_dispatch=now.isoformat())['action'],'wait')
        old=now-timedelta(hours=2)
        self.assertTrue(decide([{'id':1,'created_at':old.isoformat(),'status':'queued'}],None,now)['needs_attention'])

    @patch('ForecastAgent.agent.run_research')
    @patch('ForecastAgent.monitor_tournament.get_json')
    def test_split_poll_never_calls_model_and_worker_obeys_snapshot_only(self,get_json,research):
        get_json.return_value={'results':[post(1,11)],'next':None}
        with TemporaryDirectory() as incoming,TemporaryDirectory() as worker,patch.dict('os.environ',{'QUEUE_READ_ONLY_RESEARCH':'0'}):
            snapshot_questions('token',Path(incoming),research=False)
            result=research_saved_questions(Path(incoming),Path(worker))
            self.assertTrue(result.exists())
        research.assert_not_called()

    @patch('ForecastAgent.agent.run_research')
    @patch('ForecastAgent.monitor_tournament.get_json')
    def test_worker_refuses_missing_completed_ledger(self,get_json,research):
        get_json.return_value={'results':[post(1,11)],'next':None}
        with TemporaryDirectory() as incoming,TemporaryDirectory() as worker:
            snapshot_questions('token',Path(incoming),research=False)
            state={'schema_version':1,'seen_question_ids':[11],'researched_question_ids':[11],'archive_next_url':None}
            (Path(worker)/'state.json').write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError,'budget reset'):research_saved_questions(Path(incoming),Path(worker))
        research.assert_not_called()
