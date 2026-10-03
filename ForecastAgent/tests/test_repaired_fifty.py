"""Frozen repaired inputs, separate labels, exact probabilities and missing cases."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis import repaired_fifty as trial
from ForecastAgent.analysis.pilot import save, digest


def decision(p):
    answers={}
    for key,q in trial.chain.questions().items():
        if q['type']=='noul':answers[key]={'type':'noul','noul':p if key=='event_yes' else 0}
        elif q['type']=='score':
            d={str(i):float(i==3) for i in range(len(q['criteria']))}
            answers[key]={'type':'score','score':3,'confidence':1,'probabilities':d}
        else:answers[key]={'type':'choice','choice':'supported','confidence':1,
            'probabilities':{k:float(k=='supported') for k in q['criteria']}}
    return {'model':trial.chain.decisions.MODEL,'answers':answers}


class RepairedFiftyTests(unittest.TestCase):
    def fixture(self,root):
        bundle={'request':{'id':'1','question':'Test question','resolution_criteria':'Exact event'},'pages':{}}
        for i in range(1,51):
            save(root/f'recollection-fifty-free-repair-{(i-1)//5}/tasks/{i}/analysis-input.json',{**bundle,'request':{**bundle['request'],'id':str(i)}})

    def test_scope_and_changed_input_fail_before_inference(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root/'input')
            with patch.object(trial.chain,'run_task',return_value={'status':'prepared'}) as model:
                trial.run(root/'input',root/'out',0,dry_run=True)
                self.assertEqual(model.call_count,5)
                save(root/'input/recollection-fifty-free-repair-0/tasks/1/analysis-input.json',{'changed':True})
                with self.assertRaisesRegex(ValueError,'Frozen'):
                    trial.run(root/'input',root/'out',0,dry_run=True)
                self.assertEqual(model.call_count,5)

    def test_missing_bodies_remain_failures_without_probability(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root/'input')
            with patch.object(trial.chain,'call') as http:
                rows=trial.run(root/'input',root/'out',0)
                http.assert_not_called()
            self.assertTrue(all(r['status']=='failed' and 'probability_yes' not in r for r in rows))

    def test_metrics_use_clipped_predictions_and_frozen_responses(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            labels=[]
            for i in range(50):
                task=root/f'results/repaired-fifty-analysis-{i//5}/tasks/{i+1}'
                bundle={'request':{'id':str(i+1)}}
                save(task/'analysis-input.json',bundle)
                save(task/'outcome.json',{'id':str(i+1),'status':'completed','bundle_sha256':digest(bundle),
                    'probability_yes':.8,'clipped_probability_yes':.8,'acquisition_gaps':[]})
                save(task/'mercury/first/response.json',decision(.8))
                labels.append({'id':str(i+1),'resolved_to':int(i<13)})
            save(root/'labels.json',{'provenance':{'test':True},'labels':labels})
            report=trial.evaluate(root/'results',root/'labels.json',root/'out')
            self.assertAlmostEqual(report['metrics']['brier'],.484)
            self.assertAlmostEqual(report['metrics']['correct_at_half'],.26)
            self.assertEqual(report['by_outcome']['1']['n'],13)
            row=trial.load(root/'results/repaired-fifty-analysis-0/tasks/1/outcome.json')
            save(root/'results/repaired-fifty-analysis-0/tasks/1/outcome.json',{**row,'probability_yes':.9})
            with self.assertRaisesRegex(ValueError,'Prediction changed'):
                trial.evaluate(root/'results',root/'missing-labels.json',root/'out')


if __name__=='__main__':unittest.main()
