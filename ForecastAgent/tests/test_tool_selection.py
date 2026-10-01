"""Capability exposure and explicit supplement retries preserve paid ledgers."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval, COLLECTION_TOOLS
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.historical_batch import initialize, enable_exa, resume_exa
from ForecastAgent.providers.exa_search import ExaError
from ForecastAgent.tests.test_collection_resume_repair import REQUEST, PLAN, call


class ToolSelectionTests(TestCase):
    def test_historical_exhaustion_and_forced_schema_reduce_payload(self):
        with TemporaryDirectory() as directory:
            task=RetrievalTask(Path(directory),REQUEST)
            task.bundle['plan']=deepcopy(PLAN)
            task.bundle['searches']=[{'status':'failed','results':[]}]*3
            task.bundle['channel_plan']=[{'channel':'tavily_search'}]
            task.bundle['fetch_attempts']=[{'status':'failed'}]*8
            tools=active_tools(task,COLLECTION_TOOLS)
            names={t['function']['name'] for t in tools}
            for name in ('plan_evidence','plan_channels','search_tavily','search_exa',
                         'collect_official','collect_polymarket','collect_archive',
                         'read_document','record_excerpts','read_dataset_rows'):
                self.assertNotIn(name,names)
            self.assertIn('finish_collection',names)
            forced=active_tools(task,COLLECTION_TOOLS,'finish_collection')
            self.assertEqual([t['function']['name'] for t in forced],['finish_collection'])
            self.assertLess(len(json.dumps(forced)),len(json.dumps(COLLECTION_TOOLS))/10)

    def test_need_identifiers_are_bound_without_mutating_registry(self):
        with TemporaryDirectory() as directory:
            task=RetrievalTask(Path(directory),REQUEST);task.bundle['plan']=deepcopy(PLAN)
            original=deepcopy(COLLECTION_TOOLS)
            tool=active_tools(task,COLLECTION_TOOLS,'search_exa')[0]
            self.assertEqual(tool['function']['parameters']['properties']['need_ids']['items']['enum'],['n'])
            self.assertEqual(COLLECTION_TOOLS,original)

    def test_resume_exa_is_idempotent_and_never_grants_another_allowance(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ,{'EXA_API_KEY':''}):
            root=Path(directory)/'campaign';fixture=Path(directory)/'input.json'
            fixture.write_text(json.dumps([{k:v for k,v in REQUEST.items() if k not in {'mode','pipeline','acquisition_profile'}}]))
            initialize(root,fixture,'collection_v3')
            task=RetrievalTask(root/'tasks'/'1',REQUEST)
            task.bundle['result']={'incomplete':True}; task.save()
            enable_exa(root,'authorized','Existing campaign repair')
            task=RetrievalTask(task.directory,REQUEST)
            task.bundle['result']={'status':'collected_with_gaps','acquisition_complete':False}
            task.bundle['model_attempts']=[{'id':1,'status':'received'}]
            task.bundle['searches']=[{'status':'failed','results':[]}];task.save()
            original=deepcopy(task.bundle)
            self.assertEqual(resume_exa(root,'wrong-authorization','Repair')['resumed_tasks'],[])
            self.assertEqual(resume_exa(root,'authorized','Repair')['resumed_tasks'],['1'])
            self.assertEqual(resume_exa(root,'authorized','Repair')['resumed_tasks'],[])
            restored=RetrievalTask(task.directory,REQUEST).bundle
            for key in ('acquisition_limits','model_attempts','searches','request_hash','budget_amendments'):
                self.assertEqual(restored[key],original[key])
            self.assertEqual(restored['result_history'],[original['result']])
            self.assertEqual(len(restored['supplement_requests']),1)
            self.assertFalse(restored['supplement_requests'][0]['new_exa_allowance'])

    @patch.dict(os.environ,{'EXA_API_KEY':'test-only'})
    @patch('ForecastAgent.runtime.retrieval.search_exa')
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_authorized_supplement_is_one_forced_attempt_then_closes(self, model, provider):
        provider.return_value={'results':[],'raw_response':{},'request_payload':{},'request_id':'empty'}
        args={'query':'agency official release','need_ids':['n'],'reason':'Missing official source',
              'search_role':'official_gap','category':'general','include_domains':['example.org']}
        model.side_effect=[call('search_exa',args,'exa'),call('finish_collection',{'gaps':['No historical snapshot']},'end')]
        with TemporaryDirectory() as directory:
            task=RetrievalTask(Path(directory),REQUEST);task.bundle['plan']=deepcopy(PLAN)
            task.bundle['result']={'incomplete':True}
            task.bundle['control']['exa_supplement_required']=True;task.save()
            result=run_retrieval(REQUEST,directory,'','')
            self.assertEqual(model.call_args_list[0].kwargs['forced_tool'],'search_exa')
            self.assertEqual([t['function']['name'] for t in model.call_args_list[0].kwargs['tools']],['search_exa'])
            first_context=model.call_args_list[0].args[0]
            self.assertIn('needs',json.loads(first_context[1]['content']))
            self.assertNotIn('channel_catalog',json.loads(first_context[1]['content']))
            self.assertNotIn('search_exa',[t['function']['name'] for t in model.call_args_list[1].kwargs['tools']])
            self.assertEqual(provider.call_count,1)
            self.assertEqual(len(result['exa_searches']),1)
            self.assertFalse(result['result'].get('incomplete',False))

    @patch.dict(os.environ,{'EXA_API_KEY':'test-only'})
    @patch('ForecastAgent.runtime.retrieval.search_exa',side_effect=ExaError(402,'Out of credits'))
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_failed_supplement_is_spent_and_does_not_block_closing(self, model, provider):
        args={'query':'agency official release','need_ids':['n'],'reason':'Missing official source','search_role':'official_gap'}
        model.side_effect=[call('search_exa',args,'exa'),call('finish_collection',{'gaps':['Provider unavailable']},'end')]
        with TemporaryDirectory() as directory:
            task=RetrievalTask(Path(directory),REQUEST);task.bundle['plan']=deepcopy(PLAN)
            task.bundle['result']={'incomplete':True};task.bundle['control']['exa_supplement_required']=True;task.save()
            result=run_retrieval(REQUEST,directory,'','')
            self.assertEqual(provider.call_count,1)
            self.assertEqual(result['exa_searches'][0]['http_status'],402)
            self.assertIsNone(model.call_args_list[1].kwargs['forced_tool'])
            self.assertFalse(result['result'].get('incomplete',False))
