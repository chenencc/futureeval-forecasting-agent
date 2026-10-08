"""Counterexamples for response aliases, unit scales and arithmetic gates."""
import copy
import unittest

from ForecastAgent.market_pulse import numeric_binding as n
from ForecastAgent.market_pulse.tests.test_financial_chain import fixture


def bound_facts():
    return [
        {'fact_id':'V1','normalized_value':90000000000,'normalized_unit':'USD'},
        {'fact_id':'V2','normalized_value':80000000000,'normalized_unit':'USD'},
        {'fact_id':'V3','normalized_value':15848000000,'normalized_unit':'USD'},
        {'fact_id':'V4','normalized_value':2566000000,'normalized_unit':'shares'},
    ]


def report():
    return {'assumptions':[{'id':'A1','value':2,'unit':'percent','rationale':'Explicit assumed sequential growth','fact_refs':['V1']}],
        'calculations':[{'id':'C1','operation':'grow','inputs':['V1','A1'],'model_value':91800,
                         'unit':'USD_millions','explanation':'Revenue growth under an assumption'}],
        'forecast':{'unit':'USD','p10':88e9,'p50':91.8e9,'p90':96e9,'central_calculation_ref':'C1'},
        'thesis':'Assumption, not observed future revenue.','limitations':['No comparable quarter.'],
        'upside':['Growth'], 'downside':['Slowdown']}


class NumericBindingTests(unittest.TestCase):
    def test_currency_abbreviation_requires_literal_numeric_currency_expression(self):
        import re
        self.assertRegex('Revenue was $28.2B.', n.UNIT_SUPPORT['USD_billions'])
        self.assertRegex('Revenue was $900M.', n.UNIT_SUPPORT['USD_millions'])
        self.assertIsNone(re.search(n.UNIT_SUPPORT['USD_billions'], 'Revenue was $28.2.', re.I))
        self.assertIsNone(re.search(n.UNIT_SUPPORT['USD_billions'], 'A class B share pays $28.2.', re.I))

    def test_explicit_percent_and_million_scales_reproduce_raw_dollars(self):
        r=n.compile_derivation(report(),bound_facts(),{'metric':'quarterly_revenue'})
        self.assertEqual(r['calculations'][0]['program_value'],91800000000)
        self.assertEqual(r['assumptions'][0]['normalized_value'],.02)

    def test_wrong_arithmetic_is_not_replaced_with_program_value(self):
        r=report();r['calculations'][0]['model_value']=91000
        with self.assertRaisesRegex(ValueError,'Arithmetic mismatch'):
            n.compile_derivation(r,bound_facts(),{'metric':'quarterly_revenue'})

    def test_explicit_conversion_preserves_physical_quantity(self):
        r=report();r['assumptions']=[
            {'id':'A1','value':91.8,'unit':'USD_billions','rationale':'Forecast assumption','fact_refs':['V1']},
            {'id':'A2','value':1000,'unit':'dimensionless','rationale':'Conversion factor from billions to millions','fact_refs':[]}]
        r['calculations']=[{'id':'C1','operation':'product','inputs':['A1','A2'],
            'model_value':91800,'unit':'USD_millions','explanation':'Explicit scale conversion'}]
        result=n.compile_derivation(r,bound_facts(),{'metric':'quarterly_revenue'})
        self.assertEqual(result['calculations'][0]['program_value'],91800000000)
        self.assertEqual(result['compatibility_repairs'][0]['kind'],'explicit_unit_conversion_identity')
        r['assumptions'][1]['rationale']='Assumed business growth multiplier'
        with self.assertRaisesRegex(ValueError,'Arithmetic mismatch'):
            n.compile_derivation(r,bound_facts(),{'metric':'quarterly_revenue'})

    def test_thousandfold_share_error_is_rejected(self):
        r=report();r['assumptions']=[{'id':'A1','value':2.566,'unit':'shares_millions',
            'rationale':'Claims to copy prior diluted shares','fact_refs':['V4']}]
        r['calculations']=[{'id':'C1','operation':'ratio','inputs':['V3','A1'],
            'model_value':6.176,'unit':'USD_per_share','explanation':'Income divided by shares'}]
        r['forecast']={'unit':'USD_per_share','p10':5,'p50':6.176,'p90':7,'central_calculation_ref':'C1'}
        with self.assertRaisesRegex(ValueError,'Arithmetic mismatch'):
            n.compile_derivation(r,bound_facts(),{'metric':'gaap_diluted_eps'})

    def test_literals_are_not_untracked_assumptions(self):
        r=report();r['calculations'][0]['inputs']=['V1','1000']
        with self.assertRaisesRegex(ValueError,'named facts'):
            n.compile_derivation(r,bound_facts(),{'metric':'quarterly_revenue'})

    def test_reference_alias_keeps_exact_source_and_unverified_period(self):
        b,t,s=fixture();s['unit_ref']=s['token_id'];s['period_text']='Unverified fiscal label'
        before=copy.deepcopy(b)
        rows,aliases,audit=n.bind(b,t,[copy.deepcopy(s) for _ in range(4)])
        self.assertEqual(rows[0]['raw_value'],90)
        self.assertFalse(rows[0]['period_claim_is_verbatim'])
        self.assertFalse(rows[0]['semantic_interpretation_verified'])
        self.assertEqual(rows[0]['period_claim'],'Unverified fiscal label')
        self.assertEqual(b,before)
        self.assertTrue(audit)

    def test_unknown_references_do_not_become_local_evidence(self):
        b,t,s=fixture();s['unit_ref']='F999.R0'
        with self.assertRaisesRegex(ValueError,'foreign-source unit'):
            n.bind(b,t,[copy.deepcopy(s) for _ in range(4)])

    def test_missing_unit_caption_stays_rejected(self):
        b,t,s=fixture();s['source_unit']='USD_millions'
        with self.assertRaisesRegex(ValueError,'literal support'):
            n.bind(b,t,[copy.deepcopy(s) for _ in range(4)])

    def test_ambiguous_alias_is_rejected(self):
        r=report();r['assumptions'][0]['fact_refs']=['F001.N0']
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            n.compile_derivation(r,bound_facts(),{'metric':'quarterly_revenue'}, {'F001.N0':['V1','V2']})


if __name__=='__main__':unittest.main()
