"""Actual shadow queue adapters with synthetic stages and physical fault records."""
import copy
import json
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.analysis.pilot import digest
from ForecastAgent.competition.queue import utc
from ForecastAgent.competition.platform import BOT_ID
from ForecastAgent.intelligence import worker
from ForecastAgent.releases import stress
from ForecastAgent.research_loop import decision_http, state, fusion
from ForecastAgent.tests.test_competition_mercury import response
from ForecastAgent.tests.test_research_loop import source_bundle
from ForecastAgent.tests.test_research_reference_map import proposal, apply


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.docs={str(100+i):{'id':100+i,'title':str(i),'user_permission':'forecaster',
            'question':stress.question(i,stress.KINDS[(i-1)%5])} for i in range(1,8)}
        self.platform=stress.Platform(self.docs)
        self.incoming=stress.snapshot(self.root/'incoming',self.docs)
        self.collected=[];self.scored=[];self.maps=True;self.empty=False

    def tearDown(self):self.tmp.cleanup()

    def acquisition(self, request_path, directory, clock):
        req=load(request_path);self.collected.append(req['id'])
        package=source_bundle(req['question_type']);package['request']=req
        package['plan']=[{'id':'release','condition':'Obtainable comparable baseline.',
            'priority':'critical','expected_source':'Official release','query':'report'}]
        state.initialize(package)
        fusion.initialize(SimpleNamespace(bundle=package,cutoff=None))
        if self.maps:apply(package,proposal(package))
        if self.empty:package['pages']={}
        save(directory/'final-map-package.json',package)
        save(directory/'worker-collection-result.json',{'complete':True})

    def decide(self, original, heads, key, observer, *, required_questions=None):
        record={'status':'reserved','request':{'state':original,'questions':heads}}
        token=observer('reserve',record);self.scored.append(original['question']['id'])
        answer=response(heads)
        record.update(status='received',response=answer);observer('complete',record,token)
        return answer

    def run_worker(self, root=None):
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch.object(decision_http,'decide',self.decide):
            return worker.run(root or self.root/'worker',self.incoming,client=self.platform,
                adapter=worker.Adapter(acquisition=self.acquisition))

    def test_seven_mixed_questions_take_two_dispatches_and_replay_has_no_calls(self):
        first=self.run_worker();self.assertEqual(len(first['processed_ids']),5)
        second=self.run_worker();self.assertEqual(len(second['processed_ids']),2)
        third=self.run_worker();self.assertEqual(third['processed_ids'],[])
        self.assertEqual(second['state_distribution'],{'shadow_scored':7})
        self.assertEqual(len(self.collected),7);self.assertEqual(len(self.scored),7)
        self.assertEqual(self.platform.writes,[]);self.assertFalse(second['enabled'])
        for p in (self.root/'worker/tasks').glob('*/candidate.json'):
            self.assertEqual(load(p)['selection'],'mapped')
            self.assertFalse((p.parent/'submission.json').exists())
            state.verify_journal(load(p.parent/'retrieval/graph-intelligence/final-map-package.json')['research_loop'])

    def test_graph_absence_uses_original_once_without_made_up_map(self):
        self.maps=False;self.run_worker()
        self.assertTrue(all(load(p)['selection']=='original' for p in (self.root/'worker/tasks').glob('*/candidate.json')))
        self.assertEqual(len(self.scored),5)

    def test_empty_originals_block_without_default_or_repeated_acquisition(self):
        self.empty=True;self.maps=False
        self.run_worker();self.run_worker();self.run_worker()
        self.assertEqual(len(self.collected),7);self.assertEqual(self.scored,[])
        self.assertEqual(self.platform.writes,[])
        self.assertTrue(all(t['stage']=='provider_blocked' for t in load(self.root/'worker/campaign.json')['tasks'].values()))

    def test_platform_closed_deadline_and_existing_forecast_skip_before_providers(self):
        self.platform.documents['101']['question']['status']='closed'
        self.platform.documents['102']['question']['scheduled_close_time']=(utc()-timedelta(minutes=1)).isoformat()
        self.platform.documents['103']['question']['my_forecasts']={'latest':{'forecast_values':[.5,.5],'author_id':BOT_ID},'history':[]}
        self.run_worker();self.assertFalse({'1','2','3'} & set(self.collected))
        tasks=load(self.root/'worker/campaign.json')['tasks']
        self.assertEqual(tasks['1']['stage'],'closed');self.assertEqual(tasks['2']['stage'],'deadline_missed')
        self.assertEqual(tasks['3']['stage'],'already_forecasted')

    def test_frozen_rules_changed_on_resume_block_without_new_network(self):
        self.run_worker();root=self.root/'worker';ledger=load(root/'campaign.json')
        ledger['tasks']['1']['stage']='retry_wait';ledger['tasks']['1']['retry_at_utc']=None;save(root/'campaign.json',ledger)
        self.platform.documents['101']['question']['resolution_criteria']='Changed immutable rules.'
        before=len(self.scored);self.run_worker()
        self.assertEqual(load(root/'campaign.json')['tasks']['1']['stage'],'blocked_integrity')
        self.assertEqual(self.scored.count('1'),1);self.assertEqual(self.collected.count('1'),1)

    def test_corrupted_candidate_is_not_delivered(self):
        self.run_worker();root=self.root/'worker';ledger=load(root/'campaign.json')
        ledger['tasks']['1']['stage']='retry_wait';ledger['tasks']['1']['retry_at_utc']=None;save(root/'campaign.json',ledger)
        p=root/'tasks/1/candidate.json';value=load(p);value['payload']['probability_yes']=.1;save(p,value)
        self.run_worker();self.assertEqual(load(root/'campaign.json')['tasks']['1']['stage'],'blocked_integrity')
        self.assertEqual(self.scored.count('1'),1);self.assertEqual(self.platform.writes,[])

    def test_mismatched_score_failure_receipt_is_durable_and_not_retried(self):
        def fail(original,heads,key,observer,**kw):
            token=observer('reserve',{'status':'reserved'})
            observer('complete',{'status':'http_error','http_status':503},token)
            raise RuntimeError('HTTP 503')
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch.object(decision_http,'decide',fail):
            worker.run(self.root/'worker',self.incoming,client=self.platform,adapter=worker.Adapter(acquisition=self.acquisition))
        root=self.root/'worker';ledger=load(root/'campaign.json')
        ledger['tasks']['1']['retry_at_utc']=None;save(root/'campaign.json',ledger)
        self.run_worker()
        self.assertEqual(load(root/'campaign.json')['tasks']['1']['stage'],'blocked_integrity')
        self.assertEqual(len(list((root/'tasks/1/graph-score/decision/http').glob('*.json'))),1)

    def test_native_lifetime_remainders_and_unknown_reservation(self):
        folder=self.root/'job';path=folder/'retrieval/development-channels/collection/model_calls/0001.json'
        save(path,{'status':'http_error','http_status':502})
        save(path.with_name('0002.json'),{'status':'received'})
        save(folder/'final-map-review/model-http/0001.json',{'status':'received'})
        from ForecastAgent.runtime import retrieval
        before=retrieval.COLLECTION_HTTP_PER_DISPATCH
        with worker.remaining_budget(folder) as used:
            self.assertEqual(used,{'http':3,'decisions':2,'failures':1})
            self.assertEqual(retrieval.COLLECTION_HTTP_PER_DISPATCH,13)
            self.assertEqual(retrieval.COLLECTION_MAX_TURNS,10)
            self.assertEqual(retrieval.MODEL_FAILURES_PER_DISPATCH,3)
        self.assertEqual(retrieval.COLLECTION_HTTP_PER_DISPATCH,before)
        save(path.with_name('0003.json'),{'status':'reserved'})
        with self.assertRaisesRegex(RuntimeError,'provider review'):worker.attempts(folder)

    def recovery_ask(self,messages,key,*,tools,observer,model_route,**kwargs):
        fields=tools[0]['function']['parameters']['properties']
        state=json.loads(messages[1]['content'])['state']
        self.assertNotIn('research_map',state)
        self.assertEqual(model_route.model(),worker.POLICY['collection_model'])
        value={'evidence_ids':[state['evidence'][0]['evidence_id']],'rationale':'Observed baseline with future uncertainty.'}
        if 'probability_yes' in fields:value['probability_yes']=.41
        else:
            keys=fields['probabilities']['properties']
            value['probabilities']={k:1/len(keys) for k in keys}
        message={'tool_calls':[{'function':{'name':'record_recovery_forecast','arguments':json.dumps(value)}}]}
        record={'status':'reserved','request':{'model':model_route.model(),'messages':messages}}
        token=observer('reserve',record)
        record.update(status='received',response={'choices':[{'message':message}]})
        observer('complete',record,token)
        return message

    def failed_decision(self,code):
        def fail(original,heads,key,observer,**kw):
            record={'status':'reserved','endpoint':decision_http.ENDPOINT,
                'request':{'model':decision_http.MODEL,'state':original,'questions':heads}}
            token=observer('reserve',record)
            record.update(status='http_error',http_status=code)
            observer('complete',record,token)
            raise RuntimeError('Decision HTTP '+str(code))
        return fail

    def test_known_service_failure_falls_back_for_all_five_formats_once(self):
        from ForecastAgent.intelligence import scoring_recovery
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}), \
             patch.object(decision_http,'decide',self.failed_decision(503)), \
             patch.object(scoring_recovery,'ask_model',self.recovery_ask):
            report=worker.run(self.root/'worker',self.incoming,client=self.platform,
                adapter=worker.Adapter(acquisition=self.acquisition))
        self.assertEqual(report['state_distribution']['shadow_scored'],5)
        for p in (self.root/'worker/tasks').glob('*/candidate.json'):
            self.assertEqual(load(p)['selection'],'single_available_reasoning_route')
            self.assertEqual(len(list((p.parent/'graph-score/decision/http').glob('*.json'))),1)
            self.assertEqual(len(list((p.parent/'graph-score/reasoning-fallback/http').glob('*.json'))),1)
        self.assertEqual(self.platform.writes,[])
        ledger=load(self.root/'worker/campaign.json')
        ledger['tasks']['1'].update(stage='retry_wait',retry_at_utc=None)
        save(self.root/'worker/campaign.json',ledger)
        with patch.object(scoring_recovery,'ask_model',side_effect=AssertionError('Fallback repeated')):
            self.run_worker()
        self.assertEqual(self.collected.count('1'),1)

    def test_account_quota_and_unknown_scoring_failures_do_not_fallback(self):
        from ForecastAgent.intelligence import scoring_recovery
        for code in (400,401,403,429):
            with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}), \
                 patch.object(decision_http,'decide',self.failed_decision(code)), \
                 patch.object(scoring_recovery,'ask_model',side_effect=AssertionError('Unsafe fallback')):
                report=worker.run(self.root/str(code),self.incoming,client=self.platform,
                    adapter=worker.Adapter(acquisition=self.acquisition))
            self.assertEqual(report['state_distribution'],{'provider_blocked':5,'queued':2})
        self.assertEqual(self.platform.writes,[])

    def test_failed_reasoning_recovery_preserves_attempt_and_cannot_renew(self):
        from ForecastAgent.intelligence import scoring_recovery
        def fail(messages,key,observer,model_route,**kw):
            token=observer('reserve',{'status':'reserved','request':{'model':model_route.model(),'messages':messages}})
            observer('complete',{'status':'http_error','http_status':502},token)
            raise RuntimeError('Recovery HTTP 502')
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}), \
             patch.object(decision_http,'decide',self.failed_decision(503)),patch.object(scoring_recovery,'ask_model',fail):
            worker.run(self.root/'worker',self.incoming,client=self.platform,adapter=worker.Adapter(acquisition=self.acquisition))
        path=self.root/'worker/campaign.json';ledger=load(path)
        ledger['tasks']['1'].update(stage='retry_wait',retry_at_utc=None);save(path,ledger)
        with patch.object(scoring_recovery,'ask_model',side_effect=AssertionError('Quota renewed')):
            self.run_worker()
        task=self.root/'worker/tasks/1'
        self.assertEqual(len(list((task/'graph-score/reasoning-fallback/http').glob('*.json'))),1)
        self.assertEqual(load(path)['tasks']['1']['stage'],'provider_blocked')
        self.assertFalse((task/'candidate.json').exists())

    def test_recovery_transport_replay_checks_evidence_and_ignores_corrupt_result(self):
        from ForecastAgent.intelligence import scoring_recovery
        self.maps=False
        self.acquisition(Path(self._prepare_request()),self.root/'package','2026-10-11')
        package=load(self.root/'package/final-map-package.json')
        from ForecastAgent.intelligence.admission import prepare
        from ForecastAgent.research_loop.prospective_trial import prepared_pair
        view,_=prepare(package);_,pair,_=prepared_pair(package,original_view=view)
        root=self.root/'fallback'
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}):
            result=scoring_recovery.run(pair['original'],root,ask=self.recovery_ask)
        with patch.object(scoring_recovery,'ask_model',side_effect=AssertionError('Received transport repeated')):
            self.assertEqual(scoring_recovery.run(pair['original'],root),result)
        changed=copy.deepcopy(result);changed['payload']['probability_yes']=.8;save(root/'result.json',changed)
        with self.assertRaisesRegex(ValueError,'differs from its transport'):
            scoring_recovery.run(pair['original'],root)

    def _prepare_request(self):
        from ForecastAgent.competition.live import live_request
        path=self.root/'fixture-request.json'
        save(path,live_request(self.docs['101'],self.docs['101']['question']))
        return path

    def test_restore_preserves_receipts_and_no_duplicate_scores(self):
        self.run_worker();restored=self.root/'restored';shutil.copytree(self.root/'worker',restored)
        before=len(self.scored);result=self.run_worker(restored)
        self.assertEqual(len(self.scored)-before,2);self.assertEqual(result['state_distribution'],{'shadow_scored':7})
        self.assertEqual(self.platform.writes,[])

    def test_child_uses_integrated_collector_with_separate_lock_and_policy(self):
        def acquisition(request_path,directory,clock):
            def collect(request,folder,**options):
                self.assertTrue(options['research_map']);self.assertTrue(options['channel_tools'])
                self.assertEqual(options['clock_utc'],clock)
                self.acquisition(request_path,directory,clock)
                return {'view':{'pages':{'saved':{}}},'report':{'status':'complete'}}
            with patch.object(worker.pipeline,'collect',collect):
                worker.collect_child(request_path,directory,clock)
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch.object(decision_http,'decide',self.decide):
            result=worker.run(self.root/'worker',self.incoming,client=self.platform,
                adapter=worker.Adapter(acquisition=acquisition))
        self.assertEqual(result['state_distribution']['shadow_scored'],5)
        self.assertEqual(self.platform.writes,[])

    def test_development_service_handoff_preserves_original_native_failure(self):
        from ForecastAgent.intelligence import development_collection as dev
        from ForecastAgent.channels.contracts import FIELD,POLICY
        folder=self.root/'native';calls=[];original=[]
        from ForecastAgent.competition.live import live_request
        request=live_request(self.docs['101'],self.docs['101']['question']);request[FIELD]=POLICY
        def run(request,directory,**kwargs):
            calls.append(1)
            if len(calls)==1:
                frozen=dev.pipeline.identity(request,True)
                save(directory/'identity.json',frozen)
                root=directory/'collection'
                record=root/'model_calls/0001.json';save(record,{'status':'http_error','http_status':502})
                package=source_bundle();package['request']=frozen['request']
                package['model_attempts']=[{'status':'http_error','path':'model_calls/0001.json','sha256':worker.sha(record)}]
                package['result']={'status':'partial','termination_reason':'model_transport_failure',
                    'incomplete':True,'execution_report':{'owner':'program','interrupted':True}}
                save(root/'bundle.json',package);original.append(worker.sha(root/'bundle.json'))
                save(directory/'state.json',{'stage':'collection','identity_sha256':digest(frozen)})
                return {'state':'partial'}
            self.assertEqual(load(directory/'state.json')['stage'],'supplement')
            self.assertEqual(worker.sha(directory/'collection/bundle.json'),original[0])
            self.assertTrue(load(directory/'raw-handoff.json')['known_service_interruption'])
            package=load(directory/'collection/bundle.json');save(directory/'package.json',package)
            return {'state':'complete'}
        with patch.object(dev.pipeline,'run',run):result=dev.collect(request,folder)
        self.assertEqual(len(calls),2);self.assertEqual(result['status'],'complete')
        self.assertTrue(result['package']['result']['incomplete']);self.assertFalse(result['budget_reset'])
        self.assertEqual(worker.sha(folder/'collection/bundle.json'),original[0])

    def test_readonly_client_and_production_ledger_are_rejected(self):
        client=worker.ReadOnlyClient(self.platform)
        with self.assertRaises(ValueError):client.request('POST','forecasts',{})
        save(self.root/'worker/campaign.json',{'schema':'wrong-production-schema','tournament':'fall-futureeval-2026','tasks':{}})
        with self.assertRaises(ValueError):self.run_worker()
        self.assertEqual(self.collected,[])


if __name__=='__main__':unittest.main()
