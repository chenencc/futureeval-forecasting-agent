"""Preview guard and inconsistent-output counterexamples without network calls."""
import copy
import unittest
from ForecastAgent.market_pulse import held_preview as p
from ForecastAgent.market_pulse import held_preview_review as audit
from ForecastAgent.market_pulse.tests.test_analysis import request


class PreviewTests(unittest.TestCase):
    def test_platform_post_is_rejected_before_transport(self):
        client=p.ReadClient('fixture','unused')
        with self.assertRaisesRegex(ValueError,'only allows GET'):
            client.request('POST','questions/forecast/',{'probability_yes':.5})

    def test_dual_percent_and_currency_labels_are_equivalent(self):
        self.assertIn('0.74 fraction',p.coordinates(74,'%'))
        self.assertIn('USD 9,200,000,000',p.coordinates(9.2,'Billion $'))
        self.assertIn('USD 108 billion',p.coordinates(108e9,'$'))
        with self.assertRaises(ValueError):p.coordinates(74,'ambiguous')

    def test_context_and_probabilities_are_not_changed_to_hide_head_conflict(self):
        response={'answers':{'event_outcome':{'probabilities':{'below':.01,'bin_0':.98,'above':.01}},
            'event_region':{'probabilities':{'below':.01,'inside':.01,'above':.98}}}}
        old=copy.deepcopy(response)
        audit=p.mass_audit(response,{})
        self.assertTrue(audit['requires_review'])
        self.assertAlmostEqual(audit['maximum_absolute_probability_difference'],.97)
        self.assertEqual(response,old)

    def test_unit_schema_and_literal_source_references_are_required(self):
        raw={'p10':70,'p50':74,'p90':78,'unit':'percentage_points',
            'probability_below':.1,'probability_inside':.3,'probability_above':.6,
            'source_refs':'R3','rationale':'Predictor only','limitations':'Uncertain future'}
        p.validate_super(raw,'percentage_points',{'R3'})
        for key,value in [('unit','fraction'),('source_refs','R99'),('p90',120),('p10',float('nan')),('limitations',['wrong schema'])]:
            wrong=copy.deepcopy(raw);wrong[key]=value
            with self.assertRaises(ValueError):p.validate_super(wrong,'percentage_points',{'R3'})

    def test_original_grid_is_not_rescaled_and_both_tails_remain(self):
        r=request();r['unit']='$';r['scaling']['range_min']=10.5e9;r['scaling']['range_max']=11.6e9
        r['scaling'].pop('continuous_range',None);old=copy.deepcopy(r)
        spec=p.spec_for(r)
        self.assertEqual(spec['edges'][0],10.5e9)
        self.assertEqual(spec['edges'][-1],11.6e9)
        self.assertIn('USD 11.6 billion',spec['criteria']['above'])
        self.assertEqual(r,old)

    def test_no_delivery_command_is_exposed(self):
        self.assertFalse(p.POLICY['delivery_enabled'])
        self.assertEqual(p.POLICY['new_searches'],0)

    def test_annulment_is_not_numeric_forecast_failure(self):
        self.assertEqual(p.lifecycle_state({'status':'resolved','label':'Acme (annulled)'}),'officially_marked_annulled')
        self.assertEqual(p.lifecycle_state({'status':'open','label':'Acme'}),'open')
        self.assertEqual(p.lifecycle_state({'status':'resolved','label':'Acme'}),'officially_non_open')

    def test_quantiles_cannot_claim_ten_percent_above_a_lower_cutpoint_than_median(self):
        raw={'p10':72,'p50':74,'p90':76,'probability_below':.1,'probability_inside':.8,'probability_above':.1}
        old=copy.deepcopy(raw)
        issues=audit.quantile_region_issues(raw,{'edges':[68.95,73.05]})
        self.assertTrue(any('p50' in s and 'above probability' in s for s in issues))
        self.assertEqual(raw,old)
        raw.update(probability_below=.02,probability_inside=.2,probability_above=.78)
        self.assertEqual(audit.quantile_region_issues(raw,{'edges':[68.95,73.05]}),[])

    def test_ninetieth_percentile_below_cutpoint_cannot_have_thirty_percent_above(self):
        raw={'p10':8.5,'p50':9.2,'p90':10,'probability_below':.33,'probability_inside':.34,'probability_above':.33}
        issues=audit.quantile_region_issues(raw,{'edges':[9.45,11.25]})
        self.assertTrue(any('p50' in s for s in issues))
        self.assertTrue(any('p90' in s for s in issues))

    def test_discrete_summary_uses_outcome_support_and_never_tail_extrapolation(self):
        r={'question_type':'discrete','unit':'Billion $','scaling':{'range_min':9.45,'range_max':9.75},
            'inbound_outcome_count':3,'open_lower_bound':True,'open_upper_bound':True}
        candidate={'continuous_cdf':[.02,.2,.7,.8]}
        self.assertEqual(audit.support_quantile(candidate,r,.5)['value'],9.6)
        self.assertIsNone(audit.support_quantile(candidate,r,.9)['value'])
        self.assertEqual(audit.support_quantile(candidate,r,.9)['state'],'above_platform_range')


if __name__=='__main__':unittest.main()
