"""Prompt compatibility, gap routing and conservative interpretation signals."""
import copy
import tempfile
import unittest

from ForecastAgent.research_loop import predictive_focus as focus, reference_map as refs
from ForecastAgent.research_loop import runtime, delivery, dispatch, state, fusion
from ForecastAgent.research_loop.acceptance import accept
from ForecastAgent.tests.test_research_reference_map import proposal, apply
from ForecastAgent.tests.test_research_target_logic import fixture
from ForecastAgent.tests.test_research_dispatch import fresh


def need(state_name='obtainable', purpose='baseline'):
    return {'target':'Official current baseline', 'reason':'Comparable current input',
            'node_ids':['observed_report'], 'role':'primary', 'suggested_tool':'page_fetch',
            'decision_impact':'A dated baseline informs the future target', 'importance':'high',
            'availability':'available', 'information_state':state_name, 'purpose':purpose,
            'source_url':''}


class PredictiveFocusTests(unittest.TestCase):
    def test_reference_prompts_have_one_contract_and_keep_operational_guards(self):
        with tempfile.TemporaryDirectory() as root:
            t = fresh(root)
            t.bundle['request'].update({refs.FIELD:refs.POLICY, focus.FIELD:focus.POLICY})
            t.execute('inspect_research_state', {'url':'https://example.org/report','limit':3}, 'test')
            prompts = [runtime.guide(t, 'Network budget remains authoritative. '),
                       delivery.context(t)[0]['content'], refs.prompt(bundle=t.bundle, local_only=True)]
            for content in prompts:
                for forbidden in ('claim_origin=source_quote','short CONTIGUOUS literal',
                                  'Copy short literal quotes','stage_basis <=180','short exact quote cannot'):
                    self.assertNotIn(forbidden, content)
                self.assertEqual(content.count('Reference-bound map interface:'), 1)
                self.assertIn('future_unknown', content)
                self.assertIn('No pending receipts', content)
            self.assertIn('Network budget', prompts[0])
            self.assertIn('Network actions', prompts[1])
            self.assertIn('No search, fetch or scoring', prompts[2])
            for content in prompts[:2]:
                self.assertNotIn('Return exactly one update_research_state', content)
                self.assertIn('not the required tool for every acquisition turn', content)
                self.assertIn('not tasks to rediscover the question', content)
            self.assertIn('Return exactly one update_research_state', prompts[2])
            props = next(x for x in runtime.configure(t, []) if x['function']['name']=='update_research_state')['function']['parameters']['properties']
            self.assertNotIn('claim', props['nodes']['items']['properties'])
            self.assertIn('information_state', props['material_requests']['items']['required'])

    def test_three_states_reject_inconsistent_declarations(self):
        b,_ = fixture()
        n = need(); self.assertEqual(focus.validate_need(b,n), 'obtainable')
        n.update(information_state='future_unknown', purpose='future_outcome', availability='future_event')
        self.assertEqual(focus.validate_need(b,n), 'future_unknown')
        n['source_url'] = 'https://example.org/report'
        with self.assertRaises(ValueError): focus.validate_need(b,n)
        n = need('saved_unread'); n['source_url']='https://example.org/report'
        self.assertEqual(focus.validate_need(b,n), 'saved_unread')
        n['source_url']='https://example.org/not-saved'
        with self.assertRaises(ValueError): focus.validate_need(b,n)

    def test_invalid_request_is_isolated_without_deleting_bound_original(self):
        b,_ = fixture(); p = proposal(b); b['request'][focus.FIELD]=focus.POLICY
        pages=copy.deepcopy(b['pages'])
        invalid=need('future_unknown'); p['material_requests']=[invalid]
        result,_=apply(b,p)
        self.assertEqual(b['pages'],pages)
        self.assertIn('observed_report',result['acceptance']['accepted_node_ids'])
        self.assertEqual(b['research_loop']['current']['material_requests'],[])
        self.assertTrue(any(r['section']=='material_requests' for r in result['acceptance']['rejected']))
        state.verify_journal(b['research_loop'])

    def test_future_and_saved_gaps_never_authorize_network(self):
        b,_ = fixture(); b['request'].update({refs.FIELD:refs.POLICY,focus.FIELD:focus.POLICY})
        n=need();self.assertTrue(focus.network_candidate(b,n))
        n.update(information_state='future_unknown',purpose='future_outcome',availability='future_event')
        self.assertFalse(focus.network_candidate(b,n))
        n=need('saved_unread');n['source_url']='https://example.org/report'
        self.assertFalse(focus.network_candidate(b,n))
        n.pop('information_state'); self.assertFalse(focus.network_candidate(b,n))

    def test_saved_gap_schedules_local_read_then_update_without_spending_network(self):
        with tempfile.TemporaryDirectory() as root:
            t=fresh(root);t.bundle['request'].update({refs.FIELD:refs.POLICY,focus.FIELD:focus.POLICY})
            t.execute('inspect_research_state',{'url':'https://example.org/report','limit':3},'test')
            p=proposal(t.bundle);p.update(update_mode='merge',material_reviews=[])
            n=need('saved_unread');n['source_url']='https://example.org/report';p['material_requests']=[n]
            t.execute('update_research_state',p,'test'); budget=t.budget();pages=copy.deepcopy(t.bundle['pages'])
            choice=dispatch.choose(t);self.assertEqual(choice['phase'],'process_read')
            dispatch.record_selection(t,choice)
            t.execute(choice['tool'],{'url':choice['url'],'limit':3},'test')
            follow=dispatch.choose(t);self.assertEqual(follow['phase'],'process_update')
            self.assertEqual(follow['gap_id'],choice['gap_id'])
            self.assertEqual(t.budget(),budget);self.assertEqual(t.bundle['pages'],pages)

    def test_scope_signals_preserve_originals_and_do_not_certify_semantics(self):
        b,_=fixture();p=proposal(b);b['request'][focus.FIELD]=focus.POLICY
        p['material_requests']=[]
        p['nodes'][0]['interpretation']='No proclamation occurred. Revenue is 900 billion.'
        p['nodes'][1]['hypothesis']='The governor intends to call a session.'
        result,_=apply(b,p);audit=result['acceptance']['interpretation_scope_audit']
        signals={r['node_id']:r['signals'] for r in audit['node_review_signals']}
        self.assertIn('numeric_literal_not_in_selected_originals_or_rules',signals['observed_report'])
        self.assertIn('negative_or_absence_inference_requires_scope_review',signals['observed_report'])
        self.assertIn('intent_or_stage_inference_requires_original_review',signals['demand'])
        self.assertFalse(audit['meaning_verified']);self.assertFalse(audit['originals_rejected_by_risk_signals'])
        self.assertEqual(len(b['research_loop']['current']['nodes'][0]['bindings']),1)

    def test_legacy_request_keeps_original_network_routing(self):
        b,_=fixture();n=need();n.pop('information_state');n.pop('purpose');n.pop('source_url')
        self.assertTrue(focus.network_candidate(b,n))
        n['availability']='future_event';self.assertFalse(focus.network_candidate(b,n))


if __name__ == '__main__':
    unittest.main()
