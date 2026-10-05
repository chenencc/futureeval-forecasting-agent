"""Unit-specific evidence selection, no hidden dependencies or record loss."""
import copy
import unittest
from ForecastAgent.supplement import mercury_material_v3 as material
from ForecastAgent.tests.test_mercury_material import MercuryMaterial


def answers(questions,selected):
    return {'model':'inception/mercury-decide:free','answers':{
        key:{'type':'choice','choice':selected[key],'confidence':1,
             'probabilities':{o:float(o == selected[key]) for o in q['criteria']}}
        for key,q in questions.items()}}


class Units(unittest.TestCase):
    def fixture(self,unit='entity_identity'):
        bundle,plan,old = MercuryMaterial().fixture()
        request = material.prepare_units(bundle,plan)
        response = answers(request['questions'],{'unit_0':unit})
        return bundle,plan,old,material.prepare(bundle,plan,response)

    def test_unit_request_does_not_see_source_facts(self):
        bundle,plan,_,_ = self.fixture()
        p = material.prepare_units(bundle,plan)
        self.assertNotIn('reading',p['state'])
        self.assertNotIn('pages',p['state'])
        self.assertEqual(p['state']['needs'],plan['needs'])

    def test_evidence_has_same_original_coverage_as_baseline(self):
        _,_,old,p = self.fixture()
        for field in ('question','needs','reading','rule_catalog'):
            self.assertEqual(p['state'][field],old['state'][field])
        self.assertEqual(len(p['questions']),1)
        self.assertTrue(p['rule_inventory'])

    def test_relation_and_reference_are_one_joint_choice(self):
        bundle,_,_,p = self.fixture()
        choice = next(c for c in p['questions']['evidence_0']['criteria'] if c.startswith('field_match|'))
        r = material.bind(p,answers(p['questions'],{'evidence_0':choice}),bundle)
        self.assertEqual(r['rows'][0]['relation'],'field_match')
        self.assertTrue(r['rows'][0]['binding'])
        self.assertIsNone(r['rows'][0]['world_event_verdict'])
        self.assertFalse(r['rows'][0]['truth_verified'])

    def test_source_identity_does_not_require_event_window_and_cannot_choose_rule(self):
        _,_,_,p = self.fixture('source_identity')
        self.assertIn('wrong event date',material.POLICIES['source_identity']['field_match'])
        for row in p['registry'][0]['choices'].values():
            if row['reference_id']:
                self.assertEqual(p['candidates'][row['reference_id']]['kind'],'source')

    def test_event_time_requires_actual_occurrence_not_publication(self):
        _,_,_,p = self.fixture('event_time')
        self.assertIn('ACTUALLY OCCURRED',material.POLICIES['event_time']['event_date_witness'])
        self.assertTrue(any(c.startswith('publication_date_only|') for c in p['questions']['evidence_0']['criteria']))
        self.assertFalse(any(c.startswith('field_match|') for c in p['questions']['evidence_0']['criteria']))

    def test_rule_definition_only_binds_to_original_rules(self):
        bundle,_,_,p = self.fixture('rule_definition')
        choice = next(c for c in p['questions']['evidence_0']['criteria'] if c.startswith('rule_match|'))
        r = material.bind(p,answers(p['questions'],{'evidence_0':choice}),bundle)
        self.assertEqual(r['rows'][0]['binding']['kind'],'rule')

    def test_none_keeps_entire_source_and_rule_inventory(self):
        bundle,_,_,p = self.fixture('event_occurrence')
        r = material.bind(p,answers(p['questions'],{'evidence_0':'NONE'}),bundle)
        self.assertEqual(r['reading'],p['state']['reading'])
        self.assertEqual(r['candidate_inventory'],p['candidates'])
        self.assertEqual(r['automatic_condition_closures'],0)

    def test_bad_unit_and_changed_body_are_rejected(self):
        bundle,plan,_,p = self.fixture()
        bad = answers(material.prepare_units(bundle,plan)['questions'],{'unit_0':'invented'})
        with self.assertRaises(ValueError):material.prepare(bundle,plan,bad)
        choice = next(c for c in p['questions']['evidence_0']['criteria'] if c.startswith('field_match|'))
        changed = copy.deepcopy(bundle); changed['pages'][next(iter(changed['pages']))]['content'] += 'changed'
        with self.assertRaisesRegex(ValueError,'saved_body_changed'):
            material.bind(p,answers(p['questions'],{'evidence_0':choice}),changed)


if __name__ == '__main__':unittest.main()
