"""Balanced exact reading and independent malformed-need quarantine."""
import json
import unittest
from ForecastAgent.supplement import material_review,need_ledger,witness_contract
from ForecastAgent.tests.test_material_gap_workflow import bundle


class ReadingCoverageTests(unittest.TestCase):
    def test_one_bad_need_does_not_discard_valid_exact_binding(self):
        b=bundle();url='https://lab.example/report'
        b['pages'][url]={'content':'The evaluated system is now available today via browser and API. '*10}
        ledger=need_ledger.build(b)
        ledger['needs'].append({**ledger['needs'][0],'id':'another'})
        p=material_review.packet(b,ledger,[],coverage_v2=True,balanced=True)
        ident=p['passages'][0]['passage_id'];need=p['needs'][0]['id']
        reply={'bindings':[{'need_id':need,'passage_id':ident,'axes':{a:True for a in material_review.AXES}},
            {'need_id':'another','passage_id':ident,'axes':{a:True for a in material_review.AXES}}],
            'priority_source_ids':[],'deferred_source_ids':[],'next_search':None,
            'need_assessments':[{'need_id':need,'status':'proposed_binding','passage_ids':[ident],'reason':'Exact passage.'},
                {'need_id':'another','status':'uncertain','passage_ids':['P-invented'],'reason':'Uncertain reference.'}]}
        decision=material_review.decode({'content':json.dumps(reply)},p)
        result=material_review.bind(b,p,decision)
        self.assertEqual([r['need_id'] for r in result['bindings']],[need])
        self.assertTrue(any(r['record'].get('need_id')=='another' for r in result['rejected_records']))
        self.assertEqual(result['reading_coverage'],p['reading_coverage'])

    def test_missing_assessments_cannot_authorize_closure(self):
        p={'coverage_protocol':witness_contract.PROTOCOL,'needs':[{'id':'n'}],
           'passages':[{'passage_id':'P1','url':'https://example.org','text':'Exact substantive passage'}],'sources':[]}
        raw={'bindings':[{'need_id':'n','passage_id':'P1','axes':{a:True for a in material_review.AXES}}],
             'priority_source_ids':[],'deferred_source_ids':[],'next_search':None}
        r=material_review.decode({'content':json.dumps(raw)},p)
        self.assertEqual(r['bindings'],[])
        self.assertEqual(r['need_assessments'][0]['status'],'uncertain')
        self.assertEqual(r['rejected_records'][0]['reason'],'need_coverage_missing')

    def test_bad_row_identity_is_isolated_without_global_failure(self):
        payload={'needs':[{'id':'n'}],'passages':[]}
        rows=[{'need_id':[]}, {'need_id':'n','status':'uncertain','passage_ids':[],'reason':'No material delivered.'}]
        valid,blocked,rejected=witness_contract.isolate_coverage(payload,rows,['malformed binding'])
        self.assertEqual(blocked,set())
        self.assertEqual(len(valid),1)
        self.assertEqual(rejected[0]['reason'],'need_coverage_unknown_need')

    def test_multiple_document_families_receive_exact_spans_under_same_limit(self):
        b=bundle();b['plan']=[{'id':'alpha','condition':'Alpha observations','priority':'critical'},
                            {'id':'beta','condition':'Beta publication notice','priority':'critical'}]
        b['pages']={'https://example.org/alpha':{'content':('Alpha observations 2026. '*35+'\n')*40},
                    'https://example.org/beta':{'content':('Beta publication notice 2026. '*20+'\n')*3}}
        p=material_review.packet(b,need_ledger.build(b),[],coverage_v2=True,balanced=True)
        self.assertTrue(any('/beta' in r['url'] for r in p['passages']))
        self.assertTrue(all(r['delivered_passage_ids'] for r in p['reading_coverage'].values()))
        self.assertLessEqual(len(p['passages']),24)
        self.assertLessEqual(sum(len(r['text']) for r in p['passages']),60000)
        for r in p['passages']:
            self.assertEqual(b['pages'][r['url']]['content'][r['start']:r['end']],r['text'])
