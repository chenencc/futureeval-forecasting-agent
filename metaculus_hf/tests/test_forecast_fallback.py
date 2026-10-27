import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from metaculus_agent import fallback,decision_model,decision_analysis
from metaculus_agent.analyze import analyze
from metaculus_agent.products import context,verify_context
from metaculus_agent.platform_adapter import live_request,rule_identity
from metaculus_agent.analysis_policy import AnalysisProviderError
from metaculus_agent.storage import QuestionBatch,RuntimeCheckpoint,dumps,Conflict
from metaculus_agent.runner import Runner,initial_state
from ForecastAgent.analysis.distributions import validate_cdf
from pipeline_v2.contracts import digest,now
from test_runner import post,sealed,Client,MemoryStore
from test_decision_model import response

ROUTE=dict(first_byte_limit=9830,second_byte_limit=12943,collection_model='nvidia/nemotron-3.5-super-vl:free')

class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        env=patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture-key','FORECAST_V2_SHARED_BUDGET_ROOT':str(self.root/'budgets')});env.start();self.addCleanup(env.stop)
    def value(self,kind='binary',excluded=False):
        p=post(kind);q=p['question'];task=dict(id=str(q['id']),post_id=str(p['id']),post=p,request=live_request(p,q),rule_identity=rule_identity(p,q),run_id='fallbackfixture',started_at=now())
        binding=dict(inclusion='excluded',source_role='unresolved',content_kind='market_probability',forecast_relevance='none',coverage_note='MARKET_PRICE_REFERENCE') if excluded else None
        package=sealed(task,self.root/('native-'+kind+str(excluded)),binding=binding)
        return context(task,package,task['snapshot_id'])
    def test_deterministic_native_payloads_cover_all_supported_kinds(self):
        for kind in ['binary','multiple_choice','numeric','date']:
            q=self.value(kind)['request'];r=fallback.candidate(q,RuntimeError('PROVIDER_DOWN'))
            self.assertTrue(r['automatic']);self.assertEqual(r['forecast_origin'],'operational_default');self.assertFalse(r['fallback']['model_generated'])
            if kind=='binary':self.assertEqual(r['payload']['probability_yes'],.5)
            elif kind=='multiple_choice':self.assertAlmostEqual(sum(r['payload']['probability_yes_per_category'].values()),1)
            else:validate_cdf(r['payload']['continuous_cdf'],q,clipped=True)
    def test_open_tails_and_nonuniform_platform_grid_are_preserved(self):
        q=self.value('numeric')['request'];q.update(open_lower_bound=True,open_upper_bound=True)
        q['scaling']['continuous_range']=[i*i/400 for i in range(201)]
        result=fallback.neutral(q);cdf=result['continuous_cdf'];validate_cdf(cdf,q,clipped=True)
        self.assertAlmostEqual(cdf[0],.02);self.assertAlmostEqual(cdf[-1],.98);self.assertAlmostEqual(cdf[100],.5)
        discrete=copy.deepcopy(q);discrete['question_type']='discrete';discrete['scaling'].pop('continuous_range')
        validate_cdf(fallback.neutral(discrete)['continuous_cdf'],discrete,clipped=True)
    def test_no_body_uses_one_question_prior_call_without_excluded_text(self):
        value=self.value(excluded=True);states=[]
        def reply(state,questions,*args):states.append(copy.deepcopy(state));return response(questions)
        with patch('metaculus_agent.analyze.decision_route',return_value=ROUTE),patch.object(decision_model,'decide',side_effect=reply):
            result=analyze(value,self.root/'analysis',lambda:None)
        self.assertEqual(len(states),1);self.assertEqual(states[0]['evidence'],[]);self.assertEqual(states[0]['sources'],[])
        self.assertIn('question-prior-only',states[0]['instruction']);self.assertNotIn('Synthetic Example Alpha official report',json.dumps(states))
        self.assertEqual(result['forecast_origin'],'question_prior');self.assertEqual(result['candidate']['payload']['probability_yes'],.7)
        self.assertFalse(result['fallback']['evidence_based']);self.assertIn('not an evidence-backed',result['candidate']['comment'])
    def test_catalog_outage_uses_default_without_inference(self):
        value=self.value()
        with patch('metaculus_agent.analyze.decision_route',side_effect=AnalysisProviderError('ANALYSIS_CATALOG_UNAVAILABLE')),patch.object(decision_model,'decide',side_effect=AssertionError('unexpected inference')):
            result=analyze(value,self.root/'analysis',lambda:None)
        self.assertEqual(result['candidate']['payload']['probability_yes'],.5);self.assertFalse(result['decision_model_attempted'])
        self.assertEqual(result['fallback']['trigger_code'],'ANALYSIS_CATALOG_UNAVAILABLE');self.assertTrue((self.root/'analysis/forecast-fallback.json').exists())
    def test_auth_failure_keeps_error_and_operator_flag_but_has_a_forecast(self):
        value=self.value()
        with patch('metaculus_agent.analyze.decision_route',return_value=ROUTE),patch.object(decision_analysis,'run',side_effect=AnalysisProviderError('ANALYSIS_CREDENTIAL_INVALID',category='configuration',status=401)):
            result=analyze(value,self.root/'analysis',lambda:None)
        self.assertTrue(result['fallback']['operator_action_required']);self.assertEqual(result['fallback']['http_status'],401)
        self.assertEqual(result['candidate']['payload']['probability_yes'],.5)
    def test_invalid_model_response_has_default_not_an_unvalidated_probability(self):
        value=self.value()
        with patch('metaculus_agent.analyze.decision_route',return_value=ROUTE),patch.object(decision_analysis,'run',side_effect=decision_model.InvalidDecision('bad shape')):
            result=analyze(value,self.root/'analysis',lambda:None)
        self.assertEqual(result['forecast_origin'],'operational_default');self.assertEqual(result['candidate']['payload']['probability_yes'],.5)
    def test_corrupt_body_or_question_identity_never_falls_back(self):
        for mode in ['body','identity']:
            value=self.value(kind='binary',excluded=mode=='identity')
            if mode=='body':value['intelligence']['evidence']['texts'][next(iter(value['intelligence']['evidence']['texts']))]='tampered'
            else:value['request']['id']='999'
            with patch('metaculus_agent.analyze.decision_route',side_effect=AssertionError('HTTP after corrupt context')):
                with self.assertRaises(ValueError):analyze(value,self.root/mode,lambda:None)
    def test_integrity_error_inside_model_stage_is_not_converted_to_default(self):
        value=self.value()
        with patch('metaculus_agent.analyze.decision_route',return_value=ROUTE),patch.object(decision_analysis,'run',side_effect=ValueError('FROZEN_DECISION_ANALYSIS_CHANGED')):
            with self.assertRaisesRegex(ValueError,'FROZEN_DECISION'):analyze(value,self.root/'analysis',lambda:None)
    def test_collection_outage_still_scores_once_without_faking_capture_success(self):
        backend=MemoryStore();state=initial_state('fixture');state['migration_complete']=True;backend.publish({'metaculus/_control/checkpoint.json':dumps(state)})
        store=QuestionBatch(backend,self.root/'stage');cp=RuntimeCheckpoint(store,self.root/'writer')
        def fail(task,p,root,checkpoint):
            path=root/'capture/failure.json';path.parent.mkdir(parents=True);path.write_text('{"code":"HTTP_503"}');checkpoint();raise RuntimeError('HTTP_503')
        with patch('metaculus_agent.analyze.decision_route',side_effect=AnalysisProviderError('ANALYSIS_ROUTE_UNAVAILABLE')):
            runner=Runner(cp,Client(post()),writer='fixture',live=True,collector=fail,analyzer=analyze)
            result=runner.run([post()])
        self.assertEqual(result['failures'],0);self.assertEqual(runner.client.posts,1);self.assertEqual(store.successful_commits,1)
        t=runner.state['tasks']['100'];self.assertEqual(t['status'],'accepted');self.assertEqual(t['forecast_origin'],'operational_default')
        self.assertFalse(t['collection_failure']['capture_succeeded']);self.assertFalse((runner.root/'tasks/100').exists())
        archive=json.loads(backend.read(t['runtime_archive_key']));self.assertIn('tasks/100/capture/failure.json',archive['runtime'])
    def test_collection_integrity_or_persistence_failure_cannot_trigger_prior(self):
        for exc in [ValueError('BODY_HASH_MISMATCH'),Conflict('HF_WRITER_CHANGED')]:
            with self.assertRaises(type(exc)):fallback.capture_package({'id':'100'},post(),self.root,exc)
    def test_empty_capture_snapshot_is_hash_bound_and_explicitly_failed(self):
        p=post();task=dict(id='100',post_id='1100',post=p,run_id='fallbackfixture',started_at=now(),rule_identity=rule_identity(p,p['question']),request=live_request(p,p['question']))
        package=fallback.capture_package(task,p,self.root/'capturefailed',RuntimeError('BROWSER_WARMUP_TIMEOUT'))
        value=context(task,package,task['snapshot_id']);snap=verify_context(value)
        self.assertEqual(snap['materials'],[]);self.assertEqual(snap['context']['capture_status'],'failed')
        self.assertFalse(value['collection_failure']['capture_succeeded']);self.assertFalse(task['execution_profile']['parameters']['orderbook'])
    def test_default_probe_on_saved_input_never_reserves_a_provider_call(self):
        from metaculus_agent.debug_batch import default_probe
        value=self.value('multiple_choice',excluded=True);before=digest(value)
        proof=default_probe(value,self.root/'probe',lambda:None)
        self.assertEqual(proof['status'],'operational_default_probe_passed');self.assertEqual(proof['provider_calls'],0)
        self.assertEqual(digest(value),before);self.assertTrue(proof['payload_valid'])

if __name__=='__main__':unittest.main()
