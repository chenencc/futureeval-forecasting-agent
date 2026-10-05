"""Applicability/truth isolation, provenance integrity and immutable claims."""
import copy
import unittest
from ForecastAgent.supplement import mercury_material_v2 as material
from ForecastAgent.tests.test_mercury_material import MercuryMaterial


def response(prepared, *, applicability='partial_record', evidence='partial_evidence', reference=None):
    answers = {}
    for row in prepared['registry']:
        values = {'reference':reference or next(iter(prepared['candidates'])),
                  'applicability':applicability, 'evidence':evidence}
        for kind, key in row['keys'].items():
            options = prepared['questions'][key]['criteria']
            choice = values[kind]
            answers[key] = {'type':'choice', 'choice':choice, 'confidence':1,
                           'probabilities':{o:float(o == choice) for o in options}}
    return {'model':'inception/mercury-decide:free', 'answers':answers}


class MercuryV2(unittest.TestCase):
    def fixture(self):
        bundle, plan, old = MercuryMaterial().fixture()
        return bundle, plan, old, material.prepare(bundle, plan)

    def test_coverage_and_rules_unchanged_while_heads_reduced(self):
        _, _, old, new = self.fixture()
        for field in ('question','needs','reading','rule_catalog'):
            self.assertEqual(old['state'][field],new['state'][field])
        self.assertEqual(len(new['questions']),3)
        self.assertTrue(all(c['kind'] == 'source' for c in new['candidates'].values()))
        self.assertTrue(new['rule_inventory'])

    def test_refutation_with_no_record_is_flagged_without_changing_claim(self):
        bundle, _, _, p = self.fixture()
        r = material.bind(p,response(p,applicability='no_record',evidence='exhaustive_refutation',reference='NONE'),bundle)
        row = r['rows'][0]
        self.assertIn('conclusion_applicability_disagreement',row['consistency_flags'])
        self.assertIn('conclusion_without_source_reference',row['consistency_flags'])
        self.assertIn('exhaustive_refutation_requires_coverage_audit',row['consistency_flags'])
        self.assertEqual(row['decisions']['evidence']['choice'],'exhaustive_refutation')
        self.assertEqual(r['automatic_condition_closures'],0)

    def test_support_with_mismatched_record_is_flagged_symmetrically(self):
        bundle, _, _, p = self.fixture()
        r = material.bind(p,response(p,applicability='mismatched_record',evidence='qualifying_witness'),bundle)
        self.assertIn('conclusion_applicability_disagreement',r['rows'][0]['consistency_flags'])

    def test_record_exclusion_is_not_event_refutation(self):
        bundle, _, _, p = self.fixture()
        r = material.bind(p,response(p,applicability='mismatched_record',evidence='nonqualifying_record'),bundle)
        self.assertFalse(r['rows'][0]['consistency_flags'])
        self.assertFalse(r['rows'][0]['truth_verified'])
        self.assertEqual(r['rows'][0]['condition_coverage'],'unverified')

    def test_none_does_not_drop_sources_or_rules(self):
        bundle, _, _, p = self.fixture()
        r = material.bind(p,response(p,applicability='no_record',evidence='insufficient',reference='NONE'),bundle)
        self.assertEqual(r['reading'],p['state']['reading'])
        self.assertEqual(r['rule_inventory'],p['rule_inventory'])
        self.assertIsNone(r['rows'][0]['observed_value'])

    def test_changed_source_or_rule_cannot_bind(self):
        bundle, _, _, p = self.fixture()
        changed = copy.deepcopy(bundle)
        changed['pages'][next(iter(changed['pages']))]['content'] += 'changed'
        with self.assertRaisesRegex(ValueError,'saved_body_changed'):
            material.bind(p,response(p),changed)
        changed = copy.deepcopy(bundle)
        changed['request']['resolution_criteria'] += 'changed'
        with self.assertRaisesRegex(ValueError,'rule_changed'):
            material.bind(p,response(p),changed)

    def test_independent_heads_have_complete_focus(self):
        _, _, _, p = self.fixture()
        self.assertNotIn('answers',p['state'])
        for q in p['questions'].values():
            self.assertIn('needs[0]',q['instructions'])
            self.assertIn('Texas',q['instructions'])
        self.assertIn('ENTIRE',material.EVIDENCE['exhaustive_refutation'])


if __name__ == '__main__':
    unittest.main()
