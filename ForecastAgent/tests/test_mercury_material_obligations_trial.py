"""Fixed model policy, durable calls, paired coverage and service failure stop."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import load,save
from ForecastAgent.experiments import mercury_material_obligations_trial as trial
from ForecastAgent.tests.test_mercury_material import MercuryMaterial
from ForecastAgent.tests.test_mercury_material_v3 import answers


class Trial(unittest.TestCase):
    def fixture(self,root):
        bundle,plan,_=MercuryMaterial().fixture()
        manifest={'trial_id':'offline','scope':'offline','frozen_code_sha256':{},
            'prior_experiments':[{'http':24,'unchanged':True}],
            'limits':{'logical':8,'http':8,'seconds':30,'request_bytes':250000,
                      'needs_per_case':16,'proof_heads_per_case':100},
            'cases':[{'id':'fixed','question_id':'fixture','cohort':'regression',
                      'bundle':bundle,'plan':plan,'arm_order':['v3','v4']}]}
        save(root/'manifest.json',manifest);return root/'manifest.json'

    def decide(self,state,questions,key,observer):
        selected={k:'entity_identity' if k.startswith('unit_') else 'NONE' for k in questions}
        response=answers(questions,selected)
        rec={'request':{'model':trial.decisions.MODEL,'state':state,'questions':questions},'status':'reserved'}
        token=observer('reserve',rec);rec.update(status='received',response=response);observer('complete',rec,token)
        return response

    def compile(self,messages,key,tools,forced_tool,observer,**kwargs):
        payload=json.loads(messages[1]['content'])
        reply={'conditions':[{'need_id':n['id'],'qualifiers':[]} for n in payload['needs']]}
        rec={'request':{'model':trial.SUPER,'messages':messages},'status':'reserved'}
        token=observer('reserve',rec);rec.update(status='received',response={'choices':[{'finish_reason':'tool_calls'}]});observer('complete',rec,token)
        return {'tool_calls':[{'function':{'name':forced_tool,'arguments':json.dumps(reply)}}]}

    def test_four_calls_share_reading_keep_root_and_history(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'FORECAST_MODEL':trial.SUPER,'FORECAST_MODEL_FALLBACK_SUPER':'0'}):
            root=Path(d);manifest=self.fixture(root)
            with patch.object(trial.decisions,'decide',self.decide),patch.object(trial,'ask_model',self.compile):
                r=trial.run(root/'out','dummy',manifest)
            self.assertTrue(r['business_gate_passed']);self.assertEqual(r['actual_http_attempts'],4)
            self.assertEqual([a['phase'] for a in r['attempts']],['units','evidence','compile','proof'])
            self.assertEqual(r['prior_experiments'],[{'http':24,'unchanged':True}])
            old=load(root/'out/fixed-v3-prepared.json');new=load(root/'out/fixed-v4-prepared.json')
            for k in ('reading','needs','question','rule_catalog'):self.assertEqual(old['state'][k],new['state'][k])
            self.assertEqual(len(new['questions']),len(new['state']['needs']))
            with self.assertRaisesRegex(ValueError,'existing_trial_cannot_reset_budget'):trial.run(root/'out','dummy',manifest)

    def test_service_failure_preserves_reservation_and_blocks_later_calls(self):
        def fail(state,questions,key,observer):
            observer('reserve',{'request':{'model':trial.decisions.MODEL},'status':'reserved'})
            raise RuntimeError('service_failure')
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'FORECAST_MODEL':trial.SUPER,'FORECAST_MODEL_FALLBACK_SUPER':'0'}):
            root=Path(d);manifest=self.fixture(root)
            with patch.object(trial.decisions,'decide',fail):r=trial.run(root/'out','dummy',manifest)
            self.assertTrue(r['blocked']);self.assertEqual(r['actual_http_attempts'],1)
            self.assertEqual(r['attempts'][0]['status'],'reserved')

    def test_continuation_reuses_completed_arms_and_preserves_caps(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'FORECAST_MODEL':trial.SUPER,'FORECAST_MODEL_FALLBACK_SUPER':'0'}):
            root=Path(d);manifest=self.fixture(root)
            with patch.object(trial.decisions,'decide',self.decide),patch.object(trial,'ask_model',self.compile):
                old=trial.run(root/'out','dummy',manifest)
            amended=load(manifest);amended.update(resume_required=True,
                resume_parent_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),resume_parent_run='offline')
            save(root/'resume.json',amended)
            with patch.object(trial.decisions,'decide',side_effect=AssertionError('must not rerun')),patch.object(trial,'ask_model',side_effect=AssertionError('must not rerun')):
                new=trial.run(root/'resumed','dummy',root/'resume.json',resume=root/'out')
            self.assertTrue(new['business_gate_passed']);self.assertEqual(new['attempts'],old['attempts'])
            self.assertEqual(new['calls'],old['calls']);self.assertEqual(new['limits'],old['limits'])

    def test_grouping_keeps_all_delivered_units_and_binds_real_source_span(self):
        from ForecastAgent.supplement import mercury_candidate_groups as groups
        from ForecastAgent.supplement import mercury_material_v3 as v3
        bundle,plan,_=MercuryMaterial().fixture()
        url=next(iter(bundle['pages']))
        bundle['pages'][url]['content']='Texas observed source '+('x'*1450)+'\n'
        bundle['pages'][url]['content']*=30
        unit=v3.prepare_units(bundle,plan)
        prepared=v3.prepare(bundle,plan,answers(unit['questions'],{'unit_0':'entity_identity'}))
        compact=groups.prepare(prepared)
        self.assertEqual(compact['state']['reading'],prepared['state']['reading'])
        self.assertLess(compact['candidate_group_audit']['grouped_candidates'],compact['candidate_group_audit']['original_source_units'])
        refs={r for g in compact['state']['candidate_groups'] for r in g['member_passage_ids']}
        self.assertEqual(refs,{r['passage_id'] for r in prepared['candidates'].values() if r['kind']=='source'})
        choice=next(c for c in compact['questions']['evidence_0']['criteria'] if c.startswith('field_match|'))
        result=v3.bind(compact,answers(compact['questions'],{'evidence_0':choice}),bundle)
        self.assertTrue(result['rows'][0]['text_identity_verified'])

    def test_wire_projection_keeps_semantic_text_and_marks_omissions(self):
        from ForecastAgent.supplement import mercury_wire as wire
        bundle,plan,_=MercuryMaterial().fixture()
        prepared=trial.v3.v2.prepare(bundle,plan)
        original=prepared['state']['reading']
        projected=wire.prepare(prepared)
        semantic=lambda p:[{k:v for k,v in row.items() if k in ('passage_id','url','start','end','text','context_spans')} for row in p]
        self.assertEqual(projected['state']['reading']['passages'],semantic(original['passages']))
        self.assertNotIn('inventory',projected['state']['reading'])
        self.assertIn('inventory',original)
        self.assertTrue(projected['state']['reading_coverage_notice']['omission_does_not_prove_absence'])

    def test_invalid_request_is_local_to_case_and_does_not_block_other_arm(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'FORECAST_MODEL':trial.SUPER,'FORECAST_MODEL_FALLBACK_SUPER':'0'}):
            root=Path(d);manifest=self.fixture(root)
            first=[True]
            def invalid(state,questions,key,observer):
                if first[0]:
                    first[0]=False
                    observer('reserve',{'request':{'model':trial.decisions.MODEL},'status':'reserved'})
                    raise RuntimeError('Decision endpoint HTTP 422')
                return self.decide(state,questions,key,observer)
            with patch.object(trial.decisions,'decide',invalid),patch.object(trial,'ask_model',self.compile):
                r=trial.run(root/'out','dummy',manifest)
            self.assertFalse(r['blocked']);self.assertEqual(r['cases'][0]['arms']['v4']['status'],'received')

    def test_single_schema_repair_preserves_obligation_and_is_counted(self):
        bundle,plan,_=MercuryMaterial().fixture()
        n=plan['needs'][0]
        proposal={'id':n['id'],'condition':n['condition'],'critical':n['critical'],
            'dimension':'effect','target':n['targets'][n['dimension']]['value'],'rule_ids':n['rule_ids']}
        def provider(messages,key,tools,forced_tool,observer,**kwargs):
            if forced_tool=='compile_evidence_obligations':return self.compile(messages,key,tools,forced_tool,observer,**kwargs)
            payload=json.loads(messages[1]['content'])
            corrected={**proposal,'dimension':n['dimension']} if 'previous_proposal' in payload else proposal
            reply={'needs':[corrected]}
            rec={'request':{'model':trial.SUPER,'messages':messages},'status':'reserved'}
            token=observer('reserve',rec);rec.update(status='received',response={'choices':[{'finish_reason':'tool_calls'}]});observer('complete',rec,token)
            return {'tool_calls':[{'function':{'name':forced_tool,'arguments':json.dumps(reply)}}]}
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'FORECAST_MODEL':trial.SUPER,'FORECAST_MODEL_FALLBACK_SUPER':'0'}):
            root=Path(d);manifest=self.fixture(root);m=load(manifest);m['cases'][0].pop('plan');save(manifest,m)
            with patch.object(trial.decisions,'decide',self.decide),patch.object(trial,'ask_model',provider):
                r=trial.run(root/'out','dummy',manifest)
            self.assertTrue(r['business_gate_passed']);self.assertEqual(r['actual_http_attempts'],6)
            self.assertEqual([a['phase'] for a in r['attempts']].count('planning_repair'),1)
            frozen=load(root/'out/fixed-frozen-input.json')
            self.assertEqual(frozen['plan']['needs'][0]['condition'],proposal['condition'])


if __name__=='__main__':unittest.main()
