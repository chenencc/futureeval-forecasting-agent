"""Finite-choice integrity, independent-head contradictions, and recall retention."""
import copy
import unittest
from ForecastAgent.supplement import mercury_material as material
from ForecastAgent.tests import test_acquisition_provenance as fixtures


def response(prepared, *, origin='source_fact', scope='field_only', relation='support', reference=None):
    answers = {}
    for row in prepared['registry']:
        values = {'origin': origin, 'scope': scope, 'relation': relation,
                  'reference': reference or next(r for r, c in prepared['candidates'].items() if c['kind'] == 'source')}
        for kind, key in row['keys'].items():
            options = prepared['questions'][key]['criteria']
            choice = values[kind]
            answers[key] = {'type': 'choice', 'choice': choice, 'confidence': 1,
                           'probabilities': {o: float(o == choice) for o in options}}
    return {'model': 'inception/mercury-decide:free', 'answers': answers}


class MercuryMaterial(unittest.TestCase):
    def fixture(self):
        request, plan, pages, _, _ = fixtures.Provenance().fixture()
        bundle = {'request': request, 'pages': pages}
        return bundle, plan, material.prepare(bundle, plan)

    def test_questions_name_complete_target_and_state_paths(self):
        _, _, prepared = self.fixture()
        self.assertEqual(len(prepared['questions']), 4)
        for q in prepared['questions'].values():
            self.assertIn('needs[0]', q['instructions'])
            self.assertIn('Texas', q['instructions'])
            self.assertEqual(q['type'], 'choice')

    def test_literal_reference_selection_never_certifies_condition(self):
        bundle, _, p = self.fixture()
        result = material.bind(p, response(p, scope='condition_supported'), bundle)
        row = result['rows'][0]
        self.assertTrue(row['text_identity_verified'])
        self.assertEqual(row['condition_coverage'], 'unverified')
        self.assertIsNone(row['observed_value'])
        self.assertIsNone(row['rationale'])
        self.assertEqual(result['automatic_condition_closures'], 0)

    def test_same_reading_and_needs_as_existing_baseline(self):
        bundle, plan, p = self.fixture()
        from ForecastAgent.supplement import acquisition_contract as base
        self.assertEqual(p['state']['reading'], base.reading_packet(bundle['pages']))
        self.assertEqual(p['state']['needs'], plan['needs'])
        self.assertEqual(p['state']['question'], bundle['request'])

    def test_question_answers_are_not_hidden_dependencies(self):
        _, _, p = self.fixture()
        for q in p['questions'].values():
            self.assertNotIn('selected answer', q['instructions'])
        self.assertNotIn('answers', p['state'])

    def test_head_disagreement_is_retained_not_silently_fixed(self):
        bundle, _, p = self.fixture()
        r = material.bind(p, response(p, origin='rule_constant', scope='condition_supported'), bundle)
        self.assertIn('rule_origin_reference_disagreement', r['rows'][0]['consistency_flags'])
        self.assertIn('condition_origin_disagreement', r['rows'][0]['consistency_flags'])
        self.assertEqual(r['rows'][0]['decisions']['scope']['choice'], 'condition_supported')

    def test_rule_definition_binds_to_rule_not_page(self):
        bundle, _, p = self.fixture()
        ref = next(k for k, v in p['candidates'].items() if v['kind'] == 'rule')
        r = material.bind(p, response(p, origin='rule_constant', reference=ref), bundle)
        self.assertEqual(r['rows'][0]['binding']['kind'], 'rule')

    def test_none_selection_preserves_all_saved_material(self):
        bundle, _, p = self.fixture()
        r = material.bind(p, response(p, origin='insufficient', relation='insufficient', scope='insufficient', reference='NONE'), bundle)
        self.assertIsNone(r['rows'][0]['binding'])
        self.assertEqual(r['reading'], p['state']['reading'])
        self.assertTrue(r['candidate_inventory'])

    def test_changed_capture_cannot_bind(self):
        bundle, _, p = self.fixture()
        bundle['pages'][next(iter(bundle['pages']))]['content'] += 'changed'
        with self.assertRaisesRegex(ValueError, 'saved_body_changed'):
            material.bind(p, response(p), bundle)

    def test_changed_rules_fail_before_any_provider_call(self):
        bundle, plan, _ = self.fixture()
        bundle['request']['resolution_criteria'] += 'changed'
        with self.assertRaisesRegex(ValueError, 'rule_changed_after_planning'):
            material.prepare(bundle, plan)

    def test_fabricated_choice_or_probability_is_rejected(self):
        bundle, _, p = self.fixture()
        r = response(p)
        first = next(iter(r['answers']))
        r['answers'][first]['choice'] = 'fabricated'
        with self.assertRaisesRegex(ValueError, 'Invalid choice'):
            material.bind(p, r, bundle)
        r = response(p); r['answers'][first]['probabilities']['source_fact'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'Invalid probability'):
            material.bind(p, r, bundle)

    def test_extra_or_missing_question_cannot_be_silently_ignored(self):
        bundle, _, p = self.fixture(); r = response(p)
        r['answers']['invented'] = copy.deepcopy(next(iter(r['answers'].values())))
        with self.assertRaisesRegex(ValueError, 'unexpected_or_missing_decision_ids'):
            material.bind(p, r, bundle)
        r = response(p); r['answers'].pop(next(iter(r['answers'])))
        with self.assertRaisesRegex(ValueError, 'Missing or mismatched'):
            material.bind(p, r, bundle)

    def test_degenerate_empty_plan_is_not_success(self):
        bundle, plan, _ = self.fixture(); plan['needs'] = []
        with self.assertRaisesRegex(ValueError, 'empty_plan_or_reading'):
            material.prepare(bundle, plan)


if __name__ == '__main__':
    unittest.main()
