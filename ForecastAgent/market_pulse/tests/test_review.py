"""Review-policy counterexamples for dates, units, context and dependencies."""
import copy
import unittest

from ForecastAgent.market_pulse import review, facts


def variables():
    return [{'fact_id': 'V1', 'metric': 'revenue', 'role': 'management_guidance',
        'normalized_value': 90e9, 'normalized_unit': 'USD', 'period_claim': 'Q1 FY2027'},
        {'fact_id': 'V2', 'metric': 'revenue', 'role': 'management_guidance',
        'normalized_value': 92e9, 'normalized_unit': 'USD', 'period_claim': 'Q1 FY2027'},
        {'fact_id': 'V3', 'metric': 'revenue', 'role': 'other_context',
        'normalized_value': 78e9, 'normalized_unit': 'USD',
        'period_interpretation': 'Original sequence; no fiscal dates or quarters inferred.'}]


def formula():
    return {'assumptions': [], 'calculations': [{'id': 'C1', 'operation': 'mean',
        'inputs': ['V1', 'V2'], 'explanation': 'Total guidance anchor'}], 'central_ref': 'C1',
        'scenarios': [], 'thesis': 'Management expectation, not a probability interval.',
        'limitations': ['No calibrated prediction interval.']}


class ReviewTests(unittest.TestCase):
    def test_unknown_period_cannot_receive_automatic_date_approval(self):
        answer = {'type': 'choice', 'choice': 'supported',
            'probabilities': {'supported': .99, 'conflict': .005, 'insufficient': .005}}
        response = {'answers': {'source_V1': answer, 'source_V2': answer}}
        selected = review.select_variables(variables(), response)
        self.assertEqual(selected['allowed_variable_ids'], ['V1', 'V2'])
        self.assertEqual(selected['withheld_variables'][0]['reason'], 'date_unknown_context_only')
        self.assertNotIn('source_V3', review.source_registry(variables()))

    def test_uncertain_semantics_are_withheld_even_with_binding(self):
        answer = {'choice': 'supported', 'probabilities': {'supported': .70, 'conflict': .15, 'insufficient': .15}}
        selected = review.select_variables(variables()[:1], {'answers': {'source_V1': answer}})
        self.assertFalse(selected['allowed_variable_ids'])

    def test_wrong_billion_scale_cannot_be_cancelled_in_a_ratio(self):
        raw = formula(); raw['assumptions'] = [
            {'id': 'A1', 'value': 62.5, 'unit': 'USD', 'rationale': 'Wrong scale', 'fact_refs': ['V1']}]
        with self.assertRaisesRegex(ValueError, 'Dimensional assumptions'):
            review.validate_formula(raw, variables(), ['V1', 'V2'], 'USD', {'metric': 'quarterly_revenue'})

    def test_unsupported_undated_reference_rejected(self):
        raw = formula(); raw['calculations'][0]['inputs'] = ['V3']
        with self.assertRaisesRegex(ValueError, 'withheld'):
            review.validate_formula(raw, variables(), ['V1', 'V2'], 'USD', {'metric': 'quarterly_revenue'})

    def test_guidance_cannot_be_ignored_in_central_revenue(self):
        raw = formula(); raw['calculations'][0]['inputs'] = ['V1']
        with self.assertRaisesRegex(ValueError, 'ignores supported'):
            review.validate_formula(raw, variables(), ['V1', 'V2'], 'USD', {'metric': 'quarterly_revenue'})
        out = review.validate_formula(formula(), variables(), ['V1', 'V2'], 'USD', {'metric': 'quarterly_revenue'})
        self.assertEqual(out['central_value'], 91e9)
        self.assertFalse(out['semantic_truth_verified'])

    def test_distinct_scenario_names_do_not_create_distinct_scenarios(self):
        raw = formula(); raw['scenarios'] = [
            {'name': 'low', 'calculation_ref': 'C1', 'interpretation': 'Low'},
            {'name': 'high', 'calculation_ref': 'C1', 'interpretation': 'High'}]
        with self.assertRaisesRegex(ValueError, 'same expression'):
            review.validate_formula(raw, variables(), ['V1', 'V2'], 'USD', {'metric': 'quarterly_revenue'})

    def test_distant_adjustment_footnote_survives_context_expansion(self):
        row = 'Diluted earnings per share was $1.85, up 13 percent year over year on an adjusted basis.\n'
        note = 'Non-GAAP measure excluding the one-time income tax charge recognized in the fourth quarter of 2024.\n'
        body = row + ('Other archived material.\n' * 100) + note
        ref = facts.original_ref('https://example.com/earnings', None, body, 0, len(row))
        v = {'fact_id': 'V1', 'metric': 'diluted_eps', 'basis': 'GAAP', 'role': 'actual',
            'source_unit': 'USD_per_share', 'raw_value': 1.85, 'normalized_value': 1.85,
            'normalized_unit': 'USD_per_share', 'conversion_factor': 1,
            'numeric_token': facts.numeric_tokens(row)[0], 'original_row_ref': ref,
            'unit_original_ref': ref, 'period_original_refs': [ref], 'period_claim': 'Q4 FY2025'}
        bundle = {'pages': {'https://example.com/earnings': {'content': body}}}
        view = review.context_catalog(bundle, [v])
        included = '\n'.join(r['original_text'] for r in view['original_evidence_library'])
        self.assertIn(note.strip(), included)
        for r in view['original_evidence_library']:
            self.assertEqual(r['original_text'], body[r['start']:r['end']])
        self.assertTrue(view['variables'][0]['source_ref_ids'])


if __name__ == '__main__':
    unittest.main()
