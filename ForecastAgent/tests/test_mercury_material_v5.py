"""Typed root preservation, literal separation and fallible rule alignment."""
import copy
import unittest
from ForecastAgent.supplement import mercury_material_v5 as v5
from ForecastAgent.tests.test_mercury_material import MercuryMaterial
from ForecastAgent.tests.test_mercury_material_v3 import answers


class TypedRoots(unittest.TestCase):
    def fixture(self, kind='observation', alignment='aligned'):
        bundle, plan, _ = MercuryMaterial().fixture()
        p = v5.prepare_contract(bundle, plan)
        selected = {k: next(iter(q['criteria'])) for k, q in p['questions'].items()}
        selected.update(contract_0_kind=kind, contract_0_alignment=alignment)
        contract = v5.bind_contract(p, answers(p['questions'], selected))
        return bundle, plan, v5.prepare(bundle, plan, contract)

    def bind(self, b, p, prefix):
        choice = next(c for c in p['questions']['root_0']['criteria'] if c.startswith(prefix))
        return v5.bind(p, answers(p['questions'], {'root_0': choice}), b)['rows'][0]

    def test_rules_only_contract_and_unchanged_material(self):
        b, plan, p = self.fixture()
        c = v5.prepare_contract(b, plan)
        self.assertNotIn('reading', c['state'])
        self.assertNotIn('pages', c['state'])
        old = v5.v1.prepare(b, plan)
        for field in ('question', 'needs', 'reading', 'rule_catalog'):
            self.assertEqual(p['state'][field], old['state'][field])
        self.assertEqual(p['registry'][0]['condition'], plan['needs'][0]['condition'])

    def test_parameter_and_identity_never_replace_world_observations(self):
        b, _, p = self.fixture()
        for prefix in ('parameter_context|', 'literal_identity|'):
            row = self.bind(b, p, prefix)
            self.assertEqual(row['candidate_status'], 'needs_consistency_review')
            self.assertFalse(row['truth_verified'])

    def test_literal_parameter_is_not_forced_to_external_event_coherence(self):
        b, _, p = self.fixture('parameter')
        row = self.bind(b, p, 'parameter_context|')
        self.assertEqual(row['candidate_status'], 'literal_rule_candidate_unverified')
        self.assertEqual(row['binding']['kind'], 'rule')
        self.assertIsNone(row['world_event_verdict'])

    def test_literal_identity_is_kept_without_certifying_event(self):
        b, _, p = self.fixture('identity')
        row = self.bind(b, p, 'literal_identity|')
        self.assertEqual(row['candidate_status'], 'identity_candidate_unverified')
        self.assertEqual(row['binding']['kind'], 'source')
        self.assertFalse(row['truth_verified'])

    def test_rule_conflict_preserves_root_but_blocks_candidate_status(self):
        b, plan, p = self.fixture(alignment='conflicting')
        row = self.bind(b, p, 'explicit|')
        self.assertEqual(row['original_condition'], plan['needs'][0]['condition'])
        self.assertIn('need_rule_equivalence_unestablished', row['consistency_flags'])
        self.assertEqual(row['candidate_status'], 'needs_consistency_review')

    def test_every_head_and_source_identity_are_checked(self):
        b, _, p = self.fixture()
        changed = copy.deepcopy(b)
        changed['pages'][next(iter(b['pages']))]['content'] += 'changed'
        with self.assertRaisesRegex(ValueError, 'saved_body_changed'):
            self.bind(changed, p, 'explicit|')
        with self.assertRaises(ValueError):
            v5.bind(p, {'model': 'inception/mercury-decide:free', 'answers': {}}, b)

    def test_contract_cannot_change_condition_or_duplicate_need(self):
        b, plan, p = self.fixture()
        contract = copy.deepcopy(p['contract'])
        contract['rows'][0]['original_condition'] = 'Changed polarity'
        with self.assertRaisesRegex(ValueError, 'original_condition_changed'):
            v5.prepare(b, plan, contract)
        contract = copy.deepcopy(p['contract'])
        contract['rows'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            v5.prepare(b, plan, contract)


if __name__ == '__main__':
    unittest.main()
