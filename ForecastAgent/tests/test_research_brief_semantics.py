"""Unit provenance, fallible-review isolation and unchanged output mapping."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import save, load
from ForecastAgent.research_loop import brief_semantics as sem, forecast_brief_refs as refs
from ForecastAgent.research_loop import forecast_brief, semantics_trial
from ForecastAgent.analysis.distributions import payload
from ForecastAgent.tests.test_research_forecast_brief_refs import common, proposal
from ForecastAgent.tests.test_competition_mercury import response


def opinion(state,brief,verdict='literal_or_derived'):
    _,heads=sem.review_request(state,brief);answer=response(heads)
    for key,head in heads.items():
        choice=verdict if key.endswith('_support') else 'historical_baseline'
        answer['answers'][key].update(choice=choice,confidence=.9999,
            probabilities={k:float(k==choice) for k in head['criteria']})
    return answer


class BriefSemanticsTests(unittest.TestCase):
    def test_high_confidence_cannot_certify_an_unbound_physical_unit(self):
        state=common();entry=state['evidence'][0]
        entry['text']='Object: 7.9 m (25.9 ft).\n[MHHW] = 1.624\nBench marks in METERS: 10.968\n'
        entry['end']=entry['start']+len(entry['text'])
        rid=refs.catalog(state)['references'][0]['reference_id']
        brief={'facts':[{'id':'datum','role':'baseline','evidence_ids':[rid],
            'interpretation':'MHHW is 1.624 ft.','limitation':'This is an average.'}],
            'change_factors':[],'forecast_frame':'The threshold is far above 1.624 ft.','uncertainties':[]}
        before=copy.deepcopy(state)
        candidate,audit=sem.apply_review(state,brief,opinion(state,brief))
        self.assertIsNone(candidate)
        self.assertEqual(state,before)
        self.assertEqual(audit['review_rows'][0]['physical_unit_binding']['unbound_assertions'][0]['value'],1.624)
        self.assertFalse(audit['event_probabilities_adjusted'])
        self.assertFalse(audit['original_text_removed'])

    def test_binding_matches_value_and_canonical_unit_not_page_token(self):
        index={'references':[{'reference_id':'R1','context_ids':[],
                'text':'MHHW = 1.624 meters. A separate object measures 7.9 m (25.9 ft).'}]}
        fact={'evidence_ids':['R1'],'interpretation':'MHHW is 1.624 m.'}
        self.assertFalse(sem.physical_bindings(fact,index)['unbound_assertions'])
        fact['interpretation']='MHHW is about 1.624 ft.'
        self.assertTrue(sem.physical_bindings(fact,index)['unbound_assertions'])
        fact['interpretation']='MHHW is 1.624 meters, equivalent to 5.33 ft.'
        audit=sem.physical_bindings(fact,index)
        self.assertFalse(audit['unbound_assertions'])
        self.assertTrue(audit['assertions'][1]['explicit_conversion_claim'])

    def test_no_unreviewed_synthesis_or_dependent_hypothesis_enters_score(self):
        state=common();brief=proposal(state)
        candidate,audit=sem.apply_review(state,brief,opinion(state,brief))
        self.assertEqual(candidate['facts'],brief['facts'])
        self.assertFalse(candidate['change_factors'])
        self.assertNotEqual(candidate['forecast_frame'],brief['forecast_frame'])
        self.assertFalse(audit['meaning_verified'])
        self.assertEqual(audit['omitted_synthesis']['forecast_frame'],brief['forecast_frame'])
        candidate,audit=sem.apply_review(state,brief,opinion(state,brief,'hypothesis_only'))
        self.assertIsNone(candidate)
        self.assertEqual(audit['hypothesis_only_ids'],['F1'])

    def test_probability_support_margin_not_confidence_controls_retention(self):
        state=common();brief=proposal(state);answer=opinion(state,brief)
        head=answer['answers']['claim_0_support']
        head['probabilities']={'literal_or_derived':.79,'contradicted':.0,'hypothesis_only':.01,'not_established':.2}
        candidate,audit=sem.apply_review(state,brief,answer)
        self.assertIsNone(candidate)
        head['probabilities']={'literal_or_derived':.9,'contradicted':.0,'hypothesis_only':.0,'not_established':.1}
        head['confidence']=.01
        self.assertIsNotNone(sem.apply_review(state,brief,answer)[0])

    def test_binary_conversion_never_multiplies_provider_confidence(self):
        answer={'answers':{'event_yes':{'type':'noul','noul':.7,'confidence':.1}}}
        result={'raw_forecast':{'probability_yes':.7},'payload':{'probability_yes':.7}}
        audit=sem.conversion_audit(answer,None,result)
        self.assertEqual(audit['maximum_payload_change'],0)
        result['raw_forecast']['probability_yes']=.07
        with self.assertRaises(ValueError):sem.conversion_audit(answer,None,result)

    def test_invalid_required_review_head_is_not_accepted(self):
        state=common();brief=proposal(state);answer=opinion(state,brief)
        del answer['answers']['claim_0_role']
        with self.assertRaises(ValueError):sem.apply_review(state,brief,answer)

    def test_archived_controls_are_byte_frozen_including_failed_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp);save(parent/'attempt.json',{'status':'http_error','http_status':502})
            before=semantics_trial.controls(parent)
            save(parent/'attempt.json',{'status':'http_error','http_status':503})
            self.assertNotEqual(before,semantics_trial.controls(parent))

    def test_same_source_context_restores_identity_without_fetching_hidden_text(self):
        state=common();brief=proposal(state);before=copy.deepcopy(state)
        request,heads=sem.review_request(state,brief)
        context=request['source_context']
        catalog={r['reference_id']:r for r in refs.catalog(state)['references']}
        self.assertTrue(context['references'])
        self.assertTrue(all(r==catalog[r['reference_id']] for r in context['references']))
        self.assertEqual(state,before)
        self.assertFalse(context['new_source_text'])
        self.assertTrue(all('do not demand the future target' in h['instructions'].lower() for h in heads.values()))

    def test_failed_optional_review_preserves_a_final_score_and_no_resume_renewal(self):
        state=common();brief=proposal(state)
        heads,spec=forecast_brief.registry(state['question'])
        old_response=response(heads)
        old_result={'raw_forecast':{'probability_yes':1.},
                    'payload':payload(state['question'],{'probability_yes':1.}),
                    'summary':{'probability_yes':.98}}
        def fake_decide(view,questions,key,observer,**kwargs):
            record={'request':{'state':view,'questions':questions},'status':'reserved'}
            token=observer('reserve',record)
            try:
                if any(k.startswith('claim_') for k in questions):
                    record['status']='http_error';record['http_status']=503
                    raise RuntimeError('Simulated unavailable optional reviewer')
                result=response(questions)
                record.update(status='received',response=result)
                return result
            finally:observer('complete',record,token)
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)/'control';case=parent/'live/cases/7';root=Path(tmp)/'trial'
            save(case/'common-state.json',state);save(case/'questions.json',heads)
            save(case/'spec.json',spec);save(case/'brief/accepted.json',brief)
            for arm in ('direct','brief'):
                save(case/arm/'result.json',old_result)
                save(case/arm/'decision/response.json',old_response)
            with patch.dict('os.environ',{'OPENROUTER_API_KEY':'fixture'}),patch(
                    'ForecastAgent.research_loop.decision_http.decide',side_effect=fake_decide):
                first=semantics_trial.run(parent,root,execute=True,max_http=2)
                charged={str(p):p.read_bytes() for p in semantics_trial.receipts(root)}
                second=semantics_trial.run(parent,root,execute=True,max_http=2)
            self.assertEqual(first['completed'],1)
            self.assertFalse(first['rows'][0]['arms']['reviewed']['brief_delivered'])
            self.assertEqual(second['actual_http'],2)
            self.assertEqual(charged,{str(p):p.read_bytes() for p in semantics_trial.receipts(root)})


if __name__=='__main__':unittest.main()
