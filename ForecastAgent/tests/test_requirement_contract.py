"""No-provider acceptance tests for rule clocks and isolated review failures."""
import json
import tempfile
import unittest
from unittest.mock import patch
from ForecastAgent.supplement import requirement_contract as contract
from ForecastAgent.supplement import binding_guard,material_review,enhanced,need_ledger
from ForecastAgent.tests.test_material_gap_workflow import bundle
from ForecastAgent.analysis.pilot import load
from pathlib import Path


class RequirementTests(unittest.TestCase):
    def test_planned_event_is_not_actual_commencement_and_negative_status_is_valid(self):
        need={'condition':'Whether Example has begun trading on an exchange.'}
        self.assertIn('actual_event_only_planned_witness',contract.issues(need,{'quote':'The shares are expected to begin trading on June 12.'}))
        self.assertNotIn('actual_event_only_planned_witness',contract.issues(need,{'quote':'Expected on June 12. The stock began trading on June 13.'}))
        self.assertNotIn('actual_event_only_planned_witness',contract.issues(need,{'quote':'The company has not begun trading; it is expected to begin trading later.'}))
        self.assertNotIn('actual_event_only_planned_witness',contract.issues({'condition':'Planned trading date announcement'},
                                  {'quote':'The shares are expected to begin trading on June 12.'}))

    def test_actor_direction_and_missing_subject_do_not_close_need(self):
        need={'condition':'Officials report a military attack by ABC forces on Eastern territory.'}
        self.assertIn('explicit_actor_target_direction_unverified',contract.issues(need,{'quote':'Eastern forces attacked ABC targets.'}))
        self.assertNotIn('explicit_actor_target_direction_unverified',contract.issues(need,{'quote':'ABC forces attacked Eastern targets.'}))
        self.assertNotIn('explicit_actor_target_direction_unverified',contract.issues(need,{'quote':'Eastern targets were attacked by ABC forces.'}))
        self.assertNotIn('explicit_actor_target_direction_unverified',contract.issues(need,{'quote':'Alpha Beta Coalition forces struck Eastern territory.'}))
        self.assertIn('explicit_operation_actor_missing',contract.issues({'condition':'Military analysis shows a ABC-conducted strike.'},
                                    {'quote':'Military analysis shows operations by another country.'}))

    def test_publication_month_never_becomes_observation_month(self):
        q={'resolution_criteria':'Use the release date occuring in August 2026.'}
        n=contract.attach(q,{'condition':'Current Index value for August 2026 release'})
        self.assertEqual(n['question_clock']['month'],8)
        september={'quote':'Index value is 46.4.', 'document_context':'令和8年8月調査（令和8年9月8日公表）'}
        self.assertIn('required_release_month_unverified',contract.issues(n,september))
        august={'quote':'Current index is 45.7.', 'document_context':'Published August 10, 2026. July survey results.'}
        self.assertNotIn('required_release_month_unverified',contract.issues(n,august))
        self.assertIn('required_release_month_unverified',contract.issues(n,{'quote':'August 2026 survey: 46.4'}))

    def test_generic_policy_cannot_satisfy_passed_event_record(self):
        n={'condition':'House resolutions that passed between January 2025 and March 2026'}
        self.assertIn('specific_event_disposition_unverified',contract.issues(n,{'quote':'The House may discipline Members by censure or expulsion.'}))
        self.assertIn('specific_event_disposition_unverified',contract.issues(n,{'quote':'On Motion to Table. Censuring a member. Passed.'}))
        self.assertNotIn('specific_event_disposition_unverified',contract.issues(n,{'quote':'On Agreeing to the Resolution. Censuring a member. Passed.'}))

    def test_numeric_need_requires_metric_in_exact_quote(self):
        n={'condition':'Historical market cap trajectory through June 30, 2026'}
        self.assertIn('quoted_metric_observation_unverified',contract.issues(n,{'quote':'Get push notifications on our app in June 2026.'}))
        self.assertIn('historical_target_period_unverified',contract.issues(n,{'quote':'Market cap was $113 billion in June 2024.'}))

    def test_issuer_registry_does_not_promote_syndication_to_official(self):
        n={'condition':'Newsmax official Q2 results press release'}
        self.assertTrue(binding_guard.assess(n,{'url':'https://ir.newsmax.com/news'})['eligible_for_material_closure'])
        self.assertFalse(binding_guard.assess(n,{'url':'https://newsmax.com.evil.example'})['eligible_for_material_closure'])
        self.assertFalse(binding_guard.assess(n,{'url':'https://wire.example/newsmax'})['eligible_for_material_closure'])
        self.assertFalse(binding_guard.source_requirement({'condition':'Total viewers reported by Newsmax'})['required'])

    def test_oversized_action_field_does_not_discard_independent_bindings(self):
        b=bundle();u='https://lab.example/launch';b['pages'][u]={'content':'A substantive public access announcement is saved here. '*8}
        p=material_review.packet(b,need_ledger.build(b),[{'url':u}])
        raw={'bindings':[{'need_id':'access','passage_id':p['passages'][0]['passage_id'],'axes':{a:True for a in material_review.AXES}}],
             'priority_source_ids':[p['sources'][0]['source_id']]*32,'deferred_source_ids':[],'next_search':None}
        d=material_review.decode({'content':json.dumps(raw)},p)
        r=material_review.bind(b,p,d)
        self.assertEqual(len(r['bindings']),1)
        self.assertEqual(r['priority_urls'],[])
        self.assertEqual(r['rejected_records'][0]['reason'],'priority_batch_too_large')
        raw['priority_source_ids']='[S-example, S-another]'
        d=material_review.decode({'content':json.dumps(raw)},p)
        r=material_review.bind(b,p,d)
        self.assertEqual(len(r['bindings']),1)
        self.assertEqual(r['priority_urls'],[])
        self.assertEqual(r['rejected_records'][0]['reason'],'priority_batch_invalid_type')

    def test_review_error_prevents_ready_even_when_old_material_is_present(self):
        ledger={'needs':[{'id':'n','priority':'critical','target_material_captured':True,'candidates':[]}],
                'review_errors':['invalid_source_batch_shape']}
        self.assertEqual(need_ledger.termination(ledger,reason='done',search_available=False)['acquisition_outcome'],'review_incomplete')

    def test_capacity_stop_never_overwrites_prior_bound_review(self):
        b=bundle();u='https://lab.example/launch';b['pages'][u]={'content':'A substantive public access announcement is saved here. '*8}
        b['source_leads']={'https://lab.example/detail':{'url':'https://lab.example/detail','title':'Public access details','need_ids':['access']}}
        calls=[]
        def agent(payload,state,folder):
            calls.append(1)
            p=payload['passages'][0]
            return {'bindings':[{'need_id':'access','url':p['url'],'quote':p['text'],'axes':{a:False for a in material_review.AXES}}]}
        agent.can_review=lambda state:not state.get('material_reviews')
        with tempfile.TemporaryDirectory() as tmp,patch.object(enhanced,'fetch_document',return_value={'content':'More public access details are saved. '*10,'links':[]}):
            enhanced.run(b,tmp,network=True,material_agent=agent)
            state=load(Path(tmp)/'state.json')
        self.assertEqual(calls,[1])
        self.assertTrue(state['material_review_capacity_exhausted'])
        self.assertEqual(state['material_reviews'][0]['status'],'bound')


if __name__=='__main__':unittest.main()
