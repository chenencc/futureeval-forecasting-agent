"""Fault injection for bounded dispatch, recovery and batch circuit breaking."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import os

from ForecastAgent.collection_campaign import prepare,run_batch,read,write,reconcile,resume_provider
from ForecastAgent.collection_campaign import now
from ForecastAgent.runtime.batch_health import failure,retry_minutes,transport_records


class BatchScalingTests(TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'batch';self.fixture=Path(temp.name)/'input.json'
        write(self.fixture,[{'id':str(i),'question':'Question '+str(i),'resolution_criteria':'Official publication'} for i in range(1,101)])
        env=patch.dict(os.environ,{'OPENROUTER_API_KEY':'mock-only','TAVILY_API_KEY':'mock-only','EXA_API_KEY':'mock-only'})
        env.start();self.addCleanup(env.stop)
        guard=patch('socket.socket.connect',side_effect=AssertionError('External connections forbidden'))
        guard.start();self.addCleanup(guard.stop)

    def completed(self,request,directory,*keys):
        b=read(directory/'bundle.json')
        b['result']={'incomplete':False,'acquisition_complete':True}
        b['acceptance']={'status':'accepted'}
        write(directory/'bundle.json',b)

    def limited(self,request,directory,*keys):
        b=read(directory/'bundle.json');path='rate-limit.json'
        write(directory/path,{'http_status':429,'status':'http_error'})
        b['model_attempts']=[{'path':path,'status':'http_error'}]
        b['result']={'incomplete':True}
        write(directory/'bundle.json',b)

    def test_same_root_focus_change_refused_without_quota_reset(self):
        prepare(self.root,self.fixture,5,raw_recall=True)
        self.assertEqual(read(self.root/'campaign.json')['requests']['1']['acquisition_focus'],'raw_recall')
        with self.assertRaisesRegex(ValueError,'reset'):
            prepare(self.root,self.fixture,5)

    def test_raw_empty_success_flag_is_reassessed_as_gap(self):
        prepare(self.root,self.fixture,1,raw_recall=True)
        def empty(request,directory,*keys):
            self.completed(request,directory,*keys)
            b=read(directory/'bundle.json');b['exa_searches']=[{'status':'completed','results':[]}]
            write(directory/'bundle.json',b)
        result=run_batch(self.root,1,empty)
        self.assertEqual(result['completion_states'],{'complete_with_gaps':1})
        self.assertEqual(result['task_reports'][0]['quality_inventory']['raw_capture_count'],0)
        self.assertFalse(result['task_reports'][0]['quality_inventory']['full_recall_verified'])

    def test_parameter_failure_is_terminal_and_other_tasks_continue(self):
        prepare(self.root,self.fixture,3)
        def runner(request,directory,*keys):
            if str(request['id'])=='1':raise ValueError('Invalid channel parameters')
            self.completed(request,directory,*keys)
        first=run_batch(self.root,3,runner)
        self.assertEqual(first['completion_states'],{'terminal_failure':1,'complete':2})
        self.assertEqual(first['failure_categories'],{'contract_or_integrity':1})
        with patch('ForecastAgent.collection_campaign.RetrievalTask') as task:
            run_batch(self.root,3,runner);task.assert_not_called()

    def test_outage_across_two_tasks_pauses_queue_and_persists(self):
        prepare(self.root,self.fixture,5)
        result=run_batch(self.root,5,self.limited)
        self.assertEqual(result['states'],{'incomplete':2,'pending':3})
        self.assertTrue(result['pause_until_utc'])
        before=read(self.root/'campaign.json')
        run_batch(self.root,5,self.completed)
        self.assertEqual(before,read(self.root/'campaign.json'))
        self.assertEqual(result['resources']['model_http'],2)

    def test_provider_auth_pause_requires_explicit_recovery_and_preserves_ledger(self):
        prepare(self.root,self.fixture,2)
        def denied(request,directory,*keys):
            b=read(directory/'bundle.json')
            b['searches']=[{'status':'failed','detail':'HTTP Error 401: Unauthorized'}]
            b['result']={'incomplete':True}
            write(directory/'bundle.json',b)
        result=run_batch(self.root,2,denied)
        self.assertTrue(result['requires_provider_review'])
        self.assertEqual(result['states'],{'needs_attention':1,'pending':1})
        run_batch(self.root,2,self.completed)
        before=read(self.root/'tasks/1/bundle.json')
        recovered=resume_provider(self.root,'Operator corrected the provider credentials and verified readiness.')
        self.assertFalse(recovered['requires_provider_review'])
        self.assertEqual(before,read(self.root/'tasks/1/bundle.json'))
        self.assertFalse(read(self.root/'campaign.json')['provider_resumptions'][-1]['budget_reset'])
        self.assertEqual(run_batch(self.root,2,self.completed)['states'],{'needs_attention':1,'acquired':1})

    def test_crashed_owner_reconciles_and_does_not_repeat_reservation(self):
        prepare(self.root,self.fixture,2)
        def crash(request,directory,*keys):
            b=read(directory/'bundle.json')
            b['searches']=[{'status':'reserved','query':'Already sent; response unknown'}]
            write(directory/'bundle.json',b)
            raise KeyboardInterrupt('Simulated owner loss')
        with self.assertRaises(KeyboardInterrupt):run_batch(self.root,1,crash)
        campaign=read(self.root/'campaign.json')
        recovered=reconcile(self.root,campaign)
        self.assertEqual(recovered['tasks']['1']['attempts'][0]['status'],'owner_interrupted')
        write(self.root/'campaign.json',recovered)
        before=read(self.root/'tasks/1/bundle.json')
        run_batch(self.root,1,self.completed)
        self.assertEqual(before,read(self.root/'tasks/1/bundle.json'))
        self.assertEqual(recovered['tasks']['1']['resources']['tavily_basic'],1)

    def test_rollback_of_consumed_counter_is_rejected(self):
        prepare(self.root,self.fixture,1)
        def one(request,directory,*keys):
            self.completed(request,directory,*keys)
            b=read(directory/'bundle.json');b['searches']=[{'status':'completed'}];write(directory/'bundle.json',b)
        run_batch(self.root,1,one)
        b=read(self.root/'tasks/1/bundle.json');b['searches']=[];write(self.root/'tasks/1/bundle.json',b)
        with self.assertRaisesRegex(ValueError,'regressed'):run_batch(self.root,1,self.completed)

    def test_transient_retry_reuses_search_ledger_after_backoff(self):
        from datetime import timedelta
        prepare(self.root,self.fixture,1)
        def interrupted(request,directory,*keys):
            b=read(directory/'bundle.json')
            b['searches']=[{'status':'reserved','query':'Unknown response remains consumed'}]
            b['exa_searches']=[{'status':'completed'}]
            write(directory/'bundle.json',b)
            raise TimeoutError('Temporary execution timeout')
        run_batch(self.root,1,interrupted)
        before=read(self.root/'tasks/1/bundle.json')
        run_batch(self.root,1,self.completed)
        self.assertEqual(len(read(self.root/'campaign.json')['tasks']['1']['attempts']),1)
        with patch('ForecastAgent.collection_campaign.now',return_value=now()+timedelta(minutes=35)):
            result=run_batch(self.root,1,self.completed)
        after=read(self.root/'tasks/1/bundle.json')
        self.assertEqual(before['searches'],after['searches'])
        self.assertEqual(before['exa_searches'],after['exa_searches'])
        self.assertEqual(result['resources']['tavily_basic'],1)
        self.assertEqual(result['resources']['exa'],1)
        self.assertEqual(result['completion_states'],{'complete':1})

    def test_transport_path_escape_and_corrupt_hash_are_terminal(self):
        self.root.mkdir()
        with self.assertRaisesRegex(ValueError,'escapes'):
            transport_records(self.root,[{'path':'../outside.json'}])
        write(self.root/'record.json',{'http_status':200})
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            transport_records(self.root,[{'path':'record.json','sha256':'invalid'}])

    def test_failure_taxonomy_and_backoff_are_bounded(self):
        self.assertFalse(failure(ValueError('Bad arguments'))['retryable'])
        self.assertTrue(failure(TimeoutError('Timed out'))['retryable'])
        self.assertTrue(failure(records=[{'http_status':503}])['retryable'])
        self.assertTrue(failure(records=[{'http_status':402}])['pause_batch'])
        self.assertEqual([retry_minutes(n) for n in (1,2,3,20)],[30,60,120,360])
        self.assertTrue(failure(bundle={'result':{'incomplete':True,'termination_reason':'deadline'}})['retryable'])

    def test_raw_context_has_capture_inventory_instead_of_excerpt_obligations(self):
        import json
        from ForecastAgent.tests.test_runtime_contracts import prepared,LIVE
        from ForecastAgent.runtime.context import collection_context
        with TemporaryDirectory() as root:
            task=prepared(root,{**LIVE,'acquisition_focus':'raw_recall'})
            task.bundle['messages']=[{'role':'system','content':'Raw collection only'}]
            context=collection_context(task)
            state=json.loads(context[1]['content'])
            self.assertEqual(state['acquisition_inventory']['schema'],'raw_acquisition_checkpoint_v1')
            self.assertNotIn('pending_passage_ids',state)
            self.assertNotIn('excerpts',state)

    def test_100_tasks_close_in_twenty_dispatches_without_duplicate_execution(self):
        prepare(self.root,self.fixture,100)
        for _ in range(20):result=run_batch(self.root,5,self.completed)
        self.assertEqual(result['completion_states'],{'complete':100})
        self.assertEqual(len(result['task_reports']),100)
        self.assertEqual(result['scheduler']['max_active_tasks'],1)
        self.assertEqual(result['resources']['model_http'],0)
        before=deepcopy(read(self.root/'campaign.json')['tasks'])
        run_batch(self.root,5,self.completed)
        self.assertEqual(before,read(self.root/'campaign.json')['tasks'])
