import unittest
from ForecastAgent.polymarket_rank_trial import pool,parse_ranking
class RankTrialTests(unittest.TestCase):
 def test_empty_is_valid(self):self.assertEqual(parse_ranking('[]',10),[])
 def test_invalid_index_cannot_approve(self):
  with self.assertRaises(ValueError):parse_ranking('[{"i":99,"tier":"weak"}]',1)
 def test_no_lexical_floor(self):
  rows=pool([{'markets':[{'id':'1','question':'Iran wins World Cup?','outcomes':['Yes','No'],'outcomePrices':['0.2','0.8']}]}],'Unrelated question')
  self.assertEqual(len(rows),1);self.assertFalse(rows[0]['eligible_for_edge'])
if __name__=='__main__':unittest.main()
