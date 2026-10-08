"""Source-bound assumption, dimensional uncertainty and independence regressions."""
import copy
import unittest
from unittest.mock import patch

from ForecastAgent.market_pulse import compact_trial as c, derivation as d
from ForecastAgent.market_pulse.tests.test_derivation import variable, assumptions


class CompactFinancialTests(unittest.TestCase):
    def eps(self):
        vs = [variable('V1', 'diluted_eps', 1.85, 'USD_per_share', 'Q4 FY2025'),
            variable('V2', 'growth_rate', .22, 'fraction', 'Q3 FY2026')]
        t = d.templates(vs, ['V1', 'V2'], {'metric': 'gaap_diluted_eps', 'target_period': 'Q4 FY2026'})[0]
        return vs, t

    def choice(self, g=.15, refs=None):
        return {**assumptions(g=g), 'growth_evidence_refs': ['V2'] if refs is None else refs}

    def test_growth_binds_to_a_predictor_without_claiming_future_truth(self):
        vs, t = self.eps()
        result = c.evaluate_assumptions(self.choice(), t, vs, ['V1', 'V2'])
        self.assertAlmostEqual(result['central_value'], 2.1275)
        self.assertFalse(result['future_assumptions_verified'])
        self.assertTrue(result['predictor_binding_is_not_future_truth'])

    def test_nonzero_growth_without_a_premise_is_rejected(self):
        vs, t = self.eps()
        with self.assertRaisesRegex(ValueError, 'Nonzero growth'):
            c.evaluate_assumptions(self.choice(refs=[]), t, vs, ['V1', 'V2'])

    def test_withheld_premise_is_rejected(self):
        vs, t = self.eps()
        with self.assertRaisesRegex(ValueError, 'Withheld'):
            c.evaluate_assumptions(self.choice(), t, vs, ['V1'])

    def test_duplicate_refs_are_rejected(self):
        vs, t = self.eps()
        with self.assertRaisesRegex(ValueError, 'Unique'):
            c.evaluate_assumptions(self.choice(refs=['V2', 'V2']), t, vs, ['V1', 'V2'])

    def test_cost_disappearance_cannot_enter_the_center(self):
        vs, t = self.eps(); choice = self.choice()
        choice['cost_removed_fraction'] = .5
        with self.assertRaisesRegex(ValueError, 'sensitivity-only'):
            c.evaluate_assumptions(choice, t, vs, ['V1', 'V2'])

    def test_future_limitations_required(self):
        vs, t = self.eps(); choice = self.choice()
        choice['limitations'] = []
        with self.assertRaisesRegex(ValueError, 'limitations'):
            c.evaluate_assumptions(choice, t, vs, ['V1', 'V2'])

    def test_tax_guidance_is_valid_margin_context_not_future_proof(self):
        vs, t = self.eps()
        vs.append(variable('V3', 'tax_rate', .15, 'fraction', 'Q4 FY2026', 'management_guidance'))
        choice = self.choice(g=0, refs=['V3'])
        result = c.evaluate_assumptions(choice, t, vs, ['V1', 'V2', 'V3'])
        self.assertEqual(result['central_value'], 1.85)
        self.assertFalse(result['future_assumptions_verified'])

    def test_irrelevant_metric_cannot_justify_growth(self):
        vs, t = self.eps()
        vs.append(variable('V3', 'unrelated_web_count', 7, 'fraction', 'Q4 FY2026'))
        with self.assertRaisesRegex(ValueError, 'earnings or operating'):
            c.evaluate_assumptions(self.choice(refs=['V3']), t, vs, ['V1', 'V2', 'V3'])

    def test_schema_eliminates_inapplicable_cost_and_share_choices(self):
        vs, t = self.eps()
        p = c.assumption_tool(t, ['V1', 'V2'])['function']['parameters']['properties']
        self.assertEqual(p['cost_removed_fraction']['enum'], [0])
        self.assertEqual(p['share_change_rate']['enum'], [0])

    def test_eps_uncertainty_does_not_compare_eps_with_raw_revenue(self):
        t = {'method': 'net_margin_projection', 'refs': {
            'guidance_lower': 'V1', 'guidance_upper': 'V2'},
            'canonical_inputs': {'V1': {'value': 61e9}, 'V2': {'value': 64e9}}}
        qs = {'0.1': {'value': 5.9}, '0.5': {'value': 6.3}, '0.9': {'value': 7.2}}
        with patch.object(c.base, 'quantile', side_effect=lambda a, b, p: qs[str(p)]), \
                patch.object(c.prior.finance, 'cdf_at') as interpolate:
            result = c.uncertainty_audit({}, {'continuous_cdf': []}, t)
        interpolate.assert_not_called()
        self.assertIsNone(result['guidance_width'])
        self.assertIsNone(result['predictive_to_guidance_width_ratio'])

    def test_guidance_concentration_is_flagged_without_editing_probabilities(self):
        t = {'method': 'guidance_midpoint', 'refs': {
            'guidance_lower': 'V1', 'guidance_upper': 'V2'},
            'canonical_inputs': {'V1': {'value': 90}, 'V2': {'value': 92}}}
        qs = {'0.1': {'value': 90.8}, '0.5': {'value': 90.9}, '0.9': {'value': 91}}
        candidate = {'continuous_cdf': [.02, .5, .98]}; original = copy.deepcopy(candidate)
        with patch.object(c.base, 'quantile', side_effect=lambda a, b, p: qs[str(p)]), \
                patch.object(c.prior.finance, 'cdf_at', side_effect=[.02, .98]):
            result = c.uncertainty_audit({}, candidate, t)
        self.assertTrue(any('concentration' in f for f in result['flags']))
        self.assertEqual(candidate, original)
        self.assertFalse(result['calibration_validated'])

    def test_one_outcome_head_does_not_include_prior_scores(self):
        registry = c.outcome_questions({'criteria': {'below': 'below', 'above': 'above'}})
        self.assertEqual(list(registry), ['event_outcome'])
        self.assertIn('No analyst prediction', registry['event_outcome']['instructions'])


if __name__ == '__main__':
    unittest.main()
