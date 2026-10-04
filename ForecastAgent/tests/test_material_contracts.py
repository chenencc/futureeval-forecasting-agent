"""Offline row coverage, conflict isolation and applicability acceptance gates."""
import json
import unittest
from ForecastAgent.readers.material_passages import spans
from ForecastAgent.supplement import binding_guard, material_review, need_ledger
from ForecastAgent.tests.test_material_gap_workflow import bundle


class ContractTests(unittest.TestCase):
    def fixture(self):
        b = bundle()
        url = 'https://example.org/table'
        body = '| Date | High |\n| --- | --- |\n' + ''.join(f'| 2026-08-{i:02} | {i}.123 |\n' for i in range(1,32))
        b['pages'][url] = {'content':body}
        p = material_review.packet(b,need_ledger.build(b),[{'url':url}])
        return b,url,body,p

    def test_complete_rows_and_exact_offsets(self):
        b,url,body,p = self.fixture()
        self.assertTrue(any(x['text']==body and x['reading']['complete_saved_table'] for x in p['passages']))
        chunks = spans(body,120)
        self.assertGreater(len(chunks),1)
        for x in chunks:
            self.assertEqual(body[x['start']:x['end']],x['text'])
            self.assertTrue(x['text'].endswith('\n'))
            self.assertFalse(x['reading']['upstream_completeness_verified'])

    def test_cut_table_cannot_close_even_with_all_model_axes(self):
        b,url,body,p = self.fixture()
        result = material_review.bind(b,p,{'bindings':[{'need_id':'access','url':url,
            'quote':body[:120],'axes':{a:True for a in material_review.AXES}}]})
        self.assertFalse(result['bindings'][0]['closure_guard']['eligible_for_material_closure'])

    def test_conflicting_actions_do_not_destroy_unrelated_binding(self):
        b,url,body,p = self.fixture()
        other='https://example.org/other'
        b['pages'][other]={'content':'An independently saved substantive access document. '*10}
        p=material_review.packet(b,need_ledger.build(b),[{'url':url},{'url':other}])
        good=next(x for x in p['passages'] if x['url']==other)
        result=material_review.bind(b,p,{'bindings':[{'need_id':'access','url':other,
            'quote':good['text'],'axes':{a:True for a in material_review.AXES}}],
            'priority_urls':[url],'deferred_urls':[{'url':url,'reason':'Wrong observation period'}],
            'next_search':{'need_id':'access','query':'Find the original release'}})
        self.assertEqual(result['conflicted_urls'],[url])
        self.assertEqual(result['priority_urls'],[])
        self.assertEqual(len(result['bindings']),1)
        self.assertTrue(need_ledger.build(b,{'material_reviews':[{'result':result}]})['needs'][0]['target_material_captured'])

    def test_invalid_binding_is_quarantined_without_losing_valid_binding(self):
        b,url,body,p=self.fixture()
        rows=[{'need_id':'access','passage_id':p['passages'][0]['passage_id'],'axes':{a:True for a in material_review.AXES}},
              {'need_id':'access','passage_id':'unknown','axes':{a:True for a in material_review.AXES}}]
        d=material_review.decode({'content':json.dumps({'bindings':rows,'priority_source_ids':[],
            'deferred_source_ids':[],'next_search':None})},p)
        result=material_review.bind(b,p,d)
        self.assertEqual(len(result['bindings']),1)
        self.assertEqual(len(result['rejected_records']),1)

    def test_access_metadata_is_not_a_numeric_observation(self):
        n={'condition':'How to access monthly I-92 data files (API, download links)'}
        self.assertEqual(binding_guard.required_axes(n),['entity','material_type'])
        self.assertIn('period',binding_guard.required_axes({'condition':'Metadata version effective June 2026'}))
        self.assertEqual(binding_guard.required_axes({'condition':'June 2026 monthly air passenger count'}),list(material_review.AXES))

    def test_reporting_about_official_results_is_a_secondary_role(self):
        n={'condition':'Credible international media reporting of official results'}
        self.assertEqual(binding_guard.source_requirement(n)['role'],'secondary_reporting')
        self.assertFalse(binding_guard.source_requirement(n)['required'])
        self.assertFalse(binding_guard.assess({'condition':'Official election results'},
            {'url':'https://example.org'})['eligible_for_material_closure'])


if __name__=='__main__':
    unittest.main()
