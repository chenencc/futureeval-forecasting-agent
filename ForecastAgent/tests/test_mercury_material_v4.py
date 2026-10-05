"""Root preservation, atomic gaps and conditional coherence over original text."""
import copy
import unittest
from ForecastAgent.supplement import mercury_material_v4 as v4
from ForecastAgent.tests.test_mercury_material import MercuryMaterial
from ForecastAgent.tests.test_mercury_material_v3 import answers


class Obligations(unittest.TestCase):
    def fixture(self):
        bundle,plan,_ = MercuryMaterial().fixture()
        reply = {'conditions':[{'need_id':plan['needs'][0]['id'],'qualifiers':[
            {'id':'entity','proposition':'The entity is Texas.','role':'entity',
             'rule_ids':[plan['rule_catalog'][0]['rule_id']]},
            {'id':'time','proposition':'The actual event occurred before the deadline.','role':'time',
             'rule_ids':[plan['rule_catalog'][0]['rule_id']]}]}]}
        contracts = v4.bind_contracts(bundle,plan,reply)
        return bundle,plan,reply,v4.prepare(bundle,plan,contracts)

    def response(self,p,overrides=None):
        selected = {k:next(c for c in q['criteria'] if c.startswith('explicit|')) for k,q in p['questions'].items()}
        selected.update(overrides or {})
        return answers(p['questions'],selected)

    def test_compiler_never_sees_bodies_and_keeps_original_root(self):
        b,plan,reply,p = self.fixture()
        self.assertEqual(set(v4.compile_payload(b,plan)),{'question','needs','rule_catalog'})
        reply['conditions'][0]['qualifiers']=[]
        c = v4.bind_contracts(b,plan,reply)
        self.assertEqual(c['conditions'][0]['root_proposition'],plan['needs'][0]['condition'])
        empty = v4.prepare(b,plan,c)
        self.assertEqual(len(empty['questions']),1)
        r = v4.bind(empty,self.response(empty),b)
        self.assertEqual(r['rows'][0]['candidate_status'],'partial_or_unassessed')

    def test_known_parameter_does_not_replace_observation(self):
        b,_,_,p = self.fixture()
        parameter = next(c for c in p['questions']['proof_0_0']['criteria'] if c.startswith('parameter_context|'))
        r = v4.bind(p,self.response(p,{'proof_0_0':parameter}),b)
        self.assertEqual(r['rows'][0]['gaps'][0]['status'],'parameter_context')
        self.assertEqual(r['rows'][0]['candidate_status'],'partial_or_unassessed')
        self.assertFalse(v4.prepare_coherence(p,r)['questions'])

    def test_missing_or_inferred_atomic_qualifier_remains_gap(self):
        b,_,_,p = self.fixture()
        for choice in ['NONE',next(c for c in p['questions']['proof_0_2']['criteria'] if c.startswith('inferred|'))]:
            r = v4.bind(p,self.response(p,{'proof_0_2':choice}),b)
            self.assertEqual(r['rows'][0]['candidate_status'],'partial_or_unassessed')
            self.assertEqual(r['rows'][0]['gaps'][0]['id'],'time')

    def test_missing_root_cannot_be_reconstructed_from_qualifiers(self):
        b,_,_,p = self.fixture()
        r = v4.bind(p,self.response(p,{'proof_0_0':'NONE'}),b)
        self.assertEqual(r['rows'][0]['gaps'][0]['id'],'ROOT')
        self.assertFalse(v4.prepare_coherence(p,r)['questions'])

    def test_same_url_is_not_automatic_coherence_and_rejection_keeps_claims(self):
        b,_,_,p = self.fixture()
        r = v4.bind(p,self.response(p),b)
        self.assertEqual(r['rows'][0]['candidate_status'],'pending_same_observation_check')
        c = v4.prepare_coherence(p,r)
        self.assertEqual(c['state']['candidate_claims'],r['rows'])
        for choice in ['different_observations','unestablished','same_observation']:
            revised = v4.bind_coherence(r,c,answers(c['questions'],{'coherence_0':choice}))
            self.assertEqual(revised['rows'][0]['root'],r['rows'][0]['root'])
            self.assertEqual(revised['rows'][0]['qualifiers'],r['rows'][0]['qualifiers'])
            self.assertFalse(revised['rows'][0]['truth_verified'])
            self.assertEqual(revised['automatic_condition_closures'],0)
            self.assertEqual(revised['rows'][0]['candidate_status'],
                'complete_model_candidate_unverified' if choice=='same_observation' else 'coherence_unestablished')

    def test_invalid_compilation_is_rejected_without_silent_truncation(self):
        b,plan,reply,_ = self.fixture()
        bad_cases=[]
        for field,value in [('need_id',[]),('need_id','unknown'),('qualifiers',[{}]*7)]:
            bad=copy.deepcopy(reply);bad['conditions'][0][field]=value;bad_cases.append(bad)
        for field,value in [('role',[]),('rule_ids',[{}]),('rule_ids',['unknown']),('id','')]:
            bad=copy.deepcopy(reply);bad['conditions'][0]['qualifiers'][0][field]=value;bad_cases.append(bad)
        bad=copy.deepcopy(reply);bad['conditions'][0]['qualifiers'].append(bad['conditions'][0]['qualifiers'][0]);bad_cases.append(bad)
        bad_cases.extend([{'conditions':[]},{'conditions':reply['conditions']*2}])
        for bad in bad_cases:
            with self.subTest(bad=bad),self.assertRaises(ValueError):v4.bind_contracts(b,plan,bad)

    def test_changed_body_or_rule_cannot_bind(self):
        b,_,_,p=self.fixture()
        changed=copy.deepcopy(b);changed['pages'][next(iter(changed['pages']))]['content']+='changed'
        with self.assertRaisesRegex(ValueError,'saved_body_changed'):v4.bind(p,self.response(p),changed)
        changed=copy.deepcopy(b);changed['request']['resolution_criteria']+='changed'
        with self.assertRaisesRegex(ValueError,'rule_changed'):v4.bind(p,self.response(p),changed)


if __name__=='__main__':unittest.main()
