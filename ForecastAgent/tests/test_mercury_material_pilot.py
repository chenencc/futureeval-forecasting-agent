"""Independent trial reservation and immutable journal tests; no HTTP."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import save
from ForecastAgent.experiments import mercury_material_pilot as pilot
from ForecastAgent.tests import test_mercury_material as fixtures


class Pilot(unittest.TestCase):
    def fixture(self, root):
        bundle, plan, _ = fixtures.MercuryMaterial().fixture()
        data = {'trial_id':'offline', 'scope':'offline', 'frozen_code_sha256':{},
            'prior_experiments':[{'run_id':'original', 'http':19}],
            'limits':{'logical':2,'http':2,'http_per_arm':1,'decision_request_bytes':90000},
            'cases':[{'id':'case','question_id':'fixture','bundle':bundle,'plan':plan,'arm_order':['super','mercury']}]}
        save(root/'manifest.json', data)
        return root/'manifest.json'

    def ask(self, messages, key, *, observer, forced_tool, **kwargs):
        payload=json.loads(messages[1]['content'])
        reply={'annotations':[{'need_id':n['id'],'passage_ids':[],'relation':'unknown',
            'fit':'unknown','observation':'','explanation':'No condition established.'} for n in payload['needs']]}
        message={'tool_calls':[{'function':{'name':forced_tool,'arguments':json.dumps(reply)}}]}
        record={'request':{'model':pilot.MODEL},'status':'reserved'}
        token=observer('reserve', record)
        record.update(status='received',response={'choices':[{'message':message,'finish_reason':'tool_calls'}],
                                                 'usage':{'total_tokens':1}})
        observer('complete',record,token)
        return message

    def decide(self, state, questions, key, observer):
        bundle, plan, prepared=fixtures.MercuryMaterial().fixture()
        result=fixtures.response(prepared)
        record={'request':{'model':'inception/mercury-decide:free','state':state,'questions':questions},'status':'reserved'}
        token=observer('reserve',record)
        record.update(status='received',response={**result,'usage':{'input_tokens':1,'output_tokens':0}})
        observer('complete',record,token)
        return result

    def test_fixed_pair_preserves_prior_history_and_records_both_calls(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); manifest=self.fixture(root)
            with patch.dict(os.environ,{'FORECAST_MODEL':pilot.MODEL,'FORECAST_MODEL_FALLBACK_SUPER':'0'}), \
                 patch.object(pilot,'ask_model',self.ask), patch.object(pilot.decisions,'decide',self.decide):
                report=pilot.run(root/'out','dummy',manifest)
            self.assertEqual(report['actual_http_attempts'],2)
            self.assertEqual(report['logical_decisions'],2)
            self.assertTrue(report['business_gate_passed'])
            self.assertEqual(report['prior_experiments'],[{'run_id':'original','http':19}])
            self.assertEqual(report['forecast_submissions'],0)

    def test_existing_journal_cannot_reset_allowance(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); manifest=self.fixture(root); (root/'out').mkdir()
            with patch.dict(os.environ,{'FORECAST_MODEL':pilot.MODEL,'FORECAST_MODEL_FALLBACK_SUPER':'0'}), \
                 patch.object(pilot,'ask_model') as ask:
                with self.assertRaisesRegex(ValueError,'existing_trial_cannot_reset_budget'):
                    pilot.run(root/'out','dummy',manifest)
                ask.assert_not_called()

    def test_provider_reservation_survives_transport_failure(self):
        def fail(state, questions, key, observer):
            observer('reserve',{'request':{'model':'inception/mercury-decide:free'},'status':'reserved'})
            raise RuntimeError('simulated_transport_failure')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); manifest=self.fixture(root)
            with patch.dict(os.environ,{'FORECAST_MODEL':pilot.MODEL,'FORECAST_MODEL_FALLBACK_SUPER':'0'}), \
                 patch.object(pilot,'ask_model',self.ask), patch.object(pilot.decisions,'decide',fail):
                report=pilot.run(root/'out','dummy',manifest)
            self.assertTrue(report['blocked'])
            self.assertFalse(report['business_gate_passed'])
            self.assertEqual(report['attempts'][-1]['status'],'reserved')
            self.assertEqual(report['actual_http_attempts'],2)


if __name__=='__main__':
    unittest.main()
