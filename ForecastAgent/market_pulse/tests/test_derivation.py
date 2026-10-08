"""Typed templates prevent rate-as-level and fiscal-proxy mistakes."""
import unittest
from ForecastAgent.market_pulse import derivation as d


def variable(i,metric,value,unit,period,role='actual',quote='',basis='GAAP'):
    return {'fact_id':i,'metric':metric,'normalized_value':value,'normalized_unit':unit,
        'period_interpretation':period,'role':role,'basis':basis,'original_row_ref':{'quote':quote}}


def assumptions(i='T1',g=0,cost=0,shares=0):
    return {'template_id':i,'growth_rate':g,'cost_removed_fraction':cost,'share_change_rate':shares,
        'growth_reason':'Unverified future growth assumption.','cost_reason':'Explicit cost recurrence assumption.',
        'share_reason':'Shares persistence assumption.','limitations':['Not calibrated.']}


class TypedDerivationTests(unittest.TestCase):
    def test_fifteen_percent_growth_multiplies_by_one_point_fifteen(self):
        vs=[variable('V1','diluted_eps',1.85,'USD_per_share','Q4 FY2025')]
        options=d.templates(vs,['V1'],{'metric':'gaap_diluted_eps','target_period':'Q4 FY2026'})
        result=d.evaluate(assumptions(g=.15),options)
        self.assertAlmostEqual(result['central_value'],2.1275)
        self.assertFalse(result['calibration_validated'])

    def test_adjacent_quarter_is_not_yoy_comparable(self):
        vs=[variable('V1','diluted_eps',1.57,'USD_per_share','Q3 FY2025')]
        self.assertFalse(d.templates(vs,['V1'],{'metric':'gaap_diluted_eps','target_period':'Q4 FY2026'}))

    def test_adjusted_eps_is_not_gaap_comparable(self):
        vs=[variable('V1','diluted_eps',1.85,'USD_per_share','Q4 FY2025',basis='adjusted_or_nonGAAP')]
        self.assertFalse(d.templates(vs,['V1'],{'metric':'gaap_diluted_eps','target_period':'Q4 FY2026'}))

    def test_guidance_from_different_quarters_cannot_form_midpoint(self):
        vs=[variable('V1','revenue',90e9,'USD','Q1 FY2027','management_guidance'),
            variable('V2','revenue',92e9,'USD','Q2 FY2027','management_guidance')]
        self.assertFalse(d.templates(vs,['V1','V2'],{'metric':'quarterly_revenue','target_period':'Q1 FY2027'}))

    def test_guidance_yoy_growth_cannot_be_applied_again_to_guidance(self):
        vs=[variable('V1','revenue',197e9,'USD','Q3 FY2026','management_guidance'),
            variable('V2','revenue',202e9,'USD','Q3 FY2026','management_guidance')]
        options=d.templates(vs,['V1','V2'],{'metric':'quarterly_revenue','target_period':'Q3 FY2026'})
        with self.assertRaisesRegex(ValueError,'already includes growth'):
            d.evaluate(assumptions(g=.105),options)
        self.assertEqual(d.evaluate(assumptions(),options)['central_value'],199.5e9)

    def margin_options(self):
        vs=[variable('V1','revenue',60e9,'USD','Q3 FY2026','management_guidance'),
            variable('V2','revenue',64e9,'USD','Q3 FY2026','management_guidance'),
            variable('V3','net_income',15e9,'USD','Q2 FY2026'),
            variable('V4','revenue',60e9,'USD','Q2 FY2026'),
            variable('V5','diluted_shares',2.5e9,'shares','Q2 FY2026'),
            variable('V6','one_off',2e9,'USD','Q2 FY2026',quote='Pretax severance expense.'),
            variable('V7','tax_rate',.15,'fraction','Q3 FY2026','management_guidance'),
            variable('V8','tax_rate',.17,'fraction','Q3 FY2026','management_guidance'),
            variable('V9','one_off',15.93e9,'USD','Q3 FY2025',quote='Income tax charge.')]
        return d.templates(vs,[v['fact_id'] for v in vs],{'metric':'gaap_diluted_eps','target_period':'Q3 FY2026'})

    def test_reported_net_income_is_not_taxed_twice(self):
        result=d.evaluate(assumptions(),self.margin_options())
        self.assertAlmostEqual(result['central_value'],6.2)

    def test_only_incremental_pretax_cost_receives_tax_once(self):
        result=d.evaluate(assumptions(cost=1),self.margin_options())
        self.assertAlmostEqual(result['central_value'],6.2+2e9*.84*62/60/2.5e9)
        self.assertNotIn('V9',result['template']['refs']['pretax_costs'])
        self.assertTrue(all(s['probability'] is None for s in result['sensitivities']))

    def test_wrong_source_share_scale_rejected_by_dimensions(self):
        options=self.margin_options();options[0]['canonical_inputs']['V5']['unit']='USD'
        with self.assertRaisesRegex(ValueError,'dimensions'):
            d.evaluate(assumptions(),options)

    def test_matching_wrong_quarter_guidance_is_rejected(self):
        vs=[variable('V1','revenue',197e9,'USD','Q2 FY2026','management_guidance'),
            variable('V2','revenue',202e9,'USD','Q2 FY2026','management_guidance')]
        self.assertFalse(d.templates(vs,['V1','V2'],{'metric':'quarterly_revenue','target_period':'Q3 FY2026'}))

    def test_target_period_aliases_and_guidance_endpoints(self):
        vs=[variable('V1','revenue',197e9,'USD','Q3 FY2026 guidance','management_guidance'),
            variable('V2','revenue',202e9,'USD','fiscal 2026 third quarter outlook','management_guidance')]
        options=d.templates(vs,['V1','V2'],{'metric':'quarterly_revenue','target_period':'Q3 FY2026'})
        result=d.evaluate(assumptions(),options)
        self.assertEqual([s['value'] for s in result['sensitivities']],[197e9,202e9])
        self.assertTrue(all(s['probability'] is None for s in result['sensitivities']))

    def test_guidance_endpoints_from_different_sources_are_not_paired(self):
        vs=[variable('V1','revenue',197e9,'USD','Q3 FY2026','management_guidance'),
            variable('V2','revenue',202e9,'USD','Q3 FY2026','management_guidance')]
        vs[0]['original_row_ref']['url']='https://example.org/old'
        vs[1]['original_row_ref']['url']='https://example.org/new'
        self.assertFalse(d.templates(vs,['V1','V2'],{'metric':'quarterly_revenue','target_period':'Q3 FY2026'}))

    def test_wrong_period_tax_cannot_enable_cost_removal(self):
        options=self.margin_options()
        vs=[variable('V1','revenue',60e9,'USD','Q3 FY2026','management_guidance'),
            variable('V2','revenue',64e9,'USD','Q3 FY2026','management_guidance'),
            variable('V3','net_income',15e9,'USD','Q2 FY2026'),
            variable('V4','revenue',60e9,'USD','Q2 FY2026'),
            variable('V5','diluted_shares',2.5e9,'shares','Q2 FY2026'),
            variable('V6','one_off',2e9,'USD','Q2 FY2026',quote='Pretax severance expense.'),
            variable('V7','tax_rate',.15,'fraction','Q3 FY2025','management_guidance'),
            variable('V8','tax_rate',.17,'fraction','Q3 FY2025','management_guidance')]
        options=d.templates(vs,[v['fact_id'] for v in vs],{'metric':'gaap_diluted_eps','target_period':'Q3 FY2026'})
        with self.assertRaisesRegex(ValueError,'tax-rate source'):
            d.evaluate(assumptions(cost=1),options)

    def test_comparison_quarter_is_not_a_guidance_target(self):
        v=variable('V1','revenue',197e9,'USD','Expected sales compared with third quarter 2025.','management_guidance')
        self.assertIsNone(d.period(v))
        self.assertFalse(d.guidance_applies(v,(3,2025)))

    def test_original_local_forecast_period_survives_prior_comparison(self):
        v=variable('V1','revenue',197e9,'USD','Expected sales compared with third quarter 2025.','management_guidance')
        v.pop('period_interpretation');v['period_claim']='Expected sales compared with third quarter 2025.'
        v['period_original_refs']=[{'quote':'Expected sales compared with third quarter 2025. Third quarter 2026 growth outlook.'}]
        self.assertTrue(d.guidance_applies(v,(3,2026)))
        self.assertFalse(d.guidance_applies(v,(3,2025)))

    def test_remaining_scope_requires_original_report_quarter(self):
        v=variable('V1','tax_rate',.15,'fraction','','management_guidance')
        v.pop('period_interpretation');v['period_claim']='For remaining quarters of 2026.'
        self.assertFalse(d.guidance_applies(v,(3,2026)))
        v['period_original_refs']=[{'quote':v['period_claim']},{'quote':'Second Quarter 2026 Financial Highlights'}]
        self.assertTrue(d.guidance_applies(v,(3,2026)))
        self.assertFalse(d.guidance_applies(v,(1,2026)))
        self.assertFalse(d.guidance_applies(v,(3,2025)))


if __name__=='__main__':unittest.main()
