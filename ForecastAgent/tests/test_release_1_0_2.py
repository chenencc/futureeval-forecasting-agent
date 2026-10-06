"""Release reliability contracts; fault injection is isolated from real providers."""
import copy
import os
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.competition import mercury, live, platform
from ForecastAgent.competition.queue import load, save, utc, digest
from ForecastAgent.releases import v1_0_2 as release, surfaces, stress
from ForecastAgent.releases.guard import execute
from ForecastAgent.tests.test_competition_mercury import bundle, response


class ReleaseTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def docs(self,n=7):
        return {str(100+i):{'id':100+i,'title':str(i),'user_permission':'forecaster',
                           'question':stress.question(i,stress.KINDS[(i-1)%5])} for i in range(1,n+1)}
    def args(self,docs):
        return stress.snapshot(self.root/'incoming',docs),stress.Platform(docs)
    def test_hundred_mixed_questions_with_frozen_analysis_and_exact_replay(self):
        report=stress.run(self.root/'load',100)
        self.assertEqual(report['valid_candidates'],100)
        self.assertEqual(report['duplicate_posts_on_replay'],0)
        self.assertEqual(report['type_distribution'],{k:20 for k in stress.KINDS})
    def test_group_and_conditional_children_use_exact_identity_and_rules(self):
        docs=self.docs(1);docs['101'].pop('question')
        docs['101']['group_of_questions']={'resolution_criteria':'Shared exact group rule',
            'fine_print':'INITIAL release only','questions':[stress.question(1,'numeric'),stress.question(2,'multiple_choice')]}
        docs['101']['group_of_questions']['questions'][0].pop('resolution_criteria')
        docs['102']={'id':102,'title':'Conditional event','user_permission':'forecaster','conditional':{
            'condition':{'title':'Premise event','resolution_criteria':'Official premise rule'},
            'condition_child':{'resolution_criteria':'Exact target event rule'},
            'question_yes':stress.question(3,'binary'),'question_no':stress.question(4,'binary')}}
        incoming,client=self.args(docs);seen=[]
        def collector(request,folder):seen.append(request);return stress.saved_collector(request,folder)
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',stress.decide):
            report=release.once(self.root/'worker',incoming,enabled=True,client=client,collector=collector,limit=5)
        self.assertEqual(report['state_distribution'],{'accepted':4})
        self.assertEqual({p['forecasts'][0]['question'] for p in client.writes},{1,2,3,4})
        for q in seen:
            if q['id'] in {'3','4'}:
                self.assertIn('conditional forecast, GIVEN',q['resolution_criteria']);self.assertIn('Official premise rule',q['resolution_criteria'])
        self.assertEqual(seen[0]['resolution_criteria'],'Shared exact group rule')
    def test_bad_conditional_is_accounted_without_blocking_other_posts(self):
        docs=self.docs(1)
        docs['102']={'id':102,'title':'Bad conditional','conditional':{'question_yes':stress.question(2,'binary'),
                         'question_no':stress.question(3,'binary')},'user_permission':'forecaster'}
        incoming=self.root/'incoming'
        for ident,post in docs.items():save(incoming/f'{ident}.json',{'post':post})
        save(incoming/'index.json',{'tournament':'fall-futureeval-2026','open_scan_complete':True,
            'retrieved_at_utc':utc().isoformat(),'questions':[{'question_id':1,'post_id':101,'open':True}]})
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',stress.decide):
            report=release.once(self.root/'worker',incoming,enabled=True,client=stress.Platform(docs),collector=stress.saved_collector,limit=5)
        self.assertEqual(report['state_distribution'],{'accepted':1,'blocked_integrity':2})
        self.assertTrue(load(self.root/'worker/coverage-inventory.json')['all_input_questions_recorded'])
    def test_provider_down_does_not_omit_or_block_later_cases(self):
        incoming,client=self.args(self.docs(7));calls=[]
        def infer(source,folder,ident):
            calls.append(ident)
            if ident=='1':raise RuntimeError('HTTP 503: both analysis providers unavailable')
            return release.analyze(source,folder,ident)
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',stress.decide):
            for _ in range(2):release.once(self.root/'worker',incoming,enabled=True,client=client,collector=stress.saved_collector,infer=infer,limit=5)
        state=load(self.root/'worker/campaign.json')
        self.assertEqual(len(state['tasks']),7);self.assertEqual(state['tasks']['1']['stage'],'retry_wait')
        self.assertEqual(len(client.writes),6);self.assertEqual(calls,list(map(str,range(1,8))))
        self.assertFalse((self.root/'worker/tasks/1/candidate.json').exists())
    def test_reread_failure_keeps_first_and_cache_uses_zero_new_attempts(self):
        source=self.root/'analysis-input.json';save(source,bundle(long=True));calls=[]
        def decide(state,registry,key,observer):
            token=observer('reserve',{'status':'reserved'});calls.append(1)
            if len(calls)==2:
                observer('complete',{'status':'http_error'},token);raise RuntimeError('HTTP 503')
            answer=response(registry,insufficient=True);observer('complete',{'status':'received','response':answer},token);return answer
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',decide):
            result=release.analyze(source,self.root,'7');again=release.analyze(source,self.root,'7')
        self.assertEqual(result,again);self.assertEqual(len(calls),2)
        self.assertEqual(result['selection'],'mercury_first_read');self.assertEqual(result['payload']['probability_yes'],.98)
    def test_actual_process_timeout_kills_descendants_and_next_task_runs(self):
        child_code='import time; time.sleep(30)'
        code="import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c',"+repr(child_code)+"]); print(p.pid,flush=True); time.sleep(30)"
        report=execute([sys.executable,'-u','-c',code],self.root/'hang.log',.8)
        self.assertEqual(report['status'],'timed_out');self.assertLess(report['elapsed_seconds'],15)
        child=int((self.root/'hang.log').read_text().strip());time.sleep(.1)
        if os.name=='nt':
            import subprocess
            status=subprocess.run(['tasklist','/FI',f'PID eq {child}','/FO','CSV','/NH'],capture_output=True,text=True)
            self.assertNotIn(f'"{child}"',status.stdout)
        else:
            proc=Path(f'/proc/{child}/stat')
            self.assertTrue(not proc.exists() or proc.read_text().split()[2]=='Z')
        self.assertEqual(execute([sys.executable,'-c','print(1)'],self.root/'next.log',5)['status'],'completed')
    def test_supervisor_timeout_then_later_case_and_preserved_lifetime_cap(self):
        incoming,client=self.args(self.docs(2));worker=self.root/'worker';attempts=[]
        def process(command,log,timeout,cwd):
            state=load(worker/'campaign.json');task=release.due(state)[0];attempts.append(task['id'])
            return {'status':'timed_out','returncode':-1}
        report=release.supervise(worker,incoming,submit=True,process_runner=process,task_seconds=1,batch_seconds=5)
        self.assertEqual(attempts,['1','2']);self.assertEqual(report['state_distribution'],{'retry_wait':2})
        for _ in range(2):
            state=load(worker/'campaign.json')
            for t in state['tasks'].values():t['retry_at_utc']=None
            save(worker/'campaign.json',state)
            report=release.supervise(worker,incoming,submit=True,process_runner=process,task_seconds=1,batch_seconds=5)
        self.assertEqual(report['state_distribution'],{'provider_blocked':2})
        self.assertEqual(report['attention_ids'],['1','2']);self.assertEqual(len(attempts),6)
    def test_raw_handoff_preserves_original_journals_and_rejects_transport_partial(self):
        native=self.root/'collection';b=bundle();save(native/'http/attempt.json',{'status':'received','used':8})
        b['model_attempts']=[{'path':'http/attempt.json','sha256':release.file_hash(native/'http/attempt.json'),'status':'received'}]
        b['result']={'status':'partial','incomplete':True,'termination_reason':'context_projection_failure',
                     'execution_report':{'owner':'program','interrupted':True}}
        save(native/'bundle.json',b);identity={'request':b['request']};save(self.root/'identity.json',identity)
        save(self.root/'state.json',{'stage':'collection','identity_sha256':release.pipeline.digest(identity)})
        before=release.native_manifest(native);self.assertTrue(release.handoff(self.root))
        self.assertEqual(before,release.native_manifest(native));self.assertFalse(load(self.root/'raw-handoff.json')['budget_reset'])
        b['result']['termination_reason']='provider_transport_failure';self.assertFalse(release.raw_handoff_eligible(b))
    def test_corrupted_candidate_blocks_replay_without_reanalysis_or_post(self):
        incoming,client=self.args(self.docs(1));worker=self.root/'worker'
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',stress.decide):
            release.once(worker,incoming,enabled=True,client=client,collector=stress.saved_collector)
        state=load(worker/'campaign.json');state['tasks']['1']['stage']='analyzing';save(worker/'campaign.json',state)
        candidate=worker/'tasks/1/candidate.json';data=load(candidate);data['payload']['probability_yes']=.03;save(candidate,data)
        report=release.once(worker,incoming,enabled=True,client=client,collector=lambda *a:self.fail('Duplicate collection'))
        self.assertEqual(report['state_distribution'],{'blocked_integrity':1});self.assertEqual(len(client.writes),1)
    def test_lost_response_reconciles_single_post_for_each_distribution(self):
        for kind in stress.KINDS:
            docs={'101':{'id':101,'title':'Event','user_permission':'forecaster','question':stress.question(1,kind)}}
            client=stress.Platform(docs);client.lose_response=True
            request=live.live_request(docs['101'],docs['101']['question'])
            if kind=='binary':raw={'probability_yes':.63}
            elif kind=='multiple_choice':raw={'probability_yes_per_category':{'A':.2,'B':.3,'C':.5}}
            else:
                n=request['inbound_outcome_count'];raw={'continuous_cdf':[i/n for i in range(n+1)]}
            candidate=payload(request,raw);release.validate_payload(request,candidate);folder=self.root/kind
            with self.assertRaises(TimeoutError):platform.deliver(client,{'id':'1','post_id':'101'},candidate,'Automatic',folder,enabled=True)
            docs['101']['question']['status']='closed'
            result=platform.deliver(client,{'id':'1','post_id':'101'},candidate,'Automatic',folder,enabled=True)
            self.assertEqual(result['status'],'accepted');self.assertEqual(len(client.writes),1)
    def test_infeasible_or_missing_metadata_rejected_before_http(self):
        with patch('ForecastAgent.providers.decisions.decide') as decide:
            bad=bundle('multiple_choice');bad['request']['options']=[str(i) for i in range(51)]
            with self.assertRaises(ValueError):mercury.run(bad,self.root/'many')
            bad=bundle('date');bad['request'].pop('scaling')
            with self.assertRaises(ValueError):mercury.run(bad,self.root/'missing')
            decide.assert_not_called()
    def test_invalid_probability_rejected(self):
        for value in [float('nan'),float('inf'),True,-.1,1.1,None,'0.5']:
            with self.assertRaises((ValueError,TypeError)):release.validate_payload(bundle()['request'],{'question':7,'probability_yes':value})

    def test_bad_type_scale_and_deadline_are_separate_preflight_blocks(self):
        docs=self.docs(4)
        docs['102']['question']['options']=[str(i) for i in range(51)]
        docs['103']['question'].pop('scaling')
        docs['104']['question']['scheduled_close_time']='not-a-date'
        incoming,client=self.args(docs)
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',stress.decide):
            report=release.once(self.root/'worker',incoming,enabled=True,client=client,collector=stress.saved_collector,limit=5)
        self.assertEqual(report['state_distribution'],{'accepted':1,'blocked_integrity':3})
        self.assertEqual(len(client.writes),1)

    def test_malformed_reread_is_retained_via_exact_failed_receipt_without_new_http(self):
        from ForecastAgent.providers import decisions
        source=self.root/'analysis-input.json';save(source,bundle(long=True));calls=[]
        def decide(state,registry,key,observer):
            record={'endpoint':decisions.ENDPOINT,'request':{'model':decisions.MODEL,'state':state,'questions':registry},'status':'reserved'}
            token=observer('reserve',record);calls.append(1)
            if len(calls)==2:
                record.update(status='invalid_or_transport_error',error='Invalid JSON response');observer('complete',record,token)
                raise ValueError('Invalid JSON response')
            answer=response(registry,insufficient=True);record.update(status='received',response=answer);observer('complete',record,token);return answer
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',decide):
            result=release.analyze(source,self.root,'7')
        self.assertEqual(len(calls),2);self.assertEqual(result['selection'],'mercury_first_read')
        self.assertTrue(load(self.root/'provider-recovery.json')['replay_existing_journals'])
        self.assertEqual(result['payload']['probability_yes'],.98)

    def test_service_interruption_exports_readable_raw_but_not_quota_or_unknown_receipts(self):
        native=self.root/'collection';b=bundle();record={'status':'missing_choices','response':{'error':{'code':503}}}
        save(native/'http/attempt.json',record)
        b['model_attempts']=[{'path':'http/attempt.json','sha256':release.file_hash(native/'http/attempt.json'),'status':'missing_choices'}]
        b['result']={'status':'partial','incomplete':True,'termination_reason':'model_transport_failure',
                     'execution_report':{'owner':'program','interrupted':True}}
        save(native/'bundle.json',b);identity={'request':b['request']};save(self.root/'identity.json',identity)
        save(self.root/'state.json',{'stage':'collection','identity_sha256':release.pipeline.digest(identity)})
        before=release.native_manifest(native);self.assertTrue(release.handoff(self.root))
        self.assertEqual(before,release.native_manifest(native))
        self.assertTrue(load(self.root/'raw-handoff.json')['known_service_interruption'])
        for code in [400,401,402,403,429,None]:
            record['response']['error']['code']=code;save(native/'http/attempt.json',record)
            b['model_attempts'][0]['sha256']=release.file_hash(native/'http/attempt.json')
            self.assertFalse(release.service_gap_eligible(b,native))


if __name__=='__main__':unittest.main()
