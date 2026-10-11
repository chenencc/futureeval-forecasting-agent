"""Delivery invariants that prevent optimization from rewriting evidence."""
import copy
import unittest
from unittest.mock import patch
from ForecastAgent.research_loop import forecast_projection as projection, shared_map_input
from ForecastAgent.tests.test_research_target_logic import fixture, link, apply
from ForecastAgent.research_loop import decision


def notes():
    return {'status':'unverified_interpretations','map_protocol':'reference-map-v1',
        'nodes':[{'id':'N1','interpretation':'If the current baseline persists, the future value may be similar.',
            'event_time':'2026-10-09','time_status':'reported','limitation':'Current baseline; future value unknown.',
            'bindings':[{'url':'https://example.org/report','body_sha256':'a'*64,'start':0,'end':42}],
            'target_links':[{'target_id':'F1','role':'indicator','effect':'unknown','reason':'Not a realized future outcome.'}]}],
        'relations':[],'instruction':'Unverified interpretations; gaps do not establish nonoccurrence.',
        'target_coverage':{'policy':'target-v1','enabled':True,'target_link_count':1,
            'truth_verified':False,'probability_computed':False,'link_location':'links on nodes',
            'unassessed_target_ids':['T2'],'no_direct_evidence_target_ids':['F1'],
            'targets':[{'id':'F1','condition':'Value on October 15, 2026',
                'expected_source':'Official CSV','status':'indicators_declared',
                'material_request_count':1,'has_obtainable_request':True,'adequacy_verified':False}]},
        'material_requests':[{'target':'Current baseline CSV','reason':'Needed for the exact metric.',
            'suggested_tool':'page_fetch','information_state':'obtainable','purpose':'baseline',
            'availability':'uncertain','decision_impact':'Metric definition remains unresolved.'}]}


class ForecastProjectionTests(unittest.TestCase):
    def test_default_retains_legacy_notes_exactly(self):
        source=notes(); result,audit=projection.project(source)
        self.assertEqual(result,source); self.assertEqual(audit['omitted'],[])
        self.assertIsNot(result,source)

    def test_only_named_execution_paths_removed(self):
        source=notes(); parent=copy.deepcopy(source)
        result,audit=projection.project(source,policy=projection.POLICY)
        self.assertEqual(source,parent)
        self.assertEqual(result['nodes'],source['nodes'])
        self.assertEqual(result['relations'],source['relations'])
        self.assertEqual(result['instruction'],source['instruction'])
        self.assertEqual(result['target_coverage']['unassessed_target_ids'],['T2'])
        self.assertFalse(result['target_coverage']['truth_verified'])
        self.assertFalse(result['target_coverage']['targets'][0]['adequacy_verified'])
        self.assertEqual(len(audit['omitted']),9)
        for item in reversed(audit['omitted']):
            obj=result
            for key in item['path'][:-1]:obj=obj[key]
            obj[item['path'][-1]]=item['value']
        self.assertEqual(result,source)

    def test_future_unknown_and_counterevidence_are_not_execution_metadata(self):
        source=notes();source['material_requests'][0].update(
            information_state='future_unknown',purpose='future_outcome',availability='future_event')
        source['nodes'][0]['target_links'][0]['effect']='opposes'
        result,_=projection.project(source,policy=projection.POLICY)
        for key in ('information_state','purpose','availability','reason','target','decision_impact'):
            self.assertEqual(result['material_requests'][0][key],source['material_requests'][0][key])
        self.assertEqual(result['nodes'],source['nodes'])

    def test_unknown_extensions_at_every_level_remain_visible(self):
        source=notes()
        for obj in (source,source['target_coverage'],source['target_coverage']['targets'][0],source['material_requests'][0]):
            obj['future_extension']={'budget_looks_like_metadata_but_is_unknown':100}
        result,_=projection.project(source,policy=projection.POLICY)
        for obj in (result,result['target_coverage'],result['target_coverage']['targets'][0],result['material_requests'][0]):
            self.assertIn('future_extension',obj)

    def test_invalid_policy_or_structure_is_explicit(self):
        with self.assertRaises(ValueError):projection.project(notes(),policy='automatic')
        for bad in ({'target_coverage':[]},{'material_requests':[None]},
                    {'target_coverage':{'targets':[None]}}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                projection.project(bad,policy=projection.POLICY)

    def test_integrated_same_originals_and_graph_with_no_http(self):
        bundle,proposal=fixture();proposal['nodes'][0]['target_links']=[link(bundle)]
        apply(bundle,proposal);baseline=decision.prepare(bundle)['baseline']
        heads=decision.chain.questions()
        with patch.object(decision.chain,'call',side_effect=AssertionError('No provider request')):
            common,legacy,old=shared_map_input.align(bundle,bundle,baseline,heads)
            current,compact,new=shared_map_input.align(bundle,bundle,baseline,heads,projection=projection.POLICY)
        self.assertEqual(current,common)
        self.assertEqual(compact['research_map']['nodes'],legacy['research_map']['nodes'])
        self.assertEqual(compact['research_map']['relations'],legacy['research_map']['relations'])
        self.assertLess(new['mapped_bytes'],old['mapped_bytes'])
        self.assertEqual({k:v for k,v in compact.items() if k!='research_map'},common)
        self.assertTrue(new['map_audit']['forecast_projection']['omitted'])


if __name__=='__main__':unittest.main()
