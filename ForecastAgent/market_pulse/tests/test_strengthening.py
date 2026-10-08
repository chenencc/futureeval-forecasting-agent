"""Counterexamples for source binding, formulas and sparse calibration data."""
import copy
import unittest

from ForecastAgent.market_pulse import formulas as f, error_distribution as e, enrichment as n
from ForecastAgent.market_pulse.tests.test_analysis import request
from ForecastAgent.market_pulse import facts
from ForecastAgent.market_pulse import projections


def variables():
    return [{'fact_id':'V1','normalized_value':90e9,'normalized_unit':'USD'},
            {'fact_id':'V2','normalized_value':.10,'normalized_unit':'fraction'},
            {'fact_id':'V3','normalized_value':2.5e9,'normalized_unit':'shares'}]


def formula():
    return {'assumptions':[],'calculations':[{'id':'C1','operation':'grow','inputs':['V1','V2'],'explanation':'Revenue sensitivity'}],
        'central_ref':'C1','scenarios':[],'thesis':'Explicit predictor extrapolation.','limitations':['No empirical calibration.']}


class FormulaTests(unittest.TestCase):
    def test_source_bound_variable_rejects_numeric_and_unit_tampering(self):
        body='Microsoft total revenue was $90.0 billion for the quarter ended June 30, 2026.'
        token=facts.numeric_tokens(body)[0];ref=facts.original_ref('https://example.com/report',None,body,0,len(body))
        bundle={'pages':{'https://example.com/report':{'content':body}}}
        v={'fact_id':'V1','metric':'revenue','source_unit':'USD_billions','raw_value':90,
            'normalized_value':90e9,'normalized_unit':'USD','conversion_factor':1e9,
            'numeric_token':token,'original_row_ref':ref,'unit_original_ref':ref,'period_original_refs':[ref],
            'period_interpretation':'Quarter ended June 30, 2026'}
        self.assertTrue(f.validate_variables(bundle,[v]))
        for field,value in [('raw_value',91),('normalized_value',90e6),('source_unit','USD_millions')]:
            forged=copy.deepcopy(v);forged[field]=value
            with self.assertRaises(ValueError):f.validate_variables(bundle,[forged])

    def test_source_library_dedup_keeps_all_variable_handles(self):
        body='Microsoft revenue was $90.0 billion for the quarter ended June 30, 2026.'
        ref=facts.original_ref('https://example.com/report',None,body,0,len(body))
        v={'fact_id':'V1','metric':'revenue','role':'actual','basis':'GAAP','normalized_value':90e9,'normalized_unit':'USD',
            'original_row_ref':ref,'unit_original_ref':ref,'period_original_refs':[ref],'period_claim':'2026'}
        other={**v,'fact_id':'V2'};out=f.compact_catalog([v,other])
        self.assertEqual(len(out['original_evidence_library']),1)
        self.assertEqual(out['variables'][0]['source_ref_ids'],out['variables'][1]['source_ref_ids'])

    def test_original_shared_rules_survive_sibling_scope_filter(self):
        r=request();r['fine_print']='First publication controls.'
        r['resolution_criteria']=r['resolution_criteria'].replace('* Apple Q4 FY2026 October 29','* Apple Q4 FY2026 October 29 [Source](https://www.apple.com/report)')
        q,a=f.scoped_question({'request':r})
        self.assertIn('Microsoft Q1',q['resolution_criteria'])
        self.assertNotIn('* Apple',q['resolution_criteria'])
        self.assertEqual(q['fine_print'],r['fine_print']);self.assertTrue(a['original_field_sha256'])

    def test_background_series_has_no_invented_quarter(self):
        r=request();out=f.background_variables({'request':r})
        self.assertEqual([v['normalized_value'] for v in out],[65585e6,90007e6])
        self.assertTrue(all('no fiscal dates' in v['period_interpretation'] for v in out))
        for i,v in enumerate(out,1):v['fact_id']='V'+str(i)
        self.assertTrue(f.validate_variables({'request':r},out))

    def test_model_never_needs_calculator_outputs(self):
        self.assertNotIn('model_value',f.CALC['properties'])
        out=f.evaluate(formula(),variables(),'USD')
        self.assertAlmostEqual(out['central_value'],99e9,delta=.001)
        self.assertFalse(out['calibration_validated'])

    def test_named_inputs_and_dependency_order(self):
        for ref in ('90000000000','UNKNOWN','C2'):
            raw=formula();raw['calculations'][0]['inputs'][0]=ref
            with self.assertRaises(ValueError):f.evaluate(raw,variables(),'USD')

    def test_units_cannot_be_fixed_by_an_untyped_multiplier(self):
        raw=formula();raw['calculations'][0]['inputs']=['V1','V3']
        with self.assertRaises(ValueError):f.evaluate(raw,variables(),'USD')

    def test_currency_per_share_is_program_computed(self):
        raw=formula();raw['calculations'][0].update(operation='ratio',inputs=['V1','V3'])
        self.assertEqual(f.evaluate(raw,variables(),'USD_per_share')['central_value'],36)

    def test_assumption_only_forecast_rejected(self):
        raw=formula();raw['assumptions']=[{'id':'A1','value':10,'unit':'USD','rationale':'Unsupported guess','fact_refs':[]}]
        raw['calculations'][0].update(operation='sum',inputs=['A1'])
        with self.assertRaises(ValueError):f.evaluate(raw,variables(),'USD')

    def test_literal_source_copy_retains_uncertain_assumption_and_value(self):
        raw=formula();raw['assumptions']=[{'id':'A1','value':90e9,'unit':'USD','rationale':'Assume prior value persists; not a verified future actual.','fact_refs':['V1']}]
        raw['calculations'][0].update(operation='sum',inputs=['A1'])
        out=f.evaluate(raw,variables(),'USD')
        self.assertEqual(out['central_value'],90e9);self.assertFalse(out['semantic_truth_verified'])
        self.assertTrue(out['source_copy_compatibility_audit'][0]['numeric_value_unchanged'])
        raw['assumptions'][0]['value']=91e9
        with self.assertRaises(ValueError):f.evaluate(raw,variables(),'USD')

    def test_sensitivity_is_not_a_probability_interval(self):
        raw=formula();raw['scenarios']=[{'name':'example','calculation_ref':'C1','interpretation':'Assumptions unchanged'}]
        out=f.evaluate(raw,variables(),'USD')
        self.assertIsNone(out['scenarios'][0]['probability'])
        self.assertTrue(out['scenario_values_are_not_quantiles'])


class ErrorTests(unittest.TestCase):
    def test_error_family_follows_actual_formula(self):
        vars=[{'fact_id':'V1','metric':'revenue','role':'management_guidance','normalized_unit':'USD','period_claim':'Q1'},
              {'fact_id':'V2','metric':'revenue','role':'management_guidance','normalized_unit':'USD','period_claim':'Q1'}]
        derived={'central_ref':'C1','calculations':[{'id':'C1','operation':'mean','inputs':['V1','V2']}]}
        self.assertEqual(e.current_family(derived,vars),'management_guidance_midpoint')
        derived['calculations'][0]['operation']='sum'
        self.assertEqual(e.current_family(derived,vars),'unsupported_current_formula')

    def pair(self,i=0):
        return {'period':'Q'+str(i),'metric':'quarterly_revenue','unit':'USD','model_family':'guide',
            'forecast':90e9,'actual':(89+i)*1e9,'issued_at':'2025-01-01','actual_published_at':'2025-04-01',
            'first_report_verified':True,'forecast_ref':{'source':'frozen'},'actual_ref':{'source':'frozen'}}

    def fit(self,pairs):return e.fit(pairs,'2026-10-08','quarterly_revenue','USD','guide',90e9,request())

    def test_sparse_history_never_generates_calibrated_cdf(self):
        out=self.fit([self.pair()]);self.assertIsNone(out['cdf']);self.assertFalse(out['calibration_validated'])

    def test_future_label_and_unverified_first_report_excluded(self):
        for key,value in [('actual_published_at','2026-10-08'),('first_report_verified',False),('model_family','other'),('unit','shares')]:
            pair=self.pair();pair[key]=value
            self.assertEqual(self.fit([pair])['sample_count'],0)

    def test_duplicate_period_never_inflates_error_count(self):
        self.assertEqual(self.fit([self.pair(),self.pair()])['sample_count'],1)

    def test_empirical_candidate_keeps_tails_without_claiming_calibration(self):
        out=self.fit([self.pair(i) for i in range(12)]);cdf=out['raw_cdf']
        self.assertEqual(len(cdf),201);self.assertEqual(cdf,sorted(cdf))
        self.assertFalse(out['calibration_validated'])

    def test_zero_error_spread_is_not_proof_of_certainty(self):
        pairs=[dict(self.pair(),period=str(i)) for i in range(12)]
        self.assertEqual(self.fit(pairs)['status'],'degenerate_error_history')

    def test_interval_score_penalizes_narrow_miss(self):
        self.assertEqual(e.interval_score(10,9,11),2)
        self.assertGreater(e.interval_score(15,9,11),e.interval_score(15,8,16))


class ProjectionTests(unittest.TestCase):
    def test_explicit_eps_sensitivity_never_double_taxes_net_income(self):
        specs=[('lo','revenue','USD',90,'management_guidance'),('hi','revenue','USD',110,'management_guidance'),
            ('sales','revenue','USD',100,'actual'),('ni','net_income','USD',20,'actual'),
            ('shares','diluted_shares','shares',10,'actual'),('taxlo','tax_rate','fraction',.2,'management_guidance'),
            ('taxhi','tax_rate','fraction',.2,'management_guidance'),('cost','one_off','USD',10,'actual')]
        variables=[{'fact_id':i,'metric':m,'normalized_unit':u,'normalized_value':v,'role':r} for i,m,u,v,r in specs]
        out=projections.eps_sensitivity(variables,{'guidance_lower':'lo','guidance_upper':'hi','prior_revenue':'sales',
            'prior_net_income':'ni','prior_diluted_shares':'shares','tax_lower':'taxlo','tax_upper':'taxhi','current_expense_one_offs':['cost']})
        self.assertEqual(out['baseline_eps'],2)
        self.assertEqual(out['scenarios'][-1]['eps'],2.8)
        self.assertIsNone(out['probability_interval']);self.assertFalse(out['calibration_validated'])

    def test_guide_anchor_rejects_segments_actuals_and_wrong_units(self):
        variables=[{'fact_id':'lo','metric':'revenue','normalized_unit':'USD','normalized_value':90,'role':'actual'},
            {'fact_id':'hi','metric':'revenue','normalized_unit':'USD','normalized_value':100,'role':'management_guidance'}]
        with self.assertRaises(ValueError):projections.revenue_anchor(variables,'lo','hi')


class VintageTests(unittest.TestCase):
    def test_quarter_duration_and_first_filing_vintage(self):
        original={'start':'2025-01-01','end':'2025-03-31','val':90,'filed':'2025-04-20','accn':'first','form':'10-Q','fy':2025,'fp':'Q1'}
        revision={**original,'val':100,'filed':'2026-04-20','accn':'later'}
        ytd={**original,'start':'2024-10-01','val':200}
        future={**original,'end':'2026-12-31','filed':'2027-01-20'}
        data={'facts':{'us-gaap':{'Revenues':{'units':{'USD':[revision,ytd,original,future]}}}}}
        out=n.quarter_history(data,'2026-10-08')
        self.assertEqual(len(out),1);self.assertEqual(out[0]['value'],90)
        self.assertFalse(out[0]['first_report_verified'])

    def test_cik_is_observed_not_guessed(self):
        with self.assertRaises(ValueError):n.observed_cik({'request':{'question':'Apple'},'pages':{}})
        self.assertEqual(n.observed_cik({'pages':{'https://www.sec.gov/Archives/edgar/data/320193/example.htm':{}}})['cik'],320193)


if __name__=='__main__':unittest.main()
