"""Reference-only maps never turn model prose into verified observations."""
import json
import tempfile
import unittest
from pathlib import Path

from ForecastAgent.market_repricing.official_map_references import construct_reference,replay_reference,project_reference
from ForecastAgent.market_repricing.tests import test_official_map as fixtures


class ReferenceMapTests(unittest.TestCase):
    def fixture(self): return fixtures.OfficialMapTests().common(),fixtures.OfficialMapTests().proposal()

    def test_saved_long_quotation_is_not_regenerated_or_certified(self):
        common,proposal=self.fixture()
        proposal['nodes'][1]['citations'][0]['quote']='A fabricated quote that is deliberately longer than a fixed token cap. '*8
        result=replay_reference(common,proposal)
        self.assertEqual(result['status'],'hypothesis_only')
        state=project_reference(common,result)
        for node in state['research_map']['nodes'].values():
            self.assertFalse(node['source_relevance_verified'])
            self.assertFalse(node['description_is_original_quote'])
            for binding in node['bindings']:
                self.assertNotIn('quote',binding)
                self.assertEqual(binding['binding_role'],'visible_context_not_claim_verification')
        self.assertEqual(state['evidence'],common['evidence'])

    def test_source_id_binds_only_delivered_spans(self):
        common,proposal=self.fixture()
        proposal['nodes'][1]['citations'][0]['evidence_id']='S1'
        result=replay_reference(common,proposal)
        self.assertTrue(result['audit']['structurally_usable'])
        bindings=result['graph']['nodes']['committee_hearing']['bindings']
        self.assertEqual([b['evidence_id'] for b in bindings],['H1'])

    def test_unknown_handle_cannot_become_a_source(self):
        common,proposal=self.fixture(); proposal['nodes'][1]['citations'][0]['evidence_id']='S404'
        result=replay_reference(common,proposal)
        self.assertEqual(result['status'],'rejected')
        with self.assertRaises(ValueError): project_reference(common,result)

    def test_goal_reference_is_not_a_driver_path(self):
        common,proposal=self.fixture()
        proposal['nodes'][0]['citations']=proposal['nodes'][1]['citations']
        proposal['nodes'][1]['citations']=[]
        result=replay_reference(common,proposal)
        self.assertIn('no_referenced_driver_path',result['audit']['errors'])

    def test_shared_map_slots_do_not_restart(self):
        common,proposal=self.fixture()
        for n in proposal['nodes']:
            n['reference_ids']=[c['evidence_id'] for c in n.pop('citations')];n.pop('basis')
        for e in proposal['edges']:e.pop('rule_quote')
        seen=[]
        def sender(payload):
            seen.append(payload);return {'choices':[{'message':{'content':json.dumps(proposal)}}]}
        with tempfile.TemporaryDirectory() as root:
            slots=Path(root)/'old-map';(slots/'http').mkdir(parents=True)
            (slots/'http/0.json').write_text('{}')
            result=construct_reference(common,Path(root)/'new-map',slots,model='test',sender=sender)
            self.assertEqual(result['status'],'hypothesis_only')
            self.assertEqual(len(list((slots/'http').glob('*.json'))),2)
            self.assertEqual(len(seen),1)
            again=construct_reference(common,Path(root)/'third-map',slots,model='test',sender=sender)
            self.assertEqual(again['status'],'failed');self.assertEqual(len(seen),1)

    def test_goal_caption_cannot_redefine_immutable_proposition(self):
        common,proposal=self.fixture()
        old=proposal['nodes'][0]['title']; new='Model caption differs from the exact question.'
        proposal['nodes'][0]['title']=new;proposal['edges'][0]['target']=new
        result=replay_reference(common,proposal)
        self.assertEqual(result['status'],'hypothesis_only')
        goal=next(n for n in result['graph']['nodes'].values() if n.get('is_main'))
        self.assertEqual(goal['title'],new)
        self.assertEqual(goal['goal_question_title'],old)
        self.assertTrue(goal['model_caption_is_not_resolution_rule'])
        state=project_reference(common,result)
        self.assertEqual(state['question'],common['question'])


if __name__=='__main__':unittest.main()
