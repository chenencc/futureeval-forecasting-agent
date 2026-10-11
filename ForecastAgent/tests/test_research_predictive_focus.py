"""Prompt compatibility, gap routing and conservative interpretation signals."""
import copy
import tempfile
import unittest
import json

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
    def test_reference_recovery_pins_current_cursor_without_rebasing_rejected_update(self):
        with tempfile.TemporaryDirectory() as root:
            t=fresh(root); t.bundle['request'].update({refs.FIELD:refs.POLICY, focus.FIELD:focus.POLICY})
            t.execute('inspect_research_state', {'url':'https://example.org/report','limit':3}, 'test')
            p=proposal(t.bundle); p.update(update_mode='merge',material_reviews=[])
            t.execute('update_research_state',p,'test')
            revision=t.bundle['research_loop']['revision']
            bad=copy.deepcopy(p); bad['expected_revision']=revision+1
            with self.assertRaises(ValueError) as error:
                t.execute('update_research_state',bad,'test')
            self.assertEqual(t.bundle['research_loop']['revision'],revision)
            self.assertEqual(bad['expected_revision'],revision+1)
            fusion.map_feedback(t,error=error.exception)
            feedback=t.bundle['research_acquisition']['last_map_feedback']
            self.assertNotIn('Correct the quote',feedback['instruction'])
            self.assertIn('current update_cursor',feedback['instruction'])
            messages=delivery.context(t)
            payload=json.loads(messages[1]['content'])
            self.assertEqual(payload['update_cursor']['expected_revision'],revision)
            self.assertEqual(payload['update_cursor']['material_sha256'],state.catalog(t.bundle,t.cutoff)['material_sha256'])
            self.assertIn('not the next revision',messages[0]['content'])

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


    def test_rule_threshold_and_date_are_distinct_from_an_observed_source_fact(self):
        b,_=fixture()
        b['request']['resolution_criteria']='Resolve using the 18-member cohort as of July 20, 2026.'
        node={'id':'roster','kind':'observation','interpretation':'The source lists 18 members as of July 20, 2026.',
            'evidence_ids':['R_header'],'bindings':[{'text':'Browse the current list of office holders below.'}]}
        originals=copy.deepcopy(b['pages']);report=focus.audit(b,[node],[])
        risk=report['node_review_signals'][0]
        self.assertIn('rule_only_literal_in_observation_requires_role_review',risk['signals'])
        self.assertEqual({r['literal']:r['origin'] for r in risk['numeric_literal_origins']},
                         {'18':'rule_only','20':'rule_only','2026':'rule_only'})
        self.assertEqual(risk['unanchored_numeric_literals'],[])
        self.assertEqual(b['pages'],originals);self.assertFalse(report['originals_rejected_by_risk_signals'])

    def test_literal_origins_report_source_rule_both_and_neither_without_truth_claims(self):
        b,_=fixture();b['request'].update(question='Target 5 or 9.',resolution_criteria='',fine_print='')
        node={'id':'measurement','kind':'observation','interpretation':'Observed 4 compared with 5; 9 or 77 remains uncertain.',
              'evidence_ids':['R_measurement'],'bindings':[{'text':'Measured value 4; earlier benchmark 5.'}]}
        report=focus.audit(b,[node],[]);risk=report['node_review_signals'][0]
        self.assertEqual({r['literal']:r['origin'] for r in risk['numeric_literal_origins']},
                         {'4':'selected_original','5':'both','9':'rule_only','77':'neither'})
        self.assertEqual(risk['unanchored_numeric_literals'],['77'])
        self.assertFalse(report['meaning_verified'])
        self.assertFalse(any(r['meaning_verified'] for r in risk['numeric_literal_origins']))

    def test_legitimate_target_comparison_is_advisory_and_model_gets_rule_origin(self):
        b,_=fixture();b['request'].update(question='Will the metric exceed 8?',resolution_criteria='',fine_print='')
        b.setdefault('research_loop',{})['current']={'nodes':[{'id':'metric','kind':'observation',
            'interpretation':'Observed 6 is below the target threshold of 8.',
            'bindings':[{'text':'Metric measured at 6.'}],'evidence_ids':['R_metric']}],'material_requests':[]}
        brief=focus.brief(b)
        self.assertEqual(brief['review_signals'][0]['rule_only_literals'],['8'])
        self.assertTrue(brief['signals_are_advisory'])
        self.assertIn('legitimate comparisons',focus.GUIDE.lower())


if __name__ == '__main__':
    unittest.main()
