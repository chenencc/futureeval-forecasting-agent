"""Target paths, isolated annotations and exact-source scoring boundaries."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

from ForecastAgent.research_loop import target_logic as logic, state, simple_map, delta, runtime, fusion, decision, delivery
from ForecastAgent.research_loop.acceptance import accept, MapAcceptanceError
from ForecastAgent.tests.test_research_loop import source_bundle
from ForecastAgent.tests.test_research_simple_map import literal_proposal
from ForecastAgent.tests.test_research_gap_feedback import task, initial, patch, receipt


def fixture():
    b = source_bundle()
    b['request'][logic.FIELD] = logic.POLICY
    b['plan'] = [{'id':'release','condition':'Target-period quarterly revenue.',
                  'priority':'critical','expected_source':'Official release','query':'revenue'}]
    p = literal_proposal(b)
    for n in p['nodes']:
        n['target_links'] = []
    return b, p


def link(b, role='baseline', effect='context'):
    return {'target_id':logic.targets(b)[0]['id'], 'role':role, 'effect':effect,
            'reason':'A prior-quarter measurement supplies a baseline, not the target realization.'}


def apply(b, p):
    return accept(b, p, map_protocol=simple_map.PROTOCOL)


class TargetLogicTests(unittest.TestCase):
    def test_forecast_target_is_independent_of_plan_and_not_covered_by_schedule(self):
        b, _ = fixture()
        b['request']['forecast_target_registry_policy'] = 'question_and_research_targets_v1'
        b['request']['question'] = 'How many total points will the target game have?'
        b['plan'][0]['condition'] = 'Confirm the scheduled game participants.'
        registry = logic.targets(b)
        root = registry[-1]
        self.assertEqual(root['kind'], 'forecast_target')
        self.assertEqual(root['condition'], b['request']['question'])
        old_id = root['id']
        node = {'id': 'schedule', 'kind': 'observation', 'applicability': 'target',
                'target_links': [link(b, 'direct', 'supports')]}
        report = logic.audit(b, nodes=[node], requests=[])
        self.assertEqual(report['targets'][0]['status'], 'direct_evidence_declared')
        self.assertEqual(report['targets'][-1]['status'], 'unassessed')
        b['plan'][0]['condition'] = 'Look up historical scores.'
        self.assertEqual(logic.targets(b)[-1]['id'], old_id)
        b['request']['resolution_criteria'] += ' Include overtime.'
        self.assertNotEqual(logic.targets(b)[-1]['id'], old_id)
        b['request']['forecast_target_registry_policy'] = 'unknown'
        with self.assertRaises(ValueError):
            logic.targets(b)

    def test_target_paths_work_without_fabricated_causal_edges(self):
        b,p = fixture(); p['nodes'][0]['target_links'] = [link(b)]
        p['nodes'][2]['target_links'] = [link(b,'unknown','unresolved')]
        p['nodes'][2]['evidence_ids'] = []
        raw = copy.deepcopy(b['pages']); before = copy.deepcopy(b['fetch_attempts'])
        result = apply(b,p); report = result['acceptance']['target_coverage']
        self.assertEqual(report['target_link_count'],2)
        self.assertEqual(report['targets'][0]['status'],'background_only')
        self.assertEqual(len(report['no_direct_evidence_target_ids']),1)
        self.assertFalse(report['truth_verified']); self.assertFalse(report['probability_computed'])
        self.assertEqual(b['research_loop']['current']['relations'],[])
        self.assertEqual(b['pages'],raw); self.assertEqual(b['fetch_attempts'],before)

    def test_wrong_target_and_background_direct_link_are_isolated_not_quotes(self):
        for change in ('unknown_id','direct'):
            b,p=fixture(); entry=link(b)
            if change=='unknown_id': entry['target_id']='T_invented'
            else: entry['role']='direct'
            p['nodes'][0]['target_links']=[entry]
            result=apply(b,p)
            n=b['research_loop']['current']['nodes'][0]
            self.assertEqual(n['claim'],'Revenue was 24 billion dollars.')
            self.assertEqual(n['target_links'],[])
            self.assertEqual(result['acceptance']['target_coverage']['targets'][0]['status'],'unassessed')
            self.assertTrue(any(r['section']=='target_links' for r in result['acceptance']['rejected']))

    def test_direct_is_a_declaration_never_resolution(self):
        b,p=fixture(); p['nodes'][0]['applicability']='target'
        p['nodes'][0]['target_links']=[link(b,'direct','context')]
        apply(b,p); report=logic.audit(b)
        self.assertEqual(report['targets'][0]['status'],'direct_evidence_declared')
        self.assertFalse(report['targets'][0]['adequacy_verified'])

    def test_invalid_scope_annotation_is_unknown_without_erasing_quote(self):
        b,p=fixture();p['nodes'][0]['applicability']='context'
        p['nodes'][0]['target_links']=[link(b,'procedure','context')]
        result=apply(b,p);node=b['research_loop']['current']['nodes'][0]
        self.assertEqual(node['applicability'],'unknown')
        self.assertEqual(node['claim'],'Revenue was 24 billion dollars.')
        self.assertEqual(node['target_links'][0]['role'],'procedure')
        self.assertTrue(any(r.get('field')=='applicability' for r in result['acceptance']['rejected']))
        self.assertEqual(logic.audit(b)['targets'][0]['status'],'background_only')

    def test_scope_isolation_does_not_repair_bad_quote_or_upgrade_direct(self):
        for mode in ('direct','paraphrase'):
            b,p=fixture();p['nodes'][0]['applicability']='context'
            p['nodes'][0]['target_links']=[link(b,'direct','supports')]
            if mode=='paraphrase':p['nodes'][0]['claim']='Revenue will be 24 billion dollars.'
            if mode=='paraphrase':
                with self.assertRaises(MapAcceptanceError) as caught:apply(b,p)
                accepted=caught.exception.report
            else:
                accepted=apply(b,p)['acceptance']
            coverage=accepted['target_coverage']
            self.assertEqual(coverage['target_link_count'],0)
            self.assertEqual(coverage['targets'][0]['status'],'unassessed')
            if mode=='paraphrase':self.assertNotIn('observed_report',accepted['accepted_node_ids'])

    def test_missing_links_and_no_requests_are_not_coverage(self):
        b,p=fixture(); p['nodes']=p['nodes'][:1]; p['nodes'][0].pop('target_links')
        p['material_requests']=[]; result=apply(b,p)
        report=result['acceptance']['target_coverage']
        self.assertEqual(report['unassessed_target_ids'],[logic.targets(b)[0]['id']])
        self.assertEqual(report['targets'][0]['material_request_count'],0)
        self.assertFalse(report['targets'][0]['adequacy_verified'])

    def test_conflicting_and_duplicate_links_remain_auditable(self):
        b,p=fixture(); p['nodes'][0]['target_links']=[link(b),link(b)]
        result=apply(b,p)
        self.assertEqual(result['acceptance']['target_coverage']['target_link_count'],0)
        self.assertEqual(len([r for r in result['acceptance']['rejected'] if r['section']=='target_links']),2)

    def test_unknown_and_procedure_cannot_assert_directional_support(self):
        b,p=fixture()
        for node,entry in [(p['nodes'][2],link(b,'unknown','opposes')),
                           (p['nodes'][0],link(b,'procedure','supports')),
                           (p['nodes'][1],link(b,'direct','supports'))]:
            with self.subTest(kind=node['kind']):
                with self.assertRaises(ValueError):
                    logic.validate_link(entry,node,{logic.targets(b)[0]['id']})

    def test_navigation_only_claims_are_rejected_numeric_rows_survive(self):
        b,p=fixture()
        for quote,blocked in [('|---|---|',True),('# Revenue report',True),('| 2026 | 24 |',False)]:
            node=copy.deepcopy(p['nodes'][0]);node['claim']=quote
            with self.subTest(quote=quote):
                if blocked:
                    with self.assertRaisesRegex(ValueError,'Navigation-only'): logic.isolate_node(b,node)
                else:
                    self.assertEqual(logic.isolate_node(b,node)[0]['claim'],quote)

    def test_merge_preserves_links_and_retirement_removes_coverage(self):
        b,p=fixture();b['request'][delta.FIELD]=delta.POLICY
        p['nodes'][2]['evidence_ids']=[]
        p['nodes'][0]['target_links']=[link(b)];apply(b,p)
        proposal={'expected_revision':1,'revision_kind':'interpretation_correction',
            'material_sha256':state.catalog(b)['material_sha256'],'update_mode':'merge',
            'nodes':[],'relations':[],'material_requests':[], 'retired_node_ids':[],
            'supporting_path':'','alternative_path':'','revision_reason':'Retain existing target paths.'}
        expanded,_=delta.expand(b,proposal)
        self.assertEqual(expanded['nodes'][0]['target_links'],[link(b)])
        proposal['retired_node_ids']=['observed_report','demand'];expanded,_=delta.expand(b,proposal)
        apply(b,expanded)
        self.assertEqual(logic.audit(b)['targets'][0]['status'],'unassessed')

    def test_mercury_projection_recomputes_coverage_only_from_visible_nodes(self):
        b,p=fixture();p['nodes'][0]['target_links']=[link(b)];apply(b,p)
        prepared=decision.prepare(b)
        notes=prepared['enriched']['research_map']
        self.assertEqual(notes['target_coverage']['target_link_count'],1)
        self.assertEqual(notes['nodes'][0]['target_links'],[link(b)])
        self.assertEqual(logic.audit(b,[])['targets'][0]['status'],'unassessed')
        self.assertEqual({k:v for k,v in prepared['enriched'].items() if k!='research_map'},prepared['baseline'])

    def test_native_tools_context_and_receipt_only_keep_separate_gates(self):
        with tempfile.TemporaryDirectory() as root:
            t=task(root);t.bundle['request'][logic.FIELD]=logic.POLICY
            initial(t)
            before=t.budget();revision=t.bundle['research_loop']['revision']
            frame=fusion.frontier(t)
            self.assertEqual(frame['target_coverage']['targets'][0]['status'],'unassessed')
            tools=runtime.configure(t,[])
            update=next(x for x in tools if x['function']['name']=='update_research_state')
            self.assertIn('target_links',update['function']['parameters']['properties']['nodes']['items']['required'])
            messages=delivery.context(t)
            self.assertIn('Target-linked research',messages[0]['content'])
            result=t.execute('update_research_state',patch(t,[receipt(t,'https://example.org/report','irrelevant',[],effect='no_change')]),'test')
            self.assertFalse(result.get('committed',False));self.assertEqual(t.bundle['research_loop']['revision'],revision)
            self.assertEqual(t.budget(),before)
            self.assertEqual(logic.audit(t.bundle)['targets'][0]['status'],'unassessed')

    def test_disabled_policy_preserves_old_acceptance(self):
        b=source_bundle();p=literal_proposal(b);apply(b,p)
        self.assertNotIn('target_coverage',state.view(b))
        self.assertNotIn('target_links',b['research_loop']['current']['nodes'][0])

    def test_frozen_paired_trial_prepares_identical_originals_without_http(self):
        from ForecastAgent.analysis.pilot import load, save
        from ForecastAgent.research_loop import target_logic_trial as trial
        b=source_bundle();b['plan']=fixture()[0]['plan'];apply(b,literal_proposal(b))
        with tempfile.TemporaryDirectory() as root:
            parent=Path(root)/'parent.json';save(parent,b)
            with mock_patch.object(trial,'ask_model',side_effect=AssertionError('No network')):
                report=trial.run([parent],Path(root)/'trial')
            self.assertEqual(report['physical_model_http'],0)
            inputs=[load(Path(root)/'trial/cases'/str(b['request']['id'])/a/'request.json') for a in trial.ARMS]
            self.assertEqual(inputs[0]['messages'][1],inputs[1]['messages'][1])
            self.assertTrue(report['cases'][0]['parent_preserved'])
            self.assertFalse(report['forecasts_generated'])

    def test_trial_counts_actual_escaped_context_in_both_arms(self):
        import json
        from ForecastAgent.research_loop import target_logic_trial as trial
        b=source_bundle();b['plan']=fixture()[0]['plan']
        b['pages']['https://example.org/report']['content'] *= 20
        common,selection=trial.common_input(b)
        self.assertTrue(selection['omitted_reference_ids'])
        for extra in ('',logic.GUIDE):
            messages=[{'role':'system','content':simple_map.SYSTEM+extra},
                      {'role':'user','content':json.dumps(common,ensure_ascii=False)}]
            self.assertLessEqual(len(json.dumps(messages,ensure_ascii=False)),trial.MAX_CHARS)


if __name__=='__main__':
    unittest.main()
