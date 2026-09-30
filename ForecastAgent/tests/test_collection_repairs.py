import base64
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.collection_v2 import late_dates
from ForecastAgent.providers.dated_data import normalized,request_url
from ForecastAgent.providers.archive import archive_lookup

CUTOFF=datetime(2026,3,6,tzinfo=timezone.utc)

def response(value,url='https://example.org/source'):
 raw=json.dumps(value).encode()
 return {'url':url,'final_url':url,'raw_response_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),
         'content':raw.decode(),'retrieved_at_utc':'2026-10-01T00:00:00Z','documents':[]}

class CollectionRepairTests(TestCase):
 def task(self,temp):
  task=RetrievalTask(Path(temp),{'question':'Test','resolution_criteria':'https://example.org/source',
   'as_of_utc':'2026-03-06T00:00:00Z','pipeline':'collection','acquisition_profile':'collection_v2'})
  task.bundle['plan']=[{'id':'n','priority':'critical'}]
  return task

 def test_undated_outcome_and_localized_log_blocked_as_whole_body(self):
  with TemporaryDirectory() as temp:
   task=self.task(temp)
   task.bundle['pages']['https://example.org/source']={**response({}), 'content':'Spain won the latest final in 2026.\n4 августа 2026\nRelease details.',
    'temporal_status':'current_capture_possible_later_edits'}
   result=task.execute('read_sources',{'urls':[],'queries':[{'query':'Spain final Release','need_ids':['n']}]},'')
   self.assertFalse(result['located_material'][0]['passages'])
   self.assertNotIn('Spain',task.execute('read_document',{'url':'https://example.org/source'},'')['content'])
   with self.assertRaisesRegex(ValueError,'historical body'):
    task.execute('record_excerpt',{'url':'https://example.org/source','start_char':0,'end_char':20,'need_ids':['n']},'')

 def test_passage_ids_resume_and_source_version_check(self):
  with TemporaryDirectory() as temp:
   task=self.task(temp)
   task.bundle['pages']['https://example.org/source']={**response({}),'content':'An official regulatory paragraph about reporting requirements. '*4,
    'retrieved_at_utc':'2026-02-01T00:00:00Z','temporal_status':'local_pre_cutoff_capture'}
   p=task.execute('read_sources',{'urls':[],'queries':[{'query':'regulatory reporting','need_ids':['n']}]},'')['located_material'][0]['passages'][0]
   resumed=RetrievalTask(Path(temp),task.bundle['request'])
   args={'items':[{'passage_id':p['passage_id'],'need_ids':['n']}]}
   saved=resumed.execute('record_excerpts',args,'')['items'][0]
   self.assertTrue(saved['ok']);self.assertEqual(saved['excerpt']['text'],p['text'])
   resumed.bundle['pages']['https://example.org/source']['content']+='Changed.'
   self.assertIn('Stale',resumed.execute('record_excerpts',args,'')['items'][0]['error'])

 def test_slash_dates(self):
  self.assertTrue(late_dates('Changes on 2026/09/23',CUTOFF))

 def test_verified_resume_and_announced_future_schedule(self):
  with TemporaryDirectory() as temp:
   task=self.task(temp)
   text='The agency announced a September 4, 2026 effective date for the regulation. '*3
   task.bundle['pages']['https://example.org/source']={**response({}),'content':text,
    'retrieved_at_utc':'2026-02-01T00:00:00Z','temporal_status':'local_pre_cutoff_capture'}
   task.bundle['messages']=[{'role':'assistant','content':'Verified context'}];task.save()
   resumed=RetrievalTask(Path(temp),task.bundle['request'])
   p=resumed.execute('read_sources',{'urls':[],'queries':[{'query':'effective regulation','need_ids':['n']}]},'')['located_material'][0]['passages'][0]
   self.assertIn('September',p['text'])

 def test_unverified_discovery_content_hidden_from_model_projection(self):
  from ForecastAgent.runtime.collection_v2 import model_view
  original={'sources':[{'url':'https://example.org/source','title':'Later winner','content':'Later outcome'}]}
  projected=model_view(original,{'https://example.org/source'})
  self.assertNotIn('Later',json.dumps(projected))
  self.assertIn('Later',json.dumps(original))

 def test_sst_leap_calendar_and_finalization(self):
  values=[20.0]*366;values[59]=None
  page=normalized(response([{'name':'2026','data':values},{'name':'Preliminary','data':[30]*366}]),
   {'dataset':'north_atlantic_sst','start_date':'2026-02-01','end_date':'2026-03-05'},CUTOFF)
  self.assertEqual(page['rows'][-1]['date'],'2026-02-18')
  self.assertEqual(page['rows'][0]['date'],'2026-02-01')
  self.assertEqual(len(page['rows']),18)
  self.assertIn('not a verified',page['data_warning'])
  values[59]=21.5
  march=normalized(response([{'name':'2026','data':values}]),
   {'dataset':'north_atlantic_sst','start_date':'2026-03-01','end_date':'2026-03-01'},datetime(2026,4,1,tzinfo=timezone.utc))
  self.assertEqual(march['rows'][0]['sst'],21.5)
  with self.assertRaises(ValueError): request_url({'dataset':'binance_daily','start_date':'2026-03-01','end_date':'2026-03-06'},CUTOFF)

 def test_exchange_partial_and_invalid_ohlc(self):
  stamp=int(datetime(2026,3,5,tzinfo=timezone.utc).timestamp()*1000)
  v=[stamp,'10','12','8','11','2',stamp+86399999]
  args={'dataset':'binance_daily','start_date':'2026-03-05','end_date':'2026-03-05'}
  self.assertEqual(normalized(response([v]),args,CUTOFF)['rows'][0]['high'],12)
  v[6]=int(CUTOFF.timestamp()*1000)
  self.assertFalse(normalized(response([v]),args,CUTOFF)['rows'])
  v[6]=stamp+86399999;v[2]='5'
  with self.assertRaises(ValueError):normalized(response([v]),args,CUTOFF)

 def test_sec_acceptance_cutoff_and_identity(self):
  data={'cik':123,'name':'Issuer','filings':{'recent':{'form':['S-1','S-1/A'],
   'filingDate':['2026-03-05']*2,'accessionNumber':['0000000123-26-000001']*2,
   'primaryDocument':['s1.htm']*2,'acceptanceDateTime':['2026-03-05T20:00:00Z','2026-03-06T01:00:00Z']},'files':[{'name':'older.json'}]}}
  args={'dataset':'sec_submissions','cik':'123','start_date':'2026-03-01','end_date':'2026-03-05'}
  page=normalized(response(data),args,CUTOFF)
  self.assertEqual(len(page['rows']),1);self.assertTrue(page['pagination']['has_more'])
  with self.assertRaises(ValueError):normalized(response(data),{**args,'cik':'124'},CUTOFF)

 @patch('ForecastAgent.retrieval_agent.fetch_public_page')
 def test_physical_budget_failure_and_cache(self,fetch):
  with TemporaryDirectory() as temp:
   task=self.task(temp)
   fetch.return_value=response([{'name':'2026','data':[20]*366}])
   args={'dataset':'north_atlantic_sst','start_date':'2026-02-01','end_date':'2026-02-02','need_ids':['n']}
   task.execute('collect_dataset',args,'');task.execute('collect_dataset',args,'')
   self.assertEqual(fetch.call_count,1)
   fetch.side_effect=RuntimeError('Unavailable')
   with self.assertRaises(RuntimeError):task.bounded_data_fetch('https://example.org/fail')
   self.assertEqual(len(task.bundle['fetch_attempts']),2)
   self.assertEqual(task.bundle['fetch_attempts'][-1]['status'],'failed')

 def test_archive_exact_time_and_redirect(self):
  url='https://example.org/source';replay='https://web.archive.org/web/20260201000000id_/'+url
  pages=iter([response([['timestamp','original'],['20260201000000',url]]),response({'body':'Archived'},replay)])
  page=archive_lookup(url,CUTOFF,lambda _:next(pages))
  self.assertEqual(page['temporal_status'],'archive_pre_cutoff_capture')
  pages=iter([response([['timestamp','original'],['20260201000000',url]]),response({},'https://example.org/current')])
  with self.assertRaisesRegex(ValueError,'redirected'):archive_lookup(url,CUTOFF,lambda _:next(pages))

 def test_closed_tasks_cannot_spend_initial_budget_on_new_tools(self):
  with TemporaryDirectory() as temp:
   task=self.task(temp);task.bundle['result']={'status':'collected'}
   for name in ['collect_dataset','collect_archive']:
    with self.assertRaisesRegex(ValueError,'already finished'):task.execute(name,{},'')
   self.assertEqual(len(task.bundle['fetch_attempts']),0)
