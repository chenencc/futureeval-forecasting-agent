"""Budget-censored handoff does not renew or discard original reservations."""
import copy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition import full_chain_ten_resume as trial
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.tests.test_material_handoff import fixture


def censored():
    b=fixture('Original report evidence.\n')
    next(iter(b['pages'].values()))['body_diagnostics']={'usable_text':True}
    b['result']={'status':'collected','incomplete':True,'termination_reason':'program_dispatch_limit',
                 'execution_report':{'interrupted':False},'gaps':['Original unresolved material']}
    b['model_attempts']=[{'status':'received'}]*10
    b['searches']=[{'status':'completed'}];b['exa_searches']=[{'status':'completed'}]
    b['fetch_attempts']=[{'status':'completed'}]
    return b


class FullChainTenResumeTests(TestCase):
    def test_collected_budget_stop_is_distinct_from_transport_or_empty_output(self):
        b=censored();self.assertTrue(trial.eligible(b))
        for reason in ['shared_account_quota','transport_failure','model_provider_error']:
            bad=copy.deepcopy(b);bad['result']['termination_reason']=reason
            self.assertFalse(trial.eligible(bad))
        b['pages']={};self.assertFalse(trial.eligible(b))

    def test_bridge_preserves_input_gaps_and_every_consumed_counter(self):
        a,p,c=trial.amendment();request=c['requests'][0];qid=request['id']
        with TemporaryDirectory() as tmp:
            root=Path(tmp); directory=root/qid/'acquisition'
            frozen=trial.original.pipeline.identity(request,True)
            b=censored();b['request']=frozen['request']
            save(directory/'identity.json',frozen)
            save(directory/'state.json',{'stage':'collection','identity_sha256':digest(frozen)})
            bp=directory/'collection/bundle.json';save(bp,b);raw=bp.read_bytes()
            with patch('ForecastAgent.agent.run_research') as model:
                self.assertTrue(trial.bridge(root,qid));self.assertFalse(trial.bridge(root,qid))
                model.assert_not_called()
            self.assertEqual(bp.read_bytes(),raw)
            self.assertEqual(load(directory/'state.json')['stage'],'supplement')
            receipt=load(root/qid/'budget-censored-handoff.json')
            self.assertEqual(receipt['provider_counters']['model_attempts'],10)
            self.assertEqual(receipt['original_result']['gaps'],['Original unresolved material'])
            self.assertFalse(receipt['budget_reset'])

    def test_unknown_original_identity_cannot_be_bridged(self):
        a,p,c=trial.amendment();qid=c['question_ids'][0]
        with TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/qid/'acquisition'
            save(directory/'state.json',{'stage':'collection','identity_sha256':'wrong'})
            save(directory/'identity.json',{})
            save(directory/'collection/bundle.json',censored())
            with self.assertRaisesRegex(ValueError,'Original collected'):
                trial.bridge(root,qid)

    def test_missing_artifact_does_not_allow_restart_if_collection_ran(self):
        a,p,c=trial.amendment();qid=c['question_ids'][0]
        class Client:
            def api(self,url):
                if '/artifacts?' in url:return {'artifacts':[]}
                if '/jobs?' in url:return {'jobs':[{'name':'full_chain_ten ('+qid+')','id':1,
                    'steps':[{'name':'Fresh acquisition, independent supplement, first and reread scores','conclusion':'cancelled'}]}]}
                return {'status':'completed','head_sha':a['exact_parent_commit'],'run_attempt':1}
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'recoverable artifact'):
                trial.restore(Path(tmp),qid,a['exact_parent_run_id'],Client())

    def test_unexecuted_parent_job_can_start_as_a_new_case(self):
        a,p,c=trial.amendment();qid=c['question_ids'][0]
        class Client:
            def api(self,url):
                if '/artifacts?' in url:return {'artifacts':[]}
                if '/jobs?' in url:return {'jobs':[{'name':'full_chain_ten ('+qid+')','id':1,'steps':[]}]}
                return {'status':'completed','head_sha':a['exact_parent_commit'],'run_attempt':1}
        with TemporaryDirectory() as tmp:
            r=trial.restore(Path(tmp),qid,a['exact_parent_run_id'],Client())
            self.assertEqual(r['status'],'parent_collection_never_executed')
            self.assertFalse(r['budget_reset'])
