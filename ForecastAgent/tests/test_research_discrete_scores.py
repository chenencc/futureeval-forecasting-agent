"""Analytic ordinal scoring checks, independent of model calls and resolutions."""
import unittest
from ForecastAgent.research_loop.evaluate_trial import score

class DiscreteScoreTests(unittest.TestCase):
    def setUp(self):
        self.question={'question_type':'discrete','scaling':{'range_min':-.5,'range_max':3.5},
            'inbound_outcome_count':4,'open_lower_bound':False,'open_upper_bound':False}

    def evaluate(self,cdf,value=None,**extra):
        return score({'continuous_cdf':cdf},self.question,{'value':value,**extra})

    def test_perfect_atom_has_zero_loss(self):
        self.assertEqual(self.evaluate([0,0,0,0,1],3)['ranked_probability_score'],0)

    def test_opposite_endpoints_have_max_normalized_loss(self):
        result=self.evaluate([0,1,1,1,1],3)
        self.assertEqual(result['ranked_probability_score'],3)
        self.assertEqual(result['normalized_ranked_probability_score'],1)

    def test_two_endpoint_mixture_matches_analytic_score(self):
        for truth in (0,3):
            result=self.evaluate([0,.5,.5,.5,1],truth)
            self.assertEqual(result['ranked_probability_score'],.75)
            self.assertEqual(result['normalized_ranked_probability_score'],.25)

    def test_impossible_fraction_is_not_rounded_to_a_count(self):
        with self.assertRaisesRegex(ValueError,'possible discrete'):
            self.evaluate([0,.2,.4,.6,1],1.4)

    def test_open_tail_is_a_category_without_fabricated_point(self):
        self.question['open_lower_bound']=True
        result=self.evaluate([.2,.4,.6,.8,1],None,result_kind='below_lower_bound')
        self.assertAlmostEqual(result['ranked_probability_score'],1.2)
        self.assertAlmostEqual(result['normalized_ranked_probability_score'],.3)

    def test_closed_tail_sentinel_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'not open'):
            self.evaluate([0,.2,.4,.6,1],None,result_kind='above_upper_bound')

    def test_single_possible_outcome_has_no_ordinal_threshold(self):
        self.question.update(scaling={'range_min':2.5,'range_max':3.5},inbound_outcome_count=1)
        self.assertEqual(self.evaluate([0,1],3)['normalized_ranked_probability_score'],0)

if __name__=='__main__':unittest.main()
