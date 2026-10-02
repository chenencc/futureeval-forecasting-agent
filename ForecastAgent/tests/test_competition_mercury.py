"""Offline release acceptance with real journals and simulated provider decisions."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.competition import mercury, live
from ForecastAgent.analysis.pilot import save, load
from ForecastAgent.analysis.distributions import validate_cdf


def response(registry, insufficient=False):
    answers={}
    for key,q in registry.items():
        if q['type']=='noul':answers[key]={'type':'noul','noul':1. if key=='event_yes' else 0.}
        elif q['type']=='score':
            n=len(q['criteria']);answers[key]={'type':'score','score':n-1,'confidence':1.,'probabilities':{str(i):float(i==n-1) for i in range(n)}}
        else:
            keys=list(q['criteria']);chosen='insufficient' if insufficient and 'insufficient' in keys else ('supported' if 'supported' in keys else keys[0])
            answers[key]={'type':'choice','choice':chosen,'confidence':1.,'probabilities':{k:float(k==chosen) for k in keys}}
    return {'model':'inception/mercury-decide:free','answers':answers}


def bundle(kind='binary', long=False):
    request={'id':'7','question_type':kind,'question':'Will the event occur?',
        'resolution_criteria':'Resolve using the official August release.', 'fine_print':'Use the INITIAL release only.',
        'background':'An open forecasting event.', 'official_competition':True, 'as_of_utc':'2026-10-03T00:00:00Z'}
    if kind=='multiple_choice':request['options']=['A','B','C']
    elif kind!='binary':request.update(scaling={'range_min':1750000000 if kind=='date' else 0.,'range_max':1800000000 if kind=='date' else 100.,'zero_point':None},inbound_outcome_count=200,open_lower_bound=True,open_upper_bound=True,unit='UTC' if kind=='date' else 'units')
    body=('Official August release evidence and dated observations. '*2200) if long else 'Official August release evidence and observations. '*30
    return {'request':request,'pages':{'https://example.org/report':{'content':body,'body_diagnostics':{'usable_text':True}}},'result':{'gaps':['Missing independent source']}}


class MercuryReleaseTests(unittest.TestCase):
    def test_all_types_official_payload_and_durable_replay(self):
        for kind in ['binary','multiple_choice','numeric','discrete','date']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temporary:
                calls=[]
                def decide(state,questions,key,observer):
                    self.assertIn('INITIAL release',state['question']['fine_print'])
                    self.assertNotIn('Retrospective',state['evaluation_warning'])
                    record={'request':{'state':state,'questions':questions},'status':'reserved'}
                    token=observer('reserve',record);answer=response(questions)
                    record.update(status='received',response=answer);observer('complete',record,token);calls.append(1)
                    return answer
                with patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',decide):
                    first=mercury.run(bundle(kind),Path(temporary))
                    again=mercury.run(bundle(kind),Path(temporary))
                self.assertEqual(first,again);self.assertEqual(len(calls),1)
                p=first['payload'];self.assertEqual(p['question'],7)
                if kind=='binary':self.assertEqual(p['probability_yes'],.98)
                elif kind=='multiple_choice':
                    self.assertAlmostEqual(sum(p['probability_yes_per_category'].values()),1.)
                    self.assertTrue(all(.02<=v<=.98 for v in p['probability_yes_per_category'].values()))
                else:validate_cdf(p['continuous_cdf'],bundle(kind)['request'],clipped=True)

    def test_reread_failure_retains_first_and_never_retries(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls=[]
            def decide(state,questions,key,observer):
                record={'status':'reserved'};token=observer('reserve',record);calls.append(1)
                if len(calls)==2:
                    record['status']='http_error';observer('complete',record,token)
                    raise RuntimeError('Decision endpoint HTTP 503')
                answer=response(questions,insufficient=True)
                record.update(status='received',response=answer);observer('complete',record,token)
                return answer
            with patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',decide):
                result=mercury.run(bundle(long=True),Path(temporary))
                again=mercury.run(bundle(long=True),Path(temporary))
            self.assertEqual(len(calls),2)
            self.assertTrue(result['selection']=='mercury_first_read' and result['payload']['probability_yes']==.98)
            self.assertTrue(again['selection']=='mercury_first_read')

    def test_reasoning_fallback_only_on_provider_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);source=folder/'bundle.json';save(source,bundle())
            candidate={'payload':{'question':7,'probability_yes':.62},'comment':'# ForecastAgent 1.0 competition','selection':'old'}
            with patch.object(mercury,'run',side_effect=RuntimeError('HTTP 503')),patch.object(live,'legacy_analyze',return_value=copy.deepcopy(candidate)) as legacy:
                result=live.analyze(source,folder,'7')
                self.assertTrue(legacy.call_args.kwargs['reasoning_only'])
                self.assertEqual(result['release_version'],'1.0.1')
            with patch.object(mercury,'run',side_effect=ValueError('Frozen state changed')),patch.object(live,'legacy_analyze') as legacy:
                with self.assertRaises(ValueError):live.analyze(source,folder,'7')
                legacy.assert_not_called()


if __name__=='__main__':unittest.main()
