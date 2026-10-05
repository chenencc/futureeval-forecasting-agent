"""Observation preservation and exact original operator binding under bad labels."""
import copy
import unittest
from ForecastAgent.supplement import mercury_material_v6 as v6
from ForecastAgent.supplement import rule_operator_contracts as operators
from ForecastAgent.supplement import acquisition_ids as ids
from ForecastAgent.tests.test_mercury_material import MercuryMaterial
from ForecastAgent.tests.test_mercury_material_v3 import answers


class OperatorLedgers(unittest.TestCase):
    def fixture(self, rule='The measured price must exceed $5 by June 30, 2026.',
                condition='The observed price exceeds $5.', kind='parameter'):
        bundle, _, _ = MercuryMaterial().fixture()
        bundle['request'] = {'question': 'Will the measured condition hold?', 'resolution_criteria': rule, 'fine_print': ''}
        refs = [r['rule_id'] for r in ids.rule_catalog(bundle['request']) if r['field'] == 'resolution_criteria']
        plan = ids.bind_needs(bundle['request'], [{'id':'n1','condition':condition,'critical':True,
            'dimension':'measure','target':'$5','rule_ids':refs}])
        c = v6.prepare_contract(bundle, plan)
        selected = {k: next(iter(q['criteria'])) for k,q in c['questions'].items()}
        selected['contract_0_kind'] = kind
        selected['contract_0_alignment'] = 'aligned'
        if any(k.startswith('any_time_by|') for k in c['questions']['contract_0_time']['criteria']):
            selected['contract_0_time'] = next(k for k in c['questions']['contract_0_time']['criteria'] if k.startswith('any_time_by|'))
        contract = v6.bind_contract(c, answers(c['questions'], selected))
        return bundle, plan, c, v6.prepare(bundle, plan, contract)

    def test_bad_parameter_label_cannot_erase_observed_predicate(self):
        b, plan, _, p = self.fixture()
        choice = next(k for k in p['questions']['root_0']['criteria'] if k.startswith('parameter_context|'))
        r = v6.bind(p, answers(p['questions'], {'root_0': choice}), b)
        ledger = r['rows'][0]['ledger']
        self.assertEqual(ledger['observation_obligation']['original_condition'], plan['needs'][0]['condition'])
        self.assertEqual(ledger['observation_obligation']['evidence_status'], 'unassessed')
        self.assertEqual(ledger['requested_parameters']['status'], 'requested_constraints_not_observed_values')
        self.assertEqual(ledger['literal_context']['binding']['kind'], 'rule')
        self.assertEqual(r['automatic_condition_closures'], 0)

    def test_by_rule_does_not_offer_unanchored_as_of_and_flags_old_snapshot(self):
        b, _, c, p = self.fixture(condition='The measured price as of June 30, 2026 exceeds $5.')
        self.assertFalse(any(k.startswith('as_of|') for k in c['questions']['contract_0_time']['criteria']))
        self.assertIn('snapshot_operator_not_bound_to_original_rule', p['registry'][0]['program_risks'])
        operators.verify(p['contract']['operator_inventory'], b)

    def test_negative_root_without_selected_negative_span_is_not_silently_aligned(self):
        _, _, _, p = self.fixture(rule='The restriction is not reversed before June 30, 2026.',
            condition='The restriction is not reversed before June 30, 2026.')
        self.assertIn('negative_operator_not_bound_to_original_rule', p['registry'][0]['program_risks'])

    def test_every_operator_ref_is_exact_original_text(self):
        b, _, _, p = self.fixture()
        self.assertTrue(p['registry'][0]['operator_bindings'])
        for span in p['registry'][0]['operator_bindings']:
            self.assertEqual(b['request'][span['field']][span['start']:span['end']], span['text'])
        changed = copy.deepcopy(b)
        changed['request']['resolution_criteria'] += 'changed'
        with self.assertRaisesRegex(ValueError,'operator_span_changed'):
            operators.verify(p['contract']['operator_inventory'], changed)

    def test_source_explicit_requires_scope_review_and_keeps_raw_claim(self):
        b, _, _, p = self.fixture(kind='observation')
        choice = next(k for k in p['questions']['root_0']['criteria'] if k.startswith('explicit|'))
        initial = v6.bind(p, answers(p['questions'], {'root_0':choice}), b)
        self.assertEqual(initial['rows'][0]['candidate_status'], 'pending_original_rule_scope_review')
        guard = v6.prepare_guard(p, initial)
        for decision in ('contradicted','unestablished','literal_only'):
            revised = v6.bind_guard(initial, guard, answers(guard['questions'], {'scope_0':decision}))
            row = revised['rows'][0]
            self.assertEqual(row['relation'], 'explicit')
            self.assertEqual(row['candidate_status'], 'needs_consistency_review')
            self.assertFalse(row['truth_verified'])
            if decision=='literal_only':
                self.assertEqual(row['ledger']['observation_obligation']['evidence_status'],'unassessed')

    def test_active_state_and_effect_have_program_owned_original_refs(self):
        _, _, c, _ = self.fixture(rule='On July 8, 2026, the person is subject to a restriction that prevents them from standing.')
        self.assertTrue(any(k.startswith('active_state|') for k in c['questions']['contract_0_effect']['criteria']))
        self.assertTrue(any(k.startswith('operative_effect|') for k in c['questions']['contract_0_effect']['criteria']))


if __name__=='__main__':
    unittest.main()
