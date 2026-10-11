"""Generic recency/source-scope review and literal row-context checks."""
import unittest
from ForecastAgent.research_loop import node_review, reference_map


class NodeReviewTests(unittest.TestCase):
    def test_link_review_cards_keep_own_references_and_exact_target_kind(self):
        from ForecastAgent.research_loop import target_logic
        from unittest.mock import patch
        bundle={'request':{target_logic.FIELD:target_logic.POLICY},
            'research_loop':{'current':{'nodes':[
                {'id':'index','evidence_ids':['Rindex'],'target_links':[
                    {'target_id':'Ffuture','role':'indicator','effect':'supports','reason':'Commencement implies progress.'}]},
                {'id':'photo','evidence_ids':['Rphoto'],'target_links':[]}]}}}
        with patch.object(target_logic,'targets',return_value=[{
                'id':'Ffuture','kind':'forecast_target','condition':'Future suspension by exact deadline.'}]):
            result=node_review.brief(bundle)
        card=result['link_review_cards'][0]
        self.assertEqual(card['own_reference_ids'],['Rindex'])
        self.assertEqual(card['target_kind'],'forecast_target')
        self.assertEqual(card['target_condition'],'Future suspension by exact deadline.')
        self.assertFalse(card['source_meaning_verified'])
        self.assertEqual(bundle['research_loop']['current']['nodes'][0]['target_links'][0]['role'],'indicator')

    def test_link_card_cap_exposes_omissions_without_claiming_review(self):
        bundle={'research_loop':{'current':{'nodes':[{'id':str(i),'target_links':[
            {'target_id':'unknown','role':'context','effect':'context','reason':'Scope unknown.'}]} for i in range(35)]}}}
        result=node_review.brief(bundle)
        self.assertLessEqual(len(result['link_review_cards']),32)
        self.assertEqual(len(result['link_review_cards'])+len(result['omitted_link_review_cards']),35)
        self.assertTrue(result['omitted_link_review_cards'])
        self.assertLessEqual(result['link_card_chars'],6000)
        self.assertFalse(result['meaning_verified'])

    def test_identical_explanation_with_different_references_is_advisory(self):
        nodes = [{'id': ident, 'interpretation': 'The council closed schools on Oct1.',
                  'bindings': [{'url': url, 'body_sha256': url, 'start': 0, 'end': 9}]}
                 for ident, url in [('a','october'),('b','september')]]
        result = node_review.brief({'research_loop': {'current': {'nodes': nodes}}})
        self.assertEqual(len(result['signals']), 2)
        self.assertTrue(all('same_interpretation_different_source_scope' in r['signals'] for r in result['signals']))
        self.assertFalse(result['automatic_retirement'])
        self.assertFalse(result['meaning_verified'])

    def test_historical_observation_is_not_flagged_as_latest(self):
        result = node_review.brief({'research_loop': {'current': {'nodes': [
            {'id':'old','interpretation':'A close was recorded in 2025.'},
            {'id':'stale','interpretation':'Latest data only available in 2025.'}]}}})
        self.assertEqual([r['node_id'] for r in result['signals']], ['stale'])

    def test_whole_dated_rows_selected_without_future_or_header_inference(self):
        spans = [{'evidence_id':'header','start':0,'text':'Downloaded 2026-10-11'},
            {'evidence_id':'old','start':10,'text':'{"date":"2025-10-16","value":100}'},
            {'evidence_id':'recent','start':20,'text':'{"date":"2026-10-09","value":120}'},
            {'evidence_id':'future','start':30,'text':'{"date":"2026-10-16","value":130}'},
            {'evidence_id':'invalid','start':40,'text':'{"date":"2026-99-99","value":140}'}]
        selected = node_review.dated_row_context(spans, '2026-10-11T07:00:00Z')
        self.assertEqual([s['evidence_id'] for s in selected], ['recent','old'])
        self.assertEqual(selected[0], spans[2])
        self.assertEqual(node_review.dated_row_context(spans, None), [])

    def test_all_reference_map_phases_explain_old_node_updates(self):
        for phase in ('map','review','acquisition'):
            text = reference_map.prompt(phase=phase)
            self.assertIn('Adding a new current node', text)
            self.assertIn('existing ID', text)


if __name__ == '__main__':
    unittest.main()
