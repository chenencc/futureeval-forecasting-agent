"""Paired coverage, durable independent ledgers and continuation call reuse."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.experiments import mercury_operator_ledgers_trial as trial
from ForecastAgent.tests.test_mercury_material import MercuryMaterial
from ForecastAgent.tests.test_mercury_material_v3 import answers


class Trial(unittest.TestCase):
    def fixture(self, root):
        b, plan, _ = MercuryMaterial().fixture()
        manifest={'trial_id':'offline','frozen_code_sha256':{},
            'limits':{'logical':10,'http':10,'seconds':30,'request_bytes':500000},
            'prior_experiments':[{'run':'previous','http':17,'unchanged':True}],
            'cases':[{'id':'fixed','question_id':'fixture','bundle':b,'plan':plan,'arm_order':['v5','v6']}]}
        save(root/'manifest.json',manifest)
        return root/'manifest.json'

    def decide(self, state, questions, key, observer):
        selected={k:('NONE' if 'NONE' in q['criteria'] else next(iter(q['criteria']))) for k,q in questions.items()}
        response=answers(questions,selected)
        rec={'request':{'model':trial.decisions.MODEL,'state':state,'questions':questions},'status':'reserved'}
        token=observer('reserve',rec)
        rec.update(status='received',response=response)
        observer('complete',rec,token)
        return response

    def test_four_calls_identical_coverage_and_all_observations_retained(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manifest=self.fixture(root)
            with patch.object(trial.decisions,'decide',self.decide):
                result=trial.run(root/'out','dummy',manifest)
            self.assertTrue(result['business_gate_passed'])
            self.assertEqual(result['actual_http_attempts'],4)
            old=load(root/'out/fixed-v5-prepared.json');new=load(root/'out/fixed-v6-prepared.json')
            for field in ('question','needs','reading','rule_catalog'):
                self.assertEqual(old['state'][field],new['state'][field])
            bound=load(root/'out/fixed-v6-result.json')
            self.assertEqual(len(bound['rows']),len(new['state']['needs']))
            self.assertTrue(all('observation_obligation' in r['ledger'] for r in bound['rows']))
            self.assertEqual(result['prior_experiments'][0]['http'],17)
            with self.assertRaisesRegex(ValueError,'reset_budget'):
                trial.run(root/'out','dummy',manifest)

    def test_resume_completed_outputs_makes_no_provider_calls(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manifest=self.fixture(root)
            with patch.object(trial.decisions,'decide',self.decide):
                old=trial.run(root/'out','dummy',manifest)
            with patch.object(trial.decisions,'decide',side_effect=AssertionError('extra call')):
                new=trial.run(root/'resumed','dummy',manifest,resume=root/'out')
            self.assertEqual(old['attempts'],new['attempts'])
            self.assertEqual(old['calls'],new['calls'])

    def test_service_failure_durably_preserves_reservation_and_blocks(self):
        def fail(state,questions,key,observer):
            observer('reserve',{'request':{'model':trial.decisions.MODEL},'status':'reserved'})
            raise RuntimeError('service_failure')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with patch.object(trial.decisions,'decide',fail):
                result=trial.run(root/'out','dummy',self.fixture(root))
            self.assertTrue(result['blocked'])
            self.assertEqual(result['actual_http_attempts'],1)
            self.assertEqual(result['attempts'][0]['status'],'reserved')


if __name__=='__main__':
    unittest.main()
