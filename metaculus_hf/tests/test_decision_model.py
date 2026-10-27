import copy,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from metaculus_agent import decision_model as model,decision_analysis as analysis
from metaculus_agent.analysis_policy import DECISION_MODEL,AnalysisProviderError
from metaculus_agent.products import bundle,context
from metaculus_agent.platform_adapter import live_request,rule_identity
from pipeline_v2.contracts import now
from test_runner import post,sealed

def response(questions,insufficient=False):
    answers={}
    for key,q in questions.items():
        if q['type']=='noul':answers[key]=dict(type='noul',noul=.7 if key=='event_yes' else 0.)
        elif q['type']=='score':
            n=len(q['criteria']);pick=0 if insufficient else n-1
            answers[key]=dict(type='score',score=pick,confidence=1.,probabilities={str(i):float(i==pick) for i in range(n)})
        else:
            keys=list(q['criteria']);pick='insufficient' if insufficient and 'insufficient' in keys else 'supported' if 'supported' in keys else keys[0]
            answers[key]=dict(type='choice',choice=pick,confidence=1.,probabilities={k:float(k==pick) for k in keys})
    return dict(model=DECISION_MODEL,answers=answers)

class DecisionTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'OPENROUTER_API_KEY':'fixture-only-key'});env.start();self.addCleanup(env.stop)
    def source(self,root,kind='binary',long=False):
        p=post(kind);task=dict(id=str(p['question']['id']),post_id=str(p['id']),run_id='typedtest',started_at=now(),post=p,request=live_request(p,p['question']),rule_identity=rule_identity(p,p['question']))
        package=sealed(task,root/'repo');source=bundle(context(task,package,task['snapshot_id']))
        if long:
            import hashlib
            page=next(iter(source['pages'].values()));page['content']='Synthetic Example Alpha original observation: 42 USD in 2030. '+('independent saved evidence about Example Alpha and official measurements. '*350)
            page['content_sha256']=hashlib.sha256(page['content'].encode()).hexdigest();page['capture_metadata']['text_sha256']=page['content_sha256']
        return source
    def route(self):return dict(first_byte_limit=9830,second_byte_limit=12943)
    def test_typed_validation_rejects_wrong_identity_nan_options_and_ordinal_score(self):
        questions={'a':dict(type='noul'),'b':dict(type='choice',criteria={'yes':'Yes','no':'No'}),'c':dict(type='score',criteria=['low','high'])}
        good=response(questions);self.assertEqual(model.validate(good,questions),good)
        served=copy.deepcopy(good);served['model']='baseten/mammoth-1-20261021:free';self.assertEqual(model.validate(served,questions),served)
        for edit in [lambda r:r.update(model='inception/mercury-decide:free'),lambda r:r['answers']['a'].update(noul=True),lambda r:r['answers']['a'].update(noul=float('nan')),lambda r:r['answers']['b']['probabilities'].update(other=.5),lambda r:r['answers']['c'].update(score=.2)]:
            bad=copy.deepcopy(good);edit(bad)
            with self.assertRaises(model.InvalidDecision):model.validate(bad,questions)
    def test_actual_decisions_wire_and_observer_not_chat(self):
        questions={'event_yes':dict(type='noul')};events=[]
        class Reply(io.BytesIO):status=200
        def opener(req,**kw):
            self.assertEqual(events,['reserve']);self.assertEqual(req.full_url,model.ENDPOINT)
            body=json.loads(req.data);self.assertEqual(body['model'],DECISION_MODEL);self.assertIn('state',body);self.assertIn('questions',body);self.assertNotIn('messages',body)
            return Reply(json.dumps(response(questions)).encode())
        def observer(phase,record,token=None):events.append(phase);return 'fixture'
        with patch.object(model,'urlopen',opener):model.decide({'fixture':True},questions,'fixture-key',observer)
        self.assertEqual(events,['reserve','complete'])
    def test_binary_categorical_numeric_and_date_payloads_share_native_validation(self):
        for kind in ['binary','multiple_choice','numeric','date']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=self.source(root,kind)
                with patch.object(model,'decide',side_effect=lambda state,q,*a:response(q)):
                    result=analysis.run(source,root/'analysis',self.route(),lambda:None)
                self.assertEqual(result['payload']['question'],100);self.assertNotIn('Mercury',result['comment'])
                if kind=='binary':self.assertEqual(result['payload']['probability_yes'],.7)
                elif kind=='multiple_choice':self.assertIn('probability_yes_per_category',result['payload'])
                else:self.assertIn('continuous_cdf',result['payload'])
    def test_conditional_reread_preserves_first_spans_and_cached_responses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,long=True);states=[]
            def answer(state,q,*args):states.append(copy.deepcopy(state));return response(q,insufficient=True)
            with patch.object(model,'decide',side_effect=answer):
                result=analysis.run(source,root/'analysis',self.route(),lambda:None)
                again=analysis.run(source,root/'analysis',self.route(),lambda:None)
            self.assertEqual(len(states),2);self.assertEqual(result['selection'],'mammoth_conditional_reread')
            first={e['evidence_id'] for e in states[0]['evidence']};second={e['evidence_id'] for e in states[1]['evidence']};self.assertLess(first,second)
            self.assertEqual(result['payload']['probability_yes'],.7);self.assertEqual(result,again)
    def test_failed_second_read_retains_only_valid_first_and_records_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root,long=True);calls=[]
            def answer(state,q,*args):
                calls.append(state)
                if len(calls)==2:raise AnalysisProviderError('ANALYSIS_PROVIDER_UNAVAILABLE',status=503)
                return response(q,insufficient=True)
            with patch.object(model,'decide',side_effect=answer):result=analysis.run(source,root/'analysis',self.route(),lambda:None)
            report=json.loads((root/'analysis/result.json').read_bytes())
            self.assertEqual(result['payload']['probability_yes'],.7);self.assertEqual(report['status'],'completed_with_reread_gap');self.assertEqual(report['second_error'],'ANALYSIS_PROVIDER_UNAVAILABLE')
    def test_too_large_rules_fail_before_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=self.source(root);source['request']['resolution_criteria']='x'*30000
            with patch.object(model,'decide',side_effect=AssertionError('HTTP for oversized input')):
                with self.assertRaises(ValueError):analysis.run(source,root/'analysis',self.route(),lambda:None)
if __name__=='__main__':unittest.main()
