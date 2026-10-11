"""Exact originals, scoped selectors, quarantine and native material receipts."""
import copy
import tempfile
import unittest

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import reference_map as refs, state, simple_map, target_logic, runtime, decision, delta, delivery
from ForecastAgent.research_loop.acceptance import accept, MapAcceptanceError
from ForecastAgent.tests.test_research_target_logic import fixture, link
from ForecastAgent.tests.test_research_gap_feedback import task, receipt, patch
from ForecastAgent.tests.test_research_simple_map import literal_proposal


def proposal(b):
    b['request'][refs.FIELD] = refs.POLICY
    b['request'][target_logic.FIELD] = target_logic.POLICY
    p = literal_proposal(b)
    for n in p['nodes']:
        n['target_links'] = []
    p['nodes'][0]['target_links'] = [link(b)]
    p['nodes'][1]['evidence_ids'] = []
    p['nodes'][2]['evidence_ids'] = []
    p['nodes'] = [refs.input_node(n) for n in p['nodes']]
    return p


def apply(b,p,allowed=None):
    allowed = list(state.catalog(b)['spans']) if allowed is None else allowed
    hydrated, records = refs.prepare(b,p,allowed)
    result = accept(b,hydrated,allowed=allowed,map_protocol=refs.PROTOCOL,stage_grounding=True)
    return result, records


class ReferenceMapTests(unittest.TestCase):
    def test_whole_long_original_is_bound_without_quote_copy_or_truncation(self):
        b,_=fixture();p=proposal(b);pages=copy.deepcopy(b['pages'])
        result, records=apply(b,p)
        n=b['research_loop']['current']['nodes'][0];s=n['bindings'][0]
        self.assertGreater(len(s['text']),360)
        self.assertEqual(b['pages'][s['url']]['content'][s['start']:s['end']],s['text'])
        self.assertEqual(n['claim'],refs.label(n['evidence_ids']))
        self.assertEqual(n['claim_origin'],refs.ORIGIN)
        self.assertTrue(n['original_reference_verified']);self.assertFalse(n['literal_quote_verified'])
        self.assertFalse(n['interpretation_verified']);self.assertEqual(n['event_stage'],'unknown')
        self.assertEqual(records[0]['original_bindings'][0]['bound_characters'],len(s['text']))
        self.assertEqual(b['pages'],pages);state.verify_journal(b['research_loop'])

    def test_bad_reference_sibling_is_quarantined_without_turning_it_into_gap(self):
        b,_=fixture();p=proposal(b);bad=copy.deepcopy(p['nodes'][0]);bad.update(id='bad',evidence_ids=['R_invented'])
        p['nodes'].append(bad);result,records=apply(b,p)
        self.assertNotIn('bad',result['acceptance']['accepted_node_ids'])
        self.assertEqual(records[-1]['status'],'rejected')
        p['expected_revision']=1;p['revision_kind']='interpretation_correction'
        p['retired_node_ids']=['observed_report','demand'];p['nodes']=[bad,p['nodes'][2]]
        with self.assertRaises(MapAcceptanceError):apply(b,p)
        self.assertEqual(b['research_loop']['revision'],1)

    def test_invalid_explanation_retains_exact_reference_without_target_effect(self):
        for field, value in [('interpretation', 'x'*241), ('limitation', {'bad':'type'})]:
            b,_=fixture();p=proposal(b);pages=copy.deepcopy(b['pages'])
            p['nodes'][0][field]=value;before=copy.deepcopy(p)
            result,records=apply(b,p)
            node=b['research_loop']['current']['nodes'][0]
            self.assertEqual(node['evidence_ids'],before['nodes'][0]['evidence_ids'])
            self.assertEqual(node['applicability'],'unknown');self.assertEqual(node['target_links'],[])
            self.assertEqual(node[field],'');self.assertFalse(node['interpretation_verified'])
            self.assertNotIn(refs.ANNOTATION_ERRORS,node)
            self.assertEqual(records[0]['submitted_node_sha256'],digest(before['nodes'][0]))
            self.assertTrue(records[0]['annotation_rejections'])
            self.assertTrue(result['acceptance']['narratives_omitted'])
            self.assertFalse(result['acceptance']['target_coverage']['targets'][0]['adequacy_verified'])
            self.assertEqual(b['pages'],pages);self.assertEqual(p,before)
            self.assertEqual(node['bindings'][0]['text'],state.catalog(b)['spans'][node['evidence_ids'][0]]['text'])
            state.verify_journal(b['research_loop'])

    def test_invalid_target_annotation_does_not_erase_valid_original(self):
        b,_=fixture();p=proposal(b)
        p['nodes'][0]['target_links'].append({**link(b),'target_id':'invented'})
        result,records=apply(b,p)
        node=b['research_loop']['current']['nodes'][0]
        self.assertEqual(len(node['target_links']),1)
        self.assertTrue(records[0]['annotation_rejections'])
        self.assertEqual(result['acceptance']['status'],'partial')

    def test_core_reference_error_is_precise_even_when_annotation_is_invalid(self):
        b,_=fixture();p=proposal(b)
        p['nodes'][0].update(evidence_ids=['R_invented'],interpretation='x'*241)
        with self.assertRaisesRegex(MapAcceptanceError,'Reference outside delivered original coverage') as caught:apply(b,p)
        self.assertNotIn('Supply arguments.claim',str(caught.exception))
        self.assertEqual(b.get('research_loop',{}).get('revision',0),0)

    def test_model_cannot_inject_internal_annotation_receipts(self):
        b,_=fixture();p=proposal(b);p['nodes'][0][refs.ANNOTATION_ERRORS]=[]
        with self.assertRaisesRegex(MapAcceptanceError,'Use only declared parameters'):apply(b,p)

    def test_hidden_reference_never_binds_even_when_saved(self):
        b,_=fixture();p=proposal(b)
        canonical,records=refs.prepare(b,p,[])
        self.assertEqual(records[0]['status'],'rejected')
        with self.assertRaises(MapAcceptanceError):accept(b,canonical,allowed=[],map_protocol=refs.PROTOCOL)

    def test_material_identity_change_and_body_checksum_fail_before_update(self):
        b,_=fixture();p=proposal(b);b['pages']['https://example.org/report']['content']+=' Later update.'
        with self.assertRaisesRegex(ValueError,'Material changed'):refs.prepare(b,p,list(state.catalog(b)['spans']))
        b['pages']['https://example.org/report']['content_sha256']='invalid'
        with self.assertRaisesRegex(ValueError,'checksum'):refs.prepare(b,p,[])

    def test_source_reference_origin_cannot_bypass_disabled_policy_or_label_check(self):
        b,_=fixture();p=proposal(b);canonical,_=refs.prepare(b,p,list(state.catalog(b)['spans']))
        b['request'].pop(refs.FIELD)
        with self.assertRaisesRegex(ValueError,'explicit policy'):accept(b,canonical,map_protocol=refs.PROTOCOL)
        with self.assertRaisesRegex(ValueError,'explicit reference'):state.bind_node(canonical['nodes'][0],state.catalog(b))
        b['request'][refs.FIELD]=refs.POLICY;canonical['nodes'][0]['claim']='Revenue was 900 billion dollars.'
        with self.assertRaises(MapAcceptanceError):accept(b,canonical,map_protocol=refs.PROTOCOL)

    def test_model_cannot_smuggle_quote_or_dates_in_observation_fields(self):
        for field,value in [('hypothesis','Invented measurement'),('claim','Invented measurement'),('event_time','2026-01-01')]:
            b,_=fixture();p=proposal(b);p['nodes'][0][field]=value
            with self.subTest(field=field):
                canonical,records=refs.prepare(b,p,list(state.catalog(b)['spans']))
                self.assertEqual(records[0]['status'],'rejected')
                with self.assertRaises(MapAcceptanceError):accept(b,canonical,map_protocol=refs.PROTOCOL)

    def test_canonical_input_bypass_is_rejected_before_native_acceptance(self):
        b,_=fixture();p=proposal(b);canonical,_=refs.prepare(b,p,list(state.catalog(b)['spans']))
        repeated,records=refs.prepare(b,canonical,[])
        self.assertEqual(records[0]['status'],'rejected')
        with self.assertRaises(MapAcceptanceError):accept(b,repeated,map_protocol=refs.PROTOCOL)

    def test_navigation_only_body_and_duplicate_reference_ids_are_rejected(self):
        for mode in ('heading','duplicate'):
            b,_=fixture();p=proposal(b);material=state.catalog(b)
            if mode=='heading':
                s=material['spans'][p['nodes'][0]['evidence_ids'][0]];s['text']='# Title\n|---|---|'
                with self.assertRaisesRegex(ValueError,'Navigation-only'):
                    c,_=refs.prepare(b,p,list(material['spans']))
                    refs.verify_node(c['nodes'][0],material)
            else:
                p['nodes'][0]['evidence_ids'] *= 2
                with self.assertRaises(MapAcceptanceError):apply(b,p)

    def test_reference_trial_prepares_same_frozen_text_and_scopes_without_http(self):
        import json
        from pathlib import Path
        from unittest.mock import patch as mock
        from ForecastAgent.analysis.pilot import save, load
        from ForecastAgent.research_loop import reference_map_trial as trial, target_logic_trial
        b,_=fixture();accept(b,literal_proposal(b),map_protocol=simple_map.PROTOCOL)
        common,_=target_logic_trial.common_input(b)
        with tempfile.TemporaryDirectory() as root:
            parent=Path(root)/'parent.json';original=Path(root)/'original.json';save(parent,b);save(original,common)
            with mock.object(trial,'ask_model',side_effect=AssertionError('No HTTP allowed')):
                report=trial.run([parent],[original],Path(root)/'trial')
            self.assertEqual(report['physical_model_http'],0)
            requests=[load(Path(root)/'trial/cases'/str(b['request']['id'])/arm/'request.json') for arm in trial.ARMS]
            self.assertEqual(requests[0]['messages'][1],requests[1]['messages'][1])
            self.assertNotIn('claim',requests[1]['tools'][0]['function']['parameters']['properties']['nodes']['items']['properties'])
            self.assertEqual(json.loads(requests[1]['messages'][1]['content']),common)

    def test_table_row_and_header_keep_separate_exact_bindings(self):
        b,_=fixture();b['request']['research_acquisition_policy']='map_guided_acquisition_v1'
        from ForecastAgent.research_loop import fusion
        b['request']['research_acquisition_policy']=fusion.POLICY_NAME
        b['pages']['https://example.org/report']['content']=('# Report\n| Period | USD billions |\n|---|---|\n| Q1 2026 | 24 |\n\n' + 'Full company explanatory text.\n'*15)
        p=proposal(b);material=state.catalog(b)
        table=[s for s in material['spans'].values() if s['text'].lstrip().startswith('|') and '---' not in s['text']]
        p['nodes'][0]['evidence_ids']=[s['evidence_id'] for s in table]
        apply(b,p);bindings=b['research_loop']['current']['nodes'][0]['bindings']
        self.assertEqual([s['text'] for s in bindings],[s['text'] for s in table])

    def test_mercury_projection_uses_same_originals_and_preserves_origin(self):
        b,_=fixture();p=proposal(b);apply(b,p);prepared=decision.prepare(b)
        mapped=prepared['enriched']['research_map'];self.assertEqual(mapped['nodes'][0]['claim_origin'],refs.ORIGIN)
        self.assertEqual({k:v for k,v in prepared['enriched'].items() if k!='research_map'},prepared['baseline'])
        self.assertFalse(mapped['target_coverage']['truth_verified'])

    def test_native_receipt_merge_retire_and_hashes_do_not_renew_quota(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);t.bundle['request'][refs.FIELD]=refs.POLICY;t.bundle['request'][target_logic.FIELD]=target_logic.POLICY
            t.execute('inspect_research_state',{'url':'https://example.org/report','limit':3},'test')
            p=proposal(t.bundle);p.update(update_mode='merge',material_reviews=[receipt(t,'https://example.org/report')])
            p['material_requests']=[];before=t.budget();pages=copy.deepcopy(t.bundle['pages'])
            result=t.execute('update_research_state',p,'test')
            self.assertTrue(result['material_acknowledged']);self.assertEqual(t.budget(),before)
            event=t.bundle['research_loop']['events'][-1]
            self.assertEqual(event['acceptance']['reference_submitted_proposal_sha256'],digest(p))
            self.assertTrue(event['acceptance']['reference_binding_receipts']);state.verify_journal(t.bundle['research_loop'])
            messages=delivery.context(t);self.assertIn('Reference-bound map interface',messages[0]['content'])
            tools=runtime.filter_tools(t,runtime.configure(t,[]))
            s=next(x['function']['parameters'] for x in tools if x['function']['name']=='update_research_state')
            self.assertNotIn('claim',s['properties']['nodes']['items']['properties'])
            review=patch(t,[receipt(t,'https://example.org/report','irrelevant',[],effect='no_change')])
            t.execute('update_research_state',review,'test');self.assertEqual(t.bundle['research_loop']['revision'],1)
            review['material_reviews']=[];review['retired_node_ids']=['observed_report','demand']
            t.execute('update_research_state',review,'test');self.assertTrue(state.audit(t.bundle)['gap_only'])
            self.assertEqual(t.bundle['pages'],pages);self.assertEqual(t.budget(),before)


if __name__=='__main__':
    unittest.main()
