"""Common original coverage, genuine source aliases, atomic bounds and accounting."""
import copy
import json
import tempfile
import unittest

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.intelligence.admission import inspect, prepare as admit
from ForecastAgent.intelligence.research_map import enable
from ForecastAgent.intelligence.research_map import project
from unittest.mock import patch
from ForecastAgent.research_loop import prospective_trial, scoring_delivery, target_pack, delivery, state
from ForecastAgent.research_loop.fusion_trial import acquisition_summary
from ForecastAgent.research_loop.forecast_brief import registry
from ForecastAgent.tests.test_research_reference_map import proposal, apply
from ForecastAgent.tests.test_research_target_logic import fixture
from ForecastAgent.tests.test_research_dispatch import fresh


class ScoringDeliveryTests(unittest.TestCase):
    def test_retain_exact_duplicate_source_without_rebinding_and_same_originals(self):
        b,_=fixture();p=proposal(b)
        original='https://example.org/report';alias='https://example.org/a-report'
        b['pages'][alias]=copy.deepcopy(b['pages'][original])
        p['material_sha256']=state.catalog(b)['material_sha256']
        apply(b,p)
        b['request'][scoring_delivery.FIELD]=scoring_delivery.POLICY
        view,audit=admit(b);self.assertNotIn(original,view['pages'])
        before=digest(b),digest(view)
        common,pair,result=prospective_trial.prepared_pair(b,original_view=view)
        self.assertTrue(result['map_delivered'])
        self.assertEqual((digest(b),digest(view)),before)
        self.assertTrue(any(s['url']==original for s in common['sources']))
        self.assertFalse(result['selection']['bound_source_delivery']['binding_urls_rewritten'])
        for k,v in common.items():self.assertEqual(pair['mapped']['state'][k],v)
        refs=b['research_loop']['current']['nodes'][0]['bindings']
        self.assertEqual(refs[0]['url'],original)
        self.assertTrue(all(r['status']=='delivered' for r in result['selection']['bound_original_groups']))

    def test_normalized_whitespace_duplicates_are_not_substituted_for_exact_bytes(self):
        b,_=fixture();p=proposal(b);apply(b,p)
        b['pages']['https://example.org/a-report']=copy.deepcopy(b['pages']['https://example.org/report'])
        b['pages']['https://example.org/a-report']['content']=b['pages']['https://example.org/report']['content'].replace(' ', '  ')
        view,_=admit(b);candidate,groups,audit=scoring_delivery.prepare(b,view)
        self.assertEqual(groups,[])
        self.assertNotIn('https://example.org/report',candidate['pages'])
        self.assertEqual(audit['rejected_node_groups'][0]['reason'],'source_not_admitted_or_duplicate_bytes_differ')

    def test_bookkeeping_does_not_consume_original_text_budget_or_drop_unknown_rule_fields(self):
        b,_=fixture();p=proposal(b);apply(b,p)
        b['request'][scoring_delivery.FIELD]=scoring_delivery.POLICY
        b['request']['capability_code_identity']={'files':{'source.py':'a'*12000}}
        b['request']['predictive_information_contract']={'local_snapshot':'b'*10000}
        b['request']['unknown_platform_exception']='An exact exception supplied by the platform.'
        original=copy.deepcopy(b);view,_=admit(b)
        common,pair,audit=prospective_trial.prepared_pair(b,original_view=view)
        self.assertTrue(audit['map_delivered'])
        self.assertNotIn('capability_code_identity',common['question'])
        self.assertNotIn('predictive_information_contract',common['question'])
        for key,value in b['request'].items():
            if key not in scoring_delivery.LOCAL_METADATA_FIELDS:
                self.assertEqual(common['question'][key],value)
        self.assertEqual(len(audit['selection']['bound_source_delivery']['local_metadata_omitted_from_scoring_question']),2)
        self.assertEqual(b,original)
        self.assertEqual(pair['original']['state']['question'],pair['mapped']['state']['question'])

    def test_oversize_required_group_is_atomic_no_partial_claim_support(self):
        b,_=fixture();p=proposal(b);apply(b,p)
        heads,_=registry(b['request'])
        refs=b['research_loop']['current']['nodes'][0]['bindings']
        _,audit=target_pack.pack(b,heads,limit=5500,required_groups=[{'node_id':'n','references':refs*8}])
        # Repeated ranges compact to exact originals, without copied hypothesis.
        self.assertEqual(audit['bound_original_groups'][0]['status'],'delivered')
        large=copy.deepcopy(b);url=refs[0]['url']
        large['pages'][url]['content']='An original lengthy report. '*700
        import hashlib
        ref={'url':url,'body_sha256':hashlib.sha256(large['pages'][url]['content'].encode()).hexdigest(),
             'start':0,'end':len(large['pages'][url]['content'])}
        _,audit=target_pack.pack(large,heads,limit=5500,required_groups=[{'node_id':'n','references':[ref]}])
        self.assertEqual(audit['bound_original_groups'][0]['status'],'omitted')
        self.assertEqual(audit['bound_original_groups'][0]['reason'],'complete_group_exceeds_common_request_limit')

    def test_navigation_is_separate_from_short_notices_values_and_original_preservation(self):
        shell='Dashboard\nOverview for National\nView chart details\nDownload\nDashboard'
        page={'content':shell};self.assertEqual(inspect(page)['state'],'navigation_only')
        for body in ('Dashboard\nOverview\nDownload\nLevel: Low\nUpdated 2026-10-10',
                     'Dashboard\nOverview\nDownload\nThe agency postponed publication until Tuesday.',
                     'The agency postponed publication.'):
            self.assertTrue(inspect({'content':body})['usable_text'])
        b,_=fixture();b['pages']['https://example.org/nav']=page;before=copy.deepcopy(b)
        view,audit=admit(b);self.assertNotIn('https://example.org/nav',view['pages'])
        self.assertEqual(b,before)
        from ForecastAgent.research_loop import predictive_focus, reference_map
        b['request'].update({predictive_focus.FIELD:predictive_focus.POLICY,reference_map.FIELD:reference_map.POLICY})
        catalog=state.catalog(b)
        self.assertNotIn('https://example.org/nav',catalog['sources'])
        self.assertTrue(any(r.get('body_state')=='navigation_only' for r in catalog['excluded_sources']))
        self.assertEqual(b['pages'],before['pages'])

    def test_current_clock_is_delivered_and_final_usage_is_not_double_counted(self):
        with tempfile.TemporaryDirectory() as root:
            t=fresh(root);t.bundle['request']=enable(t.bundle['request'],predictive_focus=True)
            t.bundle['request']['as_of_utc']='2026-10-11T00:00:00Z'
            payload=json.loads(delivery.context(t)[1]['content'])
            self.assertEqual(payload['operating_clock_utc'],'2026-10-11T00:00:00Z')
            t.bundle['model_attempts']=[{'usage':{'total_tokens':100}},{'status':'reserved'}]
            t.bundle['post_supplement_review']={'model_http_attempts':99,'known_total_tokens':999,
                'usage':{'totals':{'http_attempts':2,'known_total_tokens':250,'unknown_total_tokens_attempts':1}}}
            summary=acquisition_summary(t.bundle)
            self.assertEqual(summary['super_http'],4)
            self.assertEqual(summary['known_total_tokens'],350)
            self.assertEqual(summary['unknown_total_usage_attempts'],2)

    def test_projection_restore_binds_graph_and_selection_implementation(self):
        b,_=fixture();p=proposal(b);apply(b,p)
        b['request'][scoring_delivery.FIELD]=scoring_delivery.POLICY
        view,_=admit(b)
        from ForecastAgent.intelligence.identity import code_identity
        identity=code_identity()
        self.assertIn('research_loop/scoring_delivery.py',identity)
        self.assertIn('research_loop/target_pack.py',identity)
        with tempfile.TemporaryDirectory() as root:
            first=project(b,view,root)
            self.assertEqual(project(b,view,root),first)
            changed=dict(identity);changed['research_loop/target_pack.py']='changed'
            with patch('ForecastAgent.intelligence.identity.code_identity',return_value=changed):
                with self.assertRaisesRegex(ValueError,'Frozen map projection inputs changed'):
                    project(b,view,root)


if __name__=='__main__':unittest.main()
