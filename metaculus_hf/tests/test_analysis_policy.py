import io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
from metaculus_agent.analysis_policy import decision_route,classify,AnalysisProviderError,DECISION_MODEL
from metaculus_agent.analysis_transport import installed
from metaculus_agent.runner import Runner
from metaculus_agent.debug_sequence import run_case,DebugStore,KEY
from test_debug_sequence import seed,Native,capture,analyze

class PolicyTests(unittest.TestCase):
    def setUp(self):
        p=patch.dict(os.environ,dict(os.environ));p.start();self.addCleanup(p.stop)
    def catalog(self,price='0',decisions=True,endpoints=1):
        def get(req,**kwargs):
            data={'data':dict(id=DECISION_MODEL,architecture={'output_modalities':['decisions'] if decisions else ['text']},endpoints=[dict(context_length=16384,pricing=dict(prompt=price,completion='0'))]*endpoints)}
            return io.StringIO(json.dumps(data))
        return get
    def test_free_typed_decision_endpoint_is_verified_without_inference(self):
        with patch.dict(os.environ,{'METACULUS_DECISION_MODEL':DECISION_MODEL}):
            r=decision_route(opener=self.catalog());self.assertEqual(r['decision_model'],DECISION_MODEL);self.assertTrue(r['pricing_verified_zero']);self.assertFalse(r['implicit_model_fallback']);self.assertLess(r['second_byte_limit'],r['context_length'])
    def test_nonfree_unknown_or_wrong_protocol_routes_fail_closed(self):
        for price,typed in [('1',True),('invalid',True),('0',False)]:
            with self.assertRaises(AnalysisProviderError):decision_route(opener=self.catalog(price,typed))
        with self.assertRaises(AnalysisProviderError):decision_route(opener=self.catalog(endpoints=0))
        with patch.dict(os.environ,{'METACULUS_DECISION_MODEL':'unapproved/paid-model'}):
            with self.assertRaises(AnalysisProviderError):decision_route(opener=lambda *a,**k:self.fail('HTTP for unapproved model'))
    def test_auth_quota_policy_and_provider_are_distinct(self):
        self.assertEqual(classify(401).category,'configuration');self.assertEqual(classify(429).category,'quota')
        self.assertEqual(classify(404,'{"error":{"message":"No endpoints for data policy"}}').code,'ANALYSIS_ROUTING_POLICY_BLOCKED')
        self.assertEqual(classify(404,'{"error":{"message":"No endpoints found"}}').code,'ANALYSIS_ROUTE_UNAVAILABLE')
        self.assertEqual(classify(503).code,'ANALYSIS_PROVIDER_UNAVAILABLE')
    def test_auth_error_does_not_try_another_key_or_model(self):
        calls=[]
        def fail(req,*a,**k):calls.append(req);raise HTTPError(req.full_url,401,'invalid',{},io.BytesIO(b'{}'))
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture-primary','OPENROUTER_API_KEY2':'fixture-backup','FORECAST_V2_SHARED_BUDGET_ROOT':''}):
            with installed(tmp,'auth',lambda:None,opener=fail):
                from ForecastAgent.providers import decisions
                with self.assertRaises(AnalysisProviderError):decisions.urlopen(Request(decisions.ENDPOINT,data=b'{"model":"inception/mercury-decide:free"}',headers={'Authorization':'Bearer fixture-primary'}))
        self.assertEqual(len(calls),1)
    def test_provider_failure_defers_and_skips_without_spending_invalid_attempts(self):
        b=seed();session='f'*32
        def unavailable(*a):raise AnalysisProviderError('ANALYSIS_ROUTE_UNAVAILABLE',retry_after=300,status=404)
        with tempfile.TemporaryDirectory() as tmp:
            report=run_case(b,Path(tmp)/'one',session,1,Native(),collector=capture,analyzer=unavailable)
            self.assertEqual(report['code'],'ANALYSIS_ROUTE_UNAVAILABLE')
            store=DebugStore(b,session);state=json.loads(store.read(KEY));task=state['tasks']['46432']
            self.assertEqual(task['status'],'analyzing');self.assertEqual(task['attempts']['analysis'],0);self.assertEqual(task['analysis_provider_failures'],1)
            from metaculus_agent.storage import RuntimeCheckpoint
            cp=RuntimeCheckpoint(store,Path(tmp)/'later',key=KEY);runner=Runner(cp,Native(),writer=state['writer'],analyzer=lambda *a:self.fail('retry during cooldown'))
            self.assertEqual(runner.run([])['processed'],0)
    def test_saved_first_context_resumes_without_collection_and_retains_failure(self):
        b=seed();session='1'*32
        def fail(*a):raise RuntimeError('SAVED_ANALYSIS_FAILURE')
        with tempfile.TemporaryDirectory() as tmp:
            run_case(b,Path(tmp)/'first',session,1,Native(),collector=capture,analyzer=fail)
            store=DebugStore(b,session);before=json.loads(store.read(KEY));snapshot=before['tasks']['46432']['snapshot_id'];context_hash=before['tasks']['46432']['context_bytes_sha256']
            result=run_case(b,Path(tmp)/'resume',session,1,Native(),collector=lambda *a:self.fail('recollection'),analyzer=analyze,resume_analysis=True)
            self.assertEqual(result['status'],'debug_case_passed',result);self.assertTrue(result['resumed_analysis_only'])
            self.assertEqual(result['snapshot_id'],snapshot);self.assertEqual(result['context_sha256'],context_hash)
            self.assertEqual(result['new_search_requests'],0);self.assertEqual(result['new_fetch_requests'],0)
            self.assertTrue(store.exists('metaculus/case-1/previous-failure.json'))
if __name__=='__main__':unittest.main()
