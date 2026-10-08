"""Financial-period, unit and immutable-input acceptance for the analysis pilot."""
import copy
import unittest

from ForecastAgent.market_pulse import analysis as a


def request():
    return {'id': '99', 'question': 'First quarterly revenue (Microsoft)',
        'resolution_criteria': '* Apple Q4 FY2026 October 29\n* Microsoft Q1 FY 2027 October 29',
        'fine_print': 'First publication controls; no qualifying publication means annulment.',
        'background': 'Last eight quarters in chronological order, in millions of USD:\n* Microsoft: 65,585; 90,007',
        'question_type': 'numeric', 'unit': '$', 'inbound_outcome_count': 200,
        'open_lower_bound': True, 'open_upper_bound': True,
        'scaling': {'range_min': 90200000000, 'range_max': 94500000000, 'zero_point': None}}


class FinancialAnalysisTests(unittest.TestCase):
    def test_fiscal_period_and_original_quote_are_target_bound(self):
        r = request(); c = a.contract(r)
        self.assertEqual(c['target_period'], 'Q1 FY 2027')
        ref = c['target_period_refs'][0]
        self.assertEqual(ref['quote'], r[ref['field']][ref['start']:ref['end']])
        self.assertNotIn('Apple', ref['quote'])

    def test_dollars_require_explicit_million_unit_without_invented_dates(self):
        r = request(); c = a.contract(r)['history_from_original_background']
        self.assertEqual(c['values_in_question_units'], [65585000000, 90007000000])
        self.assertIn('no dates inferred', c['chronology'])
        r['background'] = r['background'].replace('in millions of USD', 'unit unspecified')
        self.assertIsNone(a.contract(r)['history_from_original_background'])

    def test_duplicate_target_period_is_rejected(self):
        r = request(); r['resolution_criteria'] += '\n* Microsoft Q2 FY2027'
        with self.assertRaises(ValueError): a.contract(r)

    def test_unusable_and_identical_bodies_excluded_without_snapshot_mutation(self):
        body = 'Microsoft quarterly revenue was $90 billion in the quarter ended June 30, 2026. ' * 15
        bundle = {'request': request(), 'pages': {
            'https://example.com/report': {'content': body},
            'https://example.com/report-copy': {'content': body},
            'https://www.sec.gov/search-filings': {'content': body}}, 'gaps': [{'missing': 'guidance'}]}
        before = copy.deepcopy(bundle); view, excluded = a.analysis_view(bundle)
        self.assertEqual(bundle, before)
        self.assertEqual(len(view['pages']), 1)
        self.assertEqual({r['reason'] for r in excluded}, {'identical_saved_body', 'search_navigation_or_social_share'})
        self.assertEqual(view['gaps'], bundle['gaps'])

    def test_open_tail_quantile_is_unknown_not_a_fabricated_boundary(self):
        cdf = [.6 + .2*i/200 for i in range(201)]
        self.assertIsNone(a.quantile(cdf, request(), .5)['value'])
        self.assertEqual(a.quantile(cdf, request(), .5)['state'], 'below_platform_range')
        self.assertEqual(a.quantile(cdf, request(), .9)['state'], 'above_platform_range')

    def test_decisions_input_output_usage_without_total_is_not_zero_or_unknown(self):
        usage = a.usage_audit([{'response': {'usage': {'input_tokens': 100, 'output_tokens': 1, 'cost': 0}}}])
        self.assertEqual(usage['known_tokens'], 101)
        self.assertEqual(usage['usage_missing_attempts'], 0)
        self.assertEqual(usage['reported_cost_usd'], 0)


if __name__ == '__main__': unittest.main()
