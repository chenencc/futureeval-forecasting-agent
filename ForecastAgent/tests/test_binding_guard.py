"""Source identity and contradictory review gates; no provider calls."""
import unittest
from ForecastAgent.supplement import binding_guard, material_review, need_ledger
from ForecastAgent.tests.test_material_gap_workflow import bundle


class BindingGuardTests(unittest.TestCase):
    def test_exact_publisher_host_not_attribution_or_suffix_spoof(self):
        n={'condition':'EIA historical price observation'}
        for url in ['https://news.example/eia','https://eia.gov.evil.example/data']:
            self.assertFalse(binding_guard.assess(n,{'url':url})['eligible_for_material_closure'])
        self.assertTrue(binding_guard.assess(n,{'url':'https://api.eia.gov/data'})['eligible_for_material_closure'])

    def test_secondary_unknown_official_and_acronym_are_distinct(self):
        self.assertFalse(binding_guard.source_requirement({'condition':'Credible secondary sources attributing EIA prices'})['required'])
        self.assertFalse(binding_guard.source_requirement({'condition':'who will win the contest'})['required'])
        self.assertFalse(binding_guard.assess({'condition':'First official report'}, {'url':'https://news.example'})['eligible_for_material_closure'])
        self.assertTrue(binding_guard.assess({'condition':'First official report','required_source_domains':['issuer.example']}, {'url':'https://data.issuer.example'})['eligible_for_material_closure'])

    def test_deferred_bound_page_stays_raw_but_cannot_close_need(self):
        b=bundle();url='https://lab.example/launch'
        text='The evaluated LLM International Math Olympiad system is not released to the public. '
        b['pages'][url]={'content':text*8,'links':[]}
        payload=material_review.packet(b,need_ledger.build(b),[])
        payload['sources']=[{'source_id':'S-test','url':url}]
        decision={'bindings':[{'need_id':'access','url':url,'quote':text,'axes':{a:True for a in material_review.AXES}}],
                  'deferred_urls':[{'url':url,'reason':'Wrong period'}]}
        result=material_review.bind(b,payload,decision)
        self.assertTrue(result['bindings'][0]['quote_bound'])
        self.assertFalse(result['bindings'][0]['closure_guard']['eligible_for_material_closure'])
        row=need_ledger.build(b,{'material_reviews':[{'result':result}]})['needs'][0]
        self.assertFalse(row['target_material_captured'])
        self.assertTrue(row['blocked_material_bindings'])
        self.assertIn(url,b['pages'])


if __name__=='__main__':unittest.main()
