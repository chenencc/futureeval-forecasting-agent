"""Format-only round trips; altered facts, stale and unread text stay rejected."""
import copy
import tempfile
import unittest

from ForecastAgent.research_loop import quote_bindings as qb, state, gap_feedback as gf
from ForecastAgent.tests.test_research_gap_feedback import task, initial, receipt, patch
from ForecastAgent.tests.test_research_simple_map import literal_proposal


class QuoteFormatBindingsTests(unittest.TestCase):
    def test_whitespace_round_trip_preserves_original_coordinates(self):
        span={'text':'As at 30\nSeptember, 16 countries reported cases.','evidence_id':'R1'}
        result=qb.locate('30 September, 16 countries',[span])
        self.assertEqual(result[3],'30\nSeptember, 16 countries')
        self.assertEqual(span['text'][result[1]:result[2]],result[3])

    def test_link_display_restores_whole_literal_link(self):
        span={'text':'[Issuer](https://official.example/report) reported revenue of $24 billion.'}
        result=qb.locate('Issuer reported revenue of $24 billion.',[span])
        self.assertEqual(result[3],span['text'])

    def test_changed_facts_and_explicit_urls_cannot_be_repaired(self):
        span={'text':'[Issuer](https://official.example/report) did not report $24 billion in 2026.'}
        for quote in ('Issuer did report $24 billion in 2026.',
                      'Issuer did not report $25 billion in 2026.',
                      'Issuer did not report $24 million in 2026.',
                      'Issuer did not report $24 billion in 2025.',
                      '[Issuer](https://fake.example/report) did not report $24 billion in 2026.'):
            with self.subTest(quote=quote):
                self.assertIsNone(qb.locate(quote,[span]))

    def test_ambiguous_locations_and_sentence_stitching_fail(self):
        span={'text':'Metric was\n24. Metric was\n24.'}
        self.assertIsNone(qb.locate('Metric was 24.',[span]))
        self.assertIsNone(qb.locate('Revenue was 24 billion.',[
            {'text':'Revenue was 24'},{'text':'billion.'}]))

    def test_image_markup_and_unsupported_rephrasing_are_not_stripped(self):
        self.assertIsNone(qb.locate('Growth was 3 percent.',[
            {'text':'Growth was 3%.'}]))
        self.assertIsNone(qb.locate('Chart shows 24 billion.',[
            {'text':'![Chart](https://example.org/image) shows 24 billion.'}]))

    def test_ambiguous_format_match_preserves_input_and_quotas(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root)
            page=t.bundle['pages']['https://example.org/report']
            page['content']=page['content'].replace('Revenue was 24','Revenue was\n24')
            raw=copy.deepcopy(t.bundle['pages']);before=t.budget()
            t.execute('inspect_research_state',{'url':'https://example.org/report','limit':1},'test')
            p=literal_proposal(t.bundle);p['nodes'][1]['evidence_ids']=[]
            p.update(update_mode='merge',material_reviews=[receipt(t,'https://example.org/report')])
            # Multiple identical occurrences are deliberately ambiguous; narrow
            # this fixture to one uniquely inspected paragraph.
            p['nodes'][0]['claim']='Official company report for the target quarter. Revenue was 24 billion dollars. Management guidance is conditional on deliveries and product demand.'
            # The paragraph repeats in the fixture, so this proposal must remain
            # rejected rather than receive invented quote coordinates.
            chosen,records=qb.prepare(t,p)
            self.assertEqual(records,[])
            self.assertEqual(chosen,p)
            self.assertEqual(t.bundle['pages'],raw)
            self.assertEqual(t.budget(),before)

    def test_one_inspected_formatted_source_closes_its_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root)
            page=t.bundle['pages']['https://example.org/report']
            page['content']='Official company report for the target quarter. Revenue was\n24 billion dollars. '+('Management describes baseline demand. '*12)
            t.execute('inspect_research_state',{'url':'https://example.org/report','limit':1},'test')
            p=literal_proposal(t.bundle);p['nodes'][1]['evidence_ids']=[]
            p.update(update_mode='merge',material_reviews=[receipt(t,'https://example.org/report')])
            original=copy.deepcopy(p);before=t.budget();raw=copy.deepcopy(t.bundle['pages'])
            result=t.execute('update_research_state',p,'test')
            self.assertEqual(p,original);self.assertEqual(t.bundle['pages'],raw)
            self.assertEqual(t.budget(),before);self.assertTrue(result['material_acknowledged'])
            proofs=result['acceptance']['quote_format_bindings']
            self.assertEqual(len(proofs),1);self.assertTrue(proofs[0]['applied'])
            n=t.bundle['research_loop']['current']['nodes'][0]
            self.assertIn(n['claim'],n['bindings'][0]['text'])
            self.assertEqual(result['gap_feedback']['quote_format_bindings'],proofs)
            self.assertEqual(gf.view(t)['pending_material_count'],0)
            state.initialize(t.bundle);gf.initialize(t)

    def test_unread_or_wrong_source_cannot_supply_format_match(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root)
            t.bundle['pages']['https://example.org/unread']={'content':'Revenue was\n24 billion dollars. '+('An unread company release. '*20)}
            m=state.catalog(t.bundle)
            p=literal_proposal(t.bundle)
            p['nodes'][0]['evidence_ids']=[next(i for i,s in m['spans'].items() if s['url']=='https://example.org/unread')]
            chosen,records=qb.prepare(t,p)
            self.assertEqual(records,[])
            self.assertEqual(chosen,p)

    def test_pending_material_metadata_exposes_only_its_bound_nodes(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t)
            t.bundle['pages']['https://example.org/other']={'content':'An independent company release mentions revenue. '*20}
            t.execute('inspect_research_state',{'url':'https://example.org/other','limit':1},'test')
            inventory=gf.materials(t)
            old=next(m for m in inventory.values() if m['url']=='https://example.org/report')
            other=next(m for m in inventory.values() if m['url']=='https://example.org/other')
            self.assertIn('observed_report',old['bound_observation_node_ids'])
            self.assertEqual(other['bound_observation_node_ids'],[])
            self.assertTrue(other['inspected_reference_ids'])
            result=t.execute('update_research_state',patch(t,[receipt(t,'https://example.org/other')]),'test')
            self.assertFalse(result['material_acknowledged'])

    def test_full_deferred_reason_is_preserved_without_expanding_context(self):
        from ForecastAgent.research_loop import fusion
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t)
            url='https://example.org/pending';t.bundle['pages'][url]={'content':'A saved source discusses an earlier stage, not the requested final outcome. '*10}
            t.execute('inspect_research_state',{'url':url,'limit':1},'test')
            r=receipt(t,url,'deferred',[],'unknown')
            r['reason']='The inspected saved material covers the earlier stage. '+('It does not establish the final target outcome. '*8)
            self.assertGreater(len(r['reason']),240)
            result=t.execute('update_research_state',patch(t,[r]),'test')
            self.assertEqual(result['gap_feedback']['accepted_reviews'][0]['reason'],r['reason'])
            self.assertEqual(result['gap_feedback']['rejected_reviews'],[])
            self.assertFalse(result['material_acknowledged'])
            self.assertNotIn(r['reason'],str(fusion.binding_frame(t)))

    def test_over_storage_cap_reason_is_rejected_without_acknowledgement(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);initial(t)
            url='https://example.org/pending';t.bundle['pages'][url]={'content':'An unfinished saved material review. '*20}
            t.execute('inspect_research_state',{'url':url,'limit':1},'test')
            r=receipt(t,url,'deferred',[],'unknown');r['reason']='x'*801
            result=t.execute('update_research_state',patch(t,[r]),'test')
            self.assertEqual(result['gap_feedback']['accepted_reviews'],[])
            self.assertIn('character limit',result['gap_feedback']['rejected_reviews'][0]['error'])
            self.assertFalse(result['material_acknowledged'])

    def test_old_policy_keeps_strict_literal_contract(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root)
            t.bundle['request'].pop(gf.FIELD)
            t.bundle['pages']['https://example.org/report']['content']='Official company report. Revenue was\n24 billion dollars. '+('A dated management report. '*15)
            t.execute('inspect_research_state',{'url':'https://example.org/report','limit':1},'test')
            p=literal_proposal(t.bundle);p['nodes'][1]['evidence_ids']=[]
            good=copy.deepcopy(p['nodes'][0]);good.update(id='literal',claim='Official company report.')
            p['nodes'].append(good);p['update_mode']='merge'
            result=t.execute('update_research_state',p,'test')
            self.assertNotIn('observed_report',result['acceptance']['accepted_node_ids'])
            self.assertIn('literal',result['acceptance']['accepted_node_ids'])
            self.assertNotIn('quote_format_bindings',result['acceptance'])

    def test_stale_reading_handle_cannot_supply_format_binding(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root)
            page=t.bundle['pages']['https://example.org/report']
            page['content']='Revenue was\n24 billion dollars. '+('A dated company release. '*20)
            t.execute('inspect_research_state',{'url':'https://example.org/report','limit':1},'test')
            p=literal_proposal(t.bundle)
            page['content']+=' The saved source was changed later.'
            chosen,records=qb.prepare(t,p)
            self.assertEqual(records,[]);self.assertEqual(chosen,p)


if __name__=='__main__':
    unittest.main()
