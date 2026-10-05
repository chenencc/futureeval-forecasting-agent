"""Sequential dependencies and failure preservation without provider requests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import save
from ForecastAgent.experiments import mercury_material_units_trial as trial
from ForecastAgent.tests.test_mercury_material import MercuryMaterial
from ForecastAgent.tests.test_mercury_material_v3 import answers


class Trial(unittest.TestCase):
    def fixture(self,root):
        bundle,plan,_ = MercuryMaterial().fixture()
        manifest = {'trial_id':'offline','scope':'offline','frozen_code_sha256':{},
            'prior_experiments':[{'http':21,'unchanged':True}],
            'limits':{'http':3,'logical':3,'seconds':30,'request_bytes':200000},
            'cases':[{'id':'fixed','question_id':'fixture','bundle':bundle,'plan':plan,'arm_order':['v2','v3']}]}
        save(root/'manifest.json',manifest); return root/'manifest.json'

    def decide(self,state,questions,key,observer):
        selected = {}
        for k,q in questions.items():
            selected[k] = 'entity_identity' if k.startswith('unit_') else 'NONE' if 'NONE' in q['criteria'] else 'insufficient' if 'insufficient' in q['criteria'] else 'no_record'
        response = answers(questions,selected)
        record = {'request':{'model':trial.decisions.MODEL,'state':state,'questions':questions},'status':'reserved'}
        token = observer('reserve',record)
        record.update(status='received',response=response)
        observer('complete',record,token)
        return response

    def test_prior_history_preserved_and_unit_reply_is_explicit_second_call_input(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); manifest = self.fixture(root)
            with patch.object(trial.decisions,'decide',self.decide):r = trial.run(root/'out','dummy',manifest)
            self.assertTrue(r['business_gate_passed'])
            self.assertEqual(r['actual_http_attempts'],3)
            self.assertEqual(r['prior_experiments'],[{'http':21,'unchanged':True}])
            self.assertEqual([a['phase'] for a in r['attempts']],['evidence','units','evidence'])
            from ForecastAgent.analysis.pilot import load
            p = load(root/'out/fixed-v3-prepared.json')
            self.assertEqual(p['state']['judgment_units']['unit_0']['choice'],'entity_identity')

    def test_existing_journal_and_transport_failure_do_not_reset(self):
        def fail(state,questions,key,observer):
            observer('reserve',{'request':{'model':trial.decisions.MODEL},'status':'reserved'})
            raise RuntimeError('simulated_service_error')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); manifest = self.fixture(root)
            with patch.object(trial.decisions,'decide',fail):r = trial.run(root/'out','dummy',manifest)
            self.assertTrue(r['blocked']); self.assertEqual(r['actual_http_attempts'],1)
            self.assertEqual(r['attempts'][0]['status'],'reserved')
            with self.assertRaisesRegex(ValueError,'existing_trial_cannot_reset_budget'):
                trial.run(root/'out','dummy',manifest)


if __name__ == '__main__':unittest.main()
