"""Two-round sequencing and cumulative reservation behavior without network."""
import json
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import save
from ForecastAgent.experiments import mercury_material_rounds as trial
from ForecastAgent.tests.test_mercury_material import MercuryMaterial


class Rounds(unittest.TestCase):
    def fixture(self, root):
        bundle, plan, _ = MercuryMaterial().fixture()
        case = {'id':'fixed','question_id':'fixture','bundle':bundle,'plan':plan,'arm_order':['v1','v2']}
        data = {'trial_id':'offline','scope':'offline','frozen_code_sha256':{},
                'prior_experiments':[{'http':6,'unchanged':True}],
                'limits':{'http':4,'logical':4,'http_per_round':{'round1':2,'round2':2},
                          'request_bytes':120000,'needs_per_case':12,'seconds':30},
                'rounds':[{'id':r,'scope':'offline','cases':[case]} for r in ('round1','round2')]}
        save(root/'manifest.json',data)
        return root/'manifest.json'

    def decide(self, state, questions, key, observer):
        answers = {}
        for k,q in questions.items():
            opts = q['criteria']
            choice = 'NONE' if 'NONE' in opts else 'insufficient' if 'insufficient' in opts else 'no_record'
            answers[k] = {'type':'choice','choice':choice,'confidence':1,
                          'probabilities':{o:float(o == choice) for o in opts}}
        r = {'model':trial.decisions.MODEL,'answers':answers,'usage':{'input_tokens':1,'output_tokens':1}}
        record = {'request':{'model':trial.decisions.MODEL,'state':state,'questions':questions},'status':'reserved'}
        token = observer('reserve',record)
        record.update(status='received',response=r)
        observer('complete',record,token)
        return r

    def test_rounds_sequence_preserves_history_and_equal_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); manifest = self.fixture(root)
            with patch.dict(os.environ,{'FORECAST_MODEL':trial.SUPER,'FORECAST_MODEL_FALLBACK_SUPER':'0'}), \
                 patch.object(trial.decisions,'decide',self.decide):
                r = trial.run(root/'out','dummy',manifest)
            self.assertTrue(r['business_gate_passed'])
            self.assertEqual(r['actual_http_attempts'],4)
            self.assertEqual(r['logical_decisions'],4)
            self.assertEqual(r['prior_experiments'],[{'http':6,'unchanged':True}])
            self.assertEqual([a['round'] for a in r['attempts']],['round1','round1','round2','round2'])
            self.assertTrue(all(a['elapsed_seconds'] >= 0 for a in r['attempts']))
            p = json.loads((root/'out/round2-fixed-v2-prepared.json').read_text())
            old = json.loads((root/'out/round2-fixed-v1-prepared.json').read_text())
            self.assertEqual(p['state']['reading'],old['state']['reading'])

    def test_existing_trial_cannot_restart_budget(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); manifest = self.fixture(root); (root/'out').mkdir()
            with patch.dict(os.environ,{'FORECAST_MODEL':trial.SUPER,'FORECAST_MODEL_FALLBACK_SUPER':'0'}):
                with self.assertRaisesRegex(ValueError,'existing_trial_cannot_reset_budget'):
                    trial.run(root/'out','dummy',manifest)

    def test_one_generated_plan_is_frozen_for_both_second_round_arms(self):
        def planning(messages,key,*,observer,forced_tool,**kwargs):
            payload = json.loads(messages[1]['content'])
            reply = {'needs':[{'id':'one','condition':'An observed Texas directive exists.',
                     'critical':True,'dimension':'entity','target':'Texas',
                     'rule_ids':[payload['rule_catalog'][0]['rule_id']]}]}
            message = {'tool_calls':[{'function':{'name':forced_tool,'arguments':json.dumps(reply)}}]}
            record = {'request':{'model':trial.SUPER},'status':'reserved'}
            token = observer('reserve',record)
            record.update(status='received',response={'choices':[{'finish_reason':'tool_calls'}],
                                                      'usage':{'prompt_tokens':1,'completion_tokens':1}})
            observer('complete',record,token)
            return message
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); manifest = self.fixture(root)
            data = json.loads(manifest.read_text()); data['rounds'][1]['cases'] = copy.deepcopy(data['rounds'][1]['cases'])
            data['rounds'][1]['cases'][0].pop('plan')
            data['limits'].update(http=5,logical=5,http_per_round={'round1':2,'round2':3})
            save(manifest,data)
            with patch.dict(os.environ,{'FORECAST_MODEL':trial.SUPER,'FORECAST_MODEL_FALLBACK_SUPER':'0'}), \
                 patch.object(trial.decisions,'decide',self.decide), patch.object(trial,'ask_model',planning) as ask:
                r = trial.run(root/'out','dummy',manifest)
            self.assertEqual(r['actual_http_attempts'],5)
            self.assertTrue(r['business_gate_passed'])
            self.assertEqual(sum(a['arm'] == 'planning' for a in r['attempts']),1)
            new = json.loads((root/'out/round2-fixed-v2-prepared.json').read_text())
            old = json.loads((root/'out/round2-fixed-v1-prepared.json').read_text())
            self.assertEqual(new['state']['needs'],old['state']['needs'])

    def test_transport_failure_preserves_reservation_and_stops_second_round(self):
        def fail(state,questions,key,observer):
            observer('reserve',{'request':{'model':trial.decisions.MODEL},'status':'reserved'})
            raise RuntimeError('simulated_transport_failure')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); manifest = self.fixture(root)
            with patch.dict(os.environ,{'FORECAST_MODEL':trial.SUPER,'FORECAST_MODEL_FALLBACK_SUPER':'0'}), \
                 patch.object(trial.decisions,'decide',fail):
                r = trial.run(root/'out','dummy',manifest)
            self.assertTrue(r['blocked'])
            self.assertEqual(r['actual_http_attempts'],1)
            self.assertEqual(r['attempts'][0]['status'],'reserved')
            self.assertEqual(len(r['rounds']),1)


if __name__ == '__main__':
    unittest.main()
