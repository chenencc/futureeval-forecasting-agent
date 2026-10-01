"""Capture-only acceptance and explicitly fresh experimental budget tests."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.tests.test_runtime_contracts import prepared, URL, LIVE
from ForecastAgent.tests.test_collection import page, call
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.collection_actions import next_action
from ForecastAgent.evidence.raw_capture import capture_report
from ForecastAgent.recall_pilot import run


class RawRecallTests(TestCase):
    def task(self,root):
        task=prepared(root,deepcopy(LIVE))
        task.raw_recall=True
        task.bundle['request']['acquisition_focus']='raw_recall'
        task.bundle['search_policy']['exa']='optional'
        return task

    def test_model_gap_and_missing_excerpts_do_not_reject_valid_original(self):
        with TemporaryDirectory() as root:
            task=self.task(root)
            result=task.execute('finish_collection',{'gaps':['I have not understood this source yet']},'')
            self.assertTrue(result['acquisition_complete'])
            self.assertEqual(result['excerpt_count'],0)
            self.assertEqual(result['agent_declared_gaps'],['I have not understood this source yet'])
            self.assertEqual(result['raw_capture_report']['capture_count'],1)
            self.assertFalse(result['raw_capture_report']['semantic_adequacy_verified'])

    def test_corrupt_raw_data_still_rejects_capture(self):
        with TemporaryDirectory() as root:
            task=self.task(root);task.bundle['pages'][URL]['sha256']='wrong'
            result=task.execute('finish_collection',{'gaps':[]},'')
            self.assertFalse(result['acquisition_complete'])
            self.assertEqual(task.bundle['acceptance']['status'],'failed')

    def test_blank_body_is_parse_gap_not_capture_success(self):
        with TemporaryDirectory() as root:
            task=self.task(root);task.bundle['pages'][URL]['content']=''
            report=capture_report(task.bundle)
            self.assertTrue(report['raw_integrity_passed'])
            self.assertEqual(report['readable_body_count'],0)
            self.assertIn(URL,report['parse_gap_urls'])
            self.assertFalse(task.execute('finish_collection',{'gaps':[]},'')['acquisition_complete'])

    @patch('ForecastAgent.runtime.retrieval.fetch_public_page')
    def test_batch_capture_needs_no_located_passages_or_review_loop(self,fetch):
        with TemporaryDirectory() as root:
            task=self.task(root);task.bundle['pages']={}
            fetch.return_value=page()
            result=task.execute('read_sources',{'urls':[URL]},'')
            self.assertEqual(result['located_material'],[])
            self.assertEqual(task.bundle['excerpts'],[])
            self.assertTrue(result['reads'][0]['ok'])
            self.assertIsNone(next_action(task))

    def test_unfetched_search_backlog_remains_visible_without_false_full_recall(self):
        with TemporaryDirectory() as root:
            task=self.task(root)
            task.bundle['searches']=[{'status':'completed','results':[{'url':'https://example.org/other'}]}]
            result=task.execute('finish_collection',{'gaps':[]},'')
            self.assertTrue(result['acquisition_complete'])
            self.assertEqual(result['raw_capture_report']['unfetched_discovery_urls'],['https://example.org/other'])
            self.assertFalse(result['raw_capture_report']['semantic_adequacy_verified'])

    @patch.dict(os.environ,{'TAVILY_API_KEY':'test','OPENROUTER_API_KEY':'test','GITHUB_RUN_ATTEMPT':'1'})
    def test_fresh_pilot_retains_fixture_and_refuses_same_root_reset(self):
        with TemporaryDirectory() as root:
            fixture=Path(root)/'fixture.json'
            rows=[{'id':str(i),'question':'Question '+str(i),'resolution_criteria':'Published record',
                'mode':'live','pipeline':'collection','acquisition_profile':'collection_v3'} for i in range(5)]
            fixture.write_text(json.dumps(rows),encoding='utf-8')
            def runner(request,directory,*keys):
                b=json.loads((directory/'bundle.json').read_text(encoding='utf-8'))
                self.assertFalse(b['searches']);self.assertFalse(b.get('model_attempts',[]));self.assertFalse(b['exa_searches'])
                self.assertEqual(request['acquisition_focus'],'raw_recall')
                return {'result':{},'acceptance':{}}
            path=Path(root)/'fresh'
            result=run(path,fixture,'User explicitly authorized fresh five-question quotas.',runner)
            self.assertEqual(len(result['tasks']),5)
            self.assertTrue(all(t['status']=='exported' for t in result['tasks'].values()))
            self.assertEqual(json.loads(fixture.read_text(encoding='utf-8')),rows)
            with self.assertRaisesRegex(ValueError,'already exists'):
                run(path,fixture,'User explicitly authorized fresh five-question quotas.',runner)

    @patch.dict(os.environ,{'GITHUB_RUN_ATTEMPT':'2'})
    def test_workflow_rerun_does_not_silently_grant_fresh_quotas(self):
        with TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError,'reruns'):
                run(Path(root)/'fresh',Path(root)/'unused.json','User explicitly authorized fresh five-question quotas.')

    def test_parse_failure_preserves_original_response_without_claiming_readable_body(self):
        from ForecastAgent.readers.loader import load_response
        import base64
        raw=b'not JSON'
        parsed=load_response({'raw':raw,'url':URL,'final_url':URL,'content_type':'application/json'},
            retrieved_at='2026-10-01T00:00:00Z',preserve_raw_on_failure=True)
        self.assertEqual(base64.b64decode(parsed['raw_response_base64']),raw)
        self.assertEqual(parsed['parse_failure']['type'],'JSONDecodeError')
        self.assertFalse(parsed['body_diagnostics']['usable_text'])

    @patch.dict(os.environ,{'EXA_API_KEY':'test-only'})
    @patch('ForecastAgent.runtime.retrieval.search_exa',return_value={'results':[],'raw_response':{},'request_payload':{}})
    @patch('ForecastAgent.runtime.retrieval.search_batch',return_value={'results':[]})
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_runtime_does_not_force_passage_selection_before_export(self,model,tavily,exa):
        from ForecastAgent.tests.test_runtime_contracts import SEARCH
        from ForecastAgent.tests.test_required_exa import EXA
        request={**deepcopy(LIVE),'acquisition_focus':'raw_recall'}
        model.side_effect=[call('search_tavily',SEARCH,'primary'),call('search_exa',EXA,'cross'),
            call('finish_collection',{'gaps':['Material interpretation deferred']},'end')]
        with TemporaryDirectory() as root:
            task=prepared(root,request);task.save()
            result=run_retrieval(request,root,'','')
            self.assertTrue(result['result']['acquisition_complete'])
            for invocation in model.call_args_list:
                names={t['function']['name'] for t in invocation.kwargs['tools']}
                self.assertNotIn('review_passages',names)
                self.assertNotIn('read_document',names)
            self.assertEqual(len(result['searches']),1)
            self.assertEqual(len(result['exa_searches']),1)
