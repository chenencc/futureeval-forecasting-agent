"""Counterexamples for guidance targets, physical units and open tails."""
import copy
from decimal import Decimal
import unittest

from ForecastAgent.market_pulse import guidance, held_review
from ForecastAgent.market_pulse.tests.test_analysis import request


def guidance_request(label='Revenue', unit='Billion $', release='Q3 FY2027', target='Q4 FY2027'):
    return {'question': f"What will be Acme's forward guidance in its {release} earnings release? ({label})",
        'unit': unit, 'background': '',
        'resolution_criteria': f"This question will resolve as the midpoint of the guidance range for {target} as stated in Acme's Outlook section of its {release} earnings press release. Revenues are rounded to the nearest billion. GAAP gross margin will be rounded to the nearest tenth of a percent. GAAP operating expenses will be rounded to the nearest tenth of a billion dollars.",
        'fine_print': '* If Acme does not issue guidance for Q3 FY2027 in the Q2 FY2027 release, the subquestion will be annulled.'}


class GuidanceReviewTests(unittest.TestCase):
    def test_previous_annulment_condition_does_not_replace_future_target(self):
        r = guidance_request(); before = copy.deepcopy(r)
        out = guidance.contract(r)
        self.assertEqual(out['target_guidance_period'], 'Q4 FY2027')
        self.assertEqual(out['publishing_release_period'], 'Q3 FY2027')
        condition = out['administrative_conditions'][0]
        self.assertEqual(condition['fiscal_periods'], ['Q3 FY2027','Q2 FY2027'])
        self.assertFalse(condition['changes_numeric_target'])
        self.assertFalse(out['delivery_authorized_by_adapter'])
        self.assertEqual(r, before)

    def test_publishing_release_conflict_is_rejected(self):
        r = guidance_request(); r['question'] = r['question'].replace('Q3 FY2027','Q2 FY2027')
        with self.assertRaises(ValueError): guidance.contract(r)

    def test_non_gaap_leaf_is_not_silently_accepted_as_gaap(self):
        with self.assertRaises(ValueError): guidance.contract(guidance_request('Gross margin (non-GAAP)','%'))

    def test_currency_and_percent_dimensions_are_not_interchangeable(self):
        with self.assertRaises(ValueError): guidance.contract(guidance_request('Gross margin (GAAP)'))
        with self.assertRaises(ValueError): guidance.convert(74,'USD_billions','percentage_points')
        self.assertEqual(guidance.convert(.74,'fraction','percentage_points'), Decimal('74'))
        self.assertEqual(guidance.convert(108000,'USD_millions','USD_billions'), Decimal('108'))

    def test_fiscal_year_rollover(self):
        out = guidance.contract(guidance_request(release='Q4 FY2027',target='Q1 FY2028'))
        self.assertEqual(out['target_guidance_period'],'Q1 FY2028')
        with self.assertRaises(ValueError): guidance.contract(guidance_request(target='Q2 FY2028'))

    def test_midpoint_conversion_and_rounding_are_rule_transforms(self):
        result = guidance.resolving_value(107000,109000,source_unit='USD_millions',target_unit='USD_billions',step=1)
        self.assertEqual(Decimal(result['rounded']),Decimal(108))
        self.assertFalse(result['is_forecast'])
        single = guidance.resolving_value(.7404,None,source_unit='fraction',target_unit='percentage_points',step=.1)
        self.assertEqual(Decimal(single['rounded']), Decimal('74.0'))
        with self.assertRaises(ValueError): guidance.resolving_value(109,107,source_unit='USD_billions',target_unit='USD_billions',step=1)

    def test_exact_halfway_is_marked_for_review(self):
        result = guidance.resolving_value(9.25,None,source_unit='USD_billions',target_unit='USD_billions',step=.1)
        self.assertTrue(result['exact_halfway_tie_requires_review'])

    def test_readable_thresholds_keep_grid_and_raw_dollar_units(self):
        r = request(); r['scaling']['range_min'] = 10.5e9; r['scaling']['range_max'] = 11.6e9
        r['scaling'].pop('continuous_range',None)
        before=copy.deepcopy(r)
        spec=held_review.readable_spec(r)
        self.assertEqual(spec['edges'][0],10.5e9)
        self.assertEqual(spec['edges'][-1],11.6e9)
        self.assertIn('USD 10.5 billion',spec['criteria']['below'])
        self.assertIn('no upper cap',spec['criteria']['above'])
        self.assertEqual(r,before)

    def test_numeric_diagnostic_cannot_hide_invalid_output_schema(self):
        result = {'rationale':'A diagnostic','limitations':'Schema violation', 'source_ref_ids':['R3']}
        self.assertEqual(held_review.physical_schema_issues(result),['limitations_not_string_array'])
        result['limitations']=['Uncertain forecast']
        self.assertEqual(held_review.physical_schema_issues(result),[])


if __name__ == '__main__': unittest.main()
