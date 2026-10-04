"""Material contracts reject wrong roles without suppressing negative evidence."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import load
from ForecastAgent.experiments.material_contract_round import MANIFEST,replay,paired,MODEL
from ForecastAgent.supplement.material_contract import evaluate

class MaterialContractTests(unittest.TestCase):
    def test_frozen_cases_and_no_new_false_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=replay(tmp)
            self.assertEqual(result['new_correct'],13)
            self.assertEqual(result['new_false_accepts'],0)
            self.assertEqual(result['new_false_rejects'],0)
            self.assertEqual(result['model_calls'],0)
    def test_representation_repair_preserves_original_spans_and_rejects_paraphrase(self):
        from ForecastAgent.supplement.quote_alignment import bind
        source={'text':'Published 18 May 2026\n\nShare\n\nThe selected squad includes Player X.', 'start':100}
        aligned=bind(source,'Published 18 May 2026\\n\\nThe selected squad includes Player X.')
        self.assertTrue(aligned['bound']);self.assertTrue(aligned['discontinuous'])
        for p in aligned['spans']:
            self.assertEqual(p['text'],source['text'][p['start']:p['end']])
            self.assertEqual(p['capture_start'],100+p['start'])
        self.assertFalse(bind(source,'The selected squad excludes Player X.')['bound'])
        self.assertFalse(bind({'text':'Same statement twice. Same statement twice.'},'Same statement twice.')['bound'])

    def test_saved_method_update_cannot_satisfy_score_snapshot(self):
        from ForecastAgent.supplement.material_contract import evaluate
        m=load(MANIFEST.with_name('MATERIAL_CONTRACT_REGRESSION_V2.json'))
        c=next(c for c in m['cases'] if c['id']=='gpqa_method_is_not_snapshot')
        observed={**c['observation'],'document_role':'historical_snapshot',
                  'fit_axes':{'entity':True,'material_type':True,'metric':False,'period':True}}
        self.assertEqual(evaluate(c['contract'],c['source'],observed)['status'],'mismatched')

    def test_polarity_never_changes_fit(self):
        c=load(MANIFEST)['cases'][0]
        for relation in ('supports_event','counterevidence','neutral','unknown'):
            observed={**c['observation'],'evidence_relation':relation}
            self.assertEqual(evaluate(c['contract'],c['source'],observed)['status'],'matched')
    def test_domain_suffix_spoof_and_hallucinated_quote(self):
        c=load(MANIFEST)['cases'][8]
        r=evaluate(c['contract'],{**c['source'],'url':'https://fifa.com.evil.example'},c['observation'])
        self.assertEqual(r['status'],'mismatched')
        self.assertEqual(evaluate(c['contract'],c['source'],{**c['observation'],'quote':'invented quotation'})['status'],'uncertain')
    def test_supporting_rows_do_not_close_historical_need(self):
        c=load(MANIFEST)['cases'][2];r=evaluate(c['contract'],c['source'],c['observation'])
        self.assertEqual(r['status'],'matched')
        self.assertFalse(r['eligible_for_need_closure'])
        contract={**c['contract'],'required_axes':['entity','metric','period']}
        self.assertEqual(evaluate(contract,c['source'],c['observation'])['status'],'mismatched')
    def test_blinded_inputs_identical_across_arms_and_no_silent_restart(self):
        inputs=[]
        def model(messages,key,**kw):
            p=json.loads(messages[-1]['content']);inputs.append(p)
            self.assertNotIn('expected',p);self.assertNotIn('observation',p);self.assertNotIn('id',p)
            token=kw['observer']('reserve',{'status':'reserved','request':{'model':MODEL}})
            content=json.dumps({'document_role':'unknown','fit_axes':{a:False for a in ('entity','metric','period','material_type')},
                'evidence_relation':'unknown','quote':p['source']['text'][:50],'reason':'No supported fit.'})
            kw['observer']('complete',{'status':'received','request':{'model':MODEL},'response':{'choices':[{'finish_reason':'stop'}],'usage':{'total_tokens':10}}},token)
            return {'content':content}
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'FORECAST_MODEL':MODEL,'FORECAST_MODEL_FALLBACK_SUPER':'0'}),patch('ForecastAgent.providers.model.ask_model',side_effect=model):
            out=Path(tmp)/'paired';r=paired(out,key='dummy')
            self.assertEqual(r['actual_http_attempts'],20)
            for i in range(0,20,2):self.assertEqual(inputs[i],inputs[i+1])
            with self.assertRaisesRegex(ValueError,'no_implicit_budget_reset'):paired(out,key='dummy')
    def test_provider_failure_stops_remaining_calls(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'FORECAST_MODEL':MODEL,'FORECAST_MODEL_FALLBACK_SUPER':'0'}),patch('ForecastAgent.providers.model.ask_model',side_effect=RuntimeError('quota unavailable')) as model:
            r=paired(Path(tmp)/'out',key='dummy');self.assertEqual(model.call_count,1)
            self.assertEqual(r['results'][0]['status'],'failed')
