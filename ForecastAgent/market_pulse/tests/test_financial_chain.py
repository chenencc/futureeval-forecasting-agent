"""Counterexamples for immutable financial rows, conversions and arithmetic."""
import copy
import unittest

from ForecastAgent.market_pulse import facts, financial_chain as c
from ForecastAgent.market_pulse.tests.test_analysis import request


def fixture():
    body=('Microsoft earnings for quarter ended June 30, 2026.\n\n'
          'Total revenue was $90.0 billion, with quarterly total revenue of $76.4 billion a year earlier.\n')
    bundle={'request':request(),'pages':{'https://www.microsoft.com/report':{'content':body}},'gaps':[]}
    table=facts.discover(bundle)
    row=table['candidates'][0]; ref=row['refs'][1]['ref_id']
    selection={'token_id':row['numbers'][0]['token_id'],'metric':'revenue','source_unit':'USD_billions',
        'unit_ref':ref,'period_ref':row['refs'][-1]['ref_id'],'period_text':'quarter ended June 30, 2026',
        'basis':'not_applicable','role':'actual'}
    return bundle,table,selection


class ChainTests(unittest.TestCase):
    def test_exact_reference_and_conversion(self):
        b,t,s=fixture();facts.validate_references(b,t)
        selected=facts.selected_facts(t,[s])[0]
        self.assertEqual(selected['normalized_value'],90000000000)
        self.assertFalse(selected['semantic_interpretation_verified'])

    def test_forged_period_is_rejected(self):
        b,t,s=fixture();s['period_text']='Q1 FY2027'
        with self.assertRaises(ValueError):facts.selected_facts(t,[s])

    def test_mutated_quote_is_rejected(self):
        b,t,s=fixture();t['candidates'][0]['refs'][0]['quote']='invented original'
        with self.assertRaises(ValueError):facts.validate_references(b,t)

    def test_missing_million_unit_is_rejected(self):
        b,t,s=fixture();s['source_unit']='USD_millions'
        with self.assertRaises(ValueError):facts.selected_facts(t,[s])

    def test_grow_and_eps_unit_dimensions(self):
        self.assertAlmostEqual(c.calculate('grow',[90e9,.1]),99e9,delta=.001)
        self.assertEqual(c.calculation_unit('grow',['USD','fraction']),'USD')
        self.assertEqual(c.calculation_unit('ratio',['USD','shares']),'USD_per_share')
        with self.assertRaises(ValueError):c.calculation_unit('mean',['USD','shares'])
        with self.assertRaises(ValueError):c.calculation_unit('grow',['USD','percent'])
        with self.assertRaises(ValueError):c.calculate('ratio',[1,0])

    def test_correct_forecast_format_does_not_hide_tail_disagreement(self):
        r=request();cdf=[.7+.25*i/200 for i in range(201)]
        candidate=c.payload(r,{'continuous_cdf':cdf})
        analysis={'forecast':{'p10':91e9,'p50':92e9,'p90':94e9},'numeric_binding_valid':True,'arithmetic_valid':True}
        answers={'material_conflict':{'noul':0},'interpretation_consistent':{'noul':1}}
        audit=c.acceptance(r,candidate,analysis,answers)
        self.assertTrue(audit['format_valid']);self.assertTrue(audit['requires_review'])
        self.assertTrue(any('open tail' in s for s in audit['review_reasons']))

    def test_predictor_rubric_never_requires_upcoming_actual(self):
        spec=c.distribution_spec(request()); q=c.decision_registry(spec)
        self.assertIn('NOT required',q['evidence_sufficiency']['instructions'])
        self.assertIn('future actual is still unknown',q['evidence_sufficiency']['criteria'][3])

    def test_narrow_uncertainty_is_reviewed_without_changing_cdf(self):
        r=request();cdf=[.02 if i<70 else .98 if i>74 else .02+.96*(i-70)/4 for i in range(201)]
        candidate=c.payload(r,{'continuous_cdf':cdf});before=copy.deepcopy(candidate)
        review=c.uncertainty_review(r,candidate,{'forecast':{'p10':88e9,'p50':92e9,'p90':96e9}})
        self.assertTrue(review['requires_review'])
        self.assertFalse(review['calibration_validated'])
        self.assertEqual(candidate,before)

    def test_discovery_does_not_mutate_saved_package(self):
        b,_,_=fixture();before=copy.deepcopy(b);facts.discover(b)
        self.assertEqual(b,before)


if __name__=='__main__':unittest.main()
