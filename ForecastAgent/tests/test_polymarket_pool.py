import unittest
from ForecastAgent.polymarket_pool import build_pool,annotate_direction
class FamilyPoolTests(unittest.TestCase):
 def market(self,i):return {'id':str(i),'question':f'Outcome {i}?','outcomes':['Yes','No'],'outcomePrices':['0.4','0.6']}
 def test_large_family_cannot_hide_later_event(self):
  payload={'events':[{'slug':'large','markets':[self.market(i) for i in range(100)]},{'slug':'small','markets':[self.market(200)]}]}
  result=build_pool([payload],'Target',market_cap=5)
  self.assertIn('200',[r['market_id'] for r in result['rows']]);self.assertEqual(result['coverage']['omitted_markets'],96)
 def test_query_breadth_and_dedupe(self):
  a={'events':[{'slug':'a','markets':[self.market(1),self.market(2)]}]};b={'events':[{'slug':'b','markets':[self.market(3),self.market(1)]}]}
  result=build_pool([a,b],'Target',market_cap=2)
  self.assertEqual([r['market_id'] for r in result['rows']],['1','3']);self.assertEqual(result['coverage']['duplicates_removed'],1)
 def test_unknown_is_not_guessed(self):
  self.assertEqual(annotate_direction({'direction':'inverse'}, {})['direction'],'unknown')
 def test_inverse_never_converts_price(self):
  r=annotate_direction({'direction':'inverse','target_proposition':'Iran participates','market_yes_proposition':'Iran removed','tier':'same_quantity_same_date'},{'yes_probability_display':0.3})
  self.assertEqual(r['direction'],'inverse');self.assertIsNone(r['target_yes_probability']);self.assertFalse(r['eligible_for_edge']);self.assertIn('top_tier_direction_conflict',r['direction_diagnostics'])
if __name__=='__main__':unittest.main()
class DirectionContractTests(unittest.TestCase):
 def test_parser_retains_direction_fields(self):
  from ForecastAgent.polymarket_rank_trial import parse_ranking
  pick=parse_ranking('[{"i":0,"tier":"weak","direction":"inverse","target_proposition":"Will Iran participate?","market_yes_proposition":"Iran removed"}]',1)[0]
  self.assertEqual(pick['direction'],'inverse');self.assertEqual(pick['market_yes_proposition'],'Iran removed')
 def test_changed_target_is_unknown(self):
  result=annotate_direction({'direction':'inverse','target_proposition':'Iran does not participate','market_yes_proposition':'Iran removed'}, {},target_question='Will Iran participate?')
  self.assertEqual(result['direction'],'unknown');self.assertIn('target_proposition_not_exact_question',result['direction_diagnostics'])
