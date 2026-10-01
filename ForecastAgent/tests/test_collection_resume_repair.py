"""Regression coverage for blocked-body loops, bounded dispatch and explicit supplements."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.telemetry import model_observer
from ForecastAgent.historical_batch import initialize, enable_exa

REQUEST={'id':'1','question':'Agency release','resolution_criteria':'https://example.org/source',
         'mode':'historical_exploratory','as_of_utc':'2026-03-01T00:00:00Z','pipeline':'collection','acquisition_profile':'collection_v3'}
PLAN=[{'id':'n','priority':'critical','condition':'Release','expected_source':'Agency','query':'Agency release'}]
PAGE={'content':'This current body announces a later winner. '*8,'documents':[],
      'sha256':'raw','temporal_status':'current_capture_possible_later_edits','url':'https://example.org/source'}


def call(name,args,identifier):
    return {'tool_calls':[{'id':identifier,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}


class ResumeRepairTests(TestCase):
    def test_blocked_reads_are_errors_and_do_not_expose_navigation(self):
        with TemporaryDirectory() as directory:
            task=RetrievalTask(Path(directory),REQUEST)
            task.bundle['plan']=PLAN
            task.bundle['pages']['https://example.org/source']=deepcopy(PAGE)
            task.bundle['source_leads']['https://example.org/later-winner']={'url':'https://example.org/later-winner',
                'origin':'page_link','parent_url':'https://example.org/source'}
            blocked=task.execute('read_document',{'url':'https://example.org/source','start_char':0},'')
            self.assertTrue(blocked['blocked'])
            self.assertEqual(blocked['content'],'')
            self.assertIsNone(blocked['next_start'])
            self.assertIn('error',blocked)
            located=task.execute('read_sources',{'urls':['https://example.org/source'],
                'queries':[{'query':'release','need_ids':['n']}]},'')
            self.assertTrue(located['no_progress'])
            self.assertIn('error',located)
            self.assertFalse(located['reads'][0]['ok'])
            self.assertNotIn('https://example.org/later-winner',task.catalog())

    def test_finish_exports_gaps_when_recent_search_is_missing(self):
        with TemporaryDirectory() as directory:
            task=RetrievalTask(Path(directory),REQUEST);task.bundle['plan']=PLAN
            task.bundle['pages']['https://example.org/source']=deepcopy(PAGE)
            result=task.execute('finish_collection',{'gaps':[]},'')
            self.assertFalse(result['acquisition_complete'])
            self.assertTrue(any('recent search' in g for g in result['discovery_notes']))
            self.assertTrue(any('Critical needs' in g for g in result['gaps']))
            self.assertFalse(result.get('incomplete',False))
            self.assertTrue((Path(directory)/'intelligence.json').exists())

    def test_physical_dispatch_cap_includes_retries_and_keeps_old_attempts(self):
        with TemporaryDirectory() as directory:
            task=RetrievalTask(Path(directory),REQUEST)
            task.bundle['model_attempts']=[{'id':1,'status':'received'}]
            observe=model_observer(task,dispatch_limit=2)
            for retry in range(2):
                observe('reserve',{'started_at_utc':'2026-10-01T00:00:00Z','retry_index':retry,'status':'reserved'})
            with self.assertRaises(RuntimeError):
                observe('reserve',{'started_at_utc':'2026-10-01T00:00:00Z','retry_index':2,'status':'reserved'})
            self.assertEqual(len(task.bundle['model_attempts']),3)
            self.assertEqual(task.bundle['model_attempts'][0],{'id':1,'status':'received'})

    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_resume_reopens_dispatch_latch_without_resetting_ledger(self, model):
        model.side_effect=[call('list_channels',{},'catalog'),call('finish_collection',{'gaps':['Missing archive']},'end')]
        with TemporaryDirectory() as directory:
            task=RetrievalTask(Path(directory),REQUEST);task.bundle['plan']=PLAN
            task.bundle['result']={'incomplete':True}
            task.bundle['control'].update(forced_close=True,consecutive_errors=5)
            task.bundle['searches']=[{'status':'failed','results':[]}]
            task.bundle['model_attempts']=[{'id':1,'status':'received'}]
            task.save()
            result=run_retrieval(REQUEST,directory,'','')
            self.assertIsNone(model.call_args_list[0].kwargs['forced_tool'])
            self.assertEqual(len(result['searches']),1)
            self.assertEqual(len(result['model_attempts']),1)
            self.assertFalse(result['result'].get('incomplete',False))

    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_repeated_blocked_reads_force_a_gap_export_in_five_calls(self, model):
        model.side_effect=[call('read_document',{'url':'https://example.org/source','start_char':i},str(i)) for i in range(3)]
        model.side_effect=list(model.side_effect)+[call('finish_collection',{'gaps':['No historical body']},'end')]
        with TemporaryDirectory() as directory:
            task=RetrievalTask(Path(directory),REQUEST);task.bundle['plan']=PLAN
            task.bundle['pages']['https://example.org/source']=deepcopy(PAGE);task.save()
            result=run_retrieval(REQUEST,directory,'','')
            self.assertLessEqual(model.call_count,5)
            self.assertEqual(model.call_args.kwargs['forced_tool'],'finish_collection')
            self.assertFalse(result['result']['acquisition_complete'])
            self.assertFalse(result['result'].get('incomplete',False))

    def test_authorized_exa_supplement_is_idempotent_and_preserves_counters(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);fixture=root/'input.json'
            fixture.write_text(json.dumps([{k:v for k,v in REQUEST.items() if k not in {'mode','pipeline','acquisition_profile'}}]),encoding='utf-8')
            initialize(root/'campaign',fixture,'collection_v3')
            task=RetrievalTask(root/'campaign'/'tasks'/'1',REQUEST)
            task.bundle['searches']=[{'status':'failed','results':[]}]
            task.bundle['model_attempts']=[{'id':1,'status':'received','usage':{'total_tokens':100}}]
            task.bundle['result']={'incomplete':True}
            task.save()
            original=deepcopy(task.bundle)
            first=enable_exa(root/'campaign','explicit-user-authorization','Repair the existing campaign')
            second=enable_exa(root/'campaign','explicit-user-authorization','Repair the existing campaign')
            restored=RetrievalTask(task.directory,REQUEST)
            self.assertEqual(first['enabled_tasks'],['1'])
            self.assertEqual(second['enabled_tasks'],[])
            self.assertEqual(restored.bundle['searches'],original['searches'])
            self.assertEqual(restored.bundle['model_attempts'],original['model_attempts'])
            self.assertEqual(restored.bundle['request_hash'],original['request_hash'])
            self.assertEqual(restored.budget()['exa_search_remaining'],1)
            self.assertEqual(restored.budget()['tavily_basic_remaining'],2)
            self.assertEqual(len(restored.bundle['budget_amendments']),1)
