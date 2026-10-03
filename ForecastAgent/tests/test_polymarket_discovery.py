"""Isolation and contract-state regression checks."""
import unittest
from ForecastAgent.polymarket_discovery import candidates,queries
class DiscoveryTests(unittest.TestCase):
    def test_closed_preserved_without_price(self):
        market={'id':'1','question':'Will Anthropic IPO in 2026?','closed':True,'active':False,'outcomes':['Yes','No'],'outcomePrices':['1','0']}
        result=candidates({'markets':[market]},market['question'])
        self.assertEqual(len(result),1)
        self.assertIsNone(result[0]['yes_probability_display'])
        self.assertTrue(result[0]['original_market_state']['closed'])
        self.assertTrue(market['closed'])
        self.assertFalse(result[0]['eligible_for_edge'])
    def test_bounded_queries(self):
        self.assertEqual(queries('Will Royal Mail raise prices before September 2026?')[1],'Royal Mail')
        self.assertLessEqual(len(queries('Will Anthropic IPO?')),2)
if __name__=='__main__':unittest.main()
