"""Independent fields, source-local arithmetic, scope review and exact replay."""
import copy
import unittest
from ForecastAgent.supplement import mercury_material_v7 as v7
from ForecastAgent.supplement import field_observations as values
from ForecastAgent.supplement import acquisition_ids as ids
from ForecastAgent.tests.test_mercury_material import MercuryMaterial
from ForecastAgent.tests.test_mercury_material_v3 import answers


class FieldScopes(unittest.TestCase):
    def fixture(self, condition='The observed price exceeds $5.016.', dimension='measure', target='$5.016',
                rule='The price exceeds $5.016 before September 6, 2026.',
                body='On October 1, 2026, the observed price was $4.41.'):
        bundle, _, _ = MercuryMaterial().fixture()
        bundle['request'] = {'question': 'Will this happen?', 'resolution_criteria': rule, 'fine_print': ''}
        url = next(iter(bundle['pages']))
        bundle['pages'][url]['content'] = body
        refs = [r['rule_id'] for r in ids.rule_catalog(bundle['request']) if r['field'] == 'resolution_criteria']
        plan = ids.bind_needs(bundle['request'], [{'id': 'n1', 'condition': condition,
            'critical': True, 'dimension': dimension, 'target': target, 'rule_ids': refs}])
        selection = v7.prepare_selection(bundle, plan)
        ref = next(k for k, v in selection['candidates'].items() if v['kind'] == 'source')
        selected = v7.bind_selection(selection, answers(selection['questions'], {'select_0': ref}), bundle)
        return bundle, plan, selection, v7.prepare_assessment(bundle, plan, selected)

    def bind(self, bundle, prepared, **choices):
        selected = {f'field_0_{a}': choices.get(a, next(iter(q['criteria'])))
                    for a in v7.AXES for q in [prepared['questions'][f'field_0_{a}']]}
        if 'field_0_value' in prepared['questions']:
            selected['field_0_value'] = choices.get('value', 'NONE')
        return v7.bind_assessment(prepared, answers(prepared['questions'], selected), bundle)

    def test_record_outside_time_keeps_identity_and_actual_value(self):
        b, _, _, p = self.fixture()
        value = next(v['value_id'] for v in p['registry'][0]['values'] if v['text'] == '$4.41')
        r = self.bind(b, p, temporal='record_outside', applicability='record_only_failure', value=value)
        row = r['rows'][0]
        self.assertTrue(row['useful_context_retained'])
        self.assertEqual(row['identity_disposition'], 'match')
        self.assertEqual(row['record_local_comparison']['ordering'], 'less')
        self.assertFalse(row['record_local_comparison']['world_event_refutation'])
        self.assertIsNone(row['world_event_verdict'])

    def test_missing_qualifier_retains_partial_primary_context(self):
        b, _, _, p = self.fixture(rule='At any point before September 1, 2026, a new restriction for centers of 50 MW applies.',
                                body='A directive requires an audit before additional connections are approved.')
        r = self.bind(b, p, observation='context', temporal='unestablished', applicability='partial_context')
        self.assertTrue(r['rows'][0]['useful_context_retained'])
        self.assertTrue(r['rows'][0]['review_required'])
        self.assertIn('conditional_review_pending', r['rows'][0]['consistency_flags'])

    def test_literal_date_does_not_receive_operative_effect_guard(self):
        b, _, _, p = self.fixture(condition='The reference date is July 8, 2026.', dimension='observation_time', target='2026-07-08',
                                rule='On July 8, 2026, the person is subject to a restriction that prevents standing.',
                                body='The article is dated July 8, 2026; the restriction has expired.')
        r = self.bind(b, p, observation='literal_only', temporal='literal_only', applicability='literal_only')
        self.assertFalse(r['rows'][0]['review_required'])
        self.assertEqual(v7.prepare_guard(p, r)['questions'], {})
        self.assertEqual(r['rows'][0]['ledger']['observation_obligation']['evidence_status'], 'unassessed')

    def test_every_program_conflict_has_consistent_pending_status(self):
        b, _, _, p = self.fixture(condition='The price as of June 30, 2026 exceeds $5.016.', rule='The price exceeds $5.016 by June 30, 2026.')
        r = self.bind(b, p, observation='unknown', temporal='unestablished', applicability='unestablished')
        self.assertEqual(r['rows'][0]['candidate_status'], 'needs_consistency_review')
        self.assertIn('snapshot_paraphrase_conflicts_with_original_boundary', r['rows'][0]['consistency_flags'])

    def test_counterevidence_review_preserves_initial_and_original_text(self):
        b, _, _, p = self.fixture()
        r = self.bind(b, p, temporal='record_outside', applicability='refutation')
        guard = v7.prepare_guard(p, r)
        revised = v7.bind_guard(r, guard, answers(guard['questions'], {'review_0': 'record_only_failure'}))
        row = revised['rows'][0]
        self.assertEqual(row['fields']['applicability'], 'refutation')
        self.assertEqual(row['scope_review']['choice'], 'record_only_failure')
        self.assertEqual(row['binding'], r['rows'][0]['binding'])
        self.assertIn('initial_and_review_disagree', row['consistency_flags'])
        self.assertEqual(row['candidate_status'], 'needs_consistency_review')

    def test_source_hash_and_missing_answer_are_enforced(self):
        b, _, selection, p = self.fixture()
        changed = copy.deepcopy(b)
        next(iter(changed['pages'].values()))['content'] += 'changed'
        with self.assertRaisesRegex(ValueError, 'body_changed'):
            self.bind(changed, p)
        with self.assertRaises(ValueError):
            v7.bind_selection(selection, {'model': 'inception/mercury-decide', 'answers': {}}, b)

    def test_selected_scope_is_explicit_and_full_selection_reading_retained(self):
        _, _, selection, p = self.fixture()
        self.assertEqual(p['selection_reading'], selection['state']['reading'])
        self.assertEqual(p['state']['coverage_notice']['scope'], 'selected_exact_spans_only')
        self.assertNotIn('observation_ledgers', p['state'])
        self.assertNotIn('operator_inventory', p['state'])

    def test_rule_or_empty_binding_cannot_supply_observed_support(self):
        b, plan, selection, _ = self.fixture()
        selected = v7.bind_selection(selection, answers(selection['questions'], {'select_0': 'NONE'}), b)
        p = v7.prepare_assessment(b, plan, selected)
        r = self.bind(b, p, applicability='support')
        self.assertIn('event_claim_without_source', r['rows'][0]['consistency_flags'])
        self.assertEqual(r['automatic_condition_closures'], 0)


class Arithmetic(unittest.TestCase):
    def test_explicit_units_convert_and_unknown_units_remain_unknown(self):
        self.assertEqual(values.quantity('2 GW'), {'value': '2000', 'unit': 'MW'})
        self.assertEqual(values.quantity('$300 billion'), {'value': '300000000000', 'unit': 'USD'})
        self.assertEqual(values.quantity('5%'), {'value': '5', 'unit': '%'})
        self.assertIsNone(values.quantity('5 EUR'))
        self.assertIsNone(values.parse_date('June 30'))

    def test_date_order_is_record_local(self):
        item = {'kind': 'date', 'normalized': '2026-10-01'}
        r = values.compare(item, '2026-09-06')
        self.assertEqual(r['ordering'], 'greater')
        self.assertTrue(r['record_local_only'])
        self.assertFalse(r['world_event_refutation'])
        self.assertEqual(values.parse_date('June 30th, 2026'), '2026-06-30')


if __name__ == '__main__':
    unittest.main()
